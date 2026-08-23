"""Persistenter Rotationszustand fuer projektbasierte Selektoren.

Der Selektor laeuft als eigener CLI-Prozess. Ohne einen kleinen, atomar
geschriebenen Cursor beginnt eine projektbasierte Rolle bei jedem Aufruf
wieder am ersten freien Projekt und kann dadurch in einer Exit-0-Schleife
haengen. Der Zustand ist bewusst getrennt von der Task-Tabelle: Eine
Auswahl ist kein Task-Claim und darf weder Aufgabenstatus noch Herkunft
veraendern.

Zwei Granularitaeten:

* ``last_project`` — der Projekt-Cursor. Wird ein ganzes Projekt
  uebersprungen, beginnt der naechste Lauf beim naechsten Projekt.
* ``deferred_tasks`` — die Trommel (Revolver) auf Aufgabenebene. Eine
  einzelne blockierte Aufgabe wird ans Ende ALLER Kandidaten gereiht,
  statt sie ueber ihre Etiketten (``effort``, ``priority``, ``status``,
  ``scope``) kuenstlich aus der Auswahl zu draengen. Umetikettierung
  waere Datenverfaelschung: Diese Felder beschreiben Eigenschaften der
  Aufgabe, nicht ihre Position in der Warteschlange.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(target: Path, state: Dict[str, Any]) -> bool:
    """Schreibt den Zustand atomar und meldet, ob das gelungen ist.

    Die Datei liegt ausserhalb des Projektbaums und wird per ``os.replace``
    erst nach vollstaendigem Schreiben sichtbar. Ein fehlgeschriebener
    Cursor darf ein fachlich gueltiges Bundle nicht in einen falschen
    Exit-3-Fehler verwandeln; der Aufrufer kann die Warnung im
    JSON-Vertrag ausgeben.
    """
    temporary = ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp",
            dir=str(target.parent),
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = ""
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _role_entry(state: Dict[str, Any], role: str) -> Dict[str, Any]:
    roles = state.get("roles", {})
    if not isinstance(roles, dict):
        return {}
    entry = roles.get(role, {})
    return entry if isinstance(entry, dict) else {}


def last_project(path: str | Path, role: str) -> str:
    """Liest das zuletzt ausgewaehlte Projekt einer Rolle.

    Ein fehlender oder beschaedigter Zustandsstand ist kein Selektorfehler:
    Dann beginnt die Rotation kontrolliert am Anfang der Kandidatenliste.
    """
    if not path:
        return ""
    entry = _role_entry(_read_state(Path(path).expanduser()), role)
    value = entry.get("last_project", "")
    return str(value) if value else ""


def remember_project(path: str | Path, role: str, project: str) -> bool:
    """Schreibt den Projekt-Cursor atomar und gibt an, ob das gelungen ist.

    Andere Felder des Rolleneintrags — insbesondere ``deferred_tasks`` —
    bleiben erhalten. Ein Projekt-Skip darf die Aufgaben-Trommel nicht
    stillschweigend leeren.
    """
    if not path or not project:
        return False

    target = Path(path).expanduser()
    state = _read_state(target)
    roles = state.get("roles", {})
    if not isinstance(roles, dict):
        roles = {}
    entry = dict(_role_entry(state, role))
    entry["last_project"] = str(project)
    roles[role] = entry
    state["version"] = 1
    state["roles"] = roles
    return _write_state(target, state)


def deferred_tasks(path: str | Path, role: str) -> List[int]:
    """Liest die Trommel: zurueckgestellte Aufgaben-IDs in Reihenfolge.

    Die Reihenfolge ist bedeutungstragend — zuletzt zurueckgestellt steht
    hinten und kommt damit zuletzt wieder an die Reihe.
    """
    if not path:
        return []
    raw = _role_entry(_read_state(Path(path).expanduser()), role).get("deferred_tasks", [])
    if not isinstance(raw, list):
        return []
    out: List[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in out:
            out.append(value)
    return out


def _store_deferred(path: str | Path, role: str, order: List[int]) -> bool:
    target = Path(path).expanduser()
    state = _read_state(target)
    roles = state.get("roles", {})
    if not isinstance(roles, dict):
        roles = {}
    entry = dict(_role_entry(state, role))
    if order:
        entry["deferred_tasks"] = order
    else:
        entry.pop("deferred_tasks", None)
    roles[role] = entry
    state["version"] = 1
    state["roles"] = roles
    return _write_state(target, state)


def defer_task(path: str | Path, role: str, task_id: int) -> bool:
    """Reiht eine Aufgabe ans Ende der Trommel.

    Ein erneutes Zurueckstellen derselben Aufgabe rueckt sie wieder ganz
    nach hinten, statt einen Zweiteintrag zu erzeugen.
    """
    if not path:
        return False
    try:
        value = int(task_id)
    except (TypeError, ValueError):
        return False
    order = [x for x in deferred_tasks(path, role) if x != value]
    order.append(value)
    return _store_deferred(path, role, order)


def undefer_task(path: str | Path, role: str, task_id: int) -> bool:
    """Nimmt eine Aufgabe aus der Trommel (etwa nach Abschluss).

    Gibt ``True`` zurueck, wenn danach ein konsistenter Zustand vorliegt —
    auch dann, wenn die Aufgabe gar nicht zurueckgestellt war.
    """
    if not path:
        return False
    try:
        value = int(task_id)
    except (TypeError, ValueError):
        return False
    current = deferred_tasks(path, role)
    if value not in current:
        return True
    return _store_deferred(path, role, [x for x in current if x != value])
