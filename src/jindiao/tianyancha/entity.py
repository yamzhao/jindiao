"""Enterprise search and deterministic subject anchoring."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from jindiao.application.errors import EntityAmbiguousError, EntityNotFoundError
from jindiao.contracts.entities import (
    CandidateSubject,
    EnterpriseInput,
    ResolvedSubject,
    SubjectSource,
)

from .client import McpCallResult

_ACTIVE_STATUSES = frozenset({"存续", "在业", "开业", "正常"})


class McpToolCaller(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult: ...


def _normalize(value: str) -> str:
    return "".join(value.split()).casefold()


def _first_text(record: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _find_items(value: object) -> list[Mapping[str, object]] | None:
    if isinstance(value, Mapping):
        # MCP providers have used several equivalent envelopes over time.  Keep
        # the extraction permissive at this transport boundary, while the
        # candidate validator below remains strict about id + company name.
        for key in ("items", "companies", "company_list", "companyList", "records", "results"):
            items = value.get(key)
            if isinstance(items, list):
                records = [item for item in items if isinstance(item, Mapping)]
                if records:
                    return records
        for key in ("result", "data", "payload", "response"):
            nested = value.get(key)
            found = _find_items(nested)
            if found is not None:
                return found
        # Some gateways wrap the list under a provider-specific key. Search
        # nested objects as a final, bounded fallback instead of discarding a
        # valid response merely because its envelope name changed.
        for nested in value.values():
            found = _find_items(nested)
            if found is not None:
                return found
    return None


def _markdown_records(text: str) -> list[dict[str, object]]:
    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    for index in range(len(rows) - 2):
        headers = [cell.strip() for cell in rows[index].strip("|").split("|")]
        separator = [cell.strip() for cell in rows[index + 1].strip("|").split("|")]
        if len(headers) != len(separator) or not all(set(cell) <= {"-", ":"} for cell in separator):
            continue
        records: list[dict[str, object]] = []
        for row in rows[index + 2 :]:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            if len(cells) != len(headers):
                break
            records.append(dict(zip(headers, cells, strict=True)))
        return records
    return []


def _candidate(
    record: Mapping[str, object], enterprise: EnterpriseInput
) -> CandidateSubject | None:
    subject_id = _first_text(record, "id", "company_id", "companyId", "企业ID", "公司ID")
    company_name = _first_text(
        record, "name", "company_name", "companyName", "企业名称", "公司名称"
    )
    if subject_id is None or company_name is None:
        return None
    credit_code = _first_text(
        record,
        "creditCode",
        "credit_code",
        "unified_social_credit_code",
        "统一社会信用代码",
    )
    region = _first_text(record, "base", "region", "province", "地区", "省份")
    status = _first_text(record, "regStatus", "registration_status", "status", "经营状态")
    match_score = 0.0
    if enterprise.unified_social_credit_code and credit_code:
        if _normalize(enterprise.unified_social_credit_code) == _normalize(credit_code):
            match_score = 1.0
    elif enterprise.company_name:
        expected_name = _normalize(enterprise.company_name)
        actual_name = _normalize(company_name)
        if expected_name == actual_name:
            match_score = 0.9
        elif expected_name in actual_name or actual_name in expected_name:
            match_score = 0.6
    if enterprise.region and region and _normalize(enterprise.region) == _normalize(region):
        match_score = min(1.0, match_score + 0.05)
    prefixed_id = subject_id if subject_id.startswith("tyc:") else f"tyc:{subject_id}"
    return CandidateSubject(
        subject_id=prefixed_id,
        company_name=company_name,
        unified_social_credit_code=credit_code,
        region=region,
        registration_status=status,
        match_score=match_score,
    )


class TianyanchaEntityResolver:
    """Search Tianyancha then require one exact, explainable enterprise match."""

    def __init__(
        self,
        client: McpToolCaller,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._clock = clock

    async def search(self, enterprise: EnterpriseInput) -> tuple[CandidateSubject, ...]:
        query = enterprise.unified_social_credit_code or enterprise.company_name
        if query is None:
            raise ValueError("enterprise input has no search identifier")
        result = await self._client.call_tool(
            "search_companies",
            {"query": query, "page": 1, "page_size": 20},
        )
        records = _find_items(result.structured_content)
        if records is None:
            records = []
            for text in result.text:
                records.extend(_markdown_records(text))
        candidates = tuple(
            candidate
            for record in records
            if (candidate := _candidate(record, enterprise)) is not None
        )
        return tuple(sorted(candidates, key=lambda item: (-item.match_score, item.subject_id)))

    async def resolve(self, enterprise: EnterpriseInput) -> ResolvedSubject:
        candidates = await self.search(enterprise)
        exact = [candidate for candidate in candidates if self._is_exact(candidate, enterprise)]
        if enterprise.region:
            regional = [
                candidate
                for candidate in exact
                if candidate.region
                and _normalize(candidate.region) == _normalize(enterprise.region)
            ]
            if regional:
                exact = regional
        if len(exact) > 1:
            active = [
                candidate
                for candidate in exact
                if candidate.registration_status in _ACTIVE_STATUSES
            ]
            if len(active) == 1:
                exact = active
        if not exact:
            raise EntityNotFoundError("no exact Tianyancha enterprise match was found")
        if len(exact) > 1:
            raise EntityAmbiguousError(
                "enterprise name matches multiple Tianyancha subjects",
                details={"candidate_ids": [candidate.subject_id for candidate in exact]},
            )
        selected = exact[0]
        return ResolvedSubject(
            subject_id=selected.subject_id,
            company_name=selected.company_name,
            unified_social_credit_code=selected.unified_social_credit_code,
            region=selected.region,
            registration_status=selected.registration_status,
            source=SubjectSource.TIANYANCHA,
            resolved_at=self._clock(),
        )

    @staticmethod
    def _is_exact(candidate: CandidateSubject, enterprise: EnterpriseInput) -> bool:
        if enterprise.unified_social_credit_code is not None:
            if candidate.unified_social_credit_code is None or _normalize(
                candidate.unified_social_credit_code
            ) != _normalize(enterprise.unified_social_credit_code):
                return False
        if enterprise.company_name is not None and _normalize(candidate.company_name) != _normalize(
            enterprise.company_name
        ):
            return False
        return True


__all__ = ["McpToolCaller", "TianyanchaEntityResolver"]
