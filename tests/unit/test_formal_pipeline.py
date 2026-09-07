from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast

import pytest
from pydantic import SecretStr
from test_multi_investigator_team import snapshot as base_snapshot
from test_single_agent_strategy import context as base_context

from jindiao.acquisition import ContextFreezer
from jindiao.agents import (
    AgentTeamsInvestigatorTeam,
    DeepSearchAgent,
    EnterpriseContextAcquisitionResult,
    EnterpriseContextAgent,
    SingleInvestigatorAgent,
)
from jindiao.application.formal_pipeline import FormalDueDiligencePipeline
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.execution import ExecutionCost, RunTermination, RunTerminationReason
from jindiao.contracts.investigation import CheckResult, CheckStatus, FactEvidenceRef
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
    DueDiligenceRequest,
    DueDiligenceResult,
    OrchestrationMode,
)
from jindiao.deepsearch import SupplementPolicy
from jindiao.investigation import CHECK_CATALOG
from jindiao.investigation.blackboard import ReviewSubmission
from jindiao.orchestration import BudgetLedger, RunBudget
from jindiao.orchestration.agent_runtime import AgentExecutionRuntime
from jindiao.scenarios import ScenarioRepository
from jindiao.tianyancha import TianyanchaMcpGateway

NOW = datetime(2026, 9, 5, 22, 0, tzinfo=UTC)


def pipeline_with_test_doubles(
    *,
    context_agent: object,
    supplement_policy: object,
    deepsearch_agent: object | None,
    context_freezer: object,
    single_investigator: object,
    multi_investigator: object,
    agent_runtime: object,
    gateway: object,
    model_name: str,
    model_provider: str,
    model_api_key: str,
    model_base_url: str,
    clock: Callable[[], datetime] = lambda: NOW,
) -> FormalDueDiligencePipeline:
    """Adapt behaviorally complete test doubles to the concrete production ports."""

    return FormalDueDiligencePipeline(
        context_agent=cast(EnterpriseContextAgent, context_agent),
        supplement_policy=cast(SupplementPolicy, supplement_policy),
        deepsearch_agent=cast(DeepSearchAgent | None, deepsearch_agent),
        context_freezer=cast(ContextFreezer, context_freezer),
        single_investigator=cast(SingleInvestigatorAgent, single_investigator),
        multi_investigator=cast(AgentTeamsInvestigatorTeam, multi_investigator),
        agent_runtime=cast(AgentExecutionRuntime, agent_runtime),
        gateway=cast(TianyanchaMcpGateway, gateway),
        model_name=model_name,
        model_provider=model_provider,
        model_api_key=model_api_key,
        model_base_url=model_base_url,
        clock=clock,
    )


def acquisition() -> EnterpriseContextAcquisitionResult:
    frozen = base_snapshot()
    contribution = AgentInvestigationResult(
        agent_id="enterprise-context-agent",
        role="enterprise-context",
        phase=AgentResultPhase.ACQUISITION,
        status=AgentStatus.COMPLETED,
        task_ids=tuple(f"acquire:{item.submodule_id}" for item in frozen.submodules),
        fact_evidence_refs=(
            FactEvidenceRef(
                evidence_id="ev-registration",
                fact_path="submodules.registration",
                summary="工商登记事实",
            ),
        ),
        prompt_version="enterprise-context-v1",
    )
    return EnterpriseContextAcquisitionResult(
        subject=frozen.subject,
        report_as_of=frozen.report_as_of,
        report_catalog_version=frozen.report_catalog_version,
        source_manifest_version="d" * 64,
        capability_names=("get_company_registration_info",),
        submodules=frozen.submodules,
        evidence=frozen.evidence,
        invocations=(),
        agent_result=contribution,
        prompt_sha256="e" * 64,
    )


class RecordingContextAgent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def run(self, enterprise: EnterpriseInput, **_: object) -> object:
        assert enterprise.company_name
        self.calls.append("acquisition")
        return SimpleNamespace(acquisition=acquisition(), events=())


class BlockingContextAgent:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self, enterprise: EnterpriseInput, **_: object) -> object:
        assert enterprise.company_name
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("blocking context agent unexpectedly resumed")


class RecordingPolicy:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def plan(self, **_: object) -> tuple[object, ...]:
        self.calls.append("supplement-policy")
        return ()


