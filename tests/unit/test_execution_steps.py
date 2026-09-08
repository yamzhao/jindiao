from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.api.event_mapper import EventMapper
from jindiao.application.execution_steps import ExecutionProjectionEvent, ExecutionStepProjector
from jindiao.contracts.events import EventSequencer, ExecutionEventType
from jindiao.contracts.execution_steps import EXECUTION_STEP_IDS, ExecutionStepSnapshot
from jindiao.contracts.product import ProductResult
from jindiao.contracts.results import OrchestrationMode
from jindiao.contracts.runs import RunStage
from jindiao.investigation.catalog import CHECK_CATALOG
from jindiao.orchestration.base import TeamRuntimeEvent


def result() -> ProductResult:
    return ProductResult.model_validate_json(
        Path("docs/api/samples/product-result-full-input.json").read_text()
    )


def step(event: ExecutionProjectionEvent) -> ExecutionStepSnapshot:
    return ExecutionStepSnapshot.model_validate(event.payload["step"])


def observe(
    projector: ExecutionStepProjector, event_type: str, member: str | None = None, **payload: Any
) -> tuple[ExecutionProjectionEvent, ...]:
    return projector.observe(
        TeamRuntimeEvent(event_type=event_type, member_name=member, payload=payload)
    )


@pytest.mark.parametrize("mode", list(OrchestrationMode))
def test_real_catalog_tasks_start_parallel_steps_and_preserve_actual_executors(
    mode: OrchestrationMode,
) -> None:
    projector = ExecutionStepProjector(mode=mode)
    plan = projector.start()
    assert plan.event_type == ExecutionEventType.PLAN_CREATED
    with pytest.raises(ValueError, match="already"):
        projector.start()
    acquisition = observe(
        projector,
        "agent.started",
        "context-agent",
        phase="acquisition",
        task_ids=[f"acquire:{i}" for i in ACQUISITION_CATALOG.default_plan_ids],
    )
    assert {step(e).step_id for e in acquisition} == set(EXECUTION_STEP_IDS[:5])
    assert all(e.stage == RunStage.ACQUISITION for e in acquisition)
    assert all(step(e).progress_message and step(e).progress_percent is None for e in acquisition)
    ids = [f"check:{i}" for i in CHECK_CATALOG.check_ids]
    events = observe(
        projector,
        "agent.started",
        "single" if mode.value == "single" else "specialist",
        phase="investigation",
        task_ids=ids,
    )
    assert events
    assert all("context-agent" in step(e).executor_ids for e in events)
    assert all(e.stage == RunStage.INVESTIGATION for e in events)
    assert (
        observe(
            projector,
            "agent.started",
            "single" if mode.value == "single" else "specialist",
            phase="investigation",
            task_ids=ids,
        )
        == ()
    )


@pytest.mark.parametrize(
    "name",
    [
        "agent.output",
        "agent.output.delta",
        "llm.reasoning",
        "llm_reasoning",
        "tool.result",
        "unknown.event",
    ],
)
def test_private_and_unknown_chunks_never_become_progress(name: str) -> None:
    projector = ExecutionStepProjector(mode=OrchestrationMode.SINGLE)
    projector.start()
    observe(projector, "agent.started", "agent", task_ids=[f"check:{CHECK_CATALOG.check_ids[0]}"])
    event = TeamRuntimeEvent(
        event_type=name,
        member_name="agent",
        payload={"task_id": f"check:{CHECK_CATALOG.check_ids[0]}", "content": "private scratchpad"},
    )
    assert projector.observe(event) == ()
    assert EventMapper().map(event, EventSequencer(request_id="r", run_id="r")) is None


def test_unmapped_tasks_do_not_fabricate_steps() -> None:
    projector = ExecutionStepProjector(mode=OrchestrationMode.MULTI)
    assert observe(projector, "report.started") == ()
    projector.start()
    assert observe(projector, "agent.started", "unknown", task_ids=["acquire:unknown"]) == ()
    assert observe(projector, "acquisition.completed", evidence_count=0) == ()


