"""Policy-gated DeepSearch evidence agent."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol

from pydantic import Field, JsonValue, model_validator

from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import SupplementTask, SupplementTaskReason
from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageGapReason,
    CoverageItem,
    Evidence,
    SourceStatus,
    SourceType,
)
from jindiao.deepsearch import (
    AnnualReportSocialSecurityOutcome,
    AnnualReportSocialSecurityProvider,
    EvidenceGap,
    MockFallbackService,
    SupplementOutcome,
)
from jindiao.tianyancha import SourceStateDecision

if TYPE_CHECKING:
    from jindiao.orchestration.base import DomainInvestigation


class DeepSearchTask(ContractModel):
    source: SourceStateDecision
    domain: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    query: str = Field(min_length=1)
    allow_degraded_mock: bool = False


class DeepSearchSupplementOutcome(ContractModel):
    task_id: str = Field(min_length=1)
    target_submodule_id: str = Field(min_length=1)
    source_status: SourceStatus
    evidence: tuple[Evidence, ...] = ()
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    unresolved: bool
    conflicts_with: tuple[str, ...] = ()

    @model_validator(mode="after")
    def preserve_source_semantics(self) -> DeepSearchSupplementOutcome:
        if self.source_status is SourceStatus.VERIFIED_RECORDS and not self.evidence:
            raise ValueError("verified DeepSearch records require Evidence")
        if self.source_status is SourceStatus.VERIFIED_EMPTY and self.evidence:
            raise ValueError("verified-empty DeepSearch outcome cannot contain Evidence")
        return self


class SupplementalResearchService(Protocol):
    async def research(
        self,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
    ) -> SupplementOutcome: ...


class DeepSearchCapabilityAgent(Protocol):
    @property
    def supplement_enabled(self) -> bool: ...

    @property
    def annual_report_enabled(self) -> bool: ...

    async def research_gap(
        self,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
    ) -> SupplementOutcome: ...

    async def fetch_annual_report_social_security(
        self,
        *,
        subject: ResolvedSubject,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome: ...

    async def aclose(self) -> None: ...


class DeepSearchEvidenceAgent:
    """Deterministic compatibility facade; formal runs use ``DeepSearchAgent``."""

    formal_agent_run = False
    agent_id = "deepsearch-agent"
    tool_whitelist = frozenset(
        {
            "deepsearch.local",
            "deepsearch.research_gap",
            "tianyancha.annual_report.fetch_latest",
        }
    )

    def __init__(
        self,
        *,
        service: MockFallbackService | None = None,
        supplement_service: SupplementalResearchService | None = None,
        annual_report_provider: AnnualReportSocialSecurityProvider | None = None,
    ) -> None:
        self._service = service
        self._supplement_service = supplement_service
        self._annual_report_provider = annual_report_provider

    @property
    def supplement_enabled(self) -> bool:
        return self._supplement_service is not None

    @property
    def annual_report_enabled(self) -> bool:
        return self._annual_report_provider is not None

    async def research_gap(
        self,
        *,
        subject: ResolvedSubject,
        gap: EvidenceGap,
        queried_at: datetime,
        report_as_of: date,
    ) -> SupplementOutcome:
        if self._supplement_service is None:
            raise AgentExecutionError(
                "DeepSearch supplemental research is not configured",
                details={"gap_id": gap.gap_id, "subject_id": subject.subject_id},
            )
        return await self._supplement_service.research(
            subject=subject,
            gap=gap,
            queried_at=queried_at,
            report_as_of=report_as_of,
        )

    async def fetch_annual_report_social_security(
        self,
        *,
        subject: ResolvedSubject,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome:
        if self._annual_report_provider is None:
            raise AgentExecutionError(
                "Tianyancha annual-report research is not configured",
                details={"subject_id": subject.subject_id},
            )
        return await self._annual_report_provider.fetch_latest(
            subject,
            queried_at=queried_at,
            report_as_of=report_as_of,
        )

    async def aclose(self) -> None:
        if self._annual_report_provider is not None:
            await self._annual_report_provider.aclose()

    async def execute_supplement_task(
        self,
        task: SupplementTask,
        subject: ResolvedSubject,
        *,
        queried_at: datetime,
    ) -> DeepSearchSupplementOutcome:
        """Execute one explicit task without changing the authoritative MCP state."""

        if task.subject_id != subject.subject_id:
            raise AgentExecutionError("DeepSearch task subject does not match resolved subject")
        if task.report_as_of > queried_at.date():
            raise AgentExecutionError("DeepSearch task report cutoff is in the future")
        if tuple(task.allowed_sources) != (SourceType.PUBLIC_WEB,):
            raise AgentExecutionError("DeepSearch task contains an unapproved source type")

        if task.reason is SupplementTaskReason.BASELINE_ENRICHMENT:
            expected = ("tianyancha_annual_report_social_security",)
            if task.allowed_tools != expected:
                raise AgentExecutionError("DeepSearch baseline task does not use the allowed Tool")
            if task.target_submodule_id != "annual_reports" or task.max_tool_calls != 1:
                raise AgentExecutionError("DeepSearch annual-report task is outside its boundary")
            annual_outcome = await self.fetch_annual_report_social_security(
                subject=subject,
                queried_at=queried_at,
                report_as_of=task.report_as_of,
            )
            return DeepSearchSupplementOutcome(
                task_id=task.task_id,
                target_submodule_id=task.target_submodule_id,
                source_status=annual_outcome.source_status,
                evidence=annual_outcome.evidence,
                scope={
                    "checked_years": list(annual_outcome.checked_years),
                },
                unresolved=(annual_outcome.source_status is not SourceStatus.VERIFIED_RECORDS),
                conflicts_with=task.conflict_evidence_ids,
            )

        if task.trigger_status is SourceStatus.VERIFIED_EMPTY:
            raise AgentExecutionError("DeepSearch cannot replace a verified_empty MCP source state")
        if task.allowed_tools != ("bounded_web_search",):
            raise AgentExecutionError("DeepSearch gap task does not use the allowed Tool")
        if self._supplement_service is None:
            raise AgentExecutionError("DeepSearch supplemental research is not configured")
        domain = task.requested_fields[0].split(".", maxsplit=1)[0]
        reason = (
            CoverageGapReason.SOURCE_UNAVAILABLE
            if task.trigger_status is SourceStatus.SOURCE_ERROR
            else CoverageGapReason.MISSING_FIELDS
        )
        gap = EvidenceGap(
            gap_id=task.task_id,
            subject_id=task.subject_id,
            domain=domain,
            submodule_id=task.target_submodule_id,
            capability=task.gap_type or "evidence_gap",
            topic=task.target_submodule_id,
            supports_fields=task.requested_fields,
            reason=reason,
        )
        research_outcome = await self._supplement_service.research(
            subject=subject,
            gap=gap,
            queried_at=queried_at,
            report_as_of=task.report_as_of,
        )
        if any(
            item.subject_id != subject.subject_id or item.source_type is not SourceType.PUBLIC_WEB
            for item in research_outcome.evidence
        ):
            raise AgentExecutionError("DeepSearch returned Evidence outside the task boundary")
        return DeepSearchSupplementOutcome(
            task_id=task.task_id,
            target_submodule_id=task.target_submodule_id,
            source_status=(
                SourceStatus.VERIFIED_RECORDS
                if research_outcome.evidence
                else SourceStatus.VERIFIED_EMPTY
            ),
            evidence=research_outcome.evidence,
            scope={
                "query_ids": [item.query_id for item in research_outcome.executed_queries],
                "rounds_executed": research_outcome.rounds_executed,
                "errors": list(research_outcome.errors),
            },
            unresolved=research_outcome.unresolved,
            conflicts_with=task.conflict_evidence_ids,
        )

    async def investigate(
        self,
        task: DeepSearchTask,
        subject: ResolvedSubject,
        *,
        queried_at: datetime,
    ) -> DomainInvestigation:
        from jindiao.orchestration.base import DomainInvestigation

        capability_fallback = (
            task.source.status is SourceStatus.CAPABILITY_ABSENT
            and task.source.mock_fallback_allowed
        )
        error_fallback = (
            task.source.status is SourceStatus.SOURCE_ERROR and task.allow_degraded_mock
        )
        if not capability_fallback and not error_fallback:
            raise AgentExecutionError(
                "DeepSearch agent received a fallback task that policy does not allow",
                details={
                    "domain": task.domain,
                    "capability": task.capability,
                    "source_status": task.source.status.value,
                },
            )
        if self._service is None:
            raise AgentExecutionError(
                "DeepSearch Mock fallback is not configured",
                details={"domain": task.domain, "capability": task.capability},
            )
        outcome = await self._service.retrieve(
            source=task.source,
            domain=task.domain,
            capability=task.capability,
            query=task.query,
            subject=subject,
            queried_at=queried_at,
            allow_degraded_mock=task.allow_degraded_mock,
        )
        record_count = (
            len(outcome.evidence)
            if outcome.source_status in {SourceStatus.VERIFIED_RECORDS, SourceStatus.DEGRADED_MOCK}
            else 0
        )
        return DomainInvestigation(
            task_id=f"deepsearch-{task.domain}-{task.capability}",
            domain=task.domain,
            evidence=outcome.evidence,
            coverage_items=(
                CoverageItem(
                    domain=task.domain,
                    capability=task.capability,
                    status=outcome.source_status,
                    record_count=record_count,
                    fallback_reason=outcome.fallback_reason,
                ),
            ),
        )


__all__ = [
    "DeepSearchCapabilityAgent",
    "DeepSearchEvidenceAgent",
    "DeepSearchSupplementOutcome",
    "DeepSearchTask",
    "SupplementalResearchService",
]
