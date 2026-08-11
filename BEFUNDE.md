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
