"""Request, run metadata, and complete result contracts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import (
    AwareDatetime,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from jindiao.security import redact_json

from .base import ContractModel
from .business import BusinessContext
from .entities import EnterpriseInput, ResolvedSubject
from .errors import ErrorRecord
from .evidence import CoverageSummary, Evidence
from .execution import ExecutionCost, LayeredExecutionCost
from .investigation import CheckResult, FactEvidenceRef, Finding, RiskItem
from .reporting import Decision, ReportSection, ReportStructure, RiskSummary


class OrchestrationMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class RunStatus(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentResultPhase(StrEnum):
    ACQUISITION = "acquisition"
    INVESTIGATION = "investigation"


class AgentInvestigationResult(ContractModel):
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    phase: AgentResultPhase
    status: AgentStatus
    task_ids: tuple[str, ...]
    check_results: tuple[CheckResult, ...] = ()
    risk_items: tuple[RiskItem, ...] = ()
    fact_evidence_refs: tuple[FactEvidenceRef, ...] = ()
    prompt_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_aggregation(self) -> AgentInvestigationResult:
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("agent result task ids must be unique")

        check_ids = [item.check_id for item in self.check_results]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("agent result check ids must be unique")
        unknown_tasks = {item.task_id for item in self.check_results} - set(self.task_ids)
        if unknown_tasks:
            raise ValueError(f"check results reference unknown tasks: {sorted(unknown_tasks)}")

        risk_ids = [item.risk_id for item in self.risk_items]
        if len(risk_ids) != len(set(risk_ids)):
            raise ValueError("agent result risk ids must be unique")
        fact_reference_keys = [
            (item.evidence_id, item.fact_path) for item in self.fact_evidence_refs
        ]
        if len(fact_reference_keys) != len(set(fact_reference_keys)):
            raise ValueError("agent result evidence references must be unique")

        nested_risk_ids = {
            risk.risk_id for check in self.check_results for risk in check.risk_items
        }
        if nested_risk_ids != set(risk_ids):
            raise ValueError("agent risk items must exactly aggregate check result risks")
        nested_fact_refs = {
            (fact.evidence_id, fact.fact_path)
            for check in self.check_results
            for fact in check.fact_evidence_refs
        }
        top_level_fact_refs = {
            (fact.evidence_id, fact.fact_path) for fact in self.fact_evidence_refs
        }
        if self.phase is AgentResultPhase.INVESTIGATION and (
            nested_fact_refs != top_level_fact_refs
        ):
            raise ValueError(
                "investigation agent fact evidence must exactly aggregate check results"
            )
        if self.phase is AgentResultPhase.ACQUISITION and self.check_results:
            raise ValueError("acquisition agent cannot declare investigation check results")
        return self

    def require_known_evidence(self, known_evidence_ids: set[str] | frozenset[str]) -> None:
        referenced = {item.evidence_id for item in self.fact_evidence_refs}
        referenced.update(
            evidence_id for risk in self.risk_items for evidence_id in risk.evidence_ids
        )
        unknown = referenced - known_evidence_ids
        if unknown:
            raise ValueError(f"agent result references unknown Evidence: {sorted(unknown)}")


class SkillEvolutionStatus(StrEnum):
    NOT_PROPOSED = "not_proposed"
    CANDIDATE = "candidate"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTIVATED = "activated"
    REJECTED = "rejected"


class SkillEvolutionFeedback(ContractModel):
    source: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class DueDiligenceRequest(ContractModel):
    enterprise: EnterpriseInput
    business_context: BusinessContext = Field(default_factory=BusinessContext)
    report_as_of: date | None = None
    language: str = Field(default="zh-CN", min_length=2)
    scenario_id: str | None = None
    allow_degraded_mock: bool = False
    skill_feedback: SkillEvolutionFeedback | None = Field(default=None, deprecated=True)


class RunMeta(ContractModel):
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: RunStatus
    mode: OrchestrationMode
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    scenario_snapshot_id: str | None = None
    is_mock: bool
    degraded: bool
    model_name: str
    rule_version: str = Field(min_length=1)
    skill_versions: dict[str, str]
    reporting_policy_sha256: str | None = None
    report_renderer_version: str | None = None
    report_replay_available: bool = False
    report_replay_reason: str | None = None

    @model_validator(mode="after")
    def completed_runs_require_completion_fields(self) -> RunMeta:
        if self.status is RunStatus.COMPLETED and (
            self.completed_at is None or self.duration_ms is None
        ):
            raise ValueError("completed runs require completed_at and duration_ms")
        return self


class AgentTrace(ContractModel):
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    task_ids: tuple[str, ...]
    status: AgentStatus
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    evidence_count: int = Field(ge=0)
    error_codes: tuple[str, ...]


class CollaborationSummary(ContractModel):
    agent_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    parallel_task_count: int = Field(ge=0)
    conflicts_detected: int = Field(ge=0)
    repairs_requested: int = Field(ge=0)
    repairs_completed: int = Field(ge=0)


class EvaluationSummary(ContractModel):
    mode: OrchestrationMode
    success: bool
    metrics: dict[str, float]


class SkillEvolutionSummary(ContractModel):
    status: SkillEvolutionStatus
    active_version: str = Field(min_length=1)
    candidate_version: str | None = None
    change_summary: str | None = None
    reason_codes: tuple[str, ...] = ()


class SnapshotSummary(ContractModel):
    """Stable public identity and coverage summary of the frozen input."""

    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_as_of: date
    coverage: CoverageSummary
    unresolved_gaps: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    shared_acquisition_cost: ExecutionCost

    @model_validator(mode="after")
    def validate_summary_sets(self) -> SnapshotSummary:
        if len(self.unresolved_gaps) != len(set(self.unresolved_gaps)):
            raise ValueError("snapshot summary gaps must be unique")
        if len(self.unresolved_conflicts) != len(set(self.unresolved_conflicts)):
            raise ValueError("snapshot summary conflicts must be unique")
        return self


class ComparisonMetadata(ContractModel):
    """Versions and fairness markers needed to interpret one result arm."""

    formal_agent_run: bool
    paired_comparison: bool = False
    comparison_pair_id: str | None = Field(default=None, min_length=1)
    topology: OrchestrationMode
    prompt_core_version: str = Field(min_length=1)
    prompt_core_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role_prompt_versions: dict[str, str]
    check_catalog_version: str = Field(min_length=1)
    check_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_version: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    reporting_policy_sha256: str | None = None
    report_renderer_version: str | None = None
    gap_mapping_version: str | None = None

    @model_validator(mode="after")
    def validate_role_prompt_versions(self) -> ComparisonMetadata:
        if any(
            not role.strip() or not version.strip()
            for role, version in self.role_prompt_versions.items()
        ):
            raise ValueError("comparison role prompt names and versions cannot be empty")
        if self.paired_comparison and self.comparison_pair_id is None:
            raise ValueError("paired comparison metadata requires comparison_pair_id")
        return self


def _default_report_structure() -> ReportStructure:
    from jindiao.reporting.catalog import REPORT_CATALOG

    return ReportStructure.from_catalog(REPORT_CATALOG)


def _zero_layered_execution_cost() -> LayeredExecutionCost:
    return LayeredExecutionCost(
        shared_acquisition_cost=ExecutionCost.zero(),
        investigation_cost=ExecutionCost.zero(),
    )


class DueDiligenceResult(ContractModel):
    meta: RunMeta
    subject: ResolvedSubject
    decision: Decision
    risk_summary: RiskSummary
    coverage: CoverageSummary
    sections: tuple[ReportSection, ...]
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    agent_results: tuple[AgentInvestigationResult, ...] = ()
    report_structure: ReportStructure = Field(default_factory=_default_report_structure)
    context_snapshot: SnapshotSummary | None = None
    execution_cost: LayeredExecutionCost = Field(default_factory=_zero_layered_execution_cost)
    comparison_metadata: ComparisonMetadata | None = None
    agent_trace: tuple[AgentTrace, ...]
    collaboration: CollaborationSummary
    evaluation: EvaluationSummary
    skill_evolution: SkillEvolutionSummary
    report_markdown: str
    errors: tuple[ErrorRecord, ...]

    @model_serializer(mode="wrap")
    def redact_public_serialization(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> object:
        """Make every JSON/SSE/benchmark serialization cross-field safe."""

        return redact_json(handler(self))

    @model_validator(mode="after")
    def completed_result_requires_report(self) -> DueDiligenceResult:
        if self.meta.status is RunStatus.COMPLETED and not self.report_markdown.strip():
            raise ValueError("completed result requires report_markdown")
        return self

    @model_validator(mode="after")
    def validate_internal_references(self) -> DueDiligenceResult:
        evidence_ids = [item.evidence_id for item in self.evidence]
        finding_ids = [item.finding_id for item in self.findings]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding ids must be unique")

        evidence_id_set = set(evidence_ids)
        finding_id_set = set(finding_ids)
        subject_id = self.subject.subject_id
        if any(item.subject_id != subject_id for item in self.evidence) or any(
            item.subject_id != subject_id for item in self.findings
        ):
            raise ValueError("all evidence and findings must use the resolved subject_id")

        for finding in self.findings:
            unknown = set(finding.evidence_ids) - evidence_id_set
            if unknown:
                raise ValueError(f"finding references unknown evidence: {sorted(unknown)}")

        for section in self.sections:
            unknown_findings = set(section.finding_ids) - finding_id_set
            unknown_evidence = set(section.evidence_ids) - evidence_id_set
            if unknown_findings:
                raise ValueError(f"section references unknown findings: {sorted(unknown_findings)}")
            if unknown_evidence:
                raise ValueError(f"section references unknown evidence: {sorted(unknown_evidence)}")

        for hit in self.decision.rule_hits:
            if hit.finding_id not in finding_id_set:
                raise ValueError(f"rule hit references unknown finding: {hit.finding_id}")
            unknown = set(hit.evidence_ids) - evidence_id_set
            if unknown:
                raise ValueError(f"rule hit references unknown evidence: {sorted(unknown)}")

        summary_references = {
            *self.decision.major_risk_finding_ids,
            *self.risk_summary.top_finding_ids,
        }
        unknown_summary_findings = summary_references - finding_id_set
        if unknown_summary_findings:
            raise ValueError(
                f"decision references unknown findings: {sorted(unknown_summary_findings)}"
            )

        agent_ids = [item.agent_id for item in self.agent_results]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent result ids must be unique")
        for agent_result in self.agent_results:
            agent_result.require_known_evidence(evidence_id_set)
            wrong_subject_checks = {
                item.check_id
                for item in agent_result.check_results
                if item.subject_id != subject_id
            }
            if wrong_subject_checks:
                raise ValueError(
                    "agent results contain checks for another subject: "
                    f"{sorted(wrong_subject_checks)}"
                )

        if self.report_structure.module_count != 8:
            raise ValueError("result report structure must contain exactly 8 modules")
        if self.report_structure.submodule_count != 48:
            raise ValueError("result report structure must contain exactly 48 submodules")

        if self.context_snapshot is not None:
            if (
                self.context_snapshot.shared_acquisition_cost
                != self.execution_cost.shared_acquisition_cost
            ):
                raise ValueError(
                    "snapshot and layered execution costs disagree on shared acquisition"
                )
            for agent_result in self.agent_results:
                mismatched_checks = {
                    item.check_id
                    for item in agent_result.check_results
                    if item.snapshot_id != self.context_snapshot.snapshot_id
                    or item.snapshot_sha256 != self.context_snapshot.snapshot_sha256
                }
                if mismatched_checks:
                    raise ValueError(
                        f"agent results reference a different snapshot: {sorted(mismatched_checks)}"
                    )

        if self.comparison_metadata is not None:
            if self.comparison_metadata.topology is not self.meta.mode:
                raise ValueError("comparison topology must match result mode")
            if self.comparison_metadata.rule_version != self.meta.rule_version:
                raise ValueError("comparison rule version must match result metadata")
        return self


__all__ = [
    "AgentInvestigationResult",
    "AgentResultPhase",
    "AgentStatus",
    "AgentTrace",
    "CollaborationSummary",
    "ComparisonMetadata",
    "DueDiligenceRequest",
    "DueDiligenceResult",
    "EvaluationSummary",
    "OrchestrationMode",
    "RunMeta",
    "RunStatus",
    "SkillEvolutionFeedback",
    "SkillEvolutionStatus",
    "SkillEvolutionSummary",
    "SnapshotSummary",
]
