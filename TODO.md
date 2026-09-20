# TODO — robuste Discovery-Fallbacks

## Offen: Pipeline-Wurzeln gelten als Projekt — T-20260920-535056160 (2026-09-20)

- [ ] Nach dem Worktree-Fix bleiben auf ASUS-GEI **29 Zeitueberschreitungen**.
  Ueberwiegend sind das keine Projekte, sondern **Pipeline-Wurzeln**
  (`.TOPICS/.AI`, `.TOPICS/.RESEARCH`, `.TOPICS/.SOFTWARE`, `.AI/.MODULES`,
  `.AI/.OS`, `.AI/.SKILLS`, ...). Sie zaehlen nur deshalb als Projekt, weil sie
  selbst eine `CLAUDE.md`/`TODO.md` tragen — die in `traversal.py` ausdruecklich
  dokumentierte Grenze des Auto-Modus. Gemessen: Ihr Digest umfasst den
  gesamten Unterbaum, deshalb reissen sie jedes Zeitlimit.
  **Kosten heute:** 29 x 30 s = ~15 min reine Wartezeit pro `init`-Lauf
  (zweiter Lauf 1046 s, davon ~870 s Timeouts). Der Host nimmt deshalb den
  dokumentierten Ausgang `[readiness] skip_timeouts = true`.
  **Naechster Schritt:** Container-Ebene strukturell erkennen (analog zum
  Worktree-Veto), statt sie je Host von Hand auszuschliessen. Ein Verzeichnis,
  dessen Kinder ueberwiegend selbst Projekte sind, ist eine Zwischenebene.

## CI-Abnahme — T-20260906-833934205 (2026-09-09)

- [x] Die drei Subprozess-Discovery-Tests konfigurieren eigene temporäre,
  erlaubte Roots. Fehlende Arbeitsplatzkonfiguration darf einen Test nicht
  verdeckt vom Entwicklerhost abhängig machen. Die Laufzeitvalidierung bleibt
  fail-closed; die vollständige Suite besteht mit 448 Tests.

Stand: 2026-07-27 — umgesetzt; die Punkte bleiben als Betriebsnachweis erhalten.

## Befund

- `python -m taskplan next --role maintainer --json` kann nach
  `discovery_timeout_seconds` mit Exit 3 und `project_discovery_timeout`
  abbrechen, bevor `next_bundle()` überhaupt einen Kandidaten auswählen kann.
- Ursache der beobachteten Exit-0-/Exit-3-Schleife war eine Signatur über die
  gesamte `taskplan.toml`: Schon ein Modellwechsel verwarf den Projekt-Cache.
  Der dadurch erzwungene OneDrive-Vollscan überschritt anschließend den
  Discovery-Timeout, bevor der Selektor arbeiten konnte.
- Im Discovery-Modus `hybrid` läuft die automatische Suche vor der manuellen
  Registry. Hängt die automatische Suche, wird die Registry nicht mehr erreicht.
- Der MAINTAINER wählt Projekte, keine einzelnen Tasks. Ohne Projektinventar kann
  er daher nicht auf einen anderen sicheren Kandidaten umleiten.
- Analyse-Snapshot: Die Task-Datenbank enthielt 1.018 offene Tasks mit 144
  bekannten Projektpfaden; der Projekt-Cache enthielt 165 Projekte. Ein
  Discovery-Timeout ist deshalb nicht gleichbedeutend mit Aufgabenende.

## Aufgaben

- [x] **TP-DISC-FB-01 / TASKPLAN #1208 — Last-known-good-Cache bei Timeout nutzen**
  - Bei `project_discovery_timeout` den letzten vollständig geschriebenen Cache
    auch nach Ablauf der TTL als degradierten Fallback zulassen, sofern Version
    und Konfigurationssignatur passen.
  - Rückgabe sichtbar mit `discovery_source=stale_cache`, `degraded=true`,
    Cache-Alter und Warnung kennzeichnen.
  - Eine geänderte Discovery-Policy macht Sektoren refresh-fällig, verwirft ihr
    letztes vollständiges Inventar aber nicht vor einem erfolgreichen Ersatz.

