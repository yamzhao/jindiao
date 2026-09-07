"""Atomic Run-level submission blackboard for investigation and review Agents."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from openjiuwen.core.foundation.tool import ToolCard, tool
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.base import ContractModel
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    DueDiligenceCheckCatalog,
    FactEvidenceRef,
    RepairTask,
    ReviewIssue,
    RiskClass,
    RiskItem,
    Severity,
)

from .validation import (
    EVIDENCE_GATE_INCONCLUSIVE_SUMMARY,
    downgrade_unsupported_check_conclusion,
    validate_check_result_evidence_gate,
    validate_check_result_scope,
)


class SubmissionGrant(ContractModel):
    agent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    check_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_ids_are_unique(self) -> SubmissionGrant:
        if len(self.check_ids) != len(set(self.check_ids)):
            raise ValueError("submission grant check ids must be unique")
        return self


class CheckSubmissionReceipt(ContractModel):
    run_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    submission_version: int = Field(ge=1)
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted: bool
    idempotent_replay: bool
    accepted_at: AwareDatetime


class ReviewSubmission(ContractModel):
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_id: str = Field(min_length=1)
    check_catalog_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    review_version: int = Field(ge=1)
    issues: tuple[ReviewIssue, ...] = ()
    repair_tasks: tuple[RepairTask, ...] = ()

    @model_validator(mode="after")
    def validate_unique_items(self) -> ReviewSubmission:
        issue_ids = tuple(item.issue_id for item in self.issues)
        repair_ids = tuple(item.repair_id for item in self.repair_tasks)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("review issue ids must be unique")
        if len(repair_ids) != len(set(repair_ids)):
            raise ValueError("review repair ids must be unique")
        known_issues = set(issue_ids)
        for repair in self.repair_tasks:
            unknown = set(repair.issue_ids) - known_issues
            if unknown:
                raise ValueError(f"repair references unknown review issues: {sorted(unknown)}")
        return self


class ReviewSubmissionReceipt(ContractModel):
    run_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    reviewer_agent_id: str = Field(min_length=1)
    review_version: int = Field(ge=1)
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted: bool
    idempotent_replay: bool
    accepted_at: AwareDatetime


class SubmitCheckResultInput(ContractModel):
    result: CheckResult


class SubmitReviewInput(ContractModel):
    review: ReviewSubmission


class BoundRiskItemDraft(ContractModel):
    """Risk fields the Agent must decide; check identity is Run-bound."""

    risk_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    risk_class: RiskClass
    severity: Severity
    conclusion: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class BoundCheckResultDraft(ContractModel):
    """Decision-only submission shape for a Run-bound investigation Agent."""

    check_id: str = Field(min_length=1)
    status: CheckStatus
    decision_summary: str = Field(min_length=1)
    risk_items: tuple[BoundRiskItemDraft, ...] = ()
    fact_evidence_refs: tuple[FactEvidenceRef, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)


class SubmitBoundCheckResultInput(ContractModel):
    result: BoundCheckResultDraft


class BoundReviewDraft(ContractModel):
    """Review content without immutable Run identity or sequence fields."""

    issues: tuple[ReviewIssue, ...] = Field(default=(), max_length=4)
    repair_tasks: tuple[RepairTask, ...] = Field(default=(), max_length=2)


class SubmitBoundReviewInput(ContractModel):
    review: BoundReviewDraft


class SubmissionBudget(Protocol):
    async def claim_tool_call(self, operation: str) -> None: ...

    async def claim_schema_retry(self, operation: str) -> None: ...


def _sha256(value: ContractModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class SubmissionBlackboard:
    """Validate and persist authoritative structured Agent submissions."""

    def __init__(
        self,
        *,
        run_id: str,
        snapshot: EnterpriseContextSnapshot,
        check_catalog: DueDiligenceCheckCatalog,
        grants: tuple[SubmissionGrant, ...],
        reviewer_agent_ids: tuple[str, ...],
        budget_ledger: SubmissionBudget | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not run_id.strip():
            raise ValueError("submission blackboard requires a run id")
        if check_catalog.report_catalog_version != snapshot.report_catalog_version:
            raise ValueError("check catalog does not match snapshot ReportCatalog")
        grant_keys = tuple((item.agent_id, item.task_id) for item in grants)
        if len(grant_keys) != len(set(grant_keys)):
            raise ValueError("submission grants must have unique agent/task keys")
        check_owners: dict[str, tuple[str, str]] = {}
        for grant in grants:
            for check_id in grant.check_ids:
                if check_id not in check_catalog.check_ids:
                    raise ValueError(f"submission grant contains unknown check: {check_id}")
                if check_id in check_owners:
                    raise ValueError(f"check has duplicate submission owners: {check_id}")
                check_owners[check_id] = (grant.agent_id, grant.task_id)
        if len(reviewer_agent_ids) != len(set(reviewer_agent_ids)):
            raise ValueError("reviewer agent ids must be unique")

        self.run_id = run_id
        self.snapshot = snapshot
        self.check_catalog = check_catalog
        self._grant_by_check = check_owners
        self._agent_ids = {item.agent_id for item in grants}
        self._reviewer_agent_ids = frozenset(reviewer_agent_ids)
        self._budget_ledger = budget_ledger
        self._clock = clock
        self._lock = asyncio.Lock()
        self._submissions: dict[tuple[str, str, str, int], CheckResult] = {}
        self._latest_by_check: dict[str, CheckResult] = {}
        self._reviews: dict[int, ReviewSubmission] = {}

    @property
    def submission_count(self) -> int:
        return len(self._submissions)

    @property
    def accepted_results(self) -> tuple[CheckResult, ...]:
        order = {check_id: index for index, check_id in enumerate(self.check_catalog.check_ids)}
        return tuple(sorted(self._latest_by_check.values(), key=lambda item: order[item.check_id]))

    def latest_result(self, check_id: str) -> CheckResult | None:
        return self._latest_by_check.get(check_id)

    @property
    def latest_review(self) -> ReviewSubmission | None:
        if not self._reviews:
            return None
        return self._reviews[max(self._reviews)]

    @property
    def reviews(self) -> tuple[ReviewSubmission, ...]:
        return tuple(self._reviews[key] for key in sorted(self._reviews))

    @property
    def repair_tasks(self) -> tuple[RepairTask, ...]:
        review = self.latest_review
        return review.repair_tasks if review is not None else ()

    async def submit_check_result(
        self,
        *,
        agent_id: str,
        result: CheckResult,
    ) -> CheckSubmissionReceipt:
        validated = CheckResult.model_validate(result.model_dump(mode="json"))
        self._validate_result_scope(agent_id=agent_id, result=validated)
        self._validate_evidence_gate(validated)
        digest = _sha256(validated)
        key = (
            agent_id,
            validated.task_id,
            validated.check_id,
            validated.submission_version,
        )
        async with self._lock:
            existing = self._submissions.get(key)
            if existing is not None:
                if existing != validated:
                    raise ValueError("submission version collision has different content")
                return self._check_receipt(
                    agent_id=agent_id,
                    result=validated,
                    digest=digest,
                    replay=True,
                )
            previous = self._latest_by_check.get(validated.check_id)
            expected_version = 1 if previous is None else previous.submission_version + 1
            if validated.submission_version != expected_version:
                raise ValueError(
                    f"submission version must advance exactly once; expected {expected_version}"
                )
            self._submissions[key] = validated
            self._latest_by_check[validated.check_id] = validated
            return self._check_receipt(
                agent_id=agent_id,
                result=validated,
                digest=digest,
                replay=False,
            )

    async def submit_review(
        self,
        *,
        reviewer_agent_id: str,
        review: ReviewSubmission,
    ) -> ReviewSubmissionReceipt:
        validated = ReviewSubmission.model_validate(review.model_dump(mode="json"))
        self._validate_review(reviewer_agent_id=reviewer_agent_id, review=validated)
        digest = _sha256(validated)
        async with self._lock:
            existing = self._reviews.get(validated.review_version)
            if existing is not None:
                if existing != validated:
                    raise ValueError("review version collision has different content")
                return self._review_receipt(
                    reviewer_agent_id=reviewer_agent_id,
                    review=validated,
                    digest=digest,
                    replay=True,
                )
            expected_version = max(self._reviews, default=0) + 1
            if validated.review_version != expected_version:
                raise ValueError(
                    f"review version must advance exactly once; expected {expected_version}"
                )
            self._reviews[validated.review_version] = validated
            return self._review_receipt(
                reviewer_agent_id=reviewer_agent_id,
                review=validated,
                digest=digest,
                replay=False,
            )

    def build_submit_check_result_tool(self, *, agent_id: str) -> Any:
        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{agent_id}.submit-check-result",
                name="submit_check_result",
                description="Validate and submit one owned check result to the Run blackboard.",
                input_params=SubmitCheckResultInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_check_result(result: dict[str, object]) -> dict[str, object]:
            if self._budget_ledger is not None:
                await self._budget_ledger.claim_tool_call("submit_check_result")
            try:
                parsed = CheckResult.model_validate(result)
            except ValidationError:
                if self._budget_ledger is not None:
                    await self._budget_ledger.claim_schema_retry("submit_check_result")
                raise
            receipt = await self.submit_check_result(agent_id=agent_id, result=parsed)
            return receipt.model_dump(mode="json")

        return submit_check_result

    def build_submit_bound_check_result_tool(
        self,
        *,
        agent_id: str,
        prompt_version: str,
    ) -> Any:
        """Build a decision-only tool with immutable identity injected server-side."""

        if not prompt_version.strip():
            raise ValueError("bound check submission tool requires a prompt version")

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{agent_id}.submit-bound-check-result",
                name="submit_check_result",
                description=(
                    "Submit one owned check decision. Provide only the decision fields in "
                    "the declared schema; Run identity, task, catalog/schema versions, "
                    "Prompt version and submission version are injected automatically."
                ),
                input_params=SubmitBoundCheckResultInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_check_result(result: dict[str, object]) -> dict[str, object]:
            if self._budget_ledger is not None:
                await self._budget_ledger.claim_tool_call("submit_check_result")
            try:
                draft = BoundCheckResultDraft.model_validate(result)
                parsed = self._bind_check_result(
                    agent_id=agent_id,
                    prompt_version=prompt_version,
                    draft=draft,
                )
            except ValidationError:
                if self._budget_ledger is not None:
                    await self._budget_ledger.claim_schema_retry("submit_check_result")
                raise
            receipt = await self.submit_check_result(agent_id=agent_id, result=parsed)
            return receipt.model_dump(mode="json")

        return submit_check_result

    def build_submit_review_tool(self, *, reviewer_agent_id: str) -> Any:
        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{reviewer_agent_id}.submit-review",
                name="submit_review",
                description="Submit structured ReviewIssue and targeted RepairTask records.",
                input_params=SubmitReviewInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_review(review: dict[str, object]) -> dict[str, object]:
            if self._budget_ledger is not None:
                await self._budget_ledger.claim_tool_call("submit_review")
            try:
                parsed = ReviewSubmission.model_validate(review)
            except ValidationError:
                if self._budget_ledger is not None:
                    await self._budget_ledger.claim_schema_retry("submit_review")
                raise
            receipt = await self.submit_review(
                reviewer_agent_id=reviewer_agent_id,
                review=parsed,
            )
            return receipt.model_dump(mode="json")

        return submit_review

    def build_submit_bound_review_tool(
        self,
        *,
        reviewer_agent_id: str,
        prompt_version: str,
    ) -> Any:
        """Build a review-content tool with immutable identity injected server-side."""

        if not prompt_version.strip():
            raise ValueError("bound review submission tool requires a prompt version")

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{reviewer_agent_id}.submit-bound-review",
                name="submit_review",
                description=(
                    "Submit ReviewIssue and RepairTask content only. Run identity, catalog, "
                    "Prompt and review version fields are injected automatically."
                ),
                input_params=SubmitBoundReviewInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_review(review: dict[str, object]) -> dict[str, object]:
            if self._budget_ledger is not None:
                await self._budget_ledger.claim_tool_call("submit_review")
            try:
                draft = BoundReviewDraft.model_validate(review)
                parsed = self._bind_review(
                    prompt_version=prompt_version,
                    draft=draft,
                )
            except ValidationError:
                if self._budget_ledger is not None:
                    await self._budget_ledger.claim_schema_retry("submit_review")
                raise
            latest = self.latest_review
            is_new_version = latest is None or parsed.review_version > latest.review_version
            if parsed.repair_tasks and is_new_version and self._budget_ledger is not None:
                claim_repair = getattr(self._budget_ledger, "claim_repair_round", None)
                if claim_repair is not None:
                    await claim_repair("agent_team.reviewer.repair")
            receipt = await self.submit_review(
                reviewer_agent_id=reviewer_agent_id,
                review=parsed,
            )
            return receipt.model_dump(mode="json")

        return submit_review

    def _bind_check_result(
        self,
        *,
        agent_id: str,
        prompt_version: str,
        draft: BoundCheckResultDraft,
    ) -> CheckResult:
        owner = self._grant_by_check.get(draft.check_id)
        if owner is None or owner[0] != agent_id:
            raise ValueError("check submission agent does not match its Run-bound grant")
        definition = self.check_catalog.get(draft.check_id)
        latest = self.latest_result(draft.check_id)
        next_version = 1 if latest is None else latest.submission_version + 1
        fact_refs_by_id: dict[str, FactEvidenceRef] = {}
        for fact_ref in draft.fact_evidence_refs:
            fact_refs_by_id.setdefault(fact_ref.evidence_id, fact_ref)
        declared_gap = bool(draft.missing_evidence) and draft.status in {
            CheckStatus.RISK,
            CheckStatus.NO_RISK,
        }
        candidate = CheckResult(
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_sha256=self.snapshot.snapshot_sha256,
            subject_id=self.snapshot.subject.subject_id,
            check_catalog_version=self.check_catalog.catalog_version,
            output_schema_version=definition.output_schema_version,
            task_id=owner[1],
            check_id=draft.check_id,
            status=CheckStatus.INCONCLUSIVE if declared_gap else draft.status,
            decision_summary=(
                EVIDENCE_GATE_INCONCLUSIVE_SUMMARY if declared_gap else draft.decision_summary
            ),
            risk_items=tuple(
                RiskItem(
                    risk_id=item.risk_id,
                    check_id=draft.check_id,
                    title=item.title,
                    status=CheckStatus.RISK,
                    risk_class=item.risk_class,
                    severity=item.severity,
                    conclusion=item.conclusion,
                    evidence_ids=item.evidence_ids,
                    confidence=item.confidence,
                )
                for item in (() if declared_gap else draft.risk_items)
            ),
            fact_evidence_refs=tuple(fact_refs_by_id.values()),
            missing_evidence=draft.missing_evidence,
            conflicts=draft.conflicts,
            confidence=draft.confidence,
            prompt_version=prompt_version,
            submission_version=next_version,
        )
        validate_check_result_scope(
            snapshot=self.snapshot,
            check_catalog=self.check_catalog,
            result=candidate,
        )
        candidate = downgrade_unsupported_check_conclusion(
            snapshot=self.snapshot,
            check_catalog=self.check_catalog,
            result=candidate,
        )
        if (
            latest is not None
            and candidate.model_copy(update={"submission_version": latest.submission_version})
            == latest
        ):
            return latest
        return candidate

    def _bind_review(
        self,
        *,
        prompt_version: str,
        draft: BoundReviewDraft,
    ) -> ReviewSubmission:
        latest = self.latest_review
        next_version = 1 if latest is None else latest.review_version + 1
        known_evidence = {item.evidence_id for item in self.snapshot.evidence}
        executable_repairs = tuple(
            repair
            for repair in draft.repair_tasks
            if set(repair.required_evidence) <= known_evidence
        )
        candidate = ReviewSubmission(
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_sha256=self.snapshot.snapshot_sha256,
            subject_id=self.snapshot.subject.subject_id,
            check_catalog_version=self.check_catalog.catalog_version,
            prompt_version=prompt_version,
            review_version=next_version,
            issues=draft.issues,
            repair_tasks=executable_repairs,
        )
        if (
            latest is not None
            and candidate.model_copy(update={"review_version": latest.review_version}) == latest
        ):
            return latest
        return candidate

    def _validate_result_scope(self, *, agent_id: str, result: CheckResult) -> None:
        validate_check_result_scope(
            snapshot=self.snapshot,
            check_catalog=self.check_catalog,
            result=result,
        )
        owner = self._grant_by_check.get(result.check_id)
        if owner != (agent_id, result.task_id):
            raise ValueError("check submission agent/task owner does not match its grant")

    def _validate_evidence_gate(self, result: CheckResult) -> None:
        validate_check_result_evidence_gate(
            snapshot=self.snapshot,
            check_catalog=self.check_catalog,
            result=result,
        )

    def _validate_review(
        self,
        *,
        reviewer_agent_id: str,
        review: ReviewSubmission,
    ) -> None:
        if reviewer_agent_id not in self._reviewer_agent_ids:
            raise ValueError("agent is not authorized as reviewer")
        if review.snapshot_id != self.snapshot.snapshot_id:
            raise ValueError("review references another snapshot")
        if review.snapshot_sha256 != self.snapshot.snapshot_sha256:
            raise ValueError("review snapshot hash does not match")
        if review.subject_id != self.snapshot.subject.subject_id:
            raise ValueError("review references another subject")
        if review.check_catalog_version != self.check_catalog.catalog_version:
            raise ValueError("review uses another CheckCatalog version")
        known_evidence = {item.evidence_id for item in self.snapshot.evidence}
        known_checks = set(self.check_catalog.check_ids)
        issue_by_id = {item.issue_id: item for item in review.issues}
        for issue in review.issues:
            if not issue.check_ids:
                raise ValueError("review issue must identify at least one check")
            unknown_checks = set(issue.check_ids) - known_checks
            if unknown_checks:
                raise ValueError(f"review references unknown checks: {sorted(unknown_checks)}")
            unknown_evidence = set(issue.evidence_ids) - known_evidence
            if unknown_evidence:
                raise ValueError(f"review references unknown Evidence: {sorted(unknown_evidence)}")
            if issue.target_agent is not None and issue.target_agent not in self._agent_ids:
                raise ValueError("review targets an unknown investigation agent")
        for repair in review.repair_tasks:
            targets = {issue_by_id[issue_id].target_agent for issue_id in repair.issue_ids}
            if any(target not in {None, repair.target_agent} for target in targets):
                raise ValueError("repair target does not match its ReviewIssue")
            if repair.target_agent not in self._agent_ids:
                raise ValueError("repair targets an unknown investigation agent")

    def _check_receipt(
        self,
        *,
        agent_id: str,
        result: CheckResult,
        digest: str,
        replay: bool,
    ) -> CheckSubmissionReceipt:
        return CheckSubmissionReceipt(
            run_id=self.run_id,
            snapshot_id=self.snapshot.snapshot_id,
            agent_id=agent_id,
            task_id=result.task_id,
            check_id=result.check_id,
            submission_version=result.submission_version,
            submission_sha256=digest,
            accepted=True,
            idempotent_replay=replay,
            accepted_at=self._clock(),
        )

    def _review_receipt(
        self,
        *,
        reviewer_agent_id: str,
        review: ReviewSubmission,
        digest: str,
        replay: bool,
    ) -> ReviewSubmissionReceipt:
        return ReviewSubmissionReceipt(
            run_id=self.run_id,
            snapshot_id=self.snapshot.snapshot_id,
            reviewer_agent_id=reviewer_agent_id,
            review_version=review.review_version,
            review_sha256=digest,
            accepted=True,
            idempotent_replay=replay,
            accepted_at=self._clock(),
        )


__all__ = [
    "BoundCheckResultDraft",
    "BoundReviewDraft",
    "BoundRiskItemDraft",
    "CheckSubmissionReceipt",
    "ReviewSubmission",
    "ReviewSubmissionReceipt",
    "SubmissionBlackboard",
    "SubmissionGrant",
    "SubmitBoundCheckResultInput",
    "SubmitBoundReviewInput",
]
