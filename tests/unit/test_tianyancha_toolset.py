from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from jindiao.application import RunContext, Settings
from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    Evidence,
    SourceStatus,
    SourceType,
)
from jindiao.deepsearch import (
    AnnualReportSocialSecurity,
    AnnualReportSocialSecurityOutcome,
    BoundedEvidenceSupplementService,
    EvidenceGap,
    SupplementDocument,
    SupplementOutcome,
    SupplementQuery,
    SupplementSearchCandidate,
)
from jindiao.orchestration.tianyancha_toolset import TianyanchaHybridToolset
from jindiao.scenarios import ScenarioRepository
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    McpCallResult,
    McpErrorKind,
    TianyanchaMcpError,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _json_object(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


class FakeLiveClient:
    def __init__(
        self,
        *,
        tools: tuple[str, ...] = ("get_company_registration_info",),
        business_result: McpCallResult | None = None,
        business_error: TianyanchaMcpError | None = None,
        business_results: dict[str, McpCallResult | TianyanchaMcpError] | None = None,
    ) -> None:
        self.tools = tools
        self.business_result = business_result or McpCallResult(
            structured_content={"items": [{"id": "reg-1", "regStatus": "存续"}]}
        )
        self.business_error = business_error
        self.business_results = business_results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        self.calls.append((name, arguments))
        if name == "search_companies":
            return McpCallResult(
                structured_content={
                    "items": [
                        {
                            "id": "123",
                            "name": "公开样本有限公司",
                            "creditCode": "91110000LIVE000001",
                            "base": "北京市",
                            "regStatus": "存续",
                        }
                    ]
                }
            )
        if name == "get_company_capabilities":
            return McpCallResult(
                structured_content={"tools": [{"tool_name": tool_name} for tool_name in self.tools]}
            )
        if name == "call_tool":
            selected = self.business_results.get(str(arguments["tool_name"]))
            if isinstance(selected, TianyanchaMcpError):
                raise selected
            if selected is not None:
                return selected
            if self.business_error is not None:
                raise self.business_error
            return self.business_result
        raise AssertionError(f"unexpected tool: {name}")

    async def aclose(self) -> None:
        self.closed = True


class RecordingSupplementProvider:
    def __init__(self) -> None:
        self.queries: list[SupplementQuery] = []
        self.fetches: list[SupplementSearchCandidate] = []

    async def search(
        self,
        query: SupplementQuery,
        *,
        limit: int,
    ) -> tuple[SupplementSearchCandidate, ...]:
        self.queries.append(query)
        assert limit == 5
        return (
            SupplementSearchCandidate(
                candidate_id="candidate-registration",
                title="企业登记公告",
                url="https://example.gov.cn/notices/registration-1",
                snippet="只能用于发现候选页面",
                publisher="市场监督管理局",
                rank=1,
            ),
        )

    async def fetch(
        self,
        candidate: SupplementSearchCandidate,
        *,
        goal: str,
    ) -> SupplementDocument:
        self.fetches.append(candidate)
        assert goal == "工商登记信息"
        return SupplementDocument(
            candidate_id=candidate.candidate_id,
            title=candidate.title,
            url=candidate.url,
            publisher=candidate.publisher,
            content="公开样本有限公司工商登记公告正文。",
        )


class FakeAnnualReportProvider:
    def __init__(self, outcome: AnnualReportSocialSecurityOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, date]] = []
        self.closed = False

    async def fetch_latest(
        self,
        subject: ResolvedSubject,
        *,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome:
        self.calls.append((subject.subject_id, report_as_of))
        return self.outcome

    async def aclose(self) -> None:
        self.closed = True


class RecordingDeepSearchAgent:
    supplement_enabled = True
    annual_report_enabled = True

    def __init__(self) -> None:
        self.gaps: list[tuple[str, str]] = []
        self.annual_reports: list[tuple[str, date]] = []
        self.closed = False

    async def research_gap(
        self,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
    ) -> SupplementOutcome:
        del queried_at, report_as_of
        self.gaps.append((subject.subject_id, gap.gap_id))
        return SupplementOutcome(
            gap=gap,
            evidence=(),
            executed_queries=(),
            rounds_executed=1,
            unresolved=True,
        )

    async def fetch_annual_report_social_security(
        self,
        *,
        subject: ResolvedSubject,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome:
        del queried_at
        self.annual_reports.append((subject.subject_id, report_as_of))
        return AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=(2025,),
        )

    async def aclose(self) -> None:
        self.closed = True


def context(*, allow_degraded_mock: bool = False) -> RunContext:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        model_name="deterministic-test-model",
        JINDIAO_ALLOW_DEGRADED_MOCK=allow_degraded_mock,
    )
    return RunContext.from_settings(
        request_id="req-live",
        run_id="run-live",
        scenario=ScenarioRepository(Path("mock_data/scenarios")).load_template("normal-enterprise"),
        settings=settings,
        skill_versions={"reporting": "1.0.0"},
        requested_enterprise=EnterpriseInput(company_name="公开样本有限公司"),
    )


