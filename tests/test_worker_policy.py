"""Consumer scenarios for the persistenceless worker completion contract."""
from dataclasses import replace

import pytest

from taskplan.worker_policy import (
    CompletionMode,
    DecisionState,
    ExternalReviewRecord,
    ExternalReviewStatus,
    PolicyInputError,
    Reviewer,
    ReviewerTier,
    ReviewRecord,
    ReviewStatus,
    TaskPolicyOverride,
    UserReviewRecord,
    UserReviewStatus,
    WorkerReviewContext,
    WorkerReviewPolicy,
    evaluate_worker_policy,
)


@pytest.fixture
def context():
    return WorkerReviewContext(
        task_id="41", author_id="writer", result_revision="result-1",
        lease_valid=True, fence_valid=True, locks_clear=True,
        task_contract_met=True, result_evidence_valid=True,
    )


def policy(mode):
    return WorkerReviewPolicy(default_mode=mode, reviewers=(
        Reviewer("writer", ReviewerTier.TOP), Reviewer("local"),
        Reviewer("independent-top", ReviewerTier.TOP),
    ))


def review(reviewer="local", revision="result-1", status=ReviewStatus.APPROVED):
    return ReviewRecord(status, reviewer, "41", revision, True)


@pytest.mark.parametrize("mode", list(CompletionMode))
@pytest.mark.parametrize("gate", ["lease_valid", "fence_valid", "locks_clear"])
def test_no_mode_can_disable_an_integrity_gate(context, mode, gate):
    decision = evaluate_worker_policy(policy(mode), replace(context, **{gate: False}))
    assert decision.state is DecisionState.INTEGRITY_BLOCKED
    assert not decision.completion_allowed
    assert not decision.dependents_may_start


@pytest.mark.parametrize("mode", list(CompletionMode))
@pytest.mark.parametrize("gate", ["task_contract_met", "result_evidence_valid"])
def test_results_must_satisfy_contract_and_evidence(context, mode, gate):
    decision = evaluate_worker_policy(policy(mode), replace(context, **{gate: False}))
    assert decision.state is DecisionState.RESULT_INCOMPLETE
    assert not decision.completion_allowed


@pytest.mark.parametrize("mode", list(CompletionMode))
def test_user_review_blocks_even_after_review_expiry_and_top_approval(context, mode):
    current = replace(context, user_review_required=True, result_age_seconds=1000,
                      review=review("independent-top"))
    result = evaluate_worker_policy(replace(policy(mode), review_expiry_seconds=1), current)
    assert result.state is DecisionState.AWAITING_USER_REVIEW
    assert not result.completion_allowed and not result.dependents_may_start
    assert result.worker_may_claim_next == (mode is not CompletionMode.HIGH_SECURITY)
    stale_user = UserReviewRecord(UserReviewStatus.APPROVED, "41", "older-result")
    assert not evaluate_worker_policy(policy(mode), replace(current, user_review=stale_user)).completion_allowed
    current_user = replace(stale_user, result_revision="result-1")
    assert evaluate_worker_policy(policy(mode), replace(current, user_review=current_user)).completion_allowed


def test_balanced_can_continue_independent_work_but_not_dependents(context):
    result = evaluate_worker_policy(policy(CompletionMode.BALANCED), context)
    assert result.state is DecisionState.AWAITING_REVIEW
    assert result.worker_may_claim_next and not result.dependents_may_start
    assert not result.completion_allowed


@pytest.mark.parametrize("record", [review("writer"), review(revision="old"),
                                   replace(review(), independent=False), review("unknown")])
def test_untrusted_self_or_stale_review_cannot_complete_balanced(context, record):
    result = evaluate_worker_policy(policy(CompletionMode.BALANCED), replace(context, review=record))
    assert not result.completion_allowed


def test_only_independent_top_review_completes_high_security(context):
    high = policy(CompletionMode.HIGH_SECURITY)
    assert not evaluate_worker_policy(high, replace(context, review=review())).completion_allowed
    result = evaluate_worker_policy(high, replace(context, review=review("independent-top")))
    assert result.completion_allowed and result.dependents_may_start
    assert result.eligible_reviewer_ids == ("independent-top",)


@pytest.mark.parametrize("mode", list(CompletionMode))
def test_current_rejection_always_requires_correction(context, mode):
    result = evaluate_worker_policy(policy(mode), replace(context,
        review=review("independent-top", status=ReviewStatus.REJECTED)))
    assert result.state is DecisionState.CORRECTION_REQUIRED
    assert not result.completion_allowed


