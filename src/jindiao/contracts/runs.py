"""Contracts for observable due-diligence runs.

The models in this module are deliberately summaries.  They are safe to send to a
browser and never contain prompts, raw provider responses, or arbitrary paths.
"""

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from .base import ContractModel
from .business import BusinessContext
from .entities import EnterpriseInput
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


class RunExecutionRequest(DueDiligenceRequest):
    """Internal execution input, also preserving all historical v1 fields."""

    mode: OrchestrationMode = OrchestrationMode.MULTI
    execution_profile: ExecutionProfile = ExecutionProfile.ATTACHED
    session_id: str | None = Field(default=None, min_length=1, max_length=200)


class RunCreateRequest(ContractModel):
    """Flat prototype form for v2 and AgentArts; amount is in CNY ten-thousands."""

    customerName: str | None = Field(default=None, description="客户名称")
    uscc: str | None = Field(default=None, description="统一社会信用代码")
    product: str | None = Field(default=None, description="业务品种")
    amount: float | None = Field(
        default=None,
        gt=0,
        strict=True,
        allow_inf_nan=False,
        description="拟申请金额 (万元), 有限正数; 兼容十进制数字字符串",
    )
    term: int | None = Field(
        default=None, gt=0, strict=True, description="期限 (月), 正整数; 兼容整数字符串"
    )
    manager: str | None = Field(default=None, description="主办客户经理")
    branch: str | None = Field(default=None, description="所属支行")
    mode: OrchestrationMode = OrchestrationMode.MULTI
    report_as_of: date | None = None
    execution_profile: ExecutionProfile = ExecutionProfile.ATTACHED
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    scenario_id: str | None = None

    @field_validator("customerName", "uscc", "product", "manager", "branch", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator("uscc")
    @classmethod
    def normalize_uscc(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("amount", mode="before", json_schema_input_type=float | str | None)
    @classmethod
    def normalize_amount_text(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
                parsed = float(text)
                # Keep overflow text intact so HTTP validation errors remain JSON-safe.
                if math.isfinite(parsed):
                    return parsed
        return value

    @field_validator("term", mode="before", json_schema_input_type=int | str | None)
    @classmethod
    def normalize_term_text(cls, value: object) -> object:
        if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
            return int(value.strip())
        return value

    @field_validator("amount")
    @classmethod
    def require_convertible_amount(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value * 10000):
            raise ValueError("amount exceeds the supported CNY range")
        return value

    @model_validator(mode="after")
    def require_identifier(self) -> RunCreateRequest:
        if self.customerName is None and self.uscc is None:
            raise ValueError("customerName or uscc is required")
        return self

    def to_execution_request(self) -> RunExecutionRequest:
        """Translate once at the boundary; existing report amounts remain CNY yuan."""
        amount_yuan = float(Decimal(str(self.amount)) * 10000) if self.amount is not None else None
        return RunExecutionRequest(
            enterprise=EnterpriseInput(
                company_name=self.customerName, unified_social_credit_code=self.uscc
            ),
            business_context=BusinessContext(
                business_product=self.product,
                application_amount=amount_yuan,
                application_term_months=self.term,
                customer_manager=self.manager,
                reporting_org=self.branch,
            ),
            mode=self.mode,
            report_as_of=self.report_as_of,
            execution_profile=self.execution_profile,
            session_id=self.session_id,
            scenario_id=self.scenario_id,
        )


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
