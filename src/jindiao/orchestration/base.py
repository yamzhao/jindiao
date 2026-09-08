"""Shared orchestration contracts and resource-budget enforcement."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Set
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import Field, JsonValue, field_validator, model_validator

from jindiao.application.context import RunContext, RunPolicy
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.errors import ErrorRecord
from jindiao.contracts.evidence import CoverageItem, CoverageSummary, Evidence
from jindiao.contracts.execution import ExecutionCost
from jindiao.contracts.investigation import Finding, RepairTask, ReviewIssue
from jindiao.contracts.results import AgentTrace, CollaborationSummary, OrchestrationMode
from jindiao.security import redact_json


class RunBudget(ContractModel):
    max_tool_calls: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    max_repair_rounds: int = Field(ge=0)
    max_llm_requests: int = Field(default=64, ge=1)
    max_input_tokens: int = Field(default=300_000, ge=1)
    max_output_tokens: int = Field(default=100_000, ge=1)
    max_total_tokens: int = Field(default=400_000, ge=1)
    enforce_token_budget: bool = True
    max_schema_retries: int = Field(default=4, ge=0)
    max_snapshot_reads: int = Field(default=240, ge=1)

    @model_validator(mode="after")
    def validate_token_limits(self) -> RunBudget:
        if self.max_total_tokens > self.max_input_tokens + self.max_output_tokens:
            raise ValueError("max_total_tokens cannot exceed combined input/output token limits")
        return self

    @classmethod
    def from_policy(cls, policy: RunPolicy) -> RunBudget:
        return cls(
            max_tool_calls=policy.max_tool_calls,
            max_concurrency=policy.max_concurrency,
            timeout_seconds=policy.request_timeout_seconds,
            max_repair_rounds=policy.max_repair_rounds,
            max_llm_requests=policy.max_llm_requests,
            max_input_tokens=policy.max_input_tokens,
            enforce_token_budget=policy.enforce_token_budget,
            max_output_tokens=policy.max_output_tokens,
            max_total_tokens=policy.max_total_tokens,
            max_schema_retries=policy.max_schema_retries,
            max_snapshot_reads=policy.max_snapshot_reads,
        )


class CancellationToken(Protocol):
    """Minimal cancellation contract shared by every execution boundary."""

    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


def check_cancellation(token: object | None) -> None:
    """Raise ``CancelledError`` when a run cancellation has been requested."""

    if token is None:
        return
    raise_if_cancelled = getattr(token, "raise_if_cancelled", None)
    if callable(raise_if_cancelled):
        raise_if_cancelled()
    elif bool(getattr(token, "cancelled", False)):
        raise asyncio.CancelledError()


async def resolve_subject_with_cancellation(
    toolset: object,
    context: RunContext,
    token: CancellationToken | None,
) -> ResolvedSubject:
    method = cast(Any, toolset).resolve_subject
    if token is None:
        return cast(ResolvedSubject, await method(context))
    return cast(ResolvedSubject, await method(context, cancellation_token=token))


async def investigate_with_cancellation(
    toolset: object,
    context: RunContext,
    subject: ResolvedSubject,
    domain: str,
    token: CancellationToken | None,
) -> DomainInvestigation:
    method = cast(Any, toolset).investigate
    if token is None:
        return cast(DomainInvestigation, await method(context, subject, domain))
    return cast(
        DomainInvestigation,
        await method(context, subject, domain, cancellation_token=token),
    )


def require_deterministic_harness(context: RunContext, *, component: str) -> None:
    """Fail closed when a legacy deterministic component sees a formal Run."""

    if (
        context.policy.formal_agent_run
        or context.policy.agent_runtime_mode != "deterministic_harness"
    ):
        raise AgentExecutionError(
            f"{component} is deterministic harness only",
            details={
                "component": component,
                "agent_runtime_mode": context.policy.agent_runtime_mode,
                "formal_agent_run": context.policy.formal_agent_run,
            },
        )


class BudgetUsageSnapshot(ContractModel):
    llm_requests: int = Field(ge=0)
    successful_llm_requests: int = Field(ge=0)
    provider_usage_requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    snapshot_reads: int = Field(ge=0)
    schema_retries: int = Field(ge=0)
    repair_rounds: int = Field(ge=0)
    active_operations: int = Field(ge=0)
    peak_concurrency: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)
    deadline_remaining_ms: int = Field(ge=0)
    exhausted_reason: str | None = None
    reserved_input_tokens: int = Field(default=0, ge=0)
    reserved_output_tokens: int = Field(default=0, ge=0)
    unknown_usage_requests: int = Field(default=0, ge=0)
    unreported_input_tokens: int = Field(default=0, ge=0)
    unreported_output_tokens: int = Field(default=0, ge=0)


@dataclass(frozen=True, eq=False)
class LLMReservation:
    """Opaque, ledger-owned claim; estimates are never reported as actual usage."""

    input_tokens: int
    output_tokens: int


class BudgetLedger:
    """Concurrency-safe shared counter and deadline for either strategy."""

    def __init__(
        self,
        budget: RunBudget,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._deadline = self._started_at + budget.timeout_seconds
        self._tool_calls = 0
        self._snapshot_reads = 0
        self._llm_requests = 0
        self._successful_llm_requests = 0
        self._provider_usage_requests = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._legacy_pending_requests = 0
        self._reservations: set[LLMReservation] = set()
        self._reserved_input_tokens = 0
        self._reserved_output_tokens = 0
        self._unknown_usage_requests = 0
        self._unreported_input_tokens = 0
        self._unreported_output_tokens = 0
        self._schema_retries = 0
        self._repair_rounds = 0
        self._active_operations = 0
        self._peak_concurrency = 0
        self._exhausted_reason: str | None = None
        self._lock = asyncio.Lock()

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    async def claim_tool_call(self, operation: str) -> None:
        async with self._lock:
            self._ensure_available(operation)
            if self._tool_calls >= self.budget.max_tool_calls:
                self._exhaust(
                    "orchestration tool-call budget exhausted",
                    operation=operation,
                    limit=self.budget.max_tool_calls,
                )
            self._tool_calls += 1

    async def claim_snapshot_read(self, operation: str) -> None:
        async with self._lock:
            self._ensure_available(operation)
            if self._snapshot_reads >= self.budget.max_snapshot_reads:
                self._exhaust(
                    "orchestration snapshot-read budget exhausted",
                    operation=operation,
                    limit=self.budget.max_snapshot_reads,
                )
            if self._tool_calls >= self.budget.max_tool_calls:
                self._exhaust(
                    "orchestration tool-call budget exhausted",
                    operation=operation,
                    limit=self.budget.max_tool_calls,
                )
            self._snapshot_reads += 1
            self._tool_calls += 1

    async def claim_llm_request(self, operation: str) -> None:
        async with self._lock:
            self._ensure_available(operation)
            if self._llm_requests >= self.budget.max_llm_requests:
                self._exhaust(
                    "orchestration LLM-request budget exhausted",
                    operation=operation,
                    limit=self.budget.max_llm_requests,
                )
            self._llm_requests += 1
            self._legacy_pending_requests += 1

    async def reserve_llm_request(
        self, operation: str, *, input_tokens: int, output_tokens: int
    ) -> LLMReservation:
        """Atomically reserve input plus a bounded output allowance before dispatch."""

        if input_tokens < 0 or output_tokens < 1:
            raise ValueError("LLM reservation needs nonnegative input and positive output")
        async with self._lock:
            self._ensure_available(operation)
            if self._llm_requests >= self.budget.max_llm_requests:
                self._exhaust(
                    "orchestration LLM-request budget exhausted",
                    operation=operation,
                    limit=self.budget.max_llm_requests,
                )
            committed_input = (
                self._input_tokens + self._reserved_input_tokens + self._unreported_input_tokens
            )
            committed_output = (
                self._output_tokens + self._reserved_output_tokens + self._unreported_output_tokens
            )
            bounded_output = output_tokens
            if self.budget.enforce_token_budget:
                bounded_output = min(
                    output_tokens,
                    self.budget.max_output_tokens - committed_output,
                    self.budget.max_total_tokens
                    - committed_input
                    - committed_output
                    - input_tokens,
                )
            if self.budget.enforce_token_budget and (
                committed_input + input_tokens > self.budget.max_input_tokens or bounded_output < 1
            ):
                self._exhaust(
                    "orchestration token budget exhausted before model request",
                    operation=operation,
                    estimated_input_tokens=input_tokens,
                    requested_output_tokens=output_tokens,
                )
            reservation = LLMReservation(input_tokens=input_tokens, output_tokens=bounded_output)
            self._reservations.add(reservation)
            self._reserved_input_tokens += reservation.input_tokens
            self._reserved_output_tokens += reservation.output_tokens
            self._llm_requests += 1
            return reservation

    async def release_llm_request(self, reservation: LLMReservation) -> None:
        """Release a provably undispatched request; repeated cleanup is harmless."""

        async with self._lock:
            if self._remove_reservation(reservation):
                self._llm_requests -= 1

    async def complete_llm_request(
        self,
        reservation: LLMReservation,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        provider_usage: bool,
        succeeded: bool = True,
    ) -> None:
        """Settle dispatched work even after cancellation, exhaustion or its deadline.

        Missing provider usage remains an explicitly unknown budget hold, not zero
        cost. Observed partial stream usage is a lower bound on interrupted work;
        its unobserved remainder stays held until the Run ends.
        """

        if input_tokens is not None and input_tokens < 0:
            raise ValueError("LLM token usage cannot be negative")
        if output_tokens is not None and output_tokens < 0:
            raise ValueError("LLM token usage cannot be negative")
        async with self._lock:
            if not self._remove_reservation(reservation):
                return
            known = provider_usage and input_tokens is not None and output_tokens is not None
            observed_input = input_tokens or 0
            observed_output = output_tokens or 0
            self._successful_llm_requests += int(succeeded or known)
            self._provider_usage_requests += int(known)
            self._input_tokens += observed_input
            self._output_tokens += observed_output
            if not known or not succeeded:
                self._unknown_usage_requests += 1
                self._unreported_input_tokens += max(0, reservation.input_tokens - observed_input)
                self._unreported_output_tokens += max(
                    0, reservation.output_tokens - observed_output
                )
            self._check_recorded_usage("complete_llm_request")

    def _remove_reservation(self, reservation: LLMReservation) -> bool:
        if reservation not in self._reservations:
            return False
        self._reservations.remove(reservation)
        self._reserved_input_tokens -= reservation.input_tokens
        self._reserved_output_tokens -= reservation.output_tokens
        return True

    async def record_llm_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        provider_usage: bool,
    ) -> None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("LLM token usage cannot be negative")
        async with self._lock:
            if self._legacy_pending_requests < 1:
                raise AgentExecutionError(
                    "LLM usage has no claimed request",
                    details={"operation": "record_llm_usage", **self.error_details()},
                )
            self._legacy_pending_requests -= 1
            self._successful_llm_requests += 1
            if provider_usage:
                self._provider_usage_requests += 1
            else:
                self._unknown_usage_requests += 1
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            self._check_recorded_usage("record_llm_usage")

    def _check_recorded_usage(self, operation: str) -> None:
        total_tokens = self._input_tokens + self._output_tokens
        if self.budget.enforce_token_budget and (
            self._input_tokens > self.budget.max_input_tokens
            or self._output_tokens > self.budget.max_output_tokens
            or total_tokens > self.budget.max_total_tokens
        ):
            self._exhaust(
                "orchestration token budget exhausted",
                operation=operation,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                total_tokens=total_tokens,
            )
        self._ensure_deadline(operation)

    async def claim_schema_retry(self, operation: str) -> None:
        async with self._lock:
            self._ensure_available(operation)
            if self._schema_retries >= self.budget.max_schema_retries:
                self._exhaust(
                    "orchestration schema-retry budget exhausted",
                    operation=operation,
                    limit=self.budget.max_schema_retries,
                )
            self._schema_retries += 1

    async def claim_repair_round(self, operation: str) -> None:
        async with self._lock:
            self._ensure_available(operation)
            if self._repair_rounds >= self.budget.max_repair_rounds:
                self._exhaust(
                    "orchestration repair-round budget exhausted",
                    operation=operation,
                    limit=self.budget.max_repair_rounds,
                )
            self._repair_rounds += 1

    async def check_deadline(self, operation: str) -> None:
        async with self._lock:
            self._ensure_available(operation)

    @asynccontextmanager
    async def operation_slot(self, operation: str) -> AsyncIterator[None]:
        async with self._lock:
            self._ensure_available(operation)
            if self._active_operations >= self.budget.max_concurrency:
                raise AgentExecutionError(
                    "orchestration concurrency budget exhausted",
                    details={
                        "operation": operation,
                        "limit": self.budget.max_concurrency,
                        **self.error_details(),
                    },
                )
            self._active_operations += 1
            self._peak_concurrency = max(
                self._peak_concurrency,
                self._active_operations,
            )
        try:
            yield
        finally:
            async with self._lock:
                self._active_operations -= 1

    def snapshot(self) -> BudgetUsageSnapshot:
        now = self._monotonic()
        return BudgetUsageSnapshot(
            llm_requests=self._llm_requests,
            successful_llm_requests=self._successful_llm_requests,
            provider_usage_requests=self._provider_usage_requests,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._input_tokens + self._output_tokens,
            tool_calls=self._tool_calls,
            snapshot_reads=self._snapshot_reads,
            schema_retries=self._schema_retries,
            repair_rounds=self._repair_rounds,
            active_operations=self._active_operations,
            peak_concurrency=self._peak_concurrency,
            wall_time_ms=max(0, round((now - self._started_at) * 1000)),
            deadline_remaining_ms=max(0, round((self._deadline - now) * 1000)),
            exhausted_reason=self._exhausted_reason,
            reserved_input_tokens=self._reserved_input_tokens,
            reserved_output_tokens=self._reserved_output_tokens,
            unknown_usage_requests=self._unknown_usage_requests,
            unreported_input_tokens=self._unreported_input_tokens,
            unreported_output_tokens=self._unreported_output_tokens,
        )

    def to_execution_cost(self) -> ExecutionCost:
        usage = self.snapshot()
        return ExecutionCost(
            llm_requests=usage.llm_requests,
            successful_llm_requests=usage.successful_llm_requests,
            provider_usage_requests=usage.provider_usage_requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            tool_calls=usage.tool_calls,
            mcp_calls=0,
            schema_retries=usage.schema_retries,
            repair_rounds=usage.repair_rounds,
            wall_time_ms=usage.wall_time_ms,
        )

    def _ensure_available(self, operation: str) -> None:
        self._ensure_deadline(operation)
        if self._exhausted_reason is not None:
            raise AgentExecutionError(
                self._exhausted_reason,
                details={"operation": operation, **self.error_details()},
            )

    def _ensure_deadline(self, operation: str) -> None:
        if self._monotonic() > self._deadline:
            self._exhausted_reason = "orchestration deadline exceeded"
            raise AgentExecutionError(
                self._exhausted_reason,
                details={"operation": operation, **self.error_details()},
            )

    def _exhaust(self, message: str, **details: object) -> None:
        self._exhausted_reason = message
        raise AgentExecutionError(message, details={**details, **self.error_details()})

    def error_details(self) -> dict[str, object]:
        """Safe accounting-only metadata for durable error/termination paths."""

        return {
            "execution_cost": self.to_execution_cost().model_dump(mode="json"),
            "token_budget_enforced": self.budget.enforce_token_budget,
            "budget_usage": self.snapshot().model_dump(mode="json"),
            "provider_usage_complete": (
                self._provider_usage_requests == self._llm_requests
                and self._unknown_usage_requests == 0
                and not self._reservations
            ),
        }


class DomainInvestigation(ContractModel):
    task_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    coverage_items: tuple[CoverageItem, ...] = Field(min_length=1)
    section_data: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    errors: tuple[ErrorRecord, ...] = ()


class TeamRuntimeEvent(ContractModel):
    event_type: str = Field(min_length=1)
    member_name: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("payload", mode="before")
    @classmethod
    def redact_private_payload(cls, value: object) -> dict[str, JsonValue]:
        safe = redact_json(value)
        if not isinstance(safe, dict):
            raise ValueError("runtime event payload must be an object")
        return safe


RuntimeEventSink = Callable[[TeamRuntimeEvent], Awaitable[None]]


async def emit_runtime_event(
    sink: RuntimeEventSink | None,
    event_type: str,
    *,
    member_name: str | None = None,
    payload: dict[str, JsonValue] | None = None,
) -> None:
    if sink is not None:
        await sink(
            TeamRuntimeEvent(
                event_type=event_type,
                member_name=member_name,
                payload=payload or {},
            )
        )


class OrchestrationOutcome(ContractModel):
    subject: ResolvedSubject
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    coverage: CoverageSummary
    review_issues: tuple[ReviewIssue, ...]
    review_completed: bool
    section_data: dict[str, dict[str, JsonValue]]
    agent_trace: tuple[AgentTrace, ...]
    collaboration: CollaborationSummary
    errors: tuple[ErrorRecord, ...]
    tool_calls: int = Field(ge=0)
    repair_tasks: tuple[RepairTask, ...] = ()
    runtime_events: tuple[TeamRuntimeEvent, ...] = ()


class InvestigationToolset(Protocol):
    async def resolve_subject(self, context: RunContext) -> ResolvedSubject: ...

    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation: ...


@runtime_checkable
class CapabilityAwareInvestigationToolset(Protocol):
    @property
    def mock_domains(self) -> Set[str]: ...

    async def domain_capabilities(
        self,
        subject: ResolvedSubject,
    ) -> Mapping[str, tuple[str, ...]]: ...


@runtime_checkable
class AsyncClosableToolset(Protocol):
    async def aclose(self) -> None: ...


class OrchestrationStrategy(Protocol):
    @property
    def mode(self) -> OrchestrationMode: ...

    async def execute(
        self,
        context: RunContext,
        *,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> OrchestrationOutcome: ...


def merge_section_data(
    artifacts: tuple[DomainInvestigation, ...],
) -> dict[str, dict[str, JsonValue]]:
    merged: dict[str, dict[str, JsonValue]] = {}
    for artifact in artifacts:
        for section_id, values in artifact.section_data.items():
            merged.setdefault(section_id, {}).update(values)
    return merged


__all__ = [
    "AsyncClosableToolset",
    "BudgetLedger",
    "BudgetUsageSnapshot",
    "CancellationToken",
    "CapabilityAwareInvestigationToolset",
    "DomainInvestigation",
    "InvestigationToolset",
    "OrchestrationOutcome",
    "OrchestrationStrategy",
    "RunBudget",
    "RuntimeEventSink",
    "TeamRuntimeEvent",
    "check_cancellation",
    "emit_runtime_event",
    "investigate_with_cancellation",
    "merge_section_data",
    "require_deterministic_harness",
    "resolve_subject_with_cancellation",
]
