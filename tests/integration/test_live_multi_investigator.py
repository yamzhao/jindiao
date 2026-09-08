"""Opt-in smoke test for the real model-backed AgentTeams investigator."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from openjiuwen.agent_teams.paths import configure_openjiuwen_home, reset_openjiuwen_home
from openjiuwen.core.runner import Runner

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.agents import AgentTeamsInvestigatorTeam
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceType
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration import BudgetLedger, RunBudget
from jindiao.prompts import load_prompt_bundle

if os.getenv("JINDIAO_RUN_LIVE_MULTI") != "1":
    pytest.skip(
        "set JINDIAO_RUN_LIVE_MULTI=1 to run the credentialed live multi smoke",
        allow_module_level=True,
    )

_MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
_MODEL_NAME = os.getenv("MODEL_NAME", "")
_MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "")
_MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
if not all((_MODEL_PROVIDER, _MODEL_NAME, _MODEL_BASE_URL, _MODEL_API_KEY)):
    pytest.skip(
        "live multi smoke requires MODEL_PROVIDER/NAME/BASE_URL/API_KEY",
        allow_module_level=True,
    )


def _snapshot() -> EnterpriseContextSnapshot:
    now = datetime.now(UTC)
    report_as_of = date.today()
    subject = ResolvedSubject(
        subject_id="live-smoke:multi-investigator",
        company_name="Multi Agent 实时冒烟测试企业",
        source=SubjectSource.TIANYANCHA,
        resolved_at=now,
    )
    registration = Evidence(
        evidence_id="live-multi-smoke-registration",
        claim="工商登记状态为存续",
        value={"registration_status": "存续"},
        subject_id=subject.subject_id,
        source_type=SourceType.TIANYANCHA,
        source_tool="live_smoke_fixture",
        source_record_id="registration-1",
        queried_at=now,
        as_of_date=report_as_of,
        confidence=1,
        is_mock=False,
        supports_fields=("governance.registration.registration_status",),
        raw_ref="mcp://live-smoke/registration-1",
        content_hash="sha256:" + "d" * 64,
    )
    submodules = tuple(
        SubmoduleContext(
            submodule_id=submodule_id,
            availability=(
                SubmoduleAvailability.AVAILABLE
                if submodule_id == "registration"
                else SubmoduleAvailability.NOT_REQUESTED
            ),
            completeness=(
                CoverageCompleteness.COMPLETE
                if submodule_id == "registration"
                else CoverageCompleteness.UNKNOWN
            ),
            facts=({"registration_status": "存续"} if submodule_id == "registration" else {}),
            evidence_ids=(registration.evidence_id,) if submodule_id == "registration" else (),
            unresolved_gap_ids=() if submodule_id == "registration" else (f"gap:{submodule_id}",),
        )
        for submodule_id in ACQUISITION_CATALOG.default_plan_ids
    )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:live-multi-smoke",
        snapshot_sha256="c" * 64,
        subject=subject,
        report_as_of=report_as_of,
        created_at=now,
        acquisition_catalog_version=ACQUISITION_CATALOG.catalog_version,
        planned_submodule_ids=ACQUISITION_CATALOG.default_plan_ids,
        source_manifest_version="live-smoke-v1",
        submodules=submodules,
        evidence=(registration,),
        supplement_tasks=(),
        unresolved_gaps=tuple(
            f"gap:{submodule_id}"
            for submodule_id in ACQUISITION_CATALOG.default_plan_ids
            if submodule_id != "registration"
        ),
        unresolved_conflicts=(),
    )


@pytest.mark.asyncio
async def test_live_multi_investigator_runs_past_team_build_and_reviews_submissions(
    tmp_path: Path,
) -> None:
    budget = BudgetLedger(
        RunBudget(
            max_tool_calls=160,
            max_concurrency=6,
            timeout_seconds=600,
            max_repair_rounds=2,
            max_llm_requests=160,
            max_input_tokens=1_200_000,
            max_output_tokens=300_000,
            max_total_tokens=1_500_000,
            max_schema_retries=12,
            max_snapshot_reads=120,
        )
    )
    configure_openjiuwen_home(tmp_path / "openjiuwen")
    await Runner.start()
    try:
        completed = await AgentTeamsInvestigatorTeam(prompt_bundle=load_prompt_bundle()).run(
            snapshot=_snapshot(),
            budget_ledger=budget,
            run_id="run-live-multi-smoke",
            model_name=_MODEL_NAME,
            model_provider=_MODEL_PROVIDER,
            model_api_key=_MODEL_API_KEY,
            model_base_url=_MODEL_BASE_URL,
            timeout_seconds=600,
        )
    finally:
        await Runner.stop()
        reset_openjiuwen_home()

    usage = budget.snapshot()
    assert {item.check_id for item in completed.check_results} == set(CHECK_CATALOG.check_ids)
    assert len({item.agent_id for item in completed.agent_results}) == 6
    assert completed.reviews
    assert completed.reviews[-1].repair_tasks == ()
    assert usage.llm_requests > 0
    assert usage.successful_llm_requests > 0
    assert usage.provider_usage_requests == usage.successful_llm_requests
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
    assert usage.snapshot_reads > 0

    public_events = json.dumps(
        [item.model_dump(mode="json") for item in completed.events],
        ensure_ascii=False,
    ).casefold()
    build_index = public_events.find("build_team")
    assert build_index >= 0
    assert "team.completed" in public_events[build_index:]
    assert "system_prompt" not in public_events
    assert "chain_of_thought" not in public_events
    assert "llm_reasoning" not in public_events
    assert _MODEL_API_KEY.casefold() not in public_events
