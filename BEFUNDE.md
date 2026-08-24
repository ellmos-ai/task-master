# Offene Befunde — task-master

**Erfasst am:** 2026-07-28  
**Rolle:** MAINTAINER (TaskMaster Loop)

---

### Befund 1: Arbeitskopie & Git-Status

- **Fundort:** Repository `C:\_Local_DEV\repos\task-master` (Branch `agent/runtime-discovery-dependencies`).
- **Beleg:**  
  `git status` ist 100% sauber (`up to date with 'origin/agent/runtime-discovery-dependencies'`).
- **Status:** Keine uncommitteden Dateien oder offenen Branch-Abweichungen.

---

### Befund 2: Testsuiten-Status & Instandhaltung

- **Fundort:** `tests/` & `llms.txt`
- **Beleg:**  
  280 Unit-Tests und 21 Subtests bestanden 100% grün (`python -m pytest -q`).
- **Maßnahme:**  
  `llms.txt` im MAINTAINER-Lauf vom 2026-07-28 auf `280/280 Tests 100% grün` und `Last-checked: 2026-07-28` aktualisiert.

---

### Befund 3 (erledigt): `api.add_from_ticket()` leitet Klassifikationsfelder nicht durch

- **Erfasst am:** 2026-08-12 (AP5-Nacharbeit, ASUS-GEI)
- **Fundort:** `taskplan/api.py`, Funktion `add_from_ticket()`
- **Beleg:**  
  Dieselbe Fehlerklasse wie die am 2026-08-12 in Commit `00699ca` behobenen
  Lücken in `add()`/`list()`: `add_from_ticket()` reicht nur
  `title/description/priority/tags` durch, obwohl `TaskClient` die
  Schema-v2-Felder (`effort`, `scope`, `project_path`, `root_id`, `source`)
  kennt. Ein aus einem Ticket erzeugter Task ist damit unklassifiziert und
  fällt beim Effort-Gate des Selektors auf die Degradierung zurück.
- **Vorschlag:** gleiches Passthrough-Muster wie in `00699ca` anwenden
  (optionale Parameter mit rückwärtskompatiblen Defaults) + Tests analog
  `TestTasksApi`.
- **Behoben am:** 2026-08-12 (AP6, ASUS-GEI), siehe `CHANGELOG.md` (Abschnitt
  Unreleased/Fixed) und Git-Log (Folgecommit zu `00699ca`).
  `add_from_ticket()` reicht `effort`/`scope`/`project_path`/`root_id`/`source`
  additiv durch (`created_by`/`assigned_to` bewusst weiterhin ausgeschlossen,
  wie bei `add()`). Die Ticket-Referenz bleibt unverändert ausschließlich in
  `tags` als `ticket:<id>` kodiert; `source` ist ein eigenständiges Feld ohne
  Kollision. 4 neue Tests in `tests/test_taskplan.py` (`TestTasksApi`), volle
  Suite 295/295 grün.
- **Status:** erledigt.

---

### Befund 4: Prozess-/Locking-/Discovery-Benchmark (2026-08-13)

- **Beleg:** `results/benchmark_locking_20260813.json` und das reproduzierbare
  Skript `benchmarks/taskplan_locking.py`.
- **Ergebnis:** 200 synthetische Projekte wurden erkannt, 80/80 parallele
  SQLite-Schreibvorgänge gespeichert, und genau ein von vier Prozessen gewann
  den gemeinsamen LockMaster-Claim; alle Prozesse sahen den Lock und wurden
  von der autonomen Auswahl abgehalten.
- **Grenze:** Der Lauf simuliert getrennte Prozesse auf einem lokalen
  Dateisystem. Er ist kein Nachweis für SMB/NFS/OneDrive oder physisch getrennte
  Maschinen; dafür bleibt ein separat autorisierter Live-Readback offen.

---

### Befund 5: 144 tote Claims sperren dem MAINTAINER 23% der Projekte (2026-08-24)

- **Erfasst am:** 2026-08-24 (TASKSOLVER-Lauf, WORKSTATION-LG, Provider `codex`)
- **Fundort:** `taskplan/selector.py:393` im Zusammenspiel mit dem Feld
  `assigned_to` der Task-Datenbank.
