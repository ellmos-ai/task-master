# -*- coding: utf-8 -*-
"""Provider-, Goal- und Timeout-Vertrag der nutzerneutralen Worker-Runtime."""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from taskplan import config as cfg
from taskplan import runner
from taskplan.__main__ import main
from taskplan.runtime import apply_backoff, runtime_profile, startup_prompt


class TestProviderModels(unittest.TestCase):
    def test_provider_role_model_beats_legacy_models(self):
        data = {
            "execution": {"provider": "codex"},
            "models": {"default": "legacy"},
            "providers": {
                "codex": {
                    "models": {"default": "gpt-default", "tasksolver": "gpt-solver"},
                    "reasoning_effort": {"default": "high", "tasksolver": "xhigh"},
                }
            },
        }
        with mock.patch.object(cfg, "load_config", return_value=data):
            self.assertEqual(cfg.model_for("tasksolver"), "gpt-solver")
            self.assertEqual(cfg.model_for("taskwriter"), "gpt-default")
            profile = cfg.provider_runtime("tasksolver")
        self.assertEqual(profile["reasoning_effort"], "xhigh")
        self.assertEqual(profile["continuation"], "goal")

    def test_legacy_models_remain_compatible(self):
        with mock.patch.object(cfg, "load_config", return_value={
                "models": {"default": "sonnet", "tasksolver": "opus"}}), \
                mock.patch.dict(cfg.os.environ, {"TASKPLAN_PROVIDER": ""}):
            self.assertEqual(cfg.model_for("tasksolver"), "opus")
            self.assertEqual(cfg.model_for("taskwriter"), "sonnet")

    def test_explicit_provider_is_user_neutral(self):
        data = {"providers": {"codex": {"models": {"default": "gpt"}}}}
        with mock.patch.object(cfg, "load_config", return_value=data):
            self.assertEqual(runtime_profile("maintainer", "codex")["model"], "gpt")

    def test_explicit_provider_never_inherits_legacy_other_provider_model(self):
        data = {
            "models": {"default": "claude-sonnet"},
            "providers": {"codex": {"continuation": "goal"}},
        }
        with mock.patch.object(cfg, "load_config", return_value=data):
            self.assertEqual(cfg.model_for("tasksolver", "codex"), "")