def routing() -> CapabilityRoutingConfig:
    return CapabilityRoutingConfig.from_file(Path("config/tianyancha-capability-routes.json"))


@pytest.mark.asyncio
async def test_live_toolset_prioritizes_manifest_tool_and_marks_mock_supplement() -> None:
    client = FakeLiveClient()
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()

    subject = await toolset.resolve_subject(run_context)
    capabilities = await toolset.domain_capabilities(subject)
    artifact = await toolset.investigate(run_context, subject, "governance")

    assert subject.company_name == "公开样本有限公司"
    assert subject.source.value == "tianyancha"
    assert capabilities["governance"][0] == "get_company_registration_info"
    assert {item.source_type for item in artifact.evidence} == {
        SourceType.TIANYANCHA,
        SourceType.MOCK,
    }
    assert artifact.coverage_items[0].status is SourceStatus.VERIFIED_RECORDS
    assert all(
        item.status is SourceStatus.CAPABILITY_ABSENT for item in artifact.coverage_items[1:]
    )
    assert len(artifact.coverage_items) == 10
    assert artifact.section_data["company-profile"]["company_name"] == subject.company_name
    assert artifact.section_data["company-profile"]["unified_social_credit_code"] == (
        subject.unified_social_credit_code
    )
    assert any(
        name == "call_tool" and arguments["tool_name"] == "get_company_registration_info"
        for name, arguments in client.calls
    )


@pytest.mark.asyncio
async def test_live_toolset_calls_multiple_tools_and_assembles_governance_submodules() -> None:
    client = FakeLiveClient(
        tools=(
            "get_company_registration_info",
            "get_shareholder_info",
            "get_branches",
        ),
        business_results={
            "get_company_registration_info": McpCallResult(
                structured_content={"items": [{"id": "reg-1", "regStatus": "存续"}]}
            ),
            "get_shareholder_info": McpCallResult(
                structured_content={"items": [{"id": "holder-1", "name": "甲股东"}]}
            ),
            "get_branches": McpCallResult(
                structured_content={"items": [{"id": "branch-1", "name": "第一分公司"}]}
            ),
        },
    )
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "governance")

    calls = [str(arguments["tool_name"]) for name, arguments in client.calls if name == "call_tool"]
    assert sorted(calls) == sorted(client.tools)
    company_profile = _json_object(artifact.section_data["company-profile"])
    submodules = _json_object(company_profile["submodules"])
    assert submodules["registration"]["source_status"] == "verified_records"
    assert submodules["registration"]["records"][0]["regStatus"] == "存续"
    assert submodules["shareholders"]["source_tool"] == "get_shareholder_info"
    assert submodules["shareholders"]["records"][0]["name"] == "甲股东"
    assert submodules["branches"]["source_tool"] == "get_branches"
    assert submodules["branches"]["records"][0]["name"] == "第一分公司"