@pytest.mark.parametrize("mode", list(CompletionMode))
@pytest.mark.parametrize("source", ["model", "user", "github"])
def test_current_rejection_opens_rework_even_when_contract_unmet(context, mode, source):
    adverse = {
        "model": {"review": review("independent-top", status=ReviewStatus.REJECTED)},
        "user": {"user_review_required": True, "user_review": UserReviewRecord(
            UserReviewStatus.REJECTED, "41", "result-1")},
        "github": {"github_pr_review_required": True, "external_review": ExternalReviewRecord(
            ExternalReviewStatus.REJECTED, "41", "result-1")},
    }
    current = replace(context, task_contract_met=False, **adverse[source])
    result = evaluate_worker_policy(policy(mode), current)
    assert result.state is DecisionState.CORRECTION_REQUIRED
    assert not result.completion_allowed and not result.dependents_may_start
    assert result.worker_may_claim_next is (mode is not CompletionMode.HIGH_SECURITY)


def test_top_findings_still_require_high_security_correction_when_contract_met(context):
    current = replace(context,
                      review=review("independent-top", status=ReviewStatus.FINDINGS))
    result = evaluate_worker_policy(policy(CompletionMode.HIGH_SECURITY), current)
    assert result.state is DecisionState.CORRECTION_REQUIRED
    assert result.reason == "high_security_findings"
    assert not result.completion_allowed and not result.worker_may_claim_next


@pytest.mark.parametrize("mode", list(CompletionMode))
def test_current_independent_findings_require_correction_when_contract_unmet(context, mode):
    current = replace(context, task_contract_met=False,
                      review=review("independent-top", status=ReviewStatus.FINDINGS))
    result = evaluate_worker_policy(policy(mode), current)
    assert result.state is DecisionState.CORRECTION_REQUIRED
    assert result.reason == "review_findings_contract_not_met"
    assert not result.completion_allowed and not result.dependents_may_start
    assert result.worker_may_claim_next is (mode is not CompletionMode.HIGH_SECURITY)
    assert result.review_task_eligible is (mode is not CompletionMode.HIGH_SECURITY)


@pytest.mark.parametrize("mode", [CompletionMode.SIMPLE_TASK, CompletionMode.BALANCED])
def test_current_findings_with_met_contract_keep_existing_completion(context, mode):
    current = replace(context,
                      review=review("independent-top", status=ReviewStatus.FINDINGS))
    result = evaluate_worker_policy(policy(mode), current)
    assert result.state is DecisionState.COMPLETED
    assert result.reason == "non_blocking_review_findings"
    assert result.completion_allowed and result.dependents_may_start


@pytest.mark.parametrize("record", [
    review("writer", status=ReviewStatus.FINDINGS),
    review("unknown", status=ReviewStatus.FINDINGS),
    review("independent-top", revision="old", status=ReviewStatus.FINDINGS),
    replace(review("independent-top", status=ReviewStatus.FINDINGS), independent=False),
])
@pytest.mark.parametrize("mode", list(CompletionMode))
def test_untrusted_or_stale_findings_do_not_open_contract_rework(context, mode, record):
    current = replace(context, task_contract_met=False, review=record)
    result = evaluate_worker_policy(policy(mode), current)
    assert result.state is DecisionState.RESULT_INCOMPLETE
    assert result.reason == "task_contract_not_met"
    assert not result.completion_allowed and not result.dependents_may_start


@pytest.mark.parametrize(("gate", "expected_state"), [
    ("lease_valid", DecisionState.INTEGRITY_BLOCKED),
    ("fence_valid", DecisionState.INTEGRITY_BLOCKED),
    ("locks_clear", DecisionState.INTEGRITY_BLOCKED),
    ("result_evidence_valid", DecisionState.RESULT_INCOMPLETE),
])
def test_findings_contract_rework_cannot_bypass_evidence_gates(context, gate, expected_state):
    current = replace(context, task_contract_met=False,
                      review=review("independent-top", status=ReviewStatus.FINDINGS),
                      **{gate: False})
    result = evaluate_worker_policy(policy(CompletionMode.BALANCED), current)
    assert result.state is expected_state
    assert not result.completion_allowed and not result.dependents_may_start


@pytest.mark.parametrize(("status", "reason"), [
    (ReviewStatus.FINDINGS, "review_findings_contract_not_met"),
    (ReviewStatus.REJECTED, "review_rejected"),
])
def test_current_adverse_review_requires_correction_even_when_external_review_is_verified(
    context, status, reason
):
    current = replace(
        context,
        task_contract_met=False,
        github_pr_review_required=True,
        external_review=ExternalReviewRecord(ExternalReviewStatus.VERIFIED, "41", "result-1"),
        review=review("independent-top", status=status),
    )
    for configured_policy in (
        policy(CompletionMode.BALANCED),
        replace(policy(CompletionMode.BALANCED), github_external_review=False),
    ):
        result = evaluate_worker_policy(configured_policy, current)
        assert result.state is DecisionState.CORRECTION_REQUIRED
        assert result.reason == reason
        assert not result.completion_allowed and not result.dependents_may_start


