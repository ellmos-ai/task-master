# -*- coding: utf-8 -*-
"""CLI-Einstiegspunkt: `python -m taskplan <befehl>`."""
import sys


def _option(args: list[str], name: str, default: str = "") -> str:
    if name not in args:
        return default
    index = args.index(name)
    return args[index + 1] if index + 1 < len(args) else default


def _release_claim_on_defer(task_id: int, store=None) -> str:
    """Loest den Claim einer zurueckgestellten Aufgabe — sichtbar.

    Zuruecklegen heisst: Ich arbeite gerade NICHT daran. Genau das bestreitet
    ein stehender ``assigned_to``-Eintrag. Bliebe er, waere eine Blockade nur
    gegen die naechste getauscht: Der Solver uebergeht die Aufgabe zwar, der
    MAINTAINER aber meidet ihr ganzes Projekt weiter, weil er jede zugewiesene
    Aufgabe als 'dort arbeitet jemand' liest.

    Der bisherige Inhaber wird IMMER genannt. Ein geloester Claim ist eine
    Aussage ueber fremde Arbeit und darf nicht still passieren.

    Rueckgabe: der geloeste Inhaber, oder "" wenn nichts zu loesen war.
    """
    if store is None:
        from .client import TaskClient
        store = TaskClient()
    task = store.get(task_id)
    if not task:
        return ""
    owner = task.get("assigned_to") or ""
    if not owner:
        return ""
    if store.assign(task_id, "", ""):
        print(f"  Claim von {owner!r} geloest — zurueckgestellt heisst, "
              f"dass niemand daran arbeitet.")
        return owner
    print(f"  Achtung: Claim von {owner!r} steht weiter und sperrt das "
          f"Projekt fuer den MAINTAINER.", file=sys.stderr)
    return ""


