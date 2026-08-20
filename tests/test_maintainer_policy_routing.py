# -*- coding: utf-8 -*-
"""Executable safety contract for policy-aware MAINTAINER runs."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taskplan.__main__ import main
from taskplan.maintenance import plan_finding


def _base(kind: str = "policy_violation") -> dict:
    return {
        "kind": kind,
        "locator": "docs/legacy.md",
        "summary": "Adopted placement policy is violated",
        "evidence": ["docs/legacy.md:1", "POLICY.md:12"],
        "policy": {
            "resolution": "resolved",
            "id": "P-ROOT-001",
            "source": "POLICY.md",
            "scope": "project",
            "adoption": "explicit",
            "valid": True,
        },
        "destination": {
            "path": "docs/archive/legacy.md",
            "content_evidence": "The document declares itself historical.",
            "provenance": "git history and document header",
        },
        "gates": {
            "authorized": True,
            "reversible": True,
            "foreign_lock": False,
            "user_lock": False,
            "hard_delete": False,
            "symlink_safe": True,
            "cloud_safe": True,
            "dirty_git_safe": True,
            "secret_safe": True,
        },
        "impact": {
            "systemwide": False,
            "cross_host": False,
            "causal_policy_conflict": False,
            "requires_user_decision": False,
        },
    }


class MaintainerPolicyRoutingTests(unittest.TestCase):
    def test_unambiguous_adopted_policy_violation_is_safe_autofix(self):
        plan = plan_finding(_base())
        self.assertEqual(plan.classification, "safe_autofix")
        self.assertTrue(plan.mutation_allowed)
        self.assertEqual(plan.routes, ("receipt",))

    def test_ambiguous_placement_stays_put_and_is_routed(self):
        finding = _base("placement")
        finding["policy"] = {"resolution": "none"}
        finding["destination"]["content_evidence"] = ""
        plan = plan_finding(finding)
        self.assertEqual(plan.classification, "needs_ticket")
        self.assertFalse(plan.mutation_allowed)
        self.assertIn("ticket_master", plan.routes)

    def test_empirical_placement_needs_no_invented_universal_policy(self):
        finding = _base("placement")
        finding["policy"] = {"resolution": "none"}
        plan = plan_finding(finding)
        self.assertEqual(plan.classification, "safe_autofix")
        self.assertTrue(plan.mutation_allowed)
        self.assertFalse(plan.policy_proposal_required)

    def test_missing_policy_adoption_blocks_autofix(self):
        finding = _base()
        finding["policy"]["adoption"] = "unknown"
        plan = plan_finding(finding)
        self.assertEqual(plan.classification, "needs_ticket")
        self.assertFalse(plan.mutation_allowed)

    def test_foreign_lock_blocks_mutation(self):
        finding = _base()
        finding["gates"]["foreign_lock"] = True
        plan = plan_finding(finding)
        self.assertFalse(plan.mutation_allowed)
        self.assertIn("foreign lock", " ".join(plan.reasons).lower())

    def test_hard_delete_is_never_planned(self):
        finding = _base()
        finding["gates"]["hard_delete"] = True
        plan = plan_finding(finding)
        self.assertFalse(plan.mutation_allowed)
        self.assertNotEqual(plan.classification, "safe_autofix")

    def test_systemwide_conflict_routes_to_auditor_and_ticket_sink(self):
        finding = _base()
        finding["impact"]["systemwide"] = True
        plan = plan_finding(finding)
        self.assertEqual(plan.classification, "needs_system_audit")
        self.assertFalse(plan.mutation_allowed)
        self.assertEqual(
            plan.routes,
            ("local_finding", "system_auditor", "ticket_master"),
        )
        user_finding = _base()
        user_finding["impact"]["requires_user_decision"] = True
        self.assertEqual(
            plan_finding(user_finding).classification,
            "needs_user_decision",
        )
        informational = _base()
        informational["impact"]["informational"] = True
        info_plan = plan_finding(informational)
        self.assertEqual(info_plan.classification, "informational")
        self.assertFalse(info_plan.mutation_allowed)
        self.assertFalse(info_plan.create_ticket)

    def test_existing_fingerprint_suppresses_duplicate_ticket(self):
        finding = _base("placement")
        finding["destination"]["content_evidence"] = ""
        first = plan_finding(finding)
        duplicate = plan_finding(
            finding,
            existing_ticket_fingerprints={first.fingerprint},
        )
        self.assertTrue(first.create_ticket)
        self.assertFalse(duplicate.create_ticket)
        self.assertTrue(duplicate.duplicate)

    def test_cli_prints_machine_readable_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "finding.json"
            input_path.write_text(json.dumps(_base()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["maintainer-plan", "--input", str(input_path)])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["classification"], "safe_autofix")
        self.assertTrue(payload["mutation_allowed"])


if __name__ == "__main__":
    unittest.main()
