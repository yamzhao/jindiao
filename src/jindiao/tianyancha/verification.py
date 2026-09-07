"""Safe, aggregate-only Tianyancha integration verification."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.evidence import SourceStatus

from .capabilities import CapabilityRoutingConfig, CompanyCapabilityService
from .entity import McpToolCaller, TianyanchaEntityResolver
from .normalizer import EvidenceDomain, TianyanchaEvidenceNormalizer
from .source_state import SourceObservation, SourceStateMachine


class TianyanchaVerificationSummary(ContractModel):
    """Non-sensitive connectivity facts safe to print or attach to verification notes."""

    company_name: str = Field(min_length=1)
    subject_source: str = Field(min_length=1)
    capability_count: int = Field(ge=0)
    route_counts: dict[str, int]
    basic_record_count: int = Field(ge=0)
    basic_record_state: SourceStatus
    internal_tool: str | None = None
    internal_record_count: int = Field(default=0, ge=0)
    internal_record_state: SourceStatus | None = None
    empty_candidate_count: int = Field(ge=0)
    empty_state: SourceStatus


async def verify_tianyancha_connection(
    *,
    client: McpToolCaller,
    company_name: str,
    empty_query: str,
    routing: CapabilityRoutingConfig,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> TianyanchaVerificationSummary:
    """Exercise subject, capability, record and empty paths without returning raw payloads."""

    resolver = TianyanchaEntityResolver(client, clock=clock)
    subject = await resolver.resolve(EnterpriseInput(company_name=company_name))
    manifest = await CompanyCapabilityService(
        client,
        ttl=timedelta(minutes=5),
        clock=clock,
    ).get(subject)
    route_counts = {
        domain: len(routing.select(manifest, domain))
        for domain in ("governance", "judicial", "operations", "peers")
    }

    queried_at = clock()
    basic_result = await client.call_tool(
        "get_company_basic_profile",
        {"company_name": subject.company_name},
    )
    basic_batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.GOVERNANCE,
        subject=subject,
        tool_name="get_company_basic_profile",
        result=basic_result,
        queried_at=queried_at,
        as_of_date=None,
        raw_snapshot_ref="artifact://live-verification/not-persisted",
    )
    basic_state = SourceStateMachine.transition(
        SourceObservation(
            capability_available=True,
            query_succeeded=True,
            record_count=basic_batch.record_count,
        )
    ).status

    internal_tool = "get_annual_reports" if "get_annual_reports" in manifest.tool_names else None
    internal_count = 0
    internal_state: SourceStatus | None = None
    if internal_tool is not None:
        internal_result = await client.call_tool(
            "call_tool",
            {
                "tool_name": internal_tool,
                "company_name": subject.company_name,
                "arguments": {"page": 1, "page_size": 3},
            },
        )
        internal_batch = TianyanchaEvidenceNormalizer().normalize(
            domain=EvidenceDomain.OPERATIONS,
            subject=subject,
            tool_name=internal_tool,
            result=internal_result,
            queried_at=clock(),
            as_of_date=None,
            raw_snapshot_ref="artifact://live-verification/not-persisted",
        )
        internal_count = internal_batch.record_count
        internal_state = SourceStateMachine.transition(
            SourceObservation(
                capability_available=True,
                query_succeeded=True,
                record_count=internal_count,
            )
        ).status

    empty_candidates = await resolver.search(EnterpriseInput(company_name=empty_query))
    empty_state = SourceStateMachine.transition(
        SourceObservation(
            capability_available=True,
            query_succeeded=True,
            record_count=len(empty_candidates),
        )
    ).status
    return TianyanchaVerificationSummary(
        company_name=subject.company_name,
        subject_source=subject.source.value,
        capability_count=len(manifest.tools),
        route_counts=route_counts,
        basic_record_count=basic_batch.record_count,
        basic_record_state=basic_state,
        internal_tool=internal_tool,
        internal_record_count=internal_count,
        internal_record_state=internal_state,
        empty_candidate_count=len(empty_candidates),
        empty_state=empty_state,
    )


__all__ = ["TianyanchaVerificationSummary", "verify_tianyancha_connection"]
