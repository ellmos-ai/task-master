# -*- coding: utf-8 -*-
"""Die Bruecke zwischen Selektor und Prompt.

Der Selektor ist deterministischer Code — aber die Rollen sind LLM-Prompts. Ohne
einen Weg, den Selektor zu BEFRAGEN, bliebe seine Reihenfolge wirkungslos: Das
Modell wuerde weiter selbst waehlen, und genau das war das Problem.

    python -m taskplan next [--role tasksolver] [--json]

liefert das naechste Buendel: Modus, Aufwand, Root, Projekt, Task-IDs — und den
Lock-Kontext, der fuer dieses Projekt gilt. Der Prompt fragt, der Selektor
antwortet, das LLM urteilt.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .client import TaskClient
from .config import (
    active_roles,
    discovery_timeout_seconds,
    lock_config,
    model_for,
    prompt_language,
    rotation_state_file,
    selector_config,
    traversal_config,
)
from .locks import CREATE, MODIFY, READ, build_lock_view
from .rotation import deferred_tasks, last_project, remember_project
from .selector import next_bundle

# All project-oriented roles can explicitly advance their own cursor.  The
# solver normally advances by completing tasks; ``taskplan skip`` is the
# escape hatch for a stale or temporarily unusable bundle.
PROJECT_ROTATION_ROLES = ("taskwriter", "maintainer", "tasksolver")


class ProjectDiscoveryTimeout(TimeoutError):
    """Projekt-Discovery hat ihre konfigurierte harte Grenze ueberschritten."""


def _discover_projects_bounded(
    timeout: float,
    force: bool = False,
    with_metadata: bool = False,
):
    """Discovery in einem abbrechbaren Unterprozess mit persistentem Cache."""
    from .traversal import Project

    if timeout <= 0:
        from .discovery import discover_snapshot
        snapshot = discover_snapshot(force=force)
        metadata = snapshot.payload()
        metadata.pop("projects", None)
        return (snapshot.projects, metadata) if with_metadata else snapshot.projects
    command = [sys.executable, "-m", "taskplan.discovery"]
    if force:
        command.append("--force")
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectDiscoveryTimeout(
            f"Projekt-Discovery nach {timeout:g} Sekunden abgebrochen"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"Exit {completed.returncode}"
        raise RuntimeError(f"Projekt-Discovery fehlgeschlagen: {detail}")
    try:
        data = json.loads(completed.stdout)
        projects = [
            Project(path=Path(item["path"]), root_id=str(item["root_id"]))
            for item in data.get("projects", [])
        ]
        metadata = {
            key: data[key] for key in (
                "cached", "source", "degraded", "cache_age_seconds",
                "refreshed_sector", "pending_sectors", "warnings",
            ) if key in data
        }
        return (projects, metadata) if with_metadata else projects
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("Projekt-Discovery lieferte ungueltiges JSON") from exc


def _lock_view():
    """Erhebt den Lock-Zustand einmal pro Lauf."""
    locks = lock_config()
    traversal = traversal_config()
    return build_lock_view(
        provider=locks["provider"],
        roots=traversal.roots,
        rule_paths=locks["rule_paths"],
        max_depth=locks["max_depth"],
    ), locks["provider"]


def next_work(role: str = "tasksolver") -> dict:
    """Was ist als Naechstes dran? Der vollstaendige Kontext fuer einen Loop-Lauf."""
    roles = active_roles()
    if role in roles and not roles[role]:
        # Abgeschaltete Rolle bricht SAUBER ab, statt still leerzulaufen.
        return {"role": role, "active": False,
                "reason": f"Rolle '{role}' ist in der Konfiguration abgeschaltet."}

    store = TaskClient()
    view, provider = _lock_view()

    config = selector_config()
    result = {
        "role": role,
        "active": True,
        "model": model_for(role),
        "lock_provider": provider,
        "db": str(store.db_path),
    }
    previous_project = ""
    rotation_path = None
    deferred_ids: list = []
    if role in PROJECT_ROTATION_ROLES:
        rotation_path = rotation_state_file()
        previous_project = last_project(rotation_path, role)
        deferred_ids = deferred_tasks(rotation_path, role)
        if previous_project:
            result["rotation"] = {"previous_project": previous_project}
        if deferred_ids:
            result.setdefault("rotation", {})["deferred_tasks"] = list(deferred_ids)
    # Der TASKWRITER braucht die Projektliste: Ist alles eingestuft, sucht er
    # das naechste Projekt, das noch GAR KEINE Aufgaben hat. Nur fuer ihn
    # erheben - fuer den Solver waere es verschwendete Zeit.
    if role in ("taskwriter", "maintainer"):
        timeout = discovery_timeout_seconds()
        try:
            discovered = _discover_projects_bounded(
                timeout, with_metadata=True
            )
            if isinstance(discovered, tuple):
                config.projects, discovery_metadata = discovered
            else:  # Kompatibilität für externe/mockende Aufrufer
                config.projects, discovery_metadata = discovered, {}
            if discovery_metadata:
                result["discovery"] = discovery_metadata
        except (ProjectDiscoveryTimeout, RuntimeError) as exc:
            from .discovery import fallback_after_failure

            fallback = fallback_after_failure(store)
            if fallback is not None:
                config.projects = fallback.projects
                metadata = fallback.payload()
                metadata.pop("projects", None)
                metadata["trigger_error"] = (
                    "project_discovery_timeout"
                    if isinstance(exc, ProjectDiscoveryTimeout)
                    else "project_discovery_error"
                )
                metadata["trigger_reason"] = str(exc)
                result["discovery"] = metadata
            else:
                error = ("project_discovery_timeout"
                         if isinstance(exc, ProjectDiscoveryTimeout)
                         else "project_discovery_error")
                result.update({
                    "bundle": None,
                    "retryable": True,
                    "error": error,
                    "reason": (
                        f"{exc}. Der Lauf endet kontrolliert statt zu haengen. "
                        "Nach dem konfigurierten Backoff erneut versuchen."
                    ),
                })
                return result

    bundle = next_bundle(config, store, view, role=role,
                        after_project=previous_project,
                        deferred_task_ids=deferred_ids)

    # Fremde Lock-Regeln gehen als TEXT weiter — nicht ausgewertet, sondern
    # dem LLM zum Lesen gegeben.
    if view.extra_rules:
        result["lock_rules"] = view.extra_rules

    if bundle is None:
        result["bundle"] = None
        if role == "taskwriter":
            result["reason"] = (
                "Nichts zu erfassen: Es gibt keine unklassifizierten Aufgaben mehr, "
                "und jedes erreichbare Projekt hat bereits Aufgaben. Das ist ein "
                "ehrlicher Leerlauf — es wird KEINE Arbeit erfunden."
            )
        elif role == "maintainer":
            result["reason"] = (
                "Kein freies Projekt: Jedes erreichbare Projekt ist entweder "
                "gesperrt oder wird gerade von einer anderen Rolle bearbeitet "
                "(aktive/zugewiesene Aufgabe). Ehrlicher Leerlauf."
            )
        else:
            result["reason"] = (
                "Kein erreichbares Buendel. Moegliche Gruende: alle offenen Aufgaben "
                "sind unklassifiziert (effort leer -> der TASKWRITER muss sie erst "
                "einstufen), zu gross (large/special), zentral (scope=central), oder "
                "ihre Projekte sind gesperrt. Das ist ein ehrlicher Leerlauf — es wird "
                "KEINE Arbeit erfunden, um den Loop zu fuellen."
            )
        return result

    project = Path(bundle.project_path) if bundle.project_path else None
    result["bundle"] = {
        "mode": bundle.mode,
        "effort": bundle.effort,
        "root_id": bundle.root_id,
        "project_path": bundle.project_path,
        "task_ids": [t["id"] for t in bundle.tasks],
        "tasks": [{"id": t["id"], "title": t["title"],
                   "priority": t["priority"], "effort": t["effort"]}
                  for t in bundle.tasks],
    }
    if (
        role in PROJECT_ROTATION_ROLES
        and rotation_path is not None
        and not bundle.tasks
    ):
        persisted = remember_project(rotation_path, role, bundle.project_path)
        rotation = result.setdefault("rotation", {})
        rotation["selected_project"] = bundle.project_path
        rotation["cursor_persisted"] = persisted
        if not persisted:
            rotation["warning"] = (
                "Rotationszustand konnte nicht geschrieben werden; "
                "der nächste Lauf beginnt wieder am Listenanfang."
            )
    if project is not None:
        result["permissions"] = {
            "read": view.allows(project, READ),
            "create": view.allows(project, CREATE),
            "modify": view.allows(project, MODIFY),
        }
    return result


EXIT_STATUS = {
    0: {
        "name": "BUNDLE_READY",
        "de": "Bündel erfolgreich geliefert",
        "en": "Bundle delivered successfully",
    },
    1: {
        "name": "NO_WORK",
        "de": "Rolle aktiv, aber derzeit kein zulässiges Bündel",
        "en": "Role active, but no eligible bundle is currently available",
    },
    2: {
        "name": "ROLE_DISABLED",
        "de": "Rolle ist in der Konfiguration deaktiviert",
        "en": "Role is disabled in configuration",
    },
    3: {
        "name": "RETRYABLE_SELECTOR_ERROR",
        "de": "Wiederholbarer Selektor-/Discovery-Fehler",
        "en": "Retryable selector/discovery error",
    },
}


def _exit_code(work: dict) -> int:
    if not work.get("active", True):
        return 2
    if work.get("retryable"):
        return 3
    return 0 if work.get("bundle") else 1


def _exit_contract(code: int) -> dict:
    language = prompt_language()
    status = EXIT_STATUS[code]
    return {
        "code": code,
        "name": status["name"],
        "meaning": status["de" if language == "de" else "en"],
    }


def _exit_line(code: int) -> str:
    contract = _exit_contract(code)
    return f"Exit {code} — {contract['meaning']} [{contract['name']}]"


def run(role: str = "tasksolver", as_json: bool = False) -> int:
    work = next_work(role)
    code = _exit_code(work)
    work["exit"] = _exit_contract(code)

    if as_json:
        print(json.dumps(work, ensure_ascii=False, indent=2))
        return code

    if not work["active"]:
        print(_exit_line(code))
        print(f"[{role.upper()}] {work['reason']}")
        return code

    if work.get("retryable"):
        print(_exit_line(code))
        print(f"[{role.upper()}] Wiederholbarer Selektorfehler")
        print(f"  {work['reason']}")
        return code

    print(_exit_line(code))
    print(f"[{role.upper()}]")
    print(f"  Datenbank : {work['db']}")
    if work.get("model"):
        print(f"  Modell    : {work['model']}")
    print(f"  Locks     : {work['lock_provider']}")
    print()

    bundle = work.get("bundle")
    if not bundle:
        print("  Nichts zu tun.")
        print()
        print(f"  {work['reason']}")
        return code

    print(f"  Modus     : {bundle['mode']}")
    print(f"  Aufwand   : {bundle['effort']}")
    print(f"  Root      : {bundle['root_id']}")
    print(f"  Projekt   : {bundle['project_path'] or '(Wurzel)'}")
    print()
    print("  Aufgaben:")
    for task in bundle["tasks"]:
        print(f"    [{task['id']:>3}] {task['priority']:<8} {task['title']}")

    perms = work.get("permissions")
    if perms:
        print()
        print("  Rechte in diesem Projekt:")
        print(f"    lesen   : {'ja' if perms['read'] else 'nein'}")
        print(f"    anlegen : {'ja' if perms['create'] else 'nein'}")
        print(f"    aendern : {'ja' if perms['modify'] else 'NEIN (gesperrt)'}")

    if work.get("lock_rules"):
        print()
        print("  Fremde Lock-Regeln (LIES SIE und wende sie an):")
        for text in work["lock_rules"]:
            print("    " + text.replace("\n", "\n    "))
    return code
