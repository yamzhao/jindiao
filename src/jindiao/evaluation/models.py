"""Frozen benchmark configuration, raw records, and aggregate contracts."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.evidence import SourceStatus
from jindiao.contracts.execution import ExecutionCost, FormalComparisonEligibility
from jindiao.contracts.results import OrchestrationMode
from jindiao.security import redact_json

PrimaryMetric = Literal["quality_score", "success_rate", "end_to_end_duration_ms"]
GainDirection = Literal["increase", "decrease"]


class BenchmarkQualityWeights(ContractModel):
    """Pre-registered weights for the versioned quality score."""

    coverage: float = Field(ge=0, le=1)
    evidence_support: float = Field(ge=0, le=1)
    risk_quality: float = Field(ge=0, le=1)
    conflict_detection: float = Field(ge=0, le=1)
    report_structure: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_unit_sum(self) -> BenchmarkQualityWeights:
        if not math.isclose(sum(self.as_dict().values()), 1.0, abs_tol=1e-9):
            raise ValueError("benchmark quality weights must sum to one")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "coverage": self.coverage,
            "evidence_support": self.evidence_support,
            "risk_quality": self.risk_quality,
            "conflict_detection": self.conflict_detection,
            "report_structure": self.report_structure,
        }

    @classmethod
    def default(cls) -> BenchmarkQualityWeights:
        return cls(
            coverage=0.30,
            evidence_support=0.25,
            risk_quality=0.20,
            conflict_detection=0.15,
            report_structure=0.10,
        )


class BenchmarkGainThreshold(ContractModel):
    """Minimum paired delta required for one primary metric."""

    metric: PrimaryMetric
    direction: GainDirection
    minimum_absolute_delta: float = Field(gt=0)

    @model_validator(mode="after")
    def require_metric_direction(self) -> BenchmarkGainThreshold:
        expected = "decrease" if self.metric == "end_to_end_duration_ms" else "increase"
        if self.direction != expected:
            raise ValueError(f"{self.metric} gain direction must be {expected}")
        return self


class BenchmarkFailureRetention(ContractModel):
    """Immutable policy preventing post-hoc deletion of unfavorable samples."""

    timeout: Literal[True]
    schema_error: Literal[True]
    partial: Literal[True]
    include_in_aggregates: Literal[True]


class BenchmarkSampleStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SCHEMA_ERROR = "schema_error"


class BenchmarkModelParameters(ContractModel):
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)


class BenchmarkBudget(ContractModel):
    max_concurrency: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)
    max_repair_rounds: int = Field(ge=0)
    timeout_seconds: int = Field(ge=1)


class BenchmarkCase(ContractModel):
    case_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    scenario_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    scenario_version: str = Field(pattern=r"^v\d+(?:\.\d+){0,2}$")
    category: str = Field(min_length=1)
    enterprise: EnterpriseInput
    source_status_overrides: dict[str, SourceStatus] = Field(default_factory=dict)
    allow_degraded_mock: bool = False
    use_scenario_id: bool = True


class BenchmarkManifest(ContractModel):
    schema_version: Literal[2]
    benchmark_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    repetitions: int = Field(ge=1)
    random_seed: int
    primary_metrics: tuple[PrimaryMetric, ...] = Field(min_length=1)
    quality_weights: BenchmarkQualityWeights
    gain_thresholds: tuple[BenchmarkGainThreshold, ...] = Field(min_length=1)
    failure_retention: BenchmarkFailureRetention
    model: BenchmarkModelParameters
    budget: BenchmarkBudget
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_cases_and_metrics(self) -> BenchmarkManifest:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark case ids must be unique")
        if len(self.primary_metrics) != len(set(self.primary_metrics)):
            raise ValueError("primary metrics must be unique")
        threshold_metrics = tuple(item.metric for item in self.gain_thresholds)
        if len(threshold_metrics) != len(set(threshold_metrics)):
            raise ValueError("benchmark gain threshold metrics must be unique")
        if set(threshold_metrics) != set(self.primary_metrics):
            raise ValueError("benchmark gain thresholds must exactly match primary metrics")
        return self

    @classmethod
    def from_file(cls, path: Path) -> BenchmarkManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def fairness_fingerprint(self, mode: OrchestrationMode) -> str:
        del mode
        fair_config = {
            "budget": self.budget.model_dump(mode="json"),
            "cases": [case.model_dump(mode="json") for case in self.cases],
            "failure_retention": self.failure_retention.model_dump(mode="json"),
            "gain_thresholds": [
                threshold.model_dump(mode="json") for threshold in self.gain_thresholds
            ],
            "model": self.model.model_dump(mode="json"),
            "primary_metrics": self.primary_metrics,
            "quality_weights": self.quality_weights.model_dump(mode="json"),
            "random_seed": self.random_seed,
            "repetitions": self.repetitions,
        }
        canonical = json.dumps(
            fair_config, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class QualityBreakdown(ContractModel):
    coverage: float = Field(ge=0, le=1)
    evidence_support: float = Field(ge=0, le=1)
    risk_quality: float = Field(ge=0, le=1)
    conflict_detection: float = Field(ge=0, le=1)
    report_structure: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    weights: dict[str, float]
    raw_counts: dict[str, int]

    @classmethod
    def from_raw_counts(
        cls,
        raw_counts: dict[str, int],
        *,
        weights: BenchmarkQualityWeights | None = None,
    ) -> QualityBreakdown:
        return cls.model_validate(
            cls._calculate(
                raw_counts,
                weights=(weights or BenchmarkQualityWeights.default()).as_dict(),
            )
        )

    @staticmethod
    def _calculate(
        raw_counts: dict[str, int],
        *,
        weights: dict[str, float],
    ) -> dict[str, object]:
        required = {
            "coverage_completed",
            "coverage_total",
            "supported_findings",
            "accepted_findings",
            "expected_risk_hits",
            "actual_risk_hits",
            "matched_risk_hits",
            "expected_conflicts",
            "detected_conflicts",
            "required_sections",
            "completed_sections",
        }
        if set(raw_counts) != required or any(value < 0 for value in raw_counts.values()):
            raise ValueError("quality raw counts are incomplete or invalid")

        def ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
            return min(1.0, numerator / denominator) if denominator else empty

        coverage = ratio(raw_counts["coverage_completed"], raw_counts["coverage_total"])
        evidence_support = ratio(
            raw_counts["supported_findings"], raw_counts["accepted_findings"], empty=1.0
        )
        expected_risk = raw_counts["expected_risk_hits"]
        actual_risk = raw_counts["actual_risk_hits"]
        matched_risk = raw_counts["matched_risk_hits"]
        if not expected_risk and not actual_risk:
            risk_quality = 1.0
        elif not matched_risk:
            risk_quality = 0.0
        else:
            precision = matched_risk / actual_risk if actual_risk else 0.0
            recall = matched_risk / expected_risk if expected_risk else 0.0
            risk_quality = 2 * precision * recall / (precision + recall)
        conflict_detection = ratio(
            raw_counts["detected_conflicts"],
            raw_counts["expected_conflicts"],
            empty=1.0,
        )
        report_structure = ratio(raw_counts["completed_sections"], raw_counts["required_sections"])
        BenchmarkQualityWeights.model_validate(weights)
        score = (
            weights["coverage"] * coverage
            + weights["evidence_support"] * evidence_support
            + weights["risk_quality"] * risk_quality
            + weights["conflict_detection"] * conflict_detection
            + weights["report_structure"] * report_structure
        )
        return {
            "coverage": round(coverage, 6),
            "evidence_support": round(evidence_support, 6),
            "risk_quality": round(risk_quality, 6),
            "conflict_detection": round(conflict_detection, 6),
            "report_structure": round(report_structure, 6),
            "quality_score": round(score, 6),
            "weights": weights,
            "raw_counts": raw_counts,
        }

    @model_validator(mode="after")
    def require_recomputable_score(self) -> QualityBreakdown:
        recomputed = self._calculate(self.raw_counts, weights=self.weights)
        comparable = (
            "coverage",
            "evidence_support",
            "risk_quality",
            "conflict_detection",
            "report_structure",
            "quality_score",
        )
        if any(getattr(self, key) != recomputed[key] for key in comparable):
            raise ValueError("quality metrics are not recomputable from raw counts")
        return self


class BenchmarkRecord(ContractModel):
    case_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    repetition: int = Field(ge=1)
    mode: OrchestrationMode
    success: bool
    sample_status: BenchmarkSampleStatus = BenchmarkSampleStatus.COMPLETED
    fairness_fingerprint: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    first_valid_evidence_ms: int | None = Field(default=None, ge=0)
    tool_calls: int = Field(ge=0)
    token_count: int = Field(ge=0)
    conflicts_detected: int = Field(ge=0)
    repairs_requested: int = Field(ge=0)
    fixed_check_completed: int = Field(default=0, ge=0)
    fixed_check_total: int = Field(default=0, ge=0)
    fixed_check_coverage: float = Field(default=0.0, ge=0, le=1)
    evidence_sufficiency: float = Field(default=0.0, ge=0, le=1)
    structured_submission_success_rate: float = Field(default=0.0, ge=0, le=1)
    shared_acquisition_cost: ExecutionCost = Field(default_factory=ExecutionCost.zero)
    investigation_cost: ExecutionCost = Field(default_factory=ExecutionCost.zero)
    quality: QualityBreakdown | None = None
    error_code: str | None = None
    result_hash: str | None = None

    @model_validator(mode="after")
    def enforce_success_payload(self) -> BenchmarkRecord:
        if self.success and self.quality is None:
            raise ValueError("successful benchmark record requires quality metrics")
        if not self.success and not self.error_code:
            raise ValueError("failed benchmark record requires error_code")
        if self.fixed_check_completed > self.fixed_check_total:
            raise ValueError("completed fixed checks cannot exceed the catalog total")
        expected_coverage = (
            self.fixed_check_completed / self.fixed_check_total if self.fixed_check_total else 0.0
        )
        if not math.isclose(
            self.fixed_check_coverage,
            expected_coverage,
            abs_tol=1e-6,
        ):
            raise ValueError("fixed check coverage must match its raw counts")
        return self


class BenchmarkAggregate(ContractModel):
    record_count: int = Field(ge=0)
    mode_metrics: dict[str, dict[str, float]]
    shared_metrics: dict[str, float] = Field(default_factory=dict)
    deltas: dict[str, float]
    confidence_intervals_95: dict[str, tuple[float, float]]
    primary_metrics: tuple[PrimaryMetric, ...]
    gain_thresholds: tuple[BenchmarkGainThreshold, ...] = ()
    formal_eligibility: FormalComparisonEligibility | None = None
    can_claim_collaboration_gain: bool
    claim: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def redact_private_metadata(cls, value: object) -> dict[str, JsonValue]:
        safe = redact_json(value)
        if not isinstance(safe, dict):
            raise ValueError("benchmark metadata must be an object")
        return safe

    @classmethod
    def from_records(
        cls,
        records: tuple[BenchmarkRecord, ...],
        *,
        primary_metrics: tuple[PrimaryMetric, ...],
        gain_thresholds: tuple[BenchmarkGainThreshold, ...] = (),
        formal_eligibility: FormalComparisonEligibility | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> BenchmarkAggregate:
        cls._validate_pairing(records)
        modes: dict[str, dict[str, float]] = {}
        for mode in OrchestrationMode:
            selected = tuple(record for record in records if record.mode is mode)
            count = len(selected)
            modes[mode.value] = {
                "quality_score": cls._mean(
                    [record.quality.quality_score if record.quality else 0.0 for record in selected]
                ),
                "success_rate": cls._mean([float(record.success) for record in selected]),
                "end_to_end_duration_ms": cls._mean(
                    [float(record.duration_ms) for record in selected]
                ),
                "first_valid_evidence_ms": cls._mean(
                    [
                        float(record.first_valid_evidence_ms)
                        for record in selected
                        if record.first_valid_evidence_ms is not None
                    ]
                ),
                "tool_calls": cls._mean([float(record.tool_calls) for record in selected]),
                "token_count": cls._mean([float(record.token_count) for record in selected]),
                "fixed_check_coverage": cls._mean(
                    [record.fixed_check_coverage for record in selected]
                ),
                "evidence_sufficiency": cls._mean(
                    [record.evidence_sufficiency for record in selected]
                ),
                "structured_submission_success_rate": cls._mean(
                    [record.structured_submission_success_rate for record in selected]
                ),
                "investigation_llm_requests": cls._mean(
                    [float(record.investigation_cost.llm_requests) for record in selected]
                ),
                "investigation_total_tokens": cls._mean(
                    [float(record.investigation_cost.total_tokens) for record in selected]
                ),
                "investigation_wall_time_ms": cls._mean(
                    [float(record.investigation_cost.wall_time_ms) for record in selected]
                ),
                "conflicts_detected": cls._mean(
                    [float(record.conflicts_detected) for record in selected]
                ),
                "repairs_requested": cls._mean(
                    [float(record.repairs_requested) for record in selected]
                ),
                "sample_count": float(count),
            }
        comparable = (
            "quality_score",
            "success_rate",
            "end_to_end_duration_ms",
            "first_valid_evidence_ms",
            "tool_calls",
            "token_count",
            "fixed_check_coverage",
            "evidence_sufficiency",
            "structured_submission_success_rate",
            "investigation_llm_requests",
            "investigation_total_tokens",
            "investigation_wall_time_ms",
            "conflicts_detected",
            "repairs_requested",
        )
        deltas = {
            name: round(modes["multi"][name] - modes["single"][name], 6) for name in comparable
        }
        intervals = {
            metric: cls._paired_interval(records, metric)
            for metric in ("quality_score", "success_rate", "end_to_end_duration_ms")
        }
        if gain_thresholds:
            thresholds_by_metric = {threshold.metric: threshold for threshold in gain_thresholds}
            if set(thresholds_by_metric) != set(primary_metrics):
                raise ValueError("benchmark gain thresholds must exactly match primary metrics")
            gain = all(
                (
                    deltas[metric] >= threshold.minimum_absolute_delta
                    if threshold.direction == "increase"
                    else deltas[metric] <= -threshold.minimum_absolute_delta
                )
                for metric, threshold in thresholds_by_metric.items()
            )
        else:
            gain = any(
                (deltas[metric] < 0 if metric == "end_to_end_duration_ms" else deltas[metric] > 0)
                for metric in primary_metrics
            )
        if formal_eligibility is not None and not formal_eligibility.eligible:
            gain = False
        claim = (
            "仅诊断: 不具备正式协作增益结论资格"
            if formal_eligibility is not None and not formal_eligibility.eligible
            else ("已证明协作增益" if gain else "未证明协作增益")
        )
        paired_shared_costs = cls._paired_shared_costs(records)
        shared_metrics = {
            "mcp_calls": cls._mean([float(cost.mcp_calls) for cost in paired_shared_costs]),
            "total_tokens": cls._mean([float(cost.total_tokens) for cost in paired_shared_costs]),
            "wall_time_ms": cls._mean([float(cost.wall_time_ms) for cost in paired_shared_costs]),
        }
        return cls(
            record_count=len(records),
            mode_metrics=modes,
            shared_metrics=shared_metrics,
            deltas=deltas,
            confidence_intervals_95=intervals,
            primary_metrics=primary_metrics,
            gain_thresholds=gain_thresholds,
            formal_eligibility=formal_eligibility,
            can_claim_collaboration_gain=gain,
            claim=claim,
            metadata=metadata or {},
        )

    @staticmethod
    def _validate_pairing(records: tuple[BenchmarkRecord, ...]) -> None:
        groups: dict[tuple[str, int], list[BenchmarkRecord]] = {}
        for record in records:
            groups.setdefault((record.case_id, record.repetition), []).append(record)
        for key, pair in groups.items():
            if {record.mode for record in pair} != set(OrchestrationMode):
                raise ValueError(f"benchmark record is not paired: {key}")
            if len({record.fairness_fingerprint for record in pair}) != 1:
                raise ValueError(f"paired benchmark fairness mismatch: {key}")
            if len({record.shared_acquisition_cost for record in pair}) != 1:
                raise ValueError(f"paired shared acquisition cost mismatch: {key}")

    @staticmethod
    def _paired_shared_costs(
        records: tuple[BenchmarkRecord, ...],
    ) -> tuple[ExecutionCost, ...]:
        by_pair: dict[tuple[str, int], ExecutionCost] = {}
        for record in records:
            by_pair.setdefault(
                (record.case_id, record.repetition),
                record.shared_acquisition_cost,
            )
        return tuple(by_pair.values())

    @staticmethod
    def _mean(values: list[float]) -> float:
        return round(statistics.fmean(values), 6) if values else 0.0

    @classmethod
    def _paired_interval(
        cls, records: tuple[BenchmarkRecord, ...], metric: str
    ) -> tuple[float, float]:
        grouped: dict[tuple[str, int], dict[OrchestrationMode, BenchmarkRecord]] = {}
        for record in records:
            grouped.setdefault((record.case_id, record.repetition), {})[record.mode] = record
        differences = []
        for pair in grouped.values():
            single = pair[OrchestrationMode.SINGLE]
            multi = pair[OrchestrationMode.MULTI]
            if metric == "quality_score":
                single_value = single.quality.quality_score if single.quality else 0.0
                multi_value = multi.quality.quality_score if multi.quality else 0.0
            elif metric == "success_rate":
                single_value = float(single.success)
                multi_value = float(multi.success)
            else:
                single_value = float(single.duration_ms)
                multi_value = float(multi.duration_ms)
            differences.append(multi_value - single_value)
        mean = cls._mean(differences)
        if len(differences) < 2:
            return (mean, mean)
        margin = 1.96 * statistics.stdev(differences) / math.sqrt(len(differences))
        return (round(mean - margin, 6), round(mean + margin, 6))


__all__ = [
    "BenchmarkAggregate",
    "BenchmarkBudget",
    "BenchmarkCase",
    "BenchmarkFailureRetention",
    "BenchmarkGainThreshold",
    "BenchmarkManifest",
    "BenchmarkModelParameters",
    "BenchmarkQualityWeights",
    "BenchmarkRecord",
    "BenchmarkSampleStatus",
    "GainDirection",
    "PrimaryMetric",
    "QualityBreakdown",
]
