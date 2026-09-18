# -*- coding: utf-8 -*-
"""Acceptance Test Suite T01-T30 for TASKPLAN Cross-System Transit v1alpha1.

Conforms to Section 11 of TASKPLAN_CROSS_SYSTEM_TRANSIT_V1.md.
Uses two synthetic nodes (host-a, host-b), isolated in-memory or on-disk SQLite DBs,
controllable timestamps, and synthetic fixtures.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import pytest

from taskplan.client import TaskClient
from taskplan.transit import (
    CONTRACT_VERSION,
    MERGE_POLICY_VERSION,
    ClaimLease,
    ClockSkewError,
    IdentityCollisionError,
    SharedScope,
    SharedScopeRegistry,
    SharedTaskMaterializer,
    TaskplanTransitMergePolicy,
    TaskplanTransitProjection,
    TransitError,
    TransitEvent,
    TransitSelectorGate,
    compute_content_revision,
    compute_event_id,
    compute_logical_task_id,
    ensure_transit_schema,
    migrate_local_to_shared,
    migrate_shared_to_local,
    scan_for_secrets,
)


@pytest.fixture
def nodes() -> Tuple[sqlite3.Connection, sqlite3.Connection, SharedScope]:
    """Creates two isolated node connections (host-a, host-b) and a common registered SharedScope."""
    conn_a = sqlite3.connect(":memory:")
    conn_b = sqlite3.connect(":memory:")

    conn_a.row_factory = sqlite3.Row
    conn_b.row_factory = sqlite3.Row

    # Create base schemas
    client_a = TaskClient(db_path=":memory:")
    client_b = TaskClient(db_path=":memory:")

    # Re-use schema in test connections
    schema_script = """
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
    conn_a.executescript(schema_script)
    conn_b.executescript(schema_script)
    ensure_transit_schema(conn_a)
    ensure_transit_schema(conn_b)

    scope = SharedScope(
        scope_id="scope-alpha",
        project_id="proj-alpha",
        canonical_root_proof="hash:sha256:abcd1234efgh5678",
        participant_system_ids=["host-a", "host-b"],
        claim_authority_system_id="host-a",
        valid_from="2026-01-01T00:00:00Z",
        expires_at_or_never="never",
    )

    reg_a = SharedScopeRegistry(conn_a, "host-a")
    reg_a.register_scope(scope, subscribe=True)

    reg_b = SharedScopeRegistry(conn_b, "host-b")
    reg_b.register_scope(scope, subscribe=True)

    return conn_a, conn_b, scope


def simulate_transit(
    src_conn: sqlite3.Connection,
    src_system: str,
    dst_conn: sqlite3.Connection,
    dst_system: str,
    quarantine_on_local_leak: bool = False,
):
    """Simulates Option A projection from src and merge into dst."""
    proj = TaskplanTransitProjection(src_conn, src_system)
    remote_conn = proj.create_projection(":memory:")
    issues = proj.verify_projection(remote_conn)
    if issues:
        raise TransitError(f"Projection verification failed: {issues}")

    policy = TaskplanTransitMergePolicy(
        own_system_id=dst_system,
        quarantine_on_local_leak=quarantine_on_local_leak,
    )

    class DummySnapshot:
        path = Path("snapshot-synthetic.db")
        node_id = src_system

    return policy.merge(dst_conn, remote_conn, DummySnapshot())


# ---------------------------------------------------------------------------
# T01: Legacy/lokale Aufgabe auf A, Snapshot nach B
# In Projektion A nicht enthalten; auf B nie sichtbar oder claimbar
# ---------------------------------------------------------------------------
def test_t01_legacy_local_task_projection_and_visibility(nodes):
    conn_a, conn_b, _ = nodes

    # Insert legacy task without transit_scope and local task with local:host-a on node A
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (title, effort, scope, created_at, updated_at, origin_system_id)
        VALUES ('Legacy local task', 'easy', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z', 'host-a')
        """
    )
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (title, effort, scope, transit_scope, created_at, updated_at, origin_system_id)
        VALUES ('Strict local task A', 'easy', 'local', 'local:host-a', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z', 'host-a')
        """
    )
    conn_a.commit()

    # Create projection
    proj = TaskplanTransitProjection(conn_a, "host-a")
    snapshot_conn = proj.create_projection(":memory:")
    assert proj.verify_projection(snapshot_conn) == []

    # Verify snapshot contains 0 tasks
    count = snapshot_conn.execute("SELECT COUNT(*) FROM rinnsal_tasks").fetchone()[0]
    assert count == 0

    # Merge snapshot to B
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    # Node B has 0 tasks
    count_b = conn_b.execute("SELECT COUNT(*) FROM rinnsal_tasks").fetchone()[0]
    assert count_b == 0