@pytest.mark.parametrize("mode", list(CompletionMode))
@pytest.mark.parametrize("status", [ReviewStatus.FINDINGS, ReviewStatus.REJECTED])
def test_verified_external_review_still_replaces_adverse_review_when_contract_is_met(
    context, mode, status
):
    current = replace(
        context,
        github_pr_review_required=True,
        external_review=ExternalReviewRecord(ExternalReviewStatus.VERIFIED, "41", "result-1"),
        review=review("independent-top", status=status),
    )
    result = evaluate_worker_policy(policy(mode), current)
    assert result.state is DecisionState.COMPLETED
    assert result.reason == "external_review_verified"
    assert result.completion_allowed and result.dependents_may_start


@pytest.mark.parametrize("record", [
    review("writer", status=ReviewStatus.REJECTED),
    review("unknown", status=ReviewStatus.REJECTED),
    review("independent-top", revision="old", status=ReviewStatus.REJECTED),
    replace(review("independent-top", status=ReviewStatus.REJECTED), independent=False),
])
def test_untrusted_rejection_cannot_open_rework_for_unmet_contract(context, record):
    result = evaluate_worker_policy(policy(CompletionMode.HIGH_SECURITY), replace(
        context, task_contract_met=False, review=record))
    assert result.state is DecisionState.RESULT_INCOMPLETE
    assert not result.completion_allowed


def test_high_security_standard_tier_findings_cannot_open_rework(context):
    current = replace(context, task_contract_met=False,
                      review=review("local", status=ReviewStatus.FINDINGS))
    result = evaluate_worker_policy(policy(CompletionMode.HIGH_SECURITY), current)
    assert result.state is DecisionState.RESULT_INCOMPLETE


@pytest.mark.parametrize("gate", ["lease_valid", "fence_valid", "locks_clear",
                                  "result_evidence_valid"])
def test_adverse_review_cannot_bypass_missing_integrity_or_evidence(context, gate):
    current = replace(context, task_contract_met=False,
                      review=review("independent-top", status=ReviewStatus.REJECTED),
                      **{gate: False})
    result = evaluate_worker_policy(policy(CompletionMode.HIGH_SECURITY), current)
    assert result.state is (DecisionState.RESULT_INCOMPLETE if gate == "result_evidence_valid"
                            else DecisionState.INTEGRITY_BLOCKED)
    assert not result.completion_allowed


@pytest.mark.parametrize("mode", list(CompletionMode))
def test_approval_and_expiry_never_complete_unmet_contract(context, mode):
    current = replace(context, task_contract_met=False, result_age_seconds=100,
                      review=review("independent-top"))
    configured = replace(policy(mode), review_expiry_seconds=1)
    result = evaluate_worker_policy(configured, current)
    assert result.state is DecisionState.RESULT_INCOMPLETE
    assert not result.completion_allowed and not result.dependents_may_start


def test_required_user_and_github_completion_gates_remain_after_rework(context):
    adverse = replace(context, task_contract_met=False, user_review_required=True,
                      github_pr_review_required=True,
                      review=review("independent-top", status=ReviewStatus.FINDINGS))
    configured = policy(CompletionMode.BALANCED)
    assert evaluate_worker_policy(configured, adverse).state is DecisionState.CORRECTION_REQUIRED
    corrected = replace(adverse, task_contract_met=True, result_revision="result-2",
                        review=review("independent-top", revision="result-1"))
    pending = evaluate_worker_policy(configured, corrected)
    assert pending.state is DecisionState.AWAITING_USER_REVIEW
    assert not pending.completion_allowed
    user_approved = replace(corrected, user_review=UserReviewRecord(
        UserReviewStatus.APPROVED, "41", "result-2"))
    github_pending = evaluate_worker_policy(configured, user_approved)
    assert github_pending.state is DecisionState.AWAITING_EXTERNAL_REVIEW
    assert not github_pending.completion_allowed


def test_verified_external_route_preserves_rejection_of_unmet_contract(context):
    current = replace(context, task_contract_met=False,
                      github_pr_review_required=True,
                      external_review=ExternalReviewRecord(
                          ExternalReviewStatus.VERIFIED, "41", "result-1"),
                      review=review("independent-top", status=ReviewStatus.REJECTED))
    routed = evaluate_worker_policy(policy(CompletionMode.HIGH_SECURITY), current)
    assert routed.state is DecisionState.CORRECTION_REQUIRED
    assert routed.reason == "review_rejected"
    assert not routed.completion_allowed and not routed.dependents_may_start
    assert not routed.worker_may_claim_next
    internal = evaluate_worker_policy(
        replace(policy(CompletionMode.HIGH_SECURITY), github_external_review=False), current)
    assert internal.state is DecisionState.CORRECTION_REQUIRED
    assert not internal.completion_allowed


