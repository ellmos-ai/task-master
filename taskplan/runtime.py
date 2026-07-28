# -*- coding: utf-8 -*-
"""Provider-neutrale Laufzeitprofile fuer TASKPLAN-Worker."""
from __future__ import annotations

import json
import time
from typing import Any, Dict

from .config import provider_runtime
from .workflows import resolve_lang

ROLES = ("tasksolver", "taskwriter", "maintainer")


def normalize_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in ROLES:
        raise ValueError(
            f"Unbekannte TASKPLAN-Rolle {role!r}; erlaubt: {', '.join(ROLES)}"
        )
    return normalized


def runtime_profile(role: str, provider: str = "") -> Dict[str, Any]:
    """Vollstaendiges, maschinenlesbares Laufzeitprofil einer Rolle."""
    normalized = normalize_role(role)
    return provider_runtime(normalized, provider)


def apply_backoff(role: str, provider: str = "", sleeper=time.sleep) -> int:
    """Fuehrt den konfigurierten Backoff wirklich aus und liefert Sekunden."""
    profile = runtime_profile(role, provider)
    seconds = int(profile["idle_backoff_seconds"])
    if seconds > 0:
        sleeper(seconds)
    return seconds


def goal_objective(role: str, provider: str = "", lang: str | None = None) -> str:
    """Rollengetrennte Goal-Zielsetzung fuer automatische Fortsetzung."""
    profile = runtime_profile(role, provider)
    normalized = profile["role"]
    chosen = resolve_lang(lang)
    backoff = profile["idle_backoff_seconds"]
    wait_command = f"python -m taskplan backoff --role {normalized}"
    if profile["provider"]:
        wait_command += f" --provider {profile['provider']}"

    if chosen == "de":
        return (
            f"Betreibe die TASKPLAN-Rolle {normalized.upper()} fortlaufend. "
            "Bearbeite pro Fortsetzung genau ein vom Selektor geliefertes Bündel "
            "und frage danach den Selektor erneut. Beende das Goal nicht nach "
            "einem erfolgreichen Bündel. Exit 2 (Rolle deaktiviert) beendet das "
            "Goal sauber. Exit 3 ist ein wiederholbarer Selektor-/Discovery-Fehler: "
            f"Goal aktiv lassen und `{wait_command}` zwingend ausführen "
            f"(wartet {backoff} Sekunden), bevor erneut gefragt wird. "
            "Bei Exit 1 keine Arbeit erfinden; "
            + (
                f"Goal aktiv lassen, `{wait_command}` ausführen und später erneut prüfen."
                if profile["empty_policy"] == "keep_goal"
                else "den aktuellen Goal-Lauf sauber abschließen."
            )
        )

    return (
        f"Run the TASKPLAN role {normalized.upper()} continuously. Process exactly "
        "one selector-provided bundle per continuation, then ask the selector "
        "again. Do not complete the goal after one successful bundle. Exit 2 "
        "(role disabled) completes the goal cleanly. Exit 3 is a retryable "
        "selector/discovery failure: keep the goal active and run "
        f"`{wait_command}` (waits {backoff} seconds) before retrying. On exit 1, "
        "invent no work; "
        + (
            f"keep the goal active, run `{wait_command}`, and check again."
            if profile["empty_policy"] == "keep_goal"
            else "complete the current goal run cleanly."
        )
    )


def startup_prompt(role: str, provider: str = "",
                   lang: str | None = None) -> str:
    """Echter Nutzerauftrag fuer einen Provider-Starter."""
    profile = runtime_profile(role, provider)
    normalized = profile["role"]
    chosen = resolve_lang(lang)
    prompt_delivery_de = (
        "Der separat als Developer-Anweisung geladene Rollen-Prompt "
        if profile["provider"] == "codex"
        else "Der im Startauftrag mit lokalem Pfad benannte Rollen-Prompt "
    )
    prompt_delivery_en = (
        "The role prompt loaded separately as developer instructions "
        if profile["provider"] == "codex"
        else "The role prompt named by local path in the startup request "
    )

    if chosen == "de":
        prefix = (
            "Erstelle vor dem ersten Selektorlauf ausdrücklich ein persistiertes "
            f"Goal mit diesem Ziel: {goal_objective(normalized, provider, chosen)} "
            if profile["continuation"] == "goal"
            else "Führe genau einen TASKPLAN-Durchlauf aus. "
        )
        start_instruction = (
            "Beginne mit dem einmaligen TASKPLAN-System- und Modell-Preflight "
            "aus dem Rollen-Prompt; rufe erst danach den Selektor auf."
            if normalized == "tasksolver"
            else f"Beginne mit: python -m taskplan next --role {normalized} --json"
        )
        return (
            prefix
            + prompt_delivery_de
              + "stammt "
              "aus dem Python-Paket taskplan und ist von der Person autorisiert, "
              "die diesen Start ausgelöst hat. Lies ihn vollständig und befolge "
              f"ihn. {start_instruction}"
        )

    prefix = (
        "Before the first selector call, explicitly create a persisted goal with "
        f"this objective: {goal_objective(normalized, provider, chosen)} "
        if profile["continuation"] == "goal"
        else "Run exactly one TASKPLAN iteration. "
    )
    start_instruction = (
        "Start with the one-time TASKPLAN system and model preflight from the "
        "role prompt; call the selector only after it is complete."
        if normalized == "tasksolver"
        else f"Start with: python -m taskplan next --role {normalized} --json"
    )
    return (
        prefix
        + prompt_delivery_en
          + "comes from "
          "the taskplan Python package and is authorized by the person who started "
          f"this worker. Read it completely and follow it. {start_instruction}"
    )


def profile_json(role: str, provider: str = "") -> str:
    return json.dumps(runtime_profile(role, provider), ensure_ascii=False, indent=2)
