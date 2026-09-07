from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.execution import ExecutionCost
from jindiao.contracts.results import DueDiligenceRequest, OrchestrationMode
from jindiao.evaluation.benchmark import BenchmarkRunner
from jindiao.evaluation.metrics import BenchmarkEvaluator, QualityBreakdown
from jindiao.evaluation.models import (
    BenchmarkAggregate,
    BenchmarkGainThreshold,
    BenchmarkManifest,
    BenchmarkRecord,
)
from jindiao.scenarios import ExpectedResultLoader, ScenarioRepository

NOW = datetime(2026, 9, 3, tzinfo=UTC)
ROOT = Path("mock_data/scenarios")


def test_checked_in_manifest_freezes_ten_scenarios_and_fair_configuration() -> None:
    manifest = BenchmarkManifest.from_file(Path("benchmarks/manifest.json"))

    assert manifest.schema_version == 2
    assert len(manifest.cases) == 10
    assert manifest.repetitions >= 2
    assert manifest.primary_metrics
    assert manifest.quality_weights.as_dict() == {
        "coverage": 0.30,
        "evidence_support": 0.25,
        "risk_quality": 0.20,
        "conflict_detection": 0.15,
        "report_structure": 0.10,
    }
    assert {
        threshold.metric: (
            threshold.direction,
            threshold.minimum_absolute_delta,
        )
        for threshold in manifest.gain_thresholds
    } == {"quality_score": ("increase", 0.02)}
    assert manifest.failure_retention.timeout is True
    assert manifest.failure_retention.schema_error is True
    assert manifest.failure_retention.partial is True
    assert manifest.failure_retention.include_in_aggregates is True
    assert {case.category for case in manifest.cases} >= {
        "normal",
        "judicial_risk",
        "operational_risk",
        "evidence_conflict",
        "capability_absent",
        "source_error",
        "entity_ambiguous",
        "report_gap",
    }
    assert manifest.fairness_fingerprint(OrchestrationMode.SINGLE) == manifest.fairness_fingerprint(
        OrchestrationMode.MULTI
    )


def test_manifest_rejects_unregistered_primary_threshold_or_failure_exclusion() -> None:
    manifest = BenchmarkManifest.from_file(Path("benchmarks/manifest.json"))
    payload = manifest.model_dump(mode="json")
    payload["gain_thresholds"] = []

    with pytest.raises(ValueError, match="gain_threshold"):
        BenchmarkManifest.model_validate(payload)

    payload = manifest.model_dump(mode="json")
    payload["failure_retention"]["partial"] = False
    with pytest.raises(ValueError):
        BenchmarkManifest.model_validate(payload)


@pytest.mark.asyncio
async def test_quality_metrics_are_weighted_and_recomputable_from_raw_counts(
    tmp_path: Path,
) -> None:
    service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            artifact_root=tmp_path,
        ),
        scenarios=ScenarioRepository(ROOT),
    )
    result = await service.run(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
            scenario_id="normal-enterprise",
        ),
        mode=OrchestrationMode.MULTI,
        request_id="request-metrics",
        run_id="run-metrics",
    )
    expected = ExpectedResultLoader(ROOT).load("normal-enterprise", version="v1.0.0")

    breakdown = BenchmarkEvaluator().evaluate(result, expected)

    assert breakdown.weights == {
        "coverage": 0.30,
        "evidence_support": 0.25,
        "risk_quality": 0.20,
        "conflict_detection": 0.15,
        "report_structure": 0.10,
    }
    assert breakdown.quality_score == 1.0
    assert QualityBreakdown.from_raw_counts(breakdown.raw_counts) == breakdown


def test_tampered_quality_score_fails_recomputation() -> None:
    breakdown = QualityBreakdown.from_raw_counts(
        {
            "coverage_completed": 3,
            "coverage_total": 4,
            "supported_findings": 4,
            "accepted_findings": 5,
            "expected_risk_hits": 2,
            "actual_risk_hits": 2,
            "matched_risk_hits": 1,
            "expected_conflicts": 1,
            "detected_conflicts": 1,
            "required_sections": 8,
            "completed_sections": 8,
        }
    )
    payload = breakdown.model_dump(mode="json")
    payload["quality_score"] = 0.999

    with pytest.raises(ValueError, match="recomputable"):
        QualityBreakdown.model_validate(payload)


def test_aggregate_does_not_claim_gain_without_primary_metric_improvement() -> None:
    raw_counts = {
        "coverage_completed": 4,
        "coverage_total": 4,
        "supported_findings": 2,
        "accepted_findings": 2,
        "expected_risk_hits": 0,
        "actual_risk_hits": 0,
        "matched_risk_hits": 0,
        "expected_conflicts": 0,
        "detected_conflicts": 0,
        "required_sections": 8,
        "completed_sections": 8,
    }
    quality = QualityBreakdown.from_raw_counts(raw_counts)
    records = tuple(
        BenchmarkRecord(
            case_id="case-1",
            scenario_id="normal-enterprise",
            repetition=1,
            mode=mode,
            success=True,
            fairness_fingerprint="same",
            duration_ms=10,
            first_valid_evidence_ms=5,
            tool_calls=5,
            token_count=0,
            conflicts_detected=0,
            repairs_requested=0,
            quality=quality,
        )
        for mode in OrchestrationMode
    )

    aggregate = BenchmarkAggregate.from_records(
        records,
        primary_metrics=("quality_score",),
    )

    assert aggregate.can_claim_collaboration_gain is False
    assert aggregate.claim == "未证明协作增益"


