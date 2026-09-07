"""Member-scoped executable tools for the DeepSearch evidence agent."""

from __future__ import annotations

from datetime import date, datetime

from openjiuwen.core.foundation.tool import ToolCard, tool
from openjiuwen.harness.schema.deep_agent_spec import register_tool_provider
from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.deepsearch import TianyanchaAnnualReportProvider

TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE = "jindiao.tianyancha_annual_report_social_security"
TIANYANCHA_ANNUAL_REPORT_TOOL_NAME = "tianyancha_annual_report_social_security"


class AnnualReportToolConfig(ContractModel):
    max_lookback_years: int = Field(default=5, ge=1, le=10)
    timeout_seconds: float = Field(default=15, gt=0, le=60)


class AnnualReportToolInput(ContractModel):
    subject: ResolvedSubject
    queried_at: datetime
    report_as_of: date


def build_tianyancha_annual_report_tool(params: dict[str, object], context: object) -> object:
    """Build a short-lived Provider-backed Tool from serializable configuration."""

    del context
    config = AnnualReportToolConfig.model_validate(params)

    @tool(  # type: ignore[untyped-decorator]
        card=ToolCard(
            id="tianyancha.annual_report.fetch_latest",
            name=TIANYANCHA_ANNUAL_REPORT_TOOL_NAME,
            description=(
                "Fetch the latest eligible Tianyancha annual report from the exact "
                "www.tianyancha.com allowlist and return social-security Evidence. "
                "A missing report is a valid verified_empty outcome."
            ),
            input_params=AnnualReportToolInput,
            stateless=True,
            idempotent=True,
        )
    )
    async def fetch_latest_annual_social_security(
        subject: dict[str, object],
        queried_at: datetime,
        report_as_of: date,
    ) -> dict[str, object]:
        request = AnnualReportToolInput.model_validate(
            {
                "subject": subject,
                "queried_at": queried_at,
                "report_as_of": report_as_of,
            }
        )
        provider = TianyanchaAnnualReportProvider(
            max_lookback_years=config.max_lookback_years,
            timeout_seconds=config.timeout_seconds,
        )
        try:
            outcome = await provider.fetch_latest(
                request.subject,
                queried_at=request.queried_at,
                report_as_of=request.report_as_of,
            )
            return outcome.model_dump(mode="json")
        finally:
            await provider.aclose()

    return fetch_latest_annual_social_security


def register_deepsearch_tool_providers() -> None:
    """Register Jindiao Tool factories; repeated registration is intentionally harmless."""

    register_tool_provider(
        TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE,
        build_tianyancha_annual_report_tool,
    )


__all__ = [
    "TIANYANCHA_ANNUAL_REPORT_TOOL_NAME",
    "TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE",
    "AnnualReportToolConfig",
    "AnnualReportToolInput",
    "build_tianyancha_annual_report_tool",
    "register_deepsearch_tool_providers",
]
