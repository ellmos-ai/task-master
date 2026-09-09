# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taskplan import runner
from taskplan.client import TaskClient
from taskplan.review_pool import ReviewPolicy, ReviewPool
from taskplan.selector import SelectorConfig
from taskplan.traversal import Project


class FakeLockView:
    extra_rules = []

    def __init__(self, locked=()):
        self.locked = {Path(path) for path in locked}

    def allows_selection(self, path):
        return Path(path) not in self.locked

    def allows(self, path, _operation):
        return self.allows_selection(path)


class TestReviewPoolRunnerIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "taskplan.db"
        self.state = self.root / "rotation.json"
        self.client = TaskClient(self.db, agent_id="test")
        self.policy = ReviewPolicy(
            review_interval_seconds=3600,
            retry_interval_seconds=300,
            presentation_lease_seconds=120,
        )
        self.projects = []
        for name in ("a", "b"):
            path = self.root / name
            path.mkdir()
            (path / "project.txt").write_text(name, encoding="utf-8")
            self.projects.append(Project(path, ".TEST"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_next(self, role, *, locked=()):
        config = SelectorConfig(
            projects=list(self.projects), review_pool_enabled=True
        )
        with mock.patch.object(runner, "TaskClient", return_value=self.client), \
                mock.patch.object(runner, "active_roles", return_value={role: True}), \
                mock.patch.object(runner, "selector_config", return_value=config), \
                mock.patch.object(runner, "review_pool_config", return_value=self.policy), \
                mock.patch.object(runner, "_discover_projects_bounded",
                                  return_value=(list(self.projects), {})), \
                mock.patch.object(runner, "rotation_state_file", return_value=self.state), \
                mock.patch.object(runner, "_lock_view",
                                  return_value=(FakeLockView(locked), "test")):
            return runner.next_work(role)

    def test_next_persists_presentation_before_delivery(self):
        work = self.run_next("taskwriter")
        self.assertIsNotNone(work["bundle"])
        self.assertEqual(work["review"]["reason"], "never_presented")
        self.assertTrue(work["review"]["presentation_id"])

        pool = ReviewPool(self.client, policy=self.policy)
        state = pool.get_state("taskwriter", work["bundle"]["project_path"])
        self.assertEqual(state["presentation_id"], work["review"]["presentation_id"])
        self.assertIsNone(state["sealed_hash"])

    def test_active_lease_moves_to_next_project_and_then_no_work(self):
        first = self.run_next("taskwriter")
        second = self.run_next("taskwriter")
        third = self.run_next("taskwriter")
        self.assertNotEqual(
            first["bundle"]["project_path"], second["bundle"]["project_path"]
        )
        self.assertIsNone(third["bundle"])
        reasons = {item["reason"] for item in third["review"]["diagnostics"]}
        self.assertEqual(reasons, {"presentation_lease"})

    def test_confirmed_completion_suppresses_unchanged_project(self):
        first = self.run_next("maintainer")
        pool = ReviewPool(self.client, policy=self.policy)
        pool.complete(
            "maintainer",
            first["bundle"]["project_path"],
            first["review"]["presentation_id"],
            "geprüft",
        )
        # Das zweite, nie präsentierte Projekt gewinnt; danach ist nur das
        # erste unverändert versiegelt und das zweite geleast.
        second = self.run_next("maintainer")
        self.assertNotEqual(
            first["bundle"]["project_path"], second["bundle"]["project_path"]
        )
        third = self.run_next("maintainer")
        self.assertIsNone(third["bundle"])
        by_path = {
            item["project_path"]: item["reason"]
            for item in third["review"]["diagnostics"]
        }
        self.assertEqual(by_path[first["bundle"]["project_path"]], "unchanged_sealed")

    def test_role_state_is_independent_in_runner(self):
        writer = self.run_next("taskwriter")
        ReviewPool(self.client, policy=self.policy).complete(
            "taskwriter", writer["bundle"]["project_path"],
            writer["review"]["presentation_id"], "erfasst"
        )
        maintainer = self.run_next("maintainer")
        self.assertEqual(maintainer["review"]["reason"], "never_presented")

    def test_lock_is_visible_in_diagnostics_and_sibling_runs(self):
        locked = self.projects[0].path
        work = self.run_next("maintainer", locked=(locked,))
        self.assertEqual(Path(work["bundle"]["project_path"]), self.projects[1].path)
        diagnostic = {
            Path(item["project_path"]): item["reason"]
            for item in work["review"]["diagnostics"]
        }
        self.assertEqual(diagnostic[locked], "lock")

    def test_unclassified_writer_task_keeps_precedence(self):
        task = self.client.add(
            "Noch einstufen", project_path=str(self.projects[1].path),
            root_id=".TEST"
        )
        work = self.run_next("taskwriter")
        self.assertEqual(work["bundle"]["task_ids"], [task["id"]])
        self.assertNotIn("review", work)


if __name__ == "__main__":
    unittest.main()
