# -*- coding: utf-8 -*-
"""
TaskClient -- SQLite-basiertes Task-Management
================================================

Kanonische Implementierung des Task-Systems (extrahiert aus rinnsal/tasks,
2026-07-11). Zero external dependencies (nur stdlib).

Kompatibilitaet: Der Tabellenname bleibt `rinnsal_tasks`, damit bestehende
DBs (~/.rinnsal/rinnsal.db, ~/.rinnsal/scanner_tasks.db) und Consumer
(rinnsal-Seam, homebase hb_state_task_*, _tasks-Scanner) ohne Migration
weiterlaufen.

Default-DB-Aufloesung (nur wenn kein db_path uebergeben wird):
    1. ENV TASKPLAN_DB
    2. ENV RINNSAL_DB (Kompatibilitaet)
    3. ~/.taskplan/taskplan.db
Die Rinnsal-Fassade (rinnsal.tasks.client) injiziert stattdessen ihren
eigenen Default (~/.rinnsal/rinnsal.db bzw. Rinnsal-Config).

Author: Lukas Geiger
License: MIT
"""
import os
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from .config import configured_db_path


TASK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rinnsal_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'medium',
    agent_id TEXT NOT NULL DEFAULT 'default',
    tags TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    done_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON rinnsal_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON rinnsal_tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON rinnsal_tasks(agent_id);
"""

# Schema v2 (2026-07-14): rein additiv. Bestehende Spalten bleiben unveraendert,
# damit die Direktleser der Tabelle (ellmos-unified-gui, ellmos-homebase-mcp,
# scanner_tasks.py) unberuehrt weiterlaufen.
#
#   effort/scope      -> ohne sie ist "erst leichte, dann mittlere" nicht erzwingbar,
#                        sondern nur erbittbar (Prompt-Prosa statt Gate)
#   project_path/root -> lagen bisher nur als Freitext in `tags` und waren nicht abfragbar
#   created_by/...    -> agent_id trug DREI Bedeutungen (Anleger/Bearbeiter/Rolle) und
#                        wurde beim Zuweisen ueberschrieben; die Herkunft ging verloren
SCHEMA_V2_COLUMNS = {
    "project_path": "TEXT DEFAULT ''",
    "root_id": "TEXT DEFAULT ''",
    "effort": "TEXT DEFAULT ''",
    "scope": "TEXT DEFAULT 'local'",
    "source": "TEXT DEFAULT ''",
    "created_by": "TEXT DEFAULT ''",
    "assigned_to": "TEXT DEFAULT ''",
    "delegation_status": "TEXT DEFAULT ''",
}

SCHEMA_V2_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tasks_effort ON rinnsal_tasks(effort);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON rinnsal_tasks(project_path);
CREATE INDEX IF NOT EXISTS idx_tasks_root ON rinnsal_tasks(root_id);
"""

VALID_STATUSES = ('open', 'active', 'done', 'cancelled')
VALID_PRIORITIES = ('critical', 'high', 'medium', 'low')

# large/special sind bewusst KEINE autonom loesbaren Klassen — das Gate sitzt im
# Selektor, nicht hier. Der Client speichert nur, was gueltig ist.
VALID_EFFORTS = ('easy', 'medium', 'large', 'special')
VALID_SCOPES = ('local', 'central')

# Reihenfolge der Spalten in jedem SELECT — eine Quelle, damit _row_to_dict
# nicht stillschweigend verrutscht, wenn jemand eine Spalte ergaenzt.
_SELECT_COLUMNS = (
    "id, title, description, status, priority, agent_id, tags, "
    "created_at, updated_at, done_at, "
    "project_path, root_id, effort, scope, source, "
    "created_by, assigned_to, delegation_status"
)


def _parse_tag_string(tags: str) -> Dict[str, str]:
    """Zerlegt die alte Freitext-Konvention `pipeline=.AI;project=C:/x;source=TODO.md`.

    Nur `key=value`-Paare werden erkannt; freie Tags ohne `=` werden ignoriert.
    Der erste Treffer gewinnt, damit ein doppelter Schluessel nicht still den
    frueheren Wert ueberschreibt.
    """
    parsed: Dict[str, str] = {}
    for part in tags.split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip() and key.strip() not in parsed:
            parsed[key.strip()] = value.strip()
    return parsed


