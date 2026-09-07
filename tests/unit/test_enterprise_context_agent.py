from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner

from jindiao.agents import EnterpriseContextAgent
from jindiao.agents.specialists import (
    GovernanceAgent,
    JudicialComplianceAgent,
    OperationsPeerAgent,
)
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import SubmoduleAvailability
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.results import AgentResultPhase
from jindiao.orchestration import AgentExecutionEventType, OpenJiuwenAgentExecutionRuntime
from jindiao.orchestration.react_model import JINDIAO_OPENAI_COMPATIBLE_PROVIDER
from jindiao.prompts import load_prompt_bundle
from jindiao.reporting.catalog import REPORT_CATALOG
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    GatewayBudget,
    McpCallResult,
    TianyanchaMcpGateway,
)

NOW = datetime(2026, 9, 5, 9, 30, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


class ContextSampleClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        self.calls.append((name, arguments))
        if name == "search_companies":
            return McpCallResult(
                structured_content={
                    "items": [
                        {
                            "id": "sample-1",
                            "name": "公平对比样本有限公司",
                            "creditCode": "91110000FAIR000001",
                            "regStatus": "存续",
                        }
                    ]
                }
            )
        if name == "get_company_capabilities":
            return McpCallResult(
                structured_content={"tools": [{"tool_name": "get_company_registration_info"}]}
            )
        if name == "call_tool":
            assert arguments["tool_name"] == "get_company_registration_info"
            return McpCallResult(
                structured_content={"items": [{"id": "reg-1", "regStatus": "存续"}]}
            )
        raise AssertionError(f"unexpected MCP tool: {name}")


class ScriptedContextModel:
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


def model_message(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        tool_calls=tool_calls,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="context-scripted-model",
            input_tokens=12,
            output_tokens=4,
            total_tokens=16,
        ),
    )


