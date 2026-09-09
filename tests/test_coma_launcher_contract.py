# -*- coding: utf-8 -*-
"""Optionaler Drift-Test: COMA und eingefrorener Built-in bauen gleiche argv."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from taskplan import launcher

coma_session = pytest.importorskip("coma.session")


@pytest.mark.parametrize("provider", ["claude", "codex", "agy", "kimi"])
def test_external_session_argv_matches_builtin(provider, tmp_path):
    prompt = tmp_path / "Rolle mit Umlaut ä.md"
    prompt.write_text("# Rolle\n", encoding="utf-8")
    env = {"TASKPLAN_WORKDIR": str(tmp_path)}
    kwargs = dict(
        env=env,
        model="test-model",
        effort="high",
        prompt_path=prompt,
        request="Vollständig prüfen.",
        session_name="vertrag",
    )
    fake_executable = str(tmp_path / f"{provider}.exe")
    with mock.patch("taskplan.launcher.shutil.which", return_value=fake_executable), \
            mock.patch("taskplan.launcher._coma_api", return_value=None):
        builtin, builtin_prompt = launcher._provider_commands(
            "reviewer", provider, **kwargs
        )
    with mock.patch("taskplan.launcher.shutil.which", return_value=fake_executable), \
            mock.patch("taskplan.launcher._coma_api", return_value=coma_session):
        shared, shared_prompt = launcher._provider_commands(
            "reviewer", provider, **kwargs
        )
    assert shared_prompt == builtin_prompt
    assert shared == builtin


@pytest.mark.parametrize("provider", ["claude", "codex", "agy", "kimi"])
def test_probe_argv_matches_builtin(provider):
    builtin = launcher._builtin_probe_command(
        provider, f"{provider}.exe", "test-model", "high"
    )
    shared = coma_session.build_probe_command(
        provider, f"{provider}.exe", model="test-model", effort="high"
    )
    assert shared == builtin


def test_prompt_path_is_not_shell_quoted_or_split(tmp_path):
    prompt = Path(tmp_path) / "ä prompt mit Leerzeichen.md"
    prompt.write_text("# Rolle\n", encoding="utf-8")
    plan = coma_session.build_session_plan(
        "claude",
        prompt_file=prompt,
        request="Los.",
        cwd=tmp_path,
        executable="claude-test",
    )
    assert plan.command[plan.command.index("--append-system-prompt-file") + 1] == str(prompt)