def _projects_command(args: list[str]) -> int:
    """Die manuelle Projekt-Registry — der Fallback der Auto-Erkennung.

    Die Automatik erkennt Projekte an Markern. Findet sie eines nicht (andere
    Konventionen, keine Steuerdatei) oder faelschlich (eine Kategorie-Ebene sieht
    aus wie ein Projekt), traegt man es hier von Hand ein. Der MAINTAINER pflegt
    die Liste automatisch nach.
    """
    from .config import discovery_mode, discovery_timeout_seconds, registry_file
    from .registry import (add_project, load_registry, registry_path,
                           remove_project)

    action = args[0] if args else "list"
    configured = registry_file()

    if action == "list":
        from .runner import (ProjectDiscoveryTimeout,
                             _discover_projects_bounded, _exit_line)
        from .discovery import DiscoveryConfigurationError
        entries = load_registry(configured)
        try:
            total, metadata = _discover_projects_bounded(
                discovery_timeout_seconds(), with_metadata=True
            )
        except DiscoveryConfigurationError as exc:
            print(_exit_line(3), file=sys.stderr)
            print(f"Projekt-Discovery falsch konfiguriert: {exc}", file=sys.stderr)
            return 3
        except (ProjectDiscoveryTimeout, RuntimeError) as exc:
            from .discovery import read_last_known_good
            fallback = read_last_known_good()
            if fallback is None:
                print(_exit_line(3), file=sys.stderr)
                print(f"Projekt-Discovery nicht verfügbar: {exc}", file=sys.stderr)
                return 3
            total = fallback.projects
            metadata = fallback.payload()
            print(
                "Warnung: Live-Refresh fehlgeschlagen; "
                "Last-known-good-Inventar wird angezeigt.",
                file=sys.stderr,
            )
        print(f"Discovery-Modus : {discovery_mode()}")
        print(f"Registry-Datei  : {registry_path(configured)}")
        print(f"Inventarquelle  : {metadata.get('source', 'unknown')}")
        print(f"Ausstehende Sektoren: {metadata.get('pending_sectors', 0)}")
        print()
        print(f"Manuell eingetragen : {len(entries)}")
        print(f"Erreichbar gesamt   : {len(total)}  (Auto + Registry, gecacht)")
        if entries:
            print()
            print("Manuelle Eintraege:")
            for entry in entries:
                note = f"  ({entry.note})" if entry.note else ""
                print(f"  [{entry.root_id}] {entry.path}{note}")
        return 0

    if action == "refresh":
        from .runner import (ProjectDiscoveryTimeout,
                             _discover_projects_bounded, _exit_line)
        from .discovery import DiscoveryConfigurationError
        try:
            total, metadata = _discover_projects_bounded(
                discovery_timeout_seconds(), force=True, with_metadata=True
            )
        except DiscoveryConfigurationError as exc:
            print(_exit_line(3), file=sys.stderr)
            print(f"Projekt-Discovery falsch konfiguriert: {exc}", file=sys.stderr)
            return 3
        except (ProjectDiscoveryTimeout, RuntimeError) as exc:
            print(_exit_line(3), file=sys.stderr)
            print(f"Projekt-Discovery nicht verfügbar: {exc}", file=sys.stderr)
            print(
                "Vorhandene Last-known-good-Sektoren bleiben unverändert nutzbar.",
                file=sys.stderr,
            )
            return 3
        print(f"Projektsektor erneuert: {metadata.get('refreshed_sector') or '(keiner fällig)'}")
        print(f"Bekanntes Gesamtinventar: {len(total)} Projekte")
        print(f"Ausstehende Sektoren: {metadata.get('pending_sectors', 0)}")
        return 0

    if action == "add":
        if len(args) < 3:
            print("Nutzung: python -m taskplan projects add <pfad> <root_id> "
                  "[notiz] [--by <wer>]", file=sys.stderr)
            return 2
        by = ""
        rest = list(args[1:])
        if "--by" in rest:
            index = rest.index("--by")
            if index + 1 < len(rest):
                by = rest[index + 1]
            rest = rest[:index] + rest[index + 2:]
        path, root_id = rest[0], rest[1]
        note = rest[2] if len(rest) > 2 else ""
        if add_project(path, root_id, note=note, added_by=by,
                       configured=configured):
            print(f"Eingetragen: [{root_id}] {path}")
            return 0
        print(f"Bereits vorhanden: {path}")
        return 0

    if action == "remove":
        if len(args) < 2:
            print("Nutzung: python -m taskplan projects remove <pfad>",
                  file=sys.stderr)
            return 2
        if remove_project(args[1], configured=configured):
            print(f"Eintrag entfernt: {args[1]}")
            print("(Nur der Eintrag — auf der Platte wurde nichts geloescht.)")
            return 0
        print(f"Kein Eintrag gefunden: {args[1]}", file=sys.stderr)
        return 1

    if action in ("flag", "unflag"):
        from pathlib import Path
        from .config import marker_rules
        from .markers import DEFAULT_FLAG_FILE, clear_flag, set_flag

        if len(args) < 2:
            print(f"Nutzung: python -m taskplan projects {action} <pfad> [notiz]",
                  file=sys.stderr)
            return 2
        rules = marker_rules()
        name = rules.flag_file.name if rules else DEFAULT_FLAG_FILE
        target = Path(args[1])
        if not target.is_dir():
            print(f"Kein Verzeichnis: {target}", file=sys.stderr)
            return 1

        if action == "flag":
            note = args[2] if len(args) > 2 else ""
            flag = set_flag(target, name, note=note)
            print(f"Markiert: {flag}")
            print("Dieses Verzeichnis gilt jetzt als Projekt — die Flagdatei")
            print("schlaegt jede Heuristik.")
            return 0

        if clear_flag(target, name):
            print(f"Markierung entfernt: {target / name}")
            return 0
        print(f"Keine Markierung gefunden: {target / name}", file=sys.stderr)
        return 1

    if action == "markers":
        from .config import marker_rules
        rules = marker_rules()
        if rules is None:
            from .config import traversal_config
            print("Marker-Regeln: (einfache Liste)")
            print(" ", list(traversal_config().effective_markers()))
            print()
            print("Fuer die vier Kategorien einen [traversal.markers]-Abschnitt")
            print("anlegen — siehe taskplan.example.toml.")
            return 0
        print("Marker-Regeln:")
        print(" ", rules.describe())
        print()
        print(f"  Verknuepfung: {rules.combine!r} "
              f"({'ALLE aktiven Kategorien muessen treffen' if rules.combine == 'all' else 'ein Treffer genuegt'})")
        print(f"  Flagdatei schlaegt IMMER alles: {rules.flag_file.enabled}")
        return 0

    print(f"Unbekannt: {action!r}. Erlaubt: list | refresh | add | remove | flag | unflag | markers",
          file=sys.stderr)
    return 2


