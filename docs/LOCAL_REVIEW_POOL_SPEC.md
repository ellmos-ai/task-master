# Lokaler Siegel- und Wiedervorlagepool

Status: verbindliche Phase-A-Spezifikation für `T-20260830-202718995`
Geltungsbereich: TASKWRITER und MAINTAINER in einer einzelnen, hostlokalen
TASKPLAN-Datenbank. Synchronisation, Transit und Fremdhost-Zustand sind nicht
Teil dieses Vertrags.

## 1. Invarianten

1. `rinnsal_tasks` bleibt unverändert die Task-Tabelle. Review-Zustand ist kein
   Task, kein Claim und ändert insbesondere nie `agent_id`, `created_by`,
   `assigned_to`, `delegation_status` oder einen Task-Status.
2. Die Identität eines Review-Eintrags ist `(role, project_key)`. Unterstützte
   Rollen sind `taskwriter` und `maintainer`; ihre Zustände sind vollständig
   voneinander getrennt.
3. `next` darf einen Kandidaten erst ausliefern, nachdem seine kurze
   Präsentationslease atomar gespeichert wurde. Nur `complete` mit der
   passenden `presentation_id` schreibt ein Siegel. Prozessabbruch,
   `NO_WORK`, Backoff und bloße Präsentation sind kein Erfolg.
4. Alle Zeiten sind UTC-ISO-8601 mit Zeitzone. Vergleiche erfolgen nach
   normalisierten UTC-Zeitpunkten. Die Implementierung nimmt eine Uhr als
   Abhängigkeit entgegen, damit Tests keine reale Zeit verwenden.
5. Die lokale TASKPLAN-Datenbank ist die einzige Wahrheit dieses Hosts. Keine
   Operation dieses Moduls liest oder schreibt Transit-Dateien.

## 2. Additives Schema

`TaskClient._ensure_schema()` legt zwei zusätzliche Tabellen idempotent mit
`CREATE TABLE IF NOT EXISTS` an. Bestehende Tabellen, Spalten und Zeilen werden
nicht umgeschrieben.

### `taskplan_project_reviews`

| Spalte | Bedeutung |
|---|---|
| `role`, `project_key` | zusammengesetzter Primärschlüssel |
| `project_path`, `root_id` | letzter lesbarer Anzeigen-/Routingwert |
| `effort` | `easy` oder `medium`; Sortierdimension, Default `easy` |
| `sealed_hash` | Hash des zuletzt bestätigten Erfolgs, sonst `NULL` |
| `last_presented_at` | letzte tatsächliche Lieferung |
| `presentation_id` | opaker Token der laufenden Präsentation |
| `presented_hash` | Hash beim Beginn dieser Präsentation |
| `presentation_lease_until` | kurze Exklusivitätsgrenze |
| `last_reviewed_at` | Zeitpunkt des letzten bestätigten Erfolgs |
| `result` | Ergebnistext des letzten bestätigten Erfolgs |
| `next_due_at` | reguläre Wiedervorlagegrenze |
| `deferred_until`, `deferred_hash`, `defer_reason` | temporäre, nicht erfolgreiche Zurückstellung |
| `manual_unseal_at`, `manual_unseal_reason` | jüngster protokollierter manueller Siegelbruch |
| `created_at`, `updated_at` | lokale Datensatzzeiten |

Leere Erfolgswerte sind `NULL`, nicht erfundene Zeitstempel oder Leer-Hashes.
Ein Index über `(role, effort, last_presented_at)` unterstützt die Auswahl.

### `taskplan_project_review_events`

Append-only-Protokoll mit `id`, `role`, `project_key`, `project_path`, `event`,
`occurred_at`, `presentation_id` und `detail`. Ereignisse sind mindestens
`presented`, `sealed`, `deferred` und `manual_unseal`. Ein manueller
Siegelbruch ist damit auch nach einem späteren erfolgreichen Siegel noch
nachweisbar.

