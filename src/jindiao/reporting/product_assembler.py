"""Combine normalized source facts, caller input and deterministic metrics."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, cast

from jindiao.acquisition.business_input import business_input_evidence
from jindiao.application.context import RunContext
from jindiao.contracts.business import ApplicationFields
from jindiao.contracts.product import (
    BusinessPlan,
    FinancialHighlights,
    MissingField,
    MissingReason,
    ProductEvidence,
    ProductMeta,
    ProductReport,
    ProductResult,
    ProductSubject,
    ProductSummary,
    ReportModule,
    RiskFinding,
    RiskPoints,
    Verification,
    VerificationFact,
)
from jindiao.contracts.product_facts import ProductFactBundle
from jindiao.contracts.report_inputs import ReviewedReportInputs
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.reporting.product_markdown import ProductMarkdownRenderer, ProductReportView
from jindiao.reporting.product_metrics import calculate_bank_flow, calculate_financials
from jindiao.reporting.product_risks import evidence_label
from jindiao.reporting.product_summary import customer_risk_advice


def _referenced(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"evidence_ids", "derived_from"} and isinstance(child, list):
                refs.update(str(item) for item in child)
            elif key == "evidence_id" and isinstance(child, str):
                refs.add(child)
            refs.update(_referenced(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_referenced(child))
    return refs


def _missing(value: object, prefix: str = "") -> list[str]:
    if value is None or value == []:
        return [prefix]
    if isinstance(value, dict):
        return [
            path
            for key, child in value.items()
            if key
            not in {
                "status",
                "analysis",
                "evidence_ids",
                "missing_fields",
                "generated_fields",
                "suggestion_source",
            }
            for path in _missing(child, f"{prefix}.{key}" if prefix else key)
        ]
    return []


def complete_missing_fields(
    report: ProductReport,
    evidence: tuple[ProductEvidence, ...] = (),
    *,
    verified_empty_fields: tuple[str, ...] = (),
    source_missing_fields: tuple[MissingField, ...] = (),
) -> ProductReport:
    modules: dict[str, object] = {}
    for name in tuple(ProductReport.model_fields)[:-1]:
        module: ReportModule = getattr(report, name)
        module_prefix = f"report.{name}."
        supported = {
            path
            for item in evidence
            if item.id in module.evidence_ids
            for path in item.supports_fields
        }
        disclosed_scalars = {
            module_prefix + key
            for key, value in module.model_dump(mode="json").items()
            if isinstance(value, str | int | float | bool) and value not in (None, "")
        } & supported
        gaps = [
            gap.model_copy(update={"field": gap.field.removeprefix(module_prefix)})
            for gap in source_missing_fields
            if gap.field.startswith(module_prefix)
            # A separate capability can be absent while another verified source
            # supplies the scalar. Collection completeness/errors still matter.
            and not (gap.reason == "capability_absent" and gap.field in disclosed_scalars)
        ]
        gaps.extend(module.missing_fields)
        known = {gap.field for gap in gaps}
        default_reason = (
            "not_provided" if name in {"business_plan", "bank_flow_analysis"} else "not_disclosed"
        )
        for path in _missing(module.model_dump(mode="json")):
            full_path = f"report.{name}.{path}"
            known_gap = any(path == item or path.startswith(item + ".") for item in known)
            verified_empty = any(
                full_path == item or full_path.startswith(item + ".")
                for item in verified_empty_fields
            )
            if not known_gap and not verified_empty:
                gaps.append(
                    MissingField(
                        field=path,
                        reason=cast(MissingReason, default_reason),
                        message="未提供调用方资料"
                        if default_reason == "not_provided"
                        else "本次来源未披露该字段",
                    )
                )
        modules[name] = module.model_copy(
            update={
                "status": "partial"
                if gaps and module.evidence_ids
                else "unavailable"
                if gaps
                else "complete",
                "missing_fields": tuple(gaps),
            }
        )
    return report.model_copy(update=modules)


class ProductReportAssembler:
    def prepare(
        self,
        facts: ProductFactBundle,
        *,
        context: RunContext,
        subject_id: str,
        queried_at: datetime,
    ) -> ProductFactBundle:
        report = facts.report
        by_id = {item.id: item for item in facts.evidence}
        user_evidence = business_input_evidence(
            context.business_context,
            subject_id=subject_id,
            run_id=context.run_id,
            queried_at=queried_at,
        )
        input_ids: dict[str, str] = {}
        for item in user_evidence:
            name = item.supports_fields[0].split(".", 1)[1]
            input_ids[name] = item.evidence_id
            by_id[item.evidence_id] = ProductEvidence(
                id=item.evidence_id,
                source_type="user_input",
                source_label=evidence_label(item),
                summary=item.claim,
                source_ref=item.raw_ref,
                data_as_of=item.as_of_date,
                queried_at=item.queried_at,
                supports_fields=("report.business_plan." + name,)
                if name in ApplicationFields.model_fields
                else (),
            )
        plan_values = {
            name: getattr(context.business_context, name) for name in ApplicationFields.model_fields
        }
        plan = BusinessPlan(
            **plan_values,
            company_name=report.company_profile.company_name,
            unified_social_credit_code=report.company_profile.unified_social_credit_code,
            industry=report.company_profile.industry,
            evidence_ids=tuple(
                dict.fromkeys(
                    (
                        *report.business_plan.evidence_ids,
                        *(
                            input_ids[name]
                            for name in ApplicationFields.model_fields
                            if name in input_ids
                        ),
                    )
                )
            ),
            analysis="申报信息由调用方提供; 建议条件将结合已审核风险和小额短期规则生成",
        )
        financial = calculate_financials(report.financial_analysis.periods)
        financial = financial.model_copy(
            update={
                "evidence_ids": report.financial_analysis.evidence_ids,
                "missing_fields": report.financial_analysis.missing_fields,
            }
        )
        bank_ids = tuple(
            dict.fromkeys(
                (
                    *report.bank_flow_analysis.evidence_ids,
                    *((input_ids["bank_flow"],) if "bank_flow" in input_ids else ()),
                )
            )
        )
        bank = calculate_bank_flow(context.business_context.bank_flow, evidence_ids=bank_ids)
        bank = bank.model_copy(
            update={
                "relationship_graph": report.bank_flow_analysis.relationship_graph,
                "evidence_ids": bank_ids,
            }
        )
        if "bank_flow" in input_ids:
            bank_input_id = input_ids["bank_flow"]
            by_id[bank_input_id] = by_id[bank_input_id].model_copy(
                update={
                    "supports_fields": ("report.bank_flow_analysis",),
                }
            )
        external = report.external_verification
        for key, input_name in (("credit", "credit_info"), ("internal_record", "internal_record")):
            supplied = getattr(context.business_context, input_name)
            if supplied is not None:
                identity = input_ids[input_name]
                parts = [
                    f"{name}={value}"
                    for name, value in supplied.model_dump(mode="json").items()
                    if value is not None
                ]
                credit_limit = getattr(supplied, "total_credit_limit", None)
                used_credit = getattr(supplied, "used_credit_amount", None)
                if (
                    input_name == "credit_info"
                    and credit_limit not in (None, 0)
                    and (used_credit is not None)
                ):
                    assert isinstance(credit_limit, int | float)
                    assert isinstance(used_credit, int | float)
                    parts.append(f"credit_usage_ratio={used_credit / credit_limit * 100:.2f}%")
                external = external.model_copy(
                    update={
                        key: Verification(
                            status="inconclusive",
                            conclusion="调用方提供的摘要, 未独立核验",
                            facts=(
                                VerificationFact(
                                    description="; ".join(parts), evidence_ids=(identity,)
                                ),
                            ),
                            evidence_ids=(identity,),
                        )
                    }
                )
                by_id[identity] = by_id[identity].model_copy(
                    update={
                        "supports_fields": (f"report.external_verification.{key}",),
                    }
                )
        external = external.model_copy(
            update={
                "evidence_ids": tuple(
                    dict.fromkeys(
                        (
                            *external.evidence_ids,
                            *(
                                input_ids[name]
                                for name in ("credit_info", "internal_record")
                                if name in input_ids
                            ),
                        )
                    )
                )
            }
        )
        profile = report.company_profile
        if financial.periods:
            latest = max(financial.periods, key=lambda item: item.period)
            profile = profile.model_copy(
                update={
                    "financial_highlights": FinancialHighlights(
                        period=latest.period,
                        revenue=latest.income_statement.revenue,
                        revenue_yoy=latest.ratios.revenue_yoy,
                        net_profit=latest.income_statement.net_profit,
                        net_profit_yoy=latest.ratios.net_profit_yoy,
                        debt_to_asset_ratio=latest.ratios.debt_to_asset_ratio,
                    )
                }
            )
        report = report.model_copy(
            update={
                "business_plan": plan,
                "company_profile": profile,
                "financial_analysis": financial,
                "bank_flow_analysis": bank,
                "external_verification": external,
            }
        )
        # One immutable derived record per calculation module; parents are source records.
        for name in ("financial_analysis", "bank_flow_analysis"):
            module = getattr(report, name)
            parents = tuple(sorted(_referenced(module.model_dump(mode="json"))))
            if not parents:
                continue
            identity = (
                "derived-"
                + hashlib.sha256((name + module.model_dump_json()).encode()).hexdigest()[:24]
            )
            by_id[identity] = ProductEvidence(
                id=identity,
                source_type="derived",
                source_label="确定性指标计算",
                summary="指标由所引来源按固定公式计算; 缺分母或不可比期间保持为空",
                source_ref="derived://prototype-v1/" + name,
                queried_at=queried_at,
                supports_fields=("report." + name,),
                derived_from=parents,
                is_mock=any(by_id[parent].is_mock for parent in parents),
            )
            update: dict[str, object] = {
                name: module.model_copy(
                    update={
                        "evidence_ids": (*module.evidence_ids, identity),
                    }
                )
            }
            if name == "financial_analysis" and financial.periods:
                update["company_profile"] = report.company_profile.model_copy(
                    update={
                        "evidence_ids": tuple(
                            dict.fromkeys((*report.company_profile.evidence_ids, identity))
                        ),
                    }
                )
                by_id[identity] = by_id[identity].model_copy(
                    update={
                        "supports_fields": (
                            "report.financial_analysis",
                            "report.company_profile.financial_highlights",
                        )
                    }
                )
            report = report.model_copy(update=update)
        evidence = tuple(by_id.values())
        return facts.model_copy(
            update={
                "report": complete_missing_fields(
                    report,
                    evidence,
                    verified_empty_fields=facts.verified_empty_fields,
                    source_missing_fields=facts.source_missing_fields,
                ),
                "evidence": evidence,
            }
        )

    def finish(
        self,
        *,
        facts: ProductFactBundle,
        risks: tuple[RiskFinding, ...],
        reviewed: ReviewedReportInputs,
        context: RunContext,
        subject: ProductSubject,
        mode: OrchestrationMode,
        generated_at: datetime,
    ) -> tuple[ProductResult, ProductReportView]:
        report = facts.report.model_copy(
            update={"risk_points": RiskPoints(finding_ids=tuple(r.id for r in risks))}
        )
        report_incomplete = any(
            getattr(report, key).status != "complete"
            for key in tuple(ProductReport.model_fields)[:-1]
        )
        suggestion: Literal["proceed", "manual_review", "stop"] = (
            "stop"
            if reviewed.decision.band.value == "reject"
            else "manual_review"
            if (
                reviewed.incomplete
                or report_incomplete
                or report.business_plan.suggested_amount == 0
                or reviewed.decision.band.value == "manual_review"
            )
            else "proceed"
        )
        summary = ProductSummary(
            risk_count=len(risks),
            ai_suggestion=suggestion,
            ai_suggestion_reason=customer_risk_advice(
                risks=risks,
                report=report,
                suggestion=suggestion,
                review_incomplete=reviewed.incomplete,
            ),
        )
        by_id = {item.id: item for item in facts.evidence}
        refs = _referenced(report.model_dump(mode="json")) | _referenced(
            [r.model_dump(mode="json") for r in risks]
        )
        pending = list(refs)
        while pending:
            identity = pending.pop()
            if identity not in by_id:
                raise ValueError("report references missing source Evidence")
            for parent in by_id[identity].derived_from:
                if parent not in refs:
                    refs.add(parent)
                    pending.append(parent)
        evidence = tuple(item for item in by_id.values() if item.id in refs)
        view = ProductReportView(
            subject=subject, summary=summary, report=report, risk_findings=risks, evidence=evidence
        )
        partial = reviewed.incomplete or report_incomplete
        result = ProductResult(
            meta=ProductMeta(
                request_id=context.request_id,
                run_id=context.run_id,
                mode=mode,
                status=RunStatus.PARTIAL if partial else RunStatus.COMPLETED,
                generated_at=generated_at,
                report_as_of=context.report_as_of,
                is_mock=any(item.is_mock for item in evidence),
            ),
            **view.model_dump(),
            report_markdown=ProductMarkdownRenderer().render(
                view, policy=context.reporting_policy.policy
            ),
        )
        return result, view