def test_completion_is_bounded_and_every_fact_uses_its_own_evidence() -> None:
    product = result()
    projector = ExecutionStepProjector(mode=OrchestrationMode.MULTI)
    projector.start()
    events = projector.complete(product)
    assert len(events) == 7
    assert all(event.event_type == ExecutionEventType.STEP_COMPLETED for event in events)
    snapshots = {step(event).step_id: step(event) for event in events}
    assert all(s.duration_ms is None for s in snapshots.values())
    for snapshot in snapshots.values():
        assert snapshot.state == "completed" and snapshot.progress_percent == 100
        ids = {s.evidence_id for s in snapshot.source_tags}
        assert ids <= {e.id for e in product.evidence}
        assert all(set(f.evidence_ids) <= ids for f in snapshot.key_facts)
        assert len(snapshot.key_facts) <= 3 and len(snapshot.gaps) <= 3
    risks = snapshots["cross-risk-review"]
    for fact in risks.key_facts:
        matching = next(
            r for r in product.risk_findings if r.risk_fact.startswith(fact.text.rstrip("…"))
        )
        assert set(fact.evidence_ids) <= {t.evidence_id for t in matching.evidence_tags}
    assert projector.complete(product) == ()
    assert observe(projector, "report.started") == ()


@pytest.mark.parametrize(
    ("status", "expected"),
    [("passed", "normal"), ("inconclusive", "inconclusive"), ("attention", "attention")],
)
def test_nested_verification_outcome_and_gaps(status: str, expected: str) -> None:
    raw = result().model_dump(mode="json")
    raw["risk_findings"] = []
    raw["summary"]["risk_count"] = 0
    raw["report"]["risk_points"]["finding_ids"] = []
    raw["report"]["company_profile"].update(status="complete", missing_fields=[])
    raw["report"]["external_verification"]["registration"].update(status=status)
    projector = ExecutionStepProjector(mode=OrchestrationMode.SINGLE)
    projector.start()
    snapshot = step(projector.complete(ProductResult.model_validate(raw))[0])
    assert snapshot.outcome == expected
    if status == "inconclusive":
        assert snapshot.gaps


def test_partial_module_stays_inconclusive_and_missing_data_is_not_zero() -> None:
    raw = result().model_dump(mode="json")
    raw["report"]["ownership"] = {"status": "unavailable"}
    projector = ExecutionStepProjector(mode=OrchestrationMode.SINGLE)
    projector.start()
    snapshot = step(projector.complete(ProductResult.model_validate(raw))[1])
    assert snapshot.outcome == "inconclusive"
    assert snapshot.gaps and not snapshot.key_facts


def test_failure_only_closes_active_steps_and_duration_is_observed() -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    projector = ExecutionStepProjector(mode=OrchestrationMode.MULTI, clock=lambda: now)
    projector.start()
    observe(projector, "report.started")
    now += timedelta(seconds=2)
    failures = projector.fail_active()
    assert len(failures) == 1
    snapshot = step(failures[0])
    assert snapshot.step_id == EXECUTION_STEP_IDS[-1]
    assert snapshot.state == "failed" and snapshot.outcome is None
    assert snapshot.duration_ms == 2000
    assert projector.fail_active() == ()


def test_empty_optional_summary_does_not_break_a_valid_result() -> None:
    raw = result().model_dump(mode="json")
    for risk in raw["risk_findings"]:
        risk["risk_fact"] = "   "
    raw["report"]["ownership"]["missing_fields"] = [
        {"field": "ownership", "reason": "not_provided", "message": "   "}
    ]
    projector = ExecutionStepProjector(mode=OrchestrationMode.SINGLE)
    projector.start()
    events = projector.complete(ProductResult.model_validate(raw))
    assert len(events) == 7
    assert not step(events[5]).key_facts
