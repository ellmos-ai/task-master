"""Consumer scenarios for the persistenceless worker completion contract."""
from dataclasses import replace

import pytest

from taskplan.worker_policy import (
    CompletionMode, DecisionState, ExternalReviewRecord, ExternalReviewStatus,
    PolicyInputError, Reviewer, ReviewerTier, ReviewRecord, ReviewStatus,
    TaskPolicyOverride, UserReviewRecord, UserReviewStatus, WorkerReviewContext,
    WorkerReviewPolicy, evaluate_worker_policy,
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
