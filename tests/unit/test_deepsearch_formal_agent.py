from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner

from jindiao.agents import DeepSearchAgent
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import SupplementTask, SupplementTaskReason
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import SourceStatus, SourceType
from jindiao.orchestration import OpenJiuwenAgentExecutionRuntime
from jindiao.orchestration.react_model import JINDIAO_OPENAI_COMPATIBLE_PROVIDER
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.prompts import load_prompt_bundle

NOW = datetime(2026, 9, 5, 13, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:123",
        company_name="年报正式 Agent 测试有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


def task() -> SupplementTask:
    return SupplementTask(
        task_id="supplement:annual_reports:formal",
        reason=SupplementTaskReason.BASELINE_ENRICHMENT,
        target_submodule_id="annual_reports",
        subject_id=subject().subject_id,
        report_as_of=REPORT_AS_OF,
        allowed_tools=("tianyancha_annual_report_social_security",),
        allowed_sources=(SourceType.PUBLIC_WEB,),
        requested_fields=("operations.annual_reports.social_security",),
        max_tool_calls=1,
    )


class FakeAnnualReportTool:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def invoke(self, values: dict[str, object]) -> dict[str, object]:
        self.calls.append(values)
        return {
            "source_status": "verified_empty",
            "report": None,
            "evidence": [],
            "checked_years": [2025, 2024],
            "error": None,
        }


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
            model_name="deepsearch-scripted-model",
            input_tokens=9,
            output_tokens=3,
            total_tokens=12,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("string_subject", [False, True], ids=["object", "json-string"])
async def test_formal_deepsearch_agent_calls_member_tool_and_submits_typed_outcome(
    string_subject: bool,
) -> None:
    annual_tool = FakeAnnualReportTool()
    model = ScriptedModel(
        [
            message(
                tool_calls=[
                    ToolCall(
                        id="annual-report",
                        type="function",
                        name="tianyancha_annual_report_social_security",
                        arguments=json.dumps(
                            {
                                "subject": (
                                    subject().model_dump_json()
                                    if string_subject
                                    else subject().model_dump(mode="json")
                                ),
                                "queried_at": NOW.isoformat(),
                                "report_as_of": REPORT_AS_OF.isoformat(),
                            },
                            ensure_ascii=False,
                        ),
                    )
                ]
            ),
            message(
                tool_calls=[
                    ToolCall(
                        id="submit-supplement",
                        type="function",
                        name="submit_supplement_outcome",
                        arguments=json.dumps({"task_id": task().task_id}),
                    )
                ]
            ),
            message("supplement submitted"),
        ]
    )
    agent = DeepSearchAgent(
        prompt_bundle=load_prompt_bundle(),
        annual_report_tool=annual_tool,
    )

    await Runner.start()
    try:
        completed = await agent.run(
            tasks=(task(),),
            subject=subject(),
            queried_at=NOW,
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            run_id="run-deepsearch-formal",
            model_name="deepsearch-scripted-model",
            model_provider="scripted",
            model=cast(Any, model),
            timeout_seconds=10,
        )
    finally:
        await Runner.stop()

    assert agent.skill_ids == ("tianyancha-annual-report-social-security",)
    assert len(annual_tool.calls) == 1
    assert annual_tool.calls[0]["subject"] == subject().model_dump(mode="json")
    assert completed.outcomes[0].source_status is SourceStatus.VERIFIED_EMPTY
    assert completed.outcomes[0].scope == {"checked_years": [2025, 2024]}
    assert completed.agent_result.risk_items == ()
    assert completed.agent_result.prompt_version == "deepsearch-acquisition-v1"
    assert model.calls == 3


def test_formal_deepsearch_agent_rejects_unconfigured_gap_tool() -> None:
    gap = task().model_copy(
        update={
            "task_id": "supplement:financial_summary:gap",
            "reason": SupplementTaskReason.EVIDENCE_GAP,
            "target_submodule_id": "financial_summary",
            "allowed_tools": ("bounded_web_search",),
            "requested_fields": ("operations.financial_summary",),
            "max_tool_calls": 2,
            "gap_type": "capability_absent",
            "trigger_status": SourceStatus.CAPABILITY_ABSENT,
        }
    )
    agent = DeepSearchAgent(
        prompt_bundle=load_prompt_bundle(),
        annual_report_tool=FakeAnnualReportTool(),
    )

    with pytest.raises(AgentExecutionError, match="bounded_web_search"):
        agent.build_react_agent(
            tasks=(gap,),
            subject=subject(),
            queried_at=NOW,
            run_id="run-deepsearch-gap",
            model_name="not-invoked",
            model_provider="scripted",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject_value",
    [
        "not-json",
        "null",
        "[]",
        "42",
        json.dumps(subject().model_dump_json()),
        json.dumps({}),
        json.dumps({**subject().model_dump(mode="json"), "unexpected": True}),
        json.dumps({**subject().model_dump(mode="json"), "subject_id": "tyc:other"}),
        json.dumps({**subject().model_dump(mode="json"), "company_name": "另一家公司"}),
    ],
    ids=["malformed", "null", "array", "number", "double-encoded", "missing-fields",
         "unknown-field", "wrong-subject", "wrong-company"],
)
async def test_invalid_subject_cannot_fetch_or_submit_an_outcome(subject_value: str) -> None:
    annual_tool = FakeAnnualReportTool()
    model = ScriptedModel([
        message(tool_calls=[ToolCall(
            id="invalid-annual", type="function",
            name="tianyancha_annual_report_social_security",
            arguments=json.dumps({"subject": subject_value, "queried_at": NOW.isoformat(),
                                  "report_as_of": REPORT_AS_OF.isoformat()}),
        )]),
        message(tool_calls=[ToolCall(
            id="unexecuted-submit", type="function", name="submit_supplement_outcome",
            arguments=json.dumps({"task_id": task().task_id}),
        )]),
        message("done"),
    ])
    agent = DeepSearchAgent(prompt_bundle=load_prompt_bundle(), annual_report_tool=annual_tool)
    await Runner.start()
    try:
        with pytest.raises(AgentExecutionError, match="without submitting") as caught:
            await agent.run(
                tasks=(task(),), subject=subject(), queried_at=NOW,
                runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
                run_id="run-invalid-subject", model_name="scripted", model_provider="scripted",
                model=cast(Any, model), timeout_seconds=10,
            )
        assert caught.value.details["missing_task_ids"] == [task().task_id]
        assert annual_tool.calls == []
    finally:
        await Runner.stop()


def test_deepsearch_builder_configures_the_real_model_client() -> None:
    agent = DeepSearchAgent(
        prompt_bundle=load_prompt_bundle(),
        annual_report_tool=FakeAnnualReportTool(),
    )
    react_agent, _ = agent.build_react_agent(
        tasks=(task(),),
        subject=subject(),
        queried_at=NOW,
        run_id="run-deepsearch-builder",
        model_name="qwen-plus",
        model_provider="OpenAI",
        model=cast(Any, ScriptedModel([message("unused")])),
        model_api_key="test-only",
        model_base_url="https://model.example/v1",
        model_temperature=0.25,
        model_timeout_seconds=42,
    )

    try:
        client_config = react_agent._config.model_client_config
        request_config = react_agent._config.model_config_obj
        assert client_config is not None
        assert client_config.client_provider == JINDIAO_OPENAI_COMPATIBLE_PROVIDER
        assert client_config.upstream_provider == "OpenAI"
        assert client_config.api_base == "https://model.example/v1"
        assert client_config.api_key == "test-only"
        assert client_config.timeout == 42
        assert request_config is not None
        assert request_config.model_name == "qwen-plus"
        assert request_config.temperature == 0.25
    finally:
        react_agent.ability_manager.teardown_tools()


def test_investigation_team_cannot_roster_or_inherit_acquisition_deepsearch() -> None:
    spec = build_due_diligence_team_spec(
        team_name="investigation-only",
        model_name="not-invoked",
        max_review_rounds=1,
        tianyancha_annual_report_enabled=True,
    )

    assert "deepsearch-agent" not in {item.member_name for item in spec.predefined_members}
    assert "deepsearch-agent" not in spec.agents
    assert all(not agent.skills and not agent.tools for agent in spec.agents.values())
