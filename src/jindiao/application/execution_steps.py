# ruff: noqa: RUF001 -- Chinese product copy uses native punctuation.
"""Project internal workflow milestones into stable user-facing execution steps."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from pydantic import JsonValue

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.contracts.events import ExecutionEventType
from jindiao.contracts.execution_steps import (
    EXECUTION_STEP_IDS,
    ExecutionKeyFact,
    ExecutionPlan,
    ExecutionSourceTag,
    ExecutionStepDefinition,
    ExecutionStepOutcome,
    ExecutionStepSnapshot,
    ExecutionStepState,
)
from jindiao.contracts.product import ProductResult, ReportModule, RiskFinding, Verification
from jindiao.contracts.public_result import PublicResult
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.contracts.runs import ActorView, RunStage
from jindiao.investigation.catalog import CHECK_CATALOG
from jindiao.orchestration.base import TeamRuntimeEvent

_DEFINITIONS = (
    ExecutionStepDefinition(
        step_id="company-verification",
        order=1,
        title="企业主体与工商信息",
        objective="核验企业身份、登记状态、主要人员和基础经营信息。",
    ),
    ExecutionStepDefinition(
        step_id="ownership-and-relations",
        order=2,
        title="股权与关联关系",
        objective="识别股东、实际控制人、受益所有人及重要关联关系。",
    ),
    ExecutionStepDefinition(
        step_id="business-and-supply-chain",
        order=3,
        title="经营情况与上下游",
        objective="分析主营业务、行业、客户、供应商及集中度。",
    ),
    ExecutionStepDefinition(
        step_id="finance-cashflow-solvency",
        order=4,
        title="财务、流水与偿债能力",
        objective="核验财务报表、银行流水、现金流和偿债指标。",
    ),
    ExecutionStepDefinition(
        step_id="external-risk-screening",
        order=5,
        title="司法、行政、税务与舆情",
        objective="扫描司法执行、行政处罚、税务、信用及公开舆情风险。",
    ),
    ExecutionStepDefinition(
        step_id="cross-risk-review",
        order=6,
        title="风险交叉审核",
        objective="交叉核对风险事实、核查结论、证据引用和冲突。",
    ),
    ExecutionStepDefinition(
        step_id="structured-report-generation",
        order=7,
        title="结构化尽调报告生成",
        objective="整合业务申报方案、报告第1至7版块和风险发现。",
    ),
)
_DEFINITION_BY_ID = {item.step_id: item for item in _DEFINITIONS}
_OBSERVABLE_EVENTS = {
    "entity.resolved",
    "acquisition.started",
    "acquisition.completed",
    "agent.started",
    "member.started",
    "agent.completed",
    "check.started",
    "check.completed",
    "submission.accepted",
    "evidence.collected",
    "source.fallback",
    "deepsearch.fallback",
    "review.started",
    "review.submitted",
    "review.issue",
    "review.repair_requested",
    "repair.requested",
    "conflict.detected",
    "report.started",
    "section.completed",
}
_SECTION_TO_STEP = {
    "company_profile": "company-verification",
    "ownership": "ownership-and-relations",
    "business_analysis": "business-and-supply-chain",
    "financial_analysis": "finance-cashflow-solvency",
    "bank_flow_analysis": "finance-cashflow-solvency",
    "external_verification": "external-risk-screening",
}


@dataclass(frozen=True)
class ExecutionProjectionEvent:
    event_type: ExecutionEventType
    payload: dict[str, JsonValue]
    stage: RunStage
    actor: ActorView


@dataclass
class _StepRuntime:
    state: ExecutionStepState = ExecutionStepState.PENDING
    progress_percent: int | None = None
    executors: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    last_message: str | None = None


def build_execution_plan(mode: OrchestrationMode) -> ExecutionPlan:
    """Return the fixed product plan shared by both execution topologies."""

    return ExecutionPlan(mode=mode, steps=_DEFINITIONS)


class ExecutionStepProjector:
    """Stateful projection of trusted milestones, never model token streams."""

    def __init__(
        self,
        *,
        mode: OrchestrationMode,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.mode = mode
        self._clock = clock
        self._steps = {step_id: _StepRuntime() for step_id in EXECUTION_STEP_IDS}
        self._agent_steps: dict[str, set[str]] = {}
        self._plan_emitted = False
        self._report_sections: set[str] = set()
        self._finished = False

    def start(self) -> ExecutionProjectionEvent:
        if self._plan_emitted:
            raise ValueError("execution plan has already been emitted")
        self._plan_emitted = True
        plan = build_execution_plan(self.mode)
        return ExecutionProjectionEvent(
            event_type=ExecutionEventType.PLAN_CREATED,
            payload={"plan": cast(JsonValue, plan.model_dump(mode="json"))},
            stage=RunStage.ACQUISITION,
            actor=ActorView(kind="system", id="jindiao", role="coordinator"),
        )

    def observe(self, event: TeamRuntimeEvent) -> tuple[ExecutionProjectionEvent, ...]:
        """Convert only events with a reliable product-step meaning."""

        if event.event_type not in _OBSERVABLE_EVENTS or not self._plan_emitted or self._finished:
            return ()

        actor = ActorView(
            kind="agent" if event.member_name else "system",
            id=event.member_name or "jindiao",
            role=(
                cast(str, event.payload["role"])
                if isinstance(event.payload.get("role"), str)
                else None
            ),
        )
        step_ids = self._step_ids(event)
        if not step_ids:
            return ()
        if event.member_name:
            self._agent_steps.setdefault(event.member_name, set()).update(step_ids)

        projected: list[ExecutionProjectionEvent] = []
        message, progress = self._progress(event)
        for step_id in step_ids:
            runtime = self._steps[step_id]
            if runtime.state not in {ExecutionStepState.PENDING, ExecutionStepState.RUNNING}:
                continue
            started = runtime.state is ExecutionStepState.PENDING
            executor_changed = self._add_executor(runtime, event.member_name)
            if (
                message == runtime.last_message
                and progress == runtime.progress_percent
                and not executor_changed
            ):
                continue
            runtime.state = ExecutionStepState.RUNNING
            runtime.started_at = runtime.started_at or self._clock()
            runtime.progress_percent = progress
            runtime.last_message = message
            projected.append(
                self._step_event(
                    ExecutionEventType.STEP_STARTED
                    if started
                    else ExecutionEventType.STEP_PROGRESS,
                    step_id,
                    actor=actor,
                    conclusion=None,
                    stage=self._event_stage(event),
                )
            )
        return tuple(projected)

    def complete(self, result: PublicResult) -> tuple[ExecutionProjectionEvent, ...]:
        """Complete every step from the validated public result."""

        if not self._plan_emitted or self._finished:
            return ()
        projected: list[ExecutionProjectionEvent] = []
        system = ActorView(kind="system", id="jindiao", role="coordinator")
        for step_id in EXECUTION_STEP_IDS:
            runtime = self._steps[step_id]
            if runtime.state is ExecutionStepState.FAILED:
                continue
            outcome, conclusion, facts, gaps, sources = self._completion(step_id, result)
            runtime.state = ExecutionStepState.COMPLETED
            runtime.progress_percent = 100
            runtime.last_message = None
            projected.append(
                self._step_event(
                    ExecutionEventType.STEP_COMPLETED,
                    step_id,
                    actor=system,
                    outcome=outcome,
                    conclusion=conclusion,
                    facts=facts,
                    gaps=gaps,
                    sources=sources,
                    stage=RunStage.REPORTING,
                )
            )
        self._finished = True
        return tuple(projected)

    def fail_active(self) -> tuple[ExecutionProjectionEvent, ...]:
        """Fail only active steps using a redaction-safe product message."""

        system = ActorView(kind="system", id="jindiao", role="coordinator")
        projected: list[ExecutionProjectionEvent] = []
        for step_id in EXECUTION_STEP_IDS:
            runtime = self._steps[step_id]
            if runtime.state is not ExecutionStepState.RUNNING:
                continue
            runtime.state = ExecutionStepState.FAILED
            runtime.last_message = None
            projected.append(
                self._step_event(
                    ExecutionEventType.STEP_FAILED,
                    step_id,
                    actor=system,
                    conclusion="该环节执行失败，请查看本次运行的错误信息。",
                )
            )
        self._finished = True
        return tuple(projected)

    @staticmethod
    def _add_executor(runtime: _StepRuntime, executor: str | None) -> bool:
        if (
            not executor
            or len(executor) > 160
            or executor in runtime.executors
            or len(runtime.executors) >= 8
        ):
            return False
        runtime.executors.append(executor)
        return True

    def _step_event(
        self,
        event_type: ExecutionEventType,
        step_id: str,
        *,
        actor: ActorView,
        conclusion: str | None,
        outcome: ExecutionStepOutcome | None = None,
        facts: tuple[ExecutionKeyFact, ...] = (),
        gaps: tuple[str, ...] = (),
        sources: tuple[ExecutionSourceTag, ...] = (),
        stage: RunStage | None = None,
    ) -> ExecutionProjectionEvent:
        definition = _DEFINITION_BY_ID[step_id]
        runtime = self._steps[step_id]
        duration: int | None = None
        if runtime.started_at and runtime.state in {
            ExecutionStepState.COMPLETED,
            ExecutionStepState.FAILED,
        }:
            duration = max(0, int((self._clock() - runtime.started_at).total_seconds() * 1000))
        snapshot = ExecutionStepSnapshot(
            **definition.model_dump(),
            state=runtime.state,
            outcome=outcome,
            conclusion=self._short(conclusion) if conclusion else None,
            progress_percent=runtime.progress_percent,
            progress_message=runtime.last_message,
            key_facts=facts,
            gaps=gaps,
            source_tags=sources,
            executor_ids=tuple(runtime.executors),
            duration_ms=duration,
        )
        return ExecutionProjectionEvent(
            event_type=event_type,
            payload={"step": cast(JsonValue, snapshot.model_dump(mode="json"))},
            stage=stage or self._stage(step_id),
            actor=actor,
        )

    def _step_ids(self, event: TeamRuntimeEvent) -> tuple[str, ...]:
        if event.event_type == "entity.resolved":
            return ("company-verification",)
        if event.event_type == "acquisition.started":
            return ("company-verification",)
        if event.event_type == "acquisition.completed":
            return tuple(
                step_id
                for step_id in EXECUTION_STEP_IDS[:5]
                if self._steps[step_id].state is ExecutionStepState.RUNNING
            )
        if event.event_type.startswith(("review.", "repair.", "conflict.")):
            return ("cross-risk-review",)
        if event.event_type.startswith(("section.", "report.")):
            section_id = event.payload.get("section_id")
            if isinstance(section_id, str) and section_id in {
                "business_plan",
                "risk_points",
                *_SECTION_TO_STEP,
            }:
                self._report_sections.add(section_id)
            return ("structured-report-generation",)
        if event.event_type == "agent.completed" and event.member_name:
            return tuple(
                step_id
                for step_id in EXECUTION_STEP_IDS
                if step_id in self._agent_steps.get(event.member_name, set())
            )

        identifiers: list[str] = []
        for key in ("task_id", "check_id", "capability", "acquisition_id"):
            value = event.payload.get(key)
            if isinstance(value, str):
                identifiers.append(value)
        for key in ("task_ids", "check_ids"):
            value = event.payload.get(key)
            if isinstance(value, list):
                identifiers.extend(item for item in value if isinstance(item, str))
        evidence = event.payload.get("evidence")
        if isinstance(evidence, dict):
            supports = evidence.get("supports_fields")
            if isinstance(supports, list):
                identifiers.extend(item for item in supports if isinstance(item, str))

        mapped: set[str] = set()
        for identifier in identifiers:
            mapped.update(self._steps_for_identifier(identifier))
        if event.member_name and not mapped and not identifiers:
            mapped.update(self._agent_steps.get(event.member_name, set()))
        return tuple(step_id for step_id in EXECUTION_STEP_IDS if step_id in mapped)

    @staticmethod
    def _steps_for_identifier(identifier: str) -> set[str]:
        raw = identifier
        if raw.startswith("check:"):
            raw = raw.removeprefix("check:")
        elif raw.startswith("acquire:"):
            raw = raw.removeprefix("acquire:")
        elif raw.startswith("review:"):
            return {"cross-risk-review"}
        if raw.startswith("report."):
            parts = raw.split(".")
            if len(parts) > 2 and parts[1] == "external_verification":
                if parts[2] == "registration":
                    return {"company-verification"}
                if parts[2] == "credit":
                    return {"finance-cashflow-solvency"}
            return (
                {_SECTION_TO_STEP[parts[1]]}
                if len(parts) > 1 and parts[1] in _SECTION_TO_STEP
                else set()
            )
        try:
            check = CHECK_CATALOG.get(raw)
        except KeyError:
            try:
                return ExecutionStepProjector._steps_for_acquisition(raw)
            except KeyError:
                return set()
        return {
            _SECTION_TO_STEP[section]
            for section in check.report_section_ids
            if section in _SECTION_TO_STEP
        }

    @staticmethod
    def _steps_for_acquisition(acquisition_id: str) -> set[str]:
        item = ACQUISITION_CATALOG.get(acquisition_id)
        steps: set[str] = set()
        for path in item.report_fields:
            parts = path.split(".")
            if len(parts) < 2:
                continue
            section = parts[1]
            if section == "external_verification" and len(parts) > 2 and parts[2] == "registration":
                steps.add("company-verification")
                continue
            if section == "external_verification" and len(parts) > 2 and parts[2] == "credit":
                steps.add("finance-cashflow-solvency")
                continue
            if section in _SECTION_TO_STEP:
                steps.add(_SECTION_TO_STEP[section])
        return steps

    def _progress(self, event: TeamRuntimeEvent) -> tuple[str, int | None]:
        if event.event_type == "acquisition.completed":
            return "资料采集环节已结束，可用证据将用于固定核查。", None
        if event.event_type == "evidence.collected":
            return "已取得可引用证据，正在交叉核验。", None
        if event.event_type in {"source.fallback", "deepsearch.fallback"}:
            return "部分数据源存在缺口，正在按可用证据继续核查。", None
        if event.event_type == "agent.completed":
            return "该 Agent 已返回结果，等待校验和汇总。", None
        if event.event_type.startswith(("review.", "repair.", "conflict.")):
            return "正在复核风险事实、证据引用和冲突。", None
        if event.event_type == "report.started":
            return "正在整合业务申报方案、报告第1至7版块和风险发现。", None
        if (
            event.event_type in {"entity.resolved", "acquisition.started"}
            or event.payload.get("phase") == "acquisition"
        ):
            return "正在采集和核验企业公开资料。", None
        if event.event_type in {"check.completed", "submission.accepted"}:
            return "已有核查结果，等待汇总与交叉审核。", None
        if event.event_type == "section.completed":
            total = 8
            count = min(total, len(self._report_sections))
            return f"结构化报告已生成 {count}/{total} 个版块，正在校验汇总。", None
        return "正在依据已采集资料执行核查。", None

    @staticmethod
    def _event_stage(event: TeamRuntimeEvent) -> RunStage:
        from jindiao.api.event_mapper import EventMapper

        return EventMapper._stage_for(event)

    def _completion(
        self, step_id: str, result: PublicResult
    ) -> tuple[
        ExecutionStepOutcome,
        str,
        tuple[ExecutionKeyFact, ...],
        tuple[str, ...],
        tuple[ExecutionSourceTag, ...],
    ]:
        if not isinstance(result, ProductResult):
            outcome = (
                ExecutionStepOutcome.NORMAL
                if step_id == "structured-report-generation"
                and result.meta.status is RunStatus.COMPLETED
                else ExecutionStepOutcome.INCONCLUSIVE
            )
            return (
                outcome,
                "该环节已完成；旧版结果未提供可展开的结构化步骤摘要。",
                (),
                (),
                (),
            )

        _, gaps = self._references_and_gaps(step_id, result)
        facts, source_tags = self._facts_and_sources(step_id, result)
        outcome = self._outcome(step_id, result, gaps)
        return outcome, self._conclusion(step_id, result, outcome), facts, gaps, source_tags

    def _references_and_gaps(
        self, step_id: str, result: ProductResult
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        values: tuple[object, ...]
        if step_id == "company-verification":
            values = (
                result.report.company_profile,
                result.report.external_verification.registration,
            )
        elif step_id == "ownership-and-relations":
            values = (result.report.ownership,)
        elif step_id == "business-and-supply-chain":
            values = (result.report.business_analysis,)
        elif step_id == "finance-cashflow-solvency":
            values = (
                result.report.financial_analysis,
                result.report.bank_flow_analysis,
                result.report.external_verification.credit,
            )
        elif step_id == "external-risk-screening":
            verification = result.report.external_verification
            values = (
                verification.judicial,
                verification.tax,
                verification.public_opinion,
                verification.internal_record,
            )
        elif step_id == "cross-risk-review":
            values = (*result.risk_findings, result.report.risk_points)
        else:
            values = (result.report,)
        references = self._unique(self._collect_key(values, "evidence_ids"))[:8]
        gaps = self._unique(
            self._short(item) for item in self._collect_gaps(values) if item.strip()
        )[:3]
        return references, gaps

    @classmethod
    def _facts_and_sources(
        cls, step_id: str, result: ProductResult
    ) -> tuple[tuple[ExecutionKeyFact, ...], tuple[ExecutionSourceTag, ...]]:
        """Keep each fact's own lineage; never borrow a neighbouring fact's source."""
        report = result.report
        candidates: list[tuple[str, tuple[str, ...]]] = []

        def add(text: str, values: Iterable[object]) -> None:
            candidates.append((text, cls._unique(cls._collect_key(values, "evidence_ids"))))

        if step_id == "company-verification":
            profile = report.company_profile
            for name, label in (
                ("registration_status", "登记状态"),
                ("legal_representative", "法定代表人"),
                ("industry", "所属行业"),
            ):
                value = getattr(profile, name)
                if value:
                    ids = tuple(
                        item.id
                        for item in result.evidence
                        if f"report.company_profile.{name}" in item.supports_fields
                    )
                    candidates.append((f"{label}：{value}", ids))
        elif step_id == "ownership-and-relations":
            ownership = report.ownership
            for values, label in (
                (ownership.shareholders, "股东"),
                (ownership.actual_controllers, "实际控制人"),
                (ownership.related_companies, "关联企业"),
            ):
                if values:
                    add(f"已识别{label} {len(values)} 个", values)
        elif step_id == "business-and-supply-chain":
            business = report.business_analysis
            if business.main_business:
                candidates.append(
                    (
                        f"主营业务：{business.main_business}",
                        tuple(
                            item.id
                            for item in result.evidence
                            if "report.business_analysis.main_business" in item.supports_fields
                        ),
                    )
                )
            for counterparties, label in (
                (business.customers, "客户"),
                (business.suppliers, "供应商"),
            ):
                if counterparties:
                    add(f"已识别{label} {len(counterparties)} 家", counterparties)
        elif step_id == "finance-cashflow-solvency":
            financial = report.financial_analysis
            bank = report.bank_flow_analysis
            if financial.periods:
                add(f"财务数据覆盖 {len(financial.periods)} 个期间", financial.periods)
            if bank.monthly_totals:
                add(f"银行流水覆盖 {len(bank.monthly_totals)} 个月", (bank,))
            labels = {"passed": "已通过", "failed": "存在差异", "inconclusive": "资料不足，待核实"}
            add(f"财务勾稽：{labels[financial.reconciliation.status]}", (financial.reconciliation,))
        elif step_id == "external-risk-screening":
            verification = report.external_verification
            for item, label in (
                (verification.judicial, "司法核验"),
                (verification.tax, "税务核验"),
                (verification.public_opinion, "舆情核验"),
            ):
                add(f"{label}：{item.conclusion}", (item,))
        elif step_id == "cross-risk-review":
            candidates.extend(
                (item.risk_fact, tuple(tag.evidence_id for tag in item.evidence_tags))
                for item in result.risk_findings[:3]
            )
        evidence = {item.id: item for item in result.evidence}
        sources: dict[str, ExecutionSourceTag] = {}
        facts: list[ExecutionKeyFact] = []
        for text, ids in candidates:
            if not text.strip():
                continue
            refs: list[str] = []
            for evidence_id in ids:
                if evidence_id not in evidence or len(evidence_id) > 160:
                    continue
                if evidence_id not in sources and len(sources) >= 8:
                    continue
                source = evidence[evidence_id]
                sources[evidence_id] = ExecutionSourceTag(
                    label=cls._short(source.source_label)[:80] or "证据来源",
                    evidence_id=evidence_id,
                    source_type=source.source_type,
                )
                refs.append(evidence_id)
            # Unsupported conclusions stay in the result, not in evidence-backed facts.
            if refs and cls._short(text) not in {fact.text for fact in facts}:
                facts.append(ExecutionKeyFact(text=cls._short(text), evidence_ids=tuple(refs)))
            if len(facts) == 3:
                break
        return tuple(facts), tuple(sources.values())

    def _outcome(
        self, step_id: str, result: ProductResult, gaps: tuple[str, ...]
    ) -> ExecutionStepOutcome:
        if step_id == "structured-report-generation":
            return (
                ExecutionStepOutcome.INCONCLUSIVE
                if result.meta.status is RunStatus.PARTIAL or gaps
                else ExecutionStepOutcome.NORMAL
            )
        if step_id == "cross-risk-review":
            if result.risk_findings:
                return ExecutionStepOutcome.ATTENTION
            return (
                ExecutionStepOutcome.INCONCLUSIVE
                if result.meta.status is RunStatus.PARTIAL or gaps
                else ExecutionStepOutcome.NORMAL
            )
        relevant_risk = any(step_id in self._risk_steps(item) for item in result.risk_findings)
        risk_evidence = {
            tag.evidence_id for risk in result.risk_findings for tag in risk.evidence_tags
        }
        relevant_risk = relevant_risk or any(
            step_id in self._steps_for_identifier(path)
            for evidence in result.evidence
            if evidence.id in risk_evidence
            for path in evidence.supports_fields
        )
        verifications = self._verifications(step_id, result)
        if (
            relevant_risk
            or any(item.status == "attention" for item in verifications)
            or (
                step_id == "finance-cashflow-solvency"
                and result.report.financial_analysis.reconciliation.status == "failed"
            )
        ):
            return ExecutionStepOutcome.ATTENTION
        modules = self._modules(step_id, result)
        if (
            gaps
            or any(module.status in {"partial", "unavailable"} for module in modules)
            or any(item.status == "inconclusive" for item in verifications)
        ):
            return ExecutionStepOutcome.INCONCLUSIVE
        return ExecutionStepOutcome.NORMAL

    @classmethod
    def _risk_steps(cls, risk: RiskFinding) -> set[str]:
        result: set[str] = set()
        for item in risk.check_items:
            result.update(cls._steps_for_identifier(item.id))
        return result

    @staticmethod
    def _verifications(step_id: str, result: ProductResult) -> tuple[Verification, ...]:
        verification = result.report.external_verification
        if step_id == "company-verification":
            return (verification.registration,)
        if step_id == "finance-cashflow-solvency":
            return (verification.credit,)
        if step_id == "external-risk-screening":
            return (
                verification.judicial,
                verification.tax,
                verification.public_opinion,
                verification.internal_record,
            )
        return ()

    @staticmethod
    def _modules(step_id: str, result: ProductResult) -> tuple[ReportModule, ...]:
        report = result.report
        if step_id == "company-verification":
            return (report.company_profile,)
        if step_id == "ownership-and-relations":
            return (report.ownership,)
        if step_id == "business-and-supply-chain":
            return (report.business_analysis,)
        if step_id == "finance-cashflow-solvency":
            return (report.financial_analysis, report.bank_flow_analysis)
        if step_id == "external-risk-screening":
            return (report.external_verification,)
        return ()

    @staticmethod
    def _conclusion(step_id: str, result: ProductResult, outcome: ExecutionStepOutcome) -> str:
        suffix = {
            ExecutionStepOutcome.NORMAL: "未见需单列关注的异常。",
            ExecutionStepOutcome.ATTENTION: "发现需关注事项，请结合关键事实与报告核实。",
            ExecutionStepOutcome.INCONCLUSIVE: "仍有资料缺口，需结合补充证据判断。",
        }[outcome]
        report = result.report
        if outcome is ExecutionStepOutcome.INCONCLUSIVE:
            if step_id == "structured-report-generation":
                return "已生成结构化报告，部分内容仍有资料缺口，请结合报告中的缺失说明核实。"
            return f"{_DEFINITION_BY_ID[step_id].title}已完成当前可用资料核查；{suffix}"
        if step_id == "company-verification":
            status = report.company_profile.registration_status or "待核实"
            return f"企业登记状态为{status}；{suffix}"
        if step_id == "ownership-and-relations":
            return (
                f"识别 {len(report.ownership.shareholders)} 名股东、"
                f"{len(report.ownership.actual_controllers)} 名实际控制人；{suffix}"
            )
        if step_id == "business-and-supply-chain":
            return (
                f"经营与上下游核查完成，覆盖 {len(report.business_analysis.customers)} 家客户、"
                f"{len(report.business_analysis.suppliers)} 家供应商；{suffix}"
            )
        if step_id == "finance-cashflow-solvency":
            return (
                f"财务与流水核查完成，覆盖 {len(report.financial_analysis.periods)} 个财务期间；"
                f"{suffix}"
            )
        if step_id == "external-risk-screening":
            return f"司法、行政、税务与舆情扫描完成；{suffix}"
        if step_id == "cross-risk-review":
            return (
                f"完成风险事实和证据交叉审核，形成 {len(result.risk_findings)} 条风险发现；{suffix}"
            )
        return "业务申报方案、报告第1至7版块和风险发现已完成结构化生成。"

    @staticmethod
    def _collect_key(values: Iterable[object], key: str) -> tuple[str, ...]:
        found: list[str] = []

        def visit(value: object) -> None:
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            if isinstance(value, Mapping):
                for name, child in value.items():
                    if name == key and isinstance(child, list | tuple):
                        found.extend(item for item in child if isinstance(item, str))
                    else:
                        visit(child)
            elif isinstance(value, list | tuple):
                for child in value:
                    visit(child)

        for item in values:
            visit(item)
        return tuple(found)

    @staticmethod
    def _collect_gaps(values: Iterable[object]) -> tuple[str, ...]:
        found: list[str] = []

        def visit(value: object) -> None:
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            if isinstance(value, Mapping):
                missing = value.get("missing_fields")
                if isinstance(missing, list):
                    for item in missing:
                        if isinstance(item, Mapping):
                            message = item.get("message")
                            if isinstance(message, str):
                                found.append(message)
                if value.get("status") == "inconclusive" and isinstance(
                    value.get("conclusion"), str
                ):
                    found.append(str(value["conclusion"]))
                elif value.get("status") in {"partial", "unavailable"} and not missing:
                    found.append("该环节的部分资料尚不可用，无法作出完整判断。")
                for name, child in value.items():
                    if name != "missing_fields":
                        visit(child)
            elif isinstance(value, list | tuple):
                for child in value:
                    visit(child)

        for item in values:
            visit(item)
        return ExecutionStepProjector._unique(found)

    @staticmethod
    def _unique(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _short(value: str | None) -> str:
        if value is None:
            return ""
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= 240 else normalized[:239] + "…"

    @staticmethod
    def _stage(step_id: str) -> RunStage:
        if step_id == "company-verification":
            return RunStage.ACQUISITION
        if step_id == "cross-risk-review":
            return RunStage.ADJUDICATION
        if step_id == "structured-report-generation":
            return RunStage.REPORTING
        return RunStage.INVESTIGATION


__all__ = [
    "ExecutionProjectionEvent",
    "ExecutionStepProjector",
    "build_execution_plan",
]