def get_default_db_path() -> str:
    """Loest den Default-Pfad zur Task-DB auf.

    Reihenfolge:
    1. ENV TASKPLAN_DB
    2. Konfigurationsdatei, `[storage] path` (siehe taskplan.config)
    3. ENV RINNSAL_DB (Kompatibilitaet mit bestehenden Rinnsal-Setups)
    4. ~/.taskplan/taskplan.db (Verzeichnis wird bei Bedarf angelegt)

    Explizit uebergebene Pfade (db_path-Parameter) haben immer Vorrang und
    laufen nicht durch diese Funktion.

    Die Konfigurationsdatei steht bewusst VOR RINNSAL_DB: Wer TASKPLAN
    ausdruecklich konfiguriert, meint das auch — eine geerbte Alt-Variable
    darf ihn nicht ueberstimmen.
    """
    env_path = os.environ.get("TASKPLAN_DB", "")
    if env_path:
        return str(Path(env_path).expanduser())

    configured = configured_db_path()
    if configured:
        return configured

    legacy = os.environ.get("RINNSAL_DB", "")
    if legacy:
        return str(Path(legacy).expanduser())

    taskplan_dir = Path.home() / ".taskplan"
    taskplan_dir.mkdir(exist_ok=True)
    return str(taskplan_dir / "taskplan.db")


def count_tasks_in(db_path: str | Path) -> Optional[int]:
    """Zaehlt die Tasks in einer DB, ohne sie anzulegen oder zu veraendern.

    Gibt None zurueck, wenn die Datei fehlt, leer ist oder keine Task-Tabelle
    hat. Oeffnet strikt read-only (`mode=ro`), damit die Pruefung selbst keine
    Datei und keine WAL-Reste erzeugt — genau so entstehen sonst
    Geisterdatenbanken.
    """
    path = Path(db_path)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
    except OSError:
        return None
    uri = "file:{}?mode=ro".format(path.as_posix())
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT count(*) FROM rinnsal_tasks").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