- [x] **TP-DISC-FB-02 / TASKPLAN #1209 — Cache-Prüfung von blockierenden Cloud-Pfaden entkoppeln**
  - Sicherstellen, dass das Lesen eines Last-known-good-Snapshots nicht selbst an
    `Path.resolve()`, `stat()` oder einem nicht reagierenden OneDrive-Root hängt.
  - Signaturprüfung rein lokal oder separat zeitbegrenzt ausführen; bei nicht
    beweisbarer Kompatibilität konservativ zum nächsten Fallback wechseln.
  - Cache-Schreibvorgänge atomar lassen und nur vollständig erzeugte Snapshots
    freigeben.

- [x] **TP-DISC-FB-03 / TASKPLAN #1210 — Manuelle Registry trotz Auto-Discovery-Timeout erreichen**
  - Im Modus `hybrid` die Registry unabhängig von der automatischen Traversierung
    laden können.
  - Bei Auto-Timeout gültige Registry-Projekte als degradierte Kandidatenquelle
    verwenden und wie bisher deduplizieren bzw. bei Konflikten priorisieren.
  - Registry-I/O-Fehler sichtbar melden; eine leere Registry ist kein
    Aufgabenende, solange weitere sichere Quellen existieren.

- [x] **TP-DISC-FB-04 / TASKPLAN #1211 — MAINTAINER-Projektinventar aus Task-Daten ableiten**
  - Als letzten Kandidaten-Fallback nichtleere `project_path`-/`root_id`-Paare aus
    der Task-Datenbank deduplizieren.
  - Pfade rein lexikalisch auf erlaubte Roots begrenzen, damit gerade der
    Fallback nicht erneut an Cloud-`stat()` hängt. Aktive Zuweisungen und Locks
    filtert anschließend unverändert der bestehende Selektor.
  - Das abgeleitete Inventar durch den bestehenden `_maintainer_bundle()`-Pfad
    schicken; das Modell darf kein Ersatzprojekt selbst erfinden.

- [x] **TP-DISC-FB-05 / TASKPLAN #1212 — Geordnete Fallback-Kette und Exit-Semantik implementieren**
  - Reihenfolge: frischer Cache → begrenzt versuchte Auto-Discovery →
    signaturkompatibler Last-known-good-Cache → manuelle Registry → validiertes
    Task-Daten-Inventar.
  - `next_bundle()` mit der ersten sicheren Kandidatenquelle weiterlaufen lassen.
  - Exit 1 nur für ehrlich belegten Leerlauf ausgeben; Exit 3 nur, wenn Discovery
    fehlschlägt und keine sichere Fallback-Quelle verfügbar ist. Exit 2 bleibt
    ausschließlich der deaktivierten Rolle vorbehalten.
  - Quelle, Degradierungsstatus und verworfene Fallback-Gründe im JSON-Vertrag
    maschinenlesbar ausgeben.
  - Abhängigkeiten: TP-DISC-FB-01 bis TP-DISC-FB-04.

- [x] **TP-DISC-FB-06 / TASKPLAN #1213 — Regressionstests, Dokumentation und Betriebsnachweis ergänzen**
  - Tests für frischen Cache, abgelaufenen kompatiblen Cache, falsche Signatur,
    beschädigten Cache, Auto-Timeout mit Registry, Task-Daten-Fallback,
    Lock-Filterung und echten Leerlauf ergänzen.
  - Beweisen, dass ein Timeout-Unterprozess beendet wird und keine Scan-Threads
    oder Kindprozesse zurückbleiben.
  - README/README_de, Beispielkonfiguration und Changelog um Fallback-Reihenfolge,
    JSON-Felder und Exit-Semantik ergänzen.
  - Abhängigkeit: TP-DISC-FB-05.

## Bandmaster-Learnings — Task-Lebenszyklus ohne Lock-Doppelbau [2026-08-15]

- [ ] Beim `assign()` optional einen unveränderlichen Bearbeitungs-Snapshot
  speichern: Task-Version, Scope, Projektzustandsreferenz und externe Claim-ID.
  TaskMaster erzeugt oder verwaltet den Lock nicht; dafür bleibt der konfigurierte
  Lock-Provider zuständig.