## 3. Projektidentität und Hashvertrag

`project_key` ist der absolute, lexikalisch normalisierte Hostpfad mit `/` als
Separator; auf case-insensitiven Systemen wird er per `normcase` vereinheitlicht.
Der Projektordner muss existieren. Symlink-Auflösung ist nicht Teil der
Identität, damit eine Änderung des Linkziels nicht unbemerkt den Schlüssel
wechselt.

Der Hashalgorithmus ist versioniert (`taskplan-project-sha256-v1`) und SHA-256.
Er umfasst, lexikografisch nach NFC-normalisiertem relativen POSIX-Pfad
sortiert:

- jede reguläre Datei als Datensatz aus Typ, relativem Pfad, Bytezahl und
  Dateiinhalt;
- jeden Symlink als Datensatz aus Typ, relativem Pfad und Linkziel;
- keine Verzeichnis-Metadaten, Zeitstempel, ACLs oder leeren Verzeichnisse.

Damit sind gleiche relevante Bytes und Pfade reproduzierbar, während reine
`mtime`-Änderungen kein Siegel brechen. Symlinks werden nie verfolgt. Ein
Pfad, der den Projektroot während der Traversierung verlassen würde, ein
Sonderdateityp, ein Lese-/Stat-Fehler oder eine während des Lesens erkannte
Dateiänderung beendet die Hashbildung fail-closed mit `hash_error`; es wird
weder präsentiert noch versiegelt.

Standardmäßig ausgeschlossene Verzeichnissegmente:

`.git`, `.hg`, `.svn`, `__pycache__`, `.pytest_cache`, `.mypy_cache`,
`.ruff_cache`, `.tox`, `.nox`, `.venv`, `venv`, `node_modules`, `dist`,
`build`, `htmlcov`, `.taskplan`.

Standardmäßig ausgeschlossene Dateien/Muster:

`LOCK.execution-contract.txt`, `.taskplan-lock*`, `*.pyc`, `*.pyo`,
`.DS_Store`, `Thumbs.db`, `.coverage`.

`[review_pool].exclude` ergänzt diese Liste mit POSIX-Globmustern. Ein Muster
mit `/` gilt für den ganzen relativen Pfad, sonst für jedes Segment bzw. den
Dateinamen. Ausschlüsse werden vor dem Betreten eines Verzeichnisses geprüft.
Alle nicht ausgeschlossenen regulären Dateien und Symlinks sind enthalten;
eine implizite Dateiendungs-Allowlist gibt es nicht.

## 4. Konfiguration

Der additive Abschnitt `[review_pool]` kennt:

- `enabled = true`
- `review_interval_seconds = 604800`
- `retry_interval_seconds = 3600`
- `presentation_lease_seconds = 900`
- `default_effort = "easy"`
- `exclude = []`

Review-/Retry-Intervalle sind nicht negativ, die Präsentationslease ist positiv;
ungültige Werte fallen auf dokumentierte Defaults zurück. `default_effort` ist
nur `easy` oder `medium`. Ein bereits
persistierter rollenspezifischer Aufwand gewinnt vor dem Default. API und CLI
können ihn explizit setzen, ohne Taskdaten umzuschreiben.

## 5. Eligibility und Diagnose

Für jedes entdeckte Projekt wird genau eine Entscheidung mit `eligible` und
einem maschinenlesbaren `reason` erzeugt. Die Präzedenz ist:

1. externer Projekt-Lock → `lock`, nicht kandidat;
2. aktive Präsentationslease → `presentation_lease`, nicht kandidat;
3. noch nie präsentiert → `never_presented`, kandidat;
4. Hash weicht vom `deferred_hash` ab → `hash_break`, kandidat;
5. manueller Siegelbruch ist jünger als der letzte Erfolg → `manual_unseal`, kandidat;
6. unveränderte aktive Deferierung → `deferred`, nicht kandidat;
7. präsentiert, aber noch nie erfolgreich versiegelt → `never_sealed`, kandidat;
8. Hash weicht vom `sealed_hash` ab → `hash_break`, kandidat;
9. `next_due_at <= now` → `due`, kandidat;
10. sonst → `unchanged_sealed`, nicht kandidat.

