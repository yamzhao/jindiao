from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from jindiao.application import RunContext, Settings
from jindiao.application.service import DueDiligenceService
from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject, SubjectSource
from jindiao.contracts.results import DueDiligenceRequest
from jindiao.orchestration.base import DomainInvestigation
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.orchestration.tianyancha_toolset import TianyanchaHybridToolset
from jindiao.scenarios import ScenarioRepository
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    McpCallResult,
    McpErrorKind,
    TianyanchaMcpError,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class RecordingLiveToolset:
    mock_domains = frozenset({"governance", "judicial", "operations", "peers"})

    def __init__(self) -> None:
        self.context: RunContext | None = None
        self.closed = False
        self.delegate = ScenarioToolset(clock=lambda: NOW)

    async def resolve_subject(self, context: RunContext) -> ResolvedSubject:
        self.context = context
        assert context.requested_enterprise is not None
        return ResolvedSubject(
            subject_id="tyc:123",
            company_name=context.requested_enterprise.company_name or "",
            unified_social_credit_code="91110000LIVE000001",
            region="北京市",
            registration_status="存续",
            source=SubjectSource.TIANYANCHA,
            resolved_at=NOW,
        )

    async def domain_capabilities(
        self,
        subject: ResolvedSubject,
    ) -> dict[str, tuple[str, ...]]:
        return {
            domain: (f"get_{domain}_record",)
            for domain in ("governance", "judicial", "operations", "peers")
        }

    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation:
        return await self.delegate.investigate(context, subject, domain)

    async def aclose(self) -> None:
        self.closed = True


class AllDomainFailureClient:
    tools = (
        "get_company_registration_info",
        "get_risk_overview",
        "get_annual_reports",
        "get_competitors",
    )

    def __init__(self) -> None:
        self.closed = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
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
            raise TianyanchaMcpError(McpErrorKind.TIMEOUT, "safe timeout")
        raise AssertionError(f"unexpected tool: {name}")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_service_uses_live_subject_with_explicit_frozen_supplement_template() -> None:
    toolset = RecordingLiveToolset()
    settings = Settings(
        model_provider="offline_mock",
        model_name="deterministic-test-model",
        data_source_mode="tianyancha",
        tianyancha_authorization=SecretStr("test-only-token"),
        live_fallback_scenario_id="normal-enterprise",
    )
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        toolset_factory=lambda: toolset,
        id_factory=lambda: "live-id",
        clock=lambda: NOW,
    )

    result = await service.run(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="公开样本有限公司"),
        )
    )

    assert toolset.context is not None
    assert toolset.context.scenario.manifest.scenario_id == "normal-enterprise"
    assert toolset.context.requested_enterprise == EnterpriseInput(company_name="公开样本有限公司")
    assert result.schema_version == "prototype-v1"
    assert result.subject.company_name == "公开样本有限公司"
    assert result.subject.subject_id == "tyc:123"
    assert result.summary.ai_suggestion == "manual_review"
    assert toolset.closed is True


@pytest.mark.asyncio
async def test_explicit_degraded_mode_builds_complete_report_when_all_live_domains_fail(
    tmp_path: Path,
) -> None:
    client = AllDomainFailureClient()
    settings = Settings(
        model_provider="offline_mock",
        model_name="deterministic-test-model",
        data_source_mode="tianyancha",
        tianyancha_authorization=SecretStr("test-only-token"),
        live_fallback_scenario_id="normal-enterprise",
        artifact_root=tmp_path,
    )
    routing = CapabilityRoutingConfig.from_file(Path("config/tianyancha-capability-routes.json"))
    ids = iter(("request-live-failure", "run-live-failure"))
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        toolset_factory=lambda: TianyanchaHybridToolset(
            client=client,
            routing=routing,
            clock=lambda: NOW,
        ),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
    )

    result = await service.run(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="公开样本有限公司"),
            allow_degraded_mock=True,
        )
    )

    assert result.subject.company_name == "公开样本有限公司"
    assert result.subject.unified_social_credit_code == "91110000LIVE000001"
    assert set(type(result.report).model_fields) == {
        "business_plan",
        "company_profile",
        "ownership",
        "business_analysis",
        "financial_analysis",
        "bank_flow_analysis",
        "external_verification",
        "risk_points",
    }
    assert result.report.company_profile.status != "unavailable"
    assert "## §1 公司基本情况" in result.report_markdown
    assert "## §4 财务分析·三表与比率" in result.report_markdown
    assert "## §6 外部多源核验" in result.report_markdown
    assert "## §7 风险点提示" in result.report_markdown
    assert result.meta.is_mock is True
    assert result.evidence
    assert {item.source_type for item in result.evidence} <= {"mock", "derived"}
    assert any(item.source_type == "mock" for item in result.evidence)
    assert all(item.is_mock for item in result.evidence)
    assert result.report_markdown.count("Mock 数据提示") == 1
    assert "- source_summary:" not in result.report_markdown
    assert client.closed is True
