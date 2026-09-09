"""Short customer-facing advice grounded in the final reviewed risk cards."""
# ruff: noqa: RUF001

from __future__ import annotations

import re
from typing import Literal

from jindiao.contracts.product import ProductReport, RiskFinding
from jindiao.investigation import CHECK_CATALOG

_MODULE_LABELS = {
    "business_plan": "业务申报",
    "company_profile": "企业基本情况",
    "ownership": "股权及实际控制人",
    "business_analysis": "经营情况",
    "financial_analysis": "财务",
    "bank_flow_analysis": "银行流水",
    "external_verification": "外部核验",
}


def _risk_label(risk: RiskFinding) -> str:
    # Do not translate by deleting English fragments: that can change a fact's
    # meaning. Fall back to the server-owned Chinese check title instead.
    candidates = [risk.title, risk.risk_fact]
    candidates.extend(
        CHECK_CATALOG.get(item.id).title
        for item in risk.check_items
        if item.id in CHECK_CATALOG.check_ids
    )
    for candidate in candidates:
        text = re.sub(r"[。！？!?\r\n]+", "；", candidate).strip(" ；;，,")
        if (
            text
            and len(text) <= 36
            and re.search(r"[\u4e00-\u9fff]", text)
            and not re.search(r"[A-Za-z]", text)
        ):
            return text
    return "需进一步核实的风险事项"


def customer_risk_advice(
    *,
    risks: tuple[RiskFinding, ...],
    report: ProductReport,
    suggestion: Literal["proceed", "manual_review", "stop"],
    review_incomplete: bool,
) -> str:
    """Use two Chinese sentences without exposing process logs or changing facts."""
    gaps = [
        label
        for name, label in _MODULE_LABELS.items()
        if getattr(report, name).status != "complete"
    ]
    if risks:
        labels = tuple(dict.fromkeys(_risk_label(risk) for risk in risks))[:3]
        summary = "客户已审核风险主要包括" + "、".join(labels)
        if gaps or review_incomplete:
            summary += "，相关资料或核查尚未完成"
    else:
        summary = "本次已核查范围内未发现已审核风险"
        if gaps:
            summary += "，" + "、".join(gaps[:2]) + "等资料仍不完整，不代表整体无风险"
        elif review_incomplete:
            summary += "，核查尚未完成，不代表整体无风险"
        else:
            summary += "，结论仅适用于已获得资料"

    verification = (
        "建议逐项核验上述风险的最新状态及其对经营和偿债的影响"
        if risks
        else "建议补充核验相关资料、资金用途及还款来源"
    )
    if risks and gaps:
        verification += "，补充缺失资料并核实还款来源"
    if suggestion == "stop" or report.business_plan.suggested_amount == 0:
        strategy = "暂不新增授信，待风险化解并经重新评估后再议"
    elif suggestion == "manual_review":
        strategy = "复核通过前暂缓新增授信，后续结合偿债能力审慎确定额度、期限和担保条件"
    else:
        strategy = "核验通过后可按审批流程审慎推进授信，额度、期限和担保条件以审批为准"
    return f"{summary}。{verification}；{strategy}。"
