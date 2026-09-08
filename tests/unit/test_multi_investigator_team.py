from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.agents import MultiInvestigatorTeam
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceStatus, SourceType
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    FactEvidenceRef,
    RepairTask,
    ReviewIssue,
)
from jindiao.investigation import CHECK_CATALOG, CheckAssignment, CheckAssignmentPlan
from jindiao.investigation.blackboard import ReviewSubmission
from jindiao.orchestration import BudgetLedger, OpenJiuwenAgentExecutionRuntime, RunBudget
from jindiao.prompts import load_prompt_bundle

NOW = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)
ROLE_AGENTS = {
    "corporate": "corporate-agent",
    "judicial-compliance": "judicial-compliance-agent",
    "financial-operations": "financial-operations-agent",
    "related-peer": "related-peer-agent",
}


def snapshot() -> EnterpriseContextSnapshot:
    subject = ResolvedSubject(
        subject_id="tyc:multi-1",
        company_name="Multi Agent 测试有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )
    evidence = Evidence(
        evidence_id="ev-registration",
        claim="工商状态为存续",
        value={"registration_status": "存续"},
        subject_id=subject.subject_id,
        source_type=SourceType.TIANYANCHA,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="get_company_registration_info",
        source_record_id="reg-1",
        queried_at=NOW,
        as_of_date=REPORT_AS_OF,
        confidence=1,
        is_mock=False,
        supports_fields=("governance.registration",),
        raw_ref="mcp://tianyancha/registration/reg-1",
        content_hash="sha256:" + "b" * 64,
    )
    submodules = tuple(
        SubmoduleContext(
            submodule_id=submodule_id,
            availability=(
                SubmoduleAvailability.AVAILABLE
                if submodule_id == "registration"
                else SubmoduleAvailability.NOT_REQUESTED
            ),
            completeness=(
                CoverageCompleteness.COMPLETE
                if submodule_id == "registration"
                else CoverageCompleteness.UNKNOWN
            ),
            facts=({"registration_status": "存续"} if submodule_id == "registration" else {}),
            evidence_ids=(evidence.evidence_id,) if submodule_id == "registration" else (),
            unresolved_gap_ids=(() if submodule_id == "registration" else (f"gap:{submodule_id}",)),
        )
        for submodule_id in ACQUISITION_CATALOG.default_plan_ids
    )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:multi",
        snapshot_sha256="a" * 64,
        subject=subject,
        report_as_of=REPORT_AS_OF,
        created_at=NOW,
        acquisition_catalog_version=ACQUISITION_CATALOG.catalog_version,
        planned_submodule_ids=ACQUISITION_CATALOG.default_plan_ids,
        source_manifest_version="manifest-v1",
        submodules=submodules,
        evidence=(evidence,),
        supplement_tasks=(),
        unresolved_gaps=tuple(
            f"gap:{item}" for item in ACQUISITION_CATALOG.default_plan_ids if item != "registration"
        ),
        unresolved_conflicts=(),
    )


def message(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        tool_calls=tool_calls,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="multi-scripted-model",
            input_tokens=6,
            output_tokens=2,
            total_tokens=8,
        ),
    )


class ScriptedModel:
    def __init__(self, responses: list[AssistantMessage]) -> None:
        self.responses = responses
        self.calls = 0

    async def invoke(self, **kwargs: object) -> AssistantMessage:
        del kwargs
        response = self.responses[self.calls]
        self.calls += 1
        return response

    async def stream(self, **kwargs: object) -> AsyncIterator[AssistantMessageChunk]:
        del kwargs
        response = self.responses[self.calls]
        self.calls += 1
        yield AssistantMessageChunk(
            content=response.content,
            tool_calls=response.tool_calls,
            usage_metadata=response.usage_metadata,
            finish_reason=response.finish_reason,
        )


