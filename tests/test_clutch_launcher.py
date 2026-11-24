# -*- coding: utf-8 -*-
"""Tests fuer die Clutch-Integration im Launcher (T-20260906-737455509 / E02)."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from taskplan.config import execution_authority, is_clutch_enabled, role_zweck
from taskplan.launcher import Candidate, _candidates, launch


class TestClutchConfig(unittest.TestCase):
    def test_default_authority_is_config(self):
        with mock.patch("taskplan.config.execution_config", return_value={}):
            self.assertEqual(execution_authority("tasksolver"), "config")
            self.assertFalse(is_clutch_enabled("tasksolver"))

    def test_authority_table_per_role(self):
        cfg = {
            "authority": {
                "tasksolver": "clutch",
                "default": "config",
            }
        }
        with mock.patch("taskplan.config.execution_config", return_value=cfg):
            self.assertEqual(execution_authority("tasksolver"), "clutch")
            self.assertTrue(is_clutch_enabled("tasksolver"))
            self.assertEqual(execution_authority("maintainer"), "config")
            self.assertFalse(is_clutch_enabled("maintainer"))

    def test_clutch_roles_list(self):
        cfg = {"clutch_roles": ["tasksolver", "operator"]}
        with mock.patch("taskplan.config.execution_config", return_value=cfg):
            self.assertTrue(is_clutch_enabled("tasksolver"))
            self.assertTrue(is_clutch_enabled("operator"))
            self.assertFalse(is_clutch_enabled("maintainer"))

    def test_global_authority_string(self):
        cfg = {"authority": "clutch"}
        with mock.patch("taskplan.config.execution_config", return_value=cfg):
            self.assertTrue(is_clutch_enabled("tasksolver"))
            self.assertTrue(is_clutch_enabled("maintainer"))

    def test_cli_flags_and_env_override(self):
        cfg = {"clutch_roles": ["tasksolver"]}
        with mock.patch("taskplan.config.execution_config", return_value=cfg):
            # --no-clutch schlaegt config
            self.assertFalse(is_clutch_enabled("tasksolver", cli_no_clutch=True))
            # TASKPLAN_CLUTCH=0 schlaegt config
            self.assertFalse(is_clutch_enabled("tasksolver", env={"TASKPLAN_CLUTCH": "0"}))
            # --clutch aktiviert clutch fuer nicht-clutch Rolle
            self.assertTrue(is_clutch_enabled("maintainer", cli_clutch=True))
            # TASKPLAN_CLUTCH=1 aktiviert clutch
            self.assertTrue(is_clutch_enabled("maintainer", env={"TASKPLAN_CLUTCH": "1"}))

    def test_role_zweck_defaults(self):
        with mock.patch("taskplan.config.execution_config", return_value={}):
            self.assertEqual(role_zweck("tasksolver"), "coding")
            self.assertEqual(role_zweck("maintainer"), "coding")
            self.assertEqual(role_zweck("taskwriter"), "creative")
            self.assertEqual(role_zweck("system-auditor"), "research")
            self.assertEqual(role_zweck("operator"), "general")


class TestClutchLauncher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = self.tmp.name
        self.env = {"TASKPLAN_WORKDIR": self.workdir}
        self.prompt = Path(self.workdir) / "TASKSOLVER.txt"
        self.prompt.write_text("Rollenprompt", encoding="utf-8")

        self.stack = [
            mock.patch("taskplan.launcher.runtime_profile",
                       return_value={"model": "sonnet", "reasoning_effort": "high"}),
            mock.patch("taskplan.launcher.startup_prompt", return_value="startup"),
            mock.patch("taskplan.launcher.get_workflow_prompt_path", return_value=self.prompt),
            mock.patch("taskplan.launcher.shutil.which", side_effect=lambda n: f"/bin/{n}"),
            mock.patch("taskplan.launcher.active_roles", return_value={"tasksolver": True, "maintainer": True}),
            mock.patch("taskplan.launcher.doctor", return_value=0),
            mock.patch("taskplan.launcher.ensure_initialised", return_value=0),
            mock.patch("taskplan.launcher.probe", return_value=(True, "ok")),
        ]
        for patcher in self.stack:
            patcher.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for patcher in reversed(self.stack):
            patcher.stop()
        self.tmp.cleanup()

    def test_clutch_missing_falls_back_to_config(self):
        cfg = {"clutch_roles": ["tasksolver"], "fallback_providers": ["codex"]}
        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=None):
            out = io.StringIO()
            with redirect_stdout(out):
                chain, skipped = _candidates(
                    "tasksolver", "", model="", effort="", fallback=True, env=self.env
                )
            text = out.getvalue()
            self.assertIn("[FALLBACK] clutch nicht verfuegbar oder fehlerhaft", text)
            self.assertGreaterEqual(len(chain), 1)
            self.assertEqual(chain[0].source, "config")

    def test_clutch_answers_and_determines_candidates(self):
        cfg = {"clutch_roles": ["tasksolver"], "fallback_providers": ["agy"]}
        mock_api = mock.Mock()
        mock_api.resolve_clutch_candidates.return_value = (
            (
                Candidate(provider="claude", model="sonnet", effort="high", source="clutch"),
                Candidate(provider="codex", model="gpt-5.6-terra", effort="high", source="clutch"),
            ),
            (),
        )
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (primary,) + tuple(fallbacks)
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())

        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=mock_api):
            chain, skipped = _candidates(
                "tasksolver", "", model="", effort="", fallback=True, env=self.env
            )
            self.assertEqual(chain[0].provider, "claude")
            self.assertEqual(chain[0].source, "clutch")
            self.assertEqual(chain[1].provider, "codex")
            self.assertEqual(chain[1].source, "clutch")

    def test_role_not_in_clutch_roles_uses_config(self):
        cfg = {"clutch_roles": ["tasksolver"], "provider": "codex"}
        mock_api = mock.Mock()
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (primary,)
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())

        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=mock_api):
            chain, _ = _candidates(
                "maintainer", "codex", model="", effort="", fallback=False, env=self.env
            )
            mock_api.resolve_clutch_candidates.assert_not_called()
            self.assertEqual(chain[0].source, "config")

    def test_explicit_provider_flag_overrides_clutch(self):
        cfg = {"clutch_roles": ["tasksolver"]}
        mock_api = mock.Mock()
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (primary,)
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())

        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=mock_api):
            chain, _ = _candidates(
                "tasksolver", "claude", model="", effort="", fallback=False,
                env=self.env, explicit_provider=True,
            )
            mock_api.resolve_clutch_candidates.assert_not_called()
            self.assertEqual(chain[0].source, "config")

    def test_no_fallback_pins_first_candidate(self):
        cfg = {"clutch_roles": ["tasksolver"]}
        mock_api = mock.Mock()
        mock_api.resolve_clutch_candidates.return_value = (
            (
                Candidate(provider="claude", model="sonnet", effort="high", source="clutch"),
            ),
            (),
        )
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (primary,)
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())

        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=mock_api):
            chain, _ = _candidates(
                "tasksolver", "", model="", effort="", fallback=False, env=self.env
            )
            self.assertEqual(len(chain), 1)
            self.assertEqual(chain[0].provider, "claude")
            self.assertEqual(chain[0].source, "clutch")

    def test_kette_output_displays_source(self):
        cfg = {"clutch_roles": ["tasksolver"]}
        mock_api = mock.Mock()
        mock_api.resolve_clutch_candidates.return_value = (
            (
                Candidate(provider="claude", model="sonnet", effort="high", source="clutch"),
            ),
            (),
        )
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (primary,)
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())
        mock_api.build_session_plan.return_value = mock.Mock(commands=(("claude", "-p", "hi"),))

        runner = mock.Mock(return_value=mock.Mock(returncode=0))
        out = io.StringIO()
        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=mock_api), \
                redirect_stdout(out), redirect_stderr(out):
            code = launch("tasksolver", "", env=dict(self.env, TASKPLAN_STARTER_DRY_RUN="1"), run=runner)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("[KETTE] 1. claude sonnet/high (clutch)", text)

    def test_interactive_clutch_choice_uses_clutch_and_skips_model_prompts(self):
        cfg = {"authority": "clutch"}
        mock_api = mock.Mock()
        mock_api.resolve_clutch_candidates.return_value = (
            (
                Candidate(provider="agy", model="gemini-4-hydrogen", effort="high", source="clutch"),
                Candidate(provider="codex", model="gpt-6.2-astra", effort="high", source="clutch"),
            ),
            (),
        )
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (primary,) + tuple(fallbacks)
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())
        mock_api.build_session_plan.return_value = mock.Mock(
            commands=(("agy", "--model", "gemini-4-hydrogen", "--effort", "high", "--prompt-interactive", "hi"),)
        )

        asked = []

        def fake_ask(prompt):
            asked.append(prompt)
            return ""  # Enter accepts default = clutch!

        runner = mock.Mock(return_value=mock.Mock(returncode=0))
        out = io.StringIO()
        with mock.patch("taskplan.config.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher.execution_config", return_value=cfg), \
                mock.patch("taskplan.launcher._coma_api", return_value=mock_api), \
                redirect_stdout(out), redirect_stderr(out):
            code = launch(
                "tasksolver", "",
                env=dict(self.env, TASKPLAN_STARTER_DRY_RUN="1"),
                interactive=True, ask=fake_ask, run=runner,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(asked), 1)
        self.assertIn("[Enter = clutch]", asked[0])
        self.assertIn("[5] clutch", asked[0])
        self.assertIn("[KETTE] 1. agy gemini-4-hydrogen/high (clutch)", out.getvalue())

    def test_stale_agy_model_auto_heals_from_live_models(self):
        mock_api = mock.Mock()
        mock_api.Candidate = Candidate
        mock_api.ordered_candidates = lambda primary, provider_default=None, fallbacks=(): (
            (primary,) + ((provider_default,) if provider_default else ()) + tuple(fallbacks)
        )
        mock_api.available_candidates = lambda candidates, which=None, allow_unverified=False: (candidates, ())
        live_models = ("gemini-4-carbon", "gemini-4-neon", "gemini-4-hydrogen")
        with mock.patch("taskplan.launcher._coma_api", return_value=mock_api), \
                mock.patch("taskplan.launcher.runtime_profile",
                           return_value={"model": "gemini-3.7-flash", "reasoning_effort": "high"}), \
                mock.patch("taskplan.launcher._discover_live_provider_models", return_value=live_models):
            out = io.StringIO()
            with redirect_stdout(out):
                chain, _ = _candidates(
                    "operator", "agy", model="", effort="", fallback=True,
                    env=self.env, explicit_provider=True,
                )
        self.assertIn("nicht mehr in lokaler CLI", out.getvalue())
        self.assertEqual(chain[0].provider, "agy")
        self.assertEqual(chain[0].model, "gemini-4-carbon")
        # Same-provider live fallback models follow before cross-provider fallbacks
        self.assertEqual(chain[1].provider, "agy")
        self.assertEqual(chain[1].model, "gemini-4-neon")
