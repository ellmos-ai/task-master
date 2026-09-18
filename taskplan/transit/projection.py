# -*- coding: utf-8 -*-
"""TaskplanTransitProjection: Projects shared tasks into an isolated snapshot database (Option A)."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Set, Union

from .models import TransitError
from .registry import SharedScopeRegistry
from .schema import ensure_transit_schema

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),
    re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
    re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
]


def scan_for_secrets(text: str) -> List[str]:
    """Scans text for sensitive credentials/keys. Returns list of matched pattern descriptions without leaking values."""
    if not text:
        return []
    findings = []
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            findings.append(f"matched {pat.pattern[:20]}...")
    return findings


BASE_TASKS_TABLE_SQL = """
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
    done_at TEXT,
    project_path TEXT DEFAULT '',
    root_id TEXT DEFAULT '',
    effort TEXT DEFAULT '',
    scope TEXT DEFAULT 'local',
    source TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    assigned_to TEXT DEFAULT '',
    delegation_status TEXT DEFAULT '',
    origin_host TEXT
);
"""


class TaskplanTransitProjection:
    """Creates a shared-only projection database for transit export (Option A)."""

    def __init__(
        self,
        source: Union[sqlite3.Connection, SharedScopeRegistry],
        own_system_id: str,
    ) -> None:
        self.own_system_id = own_system_id
        if isinstance(source, SharedScopeRegistry):
            self.registry = source
            self.source_conn = source.conn
        else:
            self.source_conn = source
            self.registry = SharedScopeRegistry(source, own_system_id)

    def create_projection(
        self,
        target_path_or_memory: str = ":memory:",
        current_time_iso: Optional[str] = None,
    ) -> sqlite3.Connection:
        """Projects only valid shared:* tasks into a new SQLite database connection."""
        target_conn = sqlite3.connect(target_path_or_memory)
        target_conn.row_factory = sqlite3.Row
        target_conn.executescript(BASE_TASKS_TABLE_SQL)
        ensure_transit_schema(target_conn)

        self.source_conn.row_factory = sqlite3.Row
        rows = self.source_conn.execute(
            """
            SELECT id, title, description, status, priority, agent_id, tags,
                   created_at, updated_at, done_at, project_path, root_id, effort,
                   scope, source, created_by, assigned_to, delegation_status,
                   origin_host, task_uid, logical_task_id, transit_scope,
                   origin_system_id, task_authority_system_id, project_id,
                   content_revision, transit_contract_version, terminal_conflict,
                   identity_collision
            FROM rinnsal_tasks
            WHERE transit_scope LIKE 'shared:%'
            """
        ).fetchall()

        valid_task_uids: Set[str] = set()
        included_scopes: Set[str] = set()

        for r in rows:
            transit_scope = r["transit_scope"] or ""
            scope_id = transit_scope.split(":", 1)[1] if ":" in transit_scope else ""

            is_valid, reason = self.registry.validate_scope(scope_id, current_time_iso)
            if not is_valid:
                continue

            for field in ("title", "description", "tags"):
                val = r[field] or ""
                findings = scan_for_secrets(val)
                if findings:
                    raise TransitError(
                        f"Secret detected in field '{field}' of task {r['task_uid']}"
                    )

            valid_task_uids.add(r["task_uid"])
            included_scopes.add(scope_id)

            target_conn.execute(
                """
                INSERT INTO rinnsal_tasks (
                    title, description, status, priority, agent_id, tags,
                    created_at, updated_at, done_at, project_path, root_id,
                    effort, scope, source, created_by, assigned_to,
                    delegation_status, origin_host, task_uid, logical_task_id,
                    transit_scope, origin_system_id, task_authority_system_id,
                    project_id, content_revision, transit_contract_version,
                    terminal_conflict, identity_collision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["title"], r["description"], r["status"], r["priority"],
                    r["agent_id"], r["tags"], r["created_at"], r["updated_at"],
                    r["done_at"], r["project_path"], r["root_id"], r["effort"],
                    r["scope"], r["source"], r["created_by"], r["assigned_to"],
                    r["delegation_status"], r["origin_host"], r["task_uid"],
                    r["logical_task_id"], r["transit_scope"], r["origin_system_id"],
                    r["task_authority_system_id"], r["project_id"],
                    r["content_revision"], r["transit_contract_version"],
                    r["terminal_conflict"], r["identity_collision"],
                ),
            )

        if valid_task_uids:
            placeholders = ",".join("?" for _ in valid_task_uids)
            event_rows = self.source_conn.execute(
                f"""
                SELECT event_id, task_uid, event_kind, actor_system_id,
                       authority_sequence, parent_event_ids, payload_json,
                       payload_sha256, created_hlc, contract_version
                FROM taskplan_transit_events
                WHERE task_uid IN ({placeholders})
                """,
                list(valid_task_uids),
            ).fetchall()

            for ev in event_rows:
                payload_json = ev["payload_json"] or ""
                findings = scan_for_secrets(payload_json)
                if findings:
                    raise TransitError(
                        f"Secret detected in payload of event {ev['event_id']}"
                    )
                target_conn.execute(
                    """
                    INSERT INTO taskplan_transit_events (
                        event_id, task_uid, event_kind, actor_system_id,
                        authority_sequence, parent_event_ids, payload_json,
                        payload_sha256, created_hlc, contract_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev["event_id"], ev["task_uid"], ev["event_kind"],
                        ev["actor_system_id"], ev["authority_sequence"],
                        ev["parent_event_ids"], ev["payload_json"],
                        ev["payload_sha256"], ev["created_hlc"],
                        ev["contract_version"],
                    ),
                )

        for sid in included_scopes:
            scope = self.registry.get_scope(sid)
            if scope:
                target_conn.execute(
                    """
                    INSERT OR REPLACE INTO taskplan_shared_scopes (
                        scope_id, project_id, canonical_root_proof,
                        participant_system_ids, claim_authority_system_id,
                        contract_version, merge_policy_version, valid_from,
                        expires_at_or_never
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope.scope_id, scope.project_id, scope.canonical_root_proof,
                        json.dumps(scope.participant_system_ids),
                        scope.claim_authority_system_id, scope.contract_version,
                        scope.merge_policy_version, scope.valid_from,
                        scope.expires_at_or_never,
                    ),
                )

        target_conn.commit()
        return target_conn

    def verify_projection(self, target_conn: sqlite3.Connection) -> List[str]:
        """Negatively verifies projection: checks no local tasks leak, no secrets, quick_check ok."""
        issues = []
        target_conn.row_factory = sqlite3.Row

        tables = {
            r[0]
            for r in target_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

        if "rinnsal_tasks" in tables:
            invalid_tasks = target_conn.execute(
                "SELECT id, transit_scope, title, description, tags FROM rinnsal_tasks WHERE transit_scope NOT LIKE 'shared:%'"
            ).fetchall()
            for t in invalid_tasks:
                issues.append(f"Non-shared task leaked into projection: id={t['id']}, transit_scope={t['transit_scope']!r}")

            tasks = target_conn.execute("SELECT id, title, description, tags FROM rinnsal_tasks").fetchall()
            for t in tasks:
                for f in ("title", "description", "tags"):
                    if scan_for_secrets(t[f] or ""):
                        issues.append(f"Secret found in field '{f}' of task {t['id']}")

        if "taskplan_transit_events" in tables:
            events = target_conn.execute("SELECT event_id, payload_json FROM taskplan_transit_events").fetchall()
            for ev in events:
                if scan_for_secrets(ev["payload_json"] or ""):
                    issues.append(f"Secret found in event {ev['event_id']}")

        qc = target_conn.execute("PRAGMA quick_check").fetchone()
        if not qc or qc[0] != "ok":
            issues.append(f"PRAGMA quick_check failed: {qc}")

        return issues

    def project_to_file(
        self,
        source_conn: sqlite3.Connection,
        target_path: Path | str,
        current_time_iso: Optional[str] = None,
    ) -> Path:
        target = Path(target_path)
        if target.exists():
            target.unlink()
        target_conn = self.create_projection(str(target), current_time_iso)
        issues = self.verify_projection(target_conn)
        target_conn.close()
        if issues:
            if target.exists():
                target.unlink()
            raise TransitError(f"Projection verification failed: {issues}")
        return target