@pytest.mark.asyncio
async def test_live_toolset_deduplicates_shared_tool_and_preserves_mixed_results() -> None:
    timeout = TianyanchaMcpError(McpErrorKind.TIMEOUT, "safe timeout")
    client = FakeLiveClient(
        tools=("get_risk_overview", "get_judicial_documents", "get_hearing_notice"),
        business_results={
            "get_risk_overview": McpCallResult(
                structured_content={"items": [{"id": "risk-1", "type": "execution"}]}
            ),
            "get_judicial_documents": McpCallResult(structured_content={"items": []}),
            "get_hearing_notice": timeout,
        },
    )
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "judicial")

    calls = [str(arguments["tool_name"]) for name, arguments in client.calls if name == "call_tool"]
    assert calls.count("get_risk_overview") == 1
    judicial_risk = _json_object(artifact.section_data["judicial-risk"])
    submodules = _json_object(judicial_risk["submodules"])
    assert submodules["consumption_restrictions"]["source_status"] == "verified_records"
    assert submodules["dishonest_enforcement"]["source_status"] == "verified_records"
    assert submodules["executions"]["source_status"] == "verified_records"
    assert submodules["judicial_documents"]["source_status"] == "verified_empty"
    assert submodules["judicial_documents"]["records"] == []
    assert submodules["hearing_notices"]["source_status"] == "source_error"
    assert submodules["hearing_notices"]["records"] == []
    assert any(item.source_tool == "get_risk_overview" for item in artifact.evidence)
    assert artifact.errors


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "tools", "section_id"),
    [
        (
            "operations",
            ("get_annual_reports", "get_administrative_penalty"),
            "operations-analysis",
        ),
        ("peers", ("get_competitors", "get_ranking_list_info"), "peer-analysis"),
    ],
)
async def test_each_operations_peer_domain_can_call_multiple_tools(
    domain: str,
    tools: tuple[str, ...],
    section_id: str,
) -> None:
    client = FakeLiveClient(tools=tools)
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, domain)

    calls = {str(arguments["tool_name"]) for name, arguments in client.calls if name == "call_tool"}
    assert calls == set(tools)
    assert artifact.section_data[section_id]["submodules"]
    assert "company-profile" not in artifact.section_data


@pytest.mark.asyncio
async def test_live_toolset_preserves_verified_empty_without_replacing_that_capability() -> None:
    client = FakeLiveClient(business_result=McpCallResult(structured_content={"items": []}))
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "governance")

    assert artifact.coverage_items[0].status is SourceStatus.VERIFIED_EMPTY
    assert artifact.coverage_items[0].capability == "get_company_registration_info"
    company_profile = _json_object(artifact.section_data["company-profile"])
    submodules = _json_object(company_profile["submodules"])
    registration = _json_object(submodules["registration"])
    assert registration["records"] == []
    assert registration["evidence_ids"] == []
    assert not any(
        item.source_tool == "get_company_registration_info" for item in artifact.evidence
    )


@pytest.mark.asyncio
async def test_live_toolset_marks_explicitly_truncated_results_as_partial() -> None:
    client = FakeLiveClient(
        business_result=McpCallResult(
            structured_content={
                "items": [{"id": "reg-1", "regStatus": "存续"}],
                "page": 1,
                "page_size": 1,
                "total": 2,
                "has_more": True,
            }
        )
    )
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "governance")

    registration = artifact.coverage_items[0]
    assert registration.status is SourceStatus.VERIFIED_RECORDS
    assert registration.completeness is CoverageCompleteness.PARTIAL
    assert registration.gap_reasons == (CoverageGapReason.PAGINATION_TRUNCATED,)
    assert all(item.source_type is not SourceType.PUBLIC_WEB for item in artifact.evidence)
    submodules = cast(
        dict[str, dict[str, object]],
        artifact.section_data["company-profile"]["submodules"],
    )
    registration_data = submodules["registration"]
    assert "supplement_evidence_ids" not in registration_data


@pytest.mark.asyncio
async def test_partial_coverage_invokes_optional_supplement_without_overwriting_tyc() -> None:
    client = FakeLiveClient(
        business_result=McpCallResult(
            structured_content={
                "items": [{"id": "reg-1", "regStatus": "存续"}],
                "page": 1,
                "page_size": 1,
                "total": 2,
                "has_more": True,
            }
        )
    )
    provider = RecordingSupplementProvider()
    service = BoundedEvidenceSupplementService(provider)
    toolset = TianyanchaHybridToolset(
        client=client,
        routing=routing(),
        supplement_service=service,
        clock=lambda: NOW,
    )
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "governance")

    assert provider.queries
    assert len(provider.fetches) == 1
    registration = artifact.coverage_items[0]
    assert registration.status is SourceStatus.VERIFIED_RECORDS
    assert registration.completeness is CoverageCompleteness.PARTIAL
    assert registration.gap_reasons == (CoverageGapReason.PAGINATION_TRUNCATED,)
    public_web = [item for item in artifact.evidence if item.source_type is SourceType.PUBLIC_WEB]
    assert len(public_web) == 1
    assert public_web[0].supports_fields == ("governance.records",)
    supplemental_coverage = next(
        item for item in artifact.coverage_items if item.capability == "supplement:registration"
    )
    assert supplemental_coverage.status is SourceStatus.VERIFIED_RECORDS
    assert supplemental_coverage.completeness is CoverageCompleteness.COMPLETE
    submodules = cast(
        dict[str, dict[str, object]],
        artifact.section_data["company-profile"]["submodules"],
    )
    registration_data = submodules["registration"]
    assert registration_data["source_status"] == "verified_records"
    assert registration_data["coverage_completeness"] == "partial"
    assert registration_data["supplement_evidence_ids"] == [public_web[0].evidence_id]
    assert registration_data["supplement_rounds"] == 1
    assert registration_data["supplement_unresolved"] is False


