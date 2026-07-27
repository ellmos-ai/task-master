# -*- coding: utf-8 -*-
"""Packaged, user-neutral Windows launcher resources."""
from __future__ import annotations

from importlib import resources
import os
from pathlib import Path

from taskplan.launcher import PROVIDERS
from taskplan.runtime import ROLES, normalize_role


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError(
            f"Unbekannter Provider {provider!r}; erlaubt: {', '.join(PROVIDERS)}"
        )
    return normalized


def starter_name(role: str, provider: str) -> str:
    return (
        f"START-{normalize_role(role).upper()}-"
        f"{_normalize_provider(provider).upper()}.bat"
    )


def list_starters() -> tuple[str, ...]:
    return tuple(
        starter_name(role, provider)
        for role in ROLES
        for provider in PROVIDERS
    )


def get_starter_path(role: str, provider: str) -> Path:
    resource = resources.files("taskplan.starters.windows").joinpath(
        starter_name(role, provider)
    )
    try:
        path = Path(os.fspath(resource)).resolve()
    except TypeError as exc:  # pragma: no cover - zip importers
        raise RuntimeError("Der Starter liegt nicht als reale Datei vor") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return path