class TaskClient:
    """
    Task-Management Client mit eigener SQLite-Tabelle.

    Verwendung:
        client = TaskClient()  # Default: siehe get_default_db_path()
        client.add("Feature X implementieren", priority="high")
        tasks = client.list()
        client.done(1)
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        agent_id: str = "default"
    ):
        if db_path is None:
            db_path = get_default_db_path()
        self._is_memory = str(db_path) == ':memory:'
        self.db_path = db_path if self._is_memory else Path(db_path)
        self.agent_id = agent_id
        self._shared_conn = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._is_memory:
            if self._shared_conn is None:
                # check_same_thread=False: die eine geteilte Connection wird
                # auch aus Poll-Threads (Telegram/Discord on_message) genutzt.
                self._shared_conn = sqlite3.connect(
                    ':memory:', check_same_thread=False)
                self._shared_conn.execute("PRAGMA foreign_keys=ON")
            return self._shared_conn
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if not self._is_memory:
            conn.close()

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript(TASK_SCHEMA_SQL)
            self._migrate_to_v2(conn)
            conn.commit()
        finally:
            if not self._is_memory:
                self._close_conn(conn)

    def _migrate_to_v2(self, conn: sqlite3.Connection) -> None:
        """Ergaenzt die v2-Spalten. Additiv, idempotent, ohne Datenaenderung.

        Bewusst NUR Struktur: Vorhandene Zeilen werden nicht angefasst. Das
        Uebertragen der alten `tags`-Konvention in die neuen Spalten ist ein
        Schreibzugriff auf Produktionsdaten und laeuft deshalb ausschliesslich
        auf ausdruecklichen Aufruf von `backfill_from_tags()` — nie beim Oeffnen.
        """
        existing = {r[1] for r in conn.execute("PRAGMA table_info(rinnsal_tasks)")}
        for column, definition in SCHEMA_V2_COLUMNS.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE rinnsal_tasks ADD COLUMN {column} {definition}")
        conn.executescript(SCHEMA_V2_INDEXES)

    def backfill_from_tags(self) -> int:
        """Uebertraegt die alte `tags`-Konvention in die v2-Spalten.

        Alt: `pipeline=.AI;project=C:/p/x;source=TODO.md` als Freitext.
        Neu: root_id / project_path / source als abfragbare Spalten.

        `tags` bleibt unveraendert erhalten — die Direktleser der Tabelle
        (unified-gui, homebase-mcp, scanner_tasks.py) lesen es weiterhin.
        Ebenso wird `created_by` aus `agent_id` gesetzt, solange es leer ist:
        beim Anlegen war `agent_id` der Anleger.

        Returns:
            Zahl der geaenderten Zeilen.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, tags, agent_id, project_path, root_id, source, created_by "
                "FROM rinnsal_tasks").fetchall()
            changed = 0
            now = datetime.now().isoformat()
            for task_id, tags, agent_id, project_path, root_id, source, created_by in rows:
                parsed = _parse_tag_string(tags or "")
                updates = {}
                # Nie ueberschreiben, was schon gesetzt ist — der Backfill ist
                # eine Ergaenzung, keine Korrektur.
                if not root_id and parsed.get("pipeline"):
                    updates["root_id"] = parsed["pipeline"]
                if not project_path and parsed.get("project"):
                    updates["project_path"] = parsed["project"]
                if not source and parsed.get("source"):
                    updates["source"] = parsed["source"]
                if not created_by and agent_id:
                    updates["created_by"] = agent_id
                if not updates:
                    continue
                fields = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE rinnsal_tasks SET {fields}, updated_at = ? WHERE id = ?",
                    (*updates.values(), now, task_id))
                changed += 1
            conn.commit()
            return changed
        finally:
            self._close_conn(conn)

    def add(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        tags: str = "",
        effort: str = "",
        scope: str = "local",
        project_path: str = "",
        root_id: str = "",
        source: str = ""
    ) -> Dict:
        """Erstellt einen neuen Task.

        `effort` darf leer bleiben (= unklassifiziert). Der Selektor behandelt
        unklassifizierte Aufgaben konservativ, statt sie faelschlich als leicht
        einzustufen.
        """
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority muss einer von {VALID_PRIORITIES} sein")
        if effort and effort not in VALID_EFFORTS:
            raise ValueError(f"effort muss einer von {VALID_EFFORTS} sein (oder leer)")
        if scope not in VALID_SCOPES:
            raise ValueError(f"scope muss einer von {VALID_SCOPES} sein")

        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                INSERT INTO rinnsal_tasks
                    (title, description, status, priority, agent_id, tags,
                     created_at, updated_at,
                     project_path, root_id, effort, scope, source, created_by)
                VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, description, priority, self.agent_id, tags, now, now,
                  project_path, root_id, effort, scope, source, self.agent_id))
            conn.commit()
            return {
                'id': cursor.lastrowid,
                'title': title,
                'description': description,
                'status': 'open',
                'priority': priority,
                'agent_id': self.agent_id,
                'tags': tags,
                'created_at': now,
                'project_path': project_path,
                'root_id': root_id,
                'effort': effort,
                'scope': scope,
                'source': source,
                'created_by': self.agent_id,
                'assigned_to': '',
                'delegation_status': '',
            }
        finally:
            self._close_conn(conn)

    def assign(self, task_id: int, to: str, status: str = "assigned") -> bool:
        """Weist einen Task einem Ausfuehrenden zu.

        Schreibt AUSSCHLIESSLICH in `assigned_to`/`delegation_status`.
        `created_by` und `agent_id` bleiben unberuehrt — sonst ginge verloren,
        wer den Task angelegt hat (genau der Fehler des alten Wrappers, der
        beim Zuweisen `agent_id` ueberschrieb).
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE rinnsal_tasks "
                "SET assigned_to = ?, delegation_status = ?, updated_at = ? "
                "WHERE id = ?",
                (to, status, now, task_id))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    def list(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        include_done: bool = False,
        limit: Optional[int] = 50,
        effort: Optional[str] = None,
        scope: Optional[str] = None,
        project_path: Optional[str] = None,
        root_id: Optional[str] = None,
        assigned_to: Optional[str] = None
    ) -> List[Dict]:
        """Listet Tasks auf.

        Die Filter auf effort/scope/project/root sind das, was der Selektor
        braucht, um "erst alle leichten, dann die mittleren" durchzusetzen —
        ohne sie bliebe die Regel Prompt-Prosa.
        """
        conn = self._get_conn()
        try:
            conditions = []
            params: list = []

            if status:
                conditions.append("status = ?")
                params.append(status)
            elif not include_done:
                conditions.append("status NOT IN ('done', 'cancelled')")

            for column, value in (("priority", priority), ("effort", effort),
                                  ("scope", scope), ("project_path", project_path),
                                  ("root_id", root_id), ("assigned_to", assigned_to)):
                if value is not None:
                    conditions.append(f"{column} = ?")
                    params.append(value)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            limit_clause = ""
            if limit is not None:
                params.append(limit)
                limit_clause = "LIMIT ?"

            rows = conn.execute(f"""
                SELECT {_SELECT_COLUMNS}
                FROM rinnsal_tasks
                {where}
                ORDER BY
                    CASE priority
                        WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5
                    END,
                    created_at ASC
                {limit_clause}
            """, params).fetchall()

            return [self._row_to_dict(r) for r in rows]
        finally:
            self._close_conn(conn)

    def get(self, task_id: int) -> Optional[Dict]:
        """Holt einen einzelnen Task."""
        conn = self._get_conn()
        try:
            row = conn.execute(f"""
                SELECT {_SELECT_COLUMNS}
                FROM rinnsal_tasks WHERE id = ?
            """, (task_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            self._close_conn(conn)

    def done(self, task_id: int) -> bool:
        """Markiert einen Task als erledigt."""
        return self._set_status(task_id, 'done')

    def activate(self, task_id: int) -> bool:
        """Setzt einen Task auf 'active'."""
        return self._set_status(task_id, 'active')

    def cancel(self, task_id: int) -> bool:
        """Storniert einen Task."""
        return self._set_status(task_id, 'cancelled')

    def reopen(self, task_id: int) -> bool:
        """Oeffnet einen erledigten/stornierten Task erneut."""
        return self._set_status(task_id, 'open')

    def update(
        self,
        task_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        tags: Optional[str] = None,
        effort: Optional[str] = None,
        scope: Optional[str] = None,
        project_path: Optional[str] = None,
        root_id: Optional[str] = None,
        source: Optional[str] = None
    ) -> bool:
        """Aktualisiert Task-Felder.

        `effort`/`scope` sind hier NACHTRAEGLICH setzbar — und das ist keine
        Bequemlichkeit, sondern die einzige Bruecke aus einer Sackgasse:

        Ohne sie waeren unklassifizierte Altlasten dauerhaft unerreichbar. Der
        TASKSOLVER fasst sie nicht an (leerer `effort` gilt bewusst NICHT als
        leicht), und der TASKWRITER koennte sie nicht nachstufen. Ein Zustand,
        aus dem es keinen Ausweg gaebe — ausser einem direkten SQL-Eingriff, den
        die Rollen-Prompts ausdruecklich verbieten. (Aufgedeckt vom
        TASKWRITER-Loop, 2026-07-14: 8 Tasks sassen genau so fest.)

        `created_by`, `agent_id` und `assigned_to` bleiben unberuehrt —
        Nachstufen aendert die Einschaetzung, nicht die Herkunft. Wer zuweisen
        will, nimmt `assign()`.
        """
        if priority and priority not in VALID_PRIORITIES:
            raise ValueError(f"priority muss einer von {VALID_PRIORITIES} sein")
        # Das Gate darf nicht durch die Hintertuer umgehbar sein: Ein ungueltiger
        # Aufwand muss beim Nachstufen genauso scheitern wie beim Anlegen.
        if effort and effort not in VALID_EFFORTS:
            raise ValueError(f"effort muss einer von {VALID_EFFORTS} sein")
        if scope and scope not in VALID_SCOPES:
            raise ValueError(f"scope muss einer von {VALID_SCOPES} sein")

        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            fields = ["updated_at = ?"]
            params: list = [now]

            for column, value in (("title", title), ("description", description),
                                  ("priority", priority), ("tags", tags),
                                  ("effort", effort), ("scope", scope),
                                  ("project_path", project_path),
                                  ("root_id", root_id), ("source", source)):
                if value is not None:
                    fields.append(f"{column} = ?")
                    params.append(value)

            params.append(task_id)
            cursor = conn.execute(
                f"UPDATE rinnsal_tasks SET {', '.join(fields)} WHERE id = ?",
                params
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    def delete(self, task_id: int) -> bool:
        """Loescht einen Task permanent."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM rinnsal_tasks WHERE id = ?", (task_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    def count(self) -> Dict:
        """Zaehlt Tasks nach Status."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT status, COUNT(*) FROM rinnsal_tasks GROUP BY status
            """).fetchall()
            result = {s: 0 for s in VALID_STATUSES}
            for status, cnt in rows:
                result[status] = cnt
            result['total'] = sum(result.values())
            return result
        finally:
            self._close_conn(conn)

    def _set_status(self, task_id: int, status: str) -> bool:
        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            done_at = now if status == 'done' else None
            cursor = conn.execute(
                "UPDATE rinnsal_tasks SET status = ?, updated_at = ?, done_at = ? WHERE id = ?",
                (status, now, done_at, task_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    @staticmethod
    def _row_to_dict(row) -> Dict:
        """Bildet eine Zeile ab. Reihenfolge MUSS `_SELECT_COLUMNS` entsprechen."""
        return {
            'id': row[0], 'title': row[1], 'description': row[2],
            'status': row[3], 'priority': row[4], 'agent_id': row[5],
            'tags': row[6], 'created_at': row[7], 'updated_at': row[8],
            'done_at': row[9],
            'project_path': row[10], 'root_id': row[11], 'effort': row[12],
            'scope': row[13], 'source': row[14],
            'created_by': row[15], 'assigned_to': row[16],
            'delegation_status': row[17],
        }
