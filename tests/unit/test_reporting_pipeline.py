# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import (
    CoverageItem,
    CoverageSummary,
    Evidence,
    SourceStatus,
    SourceType,
)
from jindiao.contracts.investigation import (
    Finding,
    FindingStatus,
    RiskClass,
    Severity,
)
from jindiao.contracts.reporting import Decision, DecisionBand, ReportViewModel, RuleHit
from jindiao.reporting import (
    FRONTEND_VIEW_IDS,
    REPORT_SECTION_IDS,
    MarkdownReportRenderer,
    ReportAssembler,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)
AS_OF = date(2026, 8, 31)


def make_subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="mock:normal-enterprise",
        company_name="乐视网信息技术（北京）股份有限公司",
        unified_social_credit_code="91110108MA01JD001A",
        region="某市",
        registration_status="存续",
        source=SubjectSource.MOCK,
        resolved_at=NOW,
    )


def make_evidence(evidence_id: str, field: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim=f"{field} 已核验",
        value=True,
        subject_id="mock:normal-enterprise",
        source_type=SourceType.MOCK,
        source_status=SourceStatus.DEGRADED_MOCK,
        source_record_id=evidence_id,
        queried_at=NOW,
        as_of_date=AS_OF,
        confidence=1,
        is_mock=True,
        supports_fields=(field,),
        raw_ref=f"mock://normal-enterprise/v1/data.json#{evidence_id}",
    )


def make_finding(
    finding_id: str,
    domain: str,
    risk_class: RiskClass,
    evidence_id: str,
    severity: Severity = Severity.INFO,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        subject_id="mock:normal-enterprise",
        domain=domain,
        claim=f"{domain} 结论 {finding_id}",
        risk_class=risk_class,
        severity=severity,
        status=FindingStatus.ACCEPTED,
        evidence_ids=(evidence_id,),
    )


def make_pipeline_inputs() -> tuple[
    tuple[Finding, ...], tuple[Evidence, ...], CoverageSummary, Decision
]:
    evidence = (
        make_evidence("ev-company", "company.registration_status"),
        make_evidence("ev-governance", "governance.shareholders"),
        make_evidence("ev-judicial", "judicial.dishonest"),
        make_evidence("ev-operations", "operations.abnormal"),
        make_evidence("ev-peers", "peers.metrics"),
    )
    findings = (
        make_finding("finding-company", "identity", RiskClass.NON_RISK, "ev-company"),
        make_finding(
            "finding-governance",
            "governance",
            RiskClass.NON_RISK,
            "ev-governance",
        ),
        make_finding(
            "finding-judicial",
            "judicial",
            RiskClass.ADMISSION,
            "ev-judicial",
            Severity.CRITICAL,
        ),
        make_finding(
            "finding-operations",
            "operations",
            RiskClass.ATTENTION,
            "ev-operations",
            Severity.MEDIUM,
        ),
        make_finding("finding-peers", "peers", RiskClass.NON_RISK, "ev-peers"),
    )
    coverage = CoverageSummary.from_items(
        [
            CoverageItem(
                domain=domain,
                capability=f"{domain}-capability",
                status=SourceStatus.DEGRADED_MOCK,
                record_count=1,
                fallback_reason="Tianyancha capability absent",
            )
            for domain in ("identity", "governance", "judicial", "operations", "peers")
        ]
    )
    hit = RuleHit(
        rule_id="admission.dishonest_subject",
        finding_id="finding-judicial",
        evidence_ids=("ev-judicial",),
        points=80,
        explanation="存在失信被执行人记录",
    )
    decision = Decision(
        band=DecisionBand.REJECT,
        score=80,
        confidence=0.88,
        rule_version="risk-rules-v1",
        rule_hits=(hit,),
        major_risk_finding_ids=("finding-judicial",),
        pending_review_items=("确认历史案件履行状态",),
        as_of_date=AS_OF,
    )
    return findings, evidence, coverage, decision


def assemble_complete_view() -> ReportViewModel:
    findings, evidence, coverage, decision = make_pipeline_inputs()
    return ReportAssembler().assemble(
        subject=make_subject(),
        decision=decision,
        coverage=coverage,
        findings=findings,
        evidence=evidence,
        generated_at=NOW,
        section_data={
            "company-profile": {"registration_status": "存续"},
            "operations-analysis": {"revenue_cny_10k": 12600},
            "peer-analysis": {"median_revenue_cny_10k": 9600},
        },
        frontend_overrides={
            "collaboration-evaluation": {
                "agent_count": 5,
                "benchmark_mode": "multi",
            }
        },
    )


def test_assembler_builds_eight_sections_and_ten_frontend_views() -> None:
    view = assemble_complete_view()

    assert tuple(section.section_id for section in view.sections) == REPORT_SECTION_IDS
    assert tuple(item.view_id for item in view.frontend_views) == FRONTEND_VIEW_IDS
    assert all(section.status.value == "complete" for section in view.sections)
    assert view.risk_summary.admission_count == 1
    assert view.risk_summary.attention_count == 1
    assert view.risk_summary.non_risk_count == 3
    assert view.risk_summary.total_score == 80
    assert view.risk_summary.top_finding_ids[0] == "finding-judicial"
    assert view.sections[0].data["decision_band"] == "reject"
    assert view.sections[1].data["total_score"] == 80
    assert view.sections[3].finding_ids == ("finding-judicial",)
    assert view.sections[3].evidence_ids == ("ev-judicial",)
    assert view.frontend_views[-1].data["agent_count"] == 5
    assert any("Mock" in disclosure for disclosure in view.source_disclosure)