def plan() -> CheckAssignmentPlan:
    target = snapshot()
    return CheckAssignmentPlan(
        run_id="run-multi-formal",
        snapshot_id=target.snapshot_id,
        snapshot_sha256=target.snapshot_sha256,
        subject_id=target.subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        prompt_version="investigation-core-v1+leader-v1",
        assignment_version=1,
        assignments=tuple(
            CheckAssignment(
                task_id=f"check:{check.check_id}",
                check_id=check.check_id,
                assigned_agent_id=ROLE_AGENTS[check.owner_role],
                required_submodule_ids=check.required_submodule_ids,
                optional_submodule_ids=check.optional_submodule_ids,
                prompt_template_id=check.prompt_template_id,
                output_schema_version=check.output_schema_version,
            )
            for check in CHECK_CATALOG.checks
        ),
    )


def leader_responses() -> list[AssistantMessage]:
    return [
        message(
            tool_calls=[
                ToolCall(
                    id="submit-assignments",
                    type="function",
                    name="submit_check_assignments",
                    arguments=json.dumps({"plan": plan().model_dump(mode="json")}),
                )
            ]
        ),
        message("all fixed checks assigned"),
    ]


def check_result(check_id: str) -> CheckResult:
    target = snapshot()
    definition = CHECK_CATALOG.get(check_id)
    registration = check_id == "registration-status-normal"
    return CheckResult(
        snapshot_id=target.snapshot_id,
        snapshot_sha256=target.snapshot_sha256,
        subject_id=target.subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        output_schema_version=definition.output_schema_version,
        task_id=f"check:{check_id}",
        check_id=check_id,
        status=CheckStatus.NO_RISK if registration else CheckStatus.INCONCLUSIVE,
        decision_summary=("工商登记状态正常。" if registration else "必需快照证据不足。"),
        fact_evidence_refs=(
            (
                FactEvidenceRef(
                    evidence_id="ev-registration",
                    fact_path="governance.registration.registration_status",
                    summary="工商状态为存续",
                ),
            )
            if registration
            else ()
        ),
        missing_evidence=() if registration else definition.required_submodule_ids,
        confidence=0.95 if registration else 0.1,
        prompt_version=f"investigation-core-v1+{definition.owner_role}-v1",
        submission_version=1,
    )


def specialist_responses(role: str) -> list[AssistantMessage]:
    target = snapshot()
    responses: list[AssistantMessage] = []
    for check in CHECK_CATALOG.checks:
        if check.owner_role != role:
            continue
        task_id = f"check:{check.check_id}"
        responses.append(
            message(
                tool_calls=[
                    ToolCall(
                        id=f"read-{check.check_id}",
                        type="function",
                        name="read_snapshot_submodule",
                        arguments=json.dumps(
                            {
                                "snapshot_id": target.snapshot_id,
                                "task_id": task_id,
                                "submodule_id": check.required_submodule_ids[0],
                            }
                        ),
                    )
                ]
            )
        )
        if check.check_id == "registration-status-normal":
            responses.append(
                message(
                    tool_calls=[
                        ToolCall(
                            id="read-registration-evidence",
                            type="function",
                            name="read_snapshot_evidence",
                            arguments=json.dumps(
                                {
                                    "snapshot_id": target.snapshot_id,
                                    "task_id": task_id,
                                    "submodule_id": "registration",
                                    "evidence_ids": ["ev-registration"],
                                }
                            ),
                        )
                    ]
                )
            )
        result = check_result(check.check_id)
        responses.append(
            message(
                tool_calls=[
                    ToolCall(
                        id=f"submit-{check.check_id}",
                        type="function",
                        name="submit_check_result",
                        arguments=json.dumps(
                            {"result": result.model_dump(mode="json")},
                            ensure_ascii=False,
                        ),
                    )
                ]
            )
        )
    responses.append(message(f"{role} checks complete"))
    return responses


def reviewer_responses(review: ReviewSubmission | None = None) -> list[AssistantMessage]:
    if review is None:
        target = snapshot()
        review = ReviewSubmission(
            snapshot_id=target.snapshot_id,
            snapshot_sha256=target.snapshot_sha256,
            subject_id=target.subject.subject_id,
            check_catalog_version=CHECK_CATALOG.catalog_version,
            prompt_version="investigation-core-v1+reviewer-v1",
            review_version=1,
        )
    return [
        message(
            tool_calls=[
                ToolCall(
                    id="read-submissions",
                    type="function",
                    name="read_check_submissions",
                    arguments="{}",
                )
            ]
        ),
        message(
            tool_calls=[
                ToolCall(
                    id="submit-review",
                    type="function",
                    name="submit_review",
                    arguments=json.dumps(
                        {"review": review.model_dump(mode="json")},
                        ensure_ascii=False,
                    ),
                )
            ]
        ),
        message("review complete"),
    ]


