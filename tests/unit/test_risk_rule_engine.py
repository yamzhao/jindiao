from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jindiao.application.errors import RiskRuleError
from jindiao.contracts.evidence import Evidence, SourceType
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    FactEvidenceRef,
    Finding,
    FindingStatus,
    RiskClass,
    RiskItem,
    Severity,
)
from jindiao.contracts.reporting import DecisionBand
from jindiao.risk import RiskRuleEngine, RiskRuleSet

NOW = datetime(2026, 9, 3, tzinfo=UTC)
AS_OF = date(2026, 8, 31)


def evidence(evidence_id: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim="失信记录",
        value=True,
        subject_id="tyc:22822",
        source_type=SourceType.TIANYANCHA,
        source_tool="dishonest_list",
        source_record_id="record-1",
        queried_at=NOW,
        confidence=1,
        is_mock=False,
        supports_fields=("judicial.dishonest",),
        raw_ref="artifact://run-1/dishonest.json#record-1",
    )


def finding(
    finding_id: str,
    claim: str,
    *,
    risk_class: RiskClass,
    status: FindingStatus = FindingStatus.ACCEPTED,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        subject_id="tyc:22822",
        domain="judicial",
        claim=claim,
        risk_class=risk_class,
        severity=Severity.CRITICAL,
        status=status,
        evidence_ids=("ev-1",),
    )


def engine() -> RiskRuleEngine:
    return RiskRuleEngine(RiskRuleSet.from_file(Path("config/risk-rules-v1.json")))


def profitability_check() -> CheckResult:
    fact = FactEvidenceRef(
        evidence_id="ev-1",
        fact_path="operations.financial_summary.net_profit",
        summary="近三年净利润持续下降",
    )
    risk = RiskItem(
        risk_id="risk-profit-decline",
        check_id="profitability-decline",
        title="盈利能力下滑",
        status=CheckStatus.RISK,
        risk_class=RiskClass.ATTENTION,
        severity=Severity.HIGH,
        conclusion="近三年净利润持续下降。",
        evidence_ids=(fact.evidence_id,),
        confidence=0.9,
    )
    return CheckResult(
        snapshot_id="snapshot:risk-engine",
        snapshot_sha256="a" * 64,
        subject_id="tyc:22822",
        check_catalog_version="due-diligence-check-catalog-v1",
        output_schema_version="check-result-v1",
        task_id="check:profitability-decline",
        check_id="profitability-decline",
        status=CheckStatus.RISK,
        decision_summary="证据支持盈利能力下滑。",
        risk_items=(risk,),
        fact_evidence_refs=(fact,),
        confidence=0.9,
        prompt_version="investigation-core-v1+financial-operations-v1",
        submission_version=1,
    )


def test_engine_scores_only_accepted_evidence_backed_findings() -> None:
    accepted = finding(
        "finding-dishonest",
        "企业存在失信被执行人记录",
        risk_class=RiskClass.ADMISSION,
    )
    unconfirmed = finding(
        "finding-unconfirmed",
        "企业存在失信信息冲突",
        risk_class=RiskClass.ADMISSION,
        status=FindingStatus.UNCONFIRMED,
    )

    decision = engine().evaluate(
        findings=(accepted, unconfirmed),
        evidence=(evidence("ev-1"),),
        as_of_date=AS_OF,
        confidence=0.85,
        pending_review_items=("conflict-1",),
    )

    assert decision.band is DecisionBand.REJECT
    assert decision.score == 80
    assert len(decision.rule_hits) == 1
    assert decision.rule_hits[0].rule_id == "admission.dishonest_subject"
    assert decision.rule_hits[0].evidence_ids == ("ev-1",)
    assert decision.major_risk_finding_ids == ("finding-dishonest",)
    assert decision.pending_review_items == ("conflict-1",)


def test_engine_scores_projected_fixed_check_risks_without_model_score_input() -> None:
    target = engine()
    check = profitability_check()

    findings = target.findings_from_check_results((check,))
    decision = target.evaluate_check_results(
        accepted_check_results=(check,),
        evidence=(evidence("ev-1"),),
        as_of_date=AS_OF,
        confidence=0.9,
    )

    assert tuple(item.finding_id for item in findings) == (
        "check:profitability-decline:risk:risk-profit-decline",
    )
    assert decision.score == 10
    assert tuple(item.rule_id for item in decision.rule_hits) == ("attention.profit_decline",)


def test_engine_refuses_unknown_or_missing_evidence_before_scoring() -> None:
    backed = finding(
        "finding-dishonest",
        "企业存在失信被执行人记录",
        risk_class=RiskClass.ADMISSION,
    )
    unbacked = Finding.model_construct(
        finding_id="finding-unbacked",
        subject_id="tyc:22822",
        domain="judicial",
        claim="企业存在失信被执行人记录",
        value=None,
        risk_class=RiskClass.ADMISSION,
        severity=Severity.CRITICAL,
        status=FindingStatus.ACCEPTED,
        evidence_ids=(),
        occurred_at=None,
    )

    with pytest.raises(RiskRuleError, match="evidence"):
        engine().evaluate(
            findings=(unbacked,),
            evidence=(evidence("ev-1"),),
            as_of_date=AS_OF,
            confidence=1,
        )
    with pytest.raises(RiskRuleError, match="unknown evidence"):
        engine().evaluate(
            findings=(backed,),
            evidence=(evidence("ev-other"),),
            as_of_date=AS_OF,
            confidence=1,
        )


def test_engine_scores_only_explicit_evidence_backed_unconfirmed_guard_rules() -> None:
    conflict = finding(
        "finding-conflict",
        "员工人数在结构化年报与补充材料中不一致",
        risk_class=RiskClass.ATTENTION,
        status=FindingStatus.UNCONFIRMED,
    )

    decision = engine().evaluate(
        findings=(conflict,),
        evidence=(evidence("ev-1"),),
        as_of_date=AS_OF,
        confidence=0.7,
        pending_review_items=("conflict-1",),
    )

    assert decision.score == 20
    assert decision.band is DecisionBand.MANUAL_REVIEW
    assert [hit.rule_id for hit in decision.rule_hits] == ["attention.unresolved_conflict"]


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, DecisionBand.PASS),
        (19, DecisionBand.PASS),
        (20, DecisionBand.MANUAL_REVIEW),
        (79, DecisionBand.MANUAL_REVIEW),
        (80, DecisionBand.REJECT),
        (200, DecisionBand.REJECT),
    ],
)
def test_band_boundaries_are_exact(score: int, expected: DecisionBand) -> None:
    assert engine().band_for_score(score) is expected