def test_preregistered_threshold_blocks_post_hoc_small_improvement() -> None:
    def quality(coverage_completed: int) -> QualityBreakdown:
        return QualityBreakdown.from_raw_counts(
            {
                "coverage_completed": coverage_completed,
                "coverage_total": 100,
                "supported_findings": 1,
                "accepted_findings": 1,
                "expected_risk_hits": 0,
                "actual_risk_hits": 0,
                "matched_risk_hits": 0,
                "expected_conflicts": 0,
                "detected_conflicts": 0,
                "required_sections": 8,
                "completed_sections": 8,
            }
        )

    records = tuple(
        BenchmarkRecord(
            case_id="case-1",
            scenario_id="normal-enterprise",
            repetition=1,
            mode=mode,
            success=True,
            fairness_fingerprint="same",
            duration_ms=10,
            first_valid_evidence_ms=5,
            tool_calls=1,
            token_count=1,
            conflicts_detected=0,
            repairs_requested=0,
            quality=quality(99 if mode is OrchestrationMode.SINGLE else 100),
        )
        for mode in OrchestrationMode
    )

    aggregate = BenchmarkAggregate.from_records(
        records,
        primary_metrics=("quality_score",),
        gain_thresholds=(
            BenchmarkGainThreshold(
                metric="quality_score",
                direction="increase",
                minimum_absolute_delta=0.02,
            ),
        ),
    )

    assert aggregate.deltas["quality_score"] == 0.003
    assert aggregate.can_claim_collaboration_gain is False
    assert aggregate.claim == "未证明协作增益"
    assert aggregate.gain_thresholds[0].minimum_absolute_delta == 0.02


def test_benchmark_metadata_cannot_serialize_prompts_reasoning_or_raw_responses() -> None:
    aggregate = BenchmarkAggregate(
        record_count=0,
        mode_metrics={},
        deltas={},
        confidence_intervals_95={},
        primary_metrics=("quality_score",),
        can_claim_collaboration_gain=False,
        claim="未证明协作增益",
        metadata={
            "code_version": "test",
            "system_prompt": "private prompt",
            "reasoning": "private chain of thought",
            "raw_web_response": "private webpage body",
            "api_key": "sk-private-benchmark-key",
        },
    )

    encoded = aggregate.model_dump_json()
    assert "code_version" in encoded
    assert "private prompt" not in encoded
    assert "private chain of thought" not in encoded
    assert "private webpage body" not in encoded
    assert "sk-private-benchmark-key" not in encoded


