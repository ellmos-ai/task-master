# -*- coding: utf-8 -*-
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import taskplan.review_pool as review_pool_module
from taskplan.client import TaskClient
from taskplan.review_pool import (
    ReviewConflict,
    ProjectHashError,
    ReviewPolicy,
    ReviewPool,
)
from taskplan.traversal import Project


class FakeClock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class ReviewPoolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "taskplan.db"
        self.client = TaskClient(self.db, agent_id="schema-test")
        self.clock = FakeClock()
        self.policy = ReviewPolicy(
            review_interval_seconds=3600,
            retry_interval_seconds=300,
            presentation_lease_seconds=60,
        )
        self.pool = ReviewPool(self.client, policy=self.policy, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, name, effort="easy"):
        path = self.root / name
        path.mkdir(exist_ok=True)
        marker = path / "project.txt"
        if not marker.exists():
            marker.write_text(name, encoding="utf-8")
        return Project(path=path, root_id=".TEST", effort=effort)

    def present(self, role, projects):
        result = self.pool.present_next(role, projects)
        self.assertIsNotNone(result["presentation"])
        return result["presentation"]

    def seal(self, role, project, result="ok"):
        presented = self.present(role, [project])
        return self.pool.complete(
            role, project.path, presented["presentation_id"], result
        )


class TestReviewSchema(ReviewPoolCase):
    def test_schema_is_additive_and_idempotent(self):
        task = self.client.add("Herkunft bleibt", effort="easy")
        before = self.client.get(task["id"])

        TaskClient(self.db, agent_id="other")
        TaskClient(self.db, agent_id="again")

        after = TaskClient(self.db).get(task["id"])
        self.assertEqual(after, before)
        conn = sqlite3.connect(self.db)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        self.assertIn("taskplan_project_reviews", tables)
        self.assertIn("taskplan_project_review_events", tables)

    def test_roles_have_independent_rows(self):
        project = self.project("alpha")
        self.seal("taskwriter", project, "erfasst")

        writer = self.pool.status("taskwriter", project.path)
        maintainer = self.pool.status("maintainer", project.path)
        self.assertEqual(writer["reason"], "unchanged_sealed")
        self.assertEqual(maintainer["reason"], "never_presented")

    def test_review_transitions_never_touch_task_origin_or_assignment(self):
        project = self.project("task-origin")
        task = self.client.add(
            "Bleibt unverändert", effort="easy",
            project_path=str(project.path), root_id=project.root_id,
        )
        self.client.assign(task["id"], "worker", "assigned")
        before = self.client.get(task["id"])

        self.seal("maintainer", project, "review only")
        self.pool.unseal("maintainer", project.path, "recheck")

        after = self.client.get(task["id"])
        self.assertEqual(after["created_by"], before["created_by"])
        self.assertEqual(after["assigned_to"], "worker")
        self.assertEqual(after["delegation_status"], "assigned")
        self.assertEqual(after["status"], before["status"])

    def test_state_survives_new_client_and_pool(self):
        project = self.project("persist")
        sealed = self.seal("taskwriter", project, "persistiert")

        reopened = ReviewPool(
            TaskClient(self.db), policy=self.policy, clock=self.clock
        )
        state = reopened.get_state("taskwriter", project.path)
        self.assertEqual(state["sealed_hash"], sealed["sealed_hash"])
        self.assertEqual(state["result"], "persistiert")


