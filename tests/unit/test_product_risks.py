from datetime import UTC, datetime

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
from jindiao.investigation import CHECK_CATALOG
from jindiao.reporting.product_risks import project_risks


def test_cards_keep_independent_incidents_and_merge_only_identical_facts() -> None:
    evidence = Evidence(
        evidence_id="e1",
        claim="司法记录",
        value={"cases": ["甲", "乙"]},
        subject_id="s1",
        source_type=SourceType.TIANYANCHA,
        queried_at=datetime.now(UTC),
        confidence=1,
        is_mock=False,
        supports_fields=("judicial.cases",),
        raw_ref="mcp://judicial",
    )
    first = Finding(
        finding_id="f1",
        subject_id="s1",
        domain="judicial",
        claim="甲案执行未结",
        risk_class=RiskClass.ATTENTION,
        severity=Severity.HIGH,
        status=FindingStatus.ACCEPTED,
        evidence_ids=("e1",),
    )
    second = first.model_copy(update={"finding_id": "f2", "claim": "乙案执行未结"})
    duplicate = first.model_copy(update={"finding_id": "duplicate"})
    normal = first.model_copy(update={"finding_id": "normal", "risk_class": RiskClass.NON_RISK})
    pending = first.model_copy(update={"status": FindingStatus.UNCONFIRMED})
    cards = project_risks((first, second, duplicate, normal, pending), (evidence,))
    assert len(cards) == 2
    assert cards[0].id == project_risks((first,), (evidence,))[0].id
    assert cards[0].source_kind == "fact"
    assert cards[0].check_items == ()
    assert cards[0].historical_case is None
    assert not cards[0].historical_case_is_mock


def test_card_maps_executed_check_and_server_owned_source_label() -> None:
    evidence = Evidence(
        evidence_id="e1",
        claim="执行事项尚未解除",
        value={"status": "未结"},
        subject_id="s1",
        source_type=SourceType.TIANYANCHA,
        source_tool="get_execution_info",
        queried_at=datetime.now(UTC),
        confidence=1,
        is_mock=False,
        supports_fields=("judicial.executions",),
        raw_ref="mcp://execution/1",
    )
    definition = CHECK_CATALOG.get("material-execution-risk")
    check = CheckResult(
        snapshot_id="snapshot",
        snapshot_sha256="a" * 64,
        subject_id="s1",
        check_catalog_version=CHECK_CATALOG.catalog_version,
        output_schema_version=definition.output_schema_version,
        task_id="check:material-execution-risk",
        check_id=definition.check_id,
        status=CheckStatus.RISK,
        decision_summary="执行事项需要关注",
        risk_items=(
            RiskItem(
                risk_id="one",
                check_id=definition.check_id,
                title="执行事项尚未解除",
                status=CheckStatus.RISK,
                risk_class=RiskClass.ATTENTION,
                severity=Severity.HIGH,
                conclusion="执行事项尚未解除",
                evidence_ids=(evidence.evidence_id,),
                confidence=0.9,
            ),
        ),
        fact_evidence_refs=(
            FactEvidenceRef(
                evidence_id=evidence.evidence_id,
                fact_path="judicial.executions",
                summary=evidence.claim,
            ),
        ),
        confidence=0.9,
        prompt_version="fixed-check-v1",
        submission_version=1,
    )
    finding = Finding(
        finding_id="finding-one",
        subject_id="s1",
        domain="judicial",
        claim="执行事项尚未解除",
        value={"check_id": definition.check_id, "title": "执行事项尚未解除"},
        risk_class=RiskClass.ATTENTION,
        severity=Severity.HIGH,
        status=FindingStatus.ACCEPTED,
        evidence_ids=(evidence.evidence_id,),
    )

    card = project_risks((finding,), (evidence,), (check,))[0]

    assert card.source_kind == "mixed"
    assert card.check_items[0].label == definition.title
    assert card.evidence_tags[0].label == "天眼查·get_execution_info"
