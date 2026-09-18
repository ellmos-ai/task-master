# -*- coding: utf-8 -*-
"""Data models, canonical hashes and validation for TASKPLAN Cross-System Transit v1alpha1."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

CONTRACT_VERSION = "v1alpha1"
MERGE_POLICY_VERSION = "v1alpha1"

SYSTEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SCOPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

VALID_EVENT_KINDS = (
    "task_created",
    "categorized",
    "deferred",
    "claim_requested",
    "claim_granted",
    "claim_released",
    "completed",
    "cancelled",
    "reopened",
    "tombstoned",
    "scope_migration_started",
    "scope_migration_committed",
    "scope_migration_rolled_back",
    "conflict_resolved",
)


class TransitError(RuntimeError):
    """Base error for transit validation, projection, and merge operations."""


class IdentityCollisionError(TransitError):
    """Raised when the same logical_task_id has conflicting immutable fields."""


class ClockSkewError(TransitError):
    """Raised when clock skew prevents safe claim evaluation."""


def validate_system_id(system_id: str) -> None:
    if not isinstance(system_id, str) or not SYSTEM_ID_RE.fullmatch(system_id):
        raise TransitError(
            f"Invalid system_id {system_id!r}: must match ^[a-z0-9][a-z0-9-]{{0,62}}$"
        )


def validate_scope_id(scope_id: str) -> None:
    if not isinstance(scope_id, str) or not SCOPE_ID_RE.fullmatch(scope_id):
        raise TransitError(
            f"Invalid scope_id {scope_id!r}: must match ^[a-z0-9][a-z0-9-]{{0,62}}$"
        )


def canonical_json(data: Any) -> str:
    """Returns deterministic, compact canonical UTF-8 JSON."""
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_logical_task_id(
    shared_scope_id: str,
    project_id: str,
    source_kind: str,
    source_locator: str,
    fingerprint: str,
    source_revision: str,
    contract_version: str = CONTRACT_VERSION,
) -> str:
    """Computes logical_task_id: sha256:<digest> over canonical JSON."""
    payload = {
        "contract_version": contract_version,
        "shared_scope_id": shared_scope_id,
        "project_id": project_id,
        "source_kind": source_kind,
        "source_locator": source_locator,
        "fingerprint": fingerprint,
        "source_revision": source_revision,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_content_revision(title: str, description: str, tags: str) -> str:
    """Computes digest of content proven at creation."""
    payload = {
        "title": title.strip(),
        "description": description.strip(),
        "tags": tags.strip(),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_event_id(
    task_uid: str,
    event_kind: str,
    actor_system_id: str,
    authority_sequence: int,
    parent_event_ids: List[str] | Tuple[str, ...],
    payload_sha256: str,
    created_hlc: str,
    contract_version: str = CONTRACT_VERSION,
) -> str:
    """Computes deterministic event_id digest over event metadata and payload hash."""
    payload = {
        "task_uid": task_uid,
        "event_kind": event_kind,
        "actor_system_id": actor_system_id,
        "authority_sequence": authority_sequence,
        "parent_event_ids": sorted(list(parent_event_ids)),
        "payload_sha256": payload_sha256,
        "created_hlc": created_hlc,
        "contract_version": contract_version,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass
class SharedScope:
    scope_id: str
    project_id: str
    canonical_root_proof: str
    participant_system_ids: List[str]
    claim_authority_system_id: str
    valid_from: str
    expires_at_or_never: str = "never"
    contract_version: str = CONTRACT_VERSION
    merge_policy_version: str = MERGE_POLICY_VERSION

    def __post_init__(self) -> None:
        validate_scope_id(self.scope_id)
        validate_system_id(self.claim_authority_system_id)
        for sid in self.participant_system_ids:
            validate_system_id(sid)
        if not self.project_id:
            raise TransitError("project_id must not be empty")
        if not self.canonical_root_proof:
            raise TransitError("canonical_root_proof must not be empty")

    def is_valid_at(self, timestamp_iso: Optional[str] = None) -> bool:
        if self.expires_at_or_never == "never":
            return True
        check_time = timestamp_iso or datetime.now(timezone.utc).isoformat()
        return self.valid_from <= check_time <= self.expires_at_or_never


@dataclass
class ClaimLease:
    fencing_token: int
    not_before: str
    not_after: str
    claimant_system_id: str
    agent_id: str
    request_id: str

    def is_active_at(self, current_time_iso: str, clock_skew_seconds: int = 0) -> bool:
        # Check lease bounds with clock skew consideration
        return self.not_before <= current_time_iso <= self.not_after


@dataclass
class TransitEvent:
    event_id: str
    task_uid: str
    event_kind: str
    actor_system_id: str
    authority_sequence: int
    parent_event_ids: List[str]
    payload_json: str
    payload_sha256: str
    created_hlc: str
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.event_kind not in VALID_EVENT_KINDS:
            raise TransitError(f"Invalid event_kind {self.event_kind!r}")
        validate_system_id(self.actor_system_id)
        if not self.payload_sha256:
            self.payload_sha256 = f"sha256:{hashlib.sha256(self.payload_json.encode('utf-8')).hexdigest()}"
        if not self.event_id:
            self.event_id = compute_event_id(
                self.task_uid,
                self.event_kind,
                self.actor_system_id,
                self.authority_sequence,
                self.parent_event_ids,
                self.payload_sha256,
                self.created_hlc,
                self.contract_version,
            )

    @property
    def payload(self) -> Dict[str, Any]:
        try:
            return json.loads(self.payload_json) if self.payload_json else {}
        except Exception as err:
            raise TransitError(f"Corrupt payload_json in event {self.event_id}: {err}") from err
