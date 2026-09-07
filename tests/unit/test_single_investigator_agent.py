from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner import Runner

from jindiao.agents import SingleInvestigatorAgent
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceStatus, SourceType
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    FactEvidenceRef,
    RiskClass,
    RiskItem,
    Severity,
)
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration import (
    BudgetLedger,
    OpenJiuwenAgentExecutionRuntime,
    RunBudget,
)
from jindiao.orchestration.react_model import JINDIAO_OPENAI_COMPATIBLE_PROVIDER
from jindiao.prompts import load_prompt_bundle
from jindiao.reporting.catalog import REPORT_CATALOG

NOW = datetime(2026, 9, 5, 17, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def context_snapshot() -> EnterpriseContextSnapshot:
    subject = ResolvedSubject(
        subject_id="tyc:single-1",
        company_name="Single Agent 测试有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )
    evidence = Evidence(
        evidence_id="ev-registration",
        claim="工商状态为存续",
        value={"registration_status": "存续"},
        subject_id=subject.subject_id,
        source_type=SourceType.TIANYANCHA,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="get_company_registration_info",
        source_record_id="reg-1",
        queried_at=NOW,
        as_of_date=REPORT_AS_OF,
        confidence=1,
        is_mock=False,
        supports_fields=("governance.registration",),
        raw_ref="mcp://tianyancha/registration/reg-1",
        content_hash="sha256:" + "b" * 64,
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
            evidence_ids=(evidence.evidence_id,) if submodule_id == "registration" else (),
            unresolved_gap_ids=(() if submodule_id == "registration" else (f"gap:{submodule_id}",)),
        )
        for submodule_id in REPORT_CATALOG.submodule_ids
    )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:single",
        snapshot_sha256="a" * 64,
        subject=subject,
        report_as_of=REPORT_AS_OF,
        created_at=NOW,
        report_catalog_version=REPORT_CATALOG.catalog_version,
        source_manifest_version="manifest-v1",
        submodules=submodules,
        evidence=(evidence,),
        supplement_tasks=(),
        unresolved_gaps=tuple(
            f"gap:{item}" for item in REPORT_CATALOG.submodule_ids if item != "registration"
        ),
        unresolved_conflicts=(),
    )


def message(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        tool_calls=tool_calls,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="single-scripted-model",
            input_tokens=5,
            output_tokens=2,
            total_tokens=7,
        ),
    )


class ScriptedModel:
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


def bound_decision(result: CheckResult) -> dict[str, object]:
    payload = result.model_dump(mode="json")
    risk_items = [
        {key: value for key, value in item.items() if key not in {"check_id", "status"}}
        for item in payload["risk_items"]
    ]
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "check_id",
            "status",
            "decision_summary",
            "fact_evidence_refs",
            "missing_evidence",
            "conflicts",
            "confidence",
        }
    } | {"risk_items": risk_items}


def complete_responses() -> list[AssistantMessage]:
    snapshot = context_snapshot()
    responses: list[AssistantMessage] = [
        message(
            tool_calls=[
                ToolCall(
                    id="read-assigned-context",
                    type="function",
                    name="read_assigned_snapshot_context",
                    arguments="{}",
                )
            ]
        )
    ]
    for check in CHECK_CATALOG.checks:
        task_id = f"check:{check.check_id}"
        fact_refs: tuple[FactEvidenceRef, ...] = ()
        missing_evidence = check.required_submodule_ids
        status = CheckStatus.INCONCLUSIVE
        decision_summary = "必需快照证据不足。"
        confidence = 0.1
        if check.check_id == "registration-status-normal":
            fact_refs = (
                FactEvidenceRef(
                    evidence_id="ev-registration",
                    fact_path="governance.registration.registration_status",
                    summary="工商状态为存续",
                ),
            )
            missing_evidence = ()
            status = CheckStatus.NO_RISK
            decision_summary = "工商登记状态正常。"
            confidence = 0.95
        result = CheckResult(
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            subject_id=snapshot.subject.subject_id,
            check_catalog_version=CHECK_CATALOG.catalog_version,
            output_schema_version=check.output_schema_version,
            task_id=task_id,
            check_id=check.check_id,
            status=status,
            decision_summary=decision_summary,
            fact_evidence_refs=fact_refs,
            missing_evidence=missing_evidence,
            confidence=confidence,
            prompt_version="investigation-core-v1+single-investigator-v1",
            submission_version=1,
        )
        responses.append(
            message(
                tool_calls=[
                    ToolCall(
                        id=f"submit-{check.check_id}",
                        type="function",
                        name="submit_check_result",
                        arguments=json.dumps(
                            {"result": bound_decision(result)},
                            ensure_ascii=False,
                        ),
                    )
                ]
            )
        )
    responses.extend(
        (
            message(
                tool_calls=[
                    ToolCall(
                        id="self-check",
                        type="function",
                        name="submit_investigation_self_check",
                        arguments="{}",
                    )
                ]
            ),
            message("all fixed checks submitted"),
        )
    )
    return responses


