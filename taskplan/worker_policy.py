"""Pure policy contract for BACH/Ocean worker completion and review.

This module decides whether a submitted worker result may complete, must wait
for review, or needs correction. It does not claim tasks, check locks, verify
GitHub, persist state, or manage model/compute slots. Those authorities remain
with the consuming runtime.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


CONTRACT_VERSION = "worker-review-policy.v1alpha1"


class PolicyInputError(ValueError):
    """A policy or evidence record is malformed."""


class CompletionMode(str, Enum):
    SIMPLE_TASK = "simple_task"
    BALANCED = "balanced"
    HIGH_SECURITY = "high_security"


class ReviewerTier(str, Enum):
    STANDARD = "standard"
    TOP = "top"


class ReviewStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    APPROVED = "approved"
    FINDINGS = "findings"
    REJECTED = "rejected"


class UserReviewStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExternalReviewStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class DecisionState(str, Enum):
    COMPLETED = "completed"
    COMPLETED_UNREVIEWED = "completed_unreviewed"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_USER_REVIEW = "awaiting_user_review"
    AWAITING_EXTERNAL_REVIEW = "awaiting_external_review"
    CORRECTION_REQUIRED = "correction_required"
    RESULT_INCOMPLETE = "result_incomplete"
    INTEGRITY_BLOCKED = "integrity_blocked"


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    tier: ReviewerTier = ReviewerTier.STANDARD

    def __post_init__(self) -> None:
        if (
            not isinstance(self.reviewer_id, str)
            or not self.reviewer_id
            or self.reviewer_id.strip() != self.reviewer_id
        ):
            raise PolicyInputError("reviewer_id must be a non-empty, trimmed id")
        if not isinstance(self.tier, ReviewerTier):
            raise PolicyInputError("reviewer tier must be a ReviewerTier")


@dataclass(frozen=True)
class ReviewRecord:
    status: ReviewStatus = ReviewStatus.NOT_REQUESTED
    reviewer_id: str | None = None
    task_id: str | None = None
    result_revision: str | None = None
    independent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReviewStatus):
            raise PolicyInputError("review status must be a ReviewStatus")
        if type(self.independent) is not bool:
            raise PolicyInputError("review independent flag must be boolean")
        _validate_optional_id("reviewer_id", self.reviewer_id)
        _validate_optional_id("review task_id", self.task_id)
        _validate_optional_id("review result_revision", self.result_revision)


@dataclass(frozen=True)
class UserReviewRecord:
    status: UserReviewStatus = UserReviewStatus.NOT_REQUESTED
    task_id: str | None = None
    result_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, UserReviewStatus):
            raise PolicyInputError("user review status must be a UserReviewStatus")
        _validate_optional_id("user review task_id", self.task_id)
        _validate_optional_id("user review result_revision", self.result_revision)


@dataclass(frozen=True)
class ExternalReviewRecord:
    status: ExternalReviewStatus = ExternalReviewStatus.NOT_APPLICABLE
    task_id: str | None = None
    result_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExternalReviewStatus):
            raise PolicyInputError("external review status must be an ExternalReviewStatus")
        _validate_optional_id("external review task_id", self.task_id)
        _validate_optional_id("external review result_revision", self.result_revision)


def _validate_optional_id(name: str, value: str | None) -> None:
    if value is not None and (
        not isinstance(value, str) or not value or value.strip() != value
    ):
        raise PolicyInputError(f"{name} must be None or a non-empty, trimmed value")


@dataclass(frozen=True)
class WorkerReviewPolicy:
    policy_version: str = "1"
    default_mode: CompletionMode = CompletionMode.SIMPLE_TASK
    reviewers: tuple[Reviewer, ...] = ()
    allow_review_task_pickup: bool = True
    review_expiry_seconds: int | None = None
    github_external_review: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version
            or self.policy_version.strip() != self.policy_version
        ):
            raise PolicyInputError("policy_version must be a non-empty, trimmed value")
        if not isinstance(self.default_mode, CompletionMode):
            raise PolicyInputError("default_mode must be a CompletionMode")
        if type(self.allow_review_task_pickup) is not bool:
            raise PolicyInputError("allow_review_task_pickup must be boolean")
        if type(self.github_external_review) is not bool:
            raise PolicyInputError("github_external_review must be boolean")
        reviewers = tuple(self.reviewers)
        if any(not isinstance(reviewer, Reviewer) for reviewer in reviewers):
            raise PolicyInputError("reviewers must contain Reviewer records")
        reviewer_ids = [reviewer.reviewer_id for reviewer in reviewers]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise PolicyInputError("reviewer ids must be unique")
        object.__setattr__(self, "reviewers", reviewers)
        if self.review_expiry_seconds is not None:
            if (
                type(self.review_expiry_seconds) is not int
                or self.review_expiry_seconds <= 0
            ):
                raise PolicyInputError("review_expiry_seconds must be positive or None")


@dataclass(frozen=True)
class TaskPolicyOverride:
    mode: CompletionMode | None = None
    require_user_review: bool = False
    github_external_review: bool | None = None
    allow_review_task_pickup: bool | None = None

    def __post_init__(self) -> None:
        if self.mode is not None and not isinstance(self.mode, CompletionMode):
            raise PolicyInputError("task mode override must be a CompletionMode")
        if type(self.require_user_review) is not bool:
            raise PolicyInputError("require_user_review must be boolean")
        if (
            self.github_external_review is not None
            and type(self.github_external_review) is not bool
        ):
            raise PolicyInputError(
                "github_external_review override must be boolean or None"
            )
        if (
            self.allow_review_task_pickup is not None
            and type(self.allow_review_task_pickup) is not bool
        ):
            raise PolicyInputError(
                "allow_review_task_pickup override must be boolean or None"
            )


@dataclass(frozen=True)
class WorkerReviewContext:
    """Runtime evidence for one exact task result.

    ``result_revision`` must identify an immutable receipt bound by the runtime
    to the exact task/scope version, project-state reference, and workspace
    fingerprint. This policy module compares revision references; it does not
    create or validate those snapshots itself.
    """

    task_id: str
    author_id: str
    result_revision: str
    lease_valid: bool
    fence_valid: bool
    locks_clear: bool
    task_contract_met: bool
    result_evidence_valid: bool
    user_review_required: bool = False
    github_pr_review_required: bool = False
    result_age_seconds: int | None = None
    review: ReviewRecord = ReviewRecord()
    user_review: UserReviewRecord = UserReviewRecord()
    external_review: ExternalReviewRecord = ExternalReviewRecord()

    def __post_init__(self) -> None:
        for name in ("task_id", "author_id", "result_revision"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or value.strip() != value
            ):
                raise PolicyInputError(f"{name} must be a non-empty, trimmed value")
        if self.result_age_seconds is not None:
            if (
                type(self.result_age_seconds) is not int
                or self.result_age_seconds < 0
            ):
                raise PolicyInputError("result_age_seconds must be non-negative or None")
        for name in (
            "lease_valid",
            "fence_valid",
            "locks_clear",
            "task_contract_met",
            "result_evidence_valid",
            "user_review_required",
            "github_pr_review_required",
        ):
            if type(getattr(self, name)) is not bool:
                raise PolicyInputError(f"{name} must be boolean")
        if not isinstance(self.review, ReviewRecord):
            raise PolicyInputError("review must be a ReviewRecord")
        if not isinstance(self.user_review, UserReviewRecord):
            raise PolicyInputError("user_review must be a UserReviewRecord")
        if not isinstance(self.external_review, ExternalReviewRecord):
            raise PolicyInputError("external_review must be an ExternalReviewRecord")


@dataclass(frozen=True)
class WorkerReviewDecision:
    contract_version: str
    policy_version: str
    policy_source: str
    task_id: str
    effective_mode: CompletionMode
    state: DecisionState
    reason: str
    completion_allowed: bool
    worker_may_claim_next: bool
    dependents_may_start: bool
    review_task_eligible: bool
    eligible_reviewer_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot suitable for a result/audit record."""
        value = asdict(self)
        value["effective_mode"] = self.effective_mode.value
        value["state"] = self.state.value
        value["eligible_reviewer_ids"] = list(self.eligible_reviewer_ids)
        return value


