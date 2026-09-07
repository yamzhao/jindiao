from __future__ import annotations

from jindiao.application.errors import (
    EntityAmbiguousError,
    ScenarioIntegrityError,
    SourceUnavailableError,
    error_to_record,
    http_status_for_error,
)
from jindiao.contracts.errors import ErrorCategory, ErrorCode


def test_entity_ambiguity_maps_to_conflict_and_non_recoverable_record() -> None:
    error = EntityAmbiguousError(
        "企业名称存在多个候选",
        details={"candidate_ids": ["a", "b"]},
    )

    record = error_to_record(error)

    assert record.category is ErrorCategory.ENTITY
    assert record.code is ErrorCode.ENTITY_AMBIGUOUS
    assert record.recoverable is False
    assert record.details == {"candidate_ids": ["a", "b"]}
    assert http_status_for_error(error) == 409


def test_source_error_is_recoverable_and_maps_to_service_unavailable() -> None:
    error = SourceUnavailableError("天眼查超时", details={"tool": "risk_overview"})

    record = error_to_record(error)

    assert record.category is ErrorCategory.SOURCE
    assert record.code is ErrorCode.SOURCE_UNAVAILABLE
    assert record.recoverable is True
    assert http_status_for_error(error) == 503


def test_integrity_error_is_not_recoverable() -> None:
    error = ScenarioIntegrityError("场景哈希不一致")

    record = error_to_record(error)

    assert record.category is ErrorCategory.SCENARIO
    assert record.code is ErrorCode.SCENARIO_INTEGRITY
    assert record.recoverable is False
    assert http_status_for_error(error) == 422


def test_unknown_error_maps_to_safe_internal_record() -> None:
    record = error_to_record(RuntimeError("database password is secret"))

    assert record.category is ErrorCategory.INTERNAL
    assert record.code is ErrorCode.INTERNAL_ERROR
    assert record.message == "Internal application error"
    assert record.details == {}
    assert record.recoverable is False
