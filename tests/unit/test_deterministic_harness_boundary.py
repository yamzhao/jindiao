from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from jindiao.agents.deepsearch_agent import DeepSearchEvidenceAgent
from jindiao.agents.specialists import GovernanceAgent
from jindiao.application import RunContext, Settings
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.orchestration import RunBudget, SingleAgentStrategy
from jindiao.orchestration.base import DomainInvestigation, TeamRuntimeEvent
from jindiao.orchestration.multi import MultiAgentStrategy
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.orchestration.tianyancha_toolset import TianyanchaHybridToolset
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def formal_context() -> RunContext:
    scenario = ScenarioRepository(Path("mock_data/scenarios")).load_template("normal-enterprise")
    settings = Settings(
        agent_runtime_mode="formal",
        model_provider="openai_compatible",
        model_name="test-model",
        model_base_url="https://model.invalid/v1",
        model_api_key=SecretStr("test-only-key"),
    )
    return RunContext.from_settings(
        request_id="req-harness-boundary",
        run_id="run-harness-boundary",
        scenario=scenario,
        settings=settings,
        skill_versions={},
    )


class LegacyToolsetProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve_subject(self, context: RunContext) -> ResolvedSubject:
        self.calls += 1
        company_name = context.scenario.manifest.enterprise_key.company_name
        assert company_name is not None
        return ResolvedSubject(
            subject_id="mock:normal-enterprise",
            company_name=company_name,
            source=SubjectSource.MOCK,
            resolved_at=NOW,
        )

    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation:
        del context, subject, domain
        self.calls += 1
        raise AssertionError("legacy domain investigation must not start")


class LegacyRuntimeProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        del spec, inputs, session_id
        self.calls += 1
        if False:
            yield TeamRuntimeEvent(event_type="unreachable")


@pytest.mark.asyncio
async def test_legacy_single_strategy_rejects_formal_context_before_tool_calls() -> None:
    context = formal_context()
    toolset = LegacyToolsetProbe()

    with pytest.raises(AgentExecutionError, match="deterministic harness only"):
        await SingleAgentStrategy(toolset=toolset).execute(
            context,
            budget=RunBudget.from_policy(context.policy),
        )

    assert toolset.calls == 0


@pytest.mark.asyncio
async def test_legacy_multi_strategy_rejects_formal_context_before_runtime_or_tools() -> None:
    context = formal_context()
    toolset = LegacyToolsetProbe()
    runtime = LegacyRuntimeProbe()

    with pytest.raises(AgentExecutionError, match="deterministic harness only"):
        await MultiAgentStrategy(
            toolset=toolset,
            team_spec=object(),
            runtime=runtime,
        ).execute(
            context,
            budget=RunBudget.from_policy(context.policy),
        )

    assert toolset.calls == 0
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_legacy_specialist_rejects_formal_context_before_domain_investigation() -> None:
    context = formal_context()
    toolset = LegacyToolsetProbe()
    subject = await toolset.resolve_subject(context)
    toolset.calls = 0

    with pytest.raises(AgentExecutionError, match="deterministic harness only"):
        await GovernanceAgent(toolset=toolset).investigate(
            context,
            subject,
            "governance",
        )

    assert toolset.calls == 0


@pytest.mark.asyncio
async def test_legacy_domain_toolsets_reject_formal_context_at_their_entry_points() -> None:
    context = formal_context()

    with pytest.raises(AgentExecutionError, match="deterministic harness only"):
        await ScenarioToolset().resolve_subject(context)

    uninitialized_live_toolset = object.__new__(TianyanchaHybridToolset)
    with pytest.raises(AgentExecutionError, match="deterministic harness only"):
        await uninitialized_live_toolset.resolve_subject(context)


def test_legacy_business_facades_are_machine_labeled_non_formal() -> None:
    assert SingleAgentStrategy.formal_agent_run is False
    assert MultiAgentStrategy.formal_agent_run is False
    assert GovernanceAgent.formal_agent_run is False
    assert DeepSearchEvidenceAgent.formal_agent_run is False
    assert ScenarioToolset.formal_agent_run is False
    assert TianyanchaHybridToolset.formal_agent_run is False