Eine abgelaufene Präsentationslease wird nicht als Erfolg behandelt; danach
greift wieder die fachliche Entscheidung. Ein `hash_error` ist fail-closed und
erscheint in der Diagnose. So kann `NO_WORK` zwischen „unverändert“, „deferiert“,
„gelockt“ und „nicht lesbar“ unterscheiden.

## 6. Sortierung und Starvation-Gate

Nach dem Eligibility-Filter lautet der stabile Sortierschlüssel:

1. Aufwand: `easy` vor `medium`;
2. noch nie präsentiert vor bereits präsentiert;
3. `last_presented_at` aufsteigend, also älteste Vorlage zuerst;
4. `project_key` aufsteigend als eindeutiger Tie-Breaker.

Deferierte oder geleaste Elemente gehören nicht zur frischen Menge. Deshalb
blockiert ein deferiertes `easy` kein frisches `easy`; existiert kein frisches
`easy`, kann ein `medium` ausgewählt werden. Nach Hashänderung oder Ablauf von
`deferred_until` ist das Element wieder frisch und `easy` gewinnt erneut.

Die bestehende TASKSOLVER-Trommel bleibt separat und unverändert: Frische
Task-`easy` gewinnen vor `medium`; sind alle zulässigen `easy`-Tasks in
`deferred_tasks`, darf der TASKSOLVER zu `medium` wechseln.

## 7. Atomare Zustandsübergänge

Alle schreibenden Übergänge laufen in einer SQLite-Transaktion mit
`BEGIN IMMEDIATE`.

### `eligible -> presented`

Der Pool hasht das Projekt, bewertet es und versucht anschließend dieselbe
Entscheidung unter Schreibsperre erneut. Nur wenn keine aktive Lease besteht
und der Zustand noch kandidat ist, erzeugt er eine zufällige
`presentation_id`, schreibt `last_presented_at`, `presented_hash` und
`presentation_lease_until` und protokolliert `presented`. Die Lieferung enthält
Token, Hash, Lease, Eligibility-Grund und Diagnoseübersicht.

### `presented -> sealed`

`complete(role, project, presentation_id, result)` verlangt einen exakt
passenden aktiven Präsentationstoken. Es bildet den aktuellen Projekt-Hash und
schreibt erst dann `sealed_hash`, `last_reviewed_at`, `result` und
`next_due_at = now + review_interval`. Präsentations-, Defer- und manuelle
Break-Felder werden geleert; `sealed` wird protokolliert. Der Token wird danach
ungültig. Ein falscher/fehlender Token, Hashfehler oder zweiter Abschluss
scheitert ohne Teilerfolg.

### `presented -> deferred`

`defer(role, project, presentation_id, reason)` verlangt denselben Token und
einen nicht leeren Grund. Es speichert `deferred_hash = aktueller Hash`,
`deferred_until = now + retry_interval` und `defer_reason`, leert die
Präsentationslease und protokolliert `deferred`. Erfolgsfelder bleiben
unverändert. Eine Hashänderung bricht die Deferierung sofort.

### `sealed/deferred -> manual_unseal`

`unseal(role, project, reason)` verlangt einen nicht leeren Grund, setzt
`manual_unseal_at`/`manual_unseal_reason` und protokolliert `manual_unseal`.
Ein laufender Worker bleibt bis zum Lease-Ende exklusiv; danach gewinnt der
manuelle Break. Das alte Siegel bleibt als Historie erhalten, bis ein neuer
bestätigter Erfolg es ersetzt.

## 8. CLI- und API-Vertrag

`python -m taskplan review ...` bietet:

