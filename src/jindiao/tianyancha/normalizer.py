"""Normalize Tianyancha tool payloads into evidence-domain contracts."""

from __future__ import annotations

import hashlib
import json
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
    items = value.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, Mapping)]
    for key in ("result", "data"):
        if key in value:
            nested = value.get(key)
            found = _extract_records(nested)
            if found is not None:
                return found
    return [value]


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
        records = _extract_records(result.structured_content)
        pagination = _extract_pagination(result.structured_content)
        values: list[object]
        if records is None:
            values = [text for text in result.text if text.strip()]
        else:
            values = list(records)

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
        return NormalizedEvidenceBatch(
            domain=domain,
            evidence=tuple(evidence_items),
            record_count=len(evidence_items),
            pagination=pagination,
        )


__all__ = [
    "EvidenceDomain",
    "NormalizedEvidenceBatch",
    "PaginationMetadata",
    "TianyanchaEvidenceNormalizer",
]
