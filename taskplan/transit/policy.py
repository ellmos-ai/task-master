# -*- coding: utf-8 -*-
"""TaskplanTransitMergePolicy: Application-specific merge policy for TASKPLAN transit snapshots."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .materializer import SharedTaskMaterializer
from .models import (
    CONTRACT_VERSION,
    MERGE_POLICY_VERSION,
    TransitError,
    TransitEvent,
    compute_event_id,
)
from .projection import scan_for_secrets
from .registry import SharedScopeRegistry
from .schema import ensure_transit_schema

logger = logging.getLogger(__name__)

try:
    from sqlite_transit_sync import MergeReport, Snapshot
except ImportError:
    @dataclass(slots=True)
    class MergeReport:
        snapshot: str
        source_node: str
        inserted: int = 0
        updated: int = 0
        unchanged: int = 0
        deleted: int = 0
        skipped_tables: list[str] = field(default_factory=list)
        tables: dict[str, dict[str, int]] = field(default_factory=dict)

        def as_dict(self) -> dict[str, Any]:
            return asdict(self)

    @dataclass(frozen=True, slots=True)
    class Snapshot:
        path: Path
        manifest_path: Path
        node_id: str
        namespace: str
        created_at: str
        sha256: str
        size: int


class TaskplanTransitMergePolicy:
    """Merge policy conforming to sqlite-transit-sync MergePolicy protocol.

    Enforces TASKPLAN domain invariants:
    1. Rejects snapshots containing forbidden local:* or empty transit_scope tasks.
    2. Scans text and payload freetext for secrets and aborts fail-closed.
    3. Merges shared scope definitions and verifies contract versions.
    4. Merges transit events append-only with byte-identical check on collision.
    5. Replays/folds affected tasks through SharedTaskMaterializer.
    """

    def __init__(
        self,
        own_system_id: str,
        max_clock_skew_seconds: int = 120,
        quarantine_on_local_leak: bool = False,
    ) -> None:
        self.own_system_id = own_system_id
        self.max_clock_skew_seconds = max_clock_skew_seconds
        self.quarantine_on_local_leak = quarantine_on_local_leak

    def merge(
        self,
        local: sqlite3.Connection,
        remote: sqlite3.Connection,
        snapshot: Snapshot,
    ) -> MergeReport:
        local.row_factory = sqlite3.Row
        remote.row_factory = sqlite3.Row
        ensure_transit_schema(local)

        report = MergeReport(
            snapshot=getattr(snapshot.path, "name", str(snapshot.path)),
            source_node=snapshot.node_id,
        )

        remote_tables = {
            r[0]
            for r in remote.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

        # 1. Privacy & Negative Verification on remote snapshot
        if "rinnsal_tasks" in remote_tables:
            tasks = remote.execute(
                "SELECT id, transit_scope, title, description, tags FROM rinnsal_tasks"
            ).fetchall()
            for t in tasks:
                ts = (t["transit_scope"] or "").strip()
                if not ts or ts.startswith("local:"):
                    if not self.quarantine_on_local_leak:
                        raise TransitError(
                            f"Remote snapshot contains forbidden non-shared task (id={t['id']}, transit_scope={ts!r})"
                        )
                # Check freetext for secrets
                text_corpus = f"{t['title'] or ''} {t['description'] or ''} {t['tags'] or ''}"
                findings = scan_for_secrets(text_corpus)
                if findings:
                    raise TransitError(
                        f"Secret detected in snapshot task freetext: {', '.join(findings)}"
                    )

        if "taskplan_transit_events" in remote_tables:
            events = remote.execute(
                "SELECT event_id, payload_json FROM taskplan_transit_events"
            ).fetchall()
            for ev in events:
                payload = ev["payload_json"] or ""
                findings = scan_for_secrets(payload)
                if findings:
                    raise TransitError(
                        f"Secret detected in snapshot event freetext: {', '.join(findings)}"
                    )

        # 2. Merge Shared Scopes
        scopes_inserted = 0
        scopes_updated = 0
        scopes_unchanged = 0
        if "taskplan_shared_scopes" in remote_tables:
            remote_scopes = remote.execute(
                """
                SELECT scope_id, project_id, canonical_root_proof, participant_system_ids,
                       claim_authority_system_id, contract_version, merge_policy_version,
                       valid_from, expires_at_or_never
                FROM taskplan_shared_scopes
                """
            ).fetchall()
            for s in remote_scopes:
                # Version check
                major_remote = s["contract_version"].split("alpha")[0].split("beta")[0].rstrip("0123456789")
                major_local = CONTRACT_VERSION.split("alpha")[0].split("beta")[0].rstrip("0123456789")
                if major_remote != major_local:
                    raise TransitError(
                        f"Incompatible contract version {s['contract_version']!r} in remote scope {s['scope_id']!r}"
                    )

                existing = local.execute(
                    "SELECT contract_version, valid_from, expires_at_or_never FROM taskplan_shared_scopes WHERE scope_id = ?",
                    (s["scope_id"],),
                ).fetchone()
                if existing is None:
                    local.execute(
                        """
                        INSERT INTO taskplan_shared_scopes (
                            scope_id, project_id, canonical_root_proof, participant_system_ids,
                            claim_authority_system_id, contract_version, merge_policy_version,
                            valid_from, expires_at_or_never
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s["scope_id"],
                            s["project_id"],
                            s["canonical_root_proof"],
                            s["participant_system_ids"],
                            s["claim_authority_system_id"],
                            s["contract_version"],
                            s["merge_policy_version"],
                            s["valid_from"],
                            s["expires_at_or_never"],
                        ),
                    )
                    scopes_inserted += 1
                else:
                    scopes_unchanged += 1

        report.tables["taskplan_shared_scopes"] = {
            "inserted": scopes_inserted,
            "updated": scopes_updated,
            "unchanged": scopes_unchanged,
        }

        # 3. Merge Transit Events
        events_inserted = 0
        events_unchanged = 0
        affected_uids: Set[str] = set()

        if "taskplan_transit_events" in remote_tables:
            remote_events = remote.execute(
                """
                SELECT event_id, task_uid, event_kind, actor_system_id, authority_sequence,
                       parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version
                FROM taskplan_transit_events
                ORDER BY authority_sequence ASC, created_hlc ASC, event_id ASC
                """
            ).fetchall()

            for ev in remote_events:
                eid = ev["event_id"]
                task_uid = ev["task_uid"]
                existing_ev = local.execute(
                    "SELECT payload_sha256, payload_json FROM taskplan_transit_events WHERE event_id = ?",
                    (eid,),
                ).fetchone()

                if existing_ev is not None:
                    # Verify byte-identical payload sha
                    if existing_ev["payload_sha256"] != ev["payload_sha256"]:
                        raise TransitError(
                            f"Digest collision or tampering for event {eid}: local payload_sha256 differs from remote"
                        )
                    events_unchanged += 1
                    report.unchanged += 1
                else:
                    # New event: verify integrity
                    expected_eid = compute_event_id(
                        task_uid=task_uid,
                        event_kind=ev["event_kind"],
                        actor_system_id=ev["actor_system_id"],
                        authority_sequence=ev["authority_sequence"],
                        parent_event_ids=json.loads(ev["parent_event_ids"]),
                        payload_sha256=ev["payload_sha256"],
                        created_hlc=ev["created_hlc"],
                        contract_version=ev["contract_version"],
                    )
                    if expected_eid != eid:
                        raise TransitError(
                            f"Integrity check failed: event_id {eid} does not match computed digest {expected_eid}"
                        )

                    local.execute(
                        """
                        INSERT INTO taskplan_transit_events (
                            event_id, task_uid, event_kind, actor_system_id, authority_sequence,
                            parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            eid,
                            task_uid,
                            ev["event_kind"],
                            ev["actor_system_id"],
                            ev["authority_sequence"],
                            ev["parent_event_ids"],
                            ev["payload_json"],
                            ev["payload_sha256"],
                            ev["created_hlc"],
                            ev["contract_version"],
                        ),
                    )
                    events_inserted += 1
                    report.inserted += 1
                    affected_uids.add(task_uid)

        report.tables["taskplan_transit_events"] = {
            "inserted": events_inserted,
            "updated": 0,
            "unchanged": events_unchanged,
        }

        # 4. Fold & Materialize affected tasks into rinnsal_tasks
        materializer = SharedTaskMaterializer(
            conn=local,
            own_system_id=self.own_system_id,
            max_clock_skew_seconds=self.max_clock_skew_seconds,
        )

        tasks_updated = 0
        tasks_inserted = 0
        for uid in affected_uids:
            ex = local.execute("SELECT id FROM rinnsal_tasks WHERE task_uid = ?", (uid,)).fetchone()
            materializer.materialize_task(uid)
            if ex:
                tasks_updated += 1
                report.updated += 1
            else:
                tasks_inserted += 1
                report.inserted += 1

        report.tables["rinnsal_tasks"] = {
            "inserted": tasks_inserted,
            "updated": tasks_updated,
            "unchanged": 0,
        }

        local.commit()
        return report