class RecordingFreezer:
    def __init__(self, calls: list[str]) -> None:
        from jindiao.acquisition import ContextFreezer

        self.calls = calls
        self.delegate = ContextFreezer(clock=lambda: NOW)

    def freeze(self, *args: object, **kwargs: object) -> object:
        self.calls.append("freeze")
        return self.delegate.freeze(*args, **kwargs)  # type: ignore[arg-type]


class RecordingSingleInvestigator:
    def __init__(
        self,
        calls: list[str],
        *,
        cost: ExecutionCost | None = None,
    ) -> None:
        self.calls = calls
        self.cost = cost or ExecutionCost.zero()
        self.budget_ledgers: list[BudgetLedger] = []

    async def run(
        self,
        *,
        snapshot: object,
        budget_ledger: BudgetLedger,
        **_: object,
    ) -> object:
        self.calls.append("single-investigation")
        self.budget_ledgers.append(budget_ledger)
        checks = tuple(
            self._check(snapshot, definition.check_id) for definition in CHECK_CATALOG.checks
        )
        result = AgentInvestigationResult(
            agent_id="single-investigator",
            role="single-investigator",
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=tuple(item.task_id for item in checks),
            check_results=checks,
            fact_evidence_refs=tuple(fact for item in checks for fact in item.fact_evidence_refs),
            prompt_version="investigation-core-v1+single-investigator-v1",
        )
        return SimpleNamespace(
            agent_result=result,
            events=(),
            investigation_cost=self.cost,
            termination=RunTermination(
                reason=RunTerminationReason.COMPLETED,
                completed_task_ids=result.task_ids,
            ),
        )

    @staticmethod
    def _check(snapshot: object, check_id: str) -> CheckResult:
        definition = CHECK_CATALOG.get(check_id)
        is_registration = check_id == "registration-status-normal"
        return CheckResult(
            snapshot_id=snapshot.snapshot_id,  # type: ignore[attr-defined]
            snapshot_sha256=snapshot.snapshot_sha256,  # type: ignore[attr-defined]
            subject_id=snapshot.subject.subject_id,  # type: ignore[attr-defined]
            check_catalog_version=CHECK_CATALOG.catalog_version,
            output_schema_version=definition.output_schema_version,
            task_id=f"check:{check_id}",
            check_id=check_id,
            status=CheckStatus.NO_RISK if is_registration else CheckStatus.INCONCLUSIVE,
            decision_summary=("工商登记状态正常。" if is_registration else "必需快照证据不足。"),
            fact_evidence_refs=(
                (
                    FactEvidenceRef(
                        evidence_id="ev-registration",
                        fact_path="governance.registration.registration_status",
                        summary="工商状态为存续",
                    ),
                )
                if is_registration
                else ()
            ),
            missing_evidence=() if is_registration else definition.required_submodule_ids,
            confidence=0.95 if is_registration else 0.1,
            prompt_version="fixed-check-v1",
            submission_version=1,
        )


class ForbiddenMultiInvestigator:
    async def run(self, **_: object) -> object:
        raise AssertionError("multi investigator must not run in single mode")


class ForbiddenSingleInvestigator:
    async def run(self, **_: object) -> object:
        raise AssertionError("single investigator must not run in multi mode")


class RecordingMultiInvestigator:
    ROLE_AGENTS: ClassVar[dict[str, str]] = {
        "corporate": "corporate-agent",
        "judicial-compliance": "judicial-compliance-agent",
        "financial-operations": "financial-operations-agent",
        "related-peer": "related-peer-agent",
    }

    def __init__(
        self,
        calls: list[str],
        *,
        cost: ExecutionCost | None = None,
    ) -> None:
        self.calls = calls
        self.cost = cost or ExecutionCost.zero()
        self.budget_ledgers: list[BudgetLedger] = []

    async def run(
        self,
        *,
        snapshot: object,
        budget_ledger: BudgetLedger,
        **_: object,
    ) -> object:
        self.calls.append("multi-investigation")
        self.budget_ledgers.append(budget_ledger)
        specialist_results = []
        for role, agent_id in self.ROLE_AGENTS.items():
            checks = tuple(
                RecordingSingleInvestigator._check(snapshot, definition.check_id)
                for definition in CHECK_CATALOG.checks
                if definition.owner_role == role
            )
            specialist_results.append(
                AgentInvestigationResult(
                    agent_id=agent_id,
                    role=role,
                    phase=AgentResultPhase.INVESTIGATION,
                    status=AgentStatus.COMPLETED,
                    task_ids=tuple(item.task_id for item in checks),
                    check_results=checks,
                    fact_evidence_refs=tuple(
                        fact for item in checks for fact in item.fact_evidence_refs
                    ),
                    prompt_version=f"investigation-core-v1+{role}-v1",
                )
            )
        review = ReviewSubmission(
            snapshot_id=snapshot.snapshot_id,  # type: ignore[attr-defined]
            snapshot_sha256=snapshot.snapshot_sha256,  # type: ignore[attr-defined]
            subject_id=snapshot.subject.subject_id,  # type: ignore[attr-defined]
            check_catalog_version=CHECK_CATALOG.catalog_version,
            prompt_version="investigation-core-v1+reviewer-v1",
            review_version=1,
        )
        return SimpleNamespace(
            agent_results=tuple(specialist_results),
            events=(),
            reviews=(review,),
            investigation_cost=self.cost,
            termination=RunTermination(
                reason=RunTerminationReason.COMPLETED,
                completed_task_ids=tuple(
                    task_id for result in specialist_results for task_id in result.task_ids
                ),
            ),
        )