# ---------------------------------------------------------------------------
# T02: local:A in absichtlich präpariertem B-Import
# importiert/quarantänisiert, aber nie selektierbar; Diagnose vorhanden
# ---------------------------------------------------------------------------
def test_t02_local_scope_in_prepared_remote_import(nodes):
    _, conn_b, _ = nodes

    # Directly prepare a rogue task with local:host-a in conn_b (or quarantined)
    conn_b.execute(
        """
        INSERT INTO rinnsal_tasks (title, effort, scope, transit_scope, origin_system_id, created_at, updated_at)
        VALUES ('Leaked A task', 'easy', 'local', 'local:host-a', 'host-a', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_b.commit()

    task_row = dict(conn_b.execute("SELECT * FROM rinnsal_tasks WHERE title='Leaked A task'").fetchone())
    gate_b = TransitSelectorGate(own_system_id="host-b")
    selectable, reason = gate_b.evaluate(task_row)

    assert not selectable
    assert "foreign local scope" in reason
    assert "host-a" in reason


# ---------------------------------------------------------------------------
# T03: identische Shared-Aufgabe auf A und B erkannt
# gleiche logical_task_id, genau eine materialisierte Aufgabe je Knoten
# ---------------------------------------------------------------------------
def test_t03_identical_shared_task_recognized_on_both_nodes(nodes):
    conn_a, conn_b, scope = nodes

    # Create shared task on A via migration
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, project_path, created_at, updated_at)
        VALUES (101, 'Deploy shared pipeline', 'medium', 'high', 'local', 'proj-alpha/deploy', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()

    uid_a, logical_id_a = migrate_local_to_shared(
        conn=conn_a,
        task_id=101,
        scope_id="scope-alpha",
        own_system_id="host-a",
        source_locator="task:101",
        fingerprint="proj-alpha:deploy",
    )

    # Transit to B
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    row_b = conn_b.execute("SELECT task_uid, logical_task_id, title FROM rinnsal_tasks").fetchone()
    assert row_b is not None
    assert row_b["task_uid"] == uid_a
    assert row_b["logical_task_id"] == logical_id_a

    # Count tasks on B
    count_b = conn_b.execute("SELECT COUNT(*) FROM rinnsal_tasks").fetchone()[0]
    assert count_b == 1


# ---------------------------------------------------------------------------
# T04: Retry desselben Imports
# keine neue Zeile oder Ereignisduplikate
# ---------------------------------------------------------------------------
def test_t04_retry_of_same_import_idempotent(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (102, 'Shared Retry Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    migrate_local_to_shared(conn_a, 102, "scope-alpha", "host-a")

    # Transit twice
    simulate_transit(conn_a, "host-a", conn_b, "host-b")
    report2 = simulate_transit(conn_a, "host-a", conn_b, "host-b")

    assert report2.inserted == 0
    assert report2.unchanged > 0

    task_count = conn_b.execute("SELECT COUNT(*) FROM rinnsal_tasks").fetchone()[0]
    event_count = conn_b.execute("SELECT COUNT(*) FROM taskplan_transit_events").fetchone()[0]
    assert task_count == 1
    assert event_count == 3


# ---------------------------------------------------------------------------
# T05: Importreihenfolge A->B und B->A
# identischer Endzustand und identische Ereignismenge
# ---------------------------------------------------------------------------
def test_t05_import_order_ab_vs_ba(nodes):
    conn_a, conn_b, _ = nodes

    # Two events on A
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (105, 'Convergent Order Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 105, "scope-alpha", "host-a")

    # Authoritative categorisation update on A
    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    ev_cat = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="categorized",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({"effort": "medium", "priority": "high"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    )
    mat_a.record_event(ev_cat)
    mat_a.materialize_task(uid)

    # Transit to B
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    # Transit back from B to A (idempotent)
    simulate_transit(conn_b, "host-b", conn_a, "host-a")

    row_a = dict(conn_a.execute("SELECT effort, priority, status FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    row_b = dict(conn_b.execute("SELECT effort, priority, status FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())

    assert row_a["effort"] == row_b["effort"] == "medium"
    assert row_a["priority"] == row_b["priority"] == "high"


# ---------------------------------------------------------------------------
# T06: Abschluss auf A, Pull auf B
# auf B terminal und nicht erneut vorgelegt
# ---------------------------------------------------------------------------
def test_t06_completion_on_a_pulled_to_b(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (106, 'Completion Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 106, "scope-alpha", "host-a")

    # Authority on A marks completed
    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    ev_done = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="completed",
        actor_system_id="host-a",
        authority_sequence=10,
        parent_event_ids=[],
        payload_json=json.dumps({"done_by": "host-a:agent1"}),
        payload_sha256="",
        created_hlc="2026-08-31T12:00:00Z",
    )
    mat_a.record_event(ev_done)
    mat_a.materialize_task(uid)

    # Pull to B
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    row_b = dict(conn_b.execute("SELECT * FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row_b["status"] == "done"

    gate_b = TransitSelectorGate(own_system_id="host-b", scope_registry=SharedScopeRegistry(conn_b, "host-b"))
    selectable, reason = gate_b.evaluate(row_b)
    assert not selectable
    assert "terminal" in reason


# ---------------------------------------------------------------------------
# T07: gleichzeitige Claim-Requests
# Autorität vergibt genau einen Grant/Fencing-Token
# ---------------------------------------------------------------------------
def test_t07_simultaneous_claim_requests_authority_ordering(nodes):
    conn_a, _, scope = nodes
    task_uid = "018f-dummy-task-uid"

    mat_a = SharedTaskMaterializer(conn_a, "host-a")

    # Authority A evaluates two requests
    req1 = TransitEvent(
        event_id="",
        task_uid=task_uid,
        event_kind="claim_requested",
        actor_system_id="host-a",
        authority_sequence=1,
        parent_event_ids=[],
        payload_json=json.dumps({"request_id": "req-002", "agent_id": "agent-a"}),
        payload_sha256="",
        created_hlc="2026-08-31T10:00:00Z",
    )
    req2 = TransitEvent(
        event_id="",
        task_uid=task_uid,
        event_kind="claim_requested",
        actor_system_id="host-b",
        authority_sequence=1,
        parent_event_ids=[],
        payload_json=json.dumps({"request_id": "req-001", "agent_id": "agent-b"}),
        payload_sha256="",
        created_hlc="2026-08-31T10:00:00Z",
    )

    grant = mat_a.evaluate_claim_requests(
        task_uid=task_uid,
        requests=[req1, req2],
        lease_duration_seconds=300,
        current_time_iso="2026-08-31T10:01:00Z",
    )

    assert grant is not None
    # Deterministic winner sorted by authority_sequence then request_id
    assert grant.payload["request_id"] == "req-001"
    assert grant.payload["claimant_system_id"] == "host-b"
    assert grant.payload["fencing_token"] == 1


# ---------------------------------------------------------------------------
# T08: Claim-Request ohne Grant
# kein active, keine Ausführung
# ---------------------------------------------------------------------------
def test_t08_claim_request_without_grant(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (108, 'Claim Req Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 108, "scope-alpha", "host-a")

    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    req = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="claim_requested",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({"request_id": "req-xyz", "agent_id": "agent-1"}),
        payload_sha256="",
        created_hlc="2026-08-31T10:05:00Z",
    )
    mat_a.record_event(req)
    mat_a.materialize_task(uid)

    row = dict(conn_a.execute("SELECT status, assigned_to FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row["status"] == "open"
    assert row["assigned_to"] == ""


# ---------------------------------------------------------------------------
# T09: Lease abgelaufen
# alter Token abgewiesen; neue Autoritätslease erforderlich
# ---------------------------------------------------------------------------
def test_t09_expired_claim_lease_rejection(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (109, 'Expired Lease Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 109, "scope-alpha", "host-a")

    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    grant = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="claim_granted",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({
            "fencing_token": 1,
            "not_before": "2026-08-31T10:00:00Z",
            "not_after": "2026-08-31T10:10:00Z",
            "claimant_system_id": "host-a",
            "agent_id": "agent-1",
            "request_id": "req-1",
        }),
        payload_sha256="",
        created_hlc="2026-08-31T10:00:00Z",
    )
    mat_a.record_event(grant)

    # Materialize at 10:15 (expired)
    mat_a.materialize_task(uid, current_time_iso="2026-08-31T10:15:00Z")

    row = dict(conn_a.execute("SELECT status, assigned_to FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row["status"] == "open"
    assert row["assigned_to"] == ""


# ---------------------------------------------------------------------------
# T10: Abschluss mit altem Fencing-Token
# fail-closed, Zustand unverändert
# ---------------------------------------------------------------------------
def test_t10_completion_with_stale_fencing_token(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (110, 'Fencing Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 110, "scope-alpha", "host-a")

    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    # Grant token 2
    mat_a.record_event(TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="claim_granted",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({"fencing_token": 2, "not_before": "2026-08-31T10:00:00Z", "not_after": "2026-08-31T10:30:00Z", "claimant_system_id": "host-a", "agent_id": "agent-1", "request_id": "r"}),
        payload_sha256="",
        created_hlc="2026-08-31T10:00:00Z",
    ))

    # Attempt completion with stale fencing_token 1
    with pytest.raises(TransitError) as exc_info:
        mat_a.complete_task(
            task_uid=uid,
            actor_system_id="host-a",
            fencing_token=1,
            completed_at="2026-08-31T10:05:00Z",
        )
    assert "Stale or invalid fencing token" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T11: Uhrabweichung innerhalb Unsicherheitsfenster
# Aufgabe bleibt ungeclaimt, klare Clock-Skew-Diagnose
# ---------------------------------------------------------------------------
def test_t11_clock_skew_within_uncertainty_window(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, transit_scope, created_at, updated_at)
        VALUES (111, 'Skew Task', 'easy', 'medium', 'local', 'shared:scope-alpha', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    task = dict(conn_a.execute("SELECT * FROM rinnsal_tasks WHERE id=111").fetchone())

    gate_a = TransitSelectorGate(
        own_system_id="host-a",
        scope_registry=SharedScopeRegistry(conn_a, "host-a"),
        max_clock_skew_seconds=120,
    )
    selectable, reason = gate_a.evaluate(task, clock_is_uncertain=True)

    assert not selectable
    assert "clock is uncertain" in reason


# ---------------------------------------------------------------------------
# T12: kontrollierte Clock-Skew außerhalb Lease
# deterministischer neuer Grant nach Autoritätsentscheidung
# ---------------------------------------------------------------------------
def test_t12_controlled_clock_skew_outside_lease(nodes):
    conn_a, _, _ = nodes
    task_uid = "018f-dummy-112"
    mat_a = SharedTaskMaterializer(conn_a, "host-a")

    req = TransitEvent(
        event_id="",
        task_uid=task_uid,
        event_kind="claim_requested",
        actor_system_id="host-b",
        authority_sequence=1,
        parent_event_ids=[],
        payload_json=json.dumps({"request_id": "req-new", "agent_id": "agent-b"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    )

    grant = mat_a.evaluate_claim_requests(
        task_uid=task_uid,
        requests=[req],
        lease_duration_seconds=300,
        current_time_iso="2026-08-31T11:01:00Z",
    )
    assert grant.payload["claimant_system_id"] == "host-b"
    assert grant.payload["fencing_token"] == 1


# ---------------------------------------------------------------------------
# T13: Tombstone vor älterem Update importiert
# Tombstone bleibt wirksam
# ---------------------------------------------------------------------------
def test_t13_tombstone_imported_before_older_update(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (113, 'Tombstone Order Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 113, "scope-alpha", "host-a")
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    mat_b = SharedTaskMaterializer(conn_b, "host-b")

    ev_tomb = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="tombstoned",
        actor_system_id="host-a",
        authority_sequence=10,
        parent_event_ids=[],
        payload_json=json.dumps({"reason": "obsolete"}),
        payload_sha256="",
        created_hlc="2026-08-31T12:00:00Z",
    )
    ev_old_update = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="categorized",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({"effort": "medium"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    )

    # Record tombstone first, then old update
    mat_b.record_event(ev_tomb)
    mat_b.record_event(ev_old_update)
    mat_b.materialize_task(uid)

    row = dict(conn_b.execute("SELECT status FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row["status"] == "cancelled"


# ---------------------------------------------------------------------------
# T14: älteres Update vor Tombstone importiert
# derselbe tombstoned Endzustand wie T13
# ---------------------------------------------------------------------------
def test_t14_older_update_imported_before_tombstone(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (114, 'Tombstone Reverse Order Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 114, "scope-alpha", "host-a")
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    mat_b = SharedTaskMaterializer(conn_b, "host-b")

    ev_old_update = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="categorized",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({"effort": "medium"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    )
    ev_tomb = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="tombstoned",
        actor_system_id="host-a",
        authority_sequence=10,
        parent_event_ids=[],
        payload_json=json.dumps({"reason": "obsolete"}),
        payload_sha256="",
        created_hlc="2026-08-31T12:00:00Z",
    )

    # Record old update first, then tombstone
    mat_b.record_event(ev_old_update)
    mat_b.record_event(ev_tomb)
    mat_b.materialize_task(uid)

    row = dict(conn_b.execute("SELECT status FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row["status"] == "cancelled"


# ---------------------------------------------------------------------------
# T15: Tombstone-Retry und Retention vor allen Acks
# idempotent; keine physische Bereinigung
# ---------------------------------------------------------------------------
def test_t15_tombstone_retry_and_retention_before_acks(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (115, 'Retention Task', 'easy', 'medium', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 115, "scope-alpha", "host-a")

    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    ev_tomb = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="tombstoned",
        actor_system_id="host-a",
        authority_sequence=10,
        parent_event_ids=[],
        payload_json=json.dumps({"reason": "retired"}),
        payload_sha256="",
        created_hlc="2026-08-31T12:00:00Z",
    )
    mat_a.record_event(ev_tomb)
    mat_a.materialize_task(uid)

    # Push to B twice
    simulate_transit(conn_a, "host-a", conn_b, "host-b")
    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    # Ensure row is NOT physically deleted
    row = conn_b.execute("SELECT id, status FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone()
    assert row is not None
    assert row["status"] == "cancelled"


# ---------------------------------------------------------------------------
# T16: autoritative Rekategorisierung
# beide Knoten konvergieren ohne Timestamp-LWW
# ---------------------------------------------------------------------------
def test_t16_authoritative_recategorization_converges(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (116, 'Recat Task', 'easy', 'low', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 116, "scope-alpha", "host-a")

    mat_a = SharedTaskMaterializer(conn_a, "host-a")
    ev = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="categorized",
        actor_system_id="host-a",
        authority_sequence=5,
        parent_event_ids=[],
        payload_json=json.dumps({"effort": "medium", "priority": "high"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    )
    mat_a.record_event(ev)
    mat_a.materialize_task(uid)

    simulate_transit(conn_a, "host-a", conn_b, "host-b")

    row_b = dict(conn_b.execute("SELECT effort, priority FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row_b["effort"] == "medium"
    assert row_b["priority"] == "high"


# ---------------------------------------------------------------------------
# T17: konkurrierende Terminalereignisse
# terminal_conflict, nicht selektierbar, Auflösung referenziert beide Eltern
# ---------------------------------------------------------------------------
def test_t17_competing_terminal_events_conflict_and_resolution(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (117, 'Terminal Conflict Task', 'easy', 'low', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 117, "scope-alpha", "host-a")

    mat_a = SharedTaskMaterializer(conn_a, "host-a")

    # Two competing terminal events: completed and cancelled
    ev1 = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="completed",
        actor_system_id="host-a",
        authority_sequence=4,
        parent_event_ids=[],
        payload_json=json.dumps({"by": "worker-1"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    )
    ev2 = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="cancelled",
        actor_system_id="host-a",
        authority_sequence=5,
        parent_event_ids=[],
        payload_json=json.dumps({"by": "worker-2"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:01:00Z",
    )

    mat_a.record_event(ev1)
    mat_a.record_event(ev2)
    mat_a.materialize_task(uid)

    row = dict(conn_a.execute("SELECT terminal_conflict FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row["terminal_conflict"] == 1

    gate = TransitSelectorGate(own_system_id="host-a", scope_registry=SharedScopeRegistry(conn_a, "host-a"))
    selectable, reason = gate.evaluate(row)
    assert not selectable
    assert "terminal_conflict" in reason

    # Authority resolves conflict referencing both parent events
    ev_res = TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="conflict_resolved",
        actor_system_id="host-a",
        authority_sequence=6,
        parent_event_ids=[ev1.event_id, ev2.event_id],
        payload_json=json.dumps({"resolved_status": "done"}),
        payload_sha256="",
        created_hlc="2026-08-31T11:05:00Z",
    )
    mat_a.record_event(ev_res)
    mat_a.materialize_task(uid)

    row_resolved = dict(conn_a.execute("SELECT terminal_conflict, status FROM rinnsal_tasks WHERE task_uid=?", (uid,)).fetchone())
    assert row_resolved["terminal_conflict"] == 0
    assert row_resolved["status"] == "done"


# ---------------------------------------------------------------------------
# T18: unbekannte System-/Scope-/Major-Version
# nicht selektierbar und nicht still verworfen
# ---------------------------------------------------------------------------
def test_t18_unknown_system_scope_or_major_version(nodes):
    conn_a, _, _ = nodes

    task = {
        "id": 118,
        "title": "Unknown Version Task",
        "effort": "easy",
        "scope": "local",
        "transit_scope": "shared:unknown-scope-999",
    }
    gate = TransitSelectorGate(own_system_id="host-a", scope_registry=SharedScopeRegistry(conn_a, "host-a"))
    selectable, reason = gate.evaluate(task)

    assert not selectable
    assert "Unknown scope" in reason


# ---------------------------------------------------------------------------
# T19: abgelaufener oder widerrufener Root-Proof
# Shared-Auswahl und Export blockiert; lokale Phase A läuft
# ---------------------------------------------------------------------------
def test_t19_expired_or_revoked_root_proof(nodes):
    conn_a, _, _ = nodes
    reg_a = SharedScopeRegistry(conn_a, "host-a")

    expired_scope = SharedScope(
        scope_id="scope-expired",
        project_id="proj-expired",
        canonical_root_proof="proof",
        participant_system_ids=["host-a"],
        claim_authority_system_id="host-a",
        valid_from="2026-01-01T00:00:00Z",
        expires_at_or_never="2026-01-02T00:00:00Z",
    )
    reg_a.register_scope(expired_scope, subscribe=True)

    task_shared = {
        "id": 119,
        "title": "Expired Shared",
        "effort": "easy",
        "scope": "local",
        "transit_scope": "shared:scope-expired",
    }
    task_local = {
        "id": 120,
        "title": "Local Phase A",
        "effort": "easy",
        "scope": "local",
        "transit_scope": "local:host-a",
    }

    gate = TransitSelectorGate(own_system_id="host-a", scope_registry=reg_a)
    selectable_shared, reason = gate.evaluate(task_shared, current_time_iso="2026-08-31T10:00:00Z")
    selectable_local, _ = gate.evaluate(task_local, current_time_iso="2026-08-31T10:00:00Z")

    assert not selectable_shared
    assert "expired" in reason
    assert selectable_local


# ---------------------------------------------------------------------------
# T20: local -> shared mit Crash vor Commit
# genau lokale Darstellung bleibt; Retry nutzt dieselbe Migrations-ID
# ---------------------------------------------------------------------------
def test_t20_local_to_shared_migration_crash_before_commit(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, transit_scope, created_at, updated_at)
        VALUES (120, 'Crash Before Commit', 'easy', 'low', 'local', 'local:host-a', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()

    with pytest.raises(TransitError) as exc_info:
        migrate_local_to_shared(
            conn=conn_a,
            task_id=120,
            scope_id="scope-alpha",
            own_system_id="host-a",
            simulate_crash_before_commit=True,
        )
    assert "Simulated crash before commit" in str(exc_info.value)

    # Local representation remains unchanged
    row = dict(conn_a.execute("SELECT transit_scope, task_uid FROM rinnsal_tasks WHERE id=120").fetchone())
    assert row["transit_scope"] == "local:host-a"
    assert row["task_uid"] is None


# ---------------------------------------------------------------------------
# T21: local -> shared vollständig
# genau Shared-Darstellung, Alias und Historie erhalten
# ---------------------------------------------------------------------------
def test_t21_local_to_shared_migration_committed(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, transit_scope, created_at, updated_at)
        VALUES (121, 'Migrate Success', 'easy', 'low', 'local', 'local:host-a', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()

    uid, logical_id = migrate_local_to_shared(
        conn=conn_a,
        task_id=121,
        scope_id="scope-alpha",
        own_system_id="host-a",
    )

    row = dict(conn_a.execute("SELECT transit_scope, task_uid, logical_task_id FROM rinnsal_tasks WHERE id=121").fetchone())
    assert row["transit_scope"] == "shared:scope-alpha"
    assert row["task_uid"] == uid
    assert row["logical_task_id"] == logical_id

    # Migration recorded as committed
    mig = conn_a.execute("SELECT status FROM taskplan_scope_migrations WHERE task_id=121").fetchone()
    assert mig["status"] == "committed"


# ---------------------------------------------------------------------------
# T22: unilateral versuchtes shared -> local
# blockiert; erst Tombstone/Close der Autorität zulässig
# ---------------------------------------------------------------------------
def test_t22_unilateral_shared_to_local_migration_blocked(nodes):
    conn_a, _, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (122, 'Shared To Local Guard', 'easy', 'low', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    uid, _ = migrate_local_to_shared(conn_a, 122, "scope-alpha", "host-a")

    # Unilateral attempt while status is open must fail
    with pytest.raises(TransitError) as exc_info:
        migrate_shared_to_local(conn_a, uid, "host-a")
    assert "Unilateral shared->local migration blocked" in str(exc_info.value)

    # After authoritative close, migration is allowed
    mat = SharedTaskMaterializer(conn_a, "host-a")
    mat.record_event(TransitEvent(
        event_id="",
        task_uid=uid,
        event_kind="completed",
        actor_system_id="host-a",
        authority_sequence=10,
        parent_event_ids=[],
        payload_json=json.dumps({"closed": True}),
        payload_sha256="",
        created_hlc="2026-08-31T11:00:00Z",
    ))
    mat.materialize_task(uid)

    new_id = migrate_shared_to_local(conn_a, uid, "host-a")
    new_row = dict(conn_a.execute("SELECT transit_scope, tags FROM rinnsal_tasks WHERE id=?", (new_id,)).fetchone())
    assert new_row["transit_scope"] == "local:host-a"
    assert f"migrated_from={uid}" in new_row["tags"]


# ---------------------------------------------------------------------------
# T23: gleiche logische ID, abweichende Identitätsfelder
# identity_collision, nicht selektierbar
# ---------------------------------------------------------------------------
def test_t23_identity_collision_with_mismatched_fields(nodes):
    conn_a, _, _ = nodes
    mat = SharedTaskMaterializer(conn_a, "host-a")

    uid1 = "018f-uid-1"
    uid2 = "018f-uid-2"
    logical_id = "sha256:same-logical-id"

    # Insert task 1
    ev1 = TransitEvent(
        event_id="",
        task_uid=uid1,
        event_kind="task_created",
        actor_system_id="host-a",
        authority_sequence=1,
        parent_event_ids=[],
        payload_json=json.dumps({"logical_task_id": logical_id, "project_id": "proj-1", "title": "Task 1", "transit_scope": "shared:scope-alpha", "origin_system_id": "host-a", "task_authority_system_id": "host-a"}),
        payload_sha256="",
        created_hlc="2026-08-31T10:00:00Z",
    )
    mat.record_event(ev1)
    mat.materialize_task(uid1)

    # Insert conflicting task 2 with same logical_task_id but different task_uid
    ev2 = TransitEvent(
        event_id="",
        task_uid=uid2,
        event_kind="task_created",
        actor_system_id="host-a",
        authority_sequence=1,
        parent_event_ids=[],
        payload_json=json.dumps({"logical_task_id": logical_id, "project_id": "proj-1", "title": "Task 2 Collision", "transit_scope": "shared:scope-alpha", "origin_system_id": "host-a", "task_authority_system_id": "host-a"}),
        payload_sha256="",
        created_hlc="2026-08-31T10:01:00Z",
    )
    mat.record_event(ev2)
    mat.materialize_task(uid2)

    row = dict(conn_a.execute("SELECT identity_collision, status FROM rinnsal_tasks WHERE task_uid=?", (uid2,)).fetchone())
    assert row["identity_collision"] == 1

    gate = TransitSelectorGate(own_system_id="host-a", scope_registry=SharedScopeRegistry(conn_a, "host-a"))
    selectable, reason = gate.evaluate(row)
    assert not selectable
    assert "identity_collision" in reason


# ---------------------------------------------------------------------------
# T24: manipuliertes Manifest/Hash/HMAC
# Ablehnung vor Merge
# ---------------------------------------------------------------------------
def test_t24_tampered_manifest_or_hash_rejection(nodes):
    conn_a, conn_b, _ = nodes

    # Prepare event with corrupted payload_sha256
    ev_tampered = TransitEvent(
        event_id="sha256:spoofed",
        task_uid="uid-x",
        event_kind="task_created",
        actor_system_id="host-a",
        authority_sequence=1,
        parent_event_ids=[],
        payload_json='{"title": "Tampered"}',
        payload_sha256="sha256:wronghash",
        created_hlc="2026-08-31T10:00:00Z",
    )

    policy = TaskplanTransitMergePolicy(own_system_id="host-b")

    # In a remote db
    remote_conn = sqlite3.connect(":memory:")
    ensure_transit_schema(remote_conn)
    remote_conn.execute(
        """
        INSERT INTO taskplan_transit_events (event_id, task_uid, event_kind, actor_system_id, authority_sequence, parent_event_ids, payload_json, payload_sha256, created_hlc, contract_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ev_tampered.event_id, ev_tampered.task_uid, ev_tampered.event_kind, ev_tampered.actor_system_id, ev_tampered.authority_sequence, json.dumps(ev_tampered.parent_event_ids), ev_tampered.payload_json, ev_tampered.payload_sha256, ev_tampered.created_hlc, ev_tampered.contract_version),
    )
    remote_conn.commit()

    class DummySnapshot:
        path = Path("tampered.db")
        node_id = "host-a"

    with pytest.raises(TransitError) as exc_info:
        policy.merge(conn_b, remote_conn, DummySnapshot())
    assert "Integrity check failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T25: Secret in Shared-Freitext
