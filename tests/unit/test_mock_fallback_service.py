from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import SourceStatus, SourceType
from jindiao.deepsearch import (
    DeepSearchHit,
    DeepSearchQuery,
    MockFallbackService,
)
from jindiao.tianyancha import SourceStateDecision

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.queries: list[DeepSearchQuery] = []

    async def aopen(self) -> None:
        return None

    async def search(self, query: DeepSearchQuery) -> tuple[DeepSearchHit, ...]:
        self.queries.append(query)
        return (
            DeepSearchHit(
                hit_id="hit-1",
                scenario_snapshot_id="normal:v1.0.0:abc",
                path="corpus/operating-notes.md",
                fragment="chunk-0001",
                content="在岗人员 128 人",
                score=1,
                raw_ref="mock://normal/v1.0.0/corpus/operating-notes.md#chunk-0001",
            ),
        )

    async def aclose(self) -> None:
        return None


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:22822",
        company_name="示例科技有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


@pytest.mark.asyncio
async def test_capability_absence_automatically_allows_mock_fallback() -> None:
    provider = FakeProvider()
    service = MockFallbackService(provider)

    outcome = await service.retrieve(
        source=SourceStateDecision(
            status=SourceStatus.CAPABILITY_ABSENT,
            mock_fallback_allowed=True,
        ),
        domain="operations",
        capability="employee_count",
        query="在岗人员",
        subject=subject(),
        queried_at=NOW,
        allow_degraded_mock=False,
    )

    assert outcome.applied is True
    assert outcome.degraded is False
    assert outcome.source_status is SourceStatus.CAPABILITY_ABSENT
    assert len(provider.queries) == 1
    assert outcome.evidence[0].source_type is SourceType.MOCK
    assert outcome.evidence[0].is_mock is True
    assert outcome.evidence[0].raw_ref.startswith("mock://")


@pytest.mark.asyncio
async def test_verified_empty_never_reads_mock() -> None:
    provider = FakeProvider()

    outcome = await MockFallbackService(provider).retrieve(
        source=SourceStateDecision(
            status=SourceStatus.VERIFIED_EMPTY,
            mock_fallback_allowed=False,
        ),
        domain="judicial",
        capability="executions",
        query="被执行记录",
        subject=subject(),
        queried_at=NOW,
        allow_degraded_mock=True,
    )

    assert outcome.applied is False
    assert outcome.evidence == ()
    assert provider.queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_degraded", [False, True])
async def test_source_error_fallback_requires_explicit_degraded_mode(
    allow_degraded: bool,
) -> None:
    provider = FakeProvider()
    outcome = await MockFallbackService(provider).retrieve(
        source=SourceStateDecision(
            status=SourceStatus.SOURCE_ERROR,
            mock_fallback_allowed=False,
        ),
        domain="operations",
        capability="employee_count",
        query="在岗人员",
        subject=subject(),
        queried_at=NOW,
        allow_degraded_mock=allow_degraded,
    )

    assert outcome.applied is allow_degraded
    assert outcome.degraded is allow_degraded
    assert outcome.source_status is (
        SourceStatus.DEGRADED_MOCK if allow_degraded else SourceStatus.SOURCE_ERROR
    )
    assert len(provider.queries) == int(allow_degraded)
    if allow_degraded:
        assert outcome.evidence[0].source_status is SourceStatus.DEGRADED_MOCK
