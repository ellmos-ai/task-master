# Changelog

## Unreleased

### Fixed
- Die Projekt-Discovery invalidiert ihr Inventar nicht mehr durch fachfremde
  Modell-/Provider-Konfigurationsänderungen. Ein 24-Stunden-Refresh erfolgt
  Root-sektorweise; bei Timeout bleibt der vollständig geschriebene
  Last-known-good-Sektor aktiv und ein hängender Root rotiert hinter andere
  fällige Sektoren.
- Der Selektor fällt bei Discovery-Fehlern auf Cache/Registry und anschließend
  bekannte Projektpfade aus dem Task-Store zurück. Exit `3` erscheint nur noch,
  wenn keine sichere lokale Inventarquelle verfügbar ist.
- Die Verzeichnis-Traversierung verwendet `os.scandir` mit isolierten
  Eintragsfehlern, sodass Cloud-Platzhalter keinen vollständigen Scan unnötig
  abbrechen.
- Provider-Starter lesen Modell und Reasoning jetzt immer aus der ausdrücklich
  benannten Provider-Sektion; ein global gesetzter Codex-Provider kann dadurch
  keinen Codex-Modellnamen mehr in einen Claude-Start einschleusen.
- TASKWRITER und MAINTAINER werten nun die vollständige Projekthistorie aus.
  Mehr als 1.000 Aufgaben lassen ältere, bereits erfasste oder noch aktive
  Projekte nicht mehr aus dem Selektorfenster fallen.
- Autonome Selektoren überspringen nun auch stale Lockdateien. Der 24-Stunden-
  TTL erlaubt weiterhin eine bewusst manuell geprüfte Änderung, führt aber
  nicht mehr dazu, dass dasselbe lokal blockierte Bündel endlos erneut gewählt
  wird, obwohl ein anderer sicherer Kandidat verfügbar ist.
- Projektbasierte MAINTAINER-Läufe persistieren jetzt einen atomaren Rotationscursor;
  ein freies Projekt wird nicht mehr bei jedem neuen CLI-Prozess erneut als erstes
  Bündel geliefert. Mit `taskplan skip --role maintainer --project <pfad>` kann ein
  Projekt für den nächsten Lauf übersprungen werden.
- Projektbasierte TASKWRITER-Erfassungsläufe verwenden nun denselben
  rollenspezifischen, atomaren Rotationsmechanismus. Reale unklassifizierte
  Task-Bündel bleiben bis zur Bearbeitung stabil; nur leere Projekt-Sweeps
  rotieren weiter. `taskplan skip` unterstützt deshalb auch `taskwriter`.

### Added
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
