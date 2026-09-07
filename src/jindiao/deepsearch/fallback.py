"""Policy-controlled conversion of local DeepSearch hits to Mock Evidence."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import Evidence, SourceStatus, SourceType
from jindiao.tianyancha import SourceStateDecision

from .interface import DeepSearchProvider, DeepSearchQuery


class MockFallbackOutcome(ContractModel):
    applied: bool
    degraded: bool
    source_status: SourceStatus
    fallback_reason: str | None = None
    evidence: tuple[Evidence, ...]

    @model_validator(mode="after")
    def validate_applied_state(self) -> MockFallbackOutcome:
        if self.applied != bool(self.evidence):
            raise ValueError("fallback applied flag must match evidence presence")
        if self.degraded != (self.source_status is SourceStatus.DEGRADED_MOCK):
            raise ValueError("degraded flag must match degraded_mock source status")
        return self


class MockFallbackService:
    def __init__(self, provider: DeepSearchProvider) -> None:
        self._provider = provider

    async def retrieve(
        self,
        *,
        source: SourceStateDecision,
        domain: str,
        capability: str,
        query: str,
        subject: ResolvedSubject,
        queried_at: datetime,
        allow_degraded_mock: bool,
    ) -> MockFallbackOutcome:
        capability_fallback = (
            source.status is SourceStatus.CAPABILITY_ABSENT and source.mock_fallback_allowed
        )
        error_fallback = source.status is SourceStatus.SOURCE_ERROR and allow_degraded_mock
        if not capability_fallback and not error_fallback:
            return MockFallbackOutcome(
                applied=False,
                degraded=False,
                source_status=source.status,
                fallback_reason=None,
                evidence=(),
            )

        hits = await self._provider.search(DeepSearchQuery(text=query))
        if not hits:
            return MockFallbackOutcome(
                applied=False,
                degraded=False,
                source_status=source.status,
                fallback_reason="mock_evidence_not_found",
                evidence=(),
            )
        effective_status = (
            SourceStatus.DEGRADED_MOCK if error_fallback else SourceStatus.CAPABILITY_ABSENT
        )
        evidence_items = tuple(
            Evidence(
                evidence_id=(
                    "ev-mock-"
                    + hashlib.sha256(
                        f"{subject.subject_id}|{domain}|{capability}|{hit.hit_id}".encode()
                    ).hexdigest()[:24]
                ),
                claim=f"Mock 补充证据: {query}",
                value=hit.content,
                subject_id=subject.subject_id,
                source_type=SourceType.MOCK,
                source_status=effective_status,
                source_tool="deepsearch.local",
                source_record_id=hit.hit_id,
                queried_at=queried_at,
                as_of_date=None,
                confidence=hit.score,
                is_mock=True,
                supports_fields=(f"{domain}.{capability}",),
                raw_ref=hit.raw_ref,
                source_chain=(hit.raw_ref,),
            )
            for hit in hits
        )
        return MockFallbackOutcome(
            applied=True,
            degraded=error_fallback,
            source_status=effective_status,
            fallback_reason=(
                "source_error_degraded_mock" if error_fallback else "tianyancha_capability_absent"
            ),
            evidence=evidence_items,
        )


__all__ = ["MockFallbackOutcome", "MockFallbackService"]