- **Beleg (live gemessen):**
  144 offene Aufgaben tragen einen `assigned_to`-Eintrag; sie verteilen sich
  auf 80 von 350 bekannten Projekten. Inhaber sind `tasksolver-codex` (137),
  `codex` (6) und `antigravity` (1).
- **Mechanik:** `selector.py:393` markiert ein Projekt als `busy`, sobald
  *irgendeine* seiner Aufgaben `status == "active"` **oder** ein nichtleeres
  `assigned_to` hat. Der MAINTAINER meidet `busy`-Projekte vollständig. Damit
  sind derzeit 23% der Projektlandschaft für ihn unerreichbar.
- **Asymmetrie, die es unauffällig macht:** Der TASKSOLVER filtert
  `assigned_to` **nicht** — geclaimte Aufgaben werden ihm weiterhin geliefert.
  Ein liegengebliebener Claim fällt deshalb im Solver-Betrieb nie auf; er wirkt
  ausschliesslich als stille Sperre gegen eine *andere* Rolle.
- **Herkunft:** Der Rollen-Prompt schreibt das Claimen vor (`assign <id> <name>`),
  kennt aber keinen Schritt, der einen Claim bei Abbruch, Blockade oder
  Kontextverlust wieder loest. Jeder abgebrochene Lauf hinterlaesst daher eine
  Karteileiche.
- **Teilweise adressiert:** Seit dem Revolver (`790290b`) loest
  `taskplan skip --task <ID>` den Claim der zurueckgestellten Aufgabe mit und
  benennt den bisherigen Inhaber. Das deckt den geordneten Rueckzug ab — nicht
  aber den Abbruch, bei dem gar kein Befehl mehr laeuft.
- **Nicht behoben, bewusst:** Die 144 Bestandsclaims wurden NICHT gesammelt
  geloest. Ein Claim ist eine Aussage ueber fremde Arbeit; ihn ohne Auftrag
  massenhaft zu entfernen, waere derselbe Fehler in die andere Richtung.
- **Naechster Schritt:** Owner-Entscheidung noetig, ob (a) Claims ein
  Ablaufdatum bekommen (analog zum 24h-Verfall der LOCK-Dateien), (b) der
  Selektor nur `status == "active"` als `busy` liest und `assigned_to`
  ignoriert, oder (c) der Bestand einmalig nach Alter bereinigt wird.


## Befund 6 — Aufgaben zeigen auf nicht registrierte Arbeitsklone

**Beobachtet 2026-08-24, zwei belegte Faelle, unbehoben.**

> **Nachtrag 2026-08-24, 04:45 — der urspruengliche Befund war zu breit.**
> Er nannte #2097 und #2098 als "zwei Faelle in Folge". Die Pruefung von #2098
> widerlegt das: Dort ist der Suffix-Klon der auf diesem Host **registrierte**
> Pfad — `.SYNC/workstation/repos.json` fuehrt ihn, und ein Verzeichnis
> `C:\_Local_DEV\repos\DokuReader` existiert hier gar nicht (nur der
> Laptop-Slot kennt es). Ein Suffix im Namen ist also kein Beleg; entscheidend
> ist allein, was die Registry des jeweiligen Hosts fuehrt.
> Belegt bleibt der Befund fuer #2097. Das vermutete Muster stand damit
> zunaechst auf einem einzigen Fall — zu wenig fuer eine Regel.
>
> **Zweiter Nachtrag 2026-08-24, 05:35:** Der StreamingGuide-Fall weiter unten
> liefert den zweiten Beleg, und zwar einen deutlich staerkeren. Das Muster
> traegt jetzt — praeziser gefasst als anfangs: Es geht nicht um Suffixe im
> Namen, sondern um Klone, die kein Mensch mehr zuordnen kann.

Der Selektor lieferte zu #2097 (CareCenter-for-Codex) als `project_path` den
Klon `CareCenter-for-Codex-tasksolver-1765-1766-1761`. Kanonisch ist laut
`.SYNC/*/repos.json` auf **beiden** Hosts `C:\_Local_DEV\repos\CareCenter-for-Codex`;
der genannte Klon ist in keiner Registry gefuehrt.

