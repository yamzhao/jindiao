from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

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
    RepairTask,
    ReviewIssue,
    RiskClass,
    Severity,
)
from jindiao.investigation import CHECK_CATALOG
from jindiao.investigation.blackboard import (
    ReviewSubmission,
    SubmissionBlackboard,
    SubmissionGrant,
)
from jindiao.reporting.catalog import REPORT_CATALOG

NOW = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def snapshot() -> EnterpriseContextSnapshot:
    subject = ResolvedSubject(
        subject_id="tyc:blackboard-1",
        company_name="提交黑板测试有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )
    registration_evidence = Evidence(
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
    submodules = []
    for submodule_id in REPORT_CATALOG.submodule_ids:
        if submodule_id == "registration":
            submodules.append(
                SubmoduleContext(
                    submodule_id=submodule_id,
                    availability=SubmoduleAvailability.AVAILABLE,
                    completeness=CoverageCompleteness.COMPLETE,
                    facts={"registration_status": "存续"},
                    evidence_ids=(registration_evidence.evidence_id,),
                )
            )
        elif submodule_id == "financial_summary":
            submodules.append(
                SubmoduleContext(
                    submodule_id=submodule_id,
                    availability=SubmoduleAvailability.SOURCE_ERROR,
                    completeness=CoverageCompleteness.UNKNOWN,
                    unresolved_gap_ids=("gap:financial_summary:source_error",),
                )
            )
        else:
            submodules.append(
                SubmoduleContext(
                    submodule_id=submodule_id,
                    availability=SubmoduleAvailability.NOT_REQUESTED,
                    completeness=CoverageCompleteness.UNKNOWN,
                )
            )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:blackboard",
        snapshot_sha256="a" * 64,
        subject=subject,
        report_as_of=REPORT_AS_OF,
        created_at=NOW,
        report_catalog_version=REPORT_CATALOG.catalog_version,
        source_manifest_version="manifest-v1",
        submodules=tuple(submodules),
        evidence=(registration_evidence,),
        supplement_tasks=(),
        unresolved_gaps=("gap:financial_summary:source_error",),
        unresolved_conflicts=(),
    )


def board(context_snapshot: EnterpriseContextSnapshot | None = None) -> SubmissionBlackboard:
    return SubmissionBlackboard(
        run_id="run-blackboard",
        snapshot=context_snapshot or snapshot(),
        check_catalog=CHECK_CATALOG,
        grants=(
            SubmissionGrant(
                agent_id="corporate-agent",
                task_id="task-registration",
                check_ids=("registration-status-normal",),
            ),
            SubmissionGrant(
                agent_id="financial-agent",
                task_id="task-profitability",
                check_ids=("profitability-decline",),
            ),
        ),
        reviewer_agent_ids=("reviewer",),
        clock=lambda: NOW,
    )


def registration_result(*, version: int = 1) -> CheckResult:
    evidence = FactEvidenceRef(
        evidence_id="ev-registration",
        fact_path="governance.registration.registration_status",
        summary="工商状态为存续",
    )
    return CheckResult(
        snapshot_id=snapshot().snapshot_id,
        snapshot_sha256=snapshot().snapshot_sha256,
        subject_id=snapshot().subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        output_schema_version="check-result-v1",
        task_id="task-registration",
        check_id="registration-status-normal",
        status=CheckStatus.NO_RISK,
        decision_summary="工商登记状态正常。",
        fact_evidence_refs=(evidence,),
        confidence=0.95,
        prompt_version="investigation-core-v1",
        submission_version=version,
    )


def nested_tool_properties(schema: dict[str, object], field: str) -> dict[str, object]:
    properties = schema["properties"]
    assert isinstance(properties, dict)
    field_schema = properties[field]
    assert isinstance(field_schema, dict)
    ref = field_schema["$ref"]
    assert isinstance(ref, str)
    definition = str(ref).rsplit("/", maxsplit=1)[-1]
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    definition_schema = definitions[definition]
    assert isinstance(definition_schema, dict)
    nested_properties = definition_schema["properties"]
    assert isinstance(nested_properties, dict)
    return nested_properties


@pytest.mark.asyncio
async def test_submit_check_result_validates_scope_versions_evidence_and_idempotency() -> None:
    target = board()
    result = registration_result()
    submit_tool = target.build_submit_check_result_tool(agent_id="corporate-agent")

    accepted_payload = await submit_tool.invoke({"result": result.model_dump(mode="json")})
    accepted = target.latest_result("registration-status-normal")
    replay = await target.submit_check_result(
        agent_id="corporate-agent",
        result=result,
    )

    assert submit_tool.card.name == "submit_check_result"
    assert accepted_payload["accepted"] is True
    assert accepted_payload["idempotent_replay"] is False
    assert replay.idempotent_replay is True
    assert target.submission_count == 1
    assert target.latest_result("registration-status-normal") == result
    assert accepted == result

    wrong_snapshot = result.model_copy(update={"snapshot_id": "snapshot:other"})
    with pytest.raises(ValueError, match="snapshot"):
        await target.submit_check_result(
            agent_id="corporate-agent",
            result=wrong_snapshot,
        )
    with pytest.raises(ValueError, match="owner"):
        await target.submit_check_result(agent_id="financial-agent", result=result)
    unknown_evidence = result.model_copy(
        update={
            "fact_evidence_refs": (
                FactEvidenceRef(
                    evidence_id="ev-unknown",
                    fact_path="governance.registration.registration_status",
                    summary="未知证据",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="unknown Evidence"):
        await target.submit_check_result(
            agent_id="corporate-agent",
            result=unknown_evidence,
        )


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_injects_immutable_submission_envelope() -> None:
    target = board()
    tool = target.build_submit_bound_check_result_tool(
        agent_id="corporate-agent",
        prompt_version="investigation-core-v1+corporate-v3",
    )
    decision = {
        "check_id": "registration-status-normal",
        "status": "no_risk",
        "decision_summary": "工商登记状态正常。",
        "risk_items": [],
        "fact_evidence_refs": [
            {
                "evidence_id": "ev-registration",
                "fact_path": "governance.registration.registration_status",
                "summary": "工商状态为存续",
            }
        ],
        "missing_evidence": [],
        "conflicts": [],
        "confidence": 0.95,
    }

    result_properties = nested_tool_properties(tool.card.input_params, "result")
    receipt = await tool.invoke({"result": decision})
    replay = await tool.invoke({"result": decision})
    accepted = target.latest_result("registration-status-normal")

    immutable_fields = {
        "snapshot_id",
        "snapshot_sha256",
        "subject_id",
        "check_catalog_version",
        "output_schema_version",
        "task_id",
        "prompt_version",
        "submission_version",
    }
    assert immutable_fields.isdisjoint(result_properties)
    assert receipt["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert accepted is not None
    assert accepted.snapshot_id == target.snapshot.snapshot_id
    assert accepted.snapshot_sha256 == target.snapshot.snapshot_sha256
    assert accepted.subject_id == target.snapshot.subject.subject_id
    assert accepted.check_catalog_version == CHECK_CATALOG.catalog_version
    assert (
        accepted.output_schema_version
        == CHECK_CATALOG.get("registration-status-normal").output_schema_version
    )
    assert accepted.task_id == "task-registration"
    assert accepted.prompt_version == "investigation-core-v1+corporate-v3"
    assert accepted.submission_version == 1


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_downgrades_source_blocked_no_risk() -> None:
    target = board()
    tool = target.build_submit_bound_check_result_tool(
        agent_id="financial-agent",
        prompt_version="investigation-core-v1+financial-v3",
    )

    payload = {
        "result": {
            "check_id": "profitability-decline",
            "status": "no_risk",
            "decision_summary": "未发现盈利能力下滑。",
            "risk_items": [],
            "fact_evidence_refs": [],
            "missing_evidence": [],
            "conflicts": [],
            "confidence": 0.8,
        }
    }
    receipt = await tool.invoke(payload)
    replay = await tool.invoke(payload)
    accepted = target.latest_result("profitability-decline")

    assert receipt["accepted"] is True
    assert receipt["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert target.submission_count == 1
    assert accepted is not None
    assert accepted.status is CheckStatus.INCONCLUSIVE
    assert accepted.decision_summary == "证据门禁未通过。无法支持 risk/no_risk 结论。"
    assert accepted.risk_items == ()
    assert accepted.missing_evidence == (
        "required:financial_summary:source_error",
        "required:income_statement:not_requested",
        "minimum_evidence_count:2",
    )


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_downgrades_declared_missing_evidence() -> None:
    target = board()
    tool = target.build_submit_bound_check_result_tool(
        agent_id="corporate-agent",
        prompt_version="investigation-core-v1+corporate-v3",
    )

    await tool.invoke(
        {
            "result": {
                "check_id": "registration-status-normal",
                "status": "no_risk",
                "decision_summary": "工商登记状态正常, 但仍声明存在缺口。",
                "risk_items": [],
                "fact_evidence_refs": [
                    {
                        "evidence_id": "ev-registration",
                        "fact_path": "governance.registration.registration_status",
                        "summary": "工商状态为存续",
                    }
                ],
                "missing_evidence": ["liquidation"],
                "conflicts": [],
                "confidence": 0.8,
            }
        }
    )
    accepted = target.latest_result("registration-status-normal")

    assert accepted is not None
    assert accepted.status is CheckStatus.INCONCLUSIVE
    assert accepted.risk_items == ()
    assert accepted.missing_evidence == ("liquidation",)


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_downgrades_unsupported_risk_and_keeps_facts() -> None:
    base = snapshot()
    submodules = tuple(
        item.model_copy(
            update={
                "availability": SubmoduleAvailability.AVAILABLE,
                "completeness": CoverageCompleteness.PARTIAL,
                "evidence_ids": ("ev-registration",),
                "unresolved_gap_ids": (),
            }
        )
        if item.submodule_id == "financial_summary"
        else item
        for item in base.submodules
    )
    target = board(
        EnterpriseContextSnapshot.model_validate(
            {
                **base.model_dump(mode="json"),
                "submodules": [item.model_dump(mode="json") for item in submodules],
            }
        )
    )
    tool = target.build_submit_bound_check_result_tool(
        agent_id="financial-agent",
        prompt_version="investigation-core-v1+financial-v3",
    )
    fact = {
        "evidence_id": "ev-registration",
        "fact_path": "operations.financial_summary.observation",
        "summary": "仅有一份可用事实。",
    }

    await tool.invoke(
        {
            "result": {
                "check_id": "profitability-decline",
                "status": "risk",
                "decision_summary": "判断为盈利下滑。",
                "risk_items": [
                    {
                        "risk_id": "risk:unsupported-profitability",
                        "title": "盈利能力下滑",
                        "risk_class": RiskClass.ATTENTION.value,
                        "severity": Severity.HIGH.value,
                        "conclusion": "判断为盈利下滑。",
                        "evidence_ids": ["ev-registration"],
                        "confidence": 0.7,
                    }
                ],
                "fact_evidence_refs": [fact],
                "missing_evidence": [],
                "conflicts": [],
                "confidence": 0.7,
            }
        }
    )
    accepted = target.latest_result("profitability-decline")

    assert accepted is not None
    assert accepted.status is CheckStatus.INCONCLUSIVE
    assert accepted.risk_items == ()
    assert tuple(item.evidence_id for item in accepted.fact_evidence_refs) == ("ev-registration",)
    assert "minimum_evidence_count:2" in accepted.missing_evidence


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_deduplicates_fact_refs_by_evidence_id() -> None:
    target = board()
    tool = target.build_submit_bound_check_result_tool(
        agent_id="corporate-agent",
        prompt_version="investigation-core-v1+corporate-v3",
    )
    fact = {
        "evidence_id": "ev-registration",
        "fact_path": "governance.registration.registration_status",
        "summary": "工商状态为存续",
    }

    await tool.invoke(
        {
            "result": {
                "check_id": "registration-status-normal",
                "status": "no_risk",
                "decision_summary": "工商登记状态正常。",
                "risk_items": [],
                "fact_evidence_refs": [fact, fact],
                "missing_evidence": [],
                "conflicts": [],
                "confidence": 0.95,
            }
        }
    )
    accepted = target.latest_result("registration-status-normal")

    assert accepted is not None
    assert accepted.fact_evidence_refs == (FactEvidenceRef.model_validate(fact),)


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_still_rejects_unknown_evidence() -> None:
    target = board()
    tool = target.build_submit_bound_check_result_tool(
        agent_id="corporate-agent",
        prompt_version="investigation-core-v1+corporate-v3",
    )

    with pytest.raises(ValueError, match="unknown Evidence"):
        await tool.invoke(
            {
                "result": {
                    "check_id": "registration-status-normal",
                    "status": "no_risk",
                    "decision_summary": "工商登记状态正常。",
                    "risk_items": [],
                    "fact_evidence_refs": [
                        {
                            "evidence_id": "ev-unknown",
                            "fact_path": "governance.registration.registration_status",
                            "summary": "未知证据",
                        }
                    ],
                    "missing_evidence": [],
                    "conflicts": [],
                    "confidence": 0.95,
                }
            }
        )


@pytest.mark.asyncio
async def test_runtime_bound_check_tool_still_rejects_risk_without_risk_item() -> None:
    target = board()
    tool = target.build_submit_bound_check_result_tool(
        agent_id="corporate-agent",
        prompt_version="investigation-core-v1+corporate-v3",
    )

    with pytest.raises(ValidationError, match="risk check requires"):
        await tool.invoke(
            {
                "result": {
                    "check_id": "registration-status-normal",
                    "status": "risk",
                    "decision_summary": "工商登记状态异常。",
                    "risk_items": [],
                    "fact_evidence_refs": [
                        {
                            "evidence_id": "ev-registration",
                            "fact_path": "governance.registration.registration_status",
                            "summary": "工商状态异常",
                        }
                    ],
                    "missing_evidence": [],
                    "conflicts": [],
                    "confidence": 0.8,
                }
            }
        )


@pytest.mark.asyncio
async def test_evidence_gate_rejects_no_risk_for_source_failure_but_accepts_inconclusive() -> None:
    target = board()
    no_risk = registration_result().model_copy(
        update={
            "task_id": "task-profitability",
            "check_id": "profitability-decline",
            "fact_evidence_refs": (),
            "confidence": 0.1,
        }
    )

    with pytest.raises(ValueError, match=r"source_error|missing required Evidence"):
        await target.submit_check_result(agent_id="financial-agent", result=no_risk)

    inconclusive = no_risk.model_copy(
        update={
            "status": CheckStatus.INCONCLUSIVE,
            "decision_summary": "财务来源失败无法判断盈利趋势。",
            "missing_evidence": ("financial_summary", "income_statement"),
        }
    )
    receipt = await target.submit_check_result(
        agent_id="financial-agent",
        result=inconclusive,
    )

    assert receipt.accepted is True


@pytest.mark.asyncio
async def test_evidence_gate_requires_inconclusive_for_unresolved_conflict() -> None:
    base = snapshot()
    submodules = tuple(
        item.model_copy(update={"conflict_evidence_ids": ("ev-registration",)})
        if item.submodule_id == "registration"
        else item
        for item in base.submodules
    )
    conflicted = EnterpriseContextSnapshot.model_validate(
        {
            **base.model_dump(mode="json"),
            "submodules": [item.model_dump(mode="json") for item in submodules],
            "unresolved_conflicts": ["ev-registration"],
        }
    )
    target = board(conflicted)
    result = registration_result()

    with pytest.raises(ValueError, match="conflict"):
        await target.submit_check_result(agent_id="corporate-agent", result=result)

    inconclusive = result.model_copy(
        update={
            "status": CheckStatus.INCONCLUSIVE,
            "decision_summary": "工商来源存在待解冲突。",
            "conflicts": ("ev-registration",),
        }
    )
    receipt = await target.submit_check_result(
        agent_id="corporate-agent",
        result=inconclusive,
    )

    assert receipt.accepted is True


@pytest.mark.asyncio
async def test_submit_review_records_issues_and_targeted_repairs_without_final_score() -> None:
    target = board()
    await target.submit_check_result(
        agent_id="corporate-agent",
        result=registration_result(),
    )
    issue = ReviewIssue(
        issue_id="issue-registration-source",
        issue_type="evidence_conflict",
        message="需要重新确认登记状态的时点。",
        check_ids=("registration-status-normal",),
        evidence_ids=("ev-registration",),
        target_agent="corporate-agent",
    )
    repair = RepairTask(
        repair_id="repair-registration-source",
        issue_ids=(issue.issue_id,),
        target_agent="corporate-agent",
        requested_fields=("registration.registration_status",),
        required_evidence=("ev-registration",),
        attempt=1,
        max_attempts=1,
    )
    review = ReviewSubmission(
        snapshot_id=snapshot().snapshot_id,
        snapshot_sha256=snapshot().snapshot_sha256,
        subject_id=snapshot().subject.subject_id,
        check_catalog_version=CHECK_CATALOG.catalog_version,
        prompt_version="reviewer-v1",
        review_version=1,
        issues=(issue,),
        repair_tasks=(repair,),
    )

    review_tool = target.build_submit_review_tool(reviewer_agent_id="reviewer")
    receipt = await review_tool.invoke({"review": review.model_dump(mode="json")})

    assert review_tool.card.name == "submit_review"
    assert receipt["accepted"] is True
    assert target.latest_review == review
    assert target.repair_tasks == (repair,)
    with pytest.raises(ValidationError, match="final_score"):
        ReviewSubmission.model_validate({**review.model_dump(mode="json"), "final_score": 99})


@pytest.mark.asyncio
async def test_runtime_bound_review_tool_injects_immutable_review_envelope() -> None:
    target = board()
    tool = target.build_submit_bound_review_tool(
        reviewer_agent_id="reviewer",
        prompt_version="investigation-core-v1+reviewer-v3",
    )
    review: dict[str, object] = {"issues": [], "repair_tasks": []}

    review_properties = nested_tool_properties(tool.card.input_params, "review")
    receipt = await tool.invoke({"review": review})
    replay = await tool.invoke({"review": review})

    immutable_fields = {
        "snapshot_id",
        "snapshot_sha256",
        "subject_id",
        "check_catalog_version",
        "prompt_version",
        "review_version",
    }
    assert immutable_fields.isdisjoint(review_properties)
    assert receipt["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert target.latest_review is not None
    assert target.latest_review.snapshot_id == target.snapshot.snapshot_id
    assert target.latest_review.snapshot_sha256 == target.snapshot.snapshot_sha256
    assert target.latest_review.subject_id == target.snapshot.subject.subject_id
    assert target.latest_review.check_catalog_version == CHECK_CATALOG.catalog_version
    assert target.latest_review.prompt_version == "investigation-core-v1+reviewer-v3"
    assert target.latest_review.review_version == 1


def test_runtime_bound_review_schema_limits_one_review_batch() -> None:
    target = board()
    tool = target.build_submit_bound_review_tool(
        reviewer_agent_id="reviewer",
        prompt_version="investigation-core-v1+reviewer-v3",
    )

    review_properties = nested_tool_properties(tool.card.input_params, "review")
    issues = review_properties["issues"]
    repairs = review_properties["repair_tasks"]

    assert isinstance(issues, dict)
    assert isinstance(repairs, dict)
    assert issues["maxItems"] == 4
    assert repairs["maxItems"] == 2


@pytest.mark.asyncio
async def test_runtime_bound_review_drops_repairs_requiring_unknown_evidence() -> None:
    target = board()
    tool = target.build_submit_bound_review_tool(
        reviewer_agent_id="reviewer",
        prompt_version="investigation-core-v1+reviewer-v3",
    )
    issue = ReviewIssue(
        issue_id="issue-missing-external-evidence",
        issue_type="evidence_sufficiency",
        message="冻结快照中没有对应证据, 无法在调查阶段补采。",
        check_ids=("profitability-decline",),
        target_agent="financial-agent",
    )
    impossible_repair = RepairTask(
        repair_id="repair-missing-external-evidence",
        issue_ids=(issue.issue_id,),
        target_agent="financial-agent",
        requested_fields=("income_statement",),
        required_evidence=("ev-not-in-frozen-snapshot",),
        attempt=1,
        max_attempts=1,
    )

    receipt = await tool.invoke(
        {
            "review": {
                "issues": [issue.model_dump(mode="json")],
                "repair_tasks": [impossible_repair.model_dump(mode="json")],
            }
        }
    )

    assert receipt["accepted"] is True
    assert target.latest_review is not None
    assert target.latest_review.issues == (issue,)
    assert target.latest_review.repair_tasks == ()
