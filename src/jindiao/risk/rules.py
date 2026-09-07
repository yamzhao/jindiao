"""Versioned deterministic risk-rule configuration."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from jindiao.contracts.base import ContractModel


class RuleCategory(StrEnum):
    ADMISSION = "admission"
    ATTENTION = "attention"


class RuleOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    IN = "in"
    EXISTS = "exists"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"


class RuleCondition(ContractModel):
    field: str = Field(
        pattern=r"^(?:domain|claim|risk_class|severity|status|value(?:\.[A-Za-z0-9_]+)*)$"
    )
    operator: RuleOperator
    value: JsonValue = None

    @model_validator(mode="after")
    def validate_operator_value(self) -> RuleCondition:
        if self.operator is RuleOperator.CONTAINS and not isinstance(self.value, str):
            raise ValueError("contains condition requires a string value")
        if self.operator is RuleOperator.IN and not isinstance(self.value, list):
            raise ValueError("in condition requires a list value")
        if self.operator is RuleOperator.EXISTS and self.value is not None:
            raise ValueError("exists condition does not accept a comparison value")
        if self.operator in {
            RuleOperator.GREATER_THAN_OR_EQUAL,
            RuleOperator.LESS_THAN_OR_EQUAL,
        } and not isinstance(self.value, int | float):
            raise ValueError("numeric comparison requires a numeric value")
        return self


class RiskRule(ContractModel):
    rule_id: str = Field(pattern=r"^(?:admission|attention)\.[a-z0-9_]+$")
    category: RuleCategory
    description: str = Field(min_length=1)
    points: int = Field(ge=0)
    conditions: tuple[RuleCondition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def category_must_match_id(self) -> RiskRule:
        if not self.rule_id.startswith(f"{self.category.value}."):
            raise ValueError("rule category must match rule_id prefix")
        return self


class RiskRuleSet(ContractModel):
    schema_version: Literal[1]
    version: str = Field(pattern=r"^v\d+(?:\.\d+){0,2}$")
    pass_upper_exclusive: int = Field(ge=1)
    reject_lower_inclusive: int = Field(ge=1)
    rules: tuple[RiskRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rules_and_boundaries(self) -> RiskRuleSet:
        if self.pass_upper_exclusive >= self.reject_lower_inclusive:
            raise ValueError("risk band boundaries must be strictly increasing")
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("risk rule ids must be unique")
        return self

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def from_file(cls, path: Path) -> RiskRuleSet:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = ["RiskRule", "RiskRuleSet", "RuleCategory", "RuleCondition", "RuleOperator"]
