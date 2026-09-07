from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_formal_pipeline import (
    FakeGateway,
    RecordingContextAgent,
    RecordingFreezer,
    RecordingMultiInvestigator,
    RecordingPolicy,
    RecordingSingleInvestigator,
    pipeline_with_test_doubles,
)
from test_single_agent_strategy import context as base_context

from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.execution import ComparisonFingerprint, ExecutionCost
from jindiao.contracts.results import OrchestrationMode
from jindiao.evaluation.paired import PairedComparisonRunner
from jindiao.orchestration import RunBudget


@pytest.mark.asyncio
async def test_pair_acquires_once_and_both_arms_receive_identical_frozen_snapshot() -> None:
    calls: list[str] = []
    gateway = FakeGateway()
    single = RecordingSingleInvestigator(calls)
    multi = RecordingMultiInvestigator(calls)
    context = replace(
        base_context(),
        requested_enterprise=EnterpriseInput(company_name="Multi Agent 测试有限公司"),
    )
    pipeline = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent(calls),
        supplement_policy=RecordingPolicy(calls),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer(calls),
        single_investigator=single,
        multi_investigator=multi,
        agent_runtime=SimpleNamespace(),
        gateway=gateway,
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
    )

    paired = await PairedComparisonRunner(pipeline=pipeline).run(
        context,
        budget=RunBudget.from_policy(context.policy),
    )

    assert calls == [
        "acquisition",
        "supplement-policy",
        "freeze",
        "single-investigation",
        "multi-investigation",
    ]
    assert paired.single.snapshot is paired.snapshot
    assert paired.multi.snapshot is paired.snapshot
    assert paired.single.snapshot.snapshot_id == paired.multi.snapshot.snapshot_id
    assert paired.single.snapshot.snapshot_sha256 == paired.multi.snapshot.snapshot_sha256
    assert paired.single.comparison_metadata.topology is OrchestrationMode.SINGLE
    assert paired.multi.comparison_metadata.topology is OrchestrationMode.MULTI
    assert paired.single.comparison_metadata.comparison_pair_id == paired.pair_id
    assert paired.multi.comparison_metadata.comparison_pair_id == paired.pair_id
    assert paired.single.comparison_metadata.paired_comparison is True
    assert paired.multi.comparison_metadata.paired_comparison is True
    assert paired.fingerprint.snapshot_id == paired.snapshot.snapshot_id
    assert paired.fingerprint.snapshot_sha256 == paired.snapshot.snapshot_sha256
    assert paired.fingerprint.reporting_policy_sha256 == context.reporting_policy.policy_sha256
    assert paired.fingerprint.report_renderer_version
    assert paired.fingerprint.gap_mapping_version
    assert (
        paired.single.comparison_metadata.reporting_policy_sha256
        == paired.multi.comparison_metadata.reporting_policy_sha256
    )
    assert single.budget_ledgers[0] is not multi.budget_ledgers[0]
    assert single.budget_ledgers[0].budget == multi.budget_ledgers[0].budget
    assert paired.execution_cost.shared_acquisition_cost.mcp_calls == 3
    assert paired.execution_cost.single_investigation_cost.mcp_calls == 0
    assert paired.execution_cost.multi_investigation_cost.mcp_calls == 0
    assert paired.execution_cost.combined.mcp_calls == 3
    assert paired.formal_eligibility.eligible is False
    assert set(paired.formal_eligibility.reasons) >= {
        "single:no_provider_usage",
        "single:zero_tokens",
        "multi:no_provider_usage",
        "multi:zero_tokens",
    }
    assert gateway.closed is True