def ledger() -> BudgetLedger:
    return BudgetLedger(
        RunBudget(
            max_tool_calls=100,
            max_concurrency=4,
            timeout_seconds=30,
            max_repair_rounds=1,
            max_llm_requests=100,
            max_input_tokens=100_000,
            max_output_tokens=50_000,
            max_total_tokens=150_000,
            max_schema_retries=4,
            max_snapshot_reads=60,
        )
    )


@pytest.mark.asyncio
async def test_multi_team_assigns_all_checks_runs_specialists_and_reviewer() -> None:
    models = {
        "leader": ScriptedModel(leader_responses()),
        **{
            agent_id: ScriptedModel(specialist_responses(role))
            for role, agent_id in ROLE_AGENTS.items()
        },
        "reviewer-agent": ScriptedModel(reviewer_responses()),
    }
    budget = ledger()
    team = MultiInvestigatorTeam(prompt_bundle=load_prompt_bundle())

    await Runner.start()
    try:
        completed = await team.run(
            snapshot=snapshot(),
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            budget_ledger=budget,
            run_id="run-multi-formal",
            model_name="multi-scripted-model",
            model_provider="scripted",
            models=cast(dict[str, Any], models),
            timeout_seconds=20,
        )
    finally:
        await Runner.stop()

    assert tuple(item.check_id for item in completed.assignments.assignments) == (
        CHECK_CATALOG.check_ids
    )
    assert len(completed.agent_results) == 6
    check_results = tuple(
        result for agent_result in completed.agent_results for result in agent_result.check_results
    )
    assert {item.check_id for item in check_results} == set(CHECK_CATALOG.check_ids)
    assert completed.reviews[-1].repair_tasks == ()
    assert "deepsearch-agent" not in {
        item.member_name for item in completed.team_spec.predefined_members
    }
    model_calls = sum(item.calls for item in models.values())
    usage = budget.snapshot()
    assert usage.llm_requests == model_calls
    assert usage.provider_usage_requests == model_calls
    assert usage.snapshot_reads == len(CHECK_CATALOG.checks) + 1
    assert all("system_prompt" not in event.payload for event in completed.events)


