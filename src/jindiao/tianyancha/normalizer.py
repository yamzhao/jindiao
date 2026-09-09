"""Normalize Tianyancha tool payloads into evidence-domain contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from urllib.parse import quote

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import Evidence, SourceStatus, SourceType

from .client import McpCallResult


class EvidenceDomain(StrEnum):
    GOVERNANCE = "governance"
    JUDICIAL = "judicial"
    OPERATIONS = "operations"
    PEERS = "peers"


class PaginationMetadata(ContractModel):
    page: int | None = Field(default=None, ge=1)
    page_size: int | None = Field(default=None, ge=1)
    total_count: int | None = Field(default=None, ge=0)
    has_more: bool | None = None

    def is_truncated(self, *, returned_count: int) -> bool:
        if self.has_more is True:
            return True
        return self.total_count is not None and self.total_count > returned_count


class NormalizedEvidenceBatch(ContractModel):
    domain: EvidenceDomain
    evidence: tuple[Evidence, ...]
    record_count: int = Field(ge=0)
    pagination: PaginationMetadata | None = None
    available_years: tuple[int, ...] = ()
    selected_years: tuple[int, ...] = ()
    links: tuple[str, ...] = ()
    statement_scope: str | None = None
    amount_multiplier: int = Field(default=1, ge=1)
    partial_reasons: tuple[str, ...] = ()


_DOMAIN_CLAIMS = {
    EvidenceDomain.GOVERNANCE: "天眼查工商治理记录",
    EvidenceDomain.JUDICIAL: "天眼查司法合规记录",
    EvidenceDomain.OPERATIONS: "天眼查经营情况记录",
    EvidenceDomain.PEERS: "天眼查关联同业记录",
}

_FIELD_SIGNALS: dict[EvidenceDomain, tuple[tuple[str, tuple[str, ...]], ...]] = {
    EvidenceDomain.GOVERNANCE: (
        ("governance.shareholders", ("shareholder", "holder", "股东", "percent")),
        ("governance.executives", ("executive", "staff", "主要人员", "position")),
        ("governance.investments", ("investment", "invest", "对外投资")),
        ("governance.changes", ("change", "变更")),
    ),
    EvidenceDomain.JUDICIAL: (
        ("judicial.cases", ("case", "caseno", "案号", "judgment", "execution")),
        ("judicial.amounts", ("amount", "金额")),
        ("judicial.dishonest", ("dishonest", "失信", "未履行")),
        ("judicial.restrictions", ("restriction", "限制高消费")),
    ),
    EvidenceDomain.OPERATIONS: (
        ("operations.abnormal_operations", ("abnormal", "经营异常")),
        ("operations.penalties", ("penalty", "行政处罚")),
        ("operations.financials", ("revenue", "profit", "financial", "营收", "利润")),
        ("operations.intellectual_property", ("patent", "copyright", "专利", "著作权")),
        ("operations.bids", ("bid", "tender", "招投标")),
    ),
    EvidenceDomain.PEERS: (
        ("peers.companies", ("peercompany", "companyname", "同行", "竞品")),
        ("peers.metrics", ("rank", "median", "metric", "排名", "中位数")),
        ("peers.industry", ("industry", "行业")),
    ),
}


def _extract_records(value: object) -> list[Mapping[str, object]] | None:
    if not isinstance(value, Mapping):
        return None
    for collection in ("items", "rows", "records", "list"):
        items = value.get(collection)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, Mapping)]
    for key in ("result", "data"):
        if key in value:
            nested = value.get(key)
            found = _extract_records(nested)
            if found is not None:
                return found
    return [value]


def _pagination_only_record(record: Mapping[str, object]) -> bool:
    keys = {str(key).replace("_", "").casefold() for key in record}
    return keys <= {
        "page",
        "pagenum",
        "pageno",
        "pagenumber",
        "currentpage",
        "pagesize",
        "size",
        "limit",
        "total",
        "totalcount",
        "count",
        "hasmore",
        "hasnext",
        "code",
        "status",
        "message",
    }


def _markdown_records(text: str) -> list[dict[str, object]]:
    def cells(line: str) -> list[str]:
        if not line.startswith("|"):
            return []
        inner = line[1:]
        if inner.endswith("|") and not inner.endswith(r"\|"):
            inner = inner[:-1]
        return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", inner)]

    rows = [cells(line.strip()) for line in text.splitlines()]

    def header_at(index: int) -> bool:
        return (
            index + 1 < len(rows)
            and bool(rows[index])
            and all(rows[index])
            and len(rows[index]) == len(rows[index + 1])
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[index + 1])
        )

    records: list[dict[str, object]] = []
    index = 0
    while index + 1 < len(rows):
        if not header_at(index):
            index += 1
            continue
        headers = rows[index]
        index += 2
        while index < len(rows):
            if header_at(index) or len(rows[index]) != len(headers):
                break
            # Some live tables repeat 人员ID. Keep agreeing values, but do not
            # arbitrarily choose between conflicting disclosures of one field.
            record: dict[str, object] = {}
            conflicts: set[str] = set()
            for key, value in zip(headers, rows[index], strict=True):
                if key in record and record[key] != value:
                    conflicts.add(key)
                record[key] = value
            records.append({key: value for key, value in record.items() if key not in conflicts})
            index += 1
    return records


def _walk(value: object) -> list[object]:
    values = [value]
    if isinstance(value, Mapping):
        for child in value.values():
            values.extend(_walk(child))
    elif isinstance(value, list | tuple):
        for child in value:
            values.extend(_walk(child))
    return values


def _available_years(value: object, texts: tuple[str, ...]) -> tuple[int, ...]:
    years: set[int] = set()
    for current in _walk(value):
        if isinstance(current, Mapping):
            for key, child in current.items():
                normalized = str(key).replace("_", "").casefold()
                if normalized in {"availableyears", "yearlist", "years"}:
                    for item in _walk(child):
                        if isinstance(item, int) and 1900 <= item <= 2100:
                            years.add(item)
                        elif isinstance(item, str):
                            years.update(int(year) for year in re.findall(r"(?:19|20)\d{2}", item))
    for text in texts:
        if any(marker in text.casefold() for marker in ("available", "可用年份", "年度目录")):
            years.update(int(year) for year in re.findall(r"(?:19|20)\d{2}", text))
    return tuple(sorted(years, reverse=True))


def _links(value: object, texts: tuple[str, ...]) -> tuple[str, ...]:
    urls: set[str] = set()
    for item in (*_walk(value), *texts):
        if isinstance(item, str):
            urls.update(re.findall(r"https?://[^\s)\]}>\"']+", item))
    return tuple(sorted(urls))


def _scope(value: object, texts: tuple[str, ...]) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if (
                str(key).replace("_", "").casefold()
                in {
                    "scope",
                    "statementscope",
                    "consolidationscope",
                    "reporttype",
                }
                and isinstance(child, str)
                and child.strip()
            ):
                return child.strip()
    combined = " ".join(texts)
    if "合并" in combined:
        return "consolidated"
    if "母公司" in combined or "单体" in combined:
        return "standalone"
    return None


def _amount_multiplier(value: object, texts: tuple[str, ...]) -> int:
    combined = json.dumps(value, ensure_ascii=False) + " " + " ".join(texts)
    match = re.search(
        r"(?:单位|unit)\s*[:\N{FULLWIDTH COLON}=]?\s*(亿元|万元|千元|元|CNY)",
        combined,
        re.I,
    )
    if match is None:
        return 1
    return {"亿元": 100_000_000, "万元": 10_000, "千元": 1_000}.get(match.group(1), 1)


def _selected_years(
    years: tuple[int, ...], *, as_of_date: date | None, lookback: int = 3
) -> tuple[int, ...]:
    cutoff = as_of_date.year if as_of_date is not None else 2100
    return tuple(year for year in years if year <= cutoff)[:lookback]


def _link_only_payload(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    allowed = {
        "url",
        "link",
        "snapshoturl",
        "downloadurl",
        "title",
        "name",
        "status",
        "message",
    }
    return all(str(key).replace("_", "").casefold() in allowed for key in value)


def _year_directory_payload(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = {str(key).replace("_", "").casefold() for key in value}
    return bool(keys & {"availableyears", "yearlist", "years"}) and keys <= {
        "availableyears",
        "yearlist",
        "years",
        "links",
        "url",
        "message",
        "status",
    }


def _first(value: Mapping[str, object], *names: str) -> object | None:
    return next((value[name] for name in names if name in value), None)


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
    elif isinstance(value, str | bytes | bytearray):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed >= 0 else None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def _extract_pagination(value: object) -> PaginationMetadata | None:
    if not isinstance(value, Mapping):
        return None
    page = _as_int(_first(value, "page", "page_no", "pageNum", "pageNo"))
    page_size = _as_int(_first(value, "page_size", "pageSize", "limit"))
    total_count = _as_int(_first(value, "total_count", "totalCount", "total", "count"))
    has_more = _as_bool(_first(value, "has_more", "hasMore"))
    if any(item is not None for item in (page, page_size, total_count, has_more)):
        return PaginationMetadata(
            page=page if page and page >= 1 else None,
            page_size=page_size if page_size and page_size >= 1 else None,
            total_count=total_count,
            has_more=has_more,
        )
    for key in ("result", "data"):
        nested = _extract_pagination(value.get(key))
        if nested is not None:
            return nested
    return None


def _record_id(record: Mapping[str, object], canonical: str) -> str:
    for key in ("id", "record_id", "caseNo", "case_no", "uuid"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _supports_fields(domain: EvidenceDomain, record: Mapping[str, object]) -> tuple[str, ...]:
    key_text = " ".join(str(key).casefold() for key in record)
    fields = [
        field
        for field, signals in _FIELD_SIGNALS[domain]
        if any(signal.casefold() in key_text for signal in signals)
    ]
    return tuple(fields) or (f"{domain.value}.records",)


class TianyanchaEvidenceNormalizer:
    """Convert structured or Markdown MCP results into traceable Evidence."""

    def normalize(
        self,
        *,
        domain: EvidenceDomain,
        subject: ResolvedSubject,
        tool_name: str,
        result: McpCallResult,
        queried_at: datetime,
        as_of_date: date | None,
        raw_snapshot_ref: str,
        source_parameters_hash: str | None = None,
    ) -> NormalizedEvidenceBatch:
        texts = tuple(text for text in result.text if text.strip())
        records = _extract_records(result.structured_content)
        pagination = _extract_pagination(result.structured_content)
        years = _available_years(result.structured_content, texts)
        links = _links(result.structured_content, texts)
        scope = _scope(result.structured_content, texts)
        multiplier = _amount_multiplier(result.structured_content, texts)
        values: list[object]
        markdown_records = [record for text in texts for record in _markdown_records(text)]
        # Count tables describe pagination, not a financial or risk fact. Keep
        # metadata-only evidence when no detail was supplied, without counting it.
        counts: list[int] = []
        detail_records: list[dict[str, object]] = []
        for markdown_record in markdown_records:
            count = (
                _as_int(markdown_record.get("值"))
                if set(markdown_record) == {"字段", "值"} and markdown_record.get("字段") == "总数"
                else None
            )
            if count is None:
                detail_records.append(markdown_record)
            else:
                counts.append(count)
        markdown_total = max(counts) if counts else None
        if markdown_total is not None:
            if pagination is None:
                pagination = PaginationMetadata(total_count=markdown_total)
            elif pagination.total_count is None:
                pagination = pagination.model_copy(update={"total_count": markdown_total})
        structured_details = [
            record
            for record in records or []
            if not _pagination_only_record(record)
            and not _year_directory_payload(record)
            and not (_links(record, ()) and _link_only_payload(record))
        ]
        count_only = bool(counts) and not detail_records and not structured_details
        directory_only = (
            bool(years)
            and not markdown_records
            and (records is None or (len(records) == 1 and _year_directory_payload(records[0])))
        )
        link_only = (
            bool(links)
            and not markdown_records
            and not directory_only
            and (not records or (len(records) == 1 and _link_only_payload(records[0])))
        )
        if markdown_records:
            values = list[object](detail_records or structured_details)
        elif records is None:
            values = list(texts)
        else:
            values = list(records)
        if directory_only:
            values = [{"available_years": list(years), "links": list(links)}]
        elif link_only:
            values = [{"links": list(links)}]
        if count_only:
            values = [{"total_count": markdown_total}]

        evidence_items: list[Evidence] = []
        for value in values:
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            record = value if isinstance(value, Mapping) else {}
            source_record_id = _record_id(record, canonical)
            digest = hashlib.sha256(
                f"{subject.subject_id}|{tool_name}|{source_record_id}|{canonical}".encode()
            ).hexdigest()
            content_hash = hashlib.sha256(canonical.encode()).hexdigest()
            supports_fields = (
                _supports_fields(domain, record) if record else (f"{domain.value}.raw",)
            )
            evidence_items.append(
                Evidence.model_validate(
                    {
                        "evidence_id": f"ev-tyc-{digest[:24]}",
                        "claim": _DOMAIN_CLAIMS[domain],
                        "value": value,
                        "subject_id": subject.subject_id,
                        "source_type": SourceType.TIANYANCHA,
                        "source_status": SourceStatus.VERIFIED_RECORDS,
                        "source_tool": tool_name,
                        "source_record_id": source_record_id,
                        "queried_at": queried_at,
                        "as_of_date": as_of_date,
                        "confidence": 1.0,
                        "is_mock": False,
                        "supports_fields": supports_fields,
                        "raw_ref": f"{raw_snapshot_ref}#record={quote(source_record_id, safe='')}",
                        "source_chain": (f"mcp://tianyancha/{tool_name}",),
                        "source_parameters_hash": source_parameters_hash,
                        "content_hash": f"sha256:{content_hash}",
                    }
                )
            )
        actual_record_count = (
            0 if directory_only or link_only or count_only else len(evidence_items)
        )
        selected_years = _selected_years(years, as_of_date=as_of_date)
        financial_tool = any(
            marker in tool_name
            for marker in ("financial", "income_statement", "balance_sheet", "cash_flow")
        )
        partial: list[str] = []
        if directory_only:
            partial.append("year_directory_only")
        if link_only:
            partial.append("link_only")
        if count_only and markdown_total:
            partial.append("count_only")
        if financial_tool and years and len(selected_years) < 3:
            partial.append("missing_period")
        return NormalizedEvidenceBatch(
            domain=domain,
            evidence=tuple(evidence_items),
            record_count=actual_record_count,
            pagination=pagination,
            available_years=years,
            selected_years=selected_years,
            links=links,
            statement_scope=scope,
            amount_multiplier=multiplier,
            partial_reasons=tuple(partial),
        )


__all__ = [
    "EvidenceDomain",
    "NormalizedEvidenceBatch",
    "PaginationMetadata",
    "TianyanchaEvidenceNormalizer",
]
