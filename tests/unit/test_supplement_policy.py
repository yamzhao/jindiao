from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jindiao.agents import DeepSearchEvidenceAgent
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import (
    SubmoduleAvailability,
    SubmoduleContext,
    SupplementTask,
    SupplementTaskReason,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, SourceStatus, SourceType
from jindiao.deepsearch import AnnualReportSocialSecurityOutcome
from jindiao.deepsearch.policy import SupplementPolicy

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:123",
        company_name="补证测试有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


def submodule(
    submodule_id: str,
    availability: SubmoduleAvailability,
) -> SubmoduleContext:
    return SubmoduleContext(
        submodule_id=submodule_id,
        availability=availability,
        completeness=(
            CoverageCompleteness.COMPLETE
            if availability is SubmoduleAvailability.VERIFIED_EMPTY
            else CoverageCompleteness.UNKNOWN
        ),
        unresolved_gap_ids=(
            ()
            if availability is SubmoduleAvailability.VERIFIED_EMPTY
            else (f"gap:{submodule_id}:{availability.value}",)
        ),
    )


def test_policy_plans_one_baseline_and_only_eligible_explicit_gap_tasks() -> None:
    contexts = (
        submodule("annual_reports", SubmoduleAvailability.VERIFIED_EMPTY),
        submodule("financial_summary", SubmoduleAvailability.CAPABILITY_ABSENT),
        submodule("registration", SubmoduleAvailability.SOURCE_ERROR),
        submodule("judicial_documents", SubmoduleAvailability.VERIFIED_EMPTY),
    )
    policy = SupplementPolicy(
        annual_report_social_security_enabled=True,
        max_gap_tasks=5,
    )

    first = policy.plan(
        subject=subject(),
        report_as_of=REPORT_AS_OF,
        submodules=contexts,
    )
    second = policy.plan(
        subject=subject(),
        report_as_of=REPORT_AS_OF,
        submodules=contexts,
    )

    assert first == second
    baseline = [item for item in first if item.reason is SupplementTaskReason.BASELINE_ENRICHMENT]
    gaps = [item for item in first if item.reason is SupplementTaskReason.EVIDENCE_GAP]
    assert len(baseline) == 1
    assert baseline[0].target_submodule_id == "annual_reports"
    assert baseline[0].allowed_tools == ("tianyancha_annual_report_social_security",)
    assert {item.target_submodule_id for item in gaps} == {
        "financial_summary",
        "registration",
    }
    assert all(item.trigger_status is not SourceStatus.VERIFIED_EMPTY for item in gaps)
    assert all(item.allowed_sources == (SourceType.PUBLIC_WEB,) for item in first)


def test_policy_applies_one_shared_gap_budget_without_counting_baseline() -> None:
    contexts = (
        submodule("annual_reports", SubmoduleAvailability.CAPABILITY_ABSENT),
        submodule("registration", SubmoduleAvailability.SOURCE_ERROR),
        submodule("financial_summary", SubmoduleAvailability.CAPABILITY_ABSENT),
    )

    tasks = SupplementPolicy(
        annual_report_social_security_enabled=True,
        max_gap_tasks=1,
    ).plan(
        subject=subject(),
        report_as_of=REPORT_AS_OF,
        submodules=contexts,
    )

    assert [item.reason for item in tasks].count(SupplementTaskReason.BASELINE_ENRICHMENT) == 1
    gaps = [item for item in tasks if item.reason is SupplementTaskReason.EVIDENCE_GAP]
    assert len(gaps) == 1
    assert gaps[0].target_submodule_id == "registration"


class RecordingAnnualReportProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []
        self.closed = False

    async def fetch_latest(
        self,
        resolved: ResolvedSubject,
        *,
        queried_at: datetime,
        report_as_of: date,
    ) -> AnnualReportSocialSecurityOutcome:
        del queried_at
        self.calls.append((resolved.subject_id, report_as_of))
        return AnnualReportSocialSecurityOutcome(
            source_status=SourceStatus.VERIFIED_EMPTY,
            checked_years=(2025, 2024),
        )

    async def aclose(self) -> None:
        self.closed = True


def baseline_task() -> SupplementTask:
    return SupplementTask(
        task_id="supplement:annual_reports:baseline",
        reason=SupplementTaskReason.BASELINE_ENRICHMENT,
        target_submodule_id="annual_reports",
        subject_id=subject().subject_id,
        report_as_of=REPORT_AS_OF,
        allowed_tools=("tianyancha_annual_report_social_security",),
        allowed_sources=(SourceType.PUBLIC_WEB,),
        requested_fields=("operations.annual_reports.social_security",),
        max_tool_calls=1,
    )


@pytest.mark.asyncio
async def test_deepsearch_executes_only_a_valid_explicit_supplement_task() -> None:
    provider = RecordingAnnualReportProvider()
    agent = DeepSearchEvidenceAgent(annual_report_provider=provider)

    outcome = await agent.execute_supplement_task(
        baseline_task(),
        subject(),
        queried_at=NOW,
    )

    assert outcome.task_id == baseline_task().task_id
    assert outcome.source_status is SourceStatus.VERIFIED_EMPTY
    assert outcome.evidence == ()
    assert outcome.scope == {"checked_years": [2025, 2024]}
    assert provider.calls == [(subject().subject_id, REPORT_AS_OF)]

    invalid = baseline_task().model_copy(update={"allowed_tools": ("unbounded_web_browser",)})
    with pytest.raises(AgentExecutionError, match="allowed Tool"):
        await agent.execute_supplement_task(invalid, subject(), queried_at=NOW)
    wrong_subject = subject().model_copy(update={"subject_id": "tyc:999"})
    with pytest.raises(AgentExecutionError, match="subject"):
        await agent.execute_supplement_task(baseline_task(), wrong_subject, queried_at=NOW)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_verified_empty_mcp_state_cannot_be_turned_into_a_gap_task() -> None:
    provider = RecordingAnnualReportProvider()
    agent = DeepSearchEvidenceAgent(annual_report_provider=provider)
    denied = SupplementTask(
        task_id="supplement:judicial_documents:gap",
        reason=SupplementTaskReason.EVIDENCE_GAP,
        target_submodule_id="judicial_documents",
        subject_id=subject().subject_id,
        report_as_of=REPORT_AS_OF,
        allowed_tools=("bounded_web_search",),
        allowed_sources=(SourceType.PUBLIC_WEB,),
        requested_fields=("judicial.documents",),
        max_tool_calls=1,
        gap_type="source_gap",
        trigger_status=SourceStatus.VERIFIED_EMPTY,
    )

    with pytest.raises(AgentExecutionError, match="verified_empty"):
        await agent.execute_supplement_task(denied, subject(), queried_at=NOW)
    assert provider.calls == []
