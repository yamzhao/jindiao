# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest
from pydantic import JsonValue, SecretStr
from test_formal_pipeline import (
    NOW,
    FakeGateway,
    ForbiddenMultiInvestigator,
    RecordingContextAgent,
    RecordingFreezer,
    RecordingPolicy,
    RecordingSingleInvestigator,
    pipeline_with_test_doubles,
)

from jindiao.application.context import RunContext
from jindiao.application.errors import AgentExecutionError, http_status_for_error
from jindiao.application.formal_pipeline import FormalDueDiligencePipeline, FormalPipelineRun
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.execution import ExecutionCost
from jindiao.contracts.results import DueDiligenceRequest, OrchestrationMode
from jindiao.orchestration.base import (
    CancellationToken,
    RunBudget,
    RuntimeEventSink,
    TeamRuntimeEvent,
)
from jindiao.reporting.product_generator import SECTION_IDS
from jindiao.scenarios import ScenarioRepository


def cost(input_tokens: int, output_tokens: int, *, complete: bool = True) -> ExecutionCost:
    return ExecutionCost.model_validate(
        {
            **ExecutionCost.zero().model_dump(),
            "llm_requests": 1 if complete else 2,
            "successful_llm_requests": 1,
            "provider_usage_requests": 1,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "tool_calls": 3,
        }
    )