@pytest.mark.asyncio
async def test_hybrid_toolset_routes_supplements_through_one_deepsearch_agent() -> None:
    client = FakeLiveClient(
        business_result=McpCallResult(
            structured_content={
                "items": [{"id": "reg-1", "regStatus": "存续"}],
                "page": 1,
                "page_size": 1,
                "total": 2,
                "has_more": True,
            }
        )
    )
    deepsearch = RecordingDeepSearchAgent()
    toolset = TianyanchaHybridToolset(
        client=client,
        routing=routing(),
        deepsearch_agent=deepsearch,
        clock=lambda: NOW,
    )
    run_context = context()
    resolved = await toolset.resolve_subject(run_context)

    await toolset.investigate(run_context, resolved, "governance")
    assert deepsearch.annual_reports == []
    await toolset.investigate(run_context, resolved, "operations")
    await toolset.aclose()

    assert len(deepsearch.gaps) == 1
    assert deepsearch.gaps[0][0] == "tyc:123"
    assert deepsearch.gaps[0][1].startswith("gap-")
    assert deepsearch.annual_reports == [("tyc:123", run_context.report_as_of)]
    assert deepsearch.closed is True


def test_hybrid_toolset_rejects_facade_plus_raw_deepsearch_dependencies() -> None:
    deepsearch = RecordingDeepSearchAgent()
    service = BoundedEvidenceSupplementService(RecordingSupplementProvider())
    annual_report = FakeAnnualReportProvider(
        AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=(2025,),
        )
    )

    with pytest.raises(ValueError, match="deepsearch_agent"):
        TianyanchaHybridToolset(
            client=FakeLiveClient(),
            routing=routing(),
            deepsearch_agent=deepsearch,
            supplement_service=service,
        )
    with pytest.raises(ValueError, match="deepsearch_agent"):
        TianyanchaHybridToolset(
            client=FakeLiveClient(),
            routing=routing(),
            deepsearch_agent=deepsearch,
            annual_report_provider=annual_report,
        )


@pytest.mark.asyncio
async def test_operations_adds_annual_report_social_security_as_partial_standard_data() -> None:
    source_url = "https://www.tianyancha.com/annualReport/123/2025"
    report = AnnualReportSocialSecurity(
        report_year=2025,
        publicized_at=datetime(2026, 6, 29, tzinfo=UTC).date(),
        company_name="公开样本有限公司",
        unified_social_credit_code="91110000LIVE000001",
        source_url=source_url,
        source_title="公开样本有限公司2025年报 - 天眼查",
        pension_insured_count=54,
        medical_insured_count=54,
        maternity_insured_count=54,
        unemployment_insured_count=54,
        work_injury_insured_count=54,
        reported_values={"城镇职工基本养老保险": "54人"},
    )
    evidence = Evidence(
        evidence_id="ev-web-annual-report",
        claim="天眼查2025年度报告社保信息",
        value=report.model_dump(mode="json"),
        subject_id="tyc:123",
        source_type=SourceType.PUBLIC_WEB,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="tianyancha.annual_report.web",
        source_record_id="123:2025",
        queried_at=NOW,
        as_of_date=report.publicized_at,
        confidence=0.9,
        is_mock=False,
        supports_fields=("operations.annual_reports.social_security",),
        raw_ref=source_url,
        source_chain=(source_url,),
        source_title=report.source_title,
        source_publisher="天眼查",
        content_hash="sha256:" + "a" * 64,
    )
    provider = FakeAnnualReportProvider(
        AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_RECORDS,
            report=report,
            evidence=(evidence,),
            checked_years=(2025,),
        )
    )
    client = FakeLiveClient()
    toolset = TianyanchaHybridToolset(
        client=client,
        routing=routing(),
        annual_report_provider=provider,
        clock=lambda: NOW,
    )
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "operations")

    assert provider.calls == [("tyc:123", run_context.report_as_of)]
    operations_analysis = _json_object(artifact.section_data["operations-analysis"])
    submodules = _json_object(operations_analysis["submodules"])
    assert "annual_report_social_security" not in submodules
    annual_reports = _json_object(submodules["annual_reports"])
    social_security = _json_object(annual_reports["social_security"])
    assert social_security["source_status"] == "verified_records"
    assert social_security["source_tool"] == "tianyancha.annual_report.web"
    assert social_security["record_count"] == 1
    assert social_security["report_found"] is True
    assert social_security["checked_years"] == [2025]
    assert social_security["records"][0]["pension_insured_count"] == 54
    assert social_security["evidence_ids"] == [evidence.evidence_id]
    assert social_security["submodule_coverage"] == "partial"
    assert evidence in artifact.evidence
    annual_coverage = next(
        item
        for item in artifact.coverage_items
        if item.capability == "tianyancha.annual_report.social_security"
    )
    assert annual_coverage.status is SourceStatus.VERIFIED_RECORDS
    assert annual_coverage.record_count == 1