@pytest.mark.asyncio
async def test_multi_reviewer_targets_one_repair_and_requires_versioned_resubmission() -> None:
    target = snapshot()
    issue = ReviewIssue(
        issue_id="issue-registration-wording",
        issue_type="evidence_summary",
        message="登记结论需要明确时点。",
        check_ids=("registration-status-normal",),
        evidence_ids=("ev-registration",),
        target_agent="corporate-agent",
    )
    repair = RepairTask(
        repair_id="repair-registration-wording",
        issue_ids=(issue.issue_id,),
        target_agent="corporate-agent",
        requested_fields=("registration.registration_status",),
        required_evidence=("ev-registration",),
        attempt=1,
        max_attempts=1,
    )
    first_review = ReviewSubmission(
        snapshot_id=target.snapshot_id,
        snapshot_sha256=target.snapshot_sha256,
        subject_id=target.subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        prompt_version="investigation-core-v1+reviewer-v1",
        review_version=1,
        issues=(issue,),
        repair_tasks=(repair,),
    )
    final_review = first_review.model_copy(
        update={"review_version": 2, "issues": (), "repair_tasks": ()}
    )
    repaired = check_result("registration-status-normal").model_copy(
        update={
            "decision_summary": "截至报告日, 工商登记状态为存续。",
            "submission_version": 2,
        }
    )
    repair_responses = [
        message(
            tool_calls=[
                ToolCall(
                    id="repair-read-registration",
                    type="function",
                    name="read_snapshot_submodule",
                    arguments=json.dumps(
                        {
                            "snapshot_id": target.snapshot_id,
                            "task_id": "check:registration-status-normal",
                            "submodule_id": "registration",
                        }
                    ),
                )
            ]
        ),
        message(
            tool_calls=[
                ToolCall(
                    id="repair-read-evidence",
                    type="function",
                    name="read_snapshot_evidence",
                    arguments=json.dumps(
                        {
                            "snapshot_id": target.snapshot_id,
                            "task_id": "check:registration-status-normal",
                            "submodule_id": "registration",
                            "evidence_ids": ["ev-registration"],
                        }
                    ),
                )
            ]
        ),
        message(
            tool_calls=[
                ToolCall(
                    id="repair-submit-registration",
                    type="function",
                    name="submit_check_result",
                    arguments=json.dumps(
                        {"result": repaired.model_dump(mode="json")},
                        ensure_ascii=False,
                    ),
                )
            ]
        ),
        message("repair complete"),
    ]
    models = {
        "leader": ScriptedModel(leader_responses()),
        **{
            agent_id: ScriptedModel(
                [
                    *specialist_responses(role),
                    *(repair_responses if agent_id == "corporate-agent" else ()),
                ]
            )
            for role, agent_id in ROLE_AGENTS.items()
        },
        "reviewer-agent": ScriptedModel(
            [*reviewer_responses(first_review), *reviewer_responses(final_review)]
        ),
    }
    budget = ledger()

    await Runner.start()
    try:
        completed = await MultiInvestigatorTeam(prompt_bundle=load_prompt_bundle()).run(
            snapshot=target,
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            budget_ledger=budget,
            run_id="run-multi-formal",
            model_name="multi-scripted-model",
            model_provider="scripted",
            models=cast(dict[str, Any], models),
            timeout_seconds=20,
        )
    finally:
        await Runner.stop()

    assert tuple(item.review_version for item in completed.reviews) == (1, 2)
    corporate = next(item for item in completed.agent_results if item.agent_id == "corporate-agent")
    registration = next(
        item for item in corporate.check_results if item.check_id == "registration-status-normal"
    )
    assert registration.submission_version == 2
    assert budget.snapshot().repair_rounds == 1


@pytest.mark.asyncio
async def test_multi_team_fails_when_one_specialist_omits_assigned_checks() -> None:
    models = {
        "leader": ScriptedModel(leader_responses()),
        **{
            agent_id: ScriptedModel(
                [message("ended early")]
                if agent_id == "corporate-agent"
                else specialist_responses(role)
            )
            for role, agent_id in ROLE_AGENTS.items()
        },
        "reviewer-agent": ScriptedModel(reviewer_responses()),
    }

    await Runner.start()
    try:
        with pytest.raises(AgentExecutionError, match="missing assigned checks"):
            await MultiInvestigatorTeam(prompt_bundle=load_prompt_bundle()).run(
                snapshot=snapshot(),
                runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
                budget_ledger=ledger(),
                run_id="run-multi-formal",
                model_name="multi-scripted-model",
                model_provider="scripted",
                models=cast(dict[str, Any], models),
                timeout_seconds=20,
            )
    finally:
        await Runner.stop()


@pytest.mark.asyncio
async def test_multi_team_stops_on_global_llm_budget_exhaustion() -> None:
    constrained = BudgetLedger(ledger().budget.model_copy(update={"max_llm_requests": 1}))
    models = {
        "leader": ScriptedModel(leader_responses()),
        **{
            agent_id: ScriptedModel(specialist_responses(role))
            for role, agent_id in ROLE_AGENTS.items()
        },
        "reviewer-agent": ScriptedModel(reviewer_responses()),
    }

    await Runner.start()
    try:
        with pytest.raises(AgentExecutionError, match="LLM-request budget exhausted"):
            await MultiInvestigatorTeam(prompt_bundle=load_prompt_bundle()).run(
                snapshot=snapshot(),
                runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
                budget_ledger=constrained,
                run_id="run-multi-formal",
                model_name="multi-scripted-model",
                model_provider="scripted",
                models=cast(dict[str, Any], models),
                timeout_seconds=20,
            )
    finally:
        await Runner.stop()

    assert constrained.snapshot().llm_requests == 1
    assert constrained.snapshot().exhausted_reason == ("orchestration LLM-request budget exhausted")