class ScriptedPipeline:
    """Isolate supplier calls while exercising the real service/artifact boundary."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        events: tuple[TeamRuntimeEvent, ...] = (),
        acquisition_cost: ExecutionCost | None = None,
        investigation_cost: ExecutionCost | None = None,
    ) -> None:
        self.error = error
        self.events = events
        self.acquisition_cost = acquisition_cost or ExecutionCost.zero()
        self.investigation_cost = investigation_cost or ExecutionCost.zero()

    async def execute(
        self,
        context: RunContext,
        *,
        mode: OrchestrationMode,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> FormalPipelineRun:
        if event_sink is not None:
            for event in self.events:
                await event_sink(event)
        if self.error is not None:
            raise self.error
        delegate = pipeline_with_test_doubles(
            context_agent=RecordingContextAgent([]),
            supplement_policy=RecordingPolicy([]),
            deepsearch_agent=None,
            context_freezer=RecordingFreezer([]),
            single_investigator=RecordingSingleInvestigator([], cost=self.investigation_cost),
            multi_investigator=ForbiddenMultiInvestigator(),
            agent_runtime=SimpleNamespace(),
            gateway=FakeGateway(),
            model_name="scripted-model",
            model_provider="scripted",
            model_api_key="test-only",
            model_base_url="http://model.test/v1",
        )
        run = await delegate.execute(
            context, mode=mode, budget=budget, cancellation_token=cancellation_token
        )
        return replace(
            run,
            snapshot=run.snapshot.model_copy(
                update={"shared_acquisition_cost": self.acquisition_cost}
            ),
            outcome=run.outcome.model_copy(update={"runtime_events": self.events}),
        )


class ScriptedReportModel:
    def __init__(
        self, report_cost: ExecutionCost, *, provider_usage_complete: bool | None = None
    ) -> None:
        self.cost = report_cost
        self.provider_usage_complete = (
            report_cost.provider_usage_requests == report_cost.llm_requests
            if provider_usage_complete is None
            else provider_usage_complete
        )

    async def generate(self, *, prompt: str, schema: dict[str, object]) -> str:
        return json.dumps(
            {
                "analyses": {name: {"text": "", "evidence_ids": []} for name in SECTION_IDS},
                "suggestions": {},
                "risks": [],
            }
        )


def service(tmp_path: Path, pipeline: ScriptedPipeline) -> DueDiligenceService:
    return DueDiligenceService(
        settings=Settings(
            agent_runtime_mode="formal",
            model_name="scripted-model",
            model_provider="scripted",
            model_api_key=SecretStr("test-only"),
            model_base_url="http://model.test/v1",
            artifact_root=tmp_path,
            max_tool_calls=100,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        clock=lambda: NOW,
        formal_pipeline_factory=lambda _: cast(FormalDueDiligencePipeline, pipeline),
    )


def request() -> DueDiligenceRequest:
    return DueDiligenceRequest(
        enterprise=EnterpriseInput(company_name="乐视网信息技术（北京）股份有限公司"),
        scenario_id="normal-enterprise",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_failed_service_persists_authoritative_cost_without_double_counting_events(
    tmp_path: Path,
    complete: bool,
) -> None:
    actual = cost(120, 30, complete=complete)
    error = AgentExecutionError(
        "budget exhausted",
        details={
            "execution_cost": actual.model_dump(mode="json"),
            "provider_usage_complete": complete,
            "prompt": "do-not-persist-private-prompt",
        },
    )
    pipeline = ScriptedPipeline(
        error=error,
        events=(
            TeamRuntimeEvent(
                event_type="model.request.completed",
                payload={"usage_metadata": {"input_tokens": 120, "output_tokens": 30}},
            ),
        ),
    )
    with pytest.raises(AgentExecutionError) as raised:
        await service(tmp_path, pipeline).run(
            request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
        )
    assert raised.value is error
    assert http_status_for_error(raised.value) == 500
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    assert metrics["token_count"] == 150
    assert metrics["tool_calls"] == actual.tool_calls
    assert metrics["execution_cost"] == actual.model_dump(mode="json")
    assert metrics["provider_usage_complete"] is complete
    assert not (tmp_path / "run/result.json").exists()
    assert "do-not-persist-private-prompt" not in json.dumps(metrics)


@pytest.mark.asyncio
async def test_failure_without_ledger_records_observed_usage_as_incomplete_lower_bound(
    tmp_path: Path,
) -> None:
    pipeline = ScriptedPipeline(
        error=AgentExecutionError("acquisition interrupted"),
        events=(
            TeamRuntimeEvent(
                event_type="model.request.completed",
                payload={
                    "usage_metadata": {"input_tokens": 80, "output_tokens": 20},
                    "prompt": "do-not-persist-private-prompt",
                },
            ),
            TeamRuntimeEvent(
                event_type="model.request.completed",
                payload={
                    "usage_metadata": {"input_tokens": 7},
                },
            ),
            TeamRuntimeEvent(
                event_type="model.request.completed",
                payload={
                    "usage_metadata": {"input_tokens": True, "output_tokens": -5},
                },
            ),
            TeamRuntimeEvent(
                event_type="tool.call.completed",
                payload={
                    "usage_metadata": {"input_tokens": 9999, "output_tokens": 9999},
                },
            ),
        ),
    )
    with pytest.raises(AgentExecutionError, match="acquisition interrupted"):
        await service(tmp_path, pipeline).run(
            request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
        )
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    assert metrics["token_count"] == 107
    assert metrics["provider_usage_complete"] is False
    actual = ExecutionCost.model_validate(metrics["execution_cost"])
    assert (actual.input_tokens, actual.output_tokens) == (87, 20)
    assert actual.llm_requests == 3
    assert actual.provider_usage_requests == 1
    assert "do-not-persist-private-prompt" not in json.dumps(metrics)


@pytest.mark.asyncio
async def test_failure_with_no_observed_usage_does_not_claim_a_complete_zero(
    tmp_path: Path,
) -> None:
    pipeline = ScriptedPipeline(error=AgentExecutionError("failed before metering"))
    with pytest.raises(AgentExecutionError):
        await service(tmp_path, pipeline).run(
            request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
        )
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    assert metrics["token_count"] == 0  # Backward-compatible counter, not known-zero usage.
    assert metrics["provider_usage_complete"] is False
    assert metrics["execution_cost"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("report_complete", [True, False])
@pytest.mark.parametrize("event_counter", [None, 9999])
async def test_formal_success_counts_each_layer_once_and_preserves_usage_completeness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_complete: bool,
    event_counter: int | None,
) -> None:
    acquisition, investigation, reporting = (
        cost(10, 20),
        cost(100, 200),
        cost(5, 7, complete=report_complete),
    )
    payload: dict[str, JsonValue] = {"usage_metadata": {"input_tokens": 110, "output_tokens": 220}}
    if event_counter is not None:
        payload["token_count"] = event_counter
    pipeline = ScriptedPipeline(
        acquisition_cost=acquisition,
        investigation_cost=investigation,
        events=(TeamRuntimeEvent(event_type="model.request.completed", payload=payload),),
    )
    monkeypatch.setattr(
        "jindiao.application.service.OpenJiuwenReportModel",
        lambda **_: ScriptedReportModel(reporting),
    )
    result = await service(tmp_path, pipeline).run(
        request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
    )
    assert result.meta.run_id == "run"
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    combined = ExecutionCost.combine(acquisition, investigation, reporting)
    assert metrics["token_count"] == combined.total_tokens == 342
    assert metrics["execution_cost"] == combined.model_dump(mode="json")
    assert metrics["provider_usage_complete"] is report_complete


@pytest.mark.asyncio
async def test_reporting_failure_persists_completed_pipeline_and_reporting_cost_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquisition, investigation, reporting = cost(10, 20), cost(100, 200), cost(5, 7, complete=False)
    pipeline = ScriptedPipeline(acquisition_cost=acquisition, investigation_cost=investigation)
    error = AgentExecutionError(
        "report failed",
        details={
            "execution_cost": reporting.model_dump(mode="json"),
            "provider_usage_complete": False,
        },
    )

    def fail_finish(*_: object, **__: object) -> NoReturn:
        raise error

    monkeypatch.setattr(
        "jindiao.application.service.OpenJiuwenReportModel",
        lambda **_: ScriptedReportModel(reporting),
    )
    monkeypatch.setattr("jindiao.application.service.ProductReportAssembler.finish", fail_finish)
    with pytest.raises(AgentExecutionError) as raised:
        await service(tmp_path, pipeline).run(
            request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
        )
    assert raised.value is error
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    assert metrics["execution_cost"] == ExecutionCost.combine(
        acquisition, investigation, reporting
    ).model_dump(mode="json")
    assert metrics["token_count"] == 342
    assert metrics["provider_usage_complete"] is False
    assert not (tmp_path / "run/result.json").exists()


@pytest.mark.asyncio
async def test_malformed_cost_details_fall_back_to_usage_without_masking_original_error(
    tmp_path: Path,
) -> None:
    error = AgentExecutionError(
        "original failure",
        details={"execution_cost": {"total_tokens": -50, "prompt": "private-token-data"}},
    )
    pipeline = ScriptedPipeline(
        error=error,
        events=(
            TeamRuntimeEvent(
                event_type="model.request.completed",
                payload={
                    "usage_metadata": {"prompt_tokens": 40, "completion_tokens": 2},
                },
            ),
        ),
    )
    with pytest.raises(AgentExecutionError) as raised:
        await service(tmp_path, pipeline).run(
            request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
        )
    assert raised.value is error
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    assert metrics["token_count"] == 42
    assert metrics["provider_usage_complete"] is False
    assert "private-token-data" not in json.dumps(metrics)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_reporting", [False, True])
async def test_report_unknown_usage_marker_overrides_balanced_request_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_reporting: bool,
) -> None:
    reporting = cost(5, 7)
    pipeline = ScriptedPipeline(acquisition_cost=cost(10, 20), investigation_cost=cost(100, 200))
    monkeypatch.setattr(
        "jindiao.application.service.OpenJiuwenReportModel",
        lambda **_: ScriptedReportModel(reporting, provider_usage_complete=False),
    )

    def fail_finish(*_: object, **__: object) -> NoReturn:
        raise AgentExecutionError("report stream interrupted after usage")

    if fail_reporting:
        monkeypatch.setattr(
            "jindiao.application.service.ProductReportAssembler.finish", fail_finish
        )
        with pytest.raises(AgentExecutionError):
            await service(tmp_path, pipeline).run(
                request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
            )
    else:
        await service(tmp_path, pipeline).run(
            request(), mode=OrchestrationMode.SINGLE, request_id="req", run_id="run"
        )
    metrics = json.loads((tmp_path / "run/metrics.json").read_text())
    assert metrics["token_count"] == 342
    assert metrics["provider_usage_complete"] is False