def _is_bound(task_id: str, result_revision: str, bound_task: str | None,
              bound_revision: str | None) -> bool:
    return bound_task == task_id and bound_revision == result_revision


def evaluate_worker_policy(
    policy: WorkerReviewPolicy,
    context: WorkerReviewContext,
    override: TaskPolicyOverride | None = None,
) -> WorkerReviewDecision:
    """Evaluate one task/result without performing any side effect.

    ``locks_clear``, lease/fence validity, result evidence, reviewer records,
    and external GitHub verification are facts supplied by their respective
    authorities. A mode or task override cannot disable those integrity gates.
    """
    if not isinstance(policy, WorkerReviewPolicy):
        raise PolicyInputError("policy must be a WorkerReviewPolicy")
    if not isinstance(context, WorkerReviewContext):
        raise PolicyInputError("context must be a WorkerReviewContext")
    if override is not None and not isinstance(override, TaskPolicyOverride):
        raise PolicyInputError("override must be a TaskPolicyOverride")

    override = override or TaskPolicyOverride()
    mode = override.mode or policy.default_mode
    has_override = (
        override.mode is not None
        or override.require_user_review
        or override.github_external_review is not None
        or override.allow_review_task_pickup is not None
    )
    policy_source = "task_override" if has_override else "system_default"
    github_external = (
        policy.github_external_review
        if override.github_external_review is None
        else override.github_external_review
    )

    all_reviewers = {reviewer.reviewer_id: reviewer for reviewer in policy.reviewers}
    if mode is CompletionMode.HIGH_SECURITY:
        review_task_reviewer_pool = tuple(
            reviewer
            for reviewer in policy.reviewers
            if reviewer.tier is ReviewerTier.TOP
        )
    else:
        review_task_reviewer_pool = policy.reviewers
    reviewer_pool = tuple(
        reviewer.reviewer_id
        for reviewer in review_task_reviewer_pool
        if reviewer.reviewer_id != context.author_id
    )
    allow_review_task_pickup = (
        policy.allow_review_task_pickup
        if override.allow_review_task_pickup is None
        else override.allow_review_task_pickup
    )
    # The task author cannot review their own result. Report review work as
    # eligible only when at least one authorized reviewer remains after that
    # exclusion; otherwise the selector would advertise an unclaimable review.
    review_task_eligible = allow_review_task_pickup and bool(reviewer_pool)

    def decision(
        state: DecisionState,
        reason: str,
        *,
        completion_allowed: bool = False,
        worker_may_claim_next: bool = False,
        dependents_may_start: bool = False,
        review_task_eligible: bool = False,
    ) -> WorkerReviewDecision:
        return WorkerReviewDecision(
            contract_version=CONTRACT_VERSION,
            policy_version=policy.policy_version,
            policy_source=policy_source,
            task_id=context.task_id,
            effective_mode=mode,
            state=state,
            reason=reason,
            completion_allowed=completion_allowed,
            worker_may_claim_next=worker_may_claim_next,
            dependents_may_start=dependents_may_start,
            review_task_eligible=review_task_eligible,
            eligible_reviewer_ids=reviewer_pool,
        )

    for field_name, valid in (
        ("lease_invalid", context.lease_valid),
        ("fence_invalid", context.fence_valid),
        ("lock_conflict", context.locks_clear),
    ):
        if not valid:
            return decision(DecisionState.INTEGRITY_BLOCKED, field_name)
    if not context.result_evidence_valid:
        return decision(DecisionState.RESULT_INCOMPLETE, "result_evidence_invalid")

    user_review_required = context.user_review_required or override.require_user_review
    user = context.user_review
    user_bound = _is_bound(context.task_id, context.result_revision, user.task_id,
                           user.result_revision)
    external = context.external_review
    external_bound = _is_bound(context.task_id, context.result_revision, external.task_id,
                               external.result_revision)
    review = context.review
    reviewer = all_reviewers.get(review.reviewer_id or "")
    review_is_current = (
        reviewer is not None
        and review.reviewer_id != context.author_id
        and review.independent
        and _is_bound(context.task_id, context.result_revision, review.task_id,
                      review.result_revision)
        and (mode is not CompletionMode.HIGH_SECURITY or reviewer.tier is ReviewerTier.TOP)
    )
    external_replaces_internal_review = (
        context.github_pr_review_required
        and github_external
        and external.status is ExternalReviewStatus.VERIFIED
        and external_bound
    )

    # A valid adverse finding is actionable even when the submitted work does
    # not yet satisfy its task contract. Neither the finding nor its correction
    # grants completion; all positive paths below still require that contract.
    if user_review_required and user.status is UserReviewStatus.REJECTED and user_bound:
        return decision(
            DecisionState.CORRECTION_REQUIRED,
            "user_review_rejected",
            worker_may_claim_next=mode is not CompletionMode.HIGH_SECURITY,
            review_task_eligible=(
                review_task_eligible and mode is not CompletionMode.HIGH_SECURITY
            ),
        )
    if (context.github_pr_review_required
            and external.status is ExternalReviewStatus.REJECTED and external_bound):
        return decision(
            DecisionState.CORRECTION_REQUIRED,
            "external_review_rejected",
            worker_may_claim_next=mode is not CompletionMode.HIGH_SECURITY,
            review_task_eligible=(
                review_task_eligible and mode is not CompletionMode.HIGH_SECURITY
            ),
        )
    if (review_is_current and review.status is ReviewStatus.REJECTED
            and (not external_replaces_internal_review
                 or not context.task_contract_met)):
        return decision(
            DecisionState.CORRECTION_REQUIRED,
            "review_rejected",
            worker_may_claim_next=mode is not CompletionMode.HIGH_SECURITY,
            review_task_eligible=(
                review_task_eligible and mode is not CompletionMode.HIGH_SECURITY
            ),
        )
    if review_is_current and review.status is ReviewStatus.FINDINGS:
        if not context.task_contract_met:
            return decision(
                DecisionState.CORRECTION_REQUIRED,
                "review_findings_contract_not_met",
                worker_may_claim_next=mode is not CompletionMode.HIGH_SECURITY,
                review_task_eligible=(
                    review_task_eligible and mode is not CompletionMode.HIGH_SECURITY
                ),
            )
        if (not external_replaces_internal_review
                and mode is CompletionMode.HIGH_SECURITY):
            return decision(
                DecisionState.CORRECTION_REQUIRED, "high_security_findings"
            )

    if not context.task_contract_met:
        return decision(DecisionState.RESULT_INCOMPLETE, "task_contract_not_met")

    if user_review_required:
        if user.status is not UserReviewStatus.APPROVED or not user_bound:
            return decision(
                DecisionState.AWAITING_USER_REVIEW,
                "user_review_required",
                worker_may_claim_next=mode is not CompletionMode.HIGH_SECURITY,
                review_task_eligible=(
                    review_task_eligible and mode is not CompletionMode.HIGH_SECURITY
                ),
            )

    if context.github_pr_review_required:
        if external.status is ExternalReviewStatus.VERIFIED and external_bound:
            if github_external:
                return decision(
                    DecisionState.COMPLETED,
                    "external_review_verified",
                    completion_allowed=True,
                    worker_may_claim_next=True,
                    dependents_may_start=True,
                    review_task_eligible=review_task_eligible,
                )
            # The route preference controls whether verified GitHub review
            # replaces internal model review. It cannot waive the canonical
            # external gate; after that gate passes, apply the normal mode.
        else:
            return decision(
                DecisionState.AWAITING_EXTERNAL_REVIEW,
                "external_review_not_verified",
                worker_may_claim_next=mode is not CompletionMode.HIGH_SECURITY,
                review_task_eligible=(
                    review_task_eligible and mode is not CompletionMode.HIGH_SECURITY
                ),
            )

    review_valid = review.status is ReviewStatus.NOT_REQUESTED or review_is_current

    if review_is_current and review.status is ReviewStatus.APPROVED:
        return decision(
            DecisionState.COMPLETED,
            "review_approved",
            completion_allowed=True,
            worker_may_claim_next=True,
            dependents_may_start=True,
            review_task_eligible=review_task_eligible,
        )
    if review_is_current and review.status is ReviewStatus.FINDINGS:
        return decision(
            DecisionState.COMPLETED,
            "non_blocking_review_findings",
            completion_allowed=True,
            worker_may_claim_next=True,
            dependents_may_start=True,
            review_task_eligible=review_task_eligible,
        )

    if mode is CompletionMode.SIMPLE_TASK:
        return decision(
            DecisionState.COMPLETED,
            "self_check_and_result_evidence_sufficient",
            completion_allowed=True,
            worker_may_claim_next=True,
            dependents_may_start=True,
            review_task_eligible=review_task_eligible,
        )

    if mode is CompletionMode.BALANCED:
        expired = (
            policy.review_expiry_seconds is not None
            and context.result_age_seconds is not None
            and context.result_age_seconds >= policy.review_expiry_seconds
            and (
                review.status in (ReviewStatus.NOT_REQUESTED, ReviewStatus.PENDING)
                or not review_is_current
            )
        )
        if expired:
            return decision(
                DecisionState.COMPLETED_UNREVIEWED,
                "balanced_review_expired_without_adverse_finding",
                completion_allowed=True,
                worker_may_claim_next=True,
                dependents_may_start=True,
                review_task_eligible=review_task_eligible,
            )
        reason = "review_not_current_or_eligible" if not review_valid else "balanced_review_pending"
        return decision(
            DecisionState.AWAITING_REVIEW,
            reason,
            worker_may_claim_next=True,
            review_task_eligible=review_task_eligible,
        )

    reason = "no_authorized_top_reviewer" if not reviewer_pool else "high_security_review_pending"
    if not review_valid:
        reason = "review_not_current_or_eligible"
    return decision(
        DecisionState.AWAITING_REVIEW,
        reason,
        review_task_eligible=review_task_eligible,
    )
