from __future__ import annotations

from datetime import UTC, datetime

from jindiao.contracts.evidence import Evidence, SourceType
from jindiao.tianyancha import EvidenceMerger

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def evidence(
    evidence_id: str,
    *,
    value: object,
    source_type: SourceType,
    raw_ref: str,
    is_mock: bool,
) -> Evidence:
    return Evidence.model_validate(
        {
            "evidence_id": evidence_id,
            "claim": "企业登记状态",
            "value": value,
            "subject_id": "tyc:22822",
            "source_type": source_type,
            "source_tool": "company_profile" if source_type is SourceType.TIANYANCHA else None,
            "source_record_id": "registration-status",
            "queried_at": NOW,
            "confidence": 1,
            "is_mock": is_mock,
            "supports_fields": ["company.registration_status"],
            "raw_ref": raw_ref,
            "source_chain": [],
        }
    )


def test_merger_deduplicates_same_fact_and_preserves_all_source_refs() -> None:
    mock = evidence(
        "ev-mock",
        value="存续",
        source_type=SourceType.MOCK,
        raw_ref="mock://normal/v1/company.json#registration_status",
        is_mock=True,
    )
    tianyancha = evidence(
        "ev-tyc",
        value="存续",
        source_type=SourceType.TIANYANCHA,
        raw_ref="artifact://run-1/tianyancha/company.json#status",
        is_mock=False,
    )

    merged = EvidenceMerger.merge((mock, tianyancha))

    assert len(merged) == 1
    assert merged[0].evidence_id == "ev-tyc"
    assert merged[0].source_type is SourceType.TIANYANCHA
    assert merged[0].source_chain == (
        "artifact://run-1/tianyancha/company.json#status",
        "mock://normal/v1/company.json#registration_status",
    )


def test_merger_does_not_collapse_conflicting_values_or_different_subjects() -> None:
    active = evidence(
        "ev-active",
        value="存续",
        source_type=SourceType.TIANYANCHA,
        raw_ref="artifact://run-1/a.json",
        is_mock=False,
    )
    cancelled = evidence(
        "ev-cancelled",
        value="注销",
        source_type=SourceType.TIANYANCHA,
        raw_ref="artifact://run-1/b.json",
        is_mock=False,
    )
    other_subject = active.model_copy(update={"evidence_id": "ev-other", "subject_id": "tyc:99881"})

    merged = EvidenceMerger.merge((active, cancelled, other_subject))

    assert {item.evidence_id for item in merged} == {"ev-active", "ev-cancelled", "ev-other"}


def test_merger_is_deterministic_for_input_order_and_repeated_records() -> None:
    first = evidence(
        "ev-b",
        value={"status": "存续"},
        source_type=SourceType.TIANYANCHA,
        raw_ref="artifact://run-1/b.json",
        is_mock=False,
    )
    second = first.model_copy(update={"evidence_id": "ev-a", "raw_ref": "artifact://run-1/a.json"})

    forward = EvidenceMerger.merge((first, second, first))
    reverse = EvidenceMerger.merge((second, first))

    assert forward == reverse
    assert forward[0].evidence_id == "ev-a"
