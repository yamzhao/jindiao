"""Investigation planning, finding, and review contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .base import ContractModel


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FindingStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNCONFIRMED = "unconfirmed"


class RiskClass(StrEnum):
    ADMISSION = "admission"
    ATTENTION = "attention"
    NON_RISK = "non_risk"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MissingDataPolicy(StrEnum):
    INCONCLUSIVE = "inconclusive"


class CheckStatus(StrEnum):
    RISK = "risk"
    NO_RISK = "no_risk"
    INCONCLUSIVE = "inconclusive"


class FactEvidenceRef(ContractModel):
    evidence_id: str = Field(min_length=1)
    fact_path: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class RiskItem(ContractModel):
    risk_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: CheckStatus
    risk_class: RiskClass
    severity: Severity
    conclusion: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_risk(self) -> RiskItem:
        if self.status is not CheckStatus.RISK:
            raise ValueError("risk item status must be risk")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("risk item evidence ids must be unique")
        return self


class CheckResult(ContractModel):
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_id: str = Field(min_length=1)
    check_catalog_version: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    status: CheckStatus
    decision_summary: str = Field(min_length=1)
    risk_items: tuple[RiskItem, ...] = ()
    fact_evidence_refs: tuple[FactEvidenceRef, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    prompt_version: str = Field(min_length=1)
    submission_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_check_result(self) -> CheckResult:
        if any(item.check_id != self.check_id for item in self.risk_items):
            raise ValueError("risk item check_id must match its check result")

        risk_ids = [item.risk_id for item in self.risk_items]
        if len(risk_ids) != len(set(risk_ids)):
            raise ValueError("check result risk ids must be unique")
        evidence_ids = [item.evidence_id for item in self.fact_evidence_refs]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("check result evidence references must be unique")

        if self.status is CheckStatus.RISK and not self.risk_items:
            raise ValueError("risk check requires at least one risk item")
        if self.status is not CheckStatus.RISK and self.risk_items:
            raise ValueError("non-risk check cannot declare risk items")
        if self.status is CheckStatus.NO_RISK and self.missing_evidence:
            raise ValueError("no_risk check cannot declare missing evidence")

        known_evidence_ids = set(evidence_ids)
        for risk in self.risk_items:
            unknown = set(risk.evidence_ids) - known_evidence_ids
            if unknown:
                raise ValueError(f"risk item references unknown fact evidence: {sorted(unknown)}")
        return self


class CheckTimeWindow(ContractModel):
    mode: Literal["point_in_time", "lookback_years"]
    lookback_years: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def validate_window(self) -> CheckTimeWindow:
        if self.mode == "lookback_years" and self.lookback_years is None:
            raise ValueError("lookback_years mode requires lookback_years")
        if self.mode == "point_in_time" and self.lookback_years is not None:
            raise ValueError("point_in_time mode cannot declare lookback_years")
        return self


class CheckEvidenceRequirements(ContractModel):
    minimum_evidence_count: int = Field(default=1, ge=1)
    require_all_required_submodules: bool = True
    allow_public_web_only_for_no_risk: bool = False


class DueDiligenceCheckDefinition(ContractModel):
    check_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owner_role: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    required_submodule_ids: tuple[str, ...] = Field(min_length=1)
    optional_submodule_ids: tuple[str, ...] = ()
    prompt_template_id: str = Field(min_length=1)
    evidence_requirements: CheckEvidenceRequirements
    time_window: CheckTimeWindow
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.INCONCLUSIVE
    severity_policy: str = Field(min_length=1)
    report_section_ids: tuple[str, ...] = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)
    enabled: bool = True
    order: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_submodule_sets(self) -> DueDiligenceCheckDefinition:
        required = self.required_submodule_ids
        optional = self.optional_submodule_ids
        if len(required) != len(set(required)) or len(optional) != len(set(optional)):
            raise ValueError("check submodule ids must be unique")
        overlap = set(required) & set(optional)
        if overlap:
            raise ValueError(f"required and optional submodules overlap: {sorted(overlap)}")
        if len(self.report_section_ids) != len(set(self.report_section_ids)):
            raise ValueError("check report section ids must be unique")
        return self


class DueDiligenceCheckCatalog(ContractModel):
    schema_version: Literal[1]
    catalog_version: str = Field(min_length=1)
    acquisition_catalog_version: str = Field(min_length=1)
    checks: tuple[DueDiligenceCheckDefinition, ...] = Field(min_length=1)

    @property
    def check_ids(self) -> tuple[str, ...]:
        return tuple(check.check_id for check in self.checks)

    def get(self, check_id: str) -> DueDiligenceCheckDefinition:
        for check in self.checks:
            if check.check_id == check_id:
                return check
        raise KeyError(check_id)

    @model_validator(mode="after")
    def validate_check_identity(self) -> DueDiligenceCheckCatalog:
        if len(self.check_ids) != len(set(self.check_ids)):
            raise ValueError("due-diligence check ids must be unique")
        orders = [check.order for check in self.checks]
        if len(orders) != len(set(orders)):
            raise ValueError("due-diligence check order values must be unique")
        return self


class InvestigationTask(ContractModel):
    task_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    assigned_agent: str = Field(min_length=1)
    capability: str | None = None
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    expected_outputs: tuple[str, ...] = Field(min_length=1)
    dependencies: tuple[str, ...] = ()
    status: TaskStatus = TaskStatus.PENDING
    skipped_reason: str | None = None

    @model_validator(mode="after")
    def require_skip_reason(self) -> InvestigationTask:
        if self.status is TaskStatus.SKIPPED and not self.skipped_reason:
            raise ValueError("skipped tasks require skipped_reason")
        return self


class InvestigationPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    tasks: tuple[InvestigationTask, ...] = Field(min_length=1)
    max_tool_calls: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_task_graph_references(self) -> InvestigationPlan:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task ids must be unique")
        known_ids = set(task_ids)
        for task in self.tasks:
            if task.task_id in task.dependencies:
                raise ValueError("task cannot depend on itself")
            unknown = set(task.dependencies) - known_ids
            if unknown:
                raise ValueError(f"unknown task dependencies: {sorted(unknown)}")
        return self


class Finding(ContractModel):
    finding_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    value: JsonValue = None
    risk_class: RiskClass
    severity: Severity
    status: FindingStatus = FindingStatus.PROPOSED
    evidence_ids: tuple[str, ...] = ()
    occurred_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def accepted_findings_require_evidence(self) -> Finding:
        if self.status is FindingStatus.ACCEPTED and not self.evidence_ids:
            raise ValueError("accepted findings require evidence")
        return self


class ReviewIssue(ContractModel):
    issue_id: str = Field(min_length=1)
    issue_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    finding_ids: tuple[str, ...] = ()
    check_ids: tuple[str, ...] = ()
    submission_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    target_agent: str | None = None
    resolved: bool = False


class RepairTask(ContractModel):
    repair_id: str = Field(min_length=1)
    issue_ids: tuple[str, ...] = Field(min_length=1)
    target_agent: str = Field(min_length=1)
    requested_fields: tuple[str, ...] = Field(min_length=1)
    required_evidence: tuple[str, ...] = Field(min_length=1)
    attempt: int = Field(ge=1)
    max_attempts: int = Field(ge=1)

    @model_validator(mode="after")
    def enforce_attempt_budget(self) -> RepairTask:
        if self.attempt > self.max_attempts:
            raise ValueError("repair attempt exceeds max_attempts")
        return self


class ReviewDecision(ContractModel):
    accepted_finding_ids: tuple[str, ...]
    rejected_finding_ids: tuple[str, ...]
    unresolved_issue_ids: tuple[str, ...]
    reviewed_at: AwareDatetime

    @model_validator(mode="after")
    def keep_finding_sets_disjoint(self) -> ReviewDecision:
        overlap = set(self.accepted_finding_ids) & set(self.rejected_finding_ids)
        if overlap:
            raise ValueError(f"accepted and rejected findings overlap: {sorted(overlap)}")
        return self


__all__ = [
    "CheckEvidenceRequirements",
    "CheckResult",
    "CheckStatus",
    "CheckTimeWindow",
    "DueDiligenceCheckCatalog",
    "DueDiligenceCheckDefinition",
    "FactEvidenceRef",
    "Finding",
    "FindingStatus",
    "InvestigationPlan",
    "InvestigationTask",
    "MissingDataPolicy",
    "RepairTask",
    "ReviewDecision",
    "ReviewIssue",
    "RiskClass",
    "RiskItem",
    "Severity",
    "TaskStatus",
]