- [ ] Für ausdrücklich gebildete Task-Batches eine Freeze-Barriere definieren:
  Nach Beginn dürfen Mitgliedschaft, Abhängigkeiten und Akzeptanzkriterien nicht
  still verändert werden. Änderungen erzeugen eine neue Batch-Version.
- [ ] Validierungsbelege an Task-/Scope-Version und Arbeitsbaum-Fingerprint
  binden. Eine spätere Mutation setzt den Beleg auf `stale`, statt den Task trotz
  veralteter Prüfung als erledigt erscheinen zu lassen.
- [ ] Append-only Recovery-Journal für `assigned`, `started`, `blocked`,
  `validated`, `done` und abgebrochene Finalisierung ergänzen; wiederholte
  Befehle müssen idempotent sein.
- [ ] TaskMaster speichert höchstens Referenzen auf Test-/Commit-/Handoff-Belege.
  Es führt keine Tests aus, erzeugt keine Commits und implementiert keine zweite
  Lock-Engine.

## Offen nach 0.6.0 (Nutzerauftrag OCEAN AGENTS STARTCENTER, 2026-09-06)

- [ ] **`doctor` warnt nicht vor fehlender Provider-Modellkonfiguration.** Genau das brach am
  2026-09-06 auf ASUS-GEI jeden Claude/Agy/Kimi-Start („Kein Modell konfiguriert"), waehrend
  `doctor` gruen meldete. Vorschlag: je Provider aus `PROVIDERS` pruefen, ob `[providers.<p>.models]`
  mindestens `default` traegt (Codex ausgenommen), und als WARNUNG ausgeben — kein Abbruch.
- [ ] **Gepinnte Starter (`starters/*/providers/`) ohne stillen Anbieterwechsel.** Ein Starter mit
  `--provider P` meint diesen Anbieter; heute faellt er ueber die Kette auf andere Provider zurueck.
  Vorschlag: `--no-fallback` in die 32 gepinnten Starter (oder ein `--pinned`, das nur den
  Default-Zweitversuch desselben Providers erlaubt). Die OneDrive-Starter tragen `--no-fallback`
  bereits.
- [ ] **Sonde laeuft im aktuellen Arbeitsverzeichnis, nicht in `TASKPLAN_WORKDIR`** (Auslegung des
  Workers, Auftrag schwieg). Angleichen, damit CLI-Konfigurationen mit Projektbezug (z. B.
  `.claude/settings`) in der Sonde dieselben sind wie im Start.
- [x] **Typing-Nit:** `launcher._provider_commands` verwendet seit 0.7.0
  `dict[str, object]`; Operator-Modus und optionaler Zeitplan teilen damit einen
  korrekten Typ.
- [ ] **Testluecke: fehlschlagendes `doctor()` blockiert externe Rollen, ungetestet.** Befund des PR-#4-Reviews (claude-sonnet, 2026-09-06): `launch()` ruft `doctor()` auch fuer `--label`-Rollen unconditional auf, aber die Fixture in `tests/test_launcher_probe.py` mockt `doctor` global auf Erfolg; die Kombination doctor-scheitert-plus-externe-Rolle hat keinen Test. Vorschlag: ein Test, der `doctor` auf Fehler patcht und fuer `--label ticket-master` Exit != 0 ohne Prozessstart erwartet.
- [x] **Rücktransfer-Richtung — umgesetzt in 0.7.0 (Nutzerentscheid E01):** KEIN neues
  Modul `agent-launcher` (existiert bereits: `ellmos-ai/agent-launcher`, Claude-only, benannte
  Dauerprozesse). Heimat der Provider-Mechanik (argv je CLI, Sonde, Fallback-Kette, Starter-
  Generator) wird **COMA** (`ellmos-ai/coma`, verifizierte Adapter claude/codex/agy); task-master
  0.7 konsumiert die optionale COMA-Sitzungs-API, `taskplan launch` bleibt Fassade. Tickets:
  T-20260906-463096841 (E01), T-20260906-737455509 (clutch als wählbare Fallback-Autorität),
  T-20260906-833934205 (`roles[]` in v2), T-20260906-249053451 (Starter ticket-master/system-auditor).
  Analyse: `~/OneDrive/.USR/OCEAN-RUECKTRANSFER-STARTER_2026-09-06.md` Abschnitt 6.
