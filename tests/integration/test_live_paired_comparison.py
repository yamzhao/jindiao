"""Opt-in live smoke for one fair, model-backed single-versus-multi pair."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path

import pytest
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home
from openjiuwen.core.runner import Runner
from pydantic import ValidationError

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.application import RunContext, Settings
from jindiao.application.errors import AgentExecutionError
from jindiao.application.service import DueDiligenceService
from jindiao.contracts.entities import EnterpriseInput
from jindiao.evaluation import PairedComparisonRunner
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration import RunBudget
from jindiao.scenarios import ScenarioRepository

if os.getenv("JINDIAO_RUN_LIVE_PAIRED") != "1":
    pytest.skip(
        "set JINDIAO_RUN_LIVE_PAIRED=1 to run the credentialed fair paired smoke",
        allow_module_level=True,
    )

try:
    SETTINGS = Settings(
        agent_runtime_mode="formal",
        data_source_mode="tianyancha",
        max_concurrency=6,
        request_timeout_seconds=600,
        max_tool_calls=160,
        max_repair_rounds=2,
        max_llm_requests=160,
        max_input_tokens=1_200_000,
        max_output_tokens=300_000,
        max_total_tokens=1_500_000,
        max_schema_retries=12,
        max_snapshot_reads=120,
        skill_evolution_enabled=False,
    )
except ValidationError:
    pytest.skip(
        "live paired smoke requires complete MODEL_* and Tianyancha credentials",
        allow_module_level=True,
    )


@pytest.mark.asyncio
async def test_live_pair_shares_snapshot_model_catalog_and_investigation_budget(
    tmp_path: Path,
) -> None:
    scenarios = ScenarioRepository(Path("mock_data/scenarios"))
    scenario = scenarios.load_template("normal-enterprise")
    run_suffix = uuid.uuid4().hex
    context = RunContext.from_settings(
        request_id=f"req-live-paired-{run_suffix}",
        run_id=f"run-live-paired-{run_suffix}",
        scenario=scenario,
        settings=SETTINGS,
        skill_versions={},
        requested_enterprise=EnterpriseInput(
            company_name=os.getenv("JINDIAO_LIVE_PAIRED_COMPANY", "华为技术有限公司")
        ),
        report_as_of=date.today(),
    )
    service = DueDiligenceService(settings=SETTINGS, scenarios=scenarios)
    pipeline = service._create_formal_pipeline(context, use_live_source=True)
    budget = RunBudget.from_policy(context.policy)

    configure_openjiuwen_home(tmp_path / "openjiuwen")
    await Runner.start()
    try:
        try:
            paired = await PairedComparisonRunner(pipeline=pipeline).run(
                context,
                budget=budget,
            )
        except AgentExecutionError as error:
            pytest.fail(f"{error.message}; safe_details={error.details}")
    finally:
        await Runner.stop()
        reset_openjiuwen_home()

    print(
        "live_paired_diagnostic="
        + json.dumps(
            {
                "pair_id": paired.pair_id,
                "snapshot_id": paired.snapshot.snapshot_id,
                "snapshot_sha256": paired.snapshot.snapshot_sha256,
                "model_provider": paired.fingerprint.model_provider,
                "model_name": paired.fingerprint.model_name,
                "investigation_budget": budget.model_dump(mode="json"),
                "single_investigation_cost": (
                    paired.execution_cost.single_investigation_cost.model_dump(mode="json")
                ),
                "multi_investigation_cost": (
                    paired.execution_cost.multi_investigation_cost.model_dump(mode="json")
                ),
                "formal_eligibility": paired.formal_eligibility.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    assert paired.single.snapshot is paired.snapshot
    assert paired.multi.snapshot is paired.snapshot
    assert paired.single.snapshot.snapshot_id == paired.multi.snapshot.snapshot_id
    assert paired.single.snapshot.snapshot_sha256 == paired.multi.snapshot.snapshot_sha256
    assert paired.fingerprint.snapshot_id == paired.snapshot.snapshot_id
    assert paired.fingerprint.snapshot_sha256 == paired.snapshot.snapshot_sha256
    assert paired.fingerprint.model_provider == SETTINGS.model_provider
    assert paired.fingerprint.model_name == SETTINGS.model_name
    assert paired.fingerprint.investigation_budget.max_llm_requests == budget.max_llm_requests
    assert tuple(item.submodule_id for item in paired.snapshot.submodules) == (
        ACQUISITION_CATALOG.default_plan_ids
    )
    assert (
        tuple(item.check_id for item in paired.single.agent_results[0].check_results)
        == CHECK_CATALOG.check_ids
    )
    assert {
        item.check_id for result in paired.multi.agent_results for item in result.check_results
    } == set(CHECK_CATALOG.check_ids)
    assert paired.execution_cost.shared_acquisition_cost.mcp_calls > 0
    assert paired.execution_cost.single_investigation_cost.provider_usage_requests > 0
    assert paired.execution_cost.multi_investigation_cost.provider_usage_requests > 0
    assert paired.formal_eligibility.eligible is True
    assert paired.formal_eligibility.reasons == ()

    public_events = json.dumps(
        [
            event.model_dump(mode="json")
            for arm in (paired.single, paired.multi)
            for event in arm.outcome.runtime_events
        ],
        ensure_ascii=False,
    ).casefold()
    assert "system_prompt" not in public_events
    assert "chain_of_thought" not in public_events
    assert "reasoning_content" not in public_events
    assert SETTINGS.model_api_key is not None
    assert SETTINGS.model_api_key.get_secret_value().casefold() not in public_events
