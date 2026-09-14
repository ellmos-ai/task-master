# -*- coding: utf-8 -*-
"""Readiness-Gate: `taskplan init` und die Sperre davor (T-20260831-555678565)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from taskplan.client import TaskClient
from taskplan.readiness import (
    READINESS_SCHEMA,
    REPAIR_COMMAND,
    ProjectInitializationTimeout,
    _bounded_project_digest,
    cached_digest,
    clear_index,
    fingerprint_records,
    initialize,
    readiness_row,
    readiness_status,
)
from taskplan.review_pool import ProjectHashError, hash_project, scan_project
from taskplan.traversal import Project


def _sleep_project_worker(connection, path, exclude, cached):
    """Top-level spawn target für den deterministischen Timeout-Test."""
    time.sleep(2)


class ReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = TaskClient(db_path=str(self.root / "taskplan.db"))

    def make_project(self, name: str, text: str = "inhalt") -> Project:
        path = self.root / name
        path.mkdir()
        (path / "TODO.md").write_text(text, encoding="utf-8")
        return Project(path=path, root_id="test")

    # --- Gate ------------------------------------------------------------
    def test_fresh_install_is_not_ready_and_names_the_repair(self) -> None:
        status = readiness_status(self.store)
        self.assertFalse(status["ready"])
        self.assertEqual(status["state"], "missing")
        self.assertEqual(status["repair"], REPAIR_COMMAND)
        self.assertIn(REPAIR_COMMAND, status["reason"])

    def test_init_makes_it_ready_and_is_idempotent(self) -> None:
        projects = [self.make_project("a"), self.make_project("b")]
        first = initialize(self.store, projects)
        self.assertTrue(first["ready"])
        self.assertEqual(first["projects_total"], 2)
        self.assertEqual(first["projects_indexed"], 2)
        self.assertTrue(readiness_status(self.store)["ready"])

        second = initialize(self.store, projects)
        self.assertTrue(second["ready"])
        row = readiness_row(self.store)
        self.assertEqual(row["schema_version"], READINESS_SCHEMA)
        self.assertEqual(row["projects_total"], 2)

    def test_init_emits_project_progress_and_timeout_policy(self) -> None:
        events: list[dict] = []
        report = initialize(self.store, [self.make_project("a")], progress=events.append)
        self.assertTrue(report["ready"])
        self.assertEqual(report["project_timeout_seconds"], 30.0)
        self.assertTrue(any(event["state"] == "started" for event in events))
        self.assertTrue(any(event["state"] == "completed" for event in events))

    def test_project_measurement_timeout_is_bounded_and_identified(self) -> None:
        project = self.make_project("slow")
        started = time.monotonic()
        with self.assertRaises(ProjectInitializationTimeout) as context:
            _bounded_project_digest(
                project.path,
                (),
                None,
                0.1,
                worker=_sleep_project_worker,
            )
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(context.exception.project_path, project.path)
        self.assertEqual(context.exception.operation, "scan_project")

    def test_timeout_stays_blocking_even_with_skip_unreadable(self) -> None:
        project = self.make_project("slow")
        timeout = ProjectInitializationTimeout(project.path, "scan_project", 0.1)
        with mock.patch("taskplan.readiness._bounded_project_digest", side_effect=timeout):
            report = initialize(
                self.store,
                [project],
                skip_unreadable=True,
                project_timeout_seconds=0.1,
            )
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["timeouts"]), 1)
        self.assertIn("Zeitlimit", report["reason"])

    def test_changed_exclude_patterns_invalidate_readiness(self) -> None:
        initialize(self.store, [self.make_project("a")], exclude=())
        self.assertTrue(readiness_status(self.store, ())["ready"])
        stale = readiness_status(self.store, ("*.md",))
        self.assertFalse(stale["ready"])
        self.assertEqual(stale["state"], "policy-changed")
        self.assertIn(REPAIR_COMMAND, stale["reason"])

    # --- Abbruch ---------------------------------------------------------
    def test_unreadable_project_leaves_not_ready(self) -> None:
        good = self.make_project("a")
        missing = Project(path=self.root / "weg", root_id="test")
        report = initialize(self.store, [good, missing])
        self.assertFalse(report["ready"])
        self.assertEqual(report["projects_skipped"], 1)
        self.assertIsNone(readiness_row(self.store))
        self.assertFalse(readiness_status(self.store)["ready"])

    def test_unreadable_project_can_be_skipped_explicitly(self) -> None:
        report = initialize(
            self.store,
            [self.make_project("a"), Project(path=self.root / "weg", root_id="test")],
            skip_unreadable=True,
        )
        self.assertTrue(report["ready"])
        self.assertEqual(readiness_row(self.store)["projects_skipped"], 1)

    def test_partial_index_survives_an_aborted_run(self) -> None:
        good = self.make_project("a")
        initialize(self.store, [good, Project(path=self.root / "weg", root_id="test")])
        # Nicht bereit, aber die bereits gemessene Arbeit ist erhalten:
        self.assertFalse(readiness_status(self.store)["ready"])
        conn = self.store._get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM taskplan_project_index"
            ).fetchone()[0]
        finally:
            self.store._close_conn(conn)
        self.assertEqual(count, 1)

    # --- Nebenwirkungsfreiheit -------------------------------------------
    def test_init_presents_nothing_and_touches_no_task(self) -> None:
        initialize(self.store, [self.make_project("a")])
        conn = self.store._get_conn()
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            reviews = 0
            if "taskplan_project_reviews" in tables:
                reviews = conn.execute(
                    "SELECT COUNT(*) FROM taskplan_project_reviews"
                ).fetchone()[0]
            events = 0
            if "taskplan_project_review_events" in tables:
                events = conn.execute(
                    "SELECT COUNT(*) FROM taskplan_project_review_events"
                ).fetchone()[0]
        finally:
            self.store._close_conn(conn)
        self.assertEqual(reviews, 0, "init darf kein Projekt praesentieren")
        self.assertEqual(events, 0, "init darf kein Review-Ereignis schreiben")

    # --- Index -----------------------------------------------------------
    def test_cached_digest_equals_the_direct_hash(self) -> None:
        project = self.make_project("a")
        self.assertEqual(
            cached_digest(self.store, project.path).value,
            hash_project(project.path).value,
        )

    def test_second_call_does_not_read_the_files_again(self) -> None:
        project = self.make_project("a")
        cached_digest(self.store, project.path)
        reads: list[str] = []
        real_open = Path.open

        def counting_open(self, *args, **kwargs):  # noqa: ANN001
            if args and "b" in str(args[0]):
                reads.append(str(self))
            return real_open(self, *args, **kwargs)

        Path.open = counting_open
        try:
            cached_digest(self.store, project.path)
        finally:
            Path.open = real_open
        self.assertEqual(reads, [], "unveraenderte Projekte duerfen nicht neu gelesen werden")

    def test_content_change_is_detected(self) -> None:
        project = self.make_project("a", "alt")
        before = cached_digest(self.store, project.path).value
        time.sleep(0.01)
        (project.path / "TODO.md").write_text("neu und laenger", encoding="utf-8")
        after = cached_digest(self.store, project.path).value
        self.assertNotEqual(before, after)

    def test_new_file_changes_the_fingerprint(self) -> None:
        project = self.make_project("a")
        before = fingerprint_records(scan_project(project.path))
        (project.path / "ZWEITE.md").write_text("x", encoding="utf-8")
        self.assertNotEqual(before, fingerprint_records(scan_project(project.path)))

    def test_rebuild_drops_the_index_and_the_marker(self) -> None:
        initialize(self.store, [self.make_project("a")])
        clear_index(self.store)
        self.assertIsNone(readiness_row(self.store))

    def test_missing_project_raises_a_hash_error(self) -> None:
        with self.assertRaises(ProjectHashError):
            cached_digest(self.store, self.root / "gibtsnicht")


if __name__ == "__main__":
    unittest.main()