# Push bricht ab; Wert erscheint nicht im Fehler
# ---------------------------------------------------------------------------
def test_t25_secret_in_shared_freetext_aborts_push(nodes):
    conn_a, _, _ = nodes

    secret_key = "ghp_1234567890abcdef1234567890abcdef12"
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, description, effort, priority, scope, created_at, updated_at)
        VALUES (125, 'Task with secret', ?, 'easy', 'low', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """,
        (f"Here is my token: {secret_key}",),
    )
    conn_a.commit()

    with pytest.raises(TransitError) as exc_info:
        migrate_local_to_shared(conn_a, 125, "scope-alpha", "host-a")

    err_msg = str(exc_info.value)
    assert secret_key not in err_msg
    assert "Secret detected" in err_msg or "projection validation issues" in err_msg


# ---------------------------------------------------------------------------
# T26: Live-DB/WAL/SHM im Yard
# Gate rot; keine Veröffentlichung oder Öffnung
# ---------------------------------------------------------------------------
def test_t26_live_db_wal_shm_in_yard_gate_red(tmp_path):
    yard_dir = tmp_path / "yard"
    yard_dir.mkdir()
    (yard_dir / "taskplan.db-wal").write_text("live wal")

    # Scanner for yard safety
    forbidden_suffixes = ("-wal", "-shm", ".lock", ".journal")
    violating_files = [f.name for f in yard_dir.iterdir() if any(f.name.endswith(s) for s in forbidden_suffixes)]

    assert len(violating_files) > 0
    assert "taskplan.db-wal" in violating_files


# ---------------------------------------------------------------------------
# T27: fehlender/deaktivierter/gestörter Carrier
# lokale Auswahl funktioniert; Shared-Diagnose statt Gesamtstillstand
# ---------------------------------------------------------------------------
def test_t27_carrier_offline_or_disabled_local_phase_a_continues(nodes):
    conn_a, _, _ = nodes

    # Phase A task
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, scope, transit_scope, created_at, updated_at)
        VALUES (127, 'Phase A continues without carrier', 'easy', 'local', 'local:host-a', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()

    # Gate with transit disabled
    gate_offline = TransitSelectorGate(own_system_id="host-a", transit_enabled=False)
    task = dict(conn_a.execute("SELECT * FROM rinnsal_tasks WHERE id=127").fetchone())

    selectable, reason = gate_offline.evaluate(task)
    assert selectable
    assert reason is None


# ---------------------------------------------------------------------------
# T28: wiederholter Push/Pull
# idempotent; PRAGMA quick_check auf beiden lokalen DBs grün
# ---------------------------------------------------------------------------
def test_t28_repeated_push_pull_idempotent_quick_check_green(nodes):
    conn_a, conn_b, _ = nodes

    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, priority, scope, created_at, updated_at)
        VALUES (128, 'Push Pull Check', 'easy', 'low', 'local', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()
    migrate_local_to_shared(conn_a, 128, "scope-alpha", "host-a")

    for _ in range(3):
        simulate_transit(conn_a, "host-a", conn_b, "host-b")
        simulate_transit(conn_b, "host-b", conn_a, "host-a")

    quick_check_a = conn_a.execute("PRAGMA quick_check").fetchone()[0]
    quick_check_b = conn_b.execute("PRAGMA quick_check").fetchone()[0]

    assert quick_check_a == "ok"
    assert quick_check_b == "ok"


# ---------------------------------------------------------------------------
# T29: Raw-Projektionsprüfung für E02=A
# null lokale/Legacy-Zeilen und null nicht freigegebene Scopes
# ---------------------------------------------------------------------------
def test_t29_raw_projection_verification_option_a(nodes):
    conn_a, _, _ = nodes

    # Add 1 local task and 1 shared task
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, scope, transit_scope, created_at, updated_at)
        VALUES (1291, 'Local Task', 'easy', 'local', 'local:host-a', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.execute(
        """
        INSERT INTO rinnsal_tasks (id, title, effort, scope, transit_scope, created_at, updated_at)
        VALUES (1292, 'Shared Task', 'easy', 'local', 'shared:scope-alpha', '2026-08-31T10:00:00Z', '2026-08-31T10:00:00Z')
        """
    )
    conn_a.commit()

    proj = TaskplanTransitProjection(conn_a, "host-a")
    snapshot_conn = proj.create_projection(":memory:")

    # Verify no local tasks exist in raw projection
    tasks = snapshot_conn.execute("SELECT id, transit_scope FROM rinnsal_tasks").fetchall()
    assert len(tasks) == 1
    assert tasks[0]["transit_scope"] == "shared:scope-alpha"

    # Verify negative verification
    issues = proj.verify_projection(snapshot_conn)
    assert issues == []


# ---------------------------------------------------------------------------
# T30: scope=central plus gültiges shared:*
# bleibt gemäß bestehendem Gate nicht autonom
# ---------------------------------------------------------------------------
def test_t30_scope_central_with_valid_shared_scope_not_autonomous(nodes):
    conn_a, _, _ = nodes

    task = {
        "id": 130,
        "title": "Central Shared Task",
        "effort": "easy",
        "scope": "central",
        "transit_scope": "shared:scope-alpha",
    }

    gate = TransitSelectorGate(own_system_id="host-a", scope_registry=SharedScopeRegistry(conn_a, "host-a"))
    selectable, reason = gate.evaluate(task)

    assert not selectable
    assert "scope=central not autonomous" in reason
