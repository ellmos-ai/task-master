import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from taskplan import config as cfg
from taskplan import runner
from taskplan.locks import LockView
from taskplan.rotation import last_project, remember_project
from taskplan.selector import SelectorConfig
from taskplan.traversal import Project


class _Store:
    def __init__(self, tasks):
        self.tasks = tasks
        self.db_path = "test.db"

    def list(self, status=None, include_done=False, limit=None, **kwargs):
        rows = self.tasks
        if status:
            rows = [row for row in rows if row["status"] == status]
        elif not include_done:
            rows = [row for row in rows
                    if row["status"] not in ("done", "cancelled")]
        return rows if limit is None else rows[:limit]

    def get(self, task_id):
        return next((row for row in self.tasks if row["id"] == task_id), None)


class TestRotationState(unittest.TestCase):
    def test_round_trip_is_atomic_and_role_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotation.json"
            self.assertTrue(remember_project(path, "maintainer", "/p/a"))
            self.assertEqual(last_project(path, "maintainer"), "/p/a")
            self.assertEqual(last_project(path, "tasksolver"), "")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)

    def test_runner_persists_cursor_between_cli_equivalents(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "rotation.json"
            projects = [
                Project(path=Path("/p/first"), root_id=".AI"),
                Project(path=Path("/p/second"), root_id=".SW"),
            ]
            store = _Store([
                {"id": 1, "status": "done", "assigned_to": "",
                 "project_path": "/p/first"},
                {"id": 2, "status": "done", "assigned_to": "",
                 "project_path": "/p/second"},
            ])
            with mock.patch.object(runner, "TaskClient", return_value=store), \
                    mock.patch.object(runner, "_lock_view",
                                      return_value=(LockView(), "lockmaster")), \
                    mock.patch.object(runner, "active_roles",
                                      return_value={"maintainer": True}), \
                    mock.patch.object(runner, "model_for",
                                      return_value="test-model"), \
                    mock.patch.object(runner, "selector_config",
                                      return_value=SelectorConfig(projects=projects)), \
                    mock.patch.object(runner, "_discover_projects_bounded",
                                      return_value=projects), \
                    mock.patch.object(runner, "rotation_state_file",
                                      return_value=state):
                first = runner.next_work("maintainer")
                second = runner.next_work("maintainer")

            self.assertEqual(Path(first["bundle"]["project_path"]).as_posix(),
                             "/p/first")
            self.assertEqual(Path(second["bundle"]["project_path"]).as_posix(),
                             "/p/second")
            self.assertTrue(second["rotation"]["cursor_persisted"])

    def test_runner_persists_taskwriter_project_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "rotation.json"
            projects = [
                Project(path=Path("/p/first"), root_id=".AI"),
                Project(path=Path("/p/second"), root_id=".SW"),
            ]
            store = _Store([])
            with mock.patch.object(runner, "TaskClient", return_value=store), \
                    mock.patch.object(runner, "_lock_view",
                                      return_value=(LockView(), "lockmaster")), \
                    mock.patch.object(runner, "active_roles",
                                      return_value={"taskwriter": True}), \
                    mock.patch.object(runner, "model_for",
                                      return_value="test-model"), \
                    mock.patch.object(runner, "selector_config",
                                      return_value=SelectorConfig(projects=projects)), \
                    mock.patch.object(runner, "_discover_projects_bounded",
                                      return_value=projects), \
                    mock.patch.object(runner, "rotation_state_file",
                                      return_value=state):
                first = runner.next_work("taskwriter")
                second = runner.next_work("taskwriter")

            self.assertEqual(Path(first["bundle"]["project_path"]).as_posix(),
                             "/p/first")
            self.assertEqual(Path(second["bundle"]["project_path"]).as_posix(),
                             "/p/second")
            self.assertTrue(second["rotation"]["cursor_persisted"])
            self.assertEqual(
                Path(last_project(state, "taskwriter")).as_posix(), "/p/second"
            )

    def test_cli_skip_records_the_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "rotation.json"
            with mock.patch.object(cfg, "rotation_state_file",
                                   return_value=state):
                from taskplan.__main__ import main
                code = main(["skip", "--role", "maintainer",
                             "--project", "/p/skip-me"])
            self.assertEqual(code, 0)
            self.assertEqual(last_project(state, "maintainer"), "/p/skip-me")

    def test_cli_skip_supports_taskwriter_but_not_tasksolver(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "rotation.json"
            with mock.patch.object(cfg, "rotation_state_file",
                                   return_value=state):
                from taskplan.__main__ import main
                writer_code = main([
                    "skip", "--role", "taskwriter",
                    "--project", "/p/writer-skip",
                ])
                solver_code = main([
                    "skip", "--role", "tasksolver",
                    "--project", "/p/solver-skip",
                ])
            self.assertEqual(writer_code, 0)
            self.assertEqual(solver_code, 2)
            self.assertEqual(
                last_project(state, "taskwriter"), "/p/writer-skip"
            )
            self.assertEqual(last_project(state, "tasksolver"), "")

    def test_missing_or_invalid_state_starts_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotation.json"
            self.assertEqual(last_project(path, "maintainer"), "")
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(last_project(path, "maintainer"), "")


if __name__ == "__main__":
    unittest.main()
