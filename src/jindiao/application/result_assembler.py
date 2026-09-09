"""Shared deterministic final-result assembly for all orchestration modes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast

from pydantic import JsonValue

from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.evidence import SourceStatus
from jindiao.contracts.execution import ExecutionCost, LayeredExecutionCost
from jindiao.contracts.investigation import CheckResult
from jindiao.contracts.report_inputs import ReviewedReportInputs
from jindiao.contracts.reporting import ReportStructure, ReportViewModel
from jindiao.contracts.results import (
    AgentInvestigationResult,
    ComparisonMetadata,
    DueDiligenceResult,
    EvaluationSummary,
    OrchestrationMode,
    RunMeta,
    RunStatus,
    SkillEvolutionStatus,
    SkillEvolutionSummary,
    SnapshotSummary,
)
from jindiao.investigation import (
    CHECK_CATALOG,
    validate_accepted_investigation_results,
)
from jindiao.orchestration.base import OrchestrationOutcome
from jindiao.reporting import MarkdownReportRenderer, ReportAssembler
from jindiao.reporting.catalog import REPORT_CATALOG
from jindiao.reporting.markdown import REPORT_RENDERER_VERSION
from jindiao.risk import QualityCalculator, RiskRuleEngine

from .context import RunContext


class ResultAssembler:
    """Apply quality, rules, view assembly and Markdown in a fixed order."""

    def __init__(
        self,
        *,
        rule_engine: RiskRuleEngine,
        quality_calculator: QualityCalculator | None = None,
        report_assembler: ReportAssembler | None = None,
        markdown_renderer: MarkdownReportRenderer | None = None,
    ) -> None:
        self._rule_engine = rule_engine
        self._quality = quality_calculator or QualityCalculator()
        self._reports = report_assembler or ReportAssembler()
        self._markdown = markdown_renderer or MarkdownReportRenderer()

    def prepare(
        self,
        *,
        context: RunContext,
        outcome: OrchestrationOutcome,
        snapshot: EnterpriseContextSnapshot | None = None,
        agent_results: tuple[AgentInvestigationResult, ...] = (),
    ) -> ReviewedReportInputs:
        """Validate and score internal facts without constructing a legacy result."""
        if not outcome.review_completed and not outcome.demo_partial_disclosure:
            raise ValueError("results cannot be assembled before review completes")
        checks = (
            validate_accepted_investigation_results(
                snapshot=snapshot,
                check_catalog=CHECK_CATALOG,
                agent_results=agent_results,
                allow_partial=bool(outcome.demo_partial_disclosure),
            )
            if snapshot is not None
            else ()
        )
        findings = (
            self._rule_engine.findings_from_check_results(checks)
            if snapshot is not None
            else outcome.findings
        )
        evidence = snapshot.evidence if snapshot is not None else outcome.evidence
        quality = self._quality.assess(
            coverage=outcome.coverage,
            findings=findings,
            evidence=evidence,
            review_issues=outcome.review_issues,
            report_as_of=context.report_as_of,
        )
        decision = self._rule_engine.evaluate(
            findings=findings,
            evidence=evidence,
            as_of_date=context.report_as_of,
            confidence=quality.decision_confidence,
            pending_review_items=quality.pending_review_items,
        )
        return ReviewedReportInputs(
            decision=decision,
            findings=findings,
            evidence=evidence,
            checks=checks,
            coverage=outcome.coverage,
            demo_partial_disclosure=outcome.demo_partial_disclosure,
            incomplete=bool(outcome.errors)
            or bool(outcome.demo_partial_disclosure)
            or any(
                item.status in {SourceStatus.CAPABILITY_ABSENT, SourceStatus.SOURCE_ERROR}
                for item in outcome.coverage.items
            )
            or any(item.status.value == "inconclusive" for item in checks),
        )

    def assemble(
        self,
        *,
        context: RunContext,
        outcome: OrchestrationOutcome,
        mode: OrchestrationMode,
        started_at: datetime,
        completed_at: datetime,
        skill_evolution: SkillEvolutionSummary | None = None,
        snapshot: EnterpriseContextSnapshot | None = None,
        agent_results: tuple[AgentInvestigationResult, ...] = (),
        investigation_cost: ExecutionCost | None = None,
        comparison_metadata: ComparisonMetadata | None = None,
        view_sink: Callable[[ReportViewModel], None] | None = None,
    ) -> DueDiligenceResult:
        if not outcome.review_completed:
            raise ValueError("results cannot be assembled before review completes")
        accepted_checks: tuple[CheckResult, ...] = ()
        result_agent_results = agent_results
        findings = outcome.findings
        evidence = outcome.evidence
        section_data = outcome.section_data
        if snapshot is not None:
            accepted_checks = validate_accepted_investigation_results(
                snapshot=snapshot,
                check_catalog=CHECK_CATALOG,
                agent_results=agent_results,
            )
            findings = self._rule_engine.findings_from_check_results(accepted_checks)
            evidence = snapshot.evidence
            section_data = self._snapshot_section_data(
                snapshot=snapshot,
                provided=outcome.section_data,
            )
            result_agent_results = (
                *snapshot.acquisition_agent_results,
                *agent_results,
            )
        quality = self._quality.assess(
            coverage=outcome.coverage,
            findings=findings,
            evidence=evidence,
            review_issues=outcome.review_issues,
            report_as_of=context.report_as_of,
        )
        if snapshot is None:
            decision = self._rule_engine.evaluate(
                findings=findings,
                evidence=evidence,
                as_of_date=context.report_as_of,
                confidence=quality.decision_confidence,
                pending_review_items=quality.pending_review_items,
            )
        else:
            decision = self._rule_engine.evaluate_check_results(
                accepted_check_results=accepted_checks,
                evidence=evidence,
                as_of_date=context.report_as_of,
                confidence=quality.decision_confidence,
                pending_review_items=quality.pending_review_items,
            )
        view = self._reports.assemble(
            subject=outcome.subject,
            decision=decision,
            coverage=outcome.coverage,
            findings=findings,
            evidence=evidence,
            generated_at=completed_at,
            section_data=section_data,
            frontend_overrides={
                "task-status": {
                    "mode": mode.value,
                    "duration_ms": max(0, int((completed_at - started_at).total_seconds() * 1000)),
                },
                "collaboration-evaluation": {
                    "agent_trace": [item.model_dump(mode="json") for item in outcome.agent_trace],
                    "collaboration": outcome.collaboration.model_dump(mode="json"),
                    "skill_versions": dict(context.skill_versions),
                },
            },
            check_results=accepted_checks,
        )
        markdown = self._markdown.render(view, policy=context.reporting_policy.policy)
        if view_sink is not None:
            view_sink(view)
        incomplete = bool(outcome.errors) or any(
            item.status in {SourceStatus.CAPABILITY_ABSENT, SourceStatus.SOURCE_ERROR}
            for item in outcome.coverage.items
        )
        if accepted_checks:
            incomplete = incomplete or any(
                item.status.value == "inconclusive" for item in accepted_checks
            )
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
        evaluation = EvaluationSummary(
            mode=mode,
            success=True,
            metrics={
                "coverage": outcome.coverage.ratio,
                "decision_confidence": quality.decision_confidence,
                "tool_calls": float(outcome.tool_calls),
            },
        )
        return DueDiligenceResult(
            meta=RunMeta(
                request_id=context.request_id,
                run_id=context.run_id,
                status=RunStatus.PARTIAL if incomplete else RunStatus.COMPLETED,
                mode=mode,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                scenario_snapshot_id=context.scenario_snapshot_id,
                is_mock=any(item.is_mock for item in evidence),
                degraded=any(item.source_status is SourceStatus.DEGRADED_MOCK for item in evidence),
                model_name=context.policy.model_name,
                rule_version=self._rule_engine.rule_version,
                skill_versions=dict(context.skill_versions),
                reporting_policy_sha256=context.reporting_policy.policy_sha256,
                report_renderer_version=REPORT_RENDERER_VERSION,
            ),
            subject=outcome.subject,
            decision=decision,
            risk_summary=view.risk_summary,
            coverage=outcome.coverage,
            sections=view.sections,
            findings=findings,
            evidence=evidence,
            agent_results=result_agent_results,
            report_structure=ReportStructure.from_catalog(REPORT_CATALOG),
            context_snapshot=(
                SnapshotSummary(
                    snapshot_id=snapshot.snapshot_id,
                    snapshot_sha256=snapshot.snapshot_sha256,
                    report_as_of=snapshot.report_as_of,
                    coverage=outcome.coverage,
                    unresolved_gaps=snapshot.unresolved_gaps,
                    unresolved_conflicts=snapshot.unresolved_conflicts,
                    shared_acquisition_cost=snapshot.shared_acquisition_cost,
                )
                if snapshot is not None
                else None
            ),
            execution_cost=LayeredExecutionCost(
                shared_acquisition_cost=(
                    snapshot.shared_acquisition_cost
                    if snapshot is not None
                    else ExecutionCost.zero()
                ),
                investigation_cost=investigation_cost or ExecutionCost.zero(),
            ),
            comparison_metadata=comparison_metadata,
            agent_trace=outcome.agent_trace,
            collaboration=outcome.collaboration,
            evaluation=evaluation,
            skill_evolution=skill_evolution
            or SkillEvolutionSummary(
                status=SkillEvolutionStatus.NOT_PROPOSED,
                active_version=context.skill_versions.get(
                    "feedback-evolved-reporting", "unversioned"
                ),
            ),
            report_markdown=markdown,
            errors=outcome.errors,
        )

    @staticmethod
    def _snapshot_section_data(
        *,
        snapshot: EnterpriseContextSnapshot,
        provided: dict[str, dict[str, JsonValue]],
    ) -> dict[str, dict[str, JsonValue]]:
        merged = {section_id: dict(values) for section_id, values in provided.items()}
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
        for module in REPORT_CATALOG.modules:
            section = merged.setdefault(module.module_id, {})
            raw_submodules = section.get("submodules")
            submodules = dict(raw_submodules) if isinstance(raw_submodules, dict) else {}
            for definition in module.submodules:
                context = snapshot.submodule(definition.submodule_id)
                context_evidence_ids = (
                    *context.evidence_ids,
                    *context.supplemental_evidence_ids,
                )
                source_items = tuple(
                    evidence_by_id[evidence_id]
                    for evidence_id in context_evidence_ids
                    if evidence_id in evidence_by_id
                )
                source_status = (
                    source_items[0].source_status.value
                    if source_items
                    else context.availability.value
                )
                source_tool = next(
                    (item.source_tool for item in source_items if item.source_tool),
                    None,
                )
                submodules[definition.submodule_id] = cast(
                    JsonValue,
                    {
                        "title": definition.title,
                        "availability": context.availability.value,
                        "completeness": context.completeness.value,
                        "source_status": source_status,
                        "source_tool": source_tool,
                        "facts": context.facts,
                        "records": [context.facts] if context.facts else [],
                        "evidence_ids": list(context.evidence_ids),
                        "supplemental_evidence_ids": list(context.supplemental_evidence_ids),
                        "unresolved_gap_ids": list(context.unresolved_gap_ids),
                        "conflict_evidence_ids": list(context.conflict_evidence_ids),
                    },
                )
            section["submodules"] = cast(JsonValue, submodules)
        return merged


__all__ = ["ResultAssembler"]
