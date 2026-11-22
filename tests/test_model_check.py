# -*- coding: utf-8 -*-
"""Tests fuer `taskplan runtime --label` und `taskplan check-models`."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from taskplan import config as cfg
from taskplan.__main__ import main
from taskplan.model_check import check_models
from taskplan.runtime import label_runtime_profile


class TestRuntimeLabelResolution(unittest.TestCase):
    def test_runtime_label_resolves_arbitrary_configured_key(self):
        data = {
            "providers": {
                "codex": {
                    "models": {
                        "default": "gpt-6.1-sol",
                        "pingpong": "gpt-6-luna",
                        "ticket-master": "gpt-6.1-sol",
                    },
                    "reasoning_effort": {
                        "default": "high",
                        "pingpong": "max",
                    },
                }
            }
        }
        output = io.StringIO()
        with mock.patch.object(cfg, "load_config", return_value=data), redirect_stdout(output):
            code = main([
                "runtime", "--label", "pingpong", "--provider", "codex", "--field", "model"
            ])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().strip(), "gpt-6-luna")

        output_effort = io.StringIO()
        with mock.patch.object(cfg, "load_config", return_value=data), redirect_stdout(output_effort):
            code_effort = main([
                "runtime", "--label", "pingpong", "--provider", "codex",
                "--field", "reasoning_effort",
            ])
        self.assertEqual(code_effort, 0)
        self.assertEqual(output_effort.getvalue().strip(), "max")

    def test_runtime_label_falls_back_to_provider_default(self):
        data = {
            "providers": {
                "claude": {
                    "models": {"default": "claude-sonnet-5-5"},
                    "reasoning_effort": {"default": "max"},
                }
            }
        }
        with mock.patch.object(cfg, "load_config", return_value=data):
            profile = label_runtime_profile("custom-observer", "claude")
        self.assertEqual(profile["model"], "claude-sonnet-5-5")
        self.assertEqual(profile["reasoning_effort"], "max")
        self.assertEqual(profile["label"], "custom-observer")

    def test_runtime_label_rejects_empty_label(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["runtime", "--label", "   ", "--provider", "codex"])
        self.assertEqual(code, 2)


class TestCheckModelsMode(unittest.TestCase):
    def test_check_models_flags_deprecated_and_hardcoded_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_bat = root / "START-LEGACY.bat"
            bad_bat.write_text(
                "@echo off\n"
                "REM example --model gemini-3.1-pro-high\n"
                "codex exec --model gpt-5.6-luna\n"
                "claude --model claude-sonnet-4-6\n",
                encoding="utf-8",
            )
            bad_toml = root / "taskplan.toml"
            bad_toml.write_text(
                '[providers.agy.models]\ndefault = "gemini-3.7-flash-high"\n',
                encoding="utf-8",
            )
            report = check_models([root], config_path=bad_toml)
        self.assertFalse(report["ok"])
        rules = {item["rule"] for item in report["findings"]}
        self.assertIn("deprecated_gemini_3x", rules)
        self.assertIn("deprecated_gpt_5x", rules)
        self.assertIn("deprecated_claude_4x", rules)
        self.assertIn("hardcoded_model_flag", rules)

    def test_check_models_accepts_dynamic_runtime_starter_and_current_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good_bat = root / "PingPong-Start-Codex.bat"
            good_bat.write_text(
                "@echo off\n"
                'for /f "usebackq delims=" %%I in (`python -m taskplan runtime --label pingpong --provider codex --field model`) do set "PINGPONG_MODEL=%%I"\n'
                'codex exec --model "%PINGPONG_MODEL%"\n',
                encoding="utf-8",
            )
            good_toml = root / "taskplan.toml"
            good_toml.write_text(
                '[providers.codex.models]\npingpong = "gpt-6-luna"\n'
                '[providers.agy.models]\ndefault = "gemini-4-hydrogen-high"\n',
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "check-models", "--path", str(root),
                    "--config", str(good_toml), "--json",
                ])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["findings"], [])

    def test_check_models_skips_archive_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_dir = root / "_archive"
            archive_dir.mkdir()
            (archive_dir / "OLD.bat").write_text("codex --model gpt-5.6-luna\n", encoding="utf-8")
            report = check_models([root], include_config=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["checked_files"], [])
