# -*- coding: utf-8 -*-
"""Scope migration protocols: local -> shared (Section 7.1) and shared -> local (Section 7.2)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .models import (
    CONTRACT_VERSION,
    TransitError,
    TransitEvent,
    compute_content_revision,
    compute_logical_task_id,
)
from .projection import TaskplanTransitProjection
from .registry import SharedScopeRegistry
from .schema import ensure_transit_schema


def migrate_local_to_shared(
    conn: sqlite3.Connection,
    task_id: int,
    scope_id: str,
    own_system_id: str,
    source_kind: str = "manual",
    source_locator: str = "",
    fingerprint: str = "",
    source_revision: str = "v1",
    dry_run_projection: bool = True,
    simulate_crash_before_commit: bool = False,
) -> Tuple[str, str]:
    """Performs the 5-step local -> shared migration per Section 7.1.

    Returns:
        (task_uid, logical_task_id)
    """
    ensure_transit_schema(conn)
    conn.row_factory = sqlite3.Row

    # Step 1: Verify Scope and Root-Proof and Authority
    registry = SharedScopeRegistry(conn, own_system_id)
    valid, reason = registry.validate_scope(scope_id)
    if not valid:
        raise TransitError(f"Cannot migrate task {task_id}: scope '{scope_id}' invalid ({reason})")

    scope = registry.get_scope(scope_id)
    if not scope:
        raise TransitError(f"Scope '{scope_id}' not found")

    row = conn.execute(
        "SELECT id, title, description, tags, project_path, effort, priority, scope FROM rinnsal_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if not row:
        raise TransitError(f"Task with id {task_id} not found")

    title = row["title"] or ""
    description = row["description"] or ""
    tags = row["tags"] or ""
    project_path = row["project_path"] or ""
    effort = row["effort"] or ""
    priority = row["priority"] or "medium"
    scope_val = row["scope"] or "local"

    from .projection import scan_for_secrets
    findings = scan_for_secrets(f"{title} {description} {tags}")
    if findings:
        raise TransitError(f"Secret detected in task freetext: {', '.join(findings)}")

    # Step 2: Deterministically derive identity fields
    if not fingerprint:
        fingerprint = f"{scope.project_id}:{title.strip().lower()}"
    if not source_locator:
        source_locator = f"task:{task_id}"

    logical_task_id = compute_logical_task_id(
        shared_scope_id=scope_id,
        project_id=scope.project_id,
        source_kind=source_kind,
        source_locator=source_locator,
        fingerprint=fingerprint,
        source_revision=source_revision,
        contract_version=CONTRACT_VERSION,
    )
    content_revision = compute_content_revision(title, description, tags)
    task_uid = str(uuid.uuid4())
    migration_id = f"mig:{task_id}:{logical_task_id}"
    now = datetime.now(timezone.utc).isoformat()

    # Step 3: Write migration started record and transit events inside local transaction
    # Check if a committed migration already exists for this task
    existing_mig = conn.execute(
        "SELECT status, task_uid FROM taskplan_scope_migrations WHERE migration_id = ?",
        (migration_id,),
    ).fetchone()
    if existing_mig and existing_mig["status"] == "committed":
        return existing_mig["task_uid"], logical_task_id

    conn.execute(
        """
        INSERT INTO taskplan_scope_migrations (
            migration_id, task_id, task_uid, from_scope, to_scope, started_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, 'started')
        ON CONFLICT(migration_id) DO UPDATE SET
            status = 'started',
            started_at = excluded.started_at
        """,
        (migration_id, task_id, task_uid, f"local:{own_system_id}", f"shared:{scope_id}", now),
    )

    # Prepare creation event payload
    created_payload = {
        "logical_task_id": logical_task_id,
        "project_id": scope.project_id,
        "source_kind": source_kind,
        "source_locator": source_locator,
        "fingerprint": fingerprint,
        "source_revision": source_revision,
        "transit_scope": f"shared:{scope_id}",
        "origin_system_id": own_system_id,
        "task_authority_system_id": scope.claim_authority_system_id,
        "content_revision": content_revision,
        "title": title,
        "description": description,
        "tags": tags,
        "effort": effort,
        "priority": priority,
        "scope": scope_val,
        "project_path": project_path,
        "created_at": now,
    }
    payload_json = json.dumps(created_payload, sort_keys=True)

    ev_create = TransitEvent(
        event_id="",
        task_uid=task_uid,
        event_kind="task_created",
        actor_system_id=own_system_id,
        authority_sequence=1,
        parent_event_ids=[],
        payload_json=payload_json,
        payload_sha256="",
        created_hlc=now,
        contract_version=CONTRACT_VERSION,
    )

    ev_mig_start = TransitEvent(
        event_id="",
        task_uid=task_uid,
        event_kind="scope_migration_started",
        actor_system_id=own_system_id,
        authority_sequence=2,
        parent_event_ids=[ev_create.event_id],
        payload_json=json.dumps({"migration_id": migration_id}),
        payload_sha256="",
        created_hlc=now,
        contract_version=CONTRACT_VERSION,
    )

    for ev in (ev_create, ev_mig_start):
        conn.execute(
            """
            INSERT OR IGNORE INTO taskplan_transit_events (
                event_id, task_uid, event_kind, actor_system_id, authority_sequence,
                parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.event_id,
                ev.task_uid,
                ev.event_kind,
                ev.actor_system_id,
                ev.authority_sequence,
                json.dumps(ev.parent_event_ids),
                ev.payload_json,
                ev.payload_sha256,
                ev.created_hlc,
                ev.contract_version,
            ),
        )

    # Update task row to shared scope
    conn.execute(
        """
        UPDATE rinnsal_tasks SET
            task_uid = ?,
            logical_task_id = ?,
            transit_scope = ?,
            origin_system_id = ?,
            task_authority_system_id = ?,
            project_id = ?,
            content_revision = ?,
            transit_contract_version = ?
        WHERE id = ?
        """,
        (
            task_uid,
            logical_task_id,
            f"shared:{scope_id}",
            own_system_id,
            scope.claim_authority_system_id,
            scope.project_id,
            content_revision,
            CONTRACT_VERSION,
            task_id,
        ),
    )

    # Step 4: Dry-run projection check
    if dry_run_projection:
        projection_mgr = TaskplanTransitProjection(conn, own_system_id)
        # Verify negative check passes and no local tasks leak
        issues = projection_mgr.verify_projection(conn)
        if issues:
            conn.rollback()
            raise TransitError(f"Migration aborted due to projection validation issues: {issues}")

    if simulate_crash_before_commit:
        conn.rollback()
        conn.execute(
            "UPDATE rinnsal_tasks SET transit_scope = ?, task_uid = NULL, logical_task_id = NULL WHERE id = ?",
            (f"local:{own_system_id}", task_id),
        )
        conn.commit()
        raise TransitError("Simulated crash before commit: transaction rolled back")

    # Step 5: Commit migration
    ev_mig_commit = TransitEvent(
        event_id="",
        task_uid=task_uid,
        event_kind="scope_migration_committed",
        actor_system_id=own_system_id,
        authority_sequence=3,
        parent_event_ids=[ev_mig_start.event_id],
        payload_json=json.dumps({"migration_id": migration_id}),
        payload_sha256="",
        created_hlc=now,
        contract_version=CONTRACT_VERSION,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO taskplan_transit_events (
            event_id, task_uid, event_kind, actor_system_id, authority_sequence,
            parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ev_mig_commit.event_id,
            ev_mig_commit.task_uid,
            ev_mig_commit.event_kind,
            ev_mig_commit.actor_system_id,
            ev_mig_commit.authority_sequence,
            json.dumps(ev_mig_commit.parent_event_ids),
            ev_mig_commit.payload_json,
            ev_mig_commit.payload_sha256,
            ev_mig_commit.created_hlc,
            ev_mig_commit.contract_version,
        ),
    )
    conn.execute(
        "UPDATE taskplan_scope_migrations SET status='committed', committed_at=? WHERE migration_id = ?",
        (now, migration_id),
    )
    conn.commit()
    return task_uid, logical_task_id


