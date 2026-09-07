"""Bounded, gap-driven supplemental evidence research contracts."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageGapReason,
    Evidence,
    SourceStatus,
    SourceType,
)


class EvidenceGap(ContractModel):
    gap_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    submodule_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    supports_fields: tuple[str, ...] = Field(min_length=1)
    reason: CoverageGapReason

    @model_validator(mode="after")
    def fields_must_belong_to_domain(self) -> EvidenceGap:
        prefix = f"{self.domain}."
        if any(not item.startswith(prefix) for item in self.supports_fields):
            raise ValueError("gap supports_fields must belong to its domain")
        return self


class SupplementQuery(ContractModel):
    query_id: str = Field(min_length=1)
    gap_id: str = Field(min_length=1)
    text: str = Field(min_length=2, max_length=500)
    round_number: int = Field(ge=1, le=2)


def _require_http_url(value: str) -> str:
    normalized = value.strip()
    if urlsplit(normalized).scheme.casefold() not in {"http", "https"}:
        raise ValueError("supplement source URL must use HTTP or HTTPS")
    return normalized


class SupplementSearchCandidate(ContractModel):
    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str
    snippet: str = ""
    publisher: str = ""
    published_at: date | None = None
    rank: int = Field(ge=1)

    _validate_url = field_validator("url")(_require_http_url)


class SupplementDocument(ContractModel):
    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str
    publisher: str = ""
    published_at: date | None = None
    content: str = Field(min_length=1)

    _validate_url = field_validator("url")(_require_http_url)


class SupplementalResearchProvider(Protocol):
    async def search(
        self,
        query: SupplementQuery,
        *,
        limit: int,
    ) -> tuple[SupplementSearchCandidate, ...]: ...

    async def fetch(
        self,
        candidate: SupplementSearchCandidate,
        *,
        goal: str,
    ) -> SupplementDocument | None: ...


class SupplementOutcome(ContractModel):
    gap: EvidenceGap
    evidence: tuple[Evidence, ...]
    executed_queries: tuple[SupplementQuery, ...]
    rounds_executed: int = Field(ge=1, le=2)
    unresolved: bool
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unresolved_matches_evidence(self) -> SupplementOutcome:
        if self.unresolved == bool(self.evidence):
            raise ValueError("unresolved must be true exactly when evidence is empty")
        return self


class BoundedSupplementQueryPlanner:
    """Create small deterministic query sets for one already-identified gap."""

    max_initial_queries = 2

    def initial_queries(
        self,
        subject: ResolvedSubject,
        gap: EvidenceGap,
    ) -> tuple[SupplementQuery, ...]:
        self._validate_subject(subject, gap)
        candidates = [f'"{subject.company_name}" {gap.topic}']
        if subject.unified_social_credit_code:
            candidates.append(f'"{subject.unified_social_credit_code}" {gap.topic}')
        elif subject.region:
            candidates.append(f'"{subject.company_name}" {subject.region} {gap.topic}')
        return self._build(gap, candidates[: self.max_initial_queries], round_number=1)

    def rewrite_query(
        self,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        *,
        executed_queries: tuple[str, ...],
    ) -> SupplementQuery | None:
        self._validate_subject(subject, gap)
        executed = {self._key(item) for item in executed_queries}
        candidates = (
            f'"{subject.company_name}" {gap.topic} 公告 详情',
            f'"{subject.company_name}" {gap.domain} {gap.topic}',
        )
        for candidate in candidates:
            if self._key(candidate) not in executed:
                return self._build(gap, [candidate], round_number=2)[0]
        return None

    @staticmethod
    def _validate_subject(subject: ResolvedSubject, gap: EvidenceGap) -> None:
        if subject.subject_id != gap.subject_id:
            raise ValueError("gap subject does not match the resolved subject")

    @classmethod
    def _build(
        cls,
        gap: EvidenceGap,
        texts: list[str],
        *,
        round_number: int,
    ) -> tuple[SupplementQuery, ...]:
        unique: list[SupplementQuery] = []
        seen: set[str] = set()
        for text in texts:
            normalized = " ".join(text.split())
            key = cls._key(normalized)
            if not key or key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha256(
                f"{gap.gap_id}|{round_number}|{normalized}".encode()
            ).hexdigest()[:16]
            unique.append(
                SupplementQuery(
                    query_id=f"sq-{digest}",
                    gap_id=gap.gap_id,
                    text=normalized,
                    round_number=round_number,
                )
            )
        return tuple(unique)

    @staticmethod
    def _key(value: str) -> str:
        return " ".join(value.casefold().split())


class BoundedEvidenceSupplementService:
    """Run at most two search rounds and emit Evidence only from fetched pages."""

    def __init__(
        self,
        provider: SupplementalResearchProvider,
        *,
        planner: BoundedSupplementQueryPlanner | None = None,
        search_results_per_query: int = 5,
        fetches_per_round: int = 2,
    ) -> None:
        if search_results_per_query < 1 or fetches_per_round < 1:
            raise ValueError("supplement search and fetch limits must be positive")
        self._provider = provider
        self._planner = planner or BoundedSupplementQueryPlanner()
        self._search_results_per_query = search_results_per_query
        self._fetches_per_round = fetches_per_round

    async def research(
        self,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
    ) -> SupplementOutcome:
        initial = self._planner.initial_queries(subject, gap)
        executed: list[SupplementQuery] = []
        errors: list[str] = []
        seen_fetch_urls: set[str] = set()
        evidence = await self._run_round(
            queries=initial,
            subject=subject,
            gap=gap,
            queried_at=queried_at,
            report_as_of=report_as_of,
            seen_fetch_urls=seen_fetch_urls,
            executed=executed,
            errors=errors,
        )
        if evidence:
            return SupplementOutcome(
                gap=gap,
                evidence=evidence,
                executed_queries=tuple(executed),
                rounds_executed=1,
                unresolved=False,
                errors=tuple(errors),
            )

        rewritten = self._planner.rewrite_query(
            subject,
            gap,
            executed_queries=tuple(item.text for item in executed),
        )
        if rewritten is None:
            return SupplementOutcome(
                gap=gap,
                evidence=(),
                executed_queries=tuple(executed),
                rounds_executed=1,
                unresolved=True,
                errors=tuple(errors),
            )
        evidence = await self._run_round(
            queries=(rewritten,),
            subject=subject,
            gap=gap,
            queried_at=queried_at,
            report_as_of=report_as_of,
            seen_fetch_urls=seen_fetch_urls,
            executed=executed,
            errors=errors,
        )
        return SupplementOutcome(
            gap=gap,
            evidence=evidence,
            executed_queries=tuple(executed),
            rounds_executed=2,
            unresolved=not evidence,
            errors=tuple(errors),
        )

    async def _run_round(
        self,
        *,
        queries: tuple[SupplementQuery, ...],
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
        seen_fetch_urls: set[str],
        executed: list[SupplementQuery],
        errors: list[str],
    ) -> tuple[Evidence, ...]:
        candidates: dict[str, SupplementSearchCandidate] = {}
        for query in queries:
            executed.append(query)
            try:
                found = await self._provider.search(
                    query,
                    limit=self._search_results_per_query,
                )
            except Exception as error:  # Provider failures remain scoped to this gap.
                errors.append(f"search:{type(error).__name__}")
                continue
            for candidate in found[: self._search_results_per_query]:
                canonical = self._canonical_url(candidate.url)
                current = candidates.get(canonical)
                if current is None or candidate.rank < current.rank:
                    candidates[canonical] = candidate

        evidence: list[Evidence] = []
        available = sorted(candidates.items(), key=lambda item: (item[1].rank, item[0]))
        fetch_count = 0
        for canonical_url, candidate in available:
            if canonical_url in seen_fetch_urls:
                continue
            if fetch_count >= self._fetches_per_round:
                break
            fetch_count += 1
            seen_fetch_urls.add(canonical_url)
            try:
                document = await self._provider.fetch(candidate, goal=gap.topic)
            except Exception as error:  # Provider failures remain scoped to this gap.
                errors.append(f"fetch:{type(error).__name__}")
                continue
            if not self._admissible(
                document,
                candidate=candidate,
                subject=subject,
                report_as_of=report_as_of,
            ):
                continue
            assert document is not None
            evidence.append(
                self._to_evidence(
                    document,
                    subject=subject,
                    gap=gap,
                    queried_at=queried_at,
                )
            )
        return tuple(evidence)

    @classmethod
    def _admissible(
        cls,
        document: SupplementDocument | None,
        *,
        candidate: SupplementSearchCandidate,
        subject: ResolvedSubject,
        report_as_of: date,
    ) -> bool:
        if document is None:
            return False
        if cls._canonical_url(document.url) != cls._canonical_url(candidate.url):
            return False
        if document.published_at is not None and document.published_at > report_as_of:
            return False
        # Search titles are discovery metadata and may describe a different entity;
        # require the fetched body itself to carry a resolved-subject anchor.
        content = document.content.casefold()
        anchors = [subject.company_name.casefold()]
        if subject.unified_social_credit_code:
            anchors.append(subject.unified_social_credit_code.casefold())
        return any(anchor in content for anchor in anchors)

    @classmethod
    def _to_evidence(
        cls,
        document: SupplementDocument,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
    ) -> Evidence:
        normalized_content = document.content.strip()
        content_hash = "sha256:" + hashlib.sha256(normalized_content.encode()).hexdigest()
        identity = f"{subject.subject_id}|{gap.gap_id}|{document.url}|{content_hash}"
        evidence_id = "ev-web-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        publisher = document.publisher.strip() or urlsplit(document.url).netloc
        return Evidence(
            evidence_id=evidence_id,
            claim=f"公开网页补充证据: {gap.topic}",
            value={
                "title": document.title,
                "publisher": publisher,
                "url": document.url,
                "published_at": (
                    document.published_at.isoformat() if document.published_at else None
                ),
                "content": normalized_content,
            },
            subject_id=subject.subject_id,
            source_type=SourceType.PUBLIC_WEB,
            source_status=SourceStatus.VERIFIED_RECORDS,
            source_tool="deepsearch.web_fetch",
            source_record_id=content_hash,
            queried_at=queried_at,
            as_of_date=document.published_at,
            confidence=0.8,
            is_mock=False,
            supports_fields=gap.supports_fields,
            raw_ref=document.url,
            source_chain=(document.url,),
            source_title=document.title,
            source_publisher=publisher,
            content_hash=content_hash,
        )

    @staticmethod
    def _canonical_url(value: str) -> str:
        # Keep the DeepSearch URL identity rule behind the Jindiao adapter boundary.
        from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
            canonicalize_url,
        )

        return cast(str, canonicalize_url(value))


__all__ = [
    "BoundedEvidenceSupplementService",
    "BoundedSupplementQueryPlanner",
    "EvidenceGap",
    "SupplementDocument",
    "SupplementOutcome",
    "SupplementQuery",
    "SupplementSearchCandidate",
    "SupplementalResearchProvider",
]
