# -*- coding: utf-8 -*-
"""Database schema and migrations for TASKPLAN Cross-System Transit."""
from __future__ import annotations

import sqlite3
from typing import Dict

SCHEMA_TRANSIT_COLUMNS: Dict[str, str] = {
    "task_uid": "TEXT",
    "logical_task_id": "TEXT",
    "transit_scope": "TEXT DEFAULT ''",
    "origin_system_id": "TEXT DEFAULT ''",
    "task_authority_system_id": "TEXT DEFAULT ''",
    "project_id": "TEXT DEFAULT ''",
    "content_revision": "TEXT DEFAULT ''",
    "transit_contract_version": "TEXT DEFAULT ''",
    "terminal_conflict": "INTEGER DEFAULT 0",
    "identity_collision": "INTEGER DEFAULT 0",
}

TRANSIT_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_tasks_task_uid ON rinnsal_tasks(task_uid);
CREATE INDEX IF NOT EXISTS idx_tasks_logical_id ON rinnsal_tasks(logical_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_transit_scope ON rinnsal_tasks(transit_scope);
"""

TRANSIT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS taskplan_transit_events (
    event_id TEXT PRIMARY KEY,
    task_uid TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    actor_system_id TEXT NOT NULL,
    authority_sequence INTEGER NOT NULL,
    parent_event_ids TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    payload_sha256 TEXT NOT NULL,
    created_hlc TEXT NOT NULL,
    contract_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transit_events_task ON taskplan_transit_events(task_uid);
CREATE INDEX IF NOT EXISTS idx_transit_events_seq ON taskplan_transit_events(task_uid, authority_sequence);

CREATE TABLE IF NOT EXISTS taskplan_shared_scopes (
    scope_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    canonical_root_proof TEXT NOT NULL,
    participant_system_ids TEXT NOT NULL,
    claim_authority_system_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    merge_policy_version TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    expires_at_or_never TEXT NOT NULL DEFAULT 'never'
);

CREATE TABLE IF NOT EXISTS taskplan_scope_subscriptions (
    scope_id TEXT PRIMARY KEY,
    subscribed_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS taskplan_scope_migrations (
    migration_id TEXT PRIMARY KEY,
    task_id INTEGER,
    task_uid TEXT,
    from_scope TEXT NOT NULL,
    to_scope TEXT NOT NULL,
    started_at TEXT NOT NULL,
    committed_at TEXT,
    status TEXT NOT NULL DEFAULT 'started'
);
"""


def ensure_transit_schema(conn: sqlite3.Connection) -> None:
    """Applies transit columns and tables additively and idempotently."""
    conn.executescript(TRANSIT_SCHEMA_SQL)
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "rinnsal_tasks" in existing_tables:
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(rinnsal_tasks)")
        }
        for column, definition in SCHEMA_TRANSIT_COLUMNS.items():
            if column not in existing_cols:
                conn.execute(f"ALTER TABLE rinnsal_tasks ADD COLUMN {column} {definition}")
        conn.executescript(TRANSIT_INDEXES_SQL)
