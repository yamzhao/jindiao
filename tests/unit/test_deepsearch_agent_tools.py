from __future__ import annotations

import importlib
from datetime import UTC, date, datetime
from typing import Any

import pytest
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import SourceStatus
from jindiao.deepsearch import AnnualReportSocialSecurityOutcome

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


@pytest.mark.asyncio
async def test_registered_annual_report_tool_delegates_typed_input_and_closes_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("jindiao.agents.deepsearch_tools")
    created: list[FakeAnnualReportProvider] = []

    class FakeAnnualReportProvider:
        def __init__(self, **kwargs: object) -> None:
            self.config = kwargs
            self.calls: list[tuple[ResolvedSubject, datetime, date]] = []
            self.closed = False
            created.append(self)

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

    monkeypatch.setattr(module, "TianyanchaAnnualReportProvider", FakeAnnualReportProvider)
    module.register_deepsearch_tool_providers()
    tool = BuiltinToolSpec(
        type=module.TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE,
        params={"max_lookback_years": 2, "timeout_seconds": 7},
    ).build(language="zh")

    result = await tool.invoke(
        {
            "subject": subject().model_dump(mode="json"),
            "queried_at": NOW.isoformat(),
            "report_as_of": AS_OF.isoformat(),
        }
    )

    assert result["source_status"] == "verified_empty"
    assert result["checked_years"] == [2025, 2024]
    assert len(created) == 1
    assert created[0].config == {"max_lookback_years": 2, "timeout_seconds": 7.0}
    assert created[0].calls == [(subject(), NOW, AS_OF)]
    assert created[0].closed is True
    assert tool.card.name == "tianyancha_annual_report_social_security"


def test_annual_report_tool_rejects_unbounded_provider_configuration() -> None:
    module: Any = importlib.import_module("jindiao.agents.deepsearch_tools")
    module.register_deepsearch_tool_providers()

    with pytest.raises(ValueError, match="max_lookback_years"):
        BuiltinToolSpec(
            type=module.TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE,
            params={"max_lookback_years": 11},
        ).build(language="zh")
    with pytest.raises(ValueError, match="timeout_seconds"):
        BuiltinToolSpec(
            type=module.TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE,
            params={"timeout_seconds": 61},
        ).build(language="zh")