def _gateway(client: ContextSampleClient) -> TianyanchaMcpGateway:
    return TianyanchaMcpGateway(
        client=client,
        routing=CapabilityRoutingConfig.from_file(Path("config/tianyancha-capability-routes.json")),
        run_id="run-context-sample",
        agent_id=EnterpriseContextAgent.agent_id,
        report_as_of=REPORT_AS_OF,
        budget=GatewayBudget(max_mcp_calls=60, max_concurrency=4, max_pages=3),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_one_context_agent_owns_subject_capability_and_all_48_coverage() -> None:
    client = ContextSampleClient()
    gateway = _gateway(client)
    agent = EnterpriseContextAgent(
        gateway=gateway,
        prompt_bundle=load_prompt_bundle(),
        report_catalog=REPORT_CATALOG,
    )

    result = await agent.acquire(EnterpriseInput(company_name="公平对比样本有限公司"))

    assert result.subject.subject_id == "tyc:sample-1"
    assert tuple(item.submodule_id for item in result.submodules) == (REPORT_CATALOG.submodule_ids)
    assert len(result.submodules) == 48
    assert result.agent_result.agent_id == EnterpriseContextAgent.agent_id
    assert result.agent_result.phase is AgentResultPhase.ACQUISITION
    assert result.agent_result.check_results == ()
    assert result.agent_result.risk_items == ()
    assert {item.agent_id for item in gateway.invocations} == {EnterpriseContextAgent.agent_id}
    assert [name for name, _ in client.calls[:2]] == [
        "search_companies",
        "get_company_capabilities",
    ]


@pytest.mark.asyncio
async def test_context_agent_deduplicates_a_shared_capability_across_coverage_tasks() -> None:
    client = ContextSampleClient()
    gateway = _gateway(client)
    agent = EnterpriseContextAgent(
        gateway=gateway,
        prompt_bundle=load_prompt_bundle(),
        report_catalog=REPORT_CATALOG,
    )

    first, second = await asyncio.gather(
        agent.acquire(EnterpriseInput(company_name="公平对比样本有限公司")),
        agent.acquire(EnterpriseInput(company_name="公平对比样本有限公司")),
    )

    assert first == second
    business_calls = [call for call in client.calls if call[0] == "call_tool"]
    assert len(business_calls) == 1


@pytest.mark.asyncio
async def test_context_agent_react_loop_collects_and_explicitly_submits_coverage() -> None:
    client = ContextSampleClient()
    agent = EnterpriseContextAgent(
        gateway=_gateway(client),
        prompt_bundle=load_prompt_bundle(),
        report_catalog=REPORT_CATALOG,
    )
    model = ScriptedContextModel(
        [
            model_message(
                tool_calls=[
                    ToolCall(
                        id="collect-context",
                        type="function",
                        name="collect_enterprise_context",
                        arguments=json.dumps(
                            {"submodule_ids": list(REPORT_CATALOG.submodule_ids)},
                            ensure_ascii=False,
                        ),
                    )
                ]
            ),
            model_message(
                tool_calls=[
                    ToolCall(
                        id="submit-context",
                        type="function",
                        name="submit_enterprise_context",
                        arguments="{}",
                    )
                ]
            ),
            model_message("context acquisition submitted"),
        ]
    )

    await Runner.start()
    try:
        completed = await agent.run(
            EnterpriseInput(company_name="公平对比样本有限公司"),
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            model_name="context-scripted-model",
            model_provider="scripted",
            model=cast(Any, model),
            timeout_seconds=10,
        )
    finally:
        await Runner.stop()

    assert completed.acquisition.agent_result.prompt_version == "enterprise-context-v2"
    assert len(completed.acquisition.submodules) == 48
    assert model.calls == 3
    assert any(
        event.event_type is AgentExecutionEventType.MODEL_REQUEST_COMPLETED
        for event in completed.events
    )
    assert all("system_prompt" not in str(event.payload) for event in completed.events)


@pytest.mark.asyncio
async def test_context_agent_reports_safe_state_when_model_omits_submission() -> None:
    agent = EnterpriseContextAgent(
        gateway=_gateway(ContextSampleClient()),
        prompt_bundle=load_prompt_bundle(),
        report_catalog=REPORT_CATALOG,
    )
    model = ScriptedContextModel(
        [
            model_message(
                tool_calls=[
                    ToolCall(
                        id="collect-context",
                        type="function",
                        name="collect_enterprise_context",
                        arguments=json.dumps(
                            {"submodule_ids": list(REPORT_CATALOG.submodule_ids)},
                            ensure_ascii=False,
                        ),
                    )
                ]
            ),
            model_message("ended without submitting"),
        ]
    )

    await Runner.start()
    try:
        with pytest.raises(AgentExecutionError) as raised:
            await agent.run(
                EnterpriseInput(company_name="公平对比样本有限公司"),
                runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
                model_name="context-scripted-model",
                model_provider="scripted",
                model=cast(Any, model),
                timeout_seconds=10,
            )
    finally:
        await Runner.stop()

    assert raised.value.details["acquisition_collected"] is True
    assert raised.value.details["submitted"] is False
    assert raised.value.details["collection_attempts"] == 1
    assert raised.value.details["collection_error_type"] is None
    assert raised.value.details["event_type_counts"]["agent.completed"] == 1
    assert "prompt" not in str(raised.value.details).casefold()


@pytest.mark.asyncio
async def test_context_agent_marks_unrequested_catalog_items_when_budget_is_exhausted() -> None:
    client = ContextSampleClient()
    constrained_gateway = TianyanchaMcpGateway(
        client=client,
        routing=CapabilityRoutingConfig.from_file(Path("config/tianyancha-capability-routes.json")),
        run_id="run-context-budget",
        agent_id=EnterpriseContextAgent.agent_id,
        report_as_of=REPORT_AS_OF,
        budget=GatewayBudget(max_mcp_calls=2, max_concurrency=4, max_pages=1),
        clock=lambda: NOW,
    )
    agent = EnterpriseContextAgent(
        gateway=constrained_gateway,
        prompt_bundle=load_prompt_bundle(),
        report_catalog=REPORT_CATALOG,
    )

    result = await agent.acquire(EnterpriseInput(company_name="公平对比样本有限公司"))

    registration = next(item for item in result.submodules if item.submodule_id == "registration")
    assert registration.availability is SubmoduleAvailability.NOT_REQUESTED
    assert registration.unresolved_gap_ids == ("gap:registration:not_requested",)


def test_context_builder_is_the_only_agent_surface_with_tianyancha_gateway_access() -> None:
    agent = EnterpriseContextAgent(
        gateway=_gateway(ContextSampleClient()),
        prompt_bundle=load_prompt_bundle(),
        report_catalog=REPORT_CATALOG,
    )
    react_agent, _ = agent.build_react_agent(
        EnterpriseInput(company_name="公平对比样本有限公司"),
        model_name="qwen-plus",
        model_provider="OpenAI",
        model_api_key="test-only",
        model_base_url="https://model.example/v1",
        model_temperature=0.25,
        model_timeout_seconds=42,
    )
    try:
        assert {item.name for item in react_agent.ability_manager.list()} == {
            "collect_enterprise_context",
            "submit_enterprise_context",
        }
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

    forbidden = {"search_companies", "get_company_capabilities"}
    for specialist in (GovernanceAgent, JudicialComplianceAgent, OperationsPeerAgent):
        assert specialist.tool_whitelist.isdisjoint(forbidden)