class TestEligibilityAndOrdering(ReviewPoolCase):
    def test_many_never_presented_projects_hash_only_the_selected_candidate(self):
        projects = [self.project(f"fresh-{index:03d}") for index in range(40)]
        expected = min(projects, key=lambda item: str(item.path).casefold())
        real_hash = review_pool_module.hash_project

        with mock.patch.object(
            review_pool_module, "hash_project", wraps=real_hash
        ) as hash_mock:
            outcome = self.pool.present_next("taskwriter", reversed(projects))

        self.assertEqual(hash_mock.call_count, 1)
        presented = outcome["presentation"]
        self.assertIsNotNone(presented)
        self.assertEqual(Path(presented["project_path"]), expected.path)
        state = self.pool.get_state("taskwriter", expected.path)
        self.assertEqual(state["presented_hash"], presented["current_hash"])
        self.assertEqual(presented["current_hash"], real_hash(expected.path).value)
        for decision in outcome["decisions"]:
            if Path(decision["project_path"]) != expected.path:
                self.assertIsNone(decision["current_hash"])
                self.assertEqual(decision["reason"], "never_presented")

    def test_first_candidate_hash_error_falls_through_to_next_candidate(self):
        broken = self.project("a-broken")
        healthy = self.project("b-healthy")
        real_hash = review_pool_module.hash_project
        calls = []

        def controlled_hash(path, *, exclude=()):
            calls.append(Path(path))
            if Path(path) == broken.path:
                raise ProjectHashError("synthetisch nicht lesbar")
            return real_hash(path, exclude=exclude)

        with mock.patch.object(
            review_pool_module, "hash_project", side_effect=controlled_hash
        ):
            outcome = self.pool.present_next(
                "taskwriter", [healthy, broken]
            )

        self.assertEqual(calls, [broken.path, healthy.path])
        self.assertEqual(Path(outcome["presentation"]["project_path"]), healthy.path)
        diagnostics = {
            Path(item["project_path"]): item for item in outcome["decisions"]
        }
        self.assertEqual(diagnostics[broken.path]["reason"], "hash_error")
        self.assertIsNone(diagnostics[broken.path]["current_hash"])
        self.assertIn("synthetisch nicht lesbar", diagnostics[broken.path]["error"])
        self.assertIsNone(self.pool.get_state("taskwriter", broken.path))
        healthy_state = self.pool.get_state("taskwriter", healthy.path)
        self.assertEqual(
            healthy_state["presented_hash"],
            outcome["presentation"]["current_hash"],
        )

    def test_lock_and_active_lease_do_not_claim_uncomputed_hashes(self):
        locked = self.project("locked-first")
        leased = self.project("leased-second")
        fresh = self.project("fresh-third")
        self.present("taskwriter", [leased])
        real_hash = review_pool_module.hash_project

        with mock.patch.object(
            review_pool_module, "hash_project", wraps=real_hash
        ) as hash_mock:
            outcome = self.pool.present_next(
                "taskwriter",
                [fresh, leased, locked],
                locked=lambda path: Path(path) == locked.path,
            )

        self.assertEqual(hash_mock.call_count, 1)
        diagnostics = {
            Path(item["project_path"]): item for item in outcome["decisions"]
        }
        self.assertEqual(diagnostics[locked.path]["reason"], "lock")
        self.assertEqual(diagnostics[leased.path]["reason"], "presentation_lease")
        self.assertIsNone(diagnostics[locked.path]["current_hash"])
        self.assertIsNone(diagnostics[leased.path]["current_hash"])
        self.assertEqual(Path(outcome["presentation"]["project_path"]), fresh.path)

    def test_due_candidate_is_rechecked_with_digest_before_persistence(self):
        project = self.project("due-and-changed")
        self.seal("maintainer", project)
        self.clock.advance(hours=2)
        (project.path / "project.txt").write_text("changed", encoding="utf-8")
        real_hash = review_pool_module.hash_project

        with mock.patch.object(
            review_pool_module, "hash_project", wraps=real_hash
        ) as hash_mock:
            outcome = self.pool.present_next("maintainer", [project])

        self.assertEqual(hash_mock.call_count, 1)
        self.assertEqual(outcome["presentation"]["reason"], "hash_break")
        state = self.pool.get_state("maintainer", project.path)
        self.assertEqual(
            state["presented_hash"], outcome["presentation"]["current_hash"]
        )

    def test_hash_required_candidate_is_rehashed_before_persistence(self):
        project = self.project("changes-between-hashes")
        self.seal("maintainer", project)
        content = project.path / "project.txt"
        content.write_text("changed for eligibility", encoding="utf-8")
        real_hash = review_pool_module.hash_project
        calls = []

        def mutate_before_second_hash(path, *, exclude=()):
            calls.append(Path(path))
            if len(calls) == 2:
                content.write_text("changed before persistence", encoding="utf-8")
            return real_hash(path, exclude=exclude)

        with mock.patch.object(
            review_pool_module,
            "hash_project",
            side_effect=mutate_before_second_hash,
        ):
            outcome = self.pool.present_next("maintainer", [project])

        self.assertEqual(calls, [project.path, project.path])
        current_hash = real_hash(project.path).value
        self.assertEqual(outcome["presentation"]["current_hash"], current_hash)
        state = self.pool.get_state("maintainer", project.path)
        self.assertEqual(state["presented_hash"], current_hash)
        self.assertEqual(outcome["presentation"]["reason"], "hash_break")

    def test_selected_candidate_second_hash_error_falls_through(self):
        first = self.project("a-first-hash-break")
        second = self.project("b-second-hash-break")
        self.seal("maintainer", first)
        self.clock.advance(minutes=1)
        self.seal("maintainer", second)
        (first.path / "project.txt").write_text("first changed", encoding="utf-8")
        (second.path / "project.txt").write_text("second changed", encoding="utf-8")
        first_before = self.pool.get_state("maintainer", first.path)
        real_hash = review_pool_module.hash_project
        calls = []
        per_project_calls = {first.path: 0, second.path: 0}

        def fail_first_candidate_refresh(path, *, exclude=()):
            path = Path(path)
            calls.append(path)
            per_project_calls[path] += 1
            if path == first.path and per_project_calls[path] == 2:
                raise ProjectHashError("frischer Hash fehlgeschlagen")
            return real_hash(path, exclude=exclude)

        with mock.patch.object(
            review_pool_module,
            "hash_project",
            side_effect=fail_first_candidate_refresh,
        ):
            outcome = self.pool.present_next("maintainer", [first, second])

        self.assertEqual(calls, [first.path, second.path, first.path, second.path])
        self.assertEqual(Path(outcome["presentation"]["project_path"]), second.path)
        diagnostics = {
            Path(item["project_path"]): item for item in outcome["decisions"]
        }
        self.assertEqual(diagnostics[first.path]["reason"], "hash_error")
        self.assertIsNone(diagnostics[first.path]["current_hash"])
        self.assertIn("frischer Hash fehlgeschlagen", diagnostics[first.path]["error"])
        first_after = self.pool.get_state("maintainer", first.path)
        self.assertEqual(first_after["presented_hash"], first_before["presented_hash"])
        self.assertIsNone(first_after["presentation_id"])
        self.assertEqual(
            outcome["presentation"]["current_hash"], real_hash(second.path).value
        )

    def test_never_presented_wins_before_previously_sealed(self):
        old = self.project("old")
        fresh = self.project("fresh")
        self.seal("taskwriter", old)
        self.clock.advance(hours=2)

        chosen = self.present("taskwriter", [old, fresh])
        self.assertEqual(Path(chosen["project_path"]), fresh.path)
        self.assertEqual(chosen["reason"], "never_presented")

    def test_oldest_last_presented_wins_within_effort(self):
        first = self.project("first")
        second = self.project("second")
        self.seal("taskwriter", first)
        self.clock.advance(minutes=10)
        self.seal("taskwriter", second)
        self.clock.advance(hours=2)

        chosen = self.present("taskwriter", [second, first])
        self.assertEqual(Path(chosen["project_path"]), first.path)
        self.assertEqual(chosen["reason"], "due")

    def test_same_hash_before_due_is_not_presented(self):
        project = self.project("quiet")
        self.seal("maintainer", project)

        outcome = self.pool.present_next("maintainer", [project])
        self.assertIsNone(outcome["presentation"])
        self.assertEqual(outcome["decisions"][0]["reason"], "unchanged_sealed")

    def test_hash_due_and_manual_break_each_reopen(self):
        project = self.project("breaks")
        self.seal("maintainer", project)

        (project.path / "project.txt").write_text("changed", encoding="utf-8")
        self.assertEqual(
            self.pool.status("maintainer", project.path)["reason"], "hash_break"
        )
        current = self.present("maintainer", [project])
        self.pool.complete(
            "maintainer", project.path, current["presentation_id"], "changed"
        )

        self.clock.advance(hours=2)
        self.assertEqual(self.pool.status("maintainer", project.path)["reason"], "due")
        current = self.present("maintainer", [project])
        self.pool.complete(
            "maintainer", project.path, current["presentation_id"], "due"
        )

        self.pool.unseal("maintainer", project.path, "operator requested")
        self.assertEqual(
            self.pool.status("maintainer", project.path)["reason"], "manual_unseal"
        )
        events = self.pool.events("maintainer", project.path)
        self.assertEqual(events[-1]["event"], "manual_unseal")
        self.assertIn("operator requested", events[-1]["detail"])

    def test_locked_project_has_explicit_diagnostic(self):
        project = self.project("locked")
        outcome = self.pool.present_next(
            "taskwriter", [project], locked=lambda _path: True
        )
        self.assertIsNone(outcome["presentation"])
        self.assertEqual(outcome["decisions"][0]["reason"], "lock")


