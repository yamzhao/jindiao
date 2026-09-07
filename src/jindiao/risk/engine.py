"""Evidence-gated deterministic evaluation of reviewed findings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import Enum

from jindiao.application.errors import RiskRuleError
from jindiao.contracts.evidence import Evidence
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    Finding,
    FindingStatus,
    RiskClass,
    Severity,
)
from jindiao.contracts.reporting import Decision, DecisionBand, RuleHit

from .rules import RiskRule, RiskRuleSet, RuleCondition, RuleOperator


def _plain(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _field_value(finding: Finding, field: str) -> object:
    root, *parts = field.split(".")
    value: object = getattr(finding, root)
    for part in parts:
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return _plain(value)


def _matches(condition: RuleCondition, finding: Finding) -> bool:
    actual = _field_value(finding, condition.field)
    expected = condition.value
    if condition.operator is RuleOperator.EXISTS:
        return actual is not None
    if condition.operator is RuleOperator.EQUALS:
        return actual == expected
    if condition.operator is RuleOperator.NOT_EQUALS:
        return actual != expected
    if condition.operator is RuleOperator.CONTAINS:
        return isinstance(actual, str) and isinstance(expected, str) and expected in actual
    if condition.operator is RuleOperator.IN:
        return isinstance(expected, list) and actual in expected
    if condition.operator is RuleOperator.GREATER_THAN_OR_EQUAL:
        return (
            isinstance(actual, int | float)
            and not isinstance(actual, bool)
            and isinstance(expected, int | float)
            and not isinstance(expected, bool)
            and actual >= expected
        )
    if condition.operator is RuleOperator.LESS_THAN_OR_EQUAL:
        return (
            isinstance(actual, int | float)
            and not isinstance(actual, bool)
            and isinstance(expected, int | float)
            and not isinstance(expected, bool)
            and actual <= expected
        )
    return False


def _rule_matches(rule: RiskRule, finding: Finding) -> bool:
    return all(_matches(condition, finding) for condition in rule.conditions)


def _explicitly_scores_unconfirmed(rule: RiskRule) -> bool:
    return any(
        condition.field == "status"
        and condition.operator is RuleOperator.EQUALS
        and condition.value == FindingStatus.UNCONFIRMED.value
        for condition in rule.conditions
    )


class RiskRuleEngine:
    def __init__(self, rules: RiskRuleSet) -> None:
        self._rules = rules

    @property
    def rule_version(self) -> str:
        return self._rules.version

    def band_for_score(self, score: int) -> DecisionBand:
        if score < 0:
            raise RiskRuleError("risk score cannot be negative")
        if score < self._rules.pass_upper_exclusive:
            return DecisionBand.PASS
        if score < self._rules.reject_lower_inclusive:
            return DecisionBand.MANUAL_REVIEW
        return DecisionBand.REJECT

    @staticmethod
    def findings_from_check_results(
        accepted_check_results: tuple[CheckResult, ...],
    ) -> tuple[Finding, ...]:
        """Project accepted model risks into the legacy deterministic rule input."""

        from jindiao.investigation import CHECK_CATALOG

        domain_by_role = {
            "corporate": "governance",
            "judicial-compliance": "judicial",
            "financial-operations": "financial",
            "related-peer": "relationships",
        }
        findings: list[Finding] = []
        for check in accepted_check_results:
            if check.status is not CheckStatus.RISK:
                continue
            definition = CHECK_CATALOG.get(check.check_id)
            domain = domain_by_role[definition.owner_role]
            for risk in check.risk_items:
                findings.append(
                    Finding(
                        finding_id=(f"check:{check.check_id}:risk:{risk.risk_id}"),
                        subject_id=check.subject_id,
                        domain=domain,
                        claim=risk.conclusion,
                        value={
                            "check_id": check.check_id,
                            "risk_id": risk.risk_id,
                            "title": risk.title,
                        },
                        risk_class=risk.risk_class,
                        severity=risk.severity,
                        status=FindingStatus.ACCEPTED,
                        evidence_ids=risk.evidence_ids,
                    )
                )
        return tuple(findings)

    def evaluate_check_results(
        self,
        *,
        accepted_check_results: tuple[CheckResult, ...],
        evidence: tuple[Evidence, ...],
        as_of_date: date,
        confidence: float,
        pending_review_items: tuple[str, ...] = (),
    ) -> Decision:
        """Score only risks projected from accepted fixed-check submissions."""

        return self.evaluate(
            findings=self.findings_from_check_results(accepted_check_results),
            evidence=evidence,
            as_of_date=as_of_date,
            confidence=confidence,
            pending_review_items=pending_review_items,
        )

    def evaluate(
        self,
        *,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
        as_of_date: date,
        confidence: float,
        pending_review_items: tuple[str, ...] = (),
    ) -> Decision:
        evidence_ids = {item.evidence_id for item in evidence}
        scorable = tuple(
            item
            for item in findings
            if item.status is FindingStatus.ACCEPTED
            or any(
                _explicitly_scores_unconfirmed(rule) and _rule_matches(rule, item)
                for rule in self._rules.rules
            )
        )
        for finding in scorable:
            if not finding.evidence_ids:
                raise RiskRuleError(
                    "scorable finding has no evidence and cannot be scored",
                    details={"finding_id": finding.finding_id},
                )
            unknown = set(finding.evidence_ids) - evidence_ids
            if unknown:
                raise RiskRuleError(
                    "scorable finding references unknown evidence",
                    details={"finding_id": finding.finding_id, "evidence_ids": sorted(unknown)},
                )

        hits: list[RuleHit] = []
        major_finding_ids: list[str] = []
        for finding in scorable:
            matched = False
            for rule in self._rules.rules:
                if (
                    finding.status is FindingStatus.UNCONFIRMED
                    and not _explicitly_scores_unconfirmed(rule)
                ):
                    continue
                if not _rule_matches(rule, finding):
                    continue
                matched = True
                hits.append(
                    RuleHit(
                        rule_id=rule.rule_id,
                        finding_id=finding.finding_id,
                        evidence_ids=finding.evidence_ids,
                        points=rule.points,
                        explanation=rule.description,
                    )
                )
            if matched and (
                finding.risk_class is RiskClass.ADMISSION
                or finding.severity in {Severity.HIGH, Severity.CRITICAL}
            ):
                major_finding_ids.append(finding.finding_id)

        score = sum(hit.points for hit in hits)
        return Decision(
            band=self.band_for_score(score),
            score=score,
            confidence=confidence,
            rule_version=self._rules.version,
            rule_hits=tuple(hits),
            major_risk_finding_ids=tuple(major_finding_ids),
            pending_review_items=pending_review_items,
            as_of_date=as_of_date,
        )


__all__ = ["RiskRuleEngine"]
