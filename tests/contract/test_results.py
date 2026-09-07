from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageSummary, Evidence, SourceType
from jindiao.contracts.execution import ExecutionCost, LayeredExecutionCost
from jindiao.contracts.investigation import Finding, FindingStatus, RiskClass, Severity
from jindiao.contracts.reporting import (
    Decision,
    DecisionBand,
    ReportSection,
    ReportSectionStatus,
    ReportStructure,
    RiskSummary,
)
from jindiao.contracts.results import (
    AgentStatus,
    AgentTrace,
    CollaborationSummary,
    ComparisonMetadata,
    DueDiligenceRequest,
    DueDiligenceResult,
    EvaluationSummary,
    OrchestrationMode,
    RunMeta,
    RunStatus,
    SkillEvolutionStatus,
    SkillEvolutionSummary,
    SnapshotSummary,
)
from jindiao.reporting.catalog import REPORT_CATALOG

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_due_diligence_request_has_safe_defaults() -> None:
    request = DueDiligenceRequest(
        enterprise=EnterpriseInput(company_name="示例科技有限公司"),
        scenario_id="normal-enterprise",
    )

    assert request.language == "zh-CN"
    assert request.allow_degraded_mock is False
    assert request.report_as_of is None


def test_result_contains_every_frontend_and_report_field() -> None:
    subject = ResolvedSubject(
        subject_id="mock:normal-enterprise",
        company_name="示例科技有限公司",
        source=SubjectSource.MOCK,
        resolved_at=NOW,
    )
    evidence = Evidence(
        evidence_id="ev-1",
        claim="企业登记状态",
        value="存续",
        subject_id=subject.subject_id,
        source_type=SourceType.MOCK,
        source_tool=None,
        source_record_id="company:registration_status",
        queried_at=NOW,
        as_of_date=date(2026, 9, 3),
        confidence=1,
        is_mock=True,
        supports_fields=("company.registration_status",),
        raw_ref="mock://normal-enterprise/v1/company.json#registration_status",
    )
    finding = Finding(
        finding_id="finding-1",
        subject_id=subject.subject_id,
        domain="identity",
        claim="企业处于存续状态",
        value="存续",
        risk_class=RiskClass.NON_RISK,
        severity=Severity.INFO,
        status=FindingStatus.ACCEPTED,
        evidence_ids=(evidence.evidence_id,),
    )
    decision = Decision(
        band=DecisionBand.PASS,
        score=0,
        confidence=1,
        rule_version="v1",
        rule_hits=(),
        major_risk_finding_ids=(),
        pending_review_items=(),
        as_of_date=date(2026, 9, 3),
    )
    risk_summary = RiskSummary(
        admission_count=0,
        attention_count=0,
        non_risk_count=1,
        total_score=0,
        top_finding_ids=(),
    )
    section = ReportSection(
        section_id="company-profile",
        title="企业基本信息",
        status=ReportSectionStatus.COMPLETE,
        coverage=1,
        finding_ids=(finding.finding_id,),
        evidence_ids=(evidence.evidence_id,),
        data={"registration_status": "存续"},
    )
    result = DueDiligenceResult(
        meta=RunMeta(
            request_id="req-1",
            run_id="run-1",
            status=RunStatus.COMPLETED,
            mode=OrchestrationMode.MULTI,
            started_at=NOW,
            completed_at=NOW,
            duration_ms=100,
            scenario_snapshot_id="normal-enterprise:v1:abc",
            is_mock=True,
            degraded=False,
            model_name="test-model",
            rule_version="v1",
            skill_versions={"reporting": "1.0.0"},
        ),
        subject=subject,
        decision=decision,
        risk_summary=risk_summary,
        coverage=CoverageSummary.from_items([]),
        sections=(section,),
        findings=(finding,),
        evidence=(evidence,),
        agent_results=(),
        report_structure=ReportStructure.from_catalog(REPORT_CATALOG),
        context_snapshot=SnapshotSummary(
            snapshot_id="snapshot:result-contract",
            snapshot_sha256="a" * 64,
            report_as_of=date(2026, 9, 3),
            coverage=CoverageSummary.from_items([]),
            unresolved_gaps=(),
            unresolved_conflicts=(),
            shared_acquisition_cost=ExecutionCost.zero(),
        ),
        execution_cost=LayeredExecutionCost(
            shared_acquisition_cost=ExecutionCost.zero(),
            investigation_cost=ExecutionCost.zero(),
        ),
        comparison_metadata=ComparisonMetadata(
            formal_agent_run=False,
            paired_comparison=False,
            topology=OrchestrationMode.MULTI,
            prompt_core_version="investigation-core-v1",
            prompt_core_sha256="b" * 64,
            role_prompt_versions={"leader": "leader-v1"},
            check_catalog_version="due-diligence-check-catalog-v1",
            check_catalog_sha256="c" * 64,
            rule_version="v1",
            evaluator_version="quality-v1",
        ),
        agent_trace=(
            AgentTrace(
                agent_id="governance-agent",
                role="governance",
                task_ids=("identity",),
                status=AgentStatus.COMPLETED,
                started_at=NOW,
                completed_at=NOW,
                duration_ms=50,
                evidence_count=1,
                error_codes=(),
            ),
        ),
        collaboration=CollaborationSummary(
            agent_count=1,
            task_count=1,
            parallel_task_count=0,
            conflicts_detected=0,
            repairs_requested=0,
            repairs_completed=0,
        ),
        evaluation=EvaluationSummary(
            mode=OrchestrationMode.MULTI,
            success=True,
            metrics={"coverage": 1.0},
        ),
        skill_evolution=SkillEvolutionSummary(
            status=SkillEvolutionStatus.NOT_PROPOSED,
            active_version="1.0.0",
        ),
        report_markdown="# 企业尽调报告\n",
        errors=(),
    )

    expected_keys = {
        "meta",
        "subject",
        "decision",
        "risk_summary",
        "coverage",
        "sections",
        "findings",
        "evidence",
        "agent_results",
        "report_structure",
        "context_snapshot",
        "execution_cost",
        "comparison_metadata",
        "agent_trace",
        "collaboration",
        "evaluation",
        "skill_evolution",
        "report_markdown",
        "errors",
    }
    assert set(result.model_dump(mode="json")) == expected_keys
    assert result.report_structure.module_count == 8
    assert result.report_structure.submodule_count == 48
    assert DueDiligenceResult.model_validate_json(result.model_dump_json()) == result


def test_completed_result_meta_requires_completion_fields() -> None:
    with pytest.raises(ValidationError):
        RunMeta(
            request_id="req-1",
            run_id="run-1",
            status=RunStatus.COMPLETED,
            mode=OrchestrationMode.MULTI,
            started_at=NOW,
            completed_at=None,
            duration_ms=None,
            is_mock=False,
            degraded=False,
            model_name="test-model",
            rule_version="v1",
            skill_versions={},
        )