def _replace_check_submission(
    responses: list[AssistantMessage],
    *,
    result: CheckResult,
    before: tuple[AssistantMessage, ...] = (),
) -> list[AssistantMessage]:
    for index, response in enumerate(responses):
        for call in response.tool_calls or ():
            if call.name != "submit_check_result":
                continue
            payload = json.loads(call.arguments)
            if payload["result"]["check_id"] != result.check_id:
                continue
            replacement = message(
                tool_calls=[
                    ToolCall(
                        id=f"submit-{result.check_id}-replacement",
                        type="function",
                        name="submit_check_result",
                        arguments=json.dumps(
                            {"result": bound_decision(result)},
                            ensure_ascii=False,
                        ),
                    )
                ]
            )
            return [*responses[:index], *before, replacement, *responses[index + 1 :]]
    raise AssertionError(f"submission not found: {result.check_id}")


def profitability_snapshot(*, conflicted_registration: bool = False) -> EnterpriseContextSnapshot:
    base = context_snapshot()
    financial = Evidence(
        evidence_id="ev-financial-summary",
        claim="近三年净利润率持续下降",
        value={"net_margin": [0.18, 0.11, 0.04]},
        subject_id=base.subject.subject_id,
        source_type=SourceType.TIANYANCHA,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="get_company_annual_reports",
        source_record_id="financial-summary-1",
        queried_at=NOW,
        as_of_date=REPORT_AS_OF,
        confidence=0.96,
        is_mock=False,
        supports_fields=("operations.financial_summary.net_margin",),
        raw_ref="mcp://tianyancha/financial-summary/1",
        content_hash="sha256:" + "c" * 64,
    )
    income = Evidence(
        evidence_id="ev-income-statement",
        claim="近三年净利润持续下降",
        value={"net_profit": [1200, 700, 200]},
        subject_id=base.subject.subject_id,
        source_type=SourceType.TIANYANCHA,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="get_company_annual_reports",
        source_record_id="income-statement-1",
        queried_at=NOW,
        as_of_date=REPORT_AS_OF,
        confidence=0.96,
        is_mock=False,
        supports_fields=("operations.income_statement.net_profit",),
        raw_ref="mcp://tianyancha/income-statement/1",
        content_hash="sha256:" + "d" * 64,
    )
    replacement_evidence = {
        "financial_summary": financial,
        "income_statement": income,
    }
    submodules = []
    for item in base.submodules:
        evidence = replacement_evidence.get(item.submodule_id)
        if evidence is not None:
            submodules.append(
                SubmoduleContext(
                    submodule_id=item.submodule_id,
                    availability=SubmoduleAvailability.AVAILABLE,
                    completeness=CoverageCompleteness.COMPLETE,
                    facts={"trend": "declining"},
                    evidence_ids=(evidence.evidence_id,),
                )
            )
        elif conflicted_registration and item.submodule_id == "registration":
            submodules.append(
                item.model_copy(update={"conflict_evidence_ids": ("ev-registration",)})
            )
        else:
            submodules.append(item)
    return EnterpriseContextSnapshot.model_validate(
        {
            **base.model_dump(mode="json"),
            "submodules": [item.model_dump(mode="json") for item in submodules],
            "evidence": [
                base.evidence[0].model_dump(mode="json"),
                financial.model_dump(mode="json"),
                income.model_dump(mode="json"),
            ],
            "unresolved_gaps": [
                item
                for item in base.unresolved_gaps
                if item not in {"gap:financial_summary", "gap:income_statement"}
            ],
            "unresolved_conflicts": (["ev-registration"] if conflicted_registration else []),
        }
    )


def profitability_risk_result(snapshot: EnterpriseContextSnapshot) -> CheckResult:
    refs = (
        FactEvidenceRef(
            evidence_id="ev-financial-summary",
            fact_path="operations.financial_summary.net_margin",
            summary="净利润率连续三年下降",
        ),
        FactEvidenceRef(
            evidence_id="ev-income-statement",
            fact_path="operations.income_statement.net_profit",
            summary="净利润连续三年下降",
        ),
    )
    return CheckResult(
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        subject_id=snapshot.subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        output_schema_version="check-result-v1",
        task_id="check:profitability-decline",
        check_id="profitability-decline",
        status=CheckStatus.RISK,
        decision_summary="多期利润和利润率均持续下滑。",
        risk_items=(
            RiskItem(
                risk_id="risk:profitability-decline",
                check_id="profitability-decline",
                title="盈利能力持续下降",
                status=CheckStatus.RISK,
                risk_class=RiskClass.ATTENTION,
                severity=Severity.HIGH,
                conclusion="近三年净利润及利润率持续下降。",
                evidence_ids=tuple(item.evidence_id for item in refs),
                confidence=0.92,
            ),
        ),
        fact_evidence_refs=refs,
        confidence=0.92,
        prompt_version="investigation-core-v1+single-investigator-v1",
        submission_version=1,
    )