@pytest.mark.asyncio
async def test_operations_preserves_missing_annual_report_as_verified_empty_partial_fact() -> None:
    provider = FakeAnnualReportProvider(
        AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=(2025, 2024, 2023),
        )
    )
    toolset = TianyanchaHybridToolset(
        client=FakeLiveClient(),
        routing=routing(),
        annual_report_provider=provider,
        clock=lambda: NOW,
    )
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "operations")

    operations_analysis = _json_object(artifact.section_data["operations-analysis"])
    submodules = _json_object(operations_analysis["submodules"])
    assert "annual_report_social_security" not in submodules
    annual_reports = _json_object(submodules["annual_reports"])
    social_security = _json_object(annual_reports["social_security"])
    assert social_security["source_status"] == "verified_empty"
    assert social_security["record_count"] == 0
    assert social_security["records"] == []
    assert social_security["report_found"] is False
    assert social_security["checked_years"] == [2025, 2024, 2023]
    assert social_security["submodule_coverage"] == "partial"
    assert artifact.errors == ()
    assert not any(item.source_tool == "tianyancha.annual_report.web" for item in artifact.evidence)


@pytest.mark.asyncio
async def test_annual_report_provider_is_only_called_for_operations_and_is_closed() -> None:
    provider = FakeAnnualReportProvider(
        AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=(2025,),
        )
    )
    client = FakeLiveClient()
    toolset = TianyanchaHybridToolset(
        client=client,
        routing=routing(),
        annual_report_provider=provider,
        clock=lambda: NOW,
    )
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    await toolset.investigate(run_context, subject, "governance")
    assert provider.calls == []

    await toolset.investigate(run_context, subject, "operations")
    await toolset.aclose()

    assert provider.calls == [("tyc:123", run_context.report_as_of)]
    assert provider.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_live_toolset_uses_mock_when_domain_capability_is_absent() -> None:
    client = FakeLiveClient(tools=())
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context()
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "operations")

    assert artifact.coverage_items[0].status is SourceStatus.CAPABILITY_ABSENT
    assert artifact.errors == ()
    assert artifact.evidence
    assert all(item.source_type is SourceType.MOCK for item in artifact.evidence)
    assert not any(name == "call_tool" for name, _ in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_degraded", [False, True])
async def test_live_toolset_source_error_requires_explicit_degraded_mock(
    allow_degraded: bool,
) -> None:
    client = FakeLiveClient(business_error=TianyanchaMcpError(McpErrorKind.TIMEOUT, "safe timeout"))
    toolset = TianyanchaHybridToolset(client=client, routing=routing(), clock=lambda: NOW)
    run_context = context(allow_degraded_mock=allow_degraded)
    subject = await toolset.resolve_subject(run_context)

    artifact = await toolset.investigate(run_context, subject, "governance")

    expected = SourceStatus.DEGRADED_MOCK if allow_degraded else SourceStatus.SOURCE_ERROR
    assert artifact.coverage_items[0].status is expected
    company_profile = _json_object(artifact.section_data["company-profile"])
    submodules = _json_object(company_profile["submodules"])
    registration = _json_object(submodules["registration"])
    assert bool(registration["records"]) is allow_degraded
    assert bool(artifact.errors) is (not allow_degraded)
    if allow_degraded:
        assert all(item.source_status is SourceStatus.DEGRADED_MOCK for item in artifact.evidence)


@pytest.mark.asyncio
async def test_live_toolset_closes_client() -> None:
    client = FakeLiveClient()
    toolset = TianyanchaHybridToolset(client=client, routing=routing())

    await toolset.aclose()

    assert client.closed is True
