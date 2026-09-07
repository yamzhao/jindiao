"""Contracts for observable due-diligence runs.

The models in this module are deliberately summaries.  They are safe to send to a
browser and never contain prompts, raw provider responses, or arbitrary paths.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .base import ContractModel
from .errors import ErrorRecord
from .execution import RunTermination
from .report_policy import ReportingPolicyBinding
from .results import (
    AgentStatus,
    DueDiligenceRequest,
    OrchestrationMode,
    RunStatus,
    SnapshotSummary,
)


class RunStage(StrEnum):
    ACCEPTED = "accepted"
    ACQUISITION = "acquisition"
    SNAPSHOT = "snapshot"
    INVESTIGATION = "investigation"
    ADJUDICATION = "adjudication"
    REPORTING = "reporting"
    TERMINAL = "terminal"


class ExecutionProfile(StrEnum):
    ATTACHED = "attached"
    DETACHED = "detached"


class ExecutionProfileConfig(ContractModel):
    """Runtime lifecycle capabilities advertised by a deployment."""

    profile: ExecutionProfile = ExecutionProfile.ATTACHED
    allow_background_tasks: bool = False
    shared_storage: bool = False
    session_required: bool = False
    max_event_queue: int = Field(default=256, ge=1)

    @model_validator(mode="after")
    def validate_detached_capabilities(self) -> ExecutionProfileConfig:
        if self.profile is ExecutionProfile.DETACHED and not (
            self.allow_background_tasks and self.shared_storage
        ):
            raise ValueError("detached profile requires allow_background_tasks and shared_storage")
        return self


class RunLink(ContractModel):
    href: str = Field(min_length=1, pattern=r"^/")
    method: Literal["GET", "POST"] = "GET"


class RunProgress(ContractModel):
    completed: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    percentage: float = Field(default=0, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def calculate_percentage(cls, value: object) -> object:
        if isinstance(value, dict):
            value = dict(value)
            completed = value.get("completed", 0)
            total = value.get("total", 0)
            if isinstance(completed, int) and isinstance(total, int):
                if total and completed > total:
                    raise ValueError("completed progress cannot exceed total")
                if value.get("percentage", 0) == 0 and total:
                    value["percentage"] = round((completed / total) * 100, 2)
        return value


class ActorView(ContractModel):
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    role: str | None = None


class AgentView(ContractModel):
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    status: AgentStatus = AgentStatus.PENDING
    phase: RunStage = RunStage.INVESTIGATION
    task_ids: tuple[str, ...] = ()
    completed_checks: int = Field(default=0, ge=0)
    total_checks: int = Field(default=0, ge=0)
    error_code: str | None = None


class CheckView(ContractModel):
    check_id: str = Field(min_length=1)
    status: str = "pending"
    agent_id: str | None = None
    submission_version: int | None = Field(default=None, ge=1)


class ReviewView(ContractModel):
    round: int = Field(default=0, ge=0)
    status: str = "pending"
    issue_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)


class BudgetView(ContractModel):
    used: dict[str, int] = Field(default_factory=dict)
    remaining: dict[str, int] = Field(default_factory=dict)
    peak_concurrency: int = Field(default=0, ge=0)


class RunCreateRequest(DueDiligenceRequest):
    """Request body for the versioned Run API."""

    mode: OrchestrationMode = OrchestrationMode.MULTI
    execution_profile: ExecutionProfile = ExecutionProfile.ATTACHED
    session_id: str | None = Field(default=None, min_length=1, max_length=200)


class RunResource(ContractModel):
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    owner_id: str = Field(min_length=1)
    mode: OrchestrationMode
    status: RunStatus = RunStatus.ACCEPTED
    stage: RunStage = RunStage.ACCEPTED
    profile: ExecutionProfile = ExecutionProfile.ATTACHED
    session_id: str | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    latest_sequence: int = Field(default=0, ge=0)
    progress: RunProgress = Field(default_factory=RunProgress)
    acquisition: RunProgress = Field(default_factory=RunProgress)
    investigation: RunProgress = Field(default_factory=RunProgress)
    reporting: RunProgress = Field(default_factory=RunProgress)
    snapshot: SnapshotSummary | None = None
    agents: tuple[AgentView, ...] = ()
    checks: tuple[CheckView, ...] = ()
    review: ReviewView = Field(default_factory=ReviewView)
    budget: BudgetView = Field(default_factory=BudgetView)
    termination: RunTermination | None = None
    error: ErrorRecord | None = None
    result_available: bool = False
    links: dict[str, RunLink] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    reporting_policy: ReportingPolicyBinding | None = None

    @model_validator(mode="after")
    def validate_terminal_fields(self) -> RunResource:
        terminal = self.status in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        if terminal and self.termination is None:
            # A projection can be assembled before a detailed termination reason is
            # known; callers may fill it in on the terminal transition.
            return self
        if self.status is RunStatus.COMPLETED and not self.result_available:
            raise ValueError("completed run must expose result_available")
        return self


__all__ = [
    "ActorView",
    "AgentView",
    "BudgetView",
    "CheckView",
    "ExecutionProfile",
    "ExecutionProfileConfig",
    "ReviewView",
    "RunCreateRequest",
    "RunLink",
    "RunProgress",
    "RunResource",
    "RunStage",
]
