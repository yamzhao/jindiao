"""Stable product-facing execution plan and step snapshots."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .results import OrchestrationMode

EXECUTION_PLAN_VERSION: Literal["due-diligence-execution-v1"] = "due-diligence-execution-v1"
EXECUTION_STEP_IDS = (
    "company-verification",
    "ownership-and-relations",
    "business-and-supply-chain",
    "finance-cashflow-solvency",
    "external-risk-screening",
    "cross-risk-review",
    "structured-report-generation",
)
BoundedId = Annotated[str, Field(min_length=1, max_length=160)]


class ExecutionStepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStepOutcome(StrEnum):
    NORMAL = "normal"
    ATTENTION = "attention"
    INCONCLUSIVE = "inconclusive"


class ExecutionStepDefinition(ContractModel):
    step_id: str = Field(min_length=1, max_length=64)
    order: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=40)
    objective: str = Field(min_length=1, max_length=160)


class ExecutionPlan(ContractModel):
    plan_version: Literal["due-diligence-execution-v1"] = EXECUTION_PLAN_VERSION
    mode: OrchestrationMode
    steps: tuple[ExecutionStepDefinition, ...] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def require_fixed_order(self) -> ExecutionPlan:
        ids = tuple(item.step_id for item in self.steps)
        orders = tuple(item.order for item in self.steps)
        if ids != EXECUTION_STEP_IDS or orders != tuple(range(1, 8)):
            raise ValueError("execution plan must contain the fixed seven steps in order")
        return self


class ExecutionKeyFact(ContractModel):
    text: str = Field(min_length=1, max_length=240)
    evidence_ids: tuple[BoundedId, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def unique_evidence(self) -> ExecutionKeyFact:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("key-fact evidence ids must be unique")
        return self


class ExecutionSourceTag(ContractModel):
    label: str = Field(min_length=1, max_length=80)
    evidence_id: str = Field(min_length=1, max_length=160)
    source_type: Literal["tianyancha", "public_web", "user_input", "derived", "mock"]


class ExecutionStepSnapshot(ContractModel):
    step_id: str = Field(min_length=1, max_length=64)
    order: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=40)
    objective: str = Field(min_length=1, max_length=160)
    state: ExecutionStepState
    outcome: ExecutionStepOutcome | None = None
    conclusion: str | None = Field(default=None, min_length=1, max_length=240)
    progress_message: str | None = Field(default=None, min_length=1, max_length=160)
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    key_facts: tuple[ExecutionKeyFact, ...] = Field(default=(), max_length=3)
    gaps: tuple[str, ...] = Field(default=(), max_length=3)
    source_tags: tuple[ExecutionSourceTag, ...] = Field(default=(), max_length=8)
    executor_ids: tuple[BoundedId, ...] = Field(default=(), max_length=8)
    duration_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_snapshot(self) -> ExecutionStepSnapshot:
        try:
            index = EXECUTION_STEP_IDS.index(self.step_id)
        except ValueError as error:
            raise ValueError("step snapshot references an unknown fixed step") from error
        if self.order != index + 1:
            raise ValueError("step order does not match the fixed plan")
        if len(self.executor_ids) != len(set(self.executor_ids)):
            raise ValueError("step executor ids must be unique")
        if len({fact.text for fact in self.key_facts}) != len(self.key_facts):
            raise ValueError("step key facts must be unique")
        if len(self.gaps) != len(set(self.gaps)) or any(
            not item.strip() or len(item) > 240 for item in self.gaps
        ):
            raise ValueError("step gaps must be unique non-empty strings up to 240 characters")
        tag_ids = [item.evidence_id for item in self.source_tags]
        if len(tag_ids) != len(set(tag_ids)):
            raise ValueError("step source tags must have unique evidence ids")
        referenced = {evidence_id for fact in self.key_facts for evidence_id in fact.evidence_ids}
        unknown = referenced - set(tag_ids)
        if unknown:
            raise ValueError(f"step facts reference unknown source tags: {sorted(unknown)}")
        if self.state is ExecutionStepState.COMPLETED:
            if self.outcome is None or self.conclusion is None or self.progress_percent != 100:
                raise ValueError("completed step requires outcome, conclusion and 100% progress")
        elif self.state is ExecutionStepState.FAILED:
            if self.outcome is not None or self.conclusion is None:
                raise ValueError("failed step requires a conclusion and no business outcome")
        elif self.outcome is not None:
            raise ValueError("business outcome is available only for completed steps")
        return self


__all__ = [
    "EXECUTION_PLAN_VERSION",
    "EXECUTION_STEP_IDS",
    "ExecutionKeyFact",
    "ExecutionPlan",
    "ExecutionSourceTag",
    "ExecutionStepDefinition",
    "ExecutionStepOutcome",
    "ExecutionStepSnapshot",
    "ExecutionStepState",
]
