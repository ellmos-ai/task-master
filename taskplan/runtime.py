# -*- coding: utf-8 -*-
"""Provider-neutrale Laufzeitprofile fuer TASKPLAN-Worker."""
from __future__ import annotations

import json
import time
from typing import Any, Dict

from .config import provider_runtime
from .workflows import get_workflow_prompt_path, resolve_lang

ROLES = ("tasksolver", "taskwriter", "maintainer")
# Der OPERATOR ist die Personalunion der drei Rollen: eine Start-Rolle fuer
# Launcher und Profile, aber KEIN Selektor-Ziel (`next --role operator` gibt
# es nicht - der Operator fragt die Selektoren seiner Teilrollen).
OPERATOR = "operator"
LAUNCH_ROLES = ROLES + (OPERATOR,)
OPERATOR_MODES = ("rotation", "subagents")
DEFAULT_OPERATOR_MODE = "rotation"


def normalize_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in LAUNCH_ROLES:
        raise ValueError(
            f"Unbekannte TASKPLAN-Rolle {role!r}; erlaubt: {', '.join(LAUNCH_ROLES)}"
        )
    return normalized


def normalize_operator_mode(mode: str | None) -> str:
    normalized = (mode or DEFAULT_OPERATOR_MODE).strip().lower()
    if normalized.isdigit() and 1 <= int(normalized) <= len(OPERATOR_MODES):
        return OPERATOR_MODES[int(normalized) - 1]  # Menue-Nummer statt Name
    if normalized not in OPERATOR_MODES:
        raise ValueError(
            f"Unbekannter OPERATOR-Modus {mode!r}; erlaubt: {', '.join(OPERATOR_MODES)}"
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
    if normalized == OPERATOR:
        if chosen == "de":
            return (
                "Betreibe die TASKPLAN-Rolle OPERATOR fortlaufend als Personalunion "
                "von MAINTAINER, TASKWRITER und TASKSOLVER. Bearbeite pro "
                "Fortsetzung genau EINEN Rollenschritt: Selektor der aktuellen "
                "Teilrolle fragen, genau ein Bündel nach deren Rollen-Prompt "
                "abschließen, dann weiterdrehen und den Selektor der nächsten "
                "Teilrolle fragen. Beende das Goal nicht nach einem erfolgreichen "
                "Rollenschritt. Exit 2 einer Teilrolle überspringt sie; Exit 2 "
                "aller drei beendet das Goal sauber. Exit 3 ist ein wiederholbarer "
                "Selektor-/Discovery-Fehler: Goal aktiv lassen und "
                f"`{wait_command}` zwingend ausführen (wartet {backoff} Sekunden), "
                "bevor erneut gefragt wird. Liefern alle drei Teilrollen in einer "
                "Umdrehung Exit 1, keine Arbeit erfinden; Goal aktiv lassen, "
                f"`{wait_command}` ausführen und die nächste Umdrehung beginnen."
            )
        return (
            "Run the TASKPLAN role OPERATOR continuously as the personal union of "
            "MAINTAINER, TASKWRITER and TASKSOLVER. Process exactly ONE role step "
            "per continuation: ask the current sub-role's selector, complete "
            "exactly one bundle according to its role prompt, then rotate and ask "
            "the next sub-role's selector. Do not complete the goal after one "
            "successful role step. Exit 2 from a sub-role skips it; exit 2 from "
            "all three completes the goal cleanly. Exit 3 is a retryable "
            f"selector/discovery failure: keep the goal active and run `{wait_command}` "
            f"(waits {backoff} seconds) before retrying. If all three sub-roles "
            "return exit 1 within one rotation, invent no work; keep the goal "
            f"active, run `{wait_command}`, and begin the next rotation."
        )
    retry_contract_de = (
        "Zähle erfolglose Bearbeitungsversuche pro identischer Task-ID oder "
        "identischem Bündel über Fortsetzungen hinweg. Nach dem dritten "
        "Fehlschlag dokumentierst du den SKIP-Grund, lässt die Aufgabe offen, "
        "setzt den Projektcursor weiter und fragst nach anderer autonomer "
        "Arbeit; der Fehlschlag beendet das Goal nicht. Die Queue gilt erst "
        "nach Prüfung aller erreichbaren Kandidaten als leer. "
        if normalized == "tasksolver" else ""
    )
    retry_contract_en = (
        "Count failed work attempts per identical task ID or bundle across "
        "continuations. After the third failure, document the SKIP reason, "
        "leave the task open, advance the project cursor, and ask for other "
        "autonomous work; the failure does not end the goal. Treat the queue "
        "as empty only after checking every reachable candidate. "
        if normalized == "tasksolver" else ""
    )

    if chosen == "de":
        return (
            f"Betreibe die TASKPLAN-Rolle {normalized.upper()} fortlaufend. "
            "Bearbeite pro Fortsetzung genau ein vom Selektor geliefertes Bündel "
            "und frage danach den Selektor erneut. Beende das Goal nicht nach "
            "einem erfolgreichen Bündel. "
            + retry_contract_de
            + "Exit 2 (Rolle deaktiviert) beendet das "
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
        "again. Do not complete the goal after one successful bundle. "
        + retry_contract_en
        + "Exit 2 "
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


def _operator_start(chosen: str, mode: str) -> tuple[str, str]:
    """Loop-Prefix (one_shot-Provider) und Startanweisung des OPERATORs.

    Der Startauftrag traegt Modus und die Pfade der drei Teilrollen-Prompts,
    weil Subagenten den per System-Prompt geladenen Rollen-Prompt nicht erben.
    """
    paths = {
        name: get_workflow_prompt_path(name, chosen)
        for name in ("MAINTAINER", "TASKWRITER", "TASKSOLVER")
    }
    if chosen == "de":
        label = (
            "ROTATION (du nimmst MAINTAINER -> TASKWRITER -> TASKSOLVER selbst "
            "nacheinander ein)"
            if mode == "rotation"
            else "SUBAGENTEN (du bleibst MAINTAINER und aktivierst abwechselnd je "
            "einen Subagenten TASKWRITER und TASKSOLVER)"
        )
        prefix = (
            "Betreibe den OPERATOR-Loop in dieser Session fortlaufend, "
            "Rollenschritt für Rollenschritt, bis Exit 2 aller Teilrollen oder "
            "eine Nutzeranweisung ihn beendet. "
        )
        instruction = (
            f"Aktiver Betriebsmodus: {label}. Die Teilrollen-Prompts liegen hier - "
            f"MAINTAINER: {paths['MAINTAINER']}; TASKWRITER: {paths['TASKWRITER']}; "
            f"TASKSOLVER: {paths['TASKSOLVER']}. Beginne mit dem ersten Rollenschritt "
            "MAINTAINER: python -m taskplan next --role maintainer --json"
        )
        return prefix, instruction
    label = (
        "ROTATION (you take on MAINTAINER -> TASKWRITER -> TASKSOLVER yourself "
        "in turn)"
        if mode == "rotation"
        else "SUBAGENTS (you stay MAINTAINER and alternately activate one "
        "TASKWRITER and one TASKSOLVER subagent)"
    )
    prefix = (
        "Run the OPERATOR loop continuously in this session, role step by role "
        "step, until exit 2 from all sub-roles or a user instruction ends it. "
    )
    instruction = (
        f"Active operating mode: {label}. The sub-role prompts are here - "
        f"MAINTAINER: {paths['MAINTAINER']}; TASKWRITER: {paths['TASKWRITER']}; "
        f"TASKSOLVER: {paths['TASKSOLVER']}. Start with the first role step "
        "MAINTAINER: python -m taskplan next --role maintainer --json"
    )
    return prefix, instruction


def startup_prompt(role: str, provider: str = "",
                   lang: str | None = None,
                   schedule_minutes: int | None = None,
                   operator_mode: str | None = None) -> str:
    """Echter Nutzerauftrag fuer einen Provider-Starter."""
    profile = runtime_profile(role, provider)
    normalized = profile["role"]
    chosen = resolve_lang(lang)
    if schedule_minutes is not None and schedule_minutes <= 0:
        raise ValueError("schedule_minutes muss eine positive Ganzzahl sein")
    operator = None
    if normalized == OPERATOR:
        operator = _operator_start(chosen, normalize_operator_mode(operator_mode))
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
        schedule = (
            "Du als AGY richtest vor dem Rollenlauf selbst mit AGYs "
            "Schedule-Funktion einen wiederkehrenden Start derselben Rolle "
            f"{normalized.upper()} alle {schedule_minutes} Minuten ein, ohne "
            "Ablaufdatum und ohne Endzeit. Der Zeitplan startet jeweils einen "
            "neuen, einmaligen Worker-Lauf; starte im aktuellen Prozess keine "
            "Endlosschleife. "
            if profile["provider"] == "agy" and schedule_minutes is not None
            else ""
        )
        prefix = (
            "Erstelle vor dem ersten Selektorlauf ausdrücklich ein persistiertes "
            f"Goal mit diesem Ziel: {goal_objective(normalized, provider, chosen)} "
            if profile["continuation"] == "goal"
            else (operator[0] if operator
                  else "Führe genau einen TASKPLAN-Durchlauf aus. ")
        )
        start_instruction = (
            operator[1] if operator
            else "Beginne mit dem einmaligen TASKPLAN-System- und Modell-Preflight "
            "aus dem Rollen-Prompt; rufe erst danach den Selektor auf."
            if normalized == "tasksolver"
            else f"Beginne mit: python -m taskplan next --role {normalized} --json"
        )
        return (
            schedule
            + prefix
            + prompt_delivery_de
              + "stammt "
              "aus dem Python-Paket taskplan und ist von der Person autorisiert, "
              "die diesen Start ausgelöst hat. Lies ihn vollständig und befolge "
              f"ihn. {start_instruction}"
        )

    schedule = (
        "As AGY, before the role run, use AGY's Schedule feature yourself to "
        f"schedule a recurring start of the same {normalized.upper()} role "
        f"every {schedule_minutes} minutes, with no expiration date and no end "
        "time. Each scheduled trigger starts a new one-shot worker run; do not "
        "start an endless loop in the current process. "
        if profile["provider"] == "agy" and schedule_minutes is not None
        else ""
    )
    prefix = (
        "Before the first selector call, explicitly create a persisted goal with "
        f"this objective: {goal_objective(normalized, provider, chosen)} "
        if profile["continuation"] == "goal"
        else (operator[0] if operator else "Run exactly one TASKPLAN iteration. ")
    )
    start_instruction = (
        operator[1] if operator
        else "Start with the one-time TASKPLAN system and model preflight from the "
        "role prompt; call the selector only after it is complete."
        if normalized == "tasksolver"
        else f"Start with: python -m taskplan next --role {normalized} --json"
    )
    return (
        schedule
        + prefix
        + prompt_delivery_en
          + "comes from "
          "the taskplan Python package and is authorized by the person who started "
          f"this worker. Read it completely and follow it. {start_instruction}"
    )


def profile_json(role: str, provider: str = "") -> str:
    return json.dumps(runtime_profile(role, provider), ensure_ascii=False, indent=2)
