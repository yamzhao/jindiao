from __future__ import annotations

import pytest

from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    FactEvidenceRef,
    RiskClass,
    RiskItem,
    Severity,
)
from jindiao.contracts.reporting import ReportStructure
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.reporting.catalog import REPORT_CATALOG


def test_investigation_agent_result_aggregates_risks_and_fact_evidence() -> None:
    fact = FactEvidenceRef(
        evidence_id="ev-profit-1",
        fact_path="operations.financial_summary.net_profit",
        summary="净利润连续三期下降",
    )
    risk = RiskItem(
        risk_id="risk-profitability-decline",
        check_id="profitability-decline",
        title="盈利能力持续下滑",
        status=CheckStatus.RISK,
        risk_class=RiskClass.ATTENTION,
        severity=Severity.HIGH,
        conclusion="最近三期净利润持续下降。",
        evidence_ids=(fact.evidence_id,),
        confidence=0.91,
    )
    check = CheckResult(
        snapshot_id="snapshot:test",
        snapshot_sha256="a" * 64,
        subject_id="tyc:123",
        check_catalog_version="due-diligence-check-catalog-v1",
        output_schema_version="check-result-v1",
        task_id="check-task:profitability-decline",
        check_id="profitability-decline",
        status=CheckStatus.RISK,
        decision_summary="多期利润趋势满足下滑核查规则。",
        risk_items=(risk,),
        fact_evidence_refs=(fact,),
        confidence=0.91,
        prompt_version="investigation-core-v1",
        submission_version=1,
    )

    result = AgentInvestigationResult(
        agent_id="financial-operations-agent",
        role="financial-operations",
        phase=AgentResultPhase.INVESTIGATION,
        status=AgentStatus.COMPLETED,
        task_ids=(check.task_id,),
        check_results=(check,),
        risk_items=(risk,),
        fact_evidence_refs=(fact,),
        prompt_version="investigation-core-v1",
    )

    result.require_known_evidence({"ev-profit-1"})
    with pytest.raises(ValueError, match="unknown Evidence"):
        result.require_known_evidence(set())


def test_agent_result_allows_one_evidence_to_support_distinct_check_fact_paths() -> None:
    shared_registration = FactEvidenceRef(
        evidence_id="ev-registration",
        fact_path="governance.registration.status",
        summary="工商登记状态为存续",
    )
    shared_change = FactEvidenceRef(
        evidence_id="ev-registration",
        fact_path="governance.registration.change_baseline",
        summary="同一登记快照作为变更核查基线",
    )
    checks = tuple(
        CheckResult(
            snapshot_id="snapshot:test",
            snapshot_sha256="a" * 64,
            subject_id="tyc:123",
            check_catalog_version="due-diligence-check-catalog-v1",
            output_schema_version="check-result-v1",
            task_id=f"check:{check_id}",
            check_id=check_id,
            status=CheckStatus.INCONCLUSIVE,
            decision_summary="共享证据支持不同事实路径。",
            fact_evidence_refs=(fact,),
            confidence=0.5,
            prompt_version="investigation-core-v1",
            submission_version=1,
        )
        for check_id, fact in (
            ("registration-status-normal", shared_registration),
            ("registration-change-anomaly", shared_change),
        )
    )

    result = AgentInvestigationResult(
        agent_id="single-investigator",
        role="single-investigator",
        phase=AgentResultPhase.INVESTIGATION,
        status=AgentStatus.COMPLETED,
        task_ids=tuple(item.task_id for item in checks),
        check_results=checks,
        fact_evidence_refs=(shared_registration, shared_change),
        prompt_version="investigation-core-v1+single-investigator-v1",
    )

    assert result.fact_evidence_refs == (shared_registration, shared_change)


def test_no_risk_check_cannot_hide_missing_evidence() -> None:
    with pytest.raises(ValueError, match="no_risk check cannot declare missing evidence"):
        CheckResult(
            snapshot_id="snapshot:test",
            snapshot_sha256="a" * 64,
            subject_id="tyc:123",
            check_catalog_version="due-diligence-check-catalog-v1",
            output_schema_version="check-result-v1",
            task_id="check-task:registration-status-normal",
            check_id="registration-status-normal",
            status=CheckStatus.NO_RISK,
            decision_summary="工商状态正常。",
            risk_items=(),
            fact_evidence_refs=(),
            missing_evidence=("registration",),
            confidence=0.5,
            prompt_version="investigation-core-v1",
            submission_version=1,
        )


def test_acquisition_agent_can_contribute_facts_without_risk_items() -> None:
    fact = FactEvidenceRef(
        evidence_id="ev-annual-social-security",
        fact_path="operations.annual_reports.social_security",
        summary="2025 年报披露养老保险参保人数 54 人。",
    )

    result = AgentInvestigationResult(
        agent_id="deepsearch-agent",
        role="supplemental-evidence",
        phase=AgentResultPhase.ACQUISITION,
        status=AgentStatus.COMPLETED,
        task_ids=("supplement:annual-reports:social-security",),
        check_results=(),
        risk_items=(),
        fact_evidence_refs=(fact,),
        prompt_version="deepsearch-acquisition-v1",
    )

    result.require_known_evidence({fact.evidence_id})
    assert result.risk_items == ()


def test_report_structure_is_built_from_the_versioned_catalog() -> None:
    structure = ReportStructure.from_catalog(REPORT_CATALOG)

    assert structure.catalog_version == "report-catalog-v1"
    assert structure.module_count == 8
    assert structure.submodule_count == 48
    assert structure.modules == REPORT_CATALOG.modules