class TestCodexGoalPrompt(unittest.TestCase):
    def test_codex_gets_explicit_persisted_goal(self):
        data = {
            "language": {"prompts": "de"},
            "providers": {"codex": {
                "continuation": "goal",
                "empty_policy": "keep_goal",
                "idle_backoff_seconds": 45,
            }},
        }
        with mock.patch.object(cfg, "load_config", return_value=data):
            prompt = startup_prompt("tasksolver", "codex", "de")
        self.assertIn("persistiertes Goal", prompt)
        self.assertIn("genau ein", prompt)
        self.assertIn("Exit 3", prompt)
        self.assertIn("45 Sekunden", prompt)
        self.assertIn("TASKPLAN-System- und Modell-Preflight", prompt)
        self.assertIn("erst danach den Selektor", prompt)

    def test_one_shot_provider_does_not_request_goal(self):
        data = {"providers": {"other": {"continuation": "one_shot"}}}
        with mock.patch.object(cfg, "load_config", return_value=data):
            prompt = startup_prompt("taskwriter", "other", "de")
        self.assertNotIn("persistiertes Goal", prompt)
        self.assertIn("genau einen TASKPLAN-Durchlauf", prompt)

    def test_agy_prompt_does_not_claim_developer_instruction_delivery(self):
        data = {"providers": {"agy": {"continuation": "one_shot"}}}
        with mock.patch.object(cfg, "load_config", return_value=data):
            prompt = startup_prompt("maintainer", "agy", "de")
        self.assertNotIn("Developer-Anweisung", prompt)
        self.assertIn("mit lokalem Pfad benannte Rollen-Prompt", prompt)

    def test_non_solver_still_starts_with_selector(self):
        data = {"providers": {"claude": {"continuation": "one_shot"}}}
        with mock.patch.object(cfg, "load_config", return_value=data):
            prompt = startup_prompt("maintainer", "claude", "de")
        self.assertIn(
            "python -m taskplan next --role maintainer --json", prompt
        )

    def test_runtime_cli_exposes_fields_for_thin_starters(self):
        data = {"providers": {"codex": {"models": {"tasksolver": "gpt-x"}}}}
        output = io.StringIO()
        with mock.patch.object(cfg, "load_config", return_value=data), redirect_stdout(output):
            code = main(["runtime", "--role", "tasksolver", "--provider", "codex",
                         "--field", "model"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().strip(), "gpt-x")

    def test_backoff_uses_a_real_injected_timer(self):
        calls = []
        data = {"providers": {"codex": {"idle_backoff_seconds": 17}}}
        with mock.patch.object(cfg, "load_config", return_value=data):
            seconds = apply_backoff("tasksolver", "codex", sleeper=calls.append)
        self.assertEqual(seconds, 17)
        self.assertEqual(calls, [17])


class TestDiscoveryTimeout(unittest.TestCase):
    def test_all_exit_codes_have_stable_names_and_two_languages(self):
        self.assertEqual(
            {code: status["name"] for code, status in runner.EXIT_STATUS.items()},
            {
                0: "BUNDLE_READY",
                1: "NO_WORK",
                2: "ROLE_DISABLED",
                3: "RETRYABLE_SELECTOR_ERROR",
            },
        )
        for status in runner.EXIT_STATUS.values():
            self.assertTrue(status["de"])
            self.assertTrue(status["en"])

    def test_bounded_discovery_returns_instead_of_hanging(self):
        expired = subprocess.TimeoutExpired(["python", "discovery"], 0.02)
        with mock.patch.object(runner.subprocess, "run", side_effect=expired):
            with self.assertRaises(runner.ProjectDiscoveryTimeout):
                runner._discover_projects_bounded(0.02)

    def test_retryable_selector_error_has_exit_code_three(self):
        work = {
            "role": "taskwriter", "active": True, "bundle": None,
            "retryable": True, "reason": "timeout",
        }
        with mock.patch.object(runner, "next_work", return_value=work):
            self.assertEqual(runner.run("taskwriter", as_json=True), 3)

    def test_json_exit_code_is_human_and_machine_readable(self):
        work = {
            "role": "taskwriter", "active": True, "bundle": None,
            "retryable": True, "reason": "timeout",
        }
        output = io.StringIO()
        with mock.patch.object(runner, "next_work", return_value=work), \
                mock.patch.object(runner, "prompt_language", return_value="en"), \
                redirect_stdout(output):
            code = runner.run("taskwriter", as_json=True)
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["exit"]["code"], 3)
        self.assertEqual(
            payload["exit"]["name"], "RETRYABLE_SELECTOR_ERROR"
        )
        self.assertIn("Retryable selector", payload["exit"]["meaning"])

    def test_console_spells_out_exit_one(self):
        work = {
            "role": "maintainer", "active": True, "bundle": None,
            "reason": "nothing eligible",
            "db": "C:/tasks.db", "lock_provider": "lockmaster", "model": "",
        }
        output = io.StringIO()
        with mock.patch.object(runner, "next_work", return_value=work), \
                mock.patch.object(runner, "prompt_language", return_value="de"), \
                redirect_stdout(output):
            code = runner.run("maintainer")
        self.assertEqual(code, 1)
        self.assertIn("Exit 1 — Rolle aktiv", output.getvalue())
        self.assertIn("[NO_WORK]", output.getvalue())

    def test_timeout_uses_last_known_good_instead_of_exit_three(self):
        from taskplan.discovery import DiscoveryResult
        from taskplan.selector import Bundle
        from taskplan.traversal import Project

        fallback = DiscoveryResult(
            projects=[Project(Path("C:/project"), "root")],
            source="stale_cache",
            degraded=True,
        )
        bundle = Bundle(
            mode="deep", effort="", root_id="root",
            project_path="C:/project", tasks=[],
        )
        with mock.patch.object(runner, "active_roles", return_value={
                "taskwriter": True, "tasksolver": True, "maintainer": True}), \
                mock.patch.object(runner, "_lock_view",
                                  return_value=(mock.Mock(
                                      extra_rules=[],
                                      allows=lambda *_: True), "lockmaster")), \
                mock.patch.object(runner, "_discover_projects_bounded",
                                  side_effect=runner.ProjectDiscoveryTimeout()), \
                mock.patch("taskplan.discovery.fallback_after_failure",
                           return_value=fallback), \
                mock.patch.object(runner, "next_bundle", return_value=bundle), \
                mock.patch.object(runner, "remember_project", return_value=True), \
                mock.patch.object(runner, "last_project", return_value=""):
            work = runner.next_work("maintainer")
        self.assertNotIn("retryable", work)
        self.assertEqual(work["bundle"]["project_path"], "C:/project")
        self.assertEqual(work["discovery"]["source"], "stale_cache")
        self.assertEqual(
            work["discovery"]["trigger_error"], "project_discovery_timeout"
        )


if __name__ == "__main__":
    unittest.main()
