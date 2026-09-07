from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageSummary, Evidence, SourceType
from jindiao.contracts.investigation import Finding, FindingStatus, RiskClass, Severity
from jindiao.contracts.reporting import (
    Decision,
    DecisionBand,
    ReportSection,
    ReportSectionStatus,
    RiskSummary,
    RuleHit,
)
from jindiao.contracts.results import (
    CollaborationSummary,
    DueDiligenceResult,
    EvaluationSummary,
    OrchestrationMode,
    RunMeta,
    RunStatus,
    SkillEvolutionStatus,
    SkillEvolutionSummary,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def valid_result_data() -> dict[str, object]:
    subject = ResolvedSubject(
        subject_id="mock:normal",
        company_name="示例科技有限公司",
        source=SubjectSource.MOCK,
        resolved_at=NOW,
    )
    evidence = Evidence(
        evidence_id="ev-1",
        claim="存在被执行记录",
        value={"amount": 100000},
        subject_id=subject.subject_id,
        source_type=SourceType.MOCK,
        source_record_id="judicial:1",
        queried_at=NOW,
        as_of_date=date(2026, 9, 3),
        confidence=1,
        is_mock=True,
        supports_fields=("judicial.executions",),
        raw_ref="mock://normal/v1/judicial.json#executions/0",
    )
    finding = Finding(
        finding_id="finding-1",
        subject_id=subject.subject_id,
        domain="judicial",
        claim="企业存在被执行记录",
        risk_class=RiskClass.ATTENTION,
        severity=Severity.HIGH,
        status=FindingStatus.ACCEPTED,
        evidence_ids=(evidence.evidence_id,),
    )
    rule_hit = RuleHit(
        rule_id="attention.execution",
        finding_id=finding.finding_id,
        evidence_ids=(evidence.evidence_id,),
        points=20,
        explanation="存在未结被执行记录",
    )
    return {
        "meta": RunMeta(
            request_id="req-1",
            run_id="run-1",
            status=RunStatus.COMPLETED,
            mode=OrchestrationMode.MULTI,
            started_at=NOW,
            completed_at=NOW,
            duration_ms=100,
            scenario_snapshot_id="normal:v1:abc",
            is_mock=True,
            degraded=False,
            model_name="test-model",
            rule_version="v1",
            skill_versions={},
        ),
        "subject": subject,
        "decision": Decision(
            band=DecisionBand.MANUAL_REVIEW,
            score=20,
            confidence=1,
            rule_version="v1",
            rule_hits=(rule_hit,),
            major_risk_finding_ids=(finding.finding_id,),
            pending_review_items=(),
            as_of_date=date(2026, 9, 3),
        ),
        "risk_summary": RiskSummary(
            admission_count=0,
            attention_count=1,
            non_risk_count=0,
            total_score=20,
            top_finding_ids=(finding.finding_id,),
        ),
        "coverage": CoverageSummary.from_items([]),
        "sections": [
            ReportSection(
                section_id="judicial-risk",
                title="司法风险",
                status=ReportSectionStatus.COMPLETE,
                coverage=1,
                finding_ids=(finding.finding_id,),
                evidence_ids=(evidence.evidence_id,),
            )
        ],
        "findings": [finding],
        "evidence": [evidence],
        "agent_trace": [],
        "collaboration": CollaborationSummary(
            agent_count=1,
            task_count=1,
            parallel_task_count=0,
            conflicts_detected=0,
            repairs_requested=0,
            repairs_completed=0,
        ),
        "evaluation": EvaluationSummary(
            mode=OrchestrationMode.MULTI,
            success=True,
            metrics={},
        ),
        "skill_evolution": SkillEvolutionSummary(
            status=SkillEvolutionStatus.NOT_PROPOSED,
            active_version="1.0.0",
        ),
        "report_markdown": "# 企业尽调报告\n",
        "errors": [],
    }


def test_result_rejects_unknown_evidence_referenced_by_finding() -> None:
    data = valid_result_data()
    findings = data["findings"]
    assert isinstance(findings, list)
    finding = findings[0]
    assert isinstance(finding, Finding)
    finding = finding.model_copy(update={"evidence_ids": ("missing",)})
    data["findings"] = [finding]

    with pytest.raises(ValidationError, match="unknown evidence"):
        DueDiligenceResult.model_validate(data)


def test_result_rejects_unknown_section_references() -> None:
    data = valid_result_data()
    sections = data["sections"]
    assert isinstance(sections, list)
    section = sections[0]
    assert isinstance(section, ReportSection)
    section = section.model_copy(update={"finding_ids": ("missing",)})
    data["sections"] = [section]

    with pytest.raises(ValidationError, match="unknown findings"):
        DueDiligenceResult.model_validate(data)


def test_result_rejects_cross_subject_evidence() -> None:
    data = valid_result_data()
    evidence_items = data["evidence"]
    assert isinstance(evidence_items, list)
    evidence = evidence_items[0]
    assert isinstance(evidence, Evidence)
    evidence = evidence.model_copy(update={"subject_id": "mock:other"})
    data["evidence"] = [evidence]

    with pytest.raises(ValidationError, match="subject_id"):
        DueDiligenceResult.model_validate(data)


def test_existing_contracts_reject_unbacked_and_mismarked_facts() -> None:
    with pytest.raises(ValidationError, match="accepted findings require evidence"):
        Finding(
            finding_id="finding-1",
            subject_id="mock:normal",
            domain="judicial",
            claim="无证据结论",
            risk_class=RiskClass.ATTENTION,
            severity=Severity.HIGH,
            status=FindingStatus.ACCEPTED,
            evidence_ids=(),
        )

    with pytest.raises(ValidationError, match="mock evidence must set is_mock"):
        Evidence(
            evidence_id="ev-1",
            claim="错误 Mock 标记",
            value=True,
            subject_id="mock:normal",
            source_type=SourceType.MOCK,
            queried_at=NOW,
            confidence=1,
            is_mock=False,
            supports_fields=("risk.flag",),
            raw_ref="mock://normal/v1/risk.json#flag",
        )
