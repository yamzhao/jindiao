from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest
from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
)
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner
from test_multi_investigator_team import ROLE_AGENTS, check_result, plan, snapshot

from jindiao.agents import AgentTeamsInvestigatorTeam
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.investigation import RepairTask, ReviewIssue
from jindiao.investigation import CHECK_CATALOG
from jindiao.investigation.blackboard import ReviewSubmission
from jindiao.orchestration import BudgetLedger, RunBudget, TeamRuntimeEvent
from jindiao.orchestration.investigation_team_tools import (
    get_investigation_team_state,
)
from jindiao.prompts import load_prompt_bundle

SCRIPTED_PROVIDER = "jindiao_agent_teams_scripted"


def _pending_scheduler_tasks(session_id: str) -> list[asyncio.Task[Any]]:
    pending: list[asyncio.Task[Any]] = []
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current or task.done():
            continue
        coroutine = task.get_coro()
        if getattr(coroutine, "__qualname__", "") != ("TaskScheduler._execute_task_wrapper"):
            continue
        frame = getattr(coroutine, "cr_frame", None)
        session = frame.f_locals.get("session") if frame is not None else None
        get_session_id = getattr(session, "get_session_id", None)
        if callable(get_session_id) and get_session_id() == session_id:
            pending.append(task)
    return pending


def _message(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        tool_calls=tool_calls,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="agent-teams-scripted",
            input_tokens=9,
            output_tokens=3,
            total_tokens=12,
        ),
    )


def _call(name: str, arguments: dict[str, object], call_id: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        name=name,
        arguments=json.dumps(arguments, ensure_ascii=False),
    )


def _bound_decision(result: Any) -> dict[str, object]:
    payload = result.model_dump(mode="json")
    risk_items = []
    for item in payload["risk_items"]:
        risk_items.append(
            {key: value for key, value in item.items() if key not in {"check_id", "status"}}
        )
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "check_id",
            "status",
            "decision_summary",
            "fact_evidence_refs",
            "missing_evidence",
            "conflicts",
            "confidence",
        }
    } | {"risk_items": risk_items}


