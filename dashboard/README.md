# TaskMaster Web-Cockpit & ATI Dashboard Export

## 1. Hintergrund & Herkunft
Dieses Verzeichnis enthält den Export des **ATI Softwareagenten-Dashboards** aus BACH (`system/gui/templates/ati.html`):
- **Datei:** [`ati-dashboard.html`](ati-dashboard.html)
- **Ursprung:** BACH Advanced Tool Integration (ATI) Cockpit.
- **Kontext:** Das ATI-Dashboard ist der historische und konzeptionelle Vorläufer von `task-master` (`taskplan`). Es kombinierte bereits früh Werkzeug-Scanning, Task-Priorisierung, Headless-Sessions, Tool-Onboarding und automatisierten Transfer zu übergeordneten Task-Systemen.

## 2. Ziel & Weiterentwicklung in `task-master`
Gemäß der ELLMOS-Architekturrichtlinie (*„Erst in ELLMOS bauen/weiterentwickeln -> dann in BACH als schlanke Seam konsumieren“*) wird das Dashboard nun in `task-master` als natives Web-Cockpit weiterentwickelt:

1. **Entkopplung von alten SQLite-Sonderwegen:**
   - Ersetzung von `/api/ati/tasks` durch die native TaskMaster-Engine (`taskplan.api`, `next_bundle()`, `Sectorized Scandir`).
2. **Effort- & Lock-Visualisierung:**
   - Visuelle Darstellung der 3-Achsen-Locks (`LOCK-SYSTEM.md`).
   - Anzeige des deterministischen Effort-Gates (Global Easy Pass vs. Medium Pass vs. Non-Autonomous).
3. **Rollen-Steuerung (TASKWRITER, TASKSOLVER, MAINTAINER):**
   - Interaktive Auslösung von Rollenläufen direkt aus dem Web-Cockpit.
4. **Tool-Discovery & Health:**
   - Anbindung an `taskplan.doctor` und `taskplan.discovery`.

## 3. Konsum durch BACH (Re-Import)
Sobald `task-master` ein eigenständiges CLI-/Web-Kommando bereitstellt (z. B. `python -m taskplan web` oder als FastAPI/Starlette-Router `taskplan.web.router`), wird BACH:
- Seine proprietären `/api/ati/*`-Routen abbauen.
- Das fertige Dashboard direkt aus dem installierten `task-master`-Paket mounten.
- Das ATI-Cockpit nahtlos als TaskMaster-Weboberfläche konsumieren.
