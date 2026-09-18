# -*- coding: utf-8 -*-
"""TASKPLAN Cross-System Transit v1alpha1 package."""
from __future__ import annotations

from .materializer import SharedTaskMaterializer
from .migrations import migrate_local_to_shared, migrate_shared_to_local
from .models import (
    CONTRACT_VERSION,
    MERGE_POLICY_VERSION,
    SCOPE_ID_RE,
    SYSTEM_ID_RE,
    ClaimLease,
    ClockSkewError,
    IdentityCollisionError,
    SharedScope,
    TransitError,
    TransitEvent,
    canonical_json,
    compute_content_revision,
    compute_event_id,
    compute_logical_task_id,
    validate_scope_id,
    validate_system_id,
)
from .policy import TaskplanTransitMergePolicy
from .projection import TaskplanTransitProjection, scan_for_secrets
from .registry import SharedScopeRegistry
from .schema import TRANSIT_SCHEMA_SQL, ensure_transit_schema
from .selector_gate import TransitSelectorGate

__all__ = [
    "CONTRACT_VERSION",
    "MERGE_POLICY_VERSION",
    "SYSTEM_ID_RE",
    "SCOPE_ID_RE",
    "TransitError",
    "IdentityCollisionError",
    "ClockSkewError",
    "validate_system_id",
    "validate_scope_id",
    "canonical_json",
    "compute_logical_task_id",
    "compute_content_revision",
    "compute_event_id",
    "SharedScope",
    "ClaimLease",
    "TransitEvent",
    "ensure_transit_schema",
    "TRANSIT_SCHEMA_SQL",
    "SharedScopeRegistry",
    "SharedTaskMaterializer",
    "TaskplanTransitProjection",
    "scan_for_secrets",
    "TaskplanTransitMergePolicy",
    "TransitSelectorGate",
    "migrate_local_to_shared",
    "migrate_shared_to_local",
]
