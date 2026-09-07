from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceStatus, SourceType
from jindiao.investigation.snapshot_access import SnapshotReadGrant, SnapshotReadToolset
from jindiao.orchestration import BudgetLedger, RunBudget
from jindiao.reporting.catalog import REPORT_CATALOG

NOW = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def snapshot() -> EnterpriseContextSnapshot:
    subject = ResolvedSubject(
        subject_id="tyc:read-1",
        company_name="只读快照测试有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )
    evidence = Evidence(
        evidence_id="ev-registration",
        claim="工商存续状态",
        value={
            "registration_status": "存续",
            "authorization": "Bearer must-not-leak",
        },
        subject_id=subject.subject_id,
        source_type=SourceType.TIANYANCHA,
        source_status=SourceStatus.VERIFIED_RECORDS,
        source_tool="get_company_registration_info",
        source_record_id="registration-1",
        queried_at=NOW,
        as_of_date=REPORT_AS_OF,
        confidence=1,
        is_mock=False,
        supports_fields=("governance.registration",),
        raw_ref="mcp://tianyancha/registration/registration-1",
        source_chain=("mcp://tianyancha/get_company_registration_info",),
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
        )
        for submodule_id in REPORT_CATALOG.submodule_ids
    )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:read-only",
        snapshot_sha256="a" * 64,
        subject=subject,
        report_as_of=REPORT_AS_OF,
        created_at=NOW,
        report_catalog_version=REPORT_CATALOG.catalog_version,
        source_manifest_version="manifest-v1",
        submodules=submodules,
        evidence=(evidence,),
        supplement_tasks=(),
        unresolved_gaps=(),
        unresolved_conflicts=(),
    )


def toolset(*, budget_ledger: BudgetLedger | None = None) -> SnapshotReadToolset:
    return SnapshotReadToolset(
        snapshot=snapshot(),
        run_id="run-read-only",
        agent_id="corporate-agent",
        grants=(
            SnapshotReadGrant(
                task_id="task-registration",
                check_ids=("registration-status-normal",),
                allowed_submodule_ids=("registration",),
            ),
        ),
        budget_ledger=budget_ledger,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_snapshot_toolcards_return_only_task_granted_normalized_data() -> None:
    target = toolset()
    cards = target.build_tools()

    assert {item.card.name for item in cards} == {
        "read_assigned_snapshot_context",
        "read_snapshot_submodule",
        "read_snapshot_evidence",
    }
    by_name = {item.card.name: item for item in cards}
    submodule = await by_name["read_snapshot_submodule"].invoke(
        {
            "snapshot_id": snapshot().snapshot_id,
            "task_id": "task-registration",
            "submodule_id": "registration",
        }
    )
    evidence = await by_name["read_snapshot_evidence"].invoke(
        {
            "snapshot_id": snapshot().snapshot_id,
            "task_id": "task-registration",
            "submodule_id": "registration",
            "evidence_ids": ["ev-registration"],
        }
    )

    assert submodule["facts"] == {"registration_status": "存续"}
    assert submodule["evidence_ids"] == ["ev-registration"]
    assert "authorization" not in evidence["items"][0]["value"]
    assert "Bearer must-not-leak" not in str(evidence)
    assert "raw_response" not in str(evidence)
    assert [record.allowed for record in target.audit_records] == [True, True]


@pytest.mark.asyncio
async def test_assigned_context_batches_unique_authorized_submodules_and_evidence() -> None:
    ledger = BudgetLedger(
        RunBudget(
            max_tool_calls=2,
            max_concurrency=1,
            timeout_seconds=10,
            max_repair_rounds=0,
            max_snapshot_reads=2,
        ),
        monotonic=lambda: 100.0,
    )
    target = toolset(budget_ledger=ledger)
    tools = {item.card.name: item for item in target.build_tools()}

    assert tools["read_assigned_snapshot_context"].card.input_params.get("required", []) == []
    result = await tools["read_assigned_snapshot_context"].invoke({})

    assert result["snapshot_id"] == snapshot().snapshot_id
    assert result["subject_id"] == snapshot().subject.subject_id
    assert result["authorized_check_ids"] == ["registration-status-normal"]
    assert [item["submodule_id"] for item in result["submodules"]] == ["registration"]
    assert result["submodules"][0]["authorized_task_ids"] == ["task-registration"]
    assert result["submodules"][0]["facts"] == {"registration_status": "存续"}
    assert result["submodules"][0]["evidence_items"][0]["evidence_id"] == ("ev-registration")
    assert "value" not in result["submodules"][0]["evidence_items"][0]
    assert result["submodules"][0]["facts"] == {"registration_status": "存续"}
    assert "Bearer must-not-leak" not in str(result)
    assert ledger.snapshot().snapshot_reads == 1
    assert ledger.snapshot().tool_calls == 1
    assert len(target.audit_records) == 1


@pytest.mark.asyncio
async def test_snapshot_reader_rejects_cross_task_submodule_snapshot_and_evidence() -> None:
    target = toolset()
    before = target.snapshot.canonical_json

    with pytest.raises(ValueError, match="task"):
        await target.read_submodule(
            snapshot_id=snapshot().snapshot_id,
            task_id="task-financial",
            submodule_id="registration",
        )
    with pytest.raises(ValueError, match="submodule"):
        await target.read_submodule(
            snapshot_id=snapshot().snapshot_id,
            task_id="task-registration",
            submodule_id="financial_summary",
        )
    with pytest.raises(ValueError, match="snapshot"):
        await target.read_submodule(
            snapshot_id="snapshot:other",
            task_id="task-registration",
            submodule_id="registration",
        )
    with pytest.raises(ValueError, match="Evidence"):
        await target.read_evidence(
            snapshot_id=snapshot().snapshot_id,
            task_id="task-registration",
            submodule_id="registration",
            evidence_ids=("ev-other",),
        )

    assert target.snapshot.canonical_json == before
    assert all(not record.allowed for record in target.audit_records)


@pytest.mark.asyncio
async def test_authorized_snapshot_tool_reads_are_charged_to_the_shared_ledger() -> None:
    ledger = BudgetLedger(
        RunBudget(
            max_tool_calls=2,
            max_concurrency=1,
            timeout_seconds=10,
            max_repair_rounds=0,
            max_snapshot_reads=2,
        ),
        monotonic=lambda: 100.0,
    )
    target = toolset(budget_ledger=ledger)

    await target.read_submodule(
        snapshot_id=snapshot().snapshot_id,
        task_id="task-registration",
        submodule_id="registration",
    )
    await target.read_evidence(
        snapshot_id=snapshot().snapshot_id,
        task_id="task-registration",
        submodule_id="registration",
        evidence_ids=("ev-registration",),
    )

    assert ledger.snapshot().snapshot_reads == 2
    assert ledger.snapshot().tool_calls == 2
