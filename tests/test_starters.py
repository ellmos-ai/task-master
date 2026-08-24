# -*- coding: utf-8 -*-
"""Packaged launcher assets and central provider command construction."""
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from taskplan.__main__ import main
from taskplan.launcher import _provider_command, launch
from taskplan.starters import get_starter_path, list_starters


class TestPackagedStarterAssets(unittest.TestCase):
    def test_all_three_roles_and_providers_are_packaged(self):
        names = set(list_starters())
        self.assertEqual(len(names), 12)
        for role in ("TASKSOLVER", "TASKWRITER", "MAINTAINER"):
            for provider in ("CLAUDE", "CODEX", "AGY", "KIMI"):
                self.assertIn(f"START-{role}-{provider}.bat", names)

    def test_starters_are_user_neutral_thin_wrappers(self):
        forbidden = ("C:\\Users\\lukas", "gpt-5.", "claude-opus",
                     "claude-sonnet", "gemini-")
        for name in list_starters():
            role, provider = name[6:-4].lower().split("-", 1)
            path = get_starter_path(role, provider)
            text = path.read_text(encoding="utf-8")
            self.assertIn("python -m taskplan launch", text)
            self.assertIn(f"--role {role}", text)
            self.assertIn(f"--provider {provider}", text)
            for needle in forbidden:
                self.assertNotIn(needle, text)

    def test_starter_cli_lists_and_resolves_assets(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["starters", "list"]), 0)
        self.assertIn("START-TASKSOLVER-CODEX.bat", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "starters", "path", "--role", "tasksolver",
                "--provider", "codex",
            ])
        self.assertEqual(code, 0)
        self.assertTrue(Path(output.getvalue().strip()).is_file())


class TestCentralLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = self.tmp.name
        self.profile = {
            "provider": "codex",
            "role": "tasksolver",
            "model": "configured-model",
            "reasoning_effort": "high",
            "continuation": "goal",
            "empty_policy": "keep_goal",
            "idle_backoff_seconds": 60,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_common(self, provider):
        profile = dict(self.profile, provider=provider)
        return (
            mock.patch("taskplan.launcher.runtime_profile",
                       return_value=profile),
            mock.patch("taskplan.launcher.startup_prompt",
                       return_value="authorized startup"),
            mock.patch("taskplan.launcher.get_workflow_prompt_path",
                       return_value=Path(self.workdir) / "TASKSOLVER.txt"),
            mock.patch("taskplan.launcher.shutil.which",
                       side_effect=lambda name: f"C:\\bin\\{name}.exe"),
        )

    def test_provider_is_explicit_in_each_command(self):
        for provider, executable in (
                ("claude", "claude.exe"),
                ("codex", "codex.exe"),
                ("agy", "agy.exe"),
                ("kimi", "kimi.exe")):
            patches = self._patch_common(provider)
            with patches[0], patches[1], patches[2], patches[3]:
                command, _ = _provider_command(
                    "tasksolver", provider,
                    env={"TASKPLAN_WORKDIR": self.workdir},
                )
            self.assertTrue(command[0].endswith(executable))
            self.assertIn("configured-model", command)

    def test_codex_without_taskplan_overrides_uses_cli_defaults(self):
        profile = dict(self.profile, model="", reasoning_effort="")
        with mock.patch("taskplan.launcher.runtime_profile",
                        return_value=profile), \
                mock.patch("taskplan.launcher.startup_prompt",
                           return_value="authorized startup"), \
                mock.patch("taskplan.launcher.get_workflow_prompt_path",
                           return_value=Path(self.workdir) / "TASKSOLVER.txt"), \
                mock.patch("taskplan.launcher.shutil.which",
                           return_value="C:\\bin\\codex.exe"):
            command, _ = _provider_command(
                "tasksolver", "codex",
                env={"TASKPLAN_WORKDIR": self.workdir},
            )
        self.assertNotIn("--model", command)
        self.assertFalse(any(
            value.startswith("model_reasoning_effort=") for value in command
        ))

    def test_codex_inherits_provider_default_overrides(self):
        data = {"providers": {"codex": {
            "models": {"default": "gpt-inherited"},
            "reasoning_effort": {"default": "medium"},
        }}}
        with mock.patch("taskplan.config.load_config", return_value=data), \
                mock.patch("taskplan.launcher.get_workflow_prompt_path",
                           return_value=Path(self.workdir) / "TASKSOLVER.txt"), \
                mock.patch("taskplan.launcher.shutil.which",
                           return_value="C:\\bin\\codex.exe"):
            command, _ = _provider_command(
                "tasksolver", "codex",
                env={"TASKPLAN_WORKDIR": self.workdir},
            )
        self.assertEqual(command[command.index("--model") + 1], "gpt-inherited")
        self.assertIn('model_reasoning_effort="medium"', command)

    def test_codex_explicit_role_overrides_are_host_neutral(self):
        data = {"providers": {"codex": {
            "models": {"default": "gpt-default", "tasksolver": "gpt-explicit"},
            "reasoning_effort": {
                "default": "medium", "tasksolver": "xhigh",
            },
        }}}
        with mock.patch("taskplan.config.load_config", return_value=data), \
                mock.patch("taskplan.launcher.get_workflow_prompt_path",
                           return_value=Path(self.workdir) / "TASKSOLVER.txt"), \
                mock.patch("taskplan.launcher.shutil.which",
                           return_value="C:\\bin\\codex.exe"):
            command, _ = _provider_command(
                "tasksolver", "codex",
                env={"TASKPLAN_WORKDIR": self.workdir},
            )
        self.assertEqual(command[command.index("--model") + 1], "gpt-explicit")
        self.assertIn('model_reasoning_effort="xhigh"', command)
        self.assertEqual(command[command.index("--cd") + 1], self.workdir)

    def test_non_codex_provider_still_requires_taskplan_model(self):
        profile = dict(self.profile, provider="claude", model="")
        with mock.patch("taskplan.launcher.runtime_profile",
                        return_value=profile):
            with self.assertRaisesRegex(ValueError, "Kein Modell konfiguriert"):
                _provider_command(
                    "tasksolver", "claude",
                    env={"TASKPLAN_WORKDIR": self.workdir},
                )

    def test_unattended_permissions_require_explicit_opt_in(self):
        patches = self._patch_common("claude")
        with patches[0], patches[1], patches[2], patches[3]:
            normal, _ = _provider_command(
                "tasksolver", "claude",
                env={"TASKPLAN_WORKDIR": self.workdir},
            )
            trusted, _ = _provider_command(
                "tasksolver", "claude",
                env={
                    "TASKPLAN_WORKDIR": self.workdir,
                    "TASKPLAN_TRUSTED_AUTOMATION": "1",
                },
            )
        self.assertNotIn("--dangerously-skip-permissions", normal)
        self.assertIn("--dangerously-skip-permissions", trusted)

    def test_dry_run_never_starts_provider(self):
        runner = mock.Mock()
        patches = self._patch_common("codex")
        output = io.StringIO()
        env = {
            "TASKPLAN_WORKDIR": self.workdir,
            "TASKPLAN_STARTER_DRY_RUN": "1",
        }
        with mock.patch("taskplan.launcher.active_roles",
                        return_value={"tasksolver": True}), \
                mock.patch("taskplan.launcher.doctor", return_value=0), \
                patches[0], patches[1], patches[2], patches[3], \
                redirect_stdout(output):
            code = launch("tasksolver", "codex", env=env, run=runner)
        self.assertEqual(code, 0)
        runner.assert_not_called()
        self.assertIn("[DRY-RUN]", output.getvalue())

    def test_codex_dry_run_without_taskplan_overrides_uses_cli_defaults(self):
        runner = mock.Mock()
        profile = dict(self.profile, model="", reasoning_effort="")
        output = io.StringIO()
        env = {
            "TASKPLAN_WORKDIR": self.workdir,
            "TASKPLAN_STARTER_DRY_RUN": "1",
        }
        with mock.patch("taskplan.launcher.active_roles",
                        return_value={"tasksolver": True}), \
                mock.patch("taskplan.launcher.doctor", return_value=0), \
                mock.patch("taskplan.launcher.runtime_profile",
                           return_value=profile), \
                mock.patch("taskplan.launcher.startup_prompt",
                           return_value="authorized startup"), \
                mock.patch("taskplan.launcher.get_workflow_prompt_path",
                           return_value=Path(self.workdir) / "TASKSOLVER.txt"), \
                mock.patch("taskplan.launcher.shutil.which",
                           return_value="C:\\bin\\codex.exe"), \
                redirect_stdout(output):
            code = launch("tasksolver", "codex", env=env, run=runner)
        self.assertEqual(code, 0)
        runner.assert_not_called()
        self.assertIn("Codex-Default", output.getvalue())
        self.assertNotIn("--model", output.getvalue())

    def test_agy_schedule_interval_reaches_startup_prompt(self):
        patches = self._patch_common("agy")
        with patches[0], patches[1] as prompt, patches[2], patches[3]:
            _provider_command(
                "tasksolver", "agy",
                env={
                    "TASKPLAN_WORKDIR": self.workdir,
                    "TASKPLAN_AGY_SCHEDULE_MINUTES": "10",
                },
            )
        prompt.assert_called_once_with(
            "tasksolver", "agy", schedule_minutes=10
        )

    def test_agy_schedule_interval_rejects_invalid_values(self):
        patches = self._patch_common("agy")
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaisesRegex(ValueError, "positive Ganzzahl"):
                _provider_command(
                    "tasksolver", "agy",
                    env={
                        "TASKPLAN_WORKDIR": self.workdir,
                        "TASKPLAN_AGY_SCHEDULE_MINUTES": "never",
                    },
                )


class TestKimiProviderCommand(unittest.TestCase):
    """Kimi-Worker: headless (-p), --yolo nur bei Trust-Opt-in, kein --effort.

    Verifiziert gegen Kimi Code CLI 0.29.2 (2026-07-28): die CLI kennt keinen
    interaktiven Startprompt (positional wird als Subcommand abgelehnt) und
    kein --effort-Flag; die Reasoning-Stufe kommt aus default_effort des
    Modells in ~/.kimi-code/config.toml.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _command(self, trusted: bool):
        profile = {
            "provider": "kimi",
            "role": "tasksolver",
            "model": "moonshot-ai/kimi-k3",
            "reasoning_effort": "max",
            "continuation": "one_shot",
            "empty_policy": "stop",
            "idle_backoff_seconds": 60,
        }
        env = {"TASKPLAN_WORKDIR": self.workdir}
        if trusted:
            env["TASKPLAN_TRUSTED_AUTOMATION"] = "1"
        with mock.patch("taskplan.launcher.runtime_profile",
                        return_value=profile), \
                mock.patch("taskplan.launcher.startup_prompt",
                           return_value="authorized startup"), \
                mock.patch("taskplan.launcher.get_workflow_prompt_path",
                           return_value=Path(self.workdir) / "TASKSOLVER.txt"), \
                mock.patch("taskplan.launcher.shutil.which",
                           side_effect=lambda name: f"C:\\bin\\{name}.exe"):
            command, _ = _provider_command("tasksolver", "kimi", env=env)
        return command

    def test_kimi_runs_headless_with_model(self):
        command = self._command(trusted=True)
        self.assertTrue(command[0].endswith("kimi.exe"))
        self.assertIn("--prompt", command)
        self.assertIn("moonshot-ai/kimi-k3", command)

    def test_kimi_yolo_only_with_trust_opt_in(self):
        self.assertIn("--yolo", self._command(trusted=True))
        self.assertNotIn("--yolo", self._command(trusted=False))

    def test_kimi_never_receives_effort_or_interactive_flags(self):
        command = self._command(trusted=True)
        self.assertNotIn("--effort", command)
        self.assertNotIn("--prompt-interactive", command)


if __name__ == "__main__":
    unittest.main()
