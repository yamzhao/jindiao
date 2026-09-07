"""Opt-in smoke test for the real model-backed single investigator."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime

import pytest
from openjiuwen.core.runner import Runner

from jindiao.agents import SingleInvestigatorAgent
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceType
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration import BudgetLedger, OpenJiuwenAgentExecutionRuntime, RunBudget
from jindiao.prompts import load_prompt_bundle
from jindiao.reporting.catalog import REPORT_CATALOG

if os.getenv("JINDIAO_RUN_LIVE_SINGLE") != "1":
    pytest.skip(
        "set JINDIAO_RUN_LIVE_SINGLE=1 to run the credentialed live single smoke",
        allow_module_level=True,
    )

_MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
_MODEL_NAME = os.getenv("MODEL_NAME", "")
_MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "")
_MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
if not all((_MODEL_PROVIDER, _MODEL_NAME, _MODEL_BASE_URL, _MODEL_API_KEY)):
    pytest.skip(
        "live single smoke requires MODEL_PROVIDER/NAME/BASE_URL/API_KEY",
        allow_module_level=True,
    )


def _snapshot() -> EnterpriseContextSnapshot:
    now = datetime.now(UTC)
    report_as_of = date.today()
    subject = ResolvedSubject(
        subject_id="live-smoke:single-investigator",
        company_name="单 Agent 实时冒烟测试企业",
        source=SubjectSource.TIANYANCHA,
        resolved_at=now,
    )
    registration = Evidence(
        evidence_id="live-smoke-registration",
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
        content_hash="sha256:" + "e" * 64,
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
        for submodule_id in REPORT_CATALOG.submodule_ids
    )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:live-single-smoke",
        snapshot_sha256="f" * 64,
        subject=subject,
        report_as_of=report_as_of,
        created_at=now,
        report_catalog_version=REPORT_CATALOG.catalog_version,
        source_manifest_version="live-smoke-v1",
        submodules=submodules,
        evidence=(registration,),
        supplement_tasks=(),
        unresolved_gaps=tuple(
            f"gap:{submodule_id}"
            for submodule_id in REPORT_CATALOG.submodule_ids
            if submodule_id != "registration"
        ),
        unresolved_conflicts=(),
    )


@pytest.mark.asyncio
async def test_live_single_investigator_reports_usage_and_respects_snapshot_boundary() -> None:
    budget = BudgetLedger(
        RunBudget(
            max_tool_calls=80,
            max_concurrency=1,
            timeout_seconds=300,
            max_repair_rounds=0,
            max_llm_requests=50,
            max_input_tokens=300_000,
            max_output_tokens=100_000,
            max_total_tokens=400_000,
            max_schema_retries=4,
            max_snapshot_reads=60,
        )
    )
    await Runner.start()
    try:
        completed = await SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle()).run(
            snapshot=_snapshot(),
            runtime=OpenJiuwenAgentExecutionRuntime(),
            budget_ledger=budget,
            run_id="run-live-single-smoke",
            model_name=_MODEL_NAME,
            model_provider=_MODEL_PROVIDER,
            model_api_key=_MODEL_API_KEY,
            model_base_url=_MODEL_BASE_URL,
            timeout_seconds=300,
            max_iterations=40,
        )
    finally:
        await Runner.stop()

    usage = budget.snapshot()
    assert completed.self_check_completed is True
    assert (
        tuple(item.check_id for item in completed.agent_result.check_results)
        == CHECK_CATALOG.check_ids
    )
    assert usage.llm_requests > 0
    assert usage.provider_usage_requests == usage.successful_llm_requests
    assert completed.snapshot_reads
    assert all(getattr(item, "allowed", False) for item in completed.snapshot_reads)
    public_events = json.dumps(
        [item.model_dump(mode="json") for item in completed.events],
        ensure_ascii=False,
    ).casefold()
    assert "system_prompt" not in public_events
    assert "chain_of_thought" not in public_events
    assert "llm_reasoning" not in public_events
    assert _MODEL_API_KEY.casefold() not in public_events
