# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from jindiao.api.event_mapper import EventMapper
from jindiao.application.errors import EntityNotFoundError
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.events import EventSequencer
from jindiao.contracts.execution import ExecutionCost
from jindiao.contracts.results import DueDiligenceRequest
from jindiao.observability import RunArtifactStore, RunMetricsCollector
from jindiao.orchestration.base import TeamRuntimeEvent
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_jsonl_trace_correlates_concurrent_events_and_has_stable_sequence(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path, clock=lambda: NOW)
    trace = store.begin(request_id="request-1", run_id="run-1")

    def emit(index: int) -> None:
        trace.emit(
            "tool.completed",
            agent_id=f"agent-{index % 2}",
            task_id=f"task-{index}",
            tool_id="tyc-company-detail",
            evidence_id=f"evidence-{index}",
            attributes={"duration_ms": index, "result_count": 1},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(emit, range(30)))

    records = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert [item["sequence"] for item in records] == list(range(1, 31))
    assert all(item["request_id"] == "request-1" and item["run_id"] == "run-1" for item in records)
    assert {item["task_id"] for item in records} == {f"task-{index}" for index in range(30)}
    assert {item["evidence_id"] for item in records} == {f"evidence-{index}" for index in range(30)}


def test_trace_uses_attribute_allowlist_and_redacts_sensitive_values(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path, clock=lambda: NOW)
    trace = store.begin(request_id="request-1", run_id="run-1")
    trace.emit(
        "run.failed",
        attributes={
            "error_code": "source_authentication_failed",
            "message": "Authorization: Bearer top-secret-value",
            "prompt": "private prompt",
            "reasoning_content": "private chain of thought",
            "api_key": "top-secret-value",
        },
    )

    raw = trace.path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["attributes"] == {
        "error_code": "source_authentication_failed",
        "message": "[REDACTED]",
    }
    assert "top-secret-value" not in raw
    assert "private prompt" not in raw
    assert "private chain of thought" not in raw


def test_mapped_public_event_uses_the_same_safe_payload_in_jsonl(tmp_path: Path) -> None:
    trace = RunArtifactStore(tmp_path, clock=lambda: NOW).begin(
        request_id="request-1",
        run_id="run-1",
    )
    event = EventMapper().map(
        TeamRuntimeEvent(
            event_type="snapshot.read",
            member_name="corporate-agent",
            payload={
                "snapshot": {
                    "snapshot_id": "snapshot-1",
                    "evidence_ids": ["ev-1"],
                    "reasoning": "private chain of thought",
                    "raw_mcp_response": "private response",
                }
            },
        ),
        EventSequencer(request_id="request-1", run_id="run-1"),
    )
    assert event is not None

    trace.emit_run_event(event)

    raw = trace.path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["event_type"] == "snapshot.read"
    assert record["payload"]["snapshot"]["snapshot_id"] == "snapshot-1"
    assert "ev-1" in raw
    assert "private chain of thought" not in raw
    assert "private response" not in raw


def test_metrics_capture_phases_first_evidence_and_resource_counts() -> None:
    timestamps = iter((10.0, 10.1, 10.4, 10.7, 11.0))
    metrics = RunMetricsCollector(monotonic=lambda: next(timestamps))

    metrics.start_phase("orchestration")
    metrics.mark_first_valid_evidence()
    metrics.finish_phase("orchestration")
    metrics.record_resources(tool_calls=7, token_count=123, conflicts=2, repairs=1)
    snapshot = metrics.finish()

    assert snapshot["end_to_end_duration_ms"] == 1000
    assert snapshot["first_valid_evidence_ms"] == 400
    assert snapshot["phase_duration_ms"] == {"orchestration": 600}
    assert snapshot["tool_calls"] == 7
    assert snapshot["token_count"] == 123
    assert snapshot["conflicts_detected"] == 2
    assert snapshot["repairs_requested"] == 1


def test_metrics_distinguish_authoritative_zero_from_unobserved_usage() -> None:
    unknown = RunMetricsCollector().finish()
    assert unknown["token_count"] == 0
    assert unknown["execution_cost"] is None
    assert unknown["provider_usage_complete"] is False

    measured = RunMetricsCollector()
    measured.record_execution_cost(ExecutionCost.zero(), provider_usage_complete=True)
    known_zero = measured.finish()
    assert known_zero["execution_cost"] == ExecutionCost.zero().model_dump(mode="json")
    assert known_zero["token_count"] == 0
    assert known_zero["provider_usage_complete"] is True


@pytest.mark.asyncio
async def test_service_writes_result_report_metrics_and_safe_trace(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            artifact_root=artifact_root,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        id_factory=iter(("request-1", "run-1")).__next__,
        artifact_store=RunArtifactStore(artifact_root, clock=lambda: NOW),
    )

    result = await service.run(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="乐视网信息技术（北京）股份有限公司"),
            scenario_id="normal-enterprise",
        )
    )

    public_result = result.model_dump_json()
    assert '"schema_version":"prototype-v1"' in public_result
    assert '"source_label"' in public_result
    assert '"system_prompt"' not in public_result
    assert '"reasoning"' not in public_result
    assert '"raw_mcp_response"' not in public_result
    assert '"authorization"' not in public_result

    run_root = artifact_root / "run-1"
    persisted = json.loads((run_root / "result.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_root / "metrics.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (run_root / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert persisted["meta"]["run_id"] == result.meta.run_id
    assert (run_root / "report.md").read_text(encoding="utf-8") == result.report_markdown
    assert metrics["tool_calls"] >= 0
    internal = json.loads((run_root / "investigation.json").read_text(encoding="utf-8"))
    assert set(internal) == {"reviewed", "agent_results", "execution_cost"}
    assert set(internal["execution_cost"]) == {
        "shared_acquisition",
        "investigation",
        "reporting",
    }
    assert {item["event_type"] for item in events} >= {
        "run.started",
        "agent.completed",
        "task.completed",
        "tool.summary",
        "evidence.collected",
        "run.completed",
    }
    assert all(item["request_id"] == "request-1" for item in events)


@pytest.mark.asyncio
async def test_failed_run_is_observed_without_sensitive_request_data(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            data_source_mode="mock",
            artifact_root=artifact_root,
            tianyancha_authorization=SecretStr("Bearer top-secret-value"),
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        id_factory=iter(("request-2", "run-2")).__next__,
        artifact_store=RunArtifactStore(artifact_root, clock=lambda: NOW),
    )

    with pytest.raises(EntityNotFoundError):
        await service.run(
            DueDiligenceRequest(enterprise=EnterpriseInput(company_name="不存在的企业有限公司"))
        )

    run_root = artifact_root / "run-2"
    raw = "\n".join(
        path.read_text(encoding="utf-8") for path in run_root.iterdir() if path.is_file()
    )
    events = [
        json.loads(line)
        for line in (run_root / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event_type"] == "run.failed"
    assert events[-1]["attributes"]["error_code"] == "entity_not_found"
    assert "top-secret-value" not in raw