def ledger() -> BudgetLedger:
    return BudgetLedger(
        RunBudget(
            max_tool_calls=80,
            max_concurrency=1,
            timeout_seconds=30,
            max_repair_rounds=0,
            max_llm_requests=80,
            max_input_tokens=10_000,
            max_output_tokens=10_000,
            max_total_tokens=20_000,
            max_schema_retries=2,
            max_snapshot_reads=40,
        )
    )


@pytest.mark.asyncio
async def test_single_react_agent_reads_one_snapshot_submits_all_checks_and_self_checks() -> None:
    model = ScriptedModel(complete_responses())
    budget = ledger()
    agent = SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle())

    await Runner.start()
    try:
        completed = await agent.run(
            snapshot=context_snapshot(),
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            budget_ledger=budget,
            run_id="run-single-formal",
            model_name="single-scripted-model",
            model_provider="scripted",
            model=cast(Any, model),
            timeout_seconds=20,
            max_iterations=40,
        )
    finally:
        await Runner.stop()

    assert completed.agent_result.agent_id == "single-investigator"
    assert completed.agent_result.task_ids == tuple(
        f"check:{item}" for item in CHECK_CATALOG.check_ids
    )
    assert len(completed.agent_result.check_results) == len(CHECK_CATALOG.checks)
    assert completed.self_check_completed is True
    assert {item.status for item in completed.agent_result.check_results} == {
        CheckStatus.NO_RISK,
        CheckStatus.INCONCLUSIVE,
    }
    assert budget.snapshot().llm_requests == model.calls
    assert budget.snapshot().provider_usage_requests == model.calls
    assert budget.snapshot().snapshot_reads == 1
    assert budget.snapshot().tool_calls == len(CHECK_CATALOG.checks) + 2
    assert all("system_prompt" not in event.payload for event in completed.events)


@pytest.mark.asyncio
async def test_single_agent_never_fills_missing_checks_after_model_stops() -> None:
    agent = SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle())
    model = ScriptedModel([message("finished too early")])

    await Runner.start()
    try:
        with pytest.raises(AgentExecutionError, match="missing fixed checks"):
            await agent.run(
                snapshot=context_snapshot(),
                runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
                budget_ledger=ledger(),
                run_id="run-single-incomplete",
                model_name="single-scripted-model",
                model_provider="scripted",
                model=cast(Any, model),
                timeout_seconds=20,
                max_iterations=40,
            )
    finally:
        await Runner.stop()


def test_single_builder_exposes_no_external_or_multi_agent_tools() -> None:
    agent = SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle())
    bindings = agent.build_react_agent(
        snapshot=context_snapshot(),
        budget_ledger=ledger(),
        run_id="run-single-builder",
        model_name="qwen-plus",
        model_provider="OpenAI",
        model=cast(Any, ScriptedModel([message("unused")])),
        model_api_key="test-only",
        model_base_url="https://model.example/v1",
        model_temperature=0.25,
        model_timeout_seconds=42,
        max_iterations=40,
    )

    try:
        assert {item.name for item in bindings.agent.ability_manager.list()} == {
            "read_assigned_snapshot_context",
            "submit_check_result",
            "submit_investigation_self_check",
        }
        abilities = {item.name: item for item in bindings.agent.ability_manager.list()}
        submit_schema = abilities["submit_check_result"].input_params
        result_ref = submit_schema["properties"]["result"]["$ref"]
        result_name = result_ref.rsplit("/", maxsplit=1)[-1]
        result_properties = submit_schema["$defs"][result_name]["properties"]
        assert "snapshot_id" not in result_properties
        assert abilities["submit_investigation_self_check"].input_params["properties"] == {}
        client_config = bindings.agent._config.model_client_config
        request_config = bindings.agent._config.model_config_obj
        assert client_config is not None
        assert client_config.client_provider == JINDIAO_OPENAI_COMPATIBLE_PROVIDER
        assert client_config.upstream_provider == "OpenAI"
        assert client_config.api_base == "https://model.example/v1"
        assert client_config.api_key == "test-only"
        assert client_config.timeout == 42
        assert request_config is not None
        assert request_config.model_name == "qwen-plus"
        assert request_config.temperature == 0.25
        assert bindings.agent._config.parallel_tool_calls is True
    finally:
        bindings.agent.ability_manager.teardown_tools()


