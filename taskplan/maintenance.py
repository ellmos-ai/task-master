# -*- coding: utf-8 -*-
"""Fail-closed planning contract for policy-aware MAINTAINER findings.

The MAINTAINER remains an LLM workflow role.  This module does not move files,
run audits, or create tickets.  It turns already collected evidence and gate
readbacks into one deterministic plan so a prompt cannot silently weaken the
adoption, lock, reversibility, or deduplication rules.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


CLASSIFICATIONS = frozenset(
    {
        "safe_autofix",
        "needs_ticket",
        "needs_system_audit",
        "needs_user_decision",
        "informational",
    }
)


class MaintenanceInputError(ValueError):
    """Raised when a finding cannot be planned without guessing."""


@dataclass(frozen=True)
class MaintenancePlan:
    classification: str
    mutation_allowed: bool
    routes: tuple[str, ...]
    reasons: tuple[str, ...]
    fingerprint: str
    create_ticket: bool
    duplicate: bool
    policy_proposal_required: bool = False

    def as_dict(self) -> dict:
        return {
            "classification": self.classification,
            "mutation_allowed": self.mutation_allowed,
            "routes": list(self.routes),
            "reasons": list(self.reasons),
            "fingerprint": self.fingerprint,
            "create_ticket": self.create_ticket,
            "duplicate": self.duplicate,
            "policy_proposal_required": self.policy_proposal_required,
        }


def _mapping(value: object, field: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise MaintenanceInputError(f"{field} must be an object")
    return value


def _text(value: object) -> str:
    return str(value or "").strip()


def _normal(value: object) -> str:
    return " ".join(_text(value).replace("\\", "/").lower().split())


def finding_fingerprint(finding: Mapping) -> str:
    """Return a privacy-minimised, stable deduplication fingerprint."""

    policy = _mapping(finding.get("policy", {}), "policy")
    destination = _mapping(finding.get("destination", {}), "destination")
    identity = {
        "kind": _normal(finding.get("kind")),
        "locator": _normal(finding.get("locator")),
        "summary": _normal(finding.get("summary")),
        "policy_id": _normal(policy.get("id")),
        "policy_scope": _normal(policy.get("scope")),
        "destination": _normal(destination.get("path")),
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "maintainer:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finish(
    classification: str,
    reasons: list[str],
    fingerprint: str,
    existing: set[str],
) -> MaintenancePlan:
    if classification not in CLASSIFICATIONS:
        raise RuntimeError(f"unknown MAINTAINER classification: {classification}")
    mutation_allowed = classification == "safe_autofix"
    routes = {
        "safe_autofix": ("receipt",),
        "needs_ticket": ("local_finding", "ticket_master"),
        "needs_system_audit": (
            "local_finding",
            "system_auditor",
            "ticket_master",
        ),
        "needs_user_decision": ("local_finding", "ticket_master"),
        "informational": ("local_finding",),
    }[classification]
    wants_ticket = "ticket_master" in routes
    duplicate = wants_ticket and fingerprint in existing
    return MaintenancePlan(
        classification=classification,
        mutation_allowed=mutation_allowed,
        routes=routes,
        reasons=tuple(reasons),
        fingerprint=fingerprint,
        create_ticket=wants_ticket and not duplicate,
        duplicate=duplicate,
    )


def plan_finding(
    finding: Mapping,
    *,
    existing_ticket_fingerprints: Iterable[str] = (),
) -> MaintenancePlan:
    """Classify one finding without touching the target or neighbouring systems.

    The caller supplies live policy resolution, evidence and gate readbacks.
    Missing values fail closed.  A ``policy.resolution`` of ``none`` is valid
    only for evidence-based placement: the absence of a universal naming policy
    does not require inventing one, but content and provenance must prove the
    destination.
    """

    finding = _mapping(finding, "finding")
    kind = _text(finding.get("kind"))
    locator = _text(finding.get("locator"))
    summary = _text(finding.get("summary"))
    evidence = finding.get("evidence", [])
    if kind not in {"policy_violation", "placement"}:
        raise MaintenanceInputError(
            "kind must be policy_violation or placement"
        )
    if not locator or not summary:
        raise MaintenanceInputError("locator and summary are required")
    if not isinstance(evidence, list) or not any(_text(item) for item in evidence):
        raise MaintenanceInputError("at least one evidence item is required")

    policy = _mapping(finding.get("policy", {}), "policy")
    destination = _mapping(finding.get("destination", {}), "destination")
    gates = _mapping(finding.get("gates", {}), "gates")
    impact = _mapping(finding.get("impact", {}), "impact")
    fingerprint = finding_fingerprint(finding)
    existing = {_text(item) for item in existing_ticket_fingerprints if _text(item)}

    if bool(impact.get("requires_user_decision")):
        return _finish(
            "needs_user_decision",
            ["the finding requires an explicit user decision"],
            fingerprint,
            existing,
        )

    if bool(impact.get("informational")):
        return _finish(
            "informational",
            ["the evidenced observation requests no current measure"],
            fingerprint,
            existing,
        )

    if any(
        bool(impact.get(field))
        for field in ("systemwide", "cross_host", "causal_policy_conflict")
    ):
        return _finish(
            "needs_system_audit",
            ["systemwide, cross-host, or causal policy evidence needs an audit"],
            fingerprint,
            existing,
        )

    if bool(gates.get("user_lock")):
        return _finish(
            "needs_user_decision",
            ["a user lock forbids MAINTAINER mutation"],
            fingerprint,
            existing,
        )

    blocked: list[str] = []
    if bool(gates.get("hard_delete")):
        blocked.append("hard delete is forbidden; archive or recycle instead")
    if bool(gates.get("foreign_lock")):
        blocked.append("foreign lock blocks mutation")
    for field, reason in (
        ("authorized", "mutation is not explicitly authorized"),
        ("reversible", "rollback is not proven"),
        ("symlink_safe", "symlink or reparse-point safety is unproven"),
        ("cloud_safe", "cloud placeholder safety is unproven"),
        ("dirty_git_safe", "dirty Git ownership is unproven"),
        ("secret_safe", "secret handling is unproven"),
    ):
        if gates.get(field) is not True:
            blocked.append(reason)
    if blocked:
        return _finish("needs_ticket", blocked, fingerprint, existing)

    resolution = _text(policy.get("resolution"))
    if resolution not in {"resolved", "none"}:
        return _finish(
            "needs_ticket",
            [f"policy resolution is {resolution or 'missing'}"],
            fingerprint,
            existing,
        )

    if resolution == "resolved":
        missing = [
            field for field in ("id", "source", "scope") if not _text(policy.get(field))
        ]
        if missing:
            return _finish(
                "needs_ticket",
                ["resolved policy lacks " + ", ".join(missing)],
                fingerprint,
                existing,
            )
        if policy.get("adoption") != "explicit" or policy.get("valid") is not True:
            return _finish(
                "needs_ticket",
                ["policy adoption or validity is not explicit"],
                fingerprint,
                existing,
            )

    if kind == "policy_violation" and resolution != "resolved":
        return _finish(
            "needs_ticket",
            ["a policy violation requires an adopted, valid canonical policy"],
            fingerprint,
            existing,
        )

    if not _text(destination.get("path")):
        return _finish(
            "needs_ticket",
            ["the destination is not proven"],
            fingerprint,
            existing,
        )
    if kind == "placement" and (
        not _text(destination.get("content_evidence"))
        or not _text(destination.get("provenance"))
    ):
        return _finish(
            "needs_ticket",
            ["content and provenance do not prove the destination"],
            fingerprint,
            existing,
        )

    reasons = ["all mutation gates are proven"]
    if kind == "placement" and resolution == "none":
        reasons.append(
            "empirical content and provenance suffice; no universal policy is invented"
        )
    else:
        reasons.append("the canonical policy is valid and explicitly adopted")
    return _finish("safe_autofix", reasons, fingerprint, existing)


def load_fingerprints(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        payload = payload.get("fingerprints", [])
    if not isinstance(payload, list):
        raise MaintenanceInputError("fingerprints file must contain a JSON list")
    return {_text(item) for item in payload if _text(item)}