class TestTransitionsAndStarvation(ReviewPoolCase):
    def test_concurrent_delivery_creates_exactly_one_active_lease(self):
        project = self.project("concurrent")
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(
                lambda _index: self.pool.present_next("taskwriter", [project]),
                range(2),
            ))
        presentations = [
            outcome["presentation"] for outcome in outcomes
            if outcome["presentation"] is not None
        ]
        self.assertEqual(len(presentations), 1)
        state = self.pool.get_state("taskwriter", project.path)
        self.assertEqual(
            state["presentation_id"], presentations[0]["presentation_id"]
        )

    def test_presentation_is_not_success_and_lease_blocks_duplicate(self):
        project = self.project("lease")
        first = self.present("taskwriter", [project])
        state = self.pool.get_state("taskwriter", project.path)
        self.assertIsNone(state["sealed_hash"])
        self.assertIsNone(state["last_reviewed_at"])
        self.assertEqual(state["presentation_id"], first["presentation_id"])

        duplicate = self.pool.present_next("taskwriter", [project])
        self.assertIsNone(duplicate["presentation"])
        self.assertEqual(duplicate["decisions"][0]["reason"], "presentation_lease")

        self.clock.advance(seconds=61)
        retry = self.present("taskwriter", [project])
        self.assertEqual(retry["reason"], "never_sealed")
        self.assertNotEqual(retry["presentation_id"], first["presentation_id"])

    def test_only_matching_token_can_seal_once(self):
        project = self.project("token")
        presented = self.present("maintainer", [project])

        with self.assertRaises(ReviewConflict):
            self.pool.complete("maintainer", project.path, "wrong", "fake")
        self.assertIsNone(self.pool.get_state("maintainer", project.path)["sealed_hash"])

        state = self.pool.complete(
            "maintainer", project.path, presented["presentation_id"], "ok"
        )
        self.assertIsNotNone(state["sealed_hash"])
        self.assertEqual(state["result"], "ok")
        self.assertIsNotNone(state["last_reviewed_at"])
        self.assertIsNotNone(state["next_due_at"])

        with self.assertRaises(ReviewConflict):
            self.pool.complete(
                "maintainer", project.path, presented["presentation_id"], "twice"
            )

    def test_defer_is_not_success_and_hash_or_retry_reopens(self):
        project = self.project("defer")
        presented = self.present("taskwriter", [project])
        state = self.pool.defer(
            "taskwriter", project.path, presented["presentation_id"], "blocked"
        )
        self.assertIsNone(state["sealed_hash"])
        self.assertIsNone(state["last_reviewed_at"])
        self.assertEqual(
            self.pool.status("taskwriter", project.path)["reason"], "deferred"
        )

        (project.path / "project.txt").write_text("changed", encoding="utf-8")
        self.assertEqual(
            self.pool.status("taskwriter", project.path)["reason"], "hash_break"
        )

        changed = self.present("taskwriter", [project])
        self.pool.defer(
            "taskwriter", project.path, changed["presentation_id"], "still blocked"
        )
        self.clock.advance(seconds=301)
        self.assertEqual(
            self.pool.status("taskwriter", project.path)["reason"], "never_sealed"
        )

    def test_deferred_easy_does_not_block_medium_but_fresh_easy_does(self):
        blocked = self.project("blocked-easy", effort="easy")
        fresh = self.project("fresh-easy", effort="easy")
        medium = self.project("medium", effort="medium")
        self.pool.set_effort("taskwriter", medium.path, "medium")

        first = self.present("taskwriter", [blocked])
        self.pool.defer(
            "taskwriter", blocked.path, first["presentation_id"], "blocked"
        )

        chosen = self.present("taskwriter", [medium, blocked, fresh])
        self.assertEqual(Path(chosen["project_path"]), fresh.path)
        self.pool.defer(
            "taskwriter", fresh.path, chosen["presentation_id"], "blocked too"
        )

        chosen = self.present("taskwriter", [blocked, fresh, medium])
        self.assertEqual(Path(chosen["project_path"]), medium.path)
        self.assertEqual(chosen["effort"], "medium")

        self.clock.advance(seconds=301)
        # Die alte Medium-Lease ist noch aktiv, aber das wieder geöffnete easy
        # gewinnt unabhängig davon die nächste Auswahl.
        chosen = self.present("taskwriter", [medium, blocked, fresh])
        self.assertEqual(chosen["effort"], "easy")


if __name__ == "__main__":
    unittest.main()