def _review_command(args: list[str]) -> int:
    """Bestätigungs- und Diagnosekanal des lokalen Projekt-Reviewpools."""
    import json
    from pathlib import Path

    from .client import TaskClient
    from .config import review_pool_config
    from .review_pool import (
        ProjectHashError,
        ReviewConflict,
        ReviewInputError,
        ReviewPool,
    )

    action = args[0] if args else ""
    role = _option(args, "--role", "")
    project = _option(args, "--project", "")
    as_json = "--json" in args
    if action not in ("status", "complete", "defer", "unseal", "effort"):
        print(
            "Nutzung: python -m taskplan review "
            "<status|complete|defer|unseal|effort> --role R --project P",
            file=sys.stderr,
        )
        return 2
    if not role or not project:
        print(
            f"Nutzung: python -m taskplan review {action} "
            "--role <taskwriter|maintainer> --project PFAD",
            file=sys.stderr,
        )
        return 2

    pool = ReviewPool(TaskClient(), policy=review_pool_config())
    try:
        if action == "status":
            from .runner import _lock_view

            view, _provider = _lock_view()
            payload = pool.status(
                role, project,
                locked=not view.allows_selection(Path(project)),
            )
        elif action == "complete":
            token = _option(args, "--presentation-id", "")
            result = _option(args, "--result", "")
            if not token or not result:
                print(
                    "Nutzung: python -m taskplan review complete --role R "
                    "--project P --presentation-id ID --result TEXT",
                    file=sys.stderr,
                )
                return 2
            payload = pool.complete(role, project, token, result)
        elif action == "defer":
            token = _option(args, "--presentation-id", "")
            reason = _option(args, "--reason", "")
            if not token or not reason:
                print(
                    "Nutzung: python -m taskplan review defer --role R "
                    "--project P --presentation-id ID --reason TEXT",
                    file=sys.stderr,
                )
                return 2
            payload = pool.defer(role, project, token, reason)
        elif action == "unseal":
            reason = _option(args, "--reason", "")
            if not reason:
                print(
                    "Nutzung: python -m taskplan review unseal --role R "
                    "--project P --reason TEXT",
                    file=sys.stderr,
                )
                return 2
            payload = pool.unseal(role, project, reason)
        else:
            effort = _option(args, "--set", "")
            if not effort:
                print(
                    "Nutzung: python -m taskplan review effort --role R "
                    "--project P --set <easy|medium>",
                    file=sys.stderr,
                )
                return 2
            payload = pool.set_effort(role, project, effort)
    except ReviewInputError as exc:
        print(f"Ungültiger Review-Aufruf: {exc}", file=sys.stderr)
        return 2
    except (ReviewConflict, ProjectHashError) as exc:
        print(f"Review nicht gebucht: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if action == "status":
        print(f"[{role.upper()}] {project}")
        print(f"  Grund     : {payload['reason']}")
        print(f"  Kandidat  : {'ja' if payload['eligible'] else 'nein'}")
        if payload.get("current_hash"):
            print(f"  Hash      : {payload['current_hash']}")
        if payload.get("error"):
            print(f"  Fehler    : {payload['error']}")
    else:
        print(f"Review-Zustand gebucht: [{role}] {project}")
        if payload.get("sealed_hash"):
            print(f"  Siegel    : {payload['sealed_hash']}")
        if payload.get("deferred_until"):
            print(f"  Defer bis : {payload['deferred_until']}")
        if payload.get("manual_unseal_at"):
            print(f"  Entsiegelt: {payload['manual_unseal_at']}")
        print(f"  Aufwand   : {payload['effort']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "help"
    rest = args[1:]

    if command == "doctor":
        from .doctor import run
        return run()

    if command == "next":
        from .runner import run
        role = "tasksolver"
        if "--role" in rest:
            index = rest.index("--role")
            if index + 1 < len(rest):
                role = rest[index + 1]
        return run(role=role, as_json="--json" in rest)

    if command == "projects":
        return _projects_command(rest)

    if command == "review":
        return _review_command(rest)

    if command == "prompt":
        from .workflows import get_workflow_prompt
        if not rest:
            print("Nutzung: python -m taskplan prompt <TASKSOLVER|TASKWRITER|MAINTAINER>",
                  file=sys.stderr)
            return 2
        try:
            print(get_workflow_prompt(rest[0]))
        except KeyError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0

    if command == "maintainer-plan":
        import json
        from pathlib import Path

        from .maintenance import (
            MaintenanceInputError,
            load_fingerprints,
            plan_finding,
        )

        input_name = _option(rest, "--input", "")
        fingerprints_name = _option(rest, "--existing-fingerprints", "")
        if not input_name:
            print("Nutzung: python -m taskplan maintainer-plan --input FINDING.json "
                  "[--existing-fingerprints FINGERPRINTS.json]", file=sys.stderr)
            return 2
        try:
            payload = json.loads(Path(input_name).read_text(encoding="utf-8"))
            fingerprints = load_fingerprints(
                Path(fingerprints_name) if fingerprints_name else None
            )
            plan = plan_finding(
                payload,
                existing_ticket_fingerprints=fingerprints,
            )
        except (OSError, json.JSONDecodeError, MaintenanceInputError) as exc:
            print(f"Ungültiger MAINTAINER-Befund: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if command == "runtime":
        from .runtime import runtime_profile
        role = _option(rest, "--role", "tasksolver")
        provider = _option(rest, "--provider", "")
        field = _option(rest, "--field", "")
        try:
            profile = runtime_profile(role, provider)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        if field:
            if field not in profile:
                print(f"Unbekanntes Runtime-Feld: {field}", file=sys.stderr)
                return 2
            print(profile[field])
            return 0
        import json
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0

    if command == "startup-prompt":
        from .runtime import startup_prompt
        role = _option(rest, "--role", "tasksolver")
        provider = _option(rest, "--provider", "")
        try:
            print(startup_prompt(role, provider))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0

    if command == "backoff":
        from .runtime import apply_backoff
        role = _option(rest, "--role", "tasksolver")
        provider = _option(rest, "--provider", "")
        try:
            seconds = apply_backoff(role, provider)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Backoff abgeschlossen: {seconds} Sekunden")
        return 0

    if command == "launch":
        from .launcher import launch
        role = _option(rest, "--role", "tasksolver")
        provider = _option(rest, "--provider", "")
        if not provider:
            print("Nutzung: python -m taskplan launch --role R --provider P",
                  file=sys.stderr)
            return 2
        return launch(role, provider)

    if command == "starters":
        from .starters import get_starter_path, list_starters
        action = rest[0] if rest else "list"
        if action == "list":
            for name in list_starters():
                print(name)
            return 0
        if action == "path":
            role = _option(rest, "--role", "")
            provider = _option(rest, "--provider", "")
            if not role or not provider:
                print("Nutzung: python -m taskplan starters path "
                      "--role R --provider P", file=sys.stderr)
                return 2
            try:
                print(get_starter_path(role, provider))
            except (ValueError, FileNotFoundError) as exc:
                print(exc, file=sys.stderr)
                return 2
            return 0
        print("Nutzung: python -m taskplan starters list | starters path "
              "--role R --provider P", file=sys.stderr)
        return 2

    if command == "skip":
        from .config import rotation_state_file
        from .rotation import defer_task, remember_project, undefer_task

        role = _option(rest, "--role", "maintainer")
        project = _option(rest, "--project", "")
        task_raw = _option(rest, "--task", "")
        presentation_id = _option(rest, "--presentation-id", "")
        reason = _option(rest, "--reason", "")
        undo = "--undo" in rest
        if not project and not task_raw:
            print("Nutzung: python -m taskplan skip "
                  "--role <maintainer|taskwriter|tasksolver> "
                  "[--project <pfad>] [--task <id> [--undo]]", file=sys.stderr)
            return 2
        if role not in ("maintainer", "taskwriter", "tasksolver"):
            print("skip ist nur für projektbasierte Rollen verfügbar: "
                  "maintainer, taskwriter oder tasksolver.",
                  file=sys.stderr)
            return 2

        # Projektrollen verwenden im aktiven Reviewpool einen echten
        # Präsentationstoken. ``skip`` bleibt als verständlicher Alias für
        # die nicht-erfolgreiche Deferierung erhalten; ohne Token gilt aus
        # Rückwärtskompatibilität weiter der alte Cursorvertrag.
        if project and presentation_id:
            if role not in ("maintainer", "taskwriter") or not reason or task_raw or undo:
                print(
                    "Review-Skip: --role taskwriter|maintainer --project P "
                    "--presentation-id ID --reason TEXT",
                    file=sys.stderr,
                )
                return 2
            from .client import TaskClient
            from .config import review_pool_config
            from .review_pool import (
                ProjectHashError,
                ReviewConflict,
                ReviewPool,
            )
            try:
                state = ReviewPool(
                    TaskClient(), policy=review_pool_config()
                ).defer(role, project, presentation_id, reason)
            except (ReviewConflict, ProjectHashError) as exc:
                print(f"Projekt konnte nicht deferiert werden: {exc}", file=sys.stderr)
                return 1
            print(
                f"Für die Rolle {role} deferiert bis "
                f"{state['deferred_until']}: {project}"
            )
            return 0

        state_file = rotation_state_file()

        # Aufgabenebene zuerst: Eine einzelne blockierte Aufgabe soll nicht
        # ueber ihre Etiketten aus der Auswahl gedraengt werden muessen.
        if task_raw:
            try:
                task_id = int(task_raw)
            except ValueError:
                print(f"--task erwartet eine Zahl, nicht {task_raw!r}.",
                      file=sys.stderr)
                return 2
            if undo:
                if not undefer_task(state_file, role, task_id):
                    print("Rotationszustand konnte nicht geschrieben werden.",
                          file=sys.stderr)
                    return 1
                print(f"Wieder eingereiht: Aufgabe {task_id}")
            else:
                if not defer_task(state_file, role, task_id):
                    print("Rotationszustand konnte nicht geschrieben werden.",
                          file=sys.stderr)
                    return 1
                print(f"Ans Ende gereiht für die Rolle {role}: Aufgabe {task_id}")
                _release_claim_on_defer(task_id)

        if project:
            if not remember_project(state_file, role, project):
                print("Rotationszustand konnte nicht geschrieben werden.",
                      file=sys.stderr)
                return 1
            print(f"Übersprungen für den nächsten Lauf: {project}")
        return 0

    if command in ("help", "-h", "--help"):
        print("taskplan — Aufgabenverwaltung")
        print()
        print("Befehle:")
        print("  next [--role R] [--json]")
        print("            Fragt den SELEKTOR: was ist als naechstes dran?")
        print("            Liefert Modus (surface/deep), Aufwand, Root, Projekt,")
        print("            Task-IDs und die Rechte in diesem Projekt.")
        print("            Exit 0 [BUNDLE_READY] = Bündel erfolgreich geliefert")
        print("            Exit 1 [NO_WORK] = Rolle aktiv, derzeit kein Bündel")
        print("            Exit 2 [ROLE_DISABLED] = Rolle deaktiviert")
        print("            Exit 3 [RETRYABLE_SELECTOR_ERROR] = wiederholbarer")
        print("                    Selektor-/Discovery-Fehler")
        print()
        print("  doctor    Zeigt, welche Datenbank benutzt wird, und warnt bei")
        print("            widerspruechlichen Fundstellen (leere aktive DB,")
        print("            Daten woanders).")
        print()
        print("  projects list | refresh | add | remove | flag | unflag | markers")
        print("            Verwaltet die manuelle Projekt-Registry — der Fallback")
        print("            der Auto-Erkennung. 'list' und 'refresh' zeigen das")
        print("            Discovery-Inventar, 'add'/'remove' pflegen manuelle")
        print("            Eintraege, 'flag'/'unflag' markieren ein Verzeichnis")
        print("            per Flagdatei als Projekt, 'markers' zeigt die")
        print("            aktiven Marker-Regeln.")
        print()
        print("  review status|complete|defer|unseal|effort --role R --project P")
        print("            Diagnostiziert den lokalen Projektpool, bestätigt einen")
        print("            Abschluss per Präsentationstoken, deferiert einen Blocker,")
        print("            bricht ein Siegel manuell oder setzt den Review-Aufwand.")
        print()
        print("  prompt <ROLLE>")
        print("            Gibt den Rollen-Prompt aus (TASKSOLVER, TASKWRITER,")
        print("            MAINTAINER).")
        print()
        print("  maintainer-plan --input FINDING.json")
        print("            Prüft Policy-, Evidenz-, Lock-, Reversibilitäts- und")
        print("            Routing-Gates und liefert einen JSON-Plan; führt selbst")
        print("            weder Dateimoves noch Audits noch Tickets aus.")
        print()
        print("  runtime --role R [--provider P] [--field FELD]")
        print("            Liefert Provider, Rollenmodell, Reasoning und Fortsetzung.")
        print()
        print("  startup-prompt --role R [--provider P]")
        print("            Erzeugt fuer Codex den autorisierten Goal-Auftrag.")
        print()
        print("  backoff --role R [--provider P]")
        print("            Erzwingt die konfigurierte Wartezeit vor einem Retry.")
        print()
        print("  launch --role R --provider P")
        print("            Startet Claude, Codex oder Agy über das Runtime-Profil.")
        print()
        print("  starters list | starters path --role R --provider P")
        print("            Listet bzw. lokalisiert die gebündelten Windows-Starter.")
        print()
        print("  skip --role <maintainer|taskwriter|tasksolver>")
        print("       [--project PFAD] [--task ID [--undo]]")
        print("       [--presentation-id ID --reason TEXT]")
        print("            --project plus Präsentationstoken deferiert ein Writer-/")
        print("            Maintainer-Review ohne Erfolg. Ohne Token gilt der alte")
        print("            Projekt-Cursorvertrag (insbesondere für TASKSOLVER).")
        print("            --task reiht EINE blockierte Aufgabe ans Ende aller")
        print("            Kandidaten (Revolver) — statt sie über ihre Etiketten")
        print("            (effort/priority/status) aus der Auswahl zu drängen.")
        print("            Sind alle Kandidaten zurückgestellt, kommt die am")
        print("            längsten wartende dran; --undo reiht wieder vor ein.")
        print()
        print("Als Bibliothek:  from taskplan import api as tasks")
        return 0

    print(f"Unbekannter Befehl: {command!r}. `python -m taskplan help` zeigt die Liste.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
