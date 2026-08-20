# Changelog

## Unreleased

### Fixed
- Der TASKSOLVER-Vertrag begrenzt lokale Bündelfehler jetzt auf drei
  dokumentierte Versuche, protokolliert danach einen SKIP-Grund und setzt die
  Queue-Prüfung fort. `cldflt.sys`, Locks, Fremdzustände und übrige Gates bleiben
  lokal fail-closed; es wurde keine neue Retry-Engine oder Task-Status-Semantik
  erfunden.
- AGY-Starter können über `TASKPLAN_AGY_SCHEDULE_MINUTES` einen expliziten,
  ablauflosen Schedule-Auftrag erzeugen. Der Auftrag hält jeden Worker-Lauf
  einmalig und verbietet eine Endlosschleife im laufenden Prozess.
- `api.add()` und `api.list()` reichen jetzt die Einstufungsfelder
  (`effort`, `scope`, `project_path`, `root_id`, `source` bzw. `assigned_to`)
  durch an `TaskClient`, das sie bereits unterstützte — die Fassade allein
  kannte sie nicht. Das README-Quickstart-Beispiel
  (`tasks.add(..., effort="easy", project_path="/repos/foo", root_id="OSS")`
  gefolgt von `tasks.list(effort="easy", scope="local")`) schlug bisher mit
  `TypeError: unexpected keyword argument` fehl. `created_by`/`assigned_to`
  bleiben bewusst keine `add()`-Parameter (siehe README "Who created it, who
  works on it") — beide neuen Parameterlisten sind rückwärtskompatibel
  angehängt, bestehende Aufrufe mit 1–4 Positionsargumenten sind unverändert.
- `python -m taskplan help` nennt jetzt auch `projects` (list/refresh/add/
  remove/flag/unflag/markers) — der Befehl war im Dispatch von `main()`
  längst implementiert, fehlte aber vollständig im Hilfetext.
- `api.add_from_ticket()` reicht jetzt ebenfalls `effort`/`scope`/
  `project_path`/`root_id`/`source` durch — dieselbe Lücke wie oben, nur für
  die Ticket-Brücke. Ein aus einem Ticket erzeugter Task blieb bisher
  unklassifiziert und fiel beim Effort-Gate des Selektors auf die
  Degradierung zurück. Die Ticket-Referenz bleibt unverändert ausschließlich
  in `tags` als `ticket:<id>` kodiert; `source` ist ein eigenständiges Feld
  (die Fundstelle, z. B. ein Dateiname) und kollidiert damit nicht.
  `created_by`/`assigned_to` bleiben aus denselben Gründen wie bei `add()`
  außen vor.
- Windows-Pfad-Docstring im Traversal-Test als Raw-String markiert, damit `python -m pytest` ohne `SyntaxWarning` läuft.
- TOML-Konfigurationstests überspringen Python 3.10 korrekt, wenn `tomllib` als Stdlib-Modul noch nicht verfügbar ist.
- Die Projekt-Discovery invalidiert ihr Inventar nicht mehr durch fachfremde Modell-/Provider-Konfigurationsänderungen. Ein 24-Stunden-Refresh erfolgt Root-sektorweise; bei Timeout bleibt der vollständig geschriebene Last-known-good-Sektor aktiv und ein hängender Root rotiert hinter andere fällige Sektoren.
- Der Selektor fällt bei Discovery-Fehlern auf Cache/Registry und anschließend bekannte Projektpfade aus dem Task-Store zurück. Exit `3` erscheint nur noch, wenn keine sichere lokale Inventarquelle verfügbar ist.
- Die Verzeichnis-Traversierung verwendet `os.scandir` mit isolierten Eintragsfehlern, sodass Cloud-Platzhalter keinen vollständigen Scan unnötig abbrechen.
- Provider-Starter lesen Modell und Reasoning jetzt immer aus der ausdrücklich benannten Provider-Sektion; ein global gesetzter Codex-Provider kann dadurch keinen Codex-Modellnamen mehr in einen Claude-Start einschleusen.
- TASKWRITER und MAINTAINER werten nun die vollständige Projekthistorie aus. Mehr als 1.000 Aufgaben lassen ältere, bereits erfasste oder noch aktive Projekte nicht mehr aus dem Selektorfenster fallen.
- Autonome Selektoren überspringen nun auch stale Lockdateien. Der 24-Stunden-TTL erlaubt weiterhin eine bewusst manuell geprüfte Änderung, führt aber nicht mehr dazu, dass dasselbe lokal blockierte Bündel endlos erneut gewählt wird, obwohl ein anderer sicherer Kandidat verfügbar ist.
- Projektbasierte MAINTAINER-Läufe persistieren jetzt einen atomaren Rotationscursor; ein freies Projekt wird nicht mehr bei jedem neuen CLI-Prozess erneut als erstes Bündel geliefert. Mit `taskplan skip --role maintainer --project <pfad>` kann ein Projekt für den nächsten Lauf übersprungen werden.
- Projektbasierte TASKWRITER-Erfassungsläufe verwenden nun denselben rollenspezifischen, atomaren Rotationsmechanismus. Reale unklassifizierte Task-Bündel bleiben bis zur Bearbeitung stabil; nur leere Projekt-Sweeps rotieren weiter. `taskplan skip` unterstützt deshalb auch `taskwriter`.

### Added
- Policy-aware MAINTAINER-Planung: `python -m taskplan maintainer-plan`
  klassifiziert einen belegten JSON-Befund fail-closed als `safe_autofix`,
  `needs_ticket`, `needs_system_audit`, `needs_user_decision` oder
  `informational`. Policy-Adoption, Locks, Reversibilität, Symlink-/Cloud-/
  Dirty-Git-/Secret-Gates und ein stabiler Deduplizierungsfingerprint werden
  maschinenlesbar geprüft; der Planer mutiert selbst nichts.
- Der zweisprachige MAINTAINER-Vertrag nutzt `policy-registry`,
  `system-auditor` und `ticket-master` nur über stabile Oberflächen, routet
  systemweite Befunde als deduplizierte Audit-Handoff-Tickets und verlangt für
  Moves Vorher-/Nachher-Pfad, SHA-256 und Rollback-Receipt. Eine fehlende
  universelle Ablagepolicy löst weiterhin empirische Einzelfallprüfung statt
  automatischer Policy-Erfindung aus.
- Discoverability, README-Design & SEO-Check (Pfad B): Interaktive Mermaid-Architekturdiagramme für den 3-Rollen-Ablauf und das deterministische Selektor-Gate in `README.md` und `README_de.md` integriert, Ökosystem- und Dachorganisations-Badges (`ellmos-ai`, `open-bricks`, stdlib-only) ergänzt, `llms.txt` Verifikations- und Zeitstempel auf 2026-08-14 synchronisiert, Unused-Imports in Testsuite bereinigt (302/302 Tests 100% grün, Ruff sauber). [2026-08-14]
- Reproduzierbarer `benchmarks/taskplan_locking.py` für begrenzte Discovery,
  parallele SQLite-TaskClient-Schreibvorgänge und LockMaster-Claim-/Readback;
  das lokale Prozess-Ergebnis vom 13.08.2026 liegt in
  `results/benchmark_locking_20260813.json` und nennt die Mehrmaschinen-Grenze
  ausdrücklich.
- Technische Hygiene & Doku-Check (Pfad A): `llms.txt` Last-checked Datum auf 2026-07-30 aktualisiert, Test-Badges in `README.md` / `README_de.md` auf 283 passed/bestanden aktualisiert, Repo-Referenz auf `ellmos-ai/task-master` präzisiert und Pytest Testsuite verifiziert (283/283 Tests 100% grün). [2026-07-30]
- Maschinen- und menschenlesbarer Exit-Vertrag für `next`: stabile Namen
  `BUNDLE_READY`, `NO_WORK`, `ROLE_DISABLED` und
  `RETRYABLE_SELECTOR_ERROR`, deutsche/englische Bedeutungen im JSON sowie
  ausgeschriebene Konsolenausgabe.
- Discovery-Metadaten zu Quelle, Degradierung, Cache-Alter, aktualisiertem und
  noch ausstehenden Root-Sektoren.
- Einmaliger TASKSOLVER-Startpreflight in Deutsch und Englisch: TASKPLAN-Systemcheck,
  eng begrenzte belegte Control-Plane-Wartung sowie aktuelle Modellrecherche mit
  Kosten-Nutzen-Gate vor dem ersten Selektoraufruf.
- Neun nutzerneutrale Windows-Starter für Claude, Codex und Agy, zentral über
  `python -m taskplan launch`; Ressourcenzugriff über `taskplan starters`.
- Technische Hygiene & Doku-Check: `llms.txt` Index-Datei hinzugefügt, Shields.io Badges, KI/LLM-Integrationshinweis in `README.md` / `README_de.md` eingebunden und Pytest Testsuite verifiziert (245/245 Tests 100% grün). [2026-07-26]
- Nutzerneutrale Provider-Runtime mit rollenbezogenen Modellen und Reasoning
  unter `[providers.<name>]`; die bisherige `[models]`-Sektion bleibt kompatibel.
- Codex-Adapterprofil `continuation = "goal"` mit explizit autorisierendem
  Startup-Prompt, Leerlauf-Policy und technisch ausgeführtem Backoff.
- CLI-Befehle `runtime`, `startup-prompt` und `backoff` für dünne, portable Starter.
- Begrenzte Projekt-Discovery: konfigurierbarer Timeout liefert Exit-Code `3`
  statt TASKWRITER oder MAINTAINER auf Cloud-Dateisystemen festzuhalten; der
  Scan läuft abbrechbar im Unterprozess und nutzt einen invalidierbaren Cache.
- Vierter Starter-Provider `kimi` (Kimi Code CLI) für alle drei Rollen:
  `taskplan launch --provider kimi`, drei weitere paketierte Starter (insgesamt
  zwölf) sowie `[providers.kimi]`-Sektionen in `taskplan.example.toml`.
  Verifiziert gegen CLI 0.29.2 (2026-07-28): die CLI kennt keinen interaktiven
  Startprompt (positional wird als Subcommand abgelehnt) und kein
  `--effort`-Flag — Kimi-Worker laufen deshalb headless (`kimi --prompt`),
  die Reasoning-Stufe kommt aus `default_effort` des Modells in
  `~/.kimi-code/config.toml` und wird im Starter nur angezeigt. [2026-07-28]

### Security
- Repository-Hygiene erweitert: lokale Env-/Token-/Credential-/Recovery-/Key-,
  Zertifikats- und SQLite-Sidecar-Dateien werden ignoriert und per Test
  abgesichert.

## 0.2.0 — 2026-07-11

### Added
- TASKSOLVER und TASKWRITER als gebündelte, importierbare Workflow-Prompts.
- `get_workflow_prompt()`, `get_workflow_prompt_path()` und `list_workflows()`.
- Paketdaten-Konfiguration und Tests für Import, Lookup, UTF-8 und reale Promptpfade.

## 0.1.0 — 2026-07-11

### Added
- Initial extraction from `rinnsal/tasks` (decision [U 2026-07-11],
  `.MEMORY` stack: USMC + GARDENER + TASKPLAN).
- `taskplan.client.TaskClient` — SQLite task CRUD (table `rinnsal_tasks`
  kept for data compatibility), WAL mode, `:memory:` support.
- `taskplan.api` — singleton convenience API (`init`, `add`, `list`, `done`,
  `next_task`, `active_tasks`, …).
- `api.add_from_ticket()` — the only ticket→task bridge (tag `ticket:<id>`);
  tasks and tickets remain separate systems.
- Own default DB resolution: `TASKPLAN_DB` > `RINNSAL_DB` >
  `~/.taskplan/taskplan.db` (the rinnsal facade injects its own default).
- Test suite (17 tests, ported from rinnsal + default-path and ticket-bridge
  coverage).