def test_assembler_retains_unavailable_section_and_discloses_source_error() -> None:
    findings, evidence, _, decision = make_pipeline_inputs()
    coverage = CoverageSummary.from_items(
        [
            CoverageItem(
                domain="judicial",
                capability="dishonest",
                status=SourceStatus.SOURCE_ERROR,
                error="upstream timeout",
            )
        ]
    )

    view = ReportAssembler().assemble(
        subject=make_subject(),
        decision=decision,
        coverage=coverage,
        findings=findings,
        evidence=evidence,
        generated_at=NOW,
    )

    judicial = next(item for item in view.sections if item.section_id == "judicial-risk")
    assert judicial.status.value == "unavailable"
    assert judicial.coverage == 0
    assert any("数据源不可用" in item for item in view.source_disclosure)


def test_markdown_is_rendered_only_from_view_model_and_keeps_values_aligned() -> None:
    view = assemble_complete_view()

    markdown = MarkdownReportRenderer().render(view)

    for section in view.sections:
        assert f"## {section.title}" in markdown
    assert "风险总分: 80" in markdown
    assert "决策分档: 拒绝" in markdown
    assert "置信度: 88.00%" in markdown
    assert "[准入类] judicial 结论 finding-judicial" in markdown
    assert "admission.dishonest_subject" in markdown
    assert "+80; Finding=finding-judicial; Evidence=ev-judicial" in markdown
    assert "mock://normal-enterprise/v1/data.json#ev-judicial" in markdown
    assert markdown.count("Mock 数据提示") == 1
    assert "来源=Mock" in markdown
    assert "## 数据缺口与人工复核" in markdown
    assert "确认历史案件履行状态" in markdown
    assert "## 免责声明" in markdown


def test_markdown_shows_one_mock_notice_at_the_top_without_repeating_warning() -> None:
    markdown = MarkdownReportRenderer().render(assemble_complete_view())

    assert markdown.startswith("# 企业信用与风控尽调报告\n\n> [!WARNING]\n> **Mock 数据提示**")
    assert markdown.count("Mock 数据提示") == 1
    assert "Mock 数据为固定版本化场景数据" not in markdown
    assert "能力缺失或演示降级后由固定 Mock" not in markdown
    assert "- Mock 补足:" not in markdown
    assert "来源=Mock" in markdown


@pytest.mark.parametrize("source_type", list(SourceType))
def test_markdown_renders_every_supported_evidence_source(source_type: SourceType) -> None:
    view = assemble_complete_view()
    original = view.evidence[0]
    source_labels = {
        SourceType.MOCK: "Mock",
        SourceType.TIANYANCHA: "天眼查 MCP",
        SourceType.PUBLIC_WEB: "公开网页",
        SourceType.DERIVED: "派生",
        SourceType.USER_INPUT: "业务输入",
    }
    raw_ref = (
        original.raw_ref
        if source_type is SourceType.MOCK
        else "https://example.com/annual-report/2025"
    )
    evidence = Evidence.model_validate(
        original.model_dump()
        | {
            "source_type": source_type,
            "is_mock": source_type is SourceType.MOCK,
            "source_status": (
                SourceStatus.DEGRADED_MOCK
                if source_type is SourceType.MOCK
                else SourceStatus.VERIFIED_RECORDS
            ),
            "raw_ref": raw_ref,
            "source_chain": ("ev-governance",) if source_type is SourceType.DERIVED else (),
            "source_title": "公开年报",
            "source_publisher": "企业信息公示平台",
            "content_hash": "sha256:" + "a" * 64,
        }
    )
    view = view.model_copy(update={"evidence": (evidence, *view.evidence[1:])})

    markdown = MarkdownReportRenderer().render(view)

    expected = f"[{evidence.evidence_id}] {evidence.claim}; 来源={source_labels[source_type]}"
    assert markdown.count(expected) >= 2  # Section citations and the evidence appendix.
    assert raw_ref in markdown
    assert view.evidence[0].source_type is source_type
    assert view.evidence[0].is_mock is (source_type is SourceType.MOCK)


def test_submodules_are_assembled_into_overview_and_rendered_as_readable_blocks() -> None:
    findings, evidence, coverage, decision = make_pipeline_inputs()
    view = ReportAssembler().assemble(
        subject=make_subject(),
        decision=decision,
        coverage=coverage,
        findings=findings,
        evidence=evidence,
        generated_at=NOW,
        section_data={
            "company-profile": {
                "submodules": {
                    "registration": {
                        "title": "工商登记信息",
                        "source_status": "verified_records",
                        "source_tool": "get_company_registration_info",
                        "record_count": 2,
                        "records": [
                            {"regStatus": "存续"},
                            "### 伪造标题\n- 外部内容",
                        ],
                        "evidence_ids": ["ev-company"],
                    },
                    "branches": {
                        "title": "分支机构",
                        "source_status": "verified_empty",
                        "source_tool": "get_branches",
                        "record_count": 0,
                        "records": [],
                        "evidence_ids": [],
                    },
                }
            }
        },
    )

    summary = view.sections[0].data["information_overview"]
    assert isinstance(summary, dict)
    assert summary["total_submodules"] == 2
    assert summary["status_counts"] == {
        "verified_records": 1,
        "verified_empty": 1,
    }
    markdown = MarkdownReportRenderer().render(view)
    assert "### 工商登记信息" in markdown
    assert "- 数据状态: 已核验有记录" in markdown
    assert "- 来源工具: get_company_registration_info" in markdown
    assert '- 记录 1: {"regStatus": "存续"}' in markdown
    assert '- 记录 2: "### 伪造标题\\n- 外部内容"' in markdown
    assert "\n### 伪造标题" not in markdown
    assert "### 分支机构" in markdown
    assert "- 数据状态: 已核验无记录" in markdown
    assert "- 记录: 无" in markdown
    assert "- submodules:" not in markdown