- **Warum das schadet:** Der Suffix-Klon von #2097 stand auf einem
  Feature-Branch, vier Commits vor `origin/main` und zwei Wochen alt. Wer den
  Pfad ungeprueft uebernimmt, misst einen veralteten Stand, behebt dort etwas,
  das kanonisch schon anders aussieht, und pusht es womoeglich in einen Zweig,
  den niemand mehr liest. Bei #2097 war genau das die Gefahr: Die im Task
  genannten Testzahlen stammten aus dem Suffix-Klon und waren doppelt ueberholt.
- **Warum es unauffaellig ist:** Der Pfad existiert, enthaelt dasselbe Repo und
  laesst sich fehlerfrei betreten. Nichts schlaegt fehl — man misst nur das
  falsche Objekt. Ohne Abgleich gegen `repos.json` faellt es nicht auf.
- **Groessenordnung:** Allein zu CareCenter liegen vier Klone am Host, drei
  davon nicht registriert. Die Suffix-Namen deuten darauf hin, dass frueher
  Loop-Laeufe je Buendel einen eigenen Klon angelegt und stehen gelassen haben.
- **Herkunft, vermutet — nicht belegt:** Wahrscheinlich hat der TASKWRITER den
  Pfad aus dem Arbeitsverzeichnis des Laufs uebernommen, in dem er die Aufgabe
  erkannt hat, statt ihn gegen die Registry aufzuloesen. Geprueft wurde das
  nicht; es ist eine Vermutung, kein Befund.
- **Wirksame Gegenmassnahme im Lauf:** Vor der ersten Messung
  `.SYNC/*/repos.json` konsultieren und die Abweichung im Task vermerken —
  so geschehen bei #2097.
- **Die Registry ist hostabhaengig, und das ist keine Nebensaechlichkeit**
  (gelernt an #2098): Fuer DokuReader fuehrt der Laptop-Slot
  `C:\_Local_DEV\repos\DokuReader`, der Workstation-Slot dagegen zwei
  Suffix-Klone — und der schoene Pfad existiert auf der Workstation nicht.
  Wer den Slot des falschen Hosts liest, "korrigiert" also einen Pfad, der
  hier nie existiert hat. Massgeblich ist immer der Slot des Hosts, auf dem
  gearbeitet wird.
- **Zweiter, weit drastischerer Fall — StreamingGuide (2026-08-24, #1770/#1122):**
  Hier kippt die Groessenordnung. In `C:\_Local_DEV
epos\` liegen **zehn** Klone
  desselben Projekts, sieben davon in der Registry. Die Namen erzaehlen die
  Entstehung: `-task1770-verify-20260810`, `-task1770-clean-20260812`,
  `-task1770-fresh-20260812`, `-tasksolver-1116-1122-1123`,
  `-tasksolver-1116-1122-rerun-20260812`, `_task1770_streamingguide_clean_...`.
  Jeder Lauf hat einen neuen Klon angelegt, statt einen bestehenden zu nutzen —
  offenbar, um einen "sauberen" Ausgangsstand zu erzwingen. Welcher Stand gilt,
  ist von aussen nicht mehr erkennbar.
  Der im Task genannte Pfad zeigt hier nicht einmal auf einen Klon, sondern auf
  ein vollstaendiges Git-Repository **in OneDrive** (Remote gesetzt, Branch
  master, 118 uncommittete Aenderungen, kein Plan-D-Pointer). Genau das
  untersagt Plan D, weil synchronisierte `.git`-Objektdateien eine bekannte
  Korruptionsquelle sind.
  Damit steht der Befund nicht mehr auf einem Einzelfall. Das Muster ist:
  **Wer einen unklaren Repository-Zustand vorfindet, legt einen neuen Klon an
  statt den Zustand zu klaeren.** Das verschiebt das Problem und vervielfacht
  es — beim naechsten Lauf ist die Lage unklarer als zuvor, nicht klarer.
- **Naechster Schritt:** Owner-Entscheidung, ob (a) der TASKWRITER Pfade beim
  Erfassen gegen `repos.json` aufloest, (b) der Selektor eine Abweichung
  meldet statt sie durchzureichen, oder (c) die verwaisten Suffix-Klone
  aufgeraeumt werden. Letzteres ist MAINTAINER-Gebiet, nicht meines.
