# -*- coding: utf-8 -*-
"""TransitSelectorGate: Enforces Section 8 selector isolation and lifecycle gates for TASKPLAN."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .models import CONTRACT_VERSION, MERGE_POLICY_VERSION
from .registry import SharedScopeRegistry


class TransitSelectorGate:
    """Evaluates whether a task from `rinnsal_tasks` passes Section 8 selection rules."""

    def __init__(
        self,
        own_system_id: str,
        scope_registry: Optional[SharedScopeRegistry] = None,
        max_clock_skew_seconds: int = 120,
        transit_enabled: bool = True,
    ) -> None:
        self.own_system_id = own_system_id
        self.scope_registry = scope_registry
        self.max_clock_skew_seconds = max_clock_skew_seconds
        self.transit_enabled = transit_enabled

    def evaluate(
        self,
        task: Dict[str, Any],
        current_time_iso: Optional[str] = None,
        clock_is_uncertain: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Evaluates whether `task` is selectable on this system.

        Returns:
            (is_selectable: bool, rejection_reason: Optional[str])
        """
        # When transit is disabled or absent, Phase A rules apply unmodified
        if not self.transit_enabled:
            return True, None

        # 1. Existing local/central scope guard: scope=central remains non-autonomous
        if task.get("scope", "local") == "central":
            return False, "scope=central not autonomous"

        # 2. Identity integrity: identity_collision blocks selection fail-closed
        if task.get("identity_collision", 0):
            return False, "identity_collision detected"

        # 3. Lifecycle checks: terminal state, terminal conflict, or tombstoned
        if task.get("terminal_conflict", 0):
            return False, "terminal_conflict detected"

        status = task.get("status", "open")
        if status in ("done", "completed", "cancelled", "tombstoned"):
            return False, f"task lifecycle is terminal ({status})"

        # 4. Distribution scope (transit_scope) isolation
        transit_scope = (task.get("transit_scope") or "").strip()

        if not transit_scope:
            # Legacy or unmigrated row: strictly local:<own-system-id>
            origin_sys = (task.get("origin_system_id") or "").strip()
            if origin_sys and origin_sys != self.own_system_id:
                return (
                    False,
                    f"legacy row with foreign origin_system_id '{origin_sys}' not selectable",
                )
            # Local legacy row is selectable on own host
            return True, None

        if transit_scope.startswith("local:"):
            loc_sys = transit_scope.split(":", 1)[1].strip()
            if loc_sys != self.own_system_id:
                return (
                    False,
                    f"foreign local scope '{transit_scope}' is strictly invisible and unclaimable on '{self.own_system_id}'",
                )
            return True, None

        if transit_scope.startswith("shared:"):
            scope_id = transit_scope.split(":", 1)[1].strip()
            if not self.scope_registry:
                return False, f"shared task '{scope_id}' requires a configured SharedScopeRegistry"

            now = current_time_iso or datetime.now(timezone.utc).isoformat()
            is_valid, reason = self.scope_registry.validate_scope(scope_id, current_time_iso=now)
            if not is_valid:
                return False, f"shared scope invalid: {reason}"

            # 5. Clock skew uncertainty check for shared tasks
            if clock_is_uncertain:
                return False, "system clock is uncertain; shared tasks paused fail-closed"

            # 6. Claim / lease verification for shared tasks
            assigned_to = (task.get("assigned_to") or "").strip()
            if assigned_to:
                # If assigned to foreign system or foreign agent
                if ":" in assigned_to:
                    claimant_sys, _ = assigned_to.split(":", 1)
                    if claimant_sys != self.own_system_id:
                        return False, f"claimed by foreign system '{claimant_sys}'"
                elif assigned_to != "" and status == "active":
                    # Check if claimed by someone else locally or active
                    pass

            return True, None

        # Unknown transit_scope format
        return False, f"unknown transit_scope format '{transit_scope}'"
