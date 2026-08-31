# -*- coding: utf-8 -*-
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taskplan import api, config as cfg
from taskplan.__main__ import main
from taskplan.review_pool import ReviewPool
from taskplan.traversal import Project


class TestReviewCliAndApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "taskplan.db"
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "work.txt").write_text("eins", encoding="utf-8")
        self.config = {
            "storage": {"path": str(self.db)},
            "review_pool": {
                "review_interval_seconds": 3600,
                "retry_interval_seconds": 300,
                "presentation_lease_seconds": 120,
            },
        }
        self.config_patch = mock.patch.object(
            cfg, "load_config", return_value=self.config
        )
        self.config_patch.start()
        api.init(str(self.db), agent_id="test")

    def tearDown(self):
        self.config_patch.stop()
        api._client = None
        self.tmp.cleanup()

    def presentation(self, role="taskwriter"):
        pool = ReviewPool(api.get_client(), policy=cfg.review_pool_config())
        selected = pool.present_next(role, [Project(self.project, ".TEST")])
        return selected["presentation"]

    def cli(self, args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_status_is_machine_readable(self):
        code, output, errors = self.cli([
            "review", "status", "--role", "taskwriter",
            "--project", str(self.project), "--json",
        ])
        self.assertEqual(code, 0, errors)
        payload = json.loads(output)
        self.assertEqual(payload["reason"], "never_presented")
        self.assertTrue(payload["eligible"])

    def test_cli_complete_requires_and_consumes_presentation_token(self):
        presented = self.presentation("maintainer")
        code, output, errors = self.cli([
            "review", "complete", "--role", "maintainer",
            "--project", str(self.project),
            "--presentation-id", presented["presentation_id"],
            "--result", "geprüft", "--json",
        ])
        self.assertEqual(code, 0, errors)
        state = json.loads(output)
        self.assertEqual(state["result"], "geprüft")
        self.assertTrue(state["sealed_hash"])

        code, _output, errors = self.cli([
            "review", "complete", "--role", "maintainer",
            "--project", str(self.project),
            "--presentation-id", presented["presentation_id"],
            "--result", "doppelt",
        ])
        self.assertEqual(code, 1)
        self.assertIn("Präsentationstoken", errors)

    def test_cli_defer_and_unseal_are_logged_not_success(self):
        presented = self.presentation()
        code, output, errors = self.cli([
            "review", "defer", "--role", "taskwriter",
            "--project", str(self.project),
            "--presentation-id", presented["presentation_id"],
            "--reason", "Blocker", "--json",
        ])
        self.assertEqual(code, 0, errors)
        state = json.loads(output)
        self.assertIsNone(state["sealed_hash"])
        self.assertEqual(state["defer_reason"], "Blocker")

        code, output, errors = self.cli([
            "review", "unseal", "--role", "taskwriter",
            "--project", str(self.project), "--reason", "manuell", "--json",
        ])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["manual_unseal_reason"], "manuell")

    def test_existing_skip_command_can_defer_a_presented_project(self):
        presented = self.presentation("maintainer")
        code, output, errors = self.cli([
            "skip", "--role", "maintainer", "--project", str(self.project),
            "--presentation-id", presented["presentation_id"],
            "--reason", "temporärer Blocker",
        ])
        self.assertEqual(code, 0, errors)
        self.assertIn("deferiert", output)
        state = ReviewPool(
            api.get_client(), policy=cfg.review_pool_config()
        ).get_state("maintainer", self.project)
        self.assertEqual(state["defer_reason"], "temporärer Blocker")

    def test_api_exposes_status_complete_defer_unseal_and_effort(self):
        self.assertEqual(
            api.review_status("taskwriter", str(self.project))["reason"],
            "never_presented",
        )
        effort = api.set_review_effort("taskwriter", str(self.project), "medium")
        self.assertEqual(effort["effort"], "medium")

        presented = self.presentation()
        deferred = api.defer_review(
            "taskwriter", str(self.project), presented["presentation_id"], "wartet"
        )
        self.assertIsNone(deferred["last_reviewed_at"])
        api.unseal_review("taskwriter", str(self.project), "operator")
        self.assertEqual(
            api.review_status("taskwriter", str(self.project))["reason"],
            "manual_unseal",
        )

        # Manueller Break öffnet den Kandidaten neu; erst dessen bestätigter
        # Token darf das API-Siegel schreiben.
        presented = self.presentation()
        completed = api.complete_review(
            "taskwriter", str(self.project), presented["presentation_id"], "fertig"
        )
        self.assertEqual(completed["result"], "fertig")

    def test_cli_rejects_missing_required_values(self):
        code, _output, errors = self.cli(["review", "complete"])
        self.assertEqual(code, 2)
        self.assertIn("Nutzung", errors)


if __name__ == "__main__":
    unittest.main()