class ScriptedAgentTeamsModelClient(BaseModelClient):  # type: ignore[misc]
    __client_name__ = SCRIPTED_PROVIDER
    __client_type__ = "llm"
    seen_tools_by_member: ClassVar[dict[str, set[str]]] = {}

    def __init__(self, model_config: Any, model_client_config: Any) -> None:
        super().__init__(model_config, model_client_config)
        self.runtime_key = str(model_client_config.runtime_key)
        self.member_name = str(model_client_config.member_name)
        self.build_sent = False
        self.initial_tasks_created = False
        self.repair_tasks_created = False
        self.review_one_created = False
        self.review_two_created = False
        self.shutdown_sent = False
        self.clean_sent = False
        self.clean_attempts = 0
        self.started_tasks: set[str] = set()
        self.read_sent: set[str] = set()
        self.submit_sent: set[str] = set()
        self.complete_sent: set[str] = set()
        self.progress_polls = 0
        self.poll_pending = False

    def _validate_config(self) -> None:
        return None

    @classmethod
    def _tool_name(cls, value: object) -> str:
        if isinstance(value, dict):
            function = value.get("function")
            if isinstance(function, dict) and function.get("name"):
                return str(function["name"])
            return str(value.get("name") or value.get("tool_name") or "")
        card = getattr(value, "card", None)
        return str(
            getattr(card, "name", None)
            or getattr(value, "name", None)
            or getattr(value, "tool_name", None)
            or ""
        )

    def _capture_tools(self, kwargs: dict[str, Any]) -> None:
        observed = type(self).seen_tools_by_member.setdefault(self.member_name, set())
        tools = kwargs.get("tools") or ()
        observed.update(name for item in tools if (name := self._tool_name(item)))

    @staticmethod
    def _text(messages: object) -> str:
        values: list[str] = []
        if isinstance(messages, list):
            for item in messages:
                content = (
                    item.get("content", "")
                    if isinstance(item, dict)
                    else getattr(item, "content", "")
                )
                values.append(str(content))
        else:
            values.append(str(messages))
        return "\n".join(values)

    async def _response(self, messages: object) -> AssistantMessage:
        text = self._text(messages)
        role = (
            "leader"
            if self.member_name == "leader"
            else "reviewer"
            if self.member_name == "reviewer-agent"
            else {agent_id: role for role, agent_id in ROLE_AGENTS.items()}[self.member_name]
        )
        state = get_investigation_team_state(self.runtime_key)
        if role == "leader":
            return self._leader_response(state, text)
        if role == "reviewer":
            return self._reviewer_response(state, text)
        return self._specialist_response(state, role, text)

    def _leader_response(self, state: Any, text: str) -> AssistantMessage:
        if state.assignment_board.plan is None and not self.build_sent:
            self.build_sent = True
            return _message(
                tool_calls=[
                    _call(
                        "build_team",
                        {
                            "display_name": "Jindiao fixed-check team",
                            "team_desc": "Prompt-driven enterprise due diligence",
                            "leader_display_name": "Due Diligence Leader",
                            "leader_desc": "Assigns fixed checks and routes repairs",
                            "enable_hitt": False,
                        },
                        "build-team",
                    )
                ]
            )
        if state.assignment_board.plan is None:
            return _message(
                tool_calls=[
                    _call(
                        "submit_check_assignments",
                        {},
                        "submit-plan",
                    )
                ]
            )
        if not self.initial_tasks_created:
            self.initial_tasks_created = True
            tasks = [
                {
                    "task_id": f"investigate:{role}",
                    "title": f"Investigate {role}",
                    "content": (
                        f"Complete every assigned {role} fixed check and submit each result."
                    ),
                    "assignee": agent_id,
                }
                for role, agent_id in ROLE_AGENTS.items()
            ]
            return _message(
                tool_calls=[
                    _call(
                        "create_task",
                        {"tasks": tasks},
                        "create-initial-investigation-tasks",
                    )
                ]
            )
        review = state.submission_board.latest_review
        registration = state.submission_board.latest_result("registration-status-normal")
        if (
            len(state.submission_board.accepted_results) == len(CHECK_CATALOG.check_ids)
            and review is None
            and not self.review_one_created
        ):
            self.review_one_created = True
            return _message(
                tool_calls=[
                    _call(
                        "create_task",
                        {
                            "tasks": [
                                {
                                    "task_id": "review:round1-signal",
                                    "title": "Start independent review round 1",
                                    "content": "Review all initial fixed-check submissions.",
                                    "assignee": "reviewer-agent",
                                }
                            ]
                        },
                        "create-review-one-signal",
                    )
                ]
            )
        if (
            review is not None
            and review.repair_tasks
            and registration is not None
            and registration.submission_version == 1
            and not self.repair_tasks_created
        ):
            self.repair_tasks_created = True
            return _message(
                tool_calls=[
                    _call(
                        "create_task",
                        {
                            "tasks": [
                                {
                                    "task_id": "repair:registration-signal",
                                    "title": "Repair registration wording",
                                    "content": (
                                        "Apply Reviewer repair and resubmit registration "
                                        "check as version 2."
                                    ),
                                    "assignee": "corporate-agent",
                                }
                            ]
                        },
                        "create-registration-repair-signal",
                    )
                ]
            )
        if (
            review is not None
            and review.review_version == 1
            and registration is not None
            and registration.submission_version == 2
            and not self.review_two_created
        ):
            self.review_two_created = True
            return _message(
                tool_calls=[
                    _call(
                        "create_task",
                        {
                            "tasks": [
                                {
                                    "task_id": "review:round2-signal",
                                    "title": "Start independent review round 2",
                                    "content": (
                                        "Review versioned repair and close resolved issues."
                                    ),
                                    "assignee": "reviewer-agent",
                                }
                            ]
                        },
                        "create-review-two-signal",
                    )
                ]
            )
        if review is not None and review.review_version == 2 and not review.repair_tasks:
            if not self.shutdown_sent:
                self.shutdown_sent = True
                return _message(
                    tool_calls=[
                        _call(
                            "shutdown_member",
                            {"member_name": member, "force": True},
                            f"shutdown-{member}",
                        )
                        for member in (*ROLE_AGENTS.values(), "reviewer-agent")
                    ]
                )
            if not self.clean_sent or "Active members remain" in text:
                self.clean_sent = True
                self.clean_attempts += 1
                return _message(
                    tool_calls=[
                        _call(
                            "clean_team",
                            {},
                            f"clean-team-{self.clean_attempts}",
                        )
                    ]
                )
            return _message("team complete")
        outstanding_review = self.review_two_created and (
            review is None or review.review_version < 2
        )
        if outstanding_review:
            return _message("waiting for member state transition")
        if self.poll_pending:
            self.poll_pending = False
            return _message("waiting for assigned tasks and review results")
        self.poll_pending = True
        self.progress_polls += 1
        return _message(
            tool_calls=[
                _call(
                    "read_investigation_progress",
                    {},
                    f"read-investigation-progress-{self.progress_polls}",
                )
            ]
        )

    def _specialist_response(self, state: Any, role: str, text: str) -> AssistantMessage:
        latest_review = state.submission_board.latest_review
        registration = state.submission_board.latest_result("registration-status-normal")
        repair_task_key = "repair:registration-signal"
        repair_in_progress = (
            repair_task_key in self.started_tasks and repair_task_key not in self.complete_sent
        )
        is_repair = role == "corporate" and (
            repair_in_progress
            or (
                latest_review is not None
                and bool(latest_review.repair_tasks)
                and registration is not None
                and registration.submission_version == 1
            )
        )
        task_key = repair_task_key if is_repair else f"investigate:{role}"
        if task_key not in self.started_tasks:
            if "[任务开工]" not in text or f"任务 [{task_key}]" not in text:
                return _message(f"waiting for scheduled handoff: {task_key}")
            self.started_tasks.add(task_key)
        owned = [item for item in CHECK_CATALOG.checks if item.owner_role == role]
        if is_repair:
            owned = [CHECK_CATALOG.get("registration-status-normal")]
        if task_key not in self.read_sent:
            self.read_sent.add(task_key)
            return _message(
                tool_calls=[
                    _call(
                        "read_assigned_snapshot_context",
                        {},
                        f"read-{task_key}-assigned-context",
                    )
                ]
            )
        if task_key not in self.submit_sent:
            self.submit_sent.add(task_key)
            results = []
            for definition in owned:
                result = check_result(definition.check_id)
                if is_repair:
                    result = result.model_copy(
                        update={
                            "submission_version": 2,
                            "decision_summary": "截至报告日，工商登记状态为存续。",  # noqa: RUF001
                        }
                    )
                results.append(
                    _call(
                        "submit_check_result",
                        {"result": _bound_decision(result)},
                        f"submit-{task_key}-{definition.check_id}",
                    )
                )
            return _message(tool_calls=results)
        if task_key not in self.complete_sent:
            self.complete_sent.add(task_key)
            return _message(
                tool_calls=[
                    _call(
                        "member_complete_task",
                        {"task_id": task_key, "note": f"{task_key} submitted"},
                        f"complete-{task_key}",
                    )
                ]
            )
        return _message(f"{task_key} complete")

    def _reviewer_response(self, state: Any, text: str) -> AssistantMessage:
        latest = state.submission_board.latest_review
        registration = state.submission_board.latest_result("registration-status-normal")
        review_one_task = "review:round1-signal"
        review_two_task = "review:round2-signal"
        if review_two_task in self.started_tasks and review_two_task not in self.complete_sent:
            review_version = 2
        elif review_one_task in self.started_tasks and review_one_task not in self.complete_sent:
            review_version = 1
        elif latest is None:
            if len(state.submission_board.accepted_results) != len(CHECK_CATALOG.check_ids):
                return _message("waiting for review assignment")
            review_version = 1
        elif (
            latest.review_version == 1
            and registration is not None
            and registration.submission_version == 2
        ):
            review_version = 2
        else:
            return _message("waiting for repaired submission")
        task_key = f"review:round{review_version}-signal"
        if task_key not in self.started_tasks:
            if "[任务开工]" not in text or f"任务 [{task_key}]" not in text:
                return _message(f"waiting for scheduled handoff: {task_key}")
            self.started_tasks.add(task_key)
        review_phase = f"review:{review_version}"
        if review_phase not in self.read_sent:
            self.read_sent.add(review_phase)
            return _message(
                tool_calls=[_call("read_check_submissions", {}, f"read-{review_phase}")]
            )
        if review_phase not in self.submit_sent:
            self.submit_sent.add(review_phase)
            issues: tuple[ReviewIssue, ...]
            repairs: tuple[RepairTask, ...]
            if review_version == 1:
                issue = ReviewIssue(
                    issue_id="issue-registration-time",
                    issue_type="evidence_summary",
                    message="登记结论需明确报告时点。",
                    check_ids=("registration-status-normal",),
                    evidence_ids=("ev-registration",),
                    target_agent="corporate-agent",
                )
                repair = RepairTask(
                    repair_id="repair-registration-time",
                    issue_ids=(issue.issue_id,),
                    target_agent="corporate-agent",
                    requested_fields=("registration.registration_status",),
                    required_evidence=("ev-registration",),
                    attempt=1,
                    max_attempts=1,
                )
                issues = (issue,)
                repairs = (repair,)
            else:
                issues = ()
                repairs = ()
            return _message(
                tool_calls=[
                    _call(
                        "submit_review",
                        {
                            "review": {
                                "issues": [item.model_dump(mode="json") for item in issues],
                                "repair_tasks": [item.model_dump(mode="json") for item in repairs],
                            }
                        },
                        f"submit-{review_phase}",
                    )
                ]
            )
        if task_key not in self.complete_sent:
            self.complete_sent.add(task_key)
            return _message(
                tool_calls=[
                    _call(
                        "member_complete_task",
                        {
                            "task_id": task_key,
                            "note": f"Review {review_version} submitted",
                        },
                        f"complete-{review_phase}",
                    ),
                ]
            )
        return _message(f"{review_phase} complete")

    async def invoke(self, messages: object, **kwargs: Any) -> AssistantMessage:
        self._capture_tools(kwargs)
        return await self._response(messages)

    async def stream(
        self,
        messages: object,
        **kwargs: Any,
    ) -> AsyncIterator[AssistantMessageChunk]:
        self._capture_tools(kwargs)
        response = await self._response(messages)
        yield AssistantMessageChunk(
            content=response.content,
            tool_calls=response.tool_calls,
            usage_metadata=response.usage_metadata,
            finish_reason=response.finish_reason,
        )

    async def generate_image(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def generate_speech(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def generate_video(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _ledger() -> BudgetLedger:
    return BudgetLedger(
        RunBudget(
            max_tool_calls=160,
            max_concurrency=6,
            timeout_seconds=45,
            max_repair_rounds=1,
            max_llm_requests=120,
            max_input_tokens=100_000,
            max_output_tokens=50_000,
            max_total_tokens=150_000,
            max_schema_retries=4,
            max_snapshot_reads=80,
        )
    )


class BusinessCompleteWithResidualTasksRuntime:
    """Keep the framework stream open after the business blackboard is terminal."""

    def __init__(self) -> None:
        self.wait_called = False
        self.closed = False

    async def stream(
        self,
        spec: Any,
        inputs: dict[str, object],
        *,
        session_id: str,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        del inputs, session_id
        runtime_key = str(spec.model_pool[0].metadata["client"]["runtime_key"])
        state = get_investigation_team_state(runtime_key)
        await state.assignment_board.submit(
            plan=plan(),
            leader_agent_id="leader",
        )
        for definition in CHECK_CATALOG.checks:
            await state.submission_board.submit_check_result(
                agent_id=ROLE_AGENTS[definition.owner_role],
                result=check_result(definition.check_id),
            )
        target = state.snapshot
        await state.submission_board.submit_review(
            reviewer_agent_id="reviewer-agent",
            review=ReviewSubmission(
                snapshot_id=target.snapshot_id,
                snapshot_sha256=target.snapshot_sha256,
                subject_id=target.subject.subject_id,
                check_catalog_version=CHECK_CATALOG.catalog_version,
                prompt_version="investigation-core-v1+reviewer-v1",
                review_version=1,
            ),
        )
        try:
            yield TeamRuntimeEvent(
                event_type="submission.accepted",
                member_name="reviewer-agent",
            )
            await asyncio.Event().wait()
        finally:
            self.closed = True

    async def wait_for_tasks_terminal(self, **kwargs: object) -> None:
        del kwargs
        self.wait_called = True
        await asyncio.Event().wait()


class PrematureReviewExitRuntime:
    """End a native stream after checks, without claiming business completion."""

    def __init__(self, *, succeed: bool = True, exhaust: bool = False) -> None:
        self.succeed = succeed
        self.exhaust = exhaust
        self.calls: list[tuple[str, str, str]] = []
        self.ledgers: list[BudgetLedger] = []

    async def stream(
        self, spec: Any, inputs: dict[str, object], *, session_id: str
    ) -> AsyncIterator[TeamRuntimeEvent]:
        key = str(spec.model_pool[0].metadata["client"]["runtime_key"])
        state = get_investigation_team_state(key)
        self.calls.append((spec.team_name, session_id, str(inputs["query"])))
        self.ledgers.append(state.budget_ledger)
        if len(self.calls) == 1:
            await state.assignment_board.submit(plan=plan(), leader_agent_id="leader")
            for definition in CHECK_CATALOG.checks:
                await state.submission_board.submit_check_result(
                    agent_id=ROLE_AGENTS[definition.owner_role],
                    result=check_result(definition.check_id),
                )
        if self.exhaust:
            await state.budget_ledger.claim_llm_request("test.exhaust")
            try:
                await state.budget_ledger.record_llm_usage(
                    input_tokens=100_001, output_tokens=0, provider_usage=True
                )
            except AgentExecutionError:
                pass  # Reproduce the SDK swallowing a member budget failure.
        elif len(self.calls) > 1 and self.succeed:
            target = state.snapshot
            await state.submission_board.submit_review(
                reviewer_agent_id="reviewer-agent",
                review=ReviewSubmission(
                    snapshot_id=target.snapshot_id,
                    snapshot_sha256=target.snapshot_sha256,
                    subject_id=target.subject.subject_id,
                    check_catalog_version=CHECK_CATALOG.catalog_version,
                    prompt_version="investigation-core-v1+reviewer-v1",
                    review_version=1,
                ),
            )
        yield TeamRuntimeEvent(event_type="team.completed", member_name="leader")


async def _run_premature_runtime(runtime: PrematureReviewExitRuntime) -> Any:
    return await AgentTeamsInvestigatorTeam(
        prompt_bundle=load_prompt_bundle(),
        runtime=runtime,  # type: ignore[arg-type]
    ).run(
        snapshot=snapshot(),
        budget_ledger=_ledger(),
        run_id="run-multi-formal",
        model_name="agent-teams-scripted",
        model_provider=SCRIPTED_PROVIDER,
        model_api_key="test-key",
        model_base_url="https://model.invalid/v1",
        timeout_seconds=5,
    )


@pytest.mark.asyncio
async def test_premature_team_exit_recovers_real_review_with_same_business_state() -> None:
    runtime = PrematureReviewExitRuntime()
    result = await _run_premature_runtime(runtime)
    assert len(runtime.calls) == 2
    assert runtime.ledgers[0] is runtime.ledgers[1]
    assert runtime.calls[0][:2] != runtime.calls[1][:2]
    assert "恢复" in runtime.calls[1][2]
    assert len(result.check_results) == 15
    assert all(check.submission_version == 1 for check in result.check_results)
    assert result.reviews[-1].repair_tasks == ()
    assert sum(e.event_type == "team.completed" for e in result.events) == 1


@pytest.mark.asyncio
async def test_premature_team_recovery_is_bounded_and_does_not_fabricate_review() -> None:
    runtime = PrematureReviewExitRuntime(succeed=False)
    with pytest.raises(AgentExecutionError, match="Reviewer did not submit"):
        await _run_premature_runtime(runtime)
    assert len(runtime.calls) == 3  # Original + at most two recovery attempts.


@pytest.mark.asyncio
async def test_exhausted_budget_is_not_reset_or_retried_after_native_exit() -> None:
    runtime = PrematureReviewExitRuntime(exhaust=True)
    with pytest.raises(AgentExecutionError, match="budget exhausted"):
        await _run_premature_runtime(runtime)
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_recovery_preserves_repair_until_specialist_and_reviewer_resubmit() -> None:
    class RepairTailRuntime(PrematureReviewExitRuntime):
        async def stream(
            self, spec: Any, inputs: dict[str, object], *, session_id: str
        ) -> AsyncIterator[TeamRuntimeEvent]:
            async for event in super().stream(spec, inputs, session_id=session_id):
                key = str(spec.model_pool[0].metadata["client"]["runtime_key"])
                state = get_investigation_team_state(key)
                target = state.snapshot
                issue = ReviewIssue(
                    issue_id="repair-time",
                    issue_type="time",
                    message="Fix citation time",
                    check_ids=("registration-status-normal",),
                    target_agent="corporate-agent",
                )
                repair = RepairTask(
                    repair_id="repair-1",
                    issue_ids=(issue.issue_id,),
                    target_agent="corporate-agent",
                    requested_fields=("decision_summary",),
                    required_evidence=("ev-registration",),
                    attempt=1,
                    max_attempts=1,
                )
                if len(self.calls) == 2:
                    assert state.submission_board.latest_review is not None
                    assert state.submission_board.latest_review.repair_tasks == (repair,)
                    await state.submission_board.submit_check_result(
                        agent_id="corporate-agent",
                        result=check_result("registration-status-normal").model_copy(
                            update={"submission_version": 2, "decision_summary": "修正时点引用"}
                        ),
                    )
                await state.submission_board.submit_review(
                    reviewer_agent_id="reviewer-agent",
                    review=ReviewSubmission(
                        snapshot_id=target.snapshot_id,
                        snapshot_sha256=target.snapshot_sha256,
                        subject_id=target.subject.subject_id,
                        check_catalog_version=CHECK_CATALOG.catalog_version,
                        prompt_version="investigation-core-v1+reviewer-v1",
                        review_version=len(self.calls),
                        issues=(issue,) if len(self.calls) == 1 else (),
                        repair_tasks=(repair,) if len(self.calls) == 1 else (),
                    ),
                )
                yield event

    runtime = RepairTailRuntime(succeed=False)
    result = await _run_premature_runtime(runtime)
    assert len(runtime.calls) == 2
    assert [r.review_version for r in result.reviews] == [1, 2]
    assert result.reviews[0].repair_tasks
    assert result.reviews[-1].repair_tasks == ()
    assert (
        next(
            c for c in result.check_results if c.check_id == "registration-status-normal"
        ).submission_version
        == 2
    )


@pytest.mark.asyncio
async def test_business_completion_closes_team_without_waiting_on_residual_coordination_tasks() -> (
    None
):
    runtime = BusinessCompleteWithResidualTasksRuntime()

    completed = await AgentTeamsInvestigatorTeam(
        prompt_bundle=load_prompt_bundle(),
        runtime=runtime,  # type: ignore[arg-type]
    ).run(
        snapshot=snapshot(),
        budget_ledger=_ledger(),
        run_id="run-multi-formal",
        model_name="agent-teams-scripted",
        model_provider=SCRIPTED_PROVIDER,
        model_api_key="test-key",
        model_base_url="https://model.invalid/v1",
        timeout_seconds=1,
    )

    assert len(completed.check_results) == len(CHECK_CATALOG.check_ids)
    assert completed.reviews[-1].repair_tasks == ()
    assert runtime.wait_called is False
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_real_agent_teams_executes_assignment_review_and_bounded_repair(
    tmp_path: Any,
) -> None:
    ScriptedAgentTeamsModelClient.seen_tools_by_member.clear()
    configure_openjiuwen_home(tmp_path / "openjiuwen")
    budget = _ledger()
    await Runner.start()
    try:
        completed = await AgentTeamsInvestigatorTeam(prompt_bundle=load_prompt_bundle()).run(
            snapshot=snapshot(),
            budget_ledger=budget,
            run_id="run-multi-formal",
            model_name="agent-teams-scripted",
            model_provider=SCRIPTED_PROVIDER,
            model_api_key="test-key",
            model_base_url="https://model.invalid/v1",
            timeout_seconds=30,
        )
        assert _pending_scheduler_tasks("run-multi-formal:multi-agent-teams") == []
    finally:
        await Runner.stop()
        reset_openjiuwen_home()

    assert completed.assignments.assignments == plan().assignments
    assert {result.check_id for result in completed.check_results} == set(CHECK_CATALOG.check_ids)
    registration = next(
        item for item in completed.check_results if item.check_id == "registration-status-normal"
    )
    assert registration.submission_version == 2
    assert tuple(item.review_version for item in completed.reviews) == (1, 2)
    assert completed.reviews[-1].repair_tasks == ()
    assert budget.snapshot().repair_rounds == 1
    assert budget.snapshot().provider_usage_requests > 0
    assert len({item.agent_id for item in completed.agent_results}) == 6
    assert any(item.event_type == "team.completed" for item in completed.events)

    expected_business_tools = {
        "leader": {"submit_check_assignments", "read_investigation_progress"},
        "corporate-agent": {
            "read_assigned_snapshot_context",
            "submit_check_result",
            "member_complete_task",
        },
        "judicial-compliance-agent": {
            "read_assigned_snapshot_context",
            "submit_check_result",
            "member_complete_task",
        },
        "financial-operations-agent": {
            "read_assigned_snapshot_context",
            "submit_check_result",
            "member_complete_task",
        },
        "related-peer-agent": {
            "read_assigned_snapshot_context",
            "submit_check_result",
            "member_complete_task",
        },
        "reviewer-agent": {
            "read_check_submissions",
            "submit_review",
            "member_complete_task",
        },
    }
    forbidden_tools = {
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "list_files",
        "execute_code",
        "skill_tool",
    }
    assert set(ScriptedAgentTeamsModelClient.seen_tools_by_member) == set(expected_business_tools)
    for member_name, required in expected_business_tools.items():
        observed = ScriptedAgentTeamsModelClient.seen_tools_by_member[member_name]
        assert required <= observed, (member_name, observed)
        assert observed.isdisjoint(forbidden_tools), (member_name, observed)
