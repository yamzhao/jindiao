"""Execution accounting, termination, and fair-comparison contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel


class ExecutionCost(ContractModel):
    """Observed resource use for exactly one execution layer."""

    llm_requests: int = Field(ge=0)
    successful_llm_requests: int = Field(ge=0)
    provider_usage_requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    mcp_calls: int = Field(ge=0)
    schema_retries: int = Field(ge=0)
    repair_rounds: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)

    @classmethod
    def zero(cls) -> ExecutionCost:
        return cls(
            llm_requests=0,
            successful_llm_requests=0,
            provider_usage_requests=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            tool_calls=0,
            mcp_calls=0,
            schema_retries=0,
            repair_rounds=0,
            wall_time_ms=0,
        )

    @classmethod
    def combine(cls, *costs: ExecutionCost) -> ExecutionCost:
        fields = (
            "llm_requests",
            "successful_llm_requests",
            "provider_usage_requests",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "tool_calls",
            "mcp_calls",
            "schema_retries",
            "repair_rounds",
            "wall_time_ms",
        )
        return cls(**{name: sum(getattr(cost, name) for cost in costs) for name in fields})

    @model_validator(mode="after")
    def reconcile_usage(self) -> ExecutionCost:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        if self.successful_llm_requests > self.llm_requests:
            raise ValueError("successful LLM requests cannot exceed total LLM requests")
        if self.provider_usage_requests > self.successful_llm_requests:
            raise ValueError("provider usage requests cannot exceed successful LLM requests")
        return self


class LayeredExecutionCost(ContractModel):
    """Keep shared acquisition outside either investigation arm."""

    shared_acquisition_cost: ExecutionCost
    investigation_cost: ExecutionCost

    @property
    def combined(self) -> ExecutionCost:
        return ExecutionCost.combine(
            self.shared_acquisition_cost,
            self.investigation_cost,
        )


class PairedExecutionCost(ContractModel):
    """Count shared acquisition once across two independently metered arms."""

    shared_acquisition_cost: ExecutionCost
    single_investigation_cost: ExecutionCost
    multi_investigation_cost: ExecutionCost

    @property
    def combined(self) -> ExecutionCost:
        return ExecutionCost.combine(
            self.shared_acquisition_cost,
            self.single_investigation_cost,
            self.multi_investigation_cost,
        )


class FormalComparisonEligibility(ContractModel):
    """Auditable hard-gate result for publishing a formal paired conclusion."""

    eligible: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reconcile_eligibility(self) -> FormalComparisonEligibility:
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("formal comparison eligibility reasons must be unique")
        if self.eligible == bool(self.reasons):
            raise ValueError("formal comparison is eligible exactly when reasons are empty")
        return self


class RunTerminationReason(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SCHEMA_RETRY_EXHAUSTED = "schema_retry_exhausted"
    CONTEXT_INVALIDATED = "context_invalidated"
    INTERRUPTED = "interrupted"


class RunTermination(ContractModel):
    reason: RunTerminationReason
    completed_task_ids: tuple[str, ...] = ()
    incomplete_task_ids: tuple[str, ...] = ()
    detail: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_task_sets(self) -> RunTermination:
        if len(self.completed_task_ids) != len(set(self.completed_task_ids)):
            raise ValueError("completed task ids must be unique")
        if len(self.incomplete_task_ids) != len(set(self.incomplete_task_ids)):
            raise ValueError("incomplete task ids must be unique")
        overlap = set(self.completed_task_ids) & set(self.incomplete_task_ids)
        if overlap:
            raise ValueError(f"tasks cannot be both completed and incomplete: {sorted(overlap)}")
        if self.reason is RunTerminationReason.COMPLETED and self.incomplete_task_ids:
            raise ValueError("completed run cannot contain incomplete tasks")
        return self


class InvestigationBudgetFingerprint(ContractModel):
    enforce_token_budget: bool = True
    max_llm_requests: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    max_total_tokens: int = Field(ge=1)
    max_wall_time_ms: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    max_schema_retries: int = Field(ge=0)
    max_repair_rounds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_token_limits(self) -> InvestigationBudgetFingerprint:
        if self.max_total_tokens > self.max_input_tokens + self.max_output_tokens:
            raise ValueError("max_total_tokens cannot exceed combined input/output token limits")
        return self


class ComparisonFingerprint(ContractModel):
    """Mode-independent inputs that must match before a formal paired run."""

    schema_version: Literal[1]
    code_version: str = Field(min_length=1)
    contract_schema_version: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_as_of: date
    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_parameters: dict[str, JsonValue]
    random_seed: int | None = None
    common_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    check_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    investigation_budget: InvestigationBudgetFingerprint
    rule_version: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    reporting_policy_sha256: str | None = None
    report_renderer_version: str | None = None
    gap_mapping_version: str | None = None

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()


__all__ = [
    "ComparisonFingerprint",
    "ExecutionCost",
    "FormalComparisonEligibility",
    "InvestigationBudgetFingerprint",
    "LayeredExecutionCost",
    "PairedExecutionCost",
    "RunTermination",
    "RunTerminationReason",
]