def test_balanced_expiry_does_not_erase_adverse_review(context):
    balanced = replace(policy(CompletionMode.BALANCED), review_expiry_seconds=1)
    expired = replace(context, result_age_seconds=100)
    assert evaluate_worker_policy(balanced, expired).state is DecisionState.COMPLETED_UNREVIEWED
    assert not evaluate_worker_policy(balanced, replace(expired,
        review=review(status=ReviewStatus.REJECTED))).completion_allowed
    assert not evaluate_worker_policy(replace(balanced, default_mode=CompletionMode.HIGH_SECURITY), expired).completion_allowed


def test_verified_external_review_avoids_second_internal_review(context):
    current = replace(context, github_pr_review_required=True,
        external_review=ExternalReviewRecord(ExternalReviewStatus.VERIFIED, "41", "result-1"))
    assert evaluate_worker_policy(policy(CompletionMode.HIGH_SECURITY), current).completion_allowed
    stale = replace(current, external_review=replace(current.external_review, result_revision="old"))
    assert evaluate_worker_policy(policy(CompletionMode.SIMPLE_TASK), stale).state is DecisionState.AWAITING_EXTERNAL_REVIEW


@pytest.mark.parametrize("mode", list(CompletionMode))
@pytest.mark.parametrize("routing_source", ["global_false", "task_override_false"])
@pytest.mark.parametrize(("external", "expected_state"), [
    (ExternalReviewRecord(ExternalReviewStatus.PENDING, "41", "result-1"),
     DecisionState.AWAITING_EXTERNAL_REVIEW),
    (ExternalReviewRecord(ExternalReviewStatus.REJECTED, "41", "result-1"),
     DecisionState.CORRECTION_REQUIRED),
    (ExternalReviewRecord(ExternalReviewStatus.REJECTED, "41", "old-result"),
     DecisionState.AWAITING_EXTERNAL_REVIEW),
    (ExternalReviewRecord(ExternalReviewStatus.VERIFIED, "41", "old-result"),
     DecisionState.AWAITING_EXTERNAL_REVIEW),
])
def test_required_github_gate_survives_disabled_routing_preference(
    context, mode, routing_source, external, expected_state
):
    current = replace(
        context,
        github_pr_review_required=True,
        external_review=external,
        # A valid internal approval cannot replace the hard GitHub gate.
        review=review("independent-top"),
    )
    if routing_source == "global_false":
        configured_policy = replace(policy(mode), github_external_review=False)
        override = None
    else:
        configured_policy = policy(mode)
        override = TaskPolicyOverride(github_external_review=False)

    result = evaluate_worker_policy(configured_policy, current, override)

    assert result.state is expected_state
    assert not result.completion_allowed
    assert not result.dependents_may_start


@pytest.mark.parametrize(("mode", "expected_state"), [
    (CompletionMode.SIMPLE_TASK, DecisionState.COMPLETED),
    (CompletionMode.BALANCED, DecisionState.AWAITING_REVIEW),
    (CompletionMode.HIGH_SECURITY, DecisionState.AWAITING_REVIEW),
])
@pytest.mark.parametrize("routing_source", ["global_false", "task_override_false"])
def test_verified_required_external_gate_with_routing_disabled_uses_normal_mode(
    context, mode, expected_state, routing_source
):
    current = replace(
        context,
        github_pr_review_required=True,
        external_review=ExternalReviewRecord(
            ExternalReviewStatus.VERIFIED, "41", "result-1"
        ),
    )

    if routing_source == "global_false":
        configured_policy = replace(policy(mode), github_external_review=False)
        override = None
    else:
        configured_policy = policy(mode)
        override = TaskPolicyOverride(github_external_review=False)

    result = evaluate_worker_policy(configured_policy, current, override)

    assert result.state is expected_state
    assert result.completion_allowed is (mode is CompletionMode.SIMPLE_TASK)
    assert result.dependents_may_start is (mode is CompletionMode.SIMPLE_TASK)


def test_override_preserves_null_false_and_cannot_remove_inherited_user_gate(context):
    override = TaskPolicyOverride(mode=None, require_user_review=False,
                                  github_external_review=False, allow_review_task_pickup=False)
    result = evaluate_worker_policy(policy(CompletionMode.BALANCED),
        replace(context, user_review_required=True), override)
    assert result.state is DecisionState.AWAITING_USER_REVIEW
    assert result.effective_mode is CompletionMode.BALANCED
    assert result.policy_source == "task_override" and not result.review_task_eligible
    assert result.as_dict()["effective_mode"] == "balanced"


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_user_review_flag_is_a_strict_boolean(value):
    with pytest.raises(PolicyInputError):
        TaskPolicyOverride(require_user_review=value)
