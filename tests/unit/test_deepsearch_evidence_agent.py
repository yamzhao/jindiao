from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jindiao.agents.deepsearch_agent import DeepSearchEvidenceAgent
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageGapReason, SourceStatus
from jindiao.deepsearch import (
    AnnualReportSocialSecurityOutcome,
    EvidenceGap,
    SupplementOutcome,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)
AS_OF = date(2026, 9, 4)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:2962178558",
        company_name="同盾科技（上海）有限公司",  # noqa: RUF001
        unified_social_credit_code="91310104MA1FR5Q84D",
        region="上海市",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


def gap() -> EvidenceGap:
    return EvidenceGap(
        gap_id="gap-governance-registration",
        subject_id=subject().subject_id,
        domain="governance",
        submodule_id="registration",
        capability="get_company_registration_info",
        topic="工商登记信息",
        supports_fields=("governance.registration",),
        reason=CoverageGapReason.PAGINATION_TRUNCATED,
    )


class SupplementService:
    def __init__(self) -> None:
        self.calls: list[tuple[ResolvedSubject, EvidenceGap, datetime, date]] = []

    async def research(
        self,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
    ) -> SupplementOutcome:
        self.calls.append((subject, gap, queried_at, report_as_of))
        return SupplementOutcome(
            gap=gap,
            evidence=(),
            executed_queries=(),
            rounds_executed=1,
            unresolved=True,
        )


class AnnualReportProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[ResolvedSubject, datetime, date]] = []
        self.closed = False

    async def fetch_latest(
        self,
        subject: ResolvedSubject,
        *,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome:
        self.calls.append((subject, queried_at, report_as_of))
        return AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=(2025, 2024),
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_agent_delegates_structured_gap_to_bounded_research_service() -> None:
    service = SupplementService()
    agent = DeepSearchEvidenceAgent(supplement_service=service)

    outcome = await agent.research_gap(
        subject=subject(),
        gap=gap(),
        queried_at=NOW,
        report_as_of=AS_OF,
    )

    assert outcome.unresolved is True
    assert service.calls == [(subject(), gap(), NOW, AS_OF)]


@pytest.mark.asyncio
async def test_agent_rejects_unconfigured_deepsearch_capabilities() -> None:
    agent = DeepSearchEvidenceAgent()

    with pytest.raises(AgentExecutionError, match="not configured"):
        await agent.research_gap(
            subject=subject(),
            gap=gap(),
            queried_at=NOW,
            report_as_of=AS_OF,
        )
    with pytest.raises(AgentExecutionError, match="not configured"):
        await agent.fetch_annual_report_social_security(
            subject=subject(),
            queried_at=NOW,
            report_as_of=AS_OF,
        )


@pytest.mark.asyncio
async def test_agent_delegates_annual_report_and_closes_owned_provider() -> None:
    provider = AnnualReportProvider()
    agent = DeepSearchEvidenceAgent(annual_report_provider=provider)

    outcome = await agent.fetch_annual_report_social_security(
        subject=subject(),
        queried_at=NOW,
        report_as_of=AS_OF,
    )
    await agent.aclose()

    assert outcome.source_status is SourceStatus.VERIFIED_EMPTY
    assert provider.calls == [(subject(), NOW, AS_OF)]
    assert provider.closed is True
