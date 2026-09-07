"""Quality metric calculation against evaluator-only expected answers."""

from __future__ import annotations

from collections.abc import Mapping

from jindiao.contracts.investigation import FindingStatus
from jindiao.contracts.results import DueDiligenceResult
from jindiao.scenarios.expected import ExpectedResultSnapshot

from .models import BenchmarkQualityWeights, QualityBreakdown


class BenchmarkEvaluator:
    REQUIRED_SECTION_COUNT = 8

    def __init__(self, *, weights: BenchmarkQualityWeights | None = None) -> None:
        self._weights = weights or BenchmarkQualityWeights.default()

    def evaluate(
        self,
        result: DueDiligenceResult,
        expected: ExpectedResultSnapshot,
    ) -> QualityBreakdown:
        expected_decision = expected.read_json("expected/decision.json")
        if not isinstance(expected_decision, Mapping):
            raise ValueError("expected decision must be an object")
        expected_hits = self._expected_rule_hits(expected_decision)
        actual_hits = {hit.rule_id for hit in result.decision.rule_hits}
        accepted = tuple(
            finding for finding in result.findings if finding.status is FindingStatus.ACCEPTED
        )
        evidence_ids = {item.evidence_id for item in result.evidence}
        supported = sum(
            bool(finding.evidence_ids) and set(finding.evidence_ids) <= evidence_ids
            for finding in accepted
        )
        expected_conflicts = int(bool(expected_decision.get("pending_review_items")))
        completed_sections = sum(
            bool(section.title.strip()) and bool(section.data) for section in result.sections
        )
        return QualityBreakdown.from_raw_counts(
            {
                "coverage_completed": result.coverage.completed_items,
                "coverage_total": result.coverage.total_items,
                "supported_findings": supported,
                "accepted_findings": len(accepted),
                "expected_risk_hits": len(expected_hits),
                "actual_risk_hits": len(actual_hits),
                "matched_risk_hits": len(expected_hits & actual_hits),
                "expected_conflicts": expected_conflicts,
                "detected_conflicts": min(
                    expected_conflicts, result.collaboration.conflicts_detected
                ),
                "required_sections": self.REQUIRED_SECTION_COUNT,
                "completed_sections": min(self.REQUIRED_SECTION_COUNT, completed_sections),
            },
            weights=self._weights,
        )

    @staticmethod
    def _expected_rule_hits(expected_decision: object) -> set[str]:
        if not isinstance(expected_decision, Mapping):
            raise ValueError("expected decision must be an object")
        hits = expected_decision.get("rule_hits")
        if not isinstance(hits, tuple):
            raise ValueError("expected decision rule_hits must be an array")
        rule_ids: set[str] = set()
        for hit in hits:
            if not isinstance(hit, Mapping) or not isinstance(hit.get("rule_id"), str):
                raise ValueError("expected rule hit is invalid")
            rule_ids.add(hit["rule_id"])
        return rule_ids


__all__ = ["BenchmarkEvaluator", "QualityBreakdown"]
