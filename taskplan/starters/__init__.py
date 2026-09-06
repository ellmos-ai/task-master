# -*- coding: utf-8 -*-
"""Packaged, user-neutral launcher resources for Windows and POSIX shells.

Two layers per platform: a provider-neutral starter per role that asks for
provider, model and reasoning at start, and one pinned starter per
role/provider pair under ``providers/``.
"""
from __future__ import annotations

from importlib import resources
import os
from pathlib import Path

from taskplan.launcher import PROVIDERS
from taskplan.runtime import LAUNCH_ROLES, normalize_role

PLATFORMS = ("windows", "posix")


def normalize_platform(platform: str) -> str:
    normalized = (platform or "windows").strip().lower()
    if normalized not in PLATFORMS:
        raise ValueError(
            f"Unbekannte Plattform {platform!r}; erlaubt: {', '.join(PLATFORMS)}"
        )
    return normalized


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(
            f"Unbekannter Provider {provider!r}; erlaubt: {', '.join(PROVIDERS)}"
        )
    return normalized


def starter_name(role: str, provider: str = "",
                 platform: str = "windows") -> str:
    """Dateiname eines Starters; ohne ``provider`` der anbieterneutrale."""
    role_key = normalize_role(role)
    if normalize_platform(platform) == "posix":
        stem = f"start-{role_key}"
        if provider:
            stem += f"-{_normalize_provider(provider)}"
        return f"{stem}.sh"
    stem = f"START-{role_key.upper()}"
    if provider:
        stem += f"-{_normalize_provider(provider).upper()}"
    return f"{stem}.bat"


def list_starters(platform: str = "windows") -> tuple[str, ...]:
    """Erst die anbieterneutralen Starter, dann die je Provider gepinnten."""
    chosen = normalize_platform(platform)
    neutral = tuple(
        starter_name(role, platform=chosen) for role in LAUNCH_ROLES
    )
    pinned = tuple(
        starter_name(role, provider, chosen)
        for role in LAUNCH_ROLES
        for provider in PROVIDERS
    )
    return neutral + pinned


def _package(platform: str, provider: str) -> str:
    package = f"taskplan.starters.{platform}"
    return f"{package}.providers" if provider else package


def get_starter_path(role: str, provider: str = "",
                     platform: str = "windows") -> Path:
    chosen = normalize_platform(platform)
    resource = resources.files(_package(chosen, provider)).joinpath(
        starter_name(role, provider, chosen)
    )
    try:
        path = Path(os.fspath(resource)).resolve()
    except TypeError as exc:  # pragma: no cover - zip importers
        raise RuntimeError("Der Starter liegt nicht als reale Datei vor") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return path
