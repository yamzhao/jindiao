from __future__ import annotations

import pytest
from pydantic import ValidationError

from jindiao.contracts.evidence import SourceStatus
from jindiao.tianyancha import McpErrorKind, SourceObservation, SourceStateMachine


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (
            SourceObservation(
                capability_available=True,
                query_succeeded=True,
                record_count=2,
            ),
            SourceStatus.VERIFIED_RECORDS,
        ),
        (
            SourceObservation(
                capability_available=True,
                query_succeeded=True,
                record_count=0,
            ),
            SourceStatus.VERIFIED_EMPTY,
        ),
        (
            SourceObservation(capability_available=False),
            SourceStatus.CAPABILITY_ABSENT,
        ),
        (
            SourceObservation(
                capability_available=True,
                error_kind=McpErrorKind.TIMEOUT,
            ),
            SourceStatus.SOURCE_ERROR,
        ),
        (
            SourceObservation(
                capability_available=True,
                error_kind=McpErrorKind.TIMEOUT,
                degraded_mock_used=True,
                record_count=1,
            ),
            SourceStatus.DEGRADED_MOCK,
        ),
    ],
)
def test_source_state_machine_has_five_unambiguous_states(
    observation: SourceObservation,
    expected: SourceStatus,
) -> None:
    decision = SourceStateMachine.transition(observation)

    assert decision.status is expected
    assert decision.original_status is (
        SourceStatus.SOURCE_ERROR if expected is SourceStatus.DEGRADED_MOCK else None
    )
    assert decision.mock_fallback_allowed is (
        expected in {SourceStatus.CAPABILITY_ABSENT, SourceStatus.DEGRADED_MOCK}
    )


def test_valid_empty_is_not_a_capability_gap_or_fallback_state() -> None:
    decision = SourceStateMachine.transition(
        SourceObservation(
            capability_available=True,
            query_succeeded=True,
            record_count=0,
        )
    )

    assert decision.status is SourceStatus.VERIFIED_EMPTY
    assert decision.mock_fallback_allowed is False


@pytest.mark.parametrize(
    "values",
    [
        {"capability_available": False, "query_succeeded": True},
        {"capability_available": True, "query_succeeded": True, "error_kind": "timeout"},
        {"capability_available": True},
        {
            "capability_available": True,
            "error_kind": "timeout",
            "degraded_mock_used": True,
            "record_count": 0,
        },
    ],
)
def test_source_observation_rejects_impossible_state_combinations(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SourceObservation.model_validate(values)
