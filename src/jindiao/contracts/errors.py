"""Stable error records returned by the application and API."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCategory(StrEnum):
    REQUEST = "request"
    ENTITY = "entity"
    SOURCE = "source"
    AGENT = "agent"
    REVIEW = "review"
    RULE = "rule"
    REPORT = "report"
    SCENARIO = "scenario"
    EVALUATION = "evaluation"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    REQUEST_INVALID = "request_invalid"
    ENTITY_NOT_FOUND = "entity_not_found"
    ENTITY_AMBIGUOUS = "entity_ambiguous"
    SOURCE_UNAVAILABLE = "source_unavailable"
    AGENT_EXECUTION_FAILED = "agent_execution_failed"
    AGENT_EXECUTION_TIMEOUT = "agent_execution_timeout"
    EVIDENCE_REVIEW_FAILED = "evidence_review_failed"
    RISK_RULE_FAILED = "risk_rule_failed"
    REPORT_GENERATION_FAILED = "report_generation_failed"
    SCENARIO_INTEGRITY = "scenario_integrity"
    EVALUATION_INTEGRITY = "evaluation_integrity"
    INTERNAL_ERROR = "internal_error"
    RUN_NOT_FOUND = "run_not_found"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    DETACHED_UNAVAILABLE = "detached_unavailable"
    RUN_CANCELLED = "run_cancelled"


class ErrorRecord(BaseModel):
    category: ErrorCategory
    code: ErrorCode
    message: str
    recoverable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


__all__ = ["ErrorCategory", "ErrorCode", "ErrorRecord"]