class FakeGateway:
    mcp_calls = 3

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_formal_pipeline_acquires_freezes_then_runs_only_selected_investigator() -> None:
    calls: list[str] = []
    gateway = FakeGateway()
    context = replace(
        base_context(),
        requested_enterprise=EnterpriseInput(company_name="Multi Agent 测试有限公司"),
    )
    pipeline = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent(calls),
        supplement_policy=RecordingPolicy(calls),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer(calls),
        single_investigator=RecordingSingleInvestigator(calls),
        multi_investigator=ForbiddenMultiInvestigator(),
        agent_runtime=SimpleNamespace(),
        gateway=gateway,
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
        clock=lambda: NOW,
    )

    completed = await pipeline.execute(
        context,
        mode=OrchestrationMode.SINGLE,
        budget=RunBudget.from_policy(context.policy),
    )

    assert calls == [
        "acquisition",
        "supplement-policy",
        "freeze",
        "single-investigation",
    ]
    assert completed.snapshot.subject.subject_id == "tyc:multi-1"
    assert completed.outcome.subject == completed.snapshot.subject
    assert completed.outcome.review_completed is True
    assert completed.agent_results[0].agent_id == "single-investigator"
    assert completed.investigation_cost == ExecutionCost.zero()
    assert completed.snapshot.shared_acquisition_cost.mcp_calls == 3
    assert gateway.closed is True


@pytest.mark.asyncio
async def test_service_formal_mode_uses_snapshot_pipeline_not_legacy_strategy(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    gateway = FakeGateway()
    pipeline = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent(calls),
        supplement_policy=RecordingPolicy(calls),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer(calls),
        single_investigator=RecordingSingleInvestigator(calls),
        multi_investigator=ForbiddenMultiInvestigator(),
        agent_runtime=SimpleNamespace(),
        gateway=gateway,
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
        clock=lambda: NOW,
    )
    settings = Settings(
        agent_runtime_mode="formal",
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key=SecretStr("test-only"),
        model_base_url="http://model.test/v1",
        artifact_root=tmp_path,
        max_tool_calls=100,
    )
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        clock=lambda: NOW,
        formal_pipeline_factory=lambda _: pipeline,
        toolset_factory=lambda: (_ for _ in ()).throw(
            AssertionError("legacy toolset must not run in formal mode")
        ),
    )

    result = await service.run(
        request=DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
            scenario_id="normal-enterprise",
        ),
        mode=OrchestrationMode.SINGLE,
        request_id="req-formal-service",
        run_id="run-formal-service",
    )

    assert calls[-1] == "single-investigation"
    assert result.context_snapshot is not None
    assert result.context_snapshot.snapshot_id.startswith("snapshot:")
    assert result.comparison_metadata is not None
    assert result.comparison_metadata.formal_agent_run is True
    assert result.comparison_metadata.topology is OrchestrationMode.SINGLE
    assert [item.agent_id for item in result.agent_results] == [
        "enterprise-context-agent",
        "single-investigator",
    ]


