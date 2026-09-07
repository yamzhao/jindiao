from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageGapReason, SourceType
from jindiao.deepsearch import (
    BoundedEvidenceSupplementService,
    BoundedSupplementQueryPlanner,
    EvidenceGap,
    SupplementDocument,
    SupplementQuery,
    SupplementSearchCandidate,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)
AS_OF = date(2026, 8, 31)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:22822",
        company_name="示例科技有限公司",
        unified_social_credit_code="91110000EXAMPLE001",
        region="北京市",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


def gap() -> EvidenceGap:
    return EvidenceGap(
        gap_id="gap-governance-registration",
        subject_id="tyc:22822",
        domain="governance",
        submodule_id="registration",
        capability="get_company_registration_info",
        topic="工商登记信息",
        supports_fields=("governance.registration",),
        reason=CoverageGapReason.PAGINATION_TRUNCATED,
    )


def test_gap_query_planner_generates_at_most_two_anchored_initial_queries() -> None:
    resolved = subject()
    queries = BoundedSupplementQueryPlanner().initial_queries(resolved, gap())
    credit_code = resolved.unified_social_credit_code

    assert credit_code is not None
    assert 1 <= len(queries) <= 2
    assert len({item.text for item in queries}) == len(queries)
    assert all(item.round_number == 1 for item in queries)
    assert all(item.gap_id == gap().gap_id for item in queries)
    assert all(resolved.company_name in item.text or credit_code in item.text for item in queries)


def test_gap_query_planner_rewrites_once_without_repeating_initial_queries() -> None:
    planner = BoundedSupplementQueryPlanner()
    first = planner.initial_queries(subject(), gap())

    rewritten = planner.rewrite_query(
        subject(),
        gap(),
        executed_queries=tuple(item.text for item in first),
    )

    assert rewritten is not None
    assert rewritten.round_number == 2
    assert rewritten.text not in {item.text for item in first}
    assert subject().company_name in rewritten.text


def test_gap_query_planner_rejects_a_gap_for_another_subject() -> None:
    other = gap().model_copy(update={"subject_id": "tyc:other"})

    with pytest.raises(ValueError, match="subject"):
        BoundedSupplementQueryPlanner().initial_queries(subject(), other)


class FakeSupplementProvider:
    def __init__(
        self,
        *,
        candidates: tuple[SupplementSearchCandidate, ...],
        documents: dict[str, SupplementDocument | None],
    ) -> None:
        self.candidates = candidates
        self.documents = documents
        self.search_calls: list[SupplementQuery] = []
        self.fetch_calls: list[str] = []

    async def search(
        self,
        query: SupplementQuery,
        *,
        limit: int,
    ) -> tuple[SupplementSearchCandidate, ...]:
        self.search_calls.append(query)
        return self.candidates[:limit]

    async def fetch(
        self,
        candidate: SupplementSearchCandidate,
        *,
        goal: str,
    ) -> SupplementDocument | None:
        self.fetch_calls.append(candidate.url)
        return self.documents.get(candidate.url)


def candidate(*, url: str = "https://example.gov.cn/notices/1") -> SupplementSearchCandidate:
    return SupplementSearchCandidate(
        candidate_id="candidate-1",
        title="示例科技有限公司工商公告",
        url=url,
        snippet="该公司相关公告摘要。仅作为搜索线索。",
        publisher="示例监管机构",
        published_at=date(2026, 8, 1),
        rank=1,
    )


def document(
    *,
    content: str = "示例科技有限公司统一社会信用代码91110000EXAMPLE001。工商登记信息发生更新。",
    published_at: date | None = date(2026, 8, 1),
    url: str = "https://example.gov.cn/notices/1",
) -> SupplementDocument:
    return SupplementDocument(
        candidate_id="candidate-1",
        title="示例科技有限公司工商公告",
        url=url,
        publisher="示例监管机构",
        published_at=published_at,
        content=content,
    )


@pytest.mark.asyncio
async def test_search_snippet_without_fetched_document_never_becomes_evidence() -> None:
    item = candidate()
    provider = FakeSupplementProvider(candidates=(item,), documents={item.url: None})

    outcome = await BoundedEvidenceSupplementService(provider).research(
        subject=subject(),
        gap=gap(),
        queried_at=NOW,
        report_as_of=AS_OF,
    )

    assert outcome.evidence == ()
    assert outcome.unresolved is True
    assert outcome.rounds_executed == 2
    assert len(outcome.executed_queries) <= 3


@pytest.mark.asyncio
async def test_duplicate_search_urls_are_fetched_once_and_emit_traceable_evidence() -> None:
    item = candidate()
    provider = FakeSupplementProvider(
        candidates=(item, item.model_copy(update={"candidate_id": "candidate-2"})),
        documents={item.url: document()},
    )

    outcome = await BoundedEvidenceSupplementService(provider).research(
        subject=subject(),
        gap=gap(),
        queried_at=NOW,
        report_as_of=AS_OF,
    )

    assert len(provider.fetch_calls) == 1
    assert outcome.rounds_executed == 1
    assert outcome.unresolved is False
    assert len(outcome.evidence) == 1
    evidence = outcome.evidence[0]
    assert evidence.source_type is SourceType.PUBLIC_WEB
    assert evidence.raw_ref == item.url
    assert evidence.content_hash is not None
    assert evidence.supports_fields == gap().supports_fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetched",
    [
        document(content="另一家企业有限公司的工商登记信息。"),
        document(published_at=date(2026, 9, 1)),
    ],
)
async def test_wrong_subject_or_future_document_is_discarded(
    fetched: SupplementDocument,
) -> None:
    item = candidate()
    provider = FakeSupplementProvider(
        candidates=(item,),
        documents={item.url: fetched},
    )

    outcome = await BoundedEvidenceSupplementService(provider).research(
        subject=subject(),
        gap=gap(),
        queried_at=NOW,
        report_as_of=AS_OF,
    )

    assert outcome.evidence == ()
    assert outcome.unresolved is True
    assert outcome.rounds_executed == 2