- `status --role R --project P [--json]`: Zustand plus aktuelle Entscheidung;
- `complete --role R --project P --presentation-id ID --result TEXT`;
- `defer --role R --project P --presentation-id ID --reason TEXT`;
- `unseal --role R --project P --reason TEXT`;
- `effort --role R --project P --set easy|medium`.

Die Python-API spiegelt diese Operationen als `review_status`,
`complete_review`, `defer_review`, `unseal_review` und `set_review_effort`.
Mutationen liefern strukturierte Ergebnisse und werfen bei Vertragsbruch eine
spezifische Ausnahme; sie geben keinen scheinbaren Erfolg als bloßes `False`
aus. `next --json` enthält bei Projektrollen `review.presentation_id`, Hash,
Lease und Eligibility-Grund. Textausgabe zeigt Token und Abschlussbefehle.

## 9. TDD-Akzeptanzmatrix

| ID | Beweis |
|---|---|
| M01 | Öffnen einer v1/v2/v3-DB erzeugt nur die beiden neuen Tabellen; Taskzeilen und Herkunft bleiben byte-/wertgleich. |
| M02 | Wiederholtes Öffnen ist idempotent; TASKWRITER- und MAINTAINER-Zeilen desselben Projekts sind unabhängig. |
| H01 | Gleicher synthetischer Baum ergibt denselben v1-Hash; Inhalts-/Pfadänderung ändert ihn, reine `mtime`-Änderung nicht. |
| H02 | Git, Cache, Build, eigener Lock und konfigurierte Excludes ändern den Hash nicht; reguläre relevante Datei schon. |
| H03 | Symlink wird als Link gehasht und nicht verfolgt; Sonderdatei, Lesefehler oder Mid-read-Änderung ist `hash_error`. |
| E01 | Nie präsentiert gewinnt innerhalb eines Aufwands vor älter/neu präsentiert. |
| E02 | Innerhalb eines Aufwands gewinnt das älteste `last_presented_at`; Pfad bricht Gleichstand stabil. |
| E03 | Gleiches Siegel vor Fälligkeit ist `unchanged_sealed`; Hashbruch, Fälligkeit und manueller Break öffnen jeweils. |
| E04 | Aktive Lease verhindert Doppellieferung; Ablauf macht einen unversiegelten Crash wieder kandidat. |
| S01 | Präsentation setzt nur Lease/Vorlage, nie Siegel-/Erfolgsfelder. |
| S02 | Passender Token versiegelt mit aktuellem Hash, Ergebnis, Prüfzeit und `next_due_at`; falscher oder wiederverwendeter Token scheitert atomar. |
| D01 | Defer setzt Retry-Zeit ohne Erfolg; vor Retry keine Wiederholung, Hashänderung öffnet sofort, Retry-Ablauf öffnet erneut. |
| D02 | Deferiertes `easy` lässt frisches `easy` gewinnen; ohne frisches `easy` wird `medium` erreichbar; nach Wiederöffnung gewinnt `easy`. |
| T01 | Vorhandene TASKSOLVER-Revolvertests bleiben grün: frisches `easy` vor `medium`, alle `easy` deferiert öffnen `medium`. |
| P01 | Zustand überlebt neue `ReviewPool`-/`TaskClient`-Instanz und Prozessgrenze in einer Dateidatenbank. |
| X01 | Diagnose nennt mindestens `never_presented`, `hash_break`, `due`, `manual_unseal`, `deferred`, `unchanged_sealed`, `lock`, `presentation_lease`, `hash_error`. |
| C01 | CLI/API decken Status, bestätigten Abschluss, Defer, manuellen Break und Aufwand ab; JSON bleibt maschinenlesbar. |
| L01 | Tests verwenden temporäre Bäume und Fake-Uhr; kein OneDrive-, Netzwerk-, Transit- oder Fremdhostzugriff. |

Die Implementierung ist erst abnahmefähig, wenn diese Matrix sowie die gesamte
bestehende Testsuite grün sind.
