from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jindiao.risk import (
    RiskRule,
    RiskRuleSet,
    RuleCategory,
    RuleCondition,
    RuleOperator,
)


def test_checked_in_rules_are_versioned_and_cover_admission_and_attention() -> None:
    rules = RiskRuleSet.from_file(Path("config/risk-rules-v1.json"))

    assert rules.version == "v1"
    assert rules.pass_upper_exclusive == 20
    assert rules.reject_lower_inclusive == 80
    assert {rule.category for rule in rules.rules} == {
        RuleCategory.ADMISSION,
        RuleCategory.ATTENTION,
    }
    assert len({rule.rule_id for rule in rules.rules}) == len(rules.rules)
    assert rules.content_hash == rules.content_hash


def test_rule_condition_restricts_fields_and_operator_value_combinations() -> None:
    condition = RuleCondition(
        field="claim",
        operator=RuleOperator.CONTAINS,
        value="失信",
    )
    assert condition.field == "claim"

    with pytest.raises(ValidationError):
        RuleCondition(field="evidence_ids", operator=RuleOperator.EXISTS, value=None)
    with pytest.raises(ValidationError):
        RuleCondition(field="claim", operator=RuleOperator.CONTAINS, value=1)


def test_rule_set_rejects_duplicate_ids_and_invalid_band_boundaries() -> None:
    rule = RiskRule(
        rule_id="attention.execution",
        category=RuleCategory.ATTENTION,
        description="存在未结被执行记录",
        points=20,
        conditions=(RuleCondition(field="claim", operator=RuleOperator.CONTAINS, value="被执行"),),
    )
    values = {
        "schema_version": 1,
        "version": "v1",
        "pass_upper_exclusive": 20,
        "reject_lower_inclusive": 80,
        "rules": [rule, rule],
    }

    with pytest.raises(ValidationError, match="unique"):
        RiskRuleSet.model_validate(values)
    with pytest.raises(ValidationError, match="boundaries"):
        RiskRuleSet.model_validate(
            values
            | {
                "pass_upper_exclusive": 80,
                "reject_lower_inclusive": 20,
                "rules": [rule],
            }
        )
