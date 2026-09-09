# -*- coding: utf-8 -*-
"""`taskplan doctor` — zeigt, WELCHE Datenbank tatsaechlich benutzt wird.

Anlass: Auf dem Entwicklungssystem existierten zwei Datenbanken und drei
ENV-Namen fuer dieselbe Sache. Der Modul-Default zeigte auf eine LEERE Datei,
waehrend die Live-Daten woanders lagen. Wer der Anweisung "nutze die
TASKPLAN-API" folgte, schrieb ins Leere und sah keinen einzigen bestehenden
Task — ohne Fehlermeldung.

Diese Klasse von Fehlern ist tueckisch, weil alles "funktioniert": kein Crash,
keine Warnung, nur stille Wirkungslosigkeit. Der Doctor macht sie sichtbar.

Aufruf:
    python -m taskplan doctor
"""
from pathlib import Path

from .client import (
    count_tasks_in,
    get_default_db_path,
)
from .config import (
    config_search_paths,
    discovery_mode,
    find_config_file,
    traversal_config,
)
from .discovery import (
    DiscoveryConfigurationError,
    validate_discovery_configuration,
)


def _known_candidates() -> list[Path]:
    """Orte, an denen erfahrungsgemaess eine Task-DB liegt.

    Bewusst eine kurze, dokumentierte Liste — sie dient nur der Warnung
    ("du schreibst ins Leere, waehrend dort Daten liegen"), nicht der
    Aufloesung. Aufgeloest wird ausschliesslich ueber ENV und Konfiguration.
    """
    home = Path.home()
    return [
        home / ".taskplan" / "taskplan.db",
        home / ".rinnsal" / "scanner_tasks.db",   # Rinnsal-Erbe, historisch
        home / ".rinnsal" / "rinnsal.db",
    ]


def run() -> int:
    """Gibt den Auflösungsstand aus. Returns: 0 = ok, 1 = Warnung."""
    active = get_default_db_path()
    active_count = count_tasks_in(active)

    print("[TASKPLAN DOCTOR]")
    print()
    print("Aktive Datenbank:")
    print(f"  {active}")
    if active_count is None:
        print("  -> existiert nicht oder enthaelt keine Task-Tabelle")
    else:
        print(f"  -> {active_count} Tasks")
    print()

    config_file = find_config_file()
    print("Konfiguration:")
    if config_file:
        print(f"  {config_file}")
    else:
        print("  keine gefunden. Gesucht wurde in:")
        for path in config_search_paths():
            print(f"    - {path}")
    print()

    discovery_error = ""
    try:
        validate_discovery_configuration(traversal_config(), discovery_mode())
    except DiscoveryConfigurationError as exc:
        discovery_error = str(exc)
        print("Projekt-Discovery:")
        print(f"  FEHLER: {discovery_error}")
        print()

    print("Andere gefundene Task-Datenbanken:")
    warn = False
    found_other = False
    for candidate in _known_candidates():
        if Path(candidate) == Path(active):
            continue
        count = count_tasks_in(candidate)
        if count is None:
            continue
        found_other = True
        print(f"  {candidate}")
        print(f"  -> {count} Tasks")
        # Der eigentliche Fehlerfall: aktiv ist leer, woanders liegen Daten.
        if count > 0 and not active_count:
            warn = True
    if not found_other:
        print("  keine")
    print()

    if warn:
        print("WARNUNG: Die aktive Datenbank ist leer, waehrend eine andere")
        print("         Daten enthaelt. Vermutlich zeigt TASKPLAN auf die")
        print("         falsche Datei — Schreibzugriffe landen dann in einer")
        print("         Datenbank, die niemand liest.")
        print()
        print("  Beheben (eines von beidem):")
        print("    - ENV setzen:   TASKPLAN_DB=<pfad zur richtigen db>")
        print("    - Konfigurieren: ~/.taskplan/taskplan.toml")
        print("        [storage]")
        print('        path = "<pfad zur richtigen db>"')
    if warn or discovery_error:
        return 1

    print("OK: Keine widerspruechliche Datenbank gefunden.")
    return 0
