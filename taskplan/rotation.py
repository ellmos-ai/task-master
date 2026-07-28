"""Persistenter Rotationszustand fuer projektbasierte Selektoren.

Der Selektor laeuft als eigener CLI-Prozess. Ohne einen kleinen, atomar
geschriebenen Cursor beginnt eine projektbasierte Rolle bei jedem Aufruf
wieder am ersten freien Projekt und kann dadurch in einer Exit-0-Schleife
haengen. Der Zustand ist bewusst getrennt von der Task-Tabelle: Eine
Auswahl ist kein Task-Claim und darf weder Aufgabenstatus noch Herkunft
veraendern.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


def _read_state(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def last_project(path: str | Path, role: str) -> str:
    """Liest das zuletzt ausgewaehlte Projekt einer Rolle.

    Ein fehlender oder beschaedigter Zustandsstand ist kein Selektorfehler:
    Dann beginnt die Rotation kontrolliert am Anfang der Kandidatenliste.
    """
    if not path:
        return ""
    state = _read_state(Path(path).expanduser())
    roles = state.get("roles", {})
    if not isinstance(roles, dict):
        return ""
    entry = roles.get(role, {})
    if not isinstance(entry, dict):
        return ""
    value = entry.get("last_project", "")
    return str(value) if value else ""


def remember_project(path: str | Path, role: str, project: str) -> bool:
    """Schreibt den Cursor atomar und gibt an, ob das gelungen ist.

    Die Datei liegt ausserhalb des Projektbaums und wird per ``os.replace``
    erst nach vollstaendigem Schreiben sichtbar. Ein fehlgeschriebener Cursor
    darf ein fachlich gueltiges Bundle nicht in einen falschen Exit-3-Fehler
    verwandeln; der Aufrufer kann die Warnung im JSON-Vertrag ausgeben.
    """
    if not path or not project:
        return False

    target = Path(path).expanduser()
    state = _read_state(target)
    roles = state.get("roles", {})
    if not isinstance(roles, dict):
        roles = {}
    roles[role] = {"last_project": str(project)}
    state["version"] = 1
    state["roles"] = roles

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