def migrate_shared_to_local(
    conn: sqlite3.Connection,
    task_uid: str,
    own_system_id: str,
) -> int:
    """Enforces Section 7.2 shared -> local rule.

    A shared task CANNOT be unilaterally moved back to local while active or open.
    The task authority must first close or tombstone the shared identity.
    After that, a new local task with a new identity and `migrated_from` link can be created.
    """
    ensure_transit_schema(conn)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT id, title, description, tags, status, transit_scope FROM rinnsal_tasks WHERE task_uid = ?",
        (task_uid,),
    ).fetchone()
    if not row:
        raise TransitError(f"Shared task {task_uid} not found")

    status = row["status"]
    if status not in ("done", "completed", "cancelled", "tombstoned"):
        raise TransitError(
            f"Unilateral shared->local migration blocked: task {task_uid} has non-terminal status '{status}'. "
            "Authority must close or tombstone the shared task first."
        )

    # Authority has closed/tombstoned the task. Now safe to create a new local task.
    now = datetime.now(timezone.utc).isoformat()
    old_tags = row["tags"] or ""
    new_tags = f"{old_tags};migrated_from={task_uid}".strip(";")

    cur = conn.execute(
        """
        INSERT INTO rinnsal_tasks (
            title, description, status, priority, agent_id, tags,
            created_at, updated_at, transit_scope, origin_system_id
        ) VALUES (?, ?, 'open', 'medium', 'default', ?, ?, ?, ?, ?)
        """,
        (
            row["title"],
            row["description"],
            new_tags,
            now,
            now,
            f"local:{own_system_id}",
            own_system_id,
        ),
    )
    conn.commit()
    return cur.lastrowid
