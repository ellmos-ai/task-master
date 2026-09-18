# -*- coding: utf-8 -*-
"""SharedScopeRegistry adapter for managing and verifying shared project scopes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .models import (
    CONTRACT_VERSION,
    MERGE_POLICY_VERSION,
    SharedScope,
    TransitError,
)
from .schema import ensure_transit_schema


class SharedScopeRegistry:
    """Manages registered shared scopes and host subscriptions in SQLite."""

    def __init__(self, conn: sqlite3.Connection, own_system_id: str) -> None:
        self.conn = conn
        self.own_system_id = own_system_id
        ensure_transit_schema(self.conn)

    def register_scope(self, scope: SharedScope, subscribe: bool = True) -> None:
        """Registers or updates a shared scope definition."""
        self.conn.execute(
            """
            INSERT INTO taskplan_shared_scopes (
                scope_id, project_id, canonical_root_proof, participant_system_ids,
                claim_authority_system_id, contract_version, merge_policy_version,
                valid_from, expires_at_or_never
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_id) DO UPDATE SET
                project_id=excluded.project_id,
                canonical_root_proof=excluded.canonical_root_proof,
                participant_system_ids=excluded.participant_system_ids,
                claim_authority_system_id=excluded.claim_authority_system_id,
                contract_version=excluded.contract_version,
                merge_policy_version=excluded.merge_policy_version,
                valid_from=excluded.valid_from,
                expires_at_or_never=excluded.expires_at_or_never
            """,
            (
                scope.scope_id,
                scope.project_id,
                scope.canonical_root_proof,
                json.dumps(scope.participant_system_ids),
                scope.claim_authority_system_id,
                scope.contract_version,
                scope.merge_policy_version,
                scope.valid_from,
                scope.expires_at_or_never,
            ),
        )
        if subscribe:
            self.subscribe(scope.scope_id)
        self.conn.commit()

    def get_scope(self, scope_id: str) -> Optional[SharedScope]:
        row = self.conn.execute(
            """
            SELECT scope_id, project_id, canonical_root_proof, participant_system_ids,
                   claim_authority_system_id, contract_version, merge_policy_version,
                   valid_from, expires_at_or_never
            FROM taskplan_shared_scopes
            WHERE scope_id = ?
            """,
            (scope_id,),
        ).fetchone()
        if not row:
            return None
        return SharedScope(
            scope_id=row[0],
            project_id=row[1],
            canonical_root_proof=row[2],
            participant_system_ids=json.loads(row[3]),
            claim_authority_system_id=row[4],
            contract_version=row[5],
            merge_policy_version=row[6],
            valid_from=row[7],
            expires_at_or_never=row[8],
        )

    def list_scopes(self) -> List[SharedScope]:
        rows = self.conn.execute(
            """
            SELECT scope_id, project_id, canonical_root_proof, participant_system_ids,
                   claim_authority_system_id, contract_version, merge_policy_version,
                   valid_from, expires_at_or_never
            FROM taskplan_shared_scopes
            ORDER BY scope_id
            """
        ).fetchall()
        return [
            SharedScope(
                scope_id=row[0],
                project_id=row[1],
                canonical_root_proof=row[2],
                participant_system_ids=json.loads(row[3]),
                claim_authority_system_id=row[4],
                contract_version=row[5],
                merge_policy_version=row[6],
                valid_from=row[7],
                expires_at_or_never=row[8],
            )
            for row in rows
        ]

    def subscribe(self, scope_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO taskplan_scope_subscriptions (scope_id, subscribed_at, active)
            VALUES (?, ?, 1)
            ON CONFLICT(scope_id) DO UPDATE SET active=1
            """,
            (scope_id, now),
        )
        self.conn.commit()

    def unsubscribe(self, scope_id: str) -> None:
        self.conn.execute(
            "UPDATE taskplan_scope_subscriptions SET active=0 WHERE scope_id = ?",
            (scope_id,),
        )
        self.conn.commit()

    def is_subscribed(self, scope_id: str) -> bool:
        row = self.conn.execute(
            "SELECT active FROM taskplan_scope_subscriptions WHERE scope_id = ?",
            (scope_id,),
        ).fetchone()
        return bool(row and row[0] == 1)

    def validate_scope(
        self, scope_id: str, current_time_iso: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Validates that a scope is registered, unexpired, supported, and subscribed."""
        scope = self.get_scope(scope_id)
        if not scope:
            return False, f"Unknown scope '{scope_id}'"
        if not scope.is_valid_at(current_time_iso):
            return False, f"Scope '{scope_id}' has expired or is not yet valid"
        if scope.contract_version != CONTRACT_VERSION:
            return False, f"Unsupported contract_version '{scope.contract_version}' in scope '{scope_id}'"
        if scope.merge_policy_version != MERGE_POLICY_VERSION:
            return False, f"Unsupported merge_policy_version '{scope.merge_policy_version}' in scope '{scope_id}'"
        if self.own_system_id not in scope.participant_system_ids:
            return False, f"System '{self.own_system_id}' is not a registered participant in scope '{scope_id}'"
        if not self.is_subscribed(scope_id):
            return False, f"Scope '{scope_id}' is not subscribed locally"
        return True, "valid"
