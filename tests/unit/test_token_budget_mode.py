from types import SimpleNamespace

import pytest
from test_formal_pipeline import (
    FakeGateway,
    ForbiddenMultiInvestigator,
    RecordingContextAgent,
    RecordingFreezer,
    RecordingPolicy,
    pipeline_with_test_doubles,
)
from test_multi_investigator_team import snapshot as base_snapshot
from test_single_agent_strategy import context

from jindiao.application.context import RunContext
from jindiao.application.errors import AgentExecutionError
from jindiao.application.settings import Settings
from jindiao.contracts.execution import ExecutionCost, InvestigationBudgetFingerprint
from jindiao.contracts.results import OrchestrationMode
from jindiao.orchestration.base import BudgetLedger, RunBudget
from jindiao.orchestration.budgeted_model import BudgetedModel
from jindiao.reporting.product_model import OpenJiuwenReportModel


def budget(*, enabled: bool = False) -> RunBudget:
    return RunBudget.model_validate(
        {
            "max_tool_calls": 2,
            "max_concurrency": 1,
            "timeout_seconds": 10,
            "max_repair_rounds": 0,
            "max_llm_requests": 2,
            "max_input_tokens": 5,
            "max_output_tokens": 5,
            "max_total_tokens": 8,
            "enforce_token_budget": enabled,
        }
    )


def settings(*, model_name: str = "", enforce_token_budget: bool = True) -> Settings:
    return Settings(
        model_api_key=None,
        model_base_url=None,
        model_name=model_name,
        enforce_token_budget=enforce_token_budget,
        agent_runtime_mode="deterministic_harness",
        data_source_mode="mock",
    )


def test_token_enforcement_setting_is_frozen_in_policy_and_budget() -> None:
    configured = settings(model_name="deepseek-v4-flash-0731", enforce_token_budget=False)
    original = context()
    frozen = RunContext.from_settings(
        request_id="test",
        run_id="test",
        scenario=original.scenario,
        settings=configured,
        skill_versions={},
    )
    assert frozen.policy.model_name == "deepseek-v4-flash-0731"
    assert frozen.policy.enforce_token_budget is False
    assert RunBudget.from_policy(frozen.policy).enforce_token_budget is False
    assert budget(enabled=True).enforce_token_budget is True
    assert Settings.model_fields["enforce_token_budget"].default is True


def test_comparison_fingerprint_distinguishes_enforced_and_observed_budgets() -> None:
    fingerprint = InvestigationBudgetFingerprint(
        max_llm_requests=2,
        max_input_tokens=5,
        max_output_tokens=5,
        max_total_tokens=8,
        max_wall_time_ms=1000,
        max_concurrency=1,
        max_schema_retries=1,
        max_repair_rounds=0,
    )
    observed = fingerprint.model_copy(update={"enforce_token_budget": False})
    assert fingerprint.enforce_token_budget is True
    assert observed != fingerprint
    assert observed.model_dump()["enforce_token_budget"] is False


def test_environment_switch_parses_false_without_affecting_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JINDIAO_ENFORCE_TOKEN_BUDGET", "false")
    configured = Settings(
        model_api_key=None,
        model_base_url=None,
        model_name="deepseek-v4-flash-0731",
        agent_runtime_mode="deterministic_harness",
        data_source_mode="mock",
    )
    assert configured.enforce_token_budget is False
    assert configured.model_name == "deepseek-v4-flash-0731"


@pytest.mark.asyncio
async def test_observe_mode_does_not_abort_before_or_after_large_usage() -> None:
    ledger = BudgetLedger(budget())
    reservation = await ledger.reserve_llm_request(
        "test", input_tokens=1_000_000, output_tokens=10000
    )
    assert reservation.output_tokens == 10000
    await ledger.complete_llm_request(
        reservation, input_tokens=900_000, output_tokens=2000, provider_usage=True
    )
    assert ledger.to_execution_cost().total_tokens == 902_000
    assert ledger.error_details()["provider_usage_complete"] is True
    assert ledger.snapshot().exhausted_reason is None


@pytest.mark.asyncio
async def test_observe_mode_still_enforces_request_and_deadline_limits() -> None:
    now = [0.0]
    ledger = BudgetLedger(
        budget().model_copy(update={"max_llm_requests": 1}), monotonic=lambda: now[0]
    )
    await ledger.claim_llm_request("first")
    await ledger.record_llm_usage(input_tokens=99, output_tokens=99, provider_usage=True)
    with pytest.raises(AgentExecutionError, match="LLM-request"):
        await ledger.claim_llm_request("second")
    other = BudgetLedger(budget(), monotonic=lambda: now[0])
    now[0] = 11.0
    with pytest.raises(AgentExecutionError, match="deadline"):
        await other.reserve_llm_request("late", input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_observe_model_dispatches_oversized_input_with_bounded_output() -> None:
    calls = []

    class Provider:
        async def invoke(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                content="ok", usage_metadata=SimpleNamespace(input_tokens=99, output_tokens=7)
            )

    ledger = BudgetLedger(budget())
    result = await BudgetedModel(Provider(), budget_ledger=ledger).invoke(
        messages=[{"role": "user", "content": "data" * 1000}],
    )
    assert result.content == "ok" and calls[0]["max_tokens"] == 10000
    assert ledger.to_execution_cost().total_tokens == 106


@pytest.mark.asyncio
async def test_observe_pipeline_does_not_abort_on_prior_token_totals() -> None:
    prior = ExecutionCost.zero().model_copy(
        update={
            "llm_requests": 1,
            "successful_llm_requests": 1,
            "provider_usage_requests": 1,
            "input_tokens": 100,
            "output_tokens": 100,
            "total_tokens": 200,
        }
    )
    inspected: list[RunBudget] = []

    class Investigator:
        async def run(self, *, budget_ledger: BudgetLedger, **kwargs: object) -> object:
            inspected.append(budget_ledger.budget)
            raise AgentExecutionError("inspection reached")

    target = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent([]),
        supplement_policy=RecordingPolicy([]),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer([]),
        single_investigator=Investigator(),
        multi_investigator=ForbiddenMultiInvestigator(),
        agent_runtime=SimpleNamespace(),
        gateway=FakeGateway(),
        model_name="scripted",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.invalid",
    )
    with pytest.raises(AgentExecutionError, match="inspection reached"):
        await target.investigate(
            context(),
            snapshot=base_snapshot().model_copy(update={"shared_acquisition_cost": prior}),
            mode=OrchestrationMode.SINGLE,
            budget=budget(),
        )
    assert inspected[0].enforce_token_budget is False
    assert inspected[0].max_llm_requests == 1


@pytest.mark.asyncio
async def test_observe_report_ignores_prior_and_prompt_token_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jindiao.reporting.product_model.Model",
        lambda **kw: pytest.fail("external provider forbidden"),
    )
    prior = ExecutionCost.zero().model_copy(update={"input_tokens": 100, "total_tokens": 100})
    report = OpenJiuwenReportModel(
        settings=settings(), budget=budget(), prior_cost=prior, elapsed_seconds=0
    )
    assert report._ledger is not None
    calls = []

    class Provider:
        async def invoke(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                content="{}", usage_metadata=SimpleNamespace(input_tokens=99, output_tokens=2)
            )

    report._model = BudgetedModel(Provider(), budget_ledger=report._ledger)
    assert await report.generate(prompt="data" * 1000, schema={}) == "{}"
    assert calls[0]["max_tokens"] == 10000
    assert report.cost.total_tokens == 101
