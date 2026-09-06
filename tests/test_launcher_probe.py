# -*- coding: utf-8 -*-
"""Modell-/Effort-Wahl, Modellsonde, Provider-Fallback und externe Rollen.

Kein Test startet je eine echte Provider-CLI. Die Sonden-Tests benutzen den
laufenden Python-Interpreter als Prozess-Attrappe — sie pruefen damit auch
wirklich, dass ein haengender Prozess beendet wird und keine Waise bleibt.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

from taskplan import launcher
from taskplan.launcher import PROBE_TOKEN, _provider_command, launch, probe

MODELS = {
    "claude": "sonnet",
    "codex": "",
    "agy": "gemini-flash",
    "kimi": "kimi-k3",
}


def _profile(role, provider=""):
    return {
        "provider": provider,
        "role": role,
        "model": MODELS.get(provider, "some-model"),
        "reasoning_effort": "high",
        "continuation": "one_shot",
        "empty_policy": "stop",
        "idle_backoff_seconds": 60,
    }


class _LauncherFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = self.tmp.name
        self.env = {"TASKPLAN_WORKDIR": self.workdir}
        self.prompt = Path(self.workdir) / "EXTERN.txt"
        self.prompt.write_text("externe Rollenanweisung", encoding="utf-8")
        self.stack = [
            mock.patch("taskplan.launcher.runtime_profile", _profile),
            mock.patch("taskplan.launcher.startup_prompt",
                       return_value="authorized startup"),
            mock.patch("taskplan.launcher.get_workflow_prompt_path",
                       return_value=Path(self.workdir) / "TASKSOLVER.txt"),
            mock.patch("taskplan.launcher.shutil.which",
                       side_effect=lambda name: f"C:\\bin\\{name}.exe"),
            mock.patch("taskplan.launcher.active_roles",
                       return_value={role: True for role in
                                     ("tasksolver", "taskwriter",
                                      "maintainer", "operator")}),
            mock.patch("taskplan.launcher.doctor", return_value=0),
            mock.patch("taskplan.launcher.execution_config", return_value={}),
        ]
        for patcher in self.stack:
            patcher.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for patcher in reversed(self.stack):
            patcher.stop()
        self.tmp.cleanup()

    def _run(self, *args, **kwargs):
        """launch() mit gesammelter Ausgabe; liefert (code, text)."""
        runner = kwargs.pop("runner", mock.Mock(
            return_value=mock.Mock(returncode=0)))
        output = io.StringIO()
        kwargs.setdefault("env", self.env)
        with redirect_stdout(output), redirect_stderr(output):
            code = launch(*args, run=runner, **kwargs)
        return code, output.getvalue(), runner


class TestExplicitModelAndEffort(_LauncherFixture):
    def test_flags_override_configuration_for_this_start_only(self):
        command, _ = _provider_command(
            "tasksolver", "claude", env=self.env,
            model="opus", effort="max",
        )
        self.assertEqual(command[command.index("--model") + 1], "opus")
        self.assertEqual(command[command.index("--effort") + 1], "max")
        # Ohne Flags gilt weiterhin die Konfiguration.
        fallback_command, _ = _provider_command(
            "tasksolver", "claude", env=self.env)
        self.assertEqual(
            fallback_command[fallback_command.index("--model") + 1], "sonnet")

    def test_claude_session_name_defaults_to_the_uppercase_key(self):
        command, _ = _provider_command("operator", "claude", env=self.env)
        self.assertEqual(command[command.index("--name") + 1], "OPERATOR")
        named, _ = _provider_command(
            "operator", "claude", env=self.env, session_name="nachtlauf")
        self.assertEqual(named[named.index("--name") + 1], "NACHTLAUF")


class TestExternalRole(_LauncherFixture):
    def _external(self, provider, **kwargs):
        return launcher._provider_commands(
            "ticket-master", provider, env=self.env,
            prompt_path=self.prompt, request="Test",
            model=kwargs.pop("model", "some-model"),
            effort=kwargs.pop("effort", "high"),
            **kwargs,
        )

    def test_prompt_is_delivered_like_a_role_prompt(self):
        with mock.patch("taskplan.launcher.startup_prompt") as startup:
            claude, _ = self._external("claude")
            codex, _ = self._external("codex")
            agy, _ = self._external("agy")
        startup.assert_not_called()
        self.assertEqual(
            claude[0][claude[0].index("--append-system-prompt-file") + 1],
            str(self.prompt))
        escaped = json.dumps(str(self.prompt))[1:-1]
        self.assertTrue(any(
            value.startswith("developer_instructions=") and escaped in value
            for value in codex[0]))
        self.assertEqual(agy[0][agy[0].index("--add-dir") + 1],
                         str(self.prompt.parent))
        self.assertTrue(agy[0][-1].startswith("First read the complete"))
        self.assertTrue(agy[0][-1].endswith("Test"))
        # Codex bekommt den Nutzerauftrag ohne Pfad-Prefix.
        self.assertEqual(codex[0][-1], "Test")

    def test_kimi_boots_headless_then_continues_interactively(self):
        commands, _ = self._external("kimi")
        self.assertEqual(len(commands), 2)
        self.assertIn("--prompt", commands[0])
        self.assertEqual(commands[1][-1], "--continue")
        self.assertNotIn("--prompt", commands[1])

    def test_taskplan_roles_keep_the_single_stage_kimi_contract(self):
        commands, _ = launcher._provider_commands(
            "tasksolver", "kimi", env=self.env)
        self.assertEqual(len(commands), 1)

    def test_label_model_comes_from_the_provider_tables(self):
        with mock.patch("taskplan.launcher.label_runtime",
                        return_value={"model": "opus",
                                      "reasoning_effort": "xhigh"}) as lookup:
            commands, _ = launcher._provider_commands(
                "ticket-master", "claude", env=self.env,
                prompt_path=self.prompt, request="Test",
            )
        lookup.assert_called_once_with("ticket-master", "claude")
        self.assertEqual(commands[0][commands[0].index("--model") + 1], "opus")

    def test_external_role_skips_the_role_gate(self):
        with mock.patch("taskplan.launcher.active_roles",
                        return_value={}) as gate, \
                mock.patch("taskplan.launcher.probe",
                           return_value=(True, "ok")):
            code, text, runner = self._run(
                "", "claude", label="ticket-master",
                prompt_file=str(self.prompt), request="Test",
            )
        gate.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("[TICKET-MASTER]", text)

    def test_external_role_needs_all_three_parts(self):
        code, text, runner = self._run(
            "", "claude", label="ticket-master", request="Test")
        self.assertEqual(code, 1)
        runner.assert_not_called()
        self.assertIn("--prompt-file", text)

    def test_missing_prompt_file_is_refused_before_any_start(self):
        code, text, runner = self._run(
            "", "claude", label="ticket-master", request="Test",
            prompt_file=str(Path(self.workdir) / "fehlt.txt"))
        self.assertEqual(code, 1)
        runner.assert_not_called()
        self.assertIn("nicht gefunden", text)

    def test_failed_kimi_boot_never_opens_the_interactive_stage(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=7))
        with mock.patch("taskplan.launcher.probe", return_value=(True, "ok")):
            code, _text, runner = self._run(
                "", "kimi", label="ticket-master",
                prompt_file=str(self.prompt), request="Test",
                fallback=False, runner=runner,
            )
        self.assertEqual(code, 7)
        self.assertEqual(runner.call_count, 1)


class TestProbe(unittest.TestCase):
    """Erfolgsmerkmal ist der Token im Ausgabestrom, nicht der Exit-Code."""

    def _python(self, code):
        return [sys.executable, "-c", code]

    def test_token_wins_even_when_the_process_never_exits(self):
        # Das gemessene agy-Muster: Token, dann haengen bis zum Kill.
        ok, reason = probe(
            self._python(
                f"import time; print('{PROBE_TOKEN}', flush=True); time.sleep(120)"
            ),
            timeout=30,
        )
        self.assertTrue(ok, reason)
        self.assertIn(PROBE_TOKEN, reason)

    def test_clean_exit_with_token_succeeds(self):
        ok, _reason = probe(self._python(f"print('{PROBE_TOKEN}')"), timeout=30)
        self.assertTrue(ok)

    def test_nonzero_exit_without_token_fails(self):
        ok, reason = probe(
            self._python("import sys; print('unrecognized_model'); sys.exit(1)"),
            timeout=30,
        )
        self.assertFalse(ok)
        self.assertIn("Exit 1", reason)

    def test_silent_process_fails_after_the_timeout(self):
        ok, reason = probe(self._python("import time; time.sleep(60)"), timeout=1)
        self.assertFalse(ok)
        self.assertIn("innerhalb", reason)

    def test_unstartable_command_is_a_failure_not_a_crash(self):
        ok, reason = probe(["taskplan-gibt-es-nicht-42"], timeout=5)
        self.assertFalse(ok)
        self.assertIn("nicht startbar", reason)

    def test_probe_command_shapes_are_provider_specific(self):
        claude = launcher._probe_command("claude", "claude.exe", "sonnet", "high")
        self.assertIn("--strict-mcp-config", claude)
        self.assertEqual(claude[-2], "-p")
        codex = launcher._probe_command("codex", "codex.exe", "", "")
        self.assertEqual(codex[1], "exec")
        self.assertIn("--sandbox", codex)
        self.assertNotIn("--model", codex)
        kimi = launcher._probe_command("kimi", "kimi.exe", "kimi-k3", "max")
        self.assertIn("--output-format", kimi)
        self.assertNotIn("--effort", kimi)


class TestFallbackChain(_LauncherFixture):
    def test_second_candidate_starts_when_the_first_probe_fails(self):
        with mock.patch("taskplan.launcher.probe",
                        side_effect=[(False, "kein Token"), (True, "ok")]):
            code, text, runner = self._run("tasksolver", "claude")
        self.assertEqual(code, 0)
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(runner.call_args[0][0][0].endswith("codex.exe"))
        self.assertIn("[SONDE]", text)

    def test_explicit_model_falls_back_to_the_provider_default_first(self):
        with mock.patch("taskplan.launcher.probe",
                        side_effect=[(False, "kein Token"), (True, "ok")]):
            code, text, runner = self._run(
                "tasksolver", "claude", model="tippfehler")
        self.assertEqual(code, 0)
        started = runner.call_args[0][0]
        self.assertTrue(started[0].endswith("claude.exe"))
        self.assertEqual(started[started.index("--model") + 1], "sonnet")
        self.assertIn("1. claude tippfehler/high", text)
        self.assertIn("2. claude sonnet/high", text)

    def test_no_fallback_limits_the_chain_to_one_candidate(self):
        with mock.patch("taskplan.launcher.probe",
                        return_value=(False, "kein Token")) as probing:
            code, text, runner = self._run(
                "tasksolver", "claude", model="tippfehler", fallback=False)
        self.assertEqual(code, 1)
        self.assertEqual(probing.call_count, 1)
        runner.assert_not_called()
        self.assertIn("Kein Kandidat", text)

    def test_missing_cli_and_missing_model_are_skipped_visibly(self):
        with mock.patch("taskplan.launcher.shutil.which",
                        side_effect=lambda name:
                        "" if name == "agy" else f"C:\\bin\\{name}.exe"), \
                mock.patch("taskplan.launcher.runtime_profile",
                           side_effect=lambda role, provider="": dict(
                               _profile(role, provider),
                               model="" if provider == "kimi"
                               else MODELS.get(provider, "m"))), \
                mock.patch("taskplan.launcher.probe",
                           return_value=(True, "ok")):
            code, text, _runner = self._run("tasksolver", "claude")
        self.assertEqual(code, 0)
        self.assertIn("agy — CLI nicht gefunden", text)
        self.assertIn("kimi — kein Eintrag in [providers.kimi.models]", text)

    def test_configured_fallback_order_is_honoured(self):
        with mock.patch("taskplan.launcher.execution_config",
                        return_value={"fallback_providers":
                                      ["kimi", "unsinn", "claude"]}), \
                mock.patch("taskplan.launcher.probe",
                           side_effect=[(False, "nein"), (True, "ok")]):
            code, text, runner = self._run("tasksolver", "claude")
        self.assertEqual(code, 0)
        self.assertTrue(runner.call_args[0][0][0].endswith("kimi.exe"))
        self.assertNotIn("codex", text.split("[SONDE]")[0].lower())

    def test_no_probe_starts_the_first_viable_candidate_directly(self):
        with mock.patch("taskplan.launcher.probe") as probing:
            code, _text, runner = self._run(
                "tasksolver", "claude", use_probe=False)
        self.assertEqual(code, 0)
        probing.assert_not_called()
        self.assertTrue(runner.call_args[0][0][0].endswith("claude.exe"))

    def test_probe_can_be_switched_off_by_environment(self):
        env = dict(self.env, TASKPLAN_STARTER_PROBE="0")
        with mock.patch("taskplan.launcher.probe") as probing:
            code, _text, _runner = self._run("tasksolver", "claude", env=env)
        self.assertEqual(code, 0)
        probing.assert_not_called()

    def test_dry_run_prints_the_chain_and_never_probes_or_starts(self):
        env = dict(self.env, TASKPLAN_STARTER_DRY_RUN="1")
        with mock.patch("taskplan.launcher.probe") as probing:
            code, text, runner = self._run("tasksolver", "claude", env=env)
        self.assertEqual(code, 0)
        probing.assert_not_called()
        runner.assert_not_called()
        self.assertIn("[KETTE] 1. claude sonnet/high", text)
        self.assertIn("[DRY-RUN]", text)


class TestInteractiveStart(_LauncherFixture):
    def test_only_unset_values_are_asked_and_enter_keeps_the_default(self):
        answers = iter(["agy", "", ""])
        asked = []

        def _ask(prompt):
            asked.append(prompt)
            return next(answers)

        with mock.patch("taskplan.launcher.provider_name", return_value="codex"), \
                mock.patch("taskplan.launcher.model_choices",
                           return_value=("gemini-flash", "gemini-pro")), \
                mock.patch("taskplan.launcher.probe", return_value=(True, "ok")):
            code, _text, runner = self._run(
                "tasksolver", "", interactive=True, ask=_ask)
        self.assertEqual(code, 0)
        self.assertEqual(len(asked), 3)
        self.assertIn("[codex]", asked[0])
        self.assertIn("gemini-flash, gemini-pro", asked[1])
        self.assertIn("low, medium, high", asked[2])
        started = runner.call_args[0][0]
        self.assertTrue(started[0].endswith("agy.exe"))
        self.assertEqual(started[started.index("--model") + 1], "gemini-flash")

    def test_flags_suppress_their_questions(self):
        asked = []

        def _ask(prompt):
            asked.append(prompt)
            return ""

        with mock.patch("taskplan.launcher.probe", return_value=(True, "ok")):
            code, _text, _runner = self._run(
                "tasksolver", "claude", model="opus", effort="max",
                interactive=True, ask=_ask)
        self.assertEqual(code, 0)
        self.assertEqual(asked, [])

    def test_a_short_answer_pipe_falls_back_to_defaults(self):
        def _ask(_prompt):
            raise EOFError

        with mock.patch("taskplan.launcher.provider_name", return_value="claude"), \
                mock.patch("taskplan.launcher.probe", return_value=(True, "ok")):
            code, _text, runner = self._run(
                "tasksolver", "", interactive=True, ask=_ask)
        self.assertEqual(code, 0)
        self.assertTrue(runner.call_args[0][0][0].endswith("claude.exe"))


if __name__ == "__main__":
    unittest.main()