@pytest.mark.asyncio
async def test_runner_exports_paired_raw_and_recomputable_summaries(tmp_path: Path) -> None:
    source = BenchmarkManifest.from_file(Path("benchmarks/manifest.json"))
    selected = tuple(
        case
        for case in source.cases
        if case.scenario_id in {"normal-enterprise", "evidence-conflict"}
    )
    manifest = source.model_copy(update={"cases": selected, "repetitions": 1})
    output = tmp_path / "benchmark"

    summary = await BenchmarkRunner(
        manifest=manifest,
        scenarios_root=ROOT,
        output_root=output,
        clock=lambda: NOW,
        allow_partial_manifest=True,
    ).run()

    records = [
        BenchmarkRecord.model_validate_json(line)
        for line in (output / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 4
    assert len({record.fairness_fingerprint for record in records}) == 1
    assert all(
        QualityBreakdown.from_raw_counts(record.quality.raw_counts) == record.quality
        for record in records
        if record.quality is not None
    )
    assert all(record.fixed_check_total == 15 for record in records)
    assert all(0 <= record.fixed_check_coverage <= 1 for record in records)
    assert all(0 <= record.evidence_sufficiency <= 1 for record in records)
    assert all(0 <= record.structured_submission_success_rate <= 1 for record in records)
    assert summary.shared_metrics == {
        "mcp_calls": 0.0,
        "total_tokens": 0.0,
        "wall_time_ms": 0.0,
    }
    assert "fixed_check_coverage" in summary.mode_metrics["single"]
    assert "evidence_sufficiency" in summary.mode_metrics["single"]
    assert "investigation_total_tokens" in summary.mode_metrics["multi"]
    assert summary.formal_eligibility is not None
    assert summary.formal_eligibility.eligible is False
    assert "runner:deterministic_harness" in summary.formal_eligibility.reasons
    assert summary.can_claim_collaboration_gain is False
    assert (
        summary.mode_metrics["multi"]["quality_score"]
        > summary.mode_metrics["single"]["quality_score"]
    )
    assert (output / "summary.json").is_file()
    assert (output / "comparison.md").is_file()
    assert (output / "failures.json").is_file()
    comparison = (output / "comparison.md").read_text(encoding="utf-8")
    assert "协作增益" in comparison
    assert "仅诊断" in comparison
    assert "固定核查覆盖率" in comparison
    assert "Evidence 充分性" in comparison
    assert "共享采集成本" in comparison
    assert not json.loads((output / "summary.json").read_text(encoding="utf-8"))[
        "can_claim_collaboration_gain"
    ]


@pytest.mark.asyncio
async def test_formal_runner_rejects_incomplete_manifest_before_creating_output(
    tmp_path: Path,
) -> None:
    source = BenchmarkManifest.from_file(Path("benchmarks/manifest.json"))
    manifest = source.model_copy(update={"cases": source.cases[:1], "repetitions": 1})
    output = tmp_path / "incomplete"

    with pytest.raises(ValueError, match="preflight"):
        await BenchmarkRunner(
            manifest=manifest,
            scenarios_root=ROOT,
            output_root=output,
            clock=lambda: NOW,
        ).run()

    assert not output.exists()


def test_reproducibility_metadata_has_source_fingerprint_without_git_head(tmp_path: Path) -> None:
    runner = BenchmarkRunner(
        manifest=BenchmarkManifest.from_file(Path("benchmarks/manifest.json")),
        scenarios_root=ROOT,
        output_root=tmp_path,
        clock=lambda: NOW,
    )

    metadata = runner.reproducibility_metadata()

    assert metadata["code_version"] != "unavailable"
    assert str(metadata["code_version"]).startswith(("git:", "source-sha256:"))
    assert metadata["command"] == (
        "python scripts/compare_agents.py --manifest benchmarks/manifest.json "
        "--output benchmarks/results/latest"
    )


@pytest.mark.asyncio
async def test_each_benchmark_run_is_stopped_at_the_frozen_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = BenchmarkManifest.from_file(Path("benchmarks/manifest.json"))
    manifest = source.model_copy(
        update={
            "cases": source.cases[:1],
            "repetitions": 1,
            "budget": source.budget.model_copy(update={"timeout_seconds": 1}),
        }
    )

    async def too_slow(*args: object, **kwargs: object) -> object:
        del args, kwargs
        await asyncio.sleep(2)
        raise AssertionError("timeout did not cancel the run")

    monkeypatch.setattr(DueDiligenceService, "run", too_slow)
    output = tmp_path / "timeout"
    await BenchmarkRunner(
        manifest=manifest,
        scenarios_root=ROOT,
        output_root=output,
        clock=lambda: NOW,
        allow_partial_manifest=True,
    ).run()
    failures = json.loads((output / "failures.json").read_text(encoding="utf-8"))

    assert len(failures) == 2
    assert {item["error_code"] for item in failures} == {"benchmark_timeout"}
    assert {item["sample_status"] for item in failures} == {"timeout"}


def test_shared_acquisition_cost_is_aggregated_once_per_pair() -> None:
    shared = ExecutionCost(
        llm_requests=1,
        successful_llm_requests=1,
        provider_usage_requests=1,
        input_tokens=8,
        output_tokens=2,
        total_tokens=10,
        tool_calls=2,
        mcp_calls=5,
        schema_retries=0,
        repair_rounds=0,
        wall_time_ms=40,
    )
    quality = QualityBreakdown.from_raw_counts(
        {
            "coverage_completed": 1,
            "coverage_total": 1,
            "supported_findings": 0,
            "accepted_findings": 0,
            "expected_risk_hits": 0,
            "actual_risk_hits": 0,
            "matched_risk_hits": 0,
            "expected_conflicts": 0,
            "detected_conflicts": 0,
            "required_sections": 8,
            "completed_sections": 8,
        }
    )
    records = tuple(
        BenchmarkRecord(
            case_id="case-1",
            scenario_id="normal-enterprise",
            repetition=1,
            mode=mode,
            success=True,
            fairness_fingerprint="same",
            duration_ms=10,
            tool_calls=2,
            token_count=10,
            conflicts_detected=0,
            repairs_requested=0,
            quality=quality,
            shared_acquisition_cost=shared,
        )
        for mode in OrchestrationMode
    )

    aggregate = BenchmarkAggregate.from_records(
        records,
        primary_metrics=("quality_score",),
    )

    assert aggregate.shared_metrics["mcp_calls"] == 5.0
    assert aggregate.shared_metrics["total_tokens"] == 10.0

    with pytest.raises(ValueError, match="not paired"):
        BenchmarkAggregate.from_records(
            records[:1],
            primary_metrics=("quality_score",),
        )

    mismatched = (
        records[0],
        records[1].model_copy(update={"shared_acquisition_cost": ExecutionCost.zero()}),
    )
    with pytest.raises(ValueError, match="shared acquisition cost mismatch"):
        BenchmarkAggregate.from_records(
            mismatched,
            primary_metrics=("quality_score",),
        )
