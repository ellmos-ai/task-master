# -*- coding: utf-8 -*-
"""Optionaler Drift-Test: COMA und eingefrorener Built-in bauen gleiche argv."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from taskplan import launcher

try:
    import coma.session as coma_session
except (ImportError, ModuleNotFoundError) as exc:
    raise unittest.SkipTest("COMA-Sitzungsvertrag ist optional") from exc


class TestComaLauncherContract(unittest.TestCase):
    def test_external_session_argv_matches_builtin(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            prompt = tmp_path / "Rolle mit Umlaut ä.md"
            prompt.write_text("# Rolle\n", encoding="utf-8")
            env = {"TASKPLAN_WORKDIR": str(tmp_path)}
            for provider in ("claude", "codex", "agy", "kimi"):
                with self.subTest(provider=provider):
                    kwargs = dict(
                        env=env,
                        model="test-model",
                        effort="high",
                        prompt_path=prompt,
                        request="Vollständig prüfen.",
                        session_name="vertrag",
                    )
                    fake_executable = str(tmp_path / f"{provider}.exe")
                    with (
                        mock.patch("taskplan.launcher.shutil.which", return_value=fake_executable),
                        mock.patch("taskplan.launcher._coma_api", return_value=None),
                    ):
                        builtin, builtin_prompt = launcher._provider_commands(
                            "reviewer", provider, **kwargs
                        )
                    with (
                        mock.patch("taskplan.launcher.shutil.which", return_value=fake_executable),
                        mock.patch("taskplan.launcher._coma_api", return_value=coma_session),
                    ):
                        shared, shared_prompt = launcher._provider_commands(
                            "reviewer", provider, **kwargs
                        )
                    self.assertEqual(shared_prompt, builtin_prompt)
                    self.assertEqual(shared, builtin)

    def test_probe_argv_matches_builtin(self):
        for provider in ("claude", "codex", "agy", "kimi"):
            with self.subTest(provider=provider):
                builtin = launcher._builtin_probe_command(
                    provider, f"{provider}.exe", "test-model", "high"
                )
                shared = coma_session.build_probe_command(
                    provider, f"{provider}.exe", model="test-model", effort="high"
                )
                self.assertEqual(shared, builtin)

    def test_prompt_path_is_not_shell_quoted_or_split(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            prompt = tmp_path / "ä prompt mit Leerzeichen.md"
            prompt.write_text("# Rolle\n", encoding="utf-8")
            plan = coma_session.build_session_plan(
                "claude",
                prompt_file=prompt,
                request="Los.",
                cwd=tmp_path,
                executable="claude-test",
            )
            self.assertEqual(
                plan.command[plan.command.index("--append-system-prompt-file") + 1],
                str(prompt),
            )


if __name__ == "__main__":
    unittest.main()