@pytest.mark.asyncio
async def test_pair_preflight_lists_all_fingerprint_mismatches_before_investigation() -> None:
    calls: list[str] = []
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
        multi_investigator=RecordingMultiInvestigator(calls),
        agent_runtime=SimpleNamespace(),
        gateway=FakeGateway(),
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
    )
    budget = RunBudget.from_policy(context.policy)

    def mismatched_fingerprint(
        mode: OrchestrationMode,
        snapshot: EnterpriseContextSnapshot,
    ) -> ComparisonFingerprint:
        fingerprint = pipeline.comparison_fingerprint(
            context,
            snapshot=snapshot,
            budget=budget,
        )
        if mode is OrchestrationMode.MULTI:
            return fingerprint.model_copy(
                update={
                    "model_name": "different-model",
                    "common_prompt_sha256": "f" * 64,
                    "check_catalog_sha256": "e" * 64,
                    "report_catalog_sha256": "d" * 64,
                    "snapshot_id": "different-snapshot",
                    "snapshot_sha256": "c" * 64,
                    "investigation_budget": fingerprint.investigation_budget.model_copy(
                        update={
                            "max_llm_requests": (
                                fingerprint.investigation_budget.max_llm_requests + 1
                            )
                        }
                    ),
                }
            )
        return fingerprint

    with pytest.raises(ValueError) as captured:
        await PairedComparisonRunner(
            pipeline=pipeline,
            fingerprint_factory=mismatched_fingerprint,
        ).run(context, budget=budget)

    message = str(captured.value)
    for field in (
        "snapshot_id",
        "snapshot_sha256",
        "common_prompt_sha256",
        "check_catalog_sha256",
        "report_catalog_sha256",
        "model_name",
        "investigation_budget",
    ):
        assert field in message
    assert calls == ["acquisition", "supplement-policy", "freeze"]


@pytest.mark.asyncio
async def test_pair_is_formal_only_with_real_usage_and_valid_fixed_check_submissions() -> None:
    calls: list[str] = []
    context = replace(
        base_context(),
        requested_enterprise=EnterpriseInput(company_name="Multi Agent 测试有限公司"),
    )
    real_usage = ExecutionCost(
        llm_requests=3,
        successful_llm_requests=3,
        provider_usage_requests=3,
        input_tokens=90,
        output_tokens=30,
        total_tokens=120,
        tool_calls=16,
        mcp_calls=0,
        schema_retries=0,
        repair_rounds=0,
        wall_time_ms=50,
    )
    pipeline = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent(calls),
        supplement_policy=RecordingPolicy(calls),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer(calls),
        single_investigator=RecordingSingleInvestigator(calls, cost=real_usage),
        multi_investigator=RecordingMultiInvestigator(calls, cost=real_usage),
        agent_runtime=SimpleNamespace(),
        gateway=FakeGateway(),
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
    )

    paired = await PairedComparisonRunner(pipeline=pipeline).run(
        context,
        budget=RunBudget.from_policy(context.policy),
    )

    assert paired.formal_eligibility.eligible is True
    assert paired.formal_eligibility.reasons == ()


@pytest.mark.asyncio
async def test_pair_rejects_formal_claim_when_multi_reports_hidden_budget() -> None:
    calls: list[str] = []
    context = replace(
        base_context(),
        requested_enterprise=EnterpriseInput(company_name="Multi Agent 测试有限公司"),
    )
    budget = RunBudget.from_policy(context.policy)
    valid_single_cost = ExecutionCost(
        llm_requests=1,
        successful_llm_requests=1,
        provider_usage_requests=1,
        input_tokens=8,
        output_tokens=2,
        total_tokens=10,
        tool_calls=15,
        mcp_calls=0,
        schema_retries=0,
        repair_rounds=0,
        wall_time_ms=10,
    )
    hidden_multi_cost = valid_single_cost.model_copy(
        update={
            "llm_requests": budget.max_llm_requests + 1,
            "successful_llm_requests": budget.max_llm_requests + 1,
            "provider_usage_requests": budget.max_llm_requests + 1,
        }
    )
    pipeline = pipeline_with_test_doubles(
        context_agent=RecordingContextAgent(calls),
        supplement_policy=RecordingPolicy(calls),
        deepsearch_agent=None,
        context_freezer=RecordingFreezer(calls),
        single_investigator=RecordingSingleInvestigator(calls, cost=valid_single_cost),
        multi_investigator=RecordingMultiInvestigator(calls, cost=hidden_multi_cost),
        agent_runtime=SimpleNamespace(),
        gateway=FakeGateway(),
        model_name="scripted-model",
        model_provider="scripted",
        model_api_key="test-only",
        model_base_url="http://model.test/v1",
    )

    paired = await PairedComparisonRunner(pipeline=pipeline).run(
        context,
        budget=budget,
    )

    assert paired.formal_eligibility.eligible is False
    assert "multi:investigation_budget_exceeded" in paired.formal_eligibility.reasons
