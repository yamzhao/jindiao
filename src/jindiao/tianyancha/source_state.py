"""Explicit Tianyancha source-state transitions used before fallback decisions."""

from __future__ import annotations

from pydantic import Field, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.evidence import SourceStatus

from .client import McpErrorKind


class SourceObservation(ContractModel):
    capability_available: bool
    query_succeeded: bool = False
    record_count: int = Field(default=0, ge=0)
    error_kind: McpErrorKind | None = None
    degraded_mock_used: bool = False

    @model_validator(mode="after")
    def validate_state_combination(self) -> SourceObservation:
        if not self.capability_available:
            if self.query_succeeded or self.error_kind is not None or self.record_count:
                raise ValueError("absent capabilities cannot have a query outcome")
            if self.degraded_mock_used:
                raise ValueError("capability absence uses normal mock fallback, not degradation")
            return self
        if self.query_succeeded:
            if self.error_kind is not None or self.degraded_mock_used:
                raise ValueError("successful queries cannot also be errors or degraded mock")
            return self
        if self.error_kind is None:
            raise ValueError("failed capability query requires an error kind")
        if self.degraded_mock_used:
            if self.record_count == 0:
                raise ValueError("degraded mock requires at least one replacement record")
        elif self.record_count != 0:
            raise ValueError("failed source queries cannot report source records")
        return self


class SourceStateDecision(ContractModel):
    status: SourceStatus
    original_status: SourceStatus | None = None
    mock_fallback_allowed: bool


class SourceStateMachine:
    @staticmethod
    def transition(observation: SourceObservation) -> SourceStateDecision:
        if not observation.capability_available:
            return SourceStateDecision(
                status=SourceStatus.CAPABILITY_ABSENT,
                mock_fallback_allowed=True,
            )
        if observation.query_succeeded:
            return SourceStateDecision(
                status=(
                    SourceStatus.VERIFIED_RECORDS
                    if observation.record_count
                    else SourceStatus.VERIFIED_EMPTY
                ),
                mock_fallback_allowed=False,
            )
        if observation.degraded_mock_used:
            return SourceStateDecision(
                status=SourceStatus.DEGRADED_MOCK,
                original_status=SourceStatus.SOURCE_ERROR,
                mock_fallback_allowed=True,
            )
        return SourceStateDecision(
            status=SourceStatus.SOURCE_ERROR,
            mock_fallback_allowed=False,
        )


__all__ = ["SourceObservation", "SourceStateDecision", "SourceStateMachine"]
