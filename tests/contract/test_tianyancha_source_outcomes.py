from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.evidence import Evidence, SourceStatus, SourceType
from jindiao.tianyancha import McpErrorKind, SourceObservation, SourceStateMachine
from jindiao.tianyancha.client import TianyanchaMcpError

NOW = datetime(2026, 9, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    ("kind", "retryable"),
    [
        (McpErrorKind.TIMEOUT, True),
        (McpErrorKind.RATE_LIMIT, True),
        (McpErrorKind.AUTHENTICATION, False),
        (McpErrorKind.PROTOCOL, False),
    ],
)
def test_source_errors_have_stable_retry_classification(
    kind: McpErrorKind,
    retryable: bool,
) -> None:
    assert TianyanchaMcpError(kind, "safe diagnostic").retryable is retryable


def test_record_empty_and_capability_absence_are_distinct_contract_states() -> None:
    records = SourceStateMachine.transition(
        SourceObservation(
            capability_available=True,
            query_succeeded=True,
            record_count=1,
        )
    )
    empty = SourceStateMachine.transition(
        SourceObservation(
            capability_available=True,
            query_succeeded=True,
            record_count=0,
        )
    )
    absent = SourceStateMachine.transition(SourceObservation(capability_available=False))

    assert records.status is SourceStatus.VERIFIED_RECORDS
    assert empty.status is SourceStatus.VERIFIED_EMPTY
    assert absent.status is SourceStatus.CAPABILITY_ABSENT
    assert empty.mock_fallback_allowed is False
    assert absent.mock_fallback_allowed is True


def test_derived_fact_requires_an_explicit_source_chain() -> None:
    values = {
        "evidence_id": "ev-derived-1",
        "claim": "近三年营收持续下降",
        "value": True,
        "subject_id": "tyc:22822",
        "source_type": SourceType.DERIVED,
        "source_status": SourceStatus.VERIFIED_RECORDS,
        "source_tool": None,
        "source_record_id": "calculation:revenue-trend",
        "queried_at": NOW,
        "confidence": 0.9,
        "is_mock": False,
        "supports_fields": ["operations.revenue_trend"],
        "raw_ref": "derived://run-1/revenue-trend",
        "source_chain": [],
    }

    with pytest.raises(ValidationError, match="derived evidence"):
        Evidence.model_validate(values)

    derived = Evidence.model_validate(
        values
        | {
            "source_chain": [
                "artifact://run-1/tianyancha/financial.json#2023",
                "artifact://run-1/tianyancha/financial.json#2025",
            ]
        }
    )
    assert len(derived.source_chain) == 2
