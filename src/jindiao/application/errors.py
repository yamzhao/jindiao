"""Application exceptions and their stable public mappings."""

from __future__ import annotations

from typing import Any, ClassVar

from jindiao.contracts.errors import ErrorCategory, ErrorCode, ErrorRecord


class JindiaoError(Exception):
    category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    recoverable: ClassVar[bool] = False
    http_status: ClassVar[int] = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class RequestInvalidError(JindiaoError):
    category = ErrorCategory.REQUEST
    code = ErrorCode.REQUEST_INVALID
    http_status = 422


class EntityNotFoundError(JindiaoError):
    category = ErrorCategory.ENTITY
    code = ErrorCode.ENTITY_NOT_FOUND
    http_status = 404


class EntityAmbiguousError(JindiaoError):
    category = ErrorCategory.ENTITY
    code = ErrorCode.ENTITY_AMBIGUOUS
    http_status = 409


class SourceUnavailableError(JindiaoError):
    category = ErrorCategory.SOURCE
    code = ErrorCode.SOURCE_UNAVAILABLE
    recoverable = True
    http_status = 503


class AgentExecutionError(JindiaoError):
    category = ErrorCategory.AGENT
    code = ErrorCode.AGENT_EXECUTION_FAILED
    recoverable = True


class EvidenceReviewError(JindiaoError):
    category = ErrorCategory.REVIEW
    code = ErrorCode.EVIDENCE_REVIEW_FAILED
    http_status = 422


class RiskRuleError(JindiaoError):
    category = ErrorCategory.RULE
    code = ErrorCode.RISK_RULE_FAILED


class ReportGenerationError(JindiaoError):
    category = ErrorCategory.REPORT
    code = ErrorCode.REPORT_GENERATION_FAILED


class ScenarioIntegrityError(JindiaoError):
    category = ErrorCategory.SCENARIO
    code = ErrorCode.SCENARIO_INTEGRITY
    http_status = 422


class EvaluationIntegrityError(JindiaoError):
    category = ErrorCategory.EVALUATION
    code = ErrorCode.EVALUATION_INTEGRITY
    http_status = 422


class RunNotFoundApplicationError(JindiaoError):
    category = ErrorCategory.REQUEST
    code = ErrorCode.RUN_NOT_FOUND
    http_status = 404


class IdempotencyConflictError(JindiaoError):
    category = ErrorCategory.REQUEST
    code = ErrorCode.IDEMPOTENCY_CONFLICT
    http_status = 409


class DetachedUnavailableError(JindiaoError):
    category = ErrorCategory.REQUEST
    code = ErrorCode.DETACHED_UNAVAILABLE
    http_status = 409


def error_to_record(error: Exception) -> ErrorRecord:
    """Convert an exception to a safe, version-stable error record."""

    if not isinstance(error, JindiaoError):
        return ErrorRecord(
            category=ErrorCategory.INTERNAL,
            code=ErrorCode.INTERNAL_ERROR,
            message="Internal application error",
        )
    return ErrorRecord(
        category=error.category,
        code=error.code,
        message=error.message,
        recoverable=error.recoverable,
        details=error.details,
    )


def http_status_for_error(error: Exception) -> int:
    """Return the HTTP status corresponding to an application exception."""

    return error.http_status if isinstance(error, JindiaoError) else 500


__all__ = [
    "AgentExecutionError",
    "DetachedUnavailableError",
    "EntityAmbiguousError",
    "EntityNotFoundError",
    "EvaluationIntegrityError",
    "EvidenceReviewError",
    "IdempotencyConflictError",
    "JindiaoError",
    "ReportGenerationError",
    "RequestInvalidError",
    "RiskRuleError",
    "RunNotFoundApplicationError",
    "ScenarioIntegrityError",
    "SourceUnavailableError",
    "error_to_record",
    "http_status_for_error",
]
