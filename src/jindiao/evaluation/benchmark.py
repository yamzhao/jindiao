"""Paired single-versus-multi benchmark runner and reproducible exports."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import random
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from jindiao.application.errors import error_to_record
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.execution import ExecutionCost, FormalComparisonEligibility
from jindiao.contracts.product import ProductResult
from jindiao.contracts.report_inputs import ReviewedReportInputs
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    DueDiligenceRequest,
    OrchestrationMode,
    RunStatus,
)
from jindiao.investigation import CHECK_CATALOG
from jindiao.observability import RunArtifactStore
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.scenarios import ExpectedResultLoader, ScenarioRepository
from jindiao.security import redact_json, redact_text

from .metrics import BenchmarkEvaluator
from .models import (
    BenchmarkAggregate,
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkRecord,
    BenchmarkSampleStatus,
)


class BenchmarkRunner:
    def __init__(
        self,
        *,
        manifest: BenchmarkManifest,
        scenarios_root: Path,
        output_root: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        risk_rules_path: Path = Path("config/risk-rules-v1.json"),
        allow_partial_manifest: bool = False,
    ) -> None:
        self._manifest = manifest
        self._scenarios_root = scenarios_root.resolve()
        self._output_root = output_root.resolve()
        self._clock = clock
        self._risk_rules_path = risk_rules_path
        self._allow_partial_manifest = allow_partial_manifest
        self._expected = ExpectedResultLoader(self._scenarios_root)
        self._evaluator = BenchmarkEvaluator(weights=manifest.quality_weights)

    async def run(self) -> BenchmarkAggregate:
        self._preflight()
        random.seed(self._manifest.random_seed)
        self._output_root.mkdir(parents=True, exist_ok=True)
        records: list[BenchmarkRecord] = []
        for repetition in range(1, self._manifest.repetitions + 1):
            for case in self._manifest.cases:
                for mode in OrchestrationMode:
                    records.append(await self._run_one(case, repetition, mode))
        metadata = self._reproducibility_metadata()
        summary = BenchmarkAggregate.from_records(
            tuple(records),
            primary_metrics=self._manifest.primary_metrics,
            gain_thresholds=self._manifest.gain_thresholds,
            formal_eligibility=FormalComparisonEligibility(
                eligible=False,
                reasons=("runner:deterministic_harness",),
            ),
            metadata=metadata,
        )
        self._export(tuple(records), summary)
        return summary

    async def _run_one(
        self,
        case: BenchmarkCase,
        repetition: int,
        mode: OrchestrationMode,
    ) -> BenchmarkRecord:
        run_id = f"bench-{case.case_id}-{repetition}-{mode.value}"
        request_id = f"request-{run_id}"
        run_artifacts = self._output_root / "artifacts"
        settings = Settings(
            environment="benchmark",
            model_provider=self._manifest.model.provider,
            model_name=self._manifest.model.name,
            model_temperature=self._manifest.model.temperature,
            mock_data_root=self._scenarios_root.parent,
            artifact_root=run_artifacts,
            max_concurrency=self._manifest.budget.max_concurrency,
            request_timeout_seconds=self._manifest.budget.timeout_seconds,
            max_tool_calls=self._manifest.budget.max_tool_calls,
            max_repair_rounds=self._manifest.budget.max_repair_rounds,
            allow_degraded_mock=case.allow_degraded_mock,
        )
        overrides = dict(case.source_status_overrides)
        service = DueDiligenceService(
            settings=settings,
            scenarios=ScenarioRepository(self._scenarios_root),
            risk_rules_path=self._risk_rules_path,
            artifact_store=RunArtifactStore(run_artifacts, clock=self._clock),
            toolset_factory=lambda: ScenarioToolset(
                source_status_overrides=overrides,
                clock=self._clock,
            ),
        )
        request = DueDiligenceRequest(
            enterprise=case.enterprise,
            scenario_id=case.scenario_id if case.use_scenario_id else None,
            allow_degraded_mock=case.allow_degraded_mock,
        )
        fingerprint = self._manifest.fairness_fingerprint(mode)
        try:
            async with asyncio.timeout(self._manifest.budget.timeout_seconds):
                result = await service.run(
                    request,
                    mode=mode,
                    request_id=request_id,
                    run_id=run_id,
                )
        except TimeoutError:
            metrics = self._read_metrics(run_artifacts / run_id / "metrics.json")
            return BenchmarkRecord(
                case_id=case.case_id,
                scenario_id=case.scenario_id,
                repetition=repetition,
                mode=mode,
                success=False,
                sample_status=BenchmarkSampleStatus.TIMEOUT,
                fairness_fingerprint=fingerprint,
                duration_ms=self._metric_int(metrics, "end_to_end_duration_ms"),
                first_valid_evidence_ms=self._metric_optional_int(
                    metrics, "first_valid_evidence_ms"
                ),
                tool_calls=self._metric_int(metrics, "tool_calls"),
                token_count=self._metric_int(metrics, "token_count"),
                conflicts_detected=self._metric_int(metrics, "conflicts_detected"),
                repairs_requested=self._metric_int(metrics, "repairs_requested"),
                error_code="benchmark_timeout",
            )
        except Exception as error:
            record = error_to_record(error)
            metrics = self._read_metrics(run_artifacts / run_id / "metrics.json")
            return BenchmarkRecord(
                case_id=case.case_id,
                scenario_id=case.scenario_id,
                repetition=repetition,
                mode=mode,
                success=False,
                sample_status=(
                    BenchmarkSampleStatus.SCHEMA_ERROR
                    if "schema" in record.code.value
                    else BenchmarkSampleStatus.FAILED
                ),
                fairness_fingerprint=fingerprint,
                duration_ms=self._metric_int(metrics, "end_to_end_duration_ms"),
                first_valid_evidence_ms=self._metric_optional_int(
                    metrics, "first_valid_evidence_ms"
                ),
                tool_calls=self._metric_int(metrics, "tool_calls"),
                token_count=self._metric_int(metrics, "token_count"),
                conflicts_detected=self._metric_int(metrics, "conflicts_detected"),
                repairs_requested=self._metric_int(metrics, "repairs_requested"),
                error_code=record.code,
            )

        if not isinstance(result, ProductResult):
            raise TypeError("benchmark new runs must return prototype-v1 results")
        metrics = self._read_metrics(run_artifacts / run_id / "metrics.json")
        internal = self._read_json(run_artifacts / run_id / "investigation.json")
        reviewed = ReviewedReportInputs.model_validate(internal.get("reviewed"))
        agent_results = tuple(
            AgentInvestigationResult.model_validate(item)
            for item in self._object_list(internal.get("agent_results"))
        )
        execution_cost = self._object_mapping(internal.get("execution_cost"))
        shared_acquisition_cost = ExecutionCost.model_validate(
            execution_cost.get("shared_acquisition", {})
        )
        investigation_cost = ExecutionCost.model_validate(execution_cost.get("investigation", {}))
        expected = self._expected.load(case.scenario_id, version=case.scenario_version)
        quality = self._evaluator.evaluate(
            result,
            expected,
            reviewed=reviewed,
            detected_conflicts=self._metric_int(metrics, "conflicts_detected"),
        )
        check_ids = {
            check.check_id
            for agent_result in agent_results
            if agent_result.phase is AgentResultPhase.INVESTIGATION
            for check in agent_result.check_results
        }
        fixed_check_total = len(CHECK_CATALOG.check_ids)
        fixed_check_completed = len(check_ids & set(CHECK_CATALOG.check_ids))
        fixed_check_coverage = fixed_check_completed / fixed_check_total
        completed = result.meta.status is RunStatus.COMPLETED
        canonical = json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return BenchmarkRecord(
            case_id=case.case_id,
            scenario_id=case.scenario_id,
            repetition=repetition,
            mode=mode,
            success=completed,
            sample_status=(
                BenchmarkSampleStatus.COMPLETED if completed else BenchmarkSampleStatus.PARTIAL
            ),
            fairness_fingerprint=fingerprint,
            duration_ms=self._metric_int(metrics, "end_to_end_duration_ms"),
            first_valid_evidence_ms=self._metric_optional_int(metrics, "first_valid_evidence_ms"),
            tool_calls=self._metric_int(metrics, "tool_calls"),
            token_count=self._metric_int(metrics, "token_count"),
            conflicts_detected=self._metric_int(metrics, "conflicts_detected"),
            repairs_requested=self._metric_int(metrics, "repairs_requested"),
            fixed_check_completed=fixed_check_completed,
            fixed_check_total=fixed_check_total,
            fixed_check_coverage=fixed_check_coverage,
            evidence_sufficiency=quality.evidence_support,
            structured_submission_success_rate=fixed_check_coverage,
            shared_acquisition_cost=shared_acquisition_cost,
            investigation_cost=investigation_cost,
            quality=quality,
            error_code=None if completed else "partial_result",
            result_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        )

    @staticmethod
    def _read_metrics(path: Path) -> dict[str, JsonValue]:
        if not path.is_file():
            return {}
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _read_json(path: Path) -> dict[str, JsonValue]:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"internal benchmark artifact must be an object: {path}")
        return raw

    @staticmethod
    def _object_mapping(value: object) -> dict[str, JsonValue]:
        if not isinstance(value, dict):
            raise ValueError("internal benchmark execution cost must be an object")
        return value

    @staticmethod
    def _object_list(value: object) -> list[dict[str, JsonValue]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("internal benchmark agent results must be an array of objects")
        return value

    @staticmethod
    def _metric_int(metrics: dict[str, JsonValue], name: str) -> int:
        value = metrics.get(name, 0)
        return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0

    @staticmethod
    def _metric_optional_int(metrics: dict[str, JsonValue], name: str) -> int | None:
        value = metrics.get(name)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
        return None

    def _dataset_hash(self) -> str:
        digest = hashlib.sha256(self._manifest.content_hash.encode())
        for scenario_id in sorted({case.scenario_id for case in self._manifest.cases}):
            digest.update(scenario_id.encode())
            digest.update((self._scenarios_root / scenario_id / "manifest.json").read_bytes())
        return digest.hexdigest()

    def _preflight(self) -> None:
        failures: list[str] = []
        if not self._allow_partial_manifest:
            required_categories = {
                "normal",
                "judicial_risk",
                "operational_risk",
                "evidence_conflict",
                "capability_absent",
                "source_error",
                "entity_ambiguous",
                "report_gap",
            }
            categories = {case.category for case in self._manifest.cases}
            missing = sorted(required_categories - categories)
            if missing:
                failures.append(f"missing categories: {', '.join(missing)}")
            if not any(
                status.value == "verified_empty"
                for case in self._manifest.cases
                for status in case.source_status_overrides.values()
            ):
                failures.append("missing verified_empty behavior")
        repository = ScenarioRepository(self._scenarios_root)
        for case in self._manifest.cases:
            try:
                snapshot = repository.load(case.scenario_id, case.enterprise)
                if snapshot.manifest.version != case.scenario_version:
                    failures.append(f"{case.case_id}: scenario version mismatch")
                expected = self._expected.load(case.scenario_id, version=case.scenario_version)
                required_expected = {"expected/findings.json", "expected/decision.json"}
                if not required_expected <= set(expected.available_paths):
                    failures.append(f"{case.case_id}: incomplete expected answers")
            except Exception as error:
                failures.append(f"{case.case_id}: {type(error).__name__}: {error}")
        fingerprints = {self._manifest.fairness_fingerprint(mode) for mode in OrchestrationMode}
        if len(fingerprints) != 1:
            failures.append("single/multi fairness fingerprints differ")
        if failures:
            raise ValueError("benchmark preflight failed: " + "; ".join(failures))

    def reproducibility_metadata(self) -> dict[str, JsonValue]:
        try:
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            revision = f"git:{revision}"
        except (OSError, subprocess.CalledProcessError):
            revision = f"source-sha256:{self._source_tree_hash()}"
        skill_versions: dict[str, JsonValue] = {}
        for skill in (
            "skills/agent/tyc-evidence-acquisition",
            "skills/team/evidence-backed-due-diligence",
            "skills/team/feedback-evolved-reporting",
        ):
            path = Path(skill)
            if (path / "VERSION").is_file():
                skill_versions[path.name] = (path / "VERSION").read_text(encoding="utf-8").strip()
        return {
            "benchmark_id": self._manifest.benchmark_id,
            "command": (
                "python scripts/compare_agents.py --manifest benchmarks/manifest.json "
                "--output benchmarks/results/latest"
            ),
            "code_version": revision,
            "concurrency": self._manifest.budget.max_concurrency,
            "dataset_hash": self._dataset_hash(),
            "hardware": {
                "machine": platform.machine(),
                "processor": platform.processor() or "unknown",
                "system": platform.system(),
            },
            "manifest_hash": self._manifest.content_hash,
            "model": self._manifest.model.model_dump(mode="json"),
            "random_seed": self._manifest.random_seed,
            "rule_version": "v1",
            "skill_versions": skill_versions,
            "timestamp": self._clock().isoformat(),
        }

    def _reproducibility_metadata(self) -> dict[str, JsonValue]:
        return self.reproducibility_metadata()

    @staticmethod
    def _source_tree_hash() -> str:
        project_root = Path(__file__).resolve().parents[3]
        candidates = [
            project_root / "src",
            project_root / "config",
            project_root / "skills",
            project_root / "benchmarks" / "manifest.json",
            project_root / "pyproject.toml",
            project_root / "requirements.txt",
        ]
        digest = hashlib.sha256()
        files: list[Path] = []
        for candidate in candidates:
            if candidate.is_file():
                files.append(candidate)
            elif candidate.is_dir():
                files.extend(
                    path
                    for path in candidate.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts
                )
        for path in sorted(files):
            digest.update(path.relative_to(project_root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _export(
        self,
        records: tuple[BenchmarkRecord, ...],
        summary: BenchmarkAggregate,
    ) -> None:
        records_path = self._output_root / "records.jsonl"
        records_path.write_text(
            "".join(
                json.dumps(
                    redact_json(record.model_dump(mode="json")),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
                for record in records
            ),
            encoding="utf-8",
        )
        (self._output_root / "summary.json").write_text(
            json.dumps(
                redact_json(summary.model_dump(mode="json")),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        failures = [record.model_dump(mode="json") for record in records if not record.success]
        (self._output_root / "failures.json").write_text(
            json.dumps(
                redact_json(failures),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (self._output_root / "comparison.md").write_text(
            redact_text(self._render_markdown(summary)), encoding="utf-8"
        )

    @staticmethod
    def _render_markdown(summary: BenchmarkAggregate) -> str:
        single = summary.mode_metrics["single"]
        multi = summary.mode_metrics["multi"]
        rows = [
            "# Single vs Multi Agent 基准对比",
            "",
            f"结论: **{summary.claim}**。主指标: {', '.join(summary.primary_metrics)}。",
            "",
            "| 指标 | Single | Multi | Multi - Single |",
            "| --- | ---: | ---: | ---: |",
        ]
        labels = {
            "quality_score": "质量总分",
            "fixed_check_coverage": "固定核查覆盖率",
            "evidence_sufficiency": "Evidence 充分性",
            "structured_submission_success_rate": "结构化提交成功率",
            "success_rate": "成功率",
            "end_to_end_duration_ms": "端到端耗时(ms)",
            "first_valid_evidence_ms": "首条有效证据(ms)",
            "tool_calls": "工具调用数",
            "token_count": "Token",
            "investigation_llm_requests": "调查 LLM 请求数",
            "investigation_total_tokens": "调查 Token",
            "investigation_wall_time_ms": "调查耗时(ms)",
            "conflicts_detected": "冲突检出数",
            "repairs_requested": "返工数",
        }
        for key, label in labels.items():
            rows.append(
                f"| {label} | {single[key]:.6f} | {multi[key]:.6f} | {summary.deltas[key]:+.6f} |"
            )
        rows.extend(
            [
                "",
                "## 代价披露",
                "",
                "Multi 的时延、工具调用和 Token 差值均在上表展示; 质量收益不用于隐藏资源代价。",
                "",
                "## 共享采集成本 (每个 pair 仅计一次)",
                "",
                f"- MCP 调用: {summary.shared_metrics['mcp_calls']:.6f}",
                f"- 总用量 (Token): {summary.shared_metrics['total_tokens']:.6f}",
                f"- 耗时(ms): {summary.shared_metrics['wall_time_ms']:.6f}",
                "",
                "## 正式结论资格",
                "",
                (
                    "- 通过"
                    if summary.formal_eligibility is not None
                    and summary.formal_eligibility.eligible
                    else "- 未通过; 本次仅作诊断性输出"
                ),
                *(
                    [f"- 原因码: {reason}" for reason in summary.formal_eligibility.reasons]
                    if summary.formal_eligibility is not None
                    else []
                ),
                "",
                "## 95% 配对置信区间",
                "",
            ]
        )
        rows.extend(
            f"- {metric}: [{bounds[0]:.6f}, {bounds[1]:.6f}]"
            for metric, bounds in summary.confidence_intervals_95.items()
        )
        return "\n".join(rows) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the paired Jindiao benchmark")
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/manifest.json"))
    parser.add_argument("--scenarios", type=Path, default=Path("mock_data/scenarios"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/latest"))
    return parser


async def _async_main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = BenchmarkManifest.from_file(args.manifest)
    summary = await BenchmarkRunner(
        manifest=manifest,
        scenarios_root=args.scenarios,
        output_root=args.output,
    ).run()
    print(summary.model_dump_json(indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BenchmarkRunner", "main"]
