# -*- coding: utf-8 -*-
"""Provider-neutral launcher for TASKPLAN role workers.

The packaged ``START-*.bat`` files are deliberately tiny wrappers around this
module. Provider command lines, prompt provenance, runtime lookup, and dry-run
behaviour therefore have one tested implementation instead of nine drifting
copies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Mapping, Sequence

from .config import active_roles
from .doctor import run as doctor
from .runtime import normalize_role, runtime_profile, startup_prompt
from .workflows import get_workflow_prompt_path

PROVIDERS = ("claude", "codex", "agy")
TRUST_ENV = "TASKPLAN_TRUSTED_AUTOMATION"
WORKDIR_ENV = "TASKPLAN_WORKDIR"
DRY_RUN_ENV = "TASKPLAN_STARTER_DRY_RUN"
CLAUDE_MCP_ENV = "TASKPLAN_CLAUDE_MCP_CONFIG"


def normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(
            f"Unbekannter Provider {provider!r}; erlaubt: {', '.join(PROVIDERS)}"
        )
    return normalized


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _workdir(env: Mapping[str, str]) -> Path:
    raw = env.get(WORKDIR_ENV, "").strip()
    path = Path(raw).expanduser() if raw else Path.cwd()
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(
            f"{WORKDIR_ENV} ist kein vorhandenes Verzeichnis: {resolved}"
        )
    return resolved


def _provider_command(
    role: str,
    provider: str,
    *,
    env: Mapping[str, str],
) -> tuple[list[str], Path]:
    profile = runtime_profile(role, provider)
    model = str(profile["model"]).strip()
    effort = str(profile["reasoning_effort"]).strip()
    if not model:
        raise ValueError(
            f"Kein Modell konfiguriert: "
            f"[providers.{provider}.models] {role} = \"...\""
        )
    if not effort:
        raise ValueError(
            f"Kein Reasoning/Thinking konfiguriert: "
            f"[providers.{provider}.reasoning_effort] {role} = \"...\""
        )

    prompt_path = get_workflow_prompt_path(role.upper())
    request = startup_prompt(role, provider)
    trusted = _truthy(env.get(TRUST_ENV, ""))
    executable = shutil.which(provider)
    if provider == "agy":
        executable = shutil.which("agy")
    if not executable:
        raise ValueError(f"CLI für Provider {provider!r} wurde nicht gefunden.")

    if provider == "codex":
        developer = (
            "Read and follow the authorized role instructions in "
            f"{prompt_path}. Read the file completely; it is the canonical "
            "source for this session."
        )
        command = [
            executable,
            "--model", model,
            "--config", f"model_reasoning_effort={json.dumps(effort)}",
            "--config", f"developer_instructions={json.dumps(developer)}",
        ]
        if trusted:
            command.extend([
                "--sandbox", "danger-full-access",
                "--ask-for-approval", "never",
            ])
        command.extend(["--cd", str(_workdir(env)), request])
        return command, prompt_path

    request_with_path = (
        f"First read the complete authorized role prompt at {prompt_path}. "
        f"{request}"
    )
    if provider == "claude":
        command = [executable]
        if trusted:
            command.append("--dangerously-skip-permissions")
        command.extend([
            "--model", model,
            "--effort", effort,
        ])
        mcp_config = env.get(CLAUDE_MCP_ENV, "").strip()
        if mcp_config:
            command.extend(["--mcp-config", str(Path(mcp_config).expanduser())])
        command.extend([
            "--append-system-prompt-file", str(prompt_path),
            request_with_path,
        ])
        return command, prompt_path

    command = [executable]
    if trusted:
        command.extend(["--dangerously-skip-permissions", "--mode", "accept-edits"])
    command.extend([
        "--model", model,
        "--effort", effort,
        "--add-dir", str(prompt_path.parent),
        "--prompt-interactive", request_with_path,
    ])
    return command, prompt_path


def _display_command(command: Sequence[str]) -> str:
    """Readable dry-run output without shell-specific execution semantics."""
    return subprocess.list2cmdline(list(command))


def launch(
    role: str,
    provider: str,
    *,
    env: Mapping[str, str] | None = None,
    run=subprocess.run,
) -> int:
    """Validate configuration and launch exactly one provider worker."""
    normalized_role = normalize_role(role)
    normalized_provider = normalize_provider(provider)
    actual_env = os.environ if env is None else env

    if not active_roles().get(normalized_role, False):
        print(
            f"[{normalized_role.upper()}] Rolle ist in der Konfiguration "
            "abgeschaltet. Nichts zu tun."
        )
        return 0

    if doctor() != 0:
        print("[FEHLER] `python -m taskplan doctor` ist fehlgeschlagen.",
              file=sys.stderr)
        return 1

    try:
        workdir = _workdir(actual_env)
        command, prompt_path = _provider_command(
            normalized_role, normalized_provider, env=actual_env
        )
    except ValueError as exc:
        print(f"[FEHLER] {exc}", file=sys.stderr)
        return 1

    profile = runtime_profile(normalized_role, normalized_provider)
    print()
    print(f"[{normalized_role.upper()}] Provider:  {normalized_provider}")
    print(f"[{normalized_role.upper()}] Modell:    {profile['model']}")
    print(f"[{normalized_role.upper()}] Reasoning: {profile['reasoning_effort']}")
    print(f"[{normalized_role.upper()}] Prompt:    {prompt_path}")
    print(f"[{normalized_role.upper()}] Arbeitsort:{workdir}")

    if _truthy(actual_env.get(DRY_RUN_ENV, "")):
        print(f"[DRY-RUN] {_display_command(command)}")
        return 0

    completed = run(command, cwd=workdir)
    return int(completed.returncode)
