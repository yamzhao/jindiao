import pytest

from jindiao.application.errors import AgentExecutionError
from jindiao.application.settings import Settings
from jindiao.contracts.execution import ExecutionCost
from jindiao.orchestration.base import RunBudget
from jindiao.reporting.product_model import OpenJiuwenReportModel


@pytest.fixture(autouse=True)
def forbid_external_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("budget-only tests must never construct an external provider")

    monkeypatch.setattr("jindiao.reporting.product_model.Model", forbidden)


def model(prior: ExecutionCost | None = None) -> OpenJiuwenReportModel:
    return OpenJiuwenReportModel(
        # Never inherit the project's credentialed .env in a negative test.
        settings=Settings(
            model_api_key=None, model_base_url=None, model_name="", model_provider="offline"
        ),
        budget=RunBudget(
            max_tool_calls=10,
            max_concurrency=1,
            timeout_seconds=30,
            max_repair_rounds=0,
            max_input_tokens=1000,
            max_output_tokens=1000,
            max_total_tokens=2000,
            max_llm_requests=10,
        ),
        prior_cost=prior or ExecutionCost.zero(),
        elapsed_seconds=0,
    )


def test_report_reservations_are_limited_by_prior_acquisition_and_investigation_cost() -> None:
    prior = ExecutionCost.zero().model_copy(
        update={
            "input_tokens": 900,
            "output_tokens": 100,
            "total_tokens": 1000,
            "llm_requests": 2,
        }
    )
    report = model(prior)
    assert report._ledger is not None
    assert report._ledger.budget.max_input_tokens == 100
    assert report._ledger.budget.max_output_tokens == 900
    assert report._ledger.budget.max_total_tokens == 1000
    assert report._ledger.budget.max_llm_requests == 8


@pytest.mark.asyncio
async def test_report_partial_provider_usage_is_not_declared_complete() -> None:
    report = model()
    assert report._ledger is not None
    reservation = await report._ledger.reserve_llm_request(
        "test", input_tokens=20, output_tokens=20
    )
    await report._ledger.complete_llm_request(
        reservation,
        input_tokens=5,
        output_tokens=2,
        provider_usage=True,
        succeeded=False,
    )
    assert report.cost.total_tokens == 7
    assert report.provider_usage_complete is False


@pytest.mark.asyncio
async def test_report_has_no_provider_dispatch_when_prior_already_spent_budget() -> None:
    prior = ExecutionCost.zero().model_copy(update={"input_tokens": 1000, "total_tokens": 1000})
    report = model(prior)
    with pytest.raises(AgentExecutionError, match="budget"):
        await report.generate(prompt="test", schema={})
    assert report.cost.llm_requests == 0
    assert report._model is None


@pytest.mark.asyncio
async def test_report_cannot_reallocate_a_prior_request_with_unreported_usage() -> None:
    prior = ExecutionCost.zero().model_copy(
        update={"llm_requests": 1, "successful_llm_requests": 1}
    )
    report = model(prior)
    with pytest.raises(AgentExecutionError, match="usage"):
        await report.generate(prompt="test", schema={})
    assert report.cost.llm_requests == 0
    assert report._model is None
    assert report.provider_usage_complete is False