@pytest.mark.asyncio
async def test_single_agent_submits_profitability_decline_risk_from_required_evidence() -> None:
    snapshot = profitability_snapshot()
    responses = _replace_check_submission(
        complete_responses(),
        result=profitability_risk_result(snapshot),
    )
    budget = ledger()

    await Runner.start()
    try:
        completed = await SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle()).run(
            snapshot=snapshot,
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            budget_ledger=budget,
            run_id="run-single-profitability",
            model_name="single-scripted-model",
            model_provider="scripted",
            model=cast(Any, ScriptedModel(responses)),
            timeout_seconds=20,
            max_iterations=40,
        )
    finally:
        await Runner.stop()

    result = next(
        item
        for item in completed.agent_result.check_results
        if item.check_id == "profitability-decline"
    )
    assert result.status is CheckStatus.RISK
    assert result.risk_items[0].evidence_ids == (
        "ev-financial-summary",
        "ev-income-statement",
    )
    assert budget.snapshot().snapshot_reads == 1


@pytest.mark.asyncio
async def test_single_agent_converges_to_inconclusive_for_conflicting_evidence() -> None:
    snapshot = profitability_snapshot(conflicted_registration=True)
    base = complete_responses()
    original = CheckResult(
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        subject_id=snapshot.subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        output_schema_version="check-result-v1",
        task_id="check:registration-status-normal",
        check_id="registration-status-normal",
        status=CheckStatus.NO_RISK,
        decision_summary="工商登记状态正常。",
        fact_evidence_refs=(
            FactEvidenceRef(
                evidence_id="ev-registration",
                fact_path="governance.registration.registration_status",
                summary="工商状态为存续",
            ),
        ),
        confidence=0.95,
        prompt_version="investigation-core-v1+single-investigator-v2",
        submission_version=1,
    )
    conflicted = original.model_copy(
        update={
            "status": CheckStatus.INCONCLUSIVE,
            "decision_summary": "登记记录存在待解冲突。",
            "conflicts": ("ev-registration",),
        }
    )
    responses = _replace_check_submission(base, result=conflicted)

    await Runner.start()
    try:
        completed = await SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle()).run(
            snapshot=snapshot,
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            budget_ledger=ledger(),
            run_id="run-single-conflict",
            model_name="single-scripted-model",
            model_provider="scripted",
            model=cast(Any, ScriptedModel(responses)),
            timeout_seconds=20,
            max_iterations=40,
        )
    finally:
        await Runner.stop()

    registration = completed.agent_result.check_results[0]
    assert registration.status is CheckStatus.INCONCLUSIVE
    assert registration.conflicts == ("ev-registration",)


@pytest.mark.asyncio
async def test_single_agent_allows_model_to_correct_invalid_schema_once() -> None:
    responses = complete_responses()
    valid = json.loads(responses[1].tool_calls[0].arguments)["result"]
    invalid = {**valid, "status": "risk", "risk_items": []}
    responses.insert(
        1,
        message(
            tool_calls=[
                ToolCall(
                    id="submit-registration-invalid",
                    type="function",
                    name="submit_check_result",
                    arguments=json.dumps({"result": invalid}, ensure_ascii=False),
                )
            ]
        ),
    )
    budget = ledger()

    await Runner.start()
    try:
        completed = await SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle()).run(
            snapshot=context_snapshot(),
            runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
            budget_ledger=budget,
            run_id="run-single-schema-correction",
            model_name="single-scripted-model",
            model_provider="scripted",
            model=cast(Any, ScriptedModel(responses)),
            timeout_seconds=20,
            max_iterations=40,
        )
    finally:
        await Runner.stop()

    assert completed.agent_result.check_results[0].status is CheckStatus.NO_RISK
    assert budget.snapshot().schema_retries == 1


@pytest.mark.asyncio
async def test_single_agent_stops_and_cleans_tools_when_llm_budget_is_exhausted() -> None:
    constrained = BudgetLedger(ledger().budget.model_copy(update={"max_llm_requests": 1}))
    target = SingleInvestigatorAgent(prompt_bundle=load_prompt_bundle())

    await Runner.start()
    try:
        with pytest.raises(Exception, match="LLM-request budget exhausted"):
            await target.run(
                snapshot=context_snapshot(),
                runtime=OpenJiuwenAgentExecutionRuntime(clock=lambda: NOW),
                budget_ledger=constrained,
                run_id="run-single-budget-exhausted",
                model_name="single-scripted-model",
                model_provider="scripted",
                model=cast(Any, ScriptedModel(complete_responses())),
                timeout_seconds=20,
                max_iterations=40,
            )
    finally:
        await Runner.stop()

    assert constrained.snapshot().llm_requests == 1
    assert constrained.snapshot().exhausted_reason == ("orchestration LLM-request budget exhausted")
