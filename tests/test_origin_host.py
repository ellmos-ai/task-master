# -*- coding: utf-8 -*-
"""Schema v3: `origin_host` (Nutzerentscheidung 2B, T-20260830-167536816).

Deckt ab:
  1. Neuanlage setzt origin_host deterministisch aus dem LIVE-Hostnamen.
  2. Migration einer Bestands-DB (ohne die Spalte) ist additiv und idempotent.
  3. Bestandszeilen bleiben NULL -- Herkunft ist rueckwirkend nicht
     rekonstruierbar.
"""
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path

from taskplan.client import TaskClient


class TestOriginHostOnCreate(unittest.TestCase):
    def setUp(self):
        self.client = TaskClient(db_path=":memory:", agent_id="test")

    def test_new_task_gets_live_hostname(self):
        t = self.client.add("Neu")
        self.assertEqual(t["origin_host"], socket.gethostname().strip())

    def test_stored_row_carries_origin_host(self):
        t = self.client.add("Neu")
        got = self.client.get(t["id"])
        self.assertEqual(got["origin_host"], socket.gethostname().strip())

    def test_update_does_not_touch_origin_host(self):
        """Herkunft = Ersterzeugung -- Nachstufen darf sie nicht aendern."""
        t = self.client.add("Neu")
        self.client.update(t["id"], effort="easy")
        got = self.client.get(t["id"])
        self.assertEqual(got["origin_host"], socket.gethostname().strip())


class TestOriginHostMigration(unittest.TestCase):
    """Bestandsdatenbanken (v1/v2-Schema, keine `origin_host`-Spalte) werden
    additiv migriert -- ohne Datenverlust und idempotent."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "alt.db"
        conn = sqlite3.connect(str(self.db))
        conn.executescript("""
            CREATE TABLE rinnsal_tasks (
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
        """)
        conn.execute(
            "INSERT INTO rinnsal_tasks (title, agent_id, tags, created_at, updated_at) "
            "VALUES ('Alt', 'scanner', '', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

    def test_migration_adds_column_without_data_loss(self):
        client = TaskClient(db_path=str(self.db), agent_id="test")
        rows = client.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Alt")

    def test_migration_leaves_existing_rows_null(self):
        """Herkunft laesst sich rueckwirkend nicht rekonstruieren -- NICHT
        nachfuellen."""
        client = TaskClient(db_path=str(self.db), agent_id="test")
        self.assertIsNone(client.list()[0]["origin_host"])

    def test_migration_is_idempotent(self):
        TaskClient(db_path=str(self.db), agent_id="test")
        TaskClient(db_path=str(self.db), agent_id="test")  # zweimal -> kein Fehler
        client = TaskClient(db_path=str(self.db), agent_id="test")
        self.assertEqual(len(client.list()), 1)
        self.assertIsNone(client.list()[0]["origin_host"])

    def test_new_row_after_migration_gets_live_hostname(self):
        client = TaskClient(db_path=str(self.db), agent_id="test")
        t = client.add("Neu nach Migration")
        self.assertEqual(t["origin_host"], socket.gethostname().strip())


if __name__ == "__main__":
    unittest.main()