@pytest.mark.asyncio
async def test_formal_pipeline_multi_mode_selects_team_and_shares_one_ledger() -> None:
    calls: list[str] = []
    gateway = FakeGateway()
    context = replace(
        base_context(),
        requested_enterprise=EnterpriseInput(company_name="Multi Agent 测试有限公司"),
    )
    pipeline = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent(calls),
        supplement_policy=RecordingPolicy(calls),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer(calls),
        single_investigator=ForbiddenSingleInvestigator(),
        multi_investigator=RecordingMultiInvestigator(calls),
        agent_runtime=SimpleNamespace(),
        gateway=gateway,
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
        clock=lambda: NOW,
    )

    completed = await pipeline.execute(
        context,
        mode=OrchestrationMode.MULTI,
        budget=RunBudget.from_policy(context.policy),
    )

    assert calls[-1] == "multi-investigation"
    assert completed.outcome.collaboration.agent_count == 4
    assert completed.outcome.collaboration.parallel_task_count == 4
    assert completed.comparison_metadata.topology is OrchestrationMode.MULTI
    assert set(completed.comparison_metadata.role_prompt_versions) == {
        "leader",
        "corporate",
        "judicial-compliance",
        "financial-operations",
        "related-peer",
        "reviewer",
    }
    assert gateway.closed is True


@pytest.mark.asyncio
async def test_formal_service_stream_orders_and_correlates_pipeline_events(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    ids = iter(("req-formal-stream", "run-formal-stream"))

    def pipeline_factory(_: object) -> FormalDueDiligencePipeline:
        return pipeline_with_test_doubles(
            context_agent=RecordingContextAgent(calls),
            supplement_policy=RecordingPolicy(calls),
            deepsearch_agent=None,
            context_freezer=RecordingFreezer(calls),
            single_investigator=RecordingSingleInvestigator(calls),
            multi_investigator=ForbiddenMultiInvestigator(),
            agent_runtime=SimpleNamespace(),
            gateway=FakeGateway(),
            model_name="scripted-model",
            model_provider="scripted",
            model_api_key="test-only",
            model_base_url="http://model.test/v1",
            clock=lambda: NOW,
        )

    settings = Settings(
        agent_runtime_mode="formal",
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key=SecretStr("test-only"),
        model_base_url="http://model.test/v1",
        artifact_root=tmp_path,
        max_tool_calls=100,
    )
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
        formal_pipeline_factory=pipeline_factory,
    )
    request = DueDiligenceRequest(
        enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
        scenario_id="normal-enterprise",
    )

    events = [event async for event in service.stream(request, mode=OrchestrationMode.SINGLE)]

    event_types = [event.event_type.value for event in events]
    assert event_types[:4] == [
        "run.accepted",
        "acquisition.started",
        "acquisition.completed",
        "snapshot.frozen",
    ]
    assert event_types[-1] == "report.completed"
    assert event_types.count("section.completed") == 8
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert {event.request_id for event in events} == {"req-formal-stream"}
    assert {event.run_id for event in events} == {"run-formal-stream"}
    final_result = DueDiligenceResult.model_validate(events[-1].payload["result"])
    assert final_result.context_snapshot is not None
    assert final_result.comparison_metadata is not None
    assert final_result.comparison_metadata.formal_agent_run is True
    assert "test-only" not in events[-1].model_dump_json()


@pytest.mark.asyncio
async def test_closing_formal_service_stream_cancels_pipeline_and_closes_gateway(
    tmp_path: Path,
) -> None:
    context_agent = BlockingContextAgent()
    gateway = FakeGateway()
    ids = iter(("req-cancel", "run-cancel"))
    pipeline = pipeline_with_test_doubles(
        context_agent=context_agent,
        supplement_policy=RecordingPolicy([]),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer([]),
        single_investigator=RecordingSingleInvestigator([]),
        multi_investigator=ForbiddenMultiInvestigator(),
        agent_runtime=SimpleNamespace(),
        gateway=gateway,
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
        clock=lambda: NOW,
    )
    service = DueDiligenceService(
        settings=Settings(
            agent_runtime_mode="formal",
            model_name="scripted-model",
            model_provider="scripted",
            model_api_key=SecretStr("test-only"),
            model_base_url="http://model.test/v1",
            artifact_root=tmp_path,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
        formal_pipeline_factory=lambda _: pipeline,
    )
    stream = service.stream(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
            scenario_id="normal-enterprise",
        ),
        mode=OrchestrationMode.SINGLE,
    )

    assert (await anext(stream)).event_type.value == "run.accepted"
    assert (await anext(stream)).event_type.value == "acquisition.started"
    await asyncio.wait_for(context_agent.started.wait(), timeout=1)

    waiting_started = asyncio.Event()

    async def wait_for_next_event() -> object:
        waiting_started.set()
        return await anext(stream)

    waiting = asyncio.create_task(wait_for_next_event())
    await waiting_started.wait()
    waiting.cancel()
    with suppress(asyncio.CancelledError):
        await waiting

    assert context_agent.cancelled is True
    assert gateway.closed is True
