from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceStatus, SourceType
from jindiao.investigation.snapshot_access import SnapshotReadGrant, SnapshotReadToolset
from jindiao.orchestration import BudgetLedger, RunBudget

NOW = datetime(2026, 9, 5, 15, 0, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def expand_context(packed: dict[str, Any]) -> dict[str, Any]:
    """Test-side inverse of the documented model view; no source data mutation."""

    def table(value: dict[str, Any]) -> list[dict[str, Any]]:
        absent = {tuple(cell) for cell in value.get("missing", [])}
        return [
            {column: row[j] for j, column in enumerate(value["columns"]) if (i, j) not in absent}
            for i, row in enumerate(value["rows"])
        ]

    original = copy.deepcopy(packed)
    assert original.pop("format") == "snapshot-context-v2"
    facts = original.pop("fact_sets")
    metadata = table(original.pop("evidence_metadata_table"))
    evidence = {
        identity: {"evidence_id": identity, **metadata[meta_index]}
        for identity, meta_index in original.pop("evidence_table")["rows"]
    }
    for module in original["submodules"]:
        entry = facts[module.pop("facts_ref")]
        module["facts"] = copy.deepcopy(entry["fields"])
        if "records_table" in entry:
            module["facts"]["records"] = table(entry["records_table"])
        module["evidence_items"] = [evidence[key] for key in module.pop("evidence_ref_ids")]
    return original


def test_compact_context_is_lossless_and_small_for_large_repeated_snapshot() -> None:
    from jindiao.investigation.compact_context import compact_assigned_context

    records = [
        {
            "年度": str(2020 + i % 6),
            "营业收入": i,
            "active": False,
            "经营范围": "设备制造及销售" * 8,
            "unused": None,
        }
        for i in range(420)
    ]
    records[0].pop("unused")
    metadata = {
        "source_type": "tianyancha",
        "source_status": "verified_records",
        "source_tool": "get_financial_summary",
        "as_of_date": "2026-08-31",
        "is_mock": False,
        "confidence": 1,
        "supports_fields": ["financial.revenue"],
    }
    modules = [
        {
            "submodule_id": f"module-{i}",
            "facts": {
                "records": records,
                "record_count": 420,
                "source_metadata": {"unit": "元"},
                "records_table": {"literal": "source keys must be preserved"},
            },
            "evidence_items": [{"evidence_id": f"e{j}", **metadata} for j in range(420)],
            "evidence_ids": [f"e{j}" for j in range(420)],
        }
        for i in range(4)
    ]
    raw: dict[str, Any] = {
        "snapshot_id": "snapshot:test",
        "submodules": modules,
        "check_evidence_scope": {"check-1": ["e1"], "check-2": ["e2"]},
    }
    before = copy.deepcopy(raw)
    compact: dict[str, Any] = compact_assigned_context(raw)
    assert expand_context(compact) == raw
    assert raw == before
    assert (
        len(json.dumps(compact, ensure_ascii=False))
        < len(json.dumps(raw, ensure_ascii=False)) * 0.35
    )
    assert len(compact["fact_sets"]) == 1
    assert len(compact["evidence_metadata_table"]["rows"]) == 1


def test_compact_context_does_not_merge_distinct_evidence_or_sparse_record_values() -> None:
    from jindiao.investigation.compact_context import compact_assigned_context

    raw: dict[str, Any] = {
        "submodules": [
            {
                "submodule_id": "m",
                "facts": {
                    "records": [
                        {"a": 0},
                        {"a": None, "b": False},
                        {"b": ["x", {"$ref": "literal"}]},
                    ]
                },
                "evidence_items": [
                    {"evidence_id": "a", "is_mock": False},
                    {"evidence_id": "b", "is_mock": True},
                ],
            }
        ]
    }
    assert expand_context(compact_assigned_context(raw)) == raw


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
        for submodule_id in ACQUISITION_CATALOG.default_plan_ids
    )
    return EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:read-only",
        snapshot_sha256="a" * 64,
        subject=subject,
        report_as_of=REPORT_AS_OF,
        created_at=NOW,
        acquisition_catalog_version=ACQUISITION_CATALOG.catalog_version,
        planned_submodule_ids=ACQUISITION_CATALOG.default_plan_ids,
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
async def test_compact_assigned_context_exposes_exact_citation_scope_and_keeps_audit_ids() -> None:
    target = toolset()
    before = target.snapshot.canonical_json
    tools = target.build_tools(evidence_aliases={"ev-registration": "e0"})
    result = await tools[0].invoke({})
    assert result["format"] == "snapshot-context-v2"
    result = expand_context(result)
    assert result["check_evidence_scope"] == {"registration-status-normal": ["e0"]}
    registration = result["submodules"][0]
    assert registration["facts"] == target.snapshot.submodule("registration").facts
    assert registration["evidence_ids"] == ["e0"]
    assert registration["evidence_items"][0]["evidence_id"] == "e0"
    assert "source_ref" not in registration["evidence_items"][0]
    assert "content_hash" not in registration["evidence_items"][0]
    assert target.snapshot.canonical_json == before
    assert target.audit_records[0].evidence_ids == ("ev-registration",)


@pytest.mark.asyncio
async def test_compact_record_rows_identify_only_exact_authorized_evidence() -> None:
    target = toolset()
    modules = tuple(
        item.model_copy(
            update={
                "facts": {
                    "records": [
                        {"registration_status": "存续"},
                        {"registration_status": "未知记录"},
                    ]
                }
            }
        )
        if item.submodule_id == "registration"
        else item
        for item in target.snapshot.submodules
    )
    target.snapshot = target.snapshot.model_copy(update={"submodules": modules})
    before = target.snapshot.canonical_json
    result = await target.build_tools(evidence_aliases={"ev-registration": "e0"})[0].invoke({})
    registration = result["submodules"][0]
    assert registration["record_evidence_ids"] == [["e0"], []]
    assert "Bearer must-not-leak" not in json.dumps(result)
    assert target.snapshot.canonical_json == before


@pytest.mark.asyncio
async def test_alias_encoding_changes_references_not_literal_source_values() -> None:
    target = toolset()
    facts = {
        "literal_original": "ev-registration",
        "literal_alias": "e0",
        "records": [{"evidence_id": "ev-registration", "nested": ["ev-registration"]}],
    }
    target.snapshot = target.snapshot.model_copy(
        update={
            "submodules": tuple(
                module.model_copy(update={"facts": facts})
                if module.submodule_id == "registration"
                else module
                for module in target.snapshot.submodules
            )
        }
    )
    result = await target.build_tools(evidence_aliases={"ev-registration": "e0"})[0].invoke({})
    expanded = expand_context(result)
    assert expanded["submodules"][0]["facts"] == facts
    assert expanded["submodules"][0]["evidence_ids"] == ["e0"]
    assert expanded["submodules"][0]["evidence_items"][0]["evidence_id"] == "e0"


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
