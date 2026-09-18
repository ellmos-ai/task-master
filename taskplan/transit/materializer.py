# -*- coding: utf-8 -*-
"""SharedTaskMaterializer: Folds transit events into materialized task rows in SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CONTRACT_VERSION,
    ClockSkewError,
    IdentityCollisionError,
    TransitError,
    TransitEvent,
)
from .schema import ensure_transit_schema


class SharedTaskMaterializer:
    """Materializes append-only transit events into the local `rinnsal_tasks` table."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        own_system_id: str,
        max_clock_skew_seconds: int = 120,
    ) -> None:
        self.conn = conn
        self.own_system_id = own_system_id
        self.max_clock_skew_seconds = max_clock_skew_seconds
        ensure_transit_schema(self.conn)

    def record_event(self, event: TransitEvent) -> bool:
        """Stores a transit event append-only.

        Returns True if newly inserted, False if identical already exists.
        Raises TransitError on digest or hash mismatch.
        """
        row = self.conn.execute(
            "SELECT payload_sha256, payload_json FROM taskplan_transit_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        if row:
            if row[0] != event.payload_sha256:
                raise TransitError(
                    f"Digest collision for event {event.event_id}: payload SHA mismatch"
                )
            return False

        self.conn.execute(
            """
            INSERT INTO taskplan_transit_events (
                event_id, task_uid, event_kind, actor_system_id, authority_sequence,
                parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.task_uid,
                event.event_kind,
                event.actor_system_id,
                event.authority_sequence,
                json.dumps(event.parent_event_ids),
                event.payload_json,
                event.payload_sha256,
                event.created_hlc,
                event.contract_version,
            ),
        )
        return True

    def get_events_for_task(self, task_uid: str) -> List[TransitEvent]:
        rows = self.conn.execute(
            """
            SELECT event_id, task_uid, event_kind, actor_system_id, authority_sequence,
                   parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version
            FROM taskplan_transit_events
            WHERE task_uid = ?
            ORDER BY authority_sequence ASC, created_hlc ASC, event_id ASC
            """,
            (task_uid,),
        ).fetchall()
        return [
            TransitEvent(
                event_id=r[0],
                task_uid=r[1],
                event_kind=r[2],
                actor_system_id=r[3],
                authority_sequence=r[4],
                parent_event_ids=json.loads(r[5]),
                payload_json=r[6],
                payload_sha256=r[7],
                created_hlc=r[8],
                contract_version=r[9],
            )
            for r in rows
        ]

    def materialize_task(
        self, task_uid: str, current_time_iso: Optional[str] = None
    ) -> Optional[int]:
        """Folds all events for `task_uid` and updates or inserts the `rinnsal_tasks` row."""
        events = self.get_events_for_task(task_uid)
        if not events:
            return None

        now = current_time_iso or datetime.now(timezone.utc).isoformat()

        # Find creation event
        created_event = next((e for e in events if e.event_kind == "task_created"), None)
        if not created_event:
            return None

        created_payload = created_event.payload
        logical_task_id = created_payload.get("logical_task_id", "")
        project_id = created_payload.get("project_id", "")
        source_kind = created_payload.get("source_kind", "")
        source_locator = created_payload.get("source_locator", "")
        fingerprint = created_payload.get("fingerprint", "")
        source_revision = created_payload.get("source_revision", "")
        transit_scope = created_payload.get("transit_scope", "")
        origin_system_id = created_payload.get("origin_system_id", "")
        task_authority_system_id = created_payload.get("task_authority_system_id", "")
        content_revision = created_payload.get("content_revision", "")

        # Check existing row by task_uid specifically
        existing_task_row = self.conn.execute(
            "SELECT id FROM rinnsal_tasks WHERE task_uid = ?",
            (task_uid,),
        ).fetchone()

        # Check if another row has the same logical_task_id with different task_uid
        colliding_row = self.conn.execute(
            "SELECT id, task_uid, project_id FROM rinnsal_tasks WHERE logical_task_id = ? AND (task_uid != ? OR task_uid IS NULL)",
            (logical_task_id, task_uid),
        ).fetchone()

        identity_collision = 0
        if colliding_row:
            identity_collision = 1
            # Also mark the colliding row with identity_collision = 1
            self.conn.execute(
                "UPDATE rinnsal_tasks SET identity_collision = 1 WHERE id = ?",
                (colliding_row[0],),
            )

        # Track state through event replay
        title = created_payload.get("title", "")
        description = created_payload.get("description", "")
        status = "open"
        priority = created_payload.get("priority", "medium")
        effort = created_payload.get("effort", "")
        scope = created_payload.get("scope", "local")
        project_path = created_payload.get("project_path", "")
        root_id = created_payload.get("root_id", "")
        source = created_payload.get("source", "")
        tags = created_payload.get("tags", "")
        created_at = created_payload.get("created_at", created_event.created_hlc)
        updated_at = created_event.created_hlc
        done_at = None
        assigned_to = ""

        # Claim lease tracking
        active_lease: Optional[Dict[str, Any]] = None
        highest_categorized_seq = created_event.authority_sequence
        terminal_conflict = 0
        terminal_events: List[TransitEvent] = []
        is_tombstoned = False

        for ev in events:
            if ev.actor_system_id != task_authority_system_id and ev.event_kind in (
                "categorized",
                "claim_granted",
                "conflict_resolved",
                "tombstoned",
            ):
                # Only the designated authority may issue these events
                continue

            if ev.event_kind == "categorized":
                if ev.authority_sequence >= highest_categorized_seq:
                    highest_categorized_seq = ev.authority_sequence
                    effort = ev.payload.get("effort", effort)
                    priority = ev.payload.get("priority", priority)
                    updated_at = ev.created_hlc

            elif ev.event_kind == "deferred":
                # Deferment event
                updated_at = ev.created_hlc

            elif ev.event_kind == "claim_granted":
                active_lease = ev.payload
                updated_at = ev.created_hlc

            elif ev.event_kind == "claim_released":
                active_lease = None
                updated_at = ev.created_hlc

            elif ev.event_kind in ("completed", "cancelled"):
                terminal_events.append(ev)
                updated_at = ev.created_hlc

            elif ev.event_kind == "reopened":
                # Must reference terminal event
                terminal_events.clear()
                terminal_conflict = 0
                status = "open"
                done_at = None
                updated_at = ev.created_hlc

            elif ev.event_kind == "tombstoned":
                is_tombstoned = True
                updated_at = ev.created_hlc

            elif ev.event_kind == "conflict_resolved":
                terminal_conflict = 0
                terminal_events = [ev]
                status = ev.payload.get("resolved_status", "done")
                done_at = ev.created_hlc
                updated_at = ev.created_hlc

        # Resolve terminal state & terminal conflict
        if is_tombstoned:
            status = "cancelled"
            done_at = updated_at
        elif len(terminal_events) > 1:
            # Check if conflicting terminal outcomes
            distinct_kinds = {e.event_kind for e in terminal_events if e.event_kind != "conflict_resolved"}
            if len(distinct_kinds) > 1:
                terminal_conflict = 1
            else:
                last_term = terminal_events[-1]
                status = "done" if last_term.event_kind == "completed" else "cancelled"
                done_at = last_term.created_hlc
        elif len(terminal_events) == 1:
            term = terminal_events[0]
            if term.event_kind == "completed":
                status = "done"
                done_at = term.created_hlc
            elif term.event_kind == "cancelled":
                status = "cancelled"
                done_at = term.created_hlc

        # If not terminal and lease is active for this node
        if status == "open" and active_lease:
            not_before = active_lease.get("not_before", "")
            not_after = active_lease.get("not_after", "")
            claimant = active_lease.get("claimant_system_id", "")
            agent = active_lease.get("agent_id", "")
            if claimant == self.own_system_id:
                if not_before <= now <= not_after:
                    status = "active"
                    assigned_to = agent
                else:
                    # Expired lease
                    status = "open"
                    assigned_to = ""
            else:
                # Held by another system
                assigned_to = f"{claimant}:{agent}"

        # Write or update rinnsal_tasks
        if existing_task_row:
            local_id = existing_task_row[0]
            self.conn.execute(
                """
                UPDATE rinnsal_tasks SET
                    title = ?, description = ?, status = ?, priority = ?,
                    effort = ?, scope = ?, project_path = ?, root_id = ?,
                    source = ?, tags = ?, updated_at = ?, done_at = ?,
                    assigned_to = ?, transit_scope = ?, origin_system_id = ?,
                    task_authority_system_id = ?, project_id = ?,
                    content_revision = ?, transit_contract_version = ?,
                    terminal_conflict = ?, identity_collision = ?
                WHERE id = ?
                """,
                (
                    title, description, status, priority,
                    effort, scope, project_path, root_id,
                    source, tags, updated_at, done_at,
                    assigned_to, transit_scope, origin_system_id,
                    task_authority_system_id, project_id,
                    content_revision, CONTRACT_VERSION,
                    terminal_conflict, identity_collision,
                    local_id,
                ),
            )
            self.conn.commit()
            return local_id
        else:
            cur = self.conn.execute(
                """
                INSERT INTO rinnsal_tasks (
                    title, description, status, priority, agent_id, tags,
                    created_at, updated_at, done_at, project_path, root_id,
                    effort, scope, source, created_by, assigned_to,
                    delegation_status, origin_host, task_uid, logical_task_id,
                    transit_scope, origin_system_id, task_authority_system_id,
                    project_id, content_revision, transit_contract_version,
                    terminal_conflict, identity_collision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title, description, status, priority, "default", tags,
                    created_at, updated_at, done_at, project_path, root_id,
                    effort, scope, source, origin_system_id, assigned_to,
                    self.own_system_id, task_uid, logical_task_id,
                    transit_scope, origin_system_id, task_authority_system_id,
                    project_id, content_revision, CONTRACT_VERSION,
                    terminal_conflict, identity_collision,
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def evaluate_claim_requests(
        self,
        task_uid: str,
        requests: List[TransitEvent],
        lease_duration_seconds: int = 300,
        current_time_iso: Optional[str] = None,
    ) -> Optional[TransitEvent]:
        """Authority evaluates competing claim requests deterministically."""
        if not requests:
            return None
        from datetime import datetime, timedelta, timezone

        now = current_time_iso or datetime.now(timezone.utc).isoformat()
        # Sort deterministically by authority_sequence then request_id
        sorted_requests = sorted(
            requests,
            key=lambda r: (r.authority_sequence, r.payload.get("request_id", "")),
        )
        winner = sorted_requests[0]

        events = self.get_events_for_task(task_uid)
        max_fencing = 0
        max_seq = 0
        for ev in events:
            if ev.authority_sequence > max_seq:
                max_seq = ev.authority_sequence
            if ev.event_kind == "claim_granted":
                fencing = ev.payload.get("fencing_token", 0)
                if fencing > max_fencing:
                    max_fencing = fencing

        base_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        not_after_dt = base_dt + timedelta(seconds=lease_duration_seconds)
        not_after = not_after_dt.isoformat().replace("+00:00", "Z")

        grant_payload = {
            "fencing_token": max_fencing + 1,
            "not_before": now,
            "not_after": not_after,
            "claimant_system_id": winner.actor_system_id,
            "agent_id": winner.payload.get("agent_id", "default"),
            "request_id": winner.payload.get("request_id", ""),
        }
        grant_ev = TransitEvent(
            event_id="",
            task_uid=task_uid,
            event_kind="claim_granted",
            actor_system_id=self.own_system_id,
            authority_sequence=max_seq + 1,
            parent_event_ids=[winner.event_id] if winner.event_id else [],
            payload_json=json.dumps(grant_payload, sort_keys=True),
            payload_sha256="",
            created_hlc=now,
            contract_version=CONTRACT_VERSION,
        )
        self.record_event(grant_ev)
        return grant_ev

    def complete_task(
        self,
        task_uid: str,
        actor_system_id: str,
        fencing_token: int,
        completed_at: Optional[str] = None,
    ) -> TransitEvent:
        """Completes a task ensuring valid fencing token."""
        events = self.get_events_for_task(task_uid)
        active_token = None
        max_seq = 0
        for ev in events:
            if ev.authority_sequence > max_seq:
                max_seq = ev.authority_sequence
            if ev.event_kind == "claim_granted":
                active_token = ev.payload.get("fencing_token")
            elif ev.event_kind in ("claim_released", "completed", "cancelled", "tombstoned"):
                active_token = None

        if active_token is None or active_token != fencing_token:
            raise TransitError(
                f"Stale or invalid fencing token: supplied {fencing_token}, active {active_token}"
            )

        now = completed_at or datetime.now(timezone.utc).isoformat()
        ev = TransitEvent(
            event_id="",
            task_uid=task_uid,
            event_kind="completed",
            actor_system_id=actor_system_id,
            authority_sequence=max_seq + 1,
            parent_event_ids=[],
            payload_json=json.dumps({"fencing_token": fencing_token, "completed_at": now}, sort_keys=True),
            payload_sha256="",
            created_hlc=now,
            contract_version=CONTRACT_VERSION,
        )
        self.record_event(ev)
        self.materialize_task(task_uid, current_time_iso=now)
        return ev
