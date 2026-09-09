"""Bounded, non-binding application suggestions; amounts are always CNY yuan."""
# ruff: noqa: RUF001

from __future__ import annotations

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.business import GuaranteeMethod, RepaymentMethod
from jindiao.contracts.product import BusinessPlan, RiskFinding
from jindiao.contracts.reporting import Decision, DecisionBand

SUGGESTION_FIELDS = (
    "suggested_amount",
    "suggested_interest_rate",
    "suggested_credit_term_months",
    "suggested_loan_term_months",
    "guarantee_methods",
    "repayment_methods",
)
PRICING_GUIDANCE = "建议按本行小额短期产品标准定价，最终以审批为准"
STOP_GUIDANCE = "暂不建议新增授信，利率与期限不适用"


class SuggestedValues(ContractModel):
    suggested_amount: float | None = Field(
        default=None, ge=0, le=10000, allow_inf_nan=False, strict=True
    )
    suggested_interest_rate: str | None = Field(default=None, min_length=1, max_length=200)
    suggested_credit_term_months: int | None = Field(default=None, ge=1, le=3, strict=True)
    suggested_loan_term_months: int | None = Field(default=None, ge=1, le=3, strict=True)
    guarantee_methods: tuple[GuaranteeMethod, ...] = ()
    repayment_methods: tuple[RepaymentMethod, ...] = Field(default=(), max_length=1)
    reason: str = Field(default="", max_length=2000)
    evidence_ids: tuple[str, ...] = ()


class SuggestionLimits(ContractModel):
    policy_version: str = "small-short-v1"
    max_amount_yuan: float
    max_term_months: int
    default_term_months: int
    pricing_guidance: str = PRICING_GUIDANCE
    stop_guidance: str = STOP_GUIDANCE
    stop: bool

    @classmethod
    def for_report(
        cls, plan: BusinessPlan, decision: Decision, risks: tuple[RiskFinding, ...]
    ) -> SuggestionLimits:
        stop = decision.band is DecisionBand.REJECT or plan.suggested_amount == 0
        term = min(plan.application_term_months or 3, 3)
        return cls(
            max_amount_yuan=0
            if stop
            else min(
                plan.application_amount if plan.application_amount is not None else 10000, 10000
            ),
            max_term_months=term,
            default_term_months=1 if risks or decision.band is not DecisionBand.PASS else term,
            stop=stop,
        )

    def validate_suggestions(self, values: SuggestedValues) -> None:
        if values.suggested_amount is not None and values.suggested_amount > self.max_amount_yuan:
            raise ValueError("suggested_amount exceeds the application/policy limit")
        for term in (values.suggested_credit_term_months, values.suggested_loan_term_months):
            if term is not None and term > self.max_term_months:
                raise ValueError("suggested term exceeds the application/policy limit")
        if (
            values.suggested_credit_term_months is not None
            and values.suggested_loan_term_months is not None
            and values.suggested_loan_term_months > values.suggested_credit_term_months
        ):
            raise ValueError("suggested loan term exceeds credit term")
        stopped = self.stop or values.suggested_amount == 0 or self.max_amount_yuan == 0
        pricing = self.stop_guidance if stopped else self.pricing_guidance
        if values.suggested_interest_rate not in (None, pricing):
            raise ValueError("suggested interest rate must use the configured pricing guidance")
        if stopped and (
            values.suggested_credit_term_months is not None
            or values.suggested_loan_term_months is not None
            or values.guarantee_methods
            or values.repayment_methods
        ):
            raise ValueError(
                "no new credit: terms, guarantee and repayment methods are inapplicable"
            )


def complete_suggestions(
    plan: BusinessPlan,
    *,
    decision: Decision,
    risks: tuple[RiskFinding, ...],
    evidence_ids: tuple[str, ...],
    values: SuggestedValues | None = None,
) -> BusinessPlan:
    limits = SuggestionLimits.for_report(plan, decision, risks)
    values = values or SuggestedValues()
    limits.validate_suggestions(values)
    amount = values.suggested_amount
    if amount is None:
        amount = limits.max_amount_yuan
    stopped = limits.stop or amount == 0
    credit_term = values.suggested_credit_term_months or max(
        values.suggested_loan_term_months or 1, limits.default_term_months
    )
    loan_term = values.suggested_loan_term_months or min(credit_term, limits.default_term_months)
    completed: dict[str, object] = {
        "suggested_amount": amount,
        "suggested_interest_rate": limits.stop_guidance if stopped else limits.pricing_guidance,
        "suggested_credit_term_months": None if stopped else credit_term,
        "suggested_loan_term_months": None if stopped else loan_term,
        "guarantee_methods": ()
        if stopped
        else values.guarantee_methods or ("legal_representative",),
        "repayment_methods": () if stopped else values.repayment_methods or ("equal_principal",),
    }
    changes = {
        name: value for name, value in completed.items() if getattr(plan, name) in (None, ())
    }
    model_fields = {name for name in changes if getattr(values, name) not in (None, ())}
    source = (
        "model"
        if model_fields == set(changes)
        else "model_with_rules"
        if model_fields or values.reason.strip()
        else "rules"
    )
    context = "；".join(risk.title for risk in risks[:3])
    reason = values.reason.strip() or (
        f"规则保守建议：已审核关注事项包括{context}。"
        if context
        else "规则保守建议：依据现有申报信息、已审核结论及资料完整度。"
    )
    if stopped:
        reason += " 暂不新增授信，建议额度为0元，期限、担保和还款安排不适用，先完成人工复核。"
    else:
        reason += (
            f" 小额短期候选额度不超过{limits.max_amount_yuan:g}元，期限不超过"
            f"{limits.max_term_months}个月；以核实资金用途、还款能力及担保人资格为前提，"
            "建议核验法定代表人或所选担保人的代偿能力，并按所选方式安排还款。"
            "利率按本行小额短期产品标准定价。以上为待审批建议，不代表批准授信。"
        )
    if decision.band is DecisionBand.MANUAL_REVIEW or risks:
        reason += " 存在已审核关注事项或资料缺口，应先人工复核后再决定是否执行候选方案。"
    if len(changes) != len(SUGGESTION_FIELDS):
        reason += " 已有人工填写的建议值保留，不属于自动生成的候选条件。"
    refs = tuple(dict.fromkeys((*plan.evidence_ids, *evidence_ids)))
    gaps = tuple(item for item in plan.missing_fields if item.field not in changes)
    return BusinessPlan.model_validate(
        {
            **plan.model_dump(),
            **changes,
            "analysis": reason,
            "generated_fields": tuple(dict.fromkeys((*plan.generated_fields, *changes))),
            "suggestion_source": source if changes else plan.suggestion_source,
            "evidence_ids": refs,
            "missing_fields": gaps,
            "status": "partial" if gaps else "complete",
        }
    )
