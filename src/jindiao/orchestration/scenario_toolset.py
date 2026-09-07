"""Deterministic investigation tools backed by one frozen Mock scenario."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from itertools import pairwise
from typing import cast

from pydantic import JsonValue

from jindiao.application.context import RunContext
from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.errors import ErrorCategory, ErrorCode, ErrorRecord
from jindiao.contracts.evidence import CoverageItem, Evidence, SourceStatus, SourceType
from jindiao.contracts.investigation import Finding, RiskClass, Severity

from .base import (
    CancellationToken,
    DomainInvestigation,
    check_cancellation,
    require_deterministic_harness,
)


def _thaw(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    raise TypeError(f"unsupported scenario value: {type(value).__name__}")


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_records(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, tuple):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


class ScenarioToolset:
    """Deterministic fixture toolset; never access evaluator expected answers."""

    formal_agent_run = False

    def __init__(
        self,
        *,
        source_status_overrides: Mapping[str, SourceStatus] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._status_overrides = dict(source_status_overrides or {})
        self._clock = clock or (lambda: datetime.now(UTC))
        self.call_order: list[str] = []

    async def resolve_subject(
        self,
        context: RunContext,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ResolvedSubject:
        require_deterministic_harness(context, component=type(self).__name__)
        check_cancellation(cancellation_token)
        self.call_order.append("resolve_subject")
        company = context.scenario.read_json("company.json")
        return ResolvedSubject(
            subject_id=f"mock:{context.scenario.manifest.scenario_id}",
            company_name=str(company["company_name"]),
            unified_social_credit_code=str(company["unified_social_credit_code"]),
            region=str(company.get("registered_address", "")),
            registration_status=str(company.get("registration_status", "")),
            source=SubjectSource.MOCK,
            resolved_at=self._clock(),
        )

    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> DomainInvestigation:
        require_deterministic_harness(context, component=type(self).__name__)
        check_cancellation(cancellation_token)
        self.call_order.append(domain)
        path = "governance.json" if domain == "governance" else f"{domain}.json"
        status = self._status_overrides.get(domain)
        if status is SourceStatus.SOURCE_ERROR and not context.policy.allow_degraded_mock:
            return self._unavailable(domain, status, error="simulated source error")
        if status is SourceStatus.VERIFIED_EMPTY:
            return self._unavailable(domain, status)

        effective_status = (
            SourceStatus.DEGRADED_MOCK
            if status is SourceStatus.SOURCE_ERROR
            else status or SourceStatus.VERIFIED_RECORDS
        )
        data = context.scenario.read_json(path)
        if domain == "governance":
            artifact = self._governance(context, subject, data, effective_status)
        elif domain == "judicial":
            artifact = self._judicial(context, subject, data, effective_status)
        elif domain == "operations":
            artifact = self._operations(context, subject, data, effective_status)
        elif domain == "peers":
            artifact = self._peers(context, subject, data, effective_status)
        else:
            raise ValueError(f"unsupported scenario domain: {domain}")

        if effective_status is SourceStatus.CAPABILITY_ABSENT:
            coverage = CoverageItem(
                domain=domain,
                capability=f"{domain}_snapshot",
                status=effective_status,
                fallback_reason="tianyancha_capability_absent_mock_used",
            )
        else:
            coverage = CoverageItem(
                domain=domain,
                capability=f"{domain}_snapshot",
                status=effective_status,
                record_count=max(1, len(artifact.evidence)),
                fallback_reason=(
                    "source_error_degraded_mock"
                    if effective_status is SourceStatus.DEGRADED_MOCK
                    else None
                ),
            )
        return artifact.model_copy(update={"coverage_items": (coverage,)})

    @staticmethod
    def _unavailable(
        domain: str,
        status: SourceStatus,
        *,
        error: str | None = None,
    ) -> DomainInvestigation:
        errors = (
            (
                ErrorRecord(
                    category=ErrorCategory.SOURCE,
                    code=ErrorCode.SOURCE_UNAVAILABLE,
                    message="Source unavailable during domain investigation",
                    recoverable=True,
                    details={"domain": domain},
                ),
            )
            if status is SourceStatus.SOURCE_ERROR
            else ()
        )
        return DomainInvestigation(
            task_id=f"single-{domain}",
            domain=domain,
            coverage_items=(
                CoverageItem(
                    domain=domain,
                    capability=f"{domain}_snapshot",
                    status=status,
                    error=error,
                ),
            ),
            errors=errors,
        )

    def _evidence(
        self,
        *,
        context: RunContext,
        subject: ResolvedSubject,
        evidence_id: str,
        domain: str,
        field: str,
        claim: str,
        value: object,
        path: str,
        fragment: str,
        status: SourceStatus,
    ) -> Evidence:
        return Evidence(
            evidence_id=evidence_id,
            claim=claim,
            value=_thaw(value),
            subject_id=subject.subject_id,
            source_type=SourceType.MOCK,
            source_status=status,
            source_tool="scenario_repository",
            source_record_id=fragment,
            queried_at=self._clock(),
            as_of_date=context.scenario.manifest.as_of_date,
            confidence=1,
            is_mock=True,
            supports_fields=(f"{domain}.{field}",),
            raw_ref=(
                f"mock://{context.scenario.manifest.scenario_id}/"
                f"{context.scenario.manifest.version}/{path}#{fragment}"
            ),
        )

    def _governance(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        data: Mapping[str, object],
        status: SourceStatus,
    ) -> DomainInvestigation:
        company = context.scenario.read_json("company.json")
        registration = self._evidence(
            context=context,
            subject=subject,
            evidence_id="ev-company-registration",
            domain="company",
            field="registration_status",
            claim="企业登记状态",
            value=company.get("registration_status"),
            path="company.json",
            fragment="registration_status",
            status=status,
        )
        governance = self._evidence(
            context=context,
            subject=subject,
            evidence_id="ev-governance-profile",
            domain="governance",
            field="profile",
            claim="股东、高管与对外投资信息",
            value=data,
            path="governance.json",
            fragment="profile",
            status=status,
        )
        return DomainInvestigation(
            task_id="single-governance",
            domain="governance",
            findings=(
                Finding(
                    finding_id="finding-registration-active",
                    subject_id=subject.subject_id,
                    domain="identity",
                    claim=f"企业登记状态为{company.get('registration_status')}",
                    value=_thaw(company.get("registration_status")),
                    risk_class=RiskClass.NON_RISK,
                    severity=Severity.INFO,
                    evidence_ids=(registration.evidence_id,),
                ),
                Finding(
                    finding_id="finding-governance-profile",
                    subject_id=subject.subject_id,
                    domain="governance",
                    claim="企业治理与关联主体信息已采集",
                    value=True,
                    risk_class=RiskClass.NON_RISK,
                    severity=Severity.INFO,
                    evidence_ids=(governance.evidence_id,),
                ),
            ),
            evidence=(registration, governance),
            coverage_items=(
                CoverageItem(
                    domain="governance",
                    capability="governance_snapshot",
                    status=SourceStatus.VERIFIED_RECORDS,
                    record_count=2,
                ),
            ),
            section_data={
                "company-profile": {
                    **cast(dict[str, JsonValue], _thaw(company)),
                    **cast(dict[str, JsonValue], _thaw(data)),
                },
                "related-parties": cast(dict[str, JsonValue], _thaw(data)),
            },
        )

    def _judicial(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        data: Mapping[str, object],
        status: SourceStatus,
    ) -> DomainInvestigation:
        evidence: list[Evidence] = []
        findings: list[Finding] = []
        dishonest = _as_records(data.get("dishonest_records"))
        restrictions = _as_records(data.get("restrictions_on_high_consumption"))
        executions = _as_records(data.get("executions"))
        if dishonest:
            item = self._evidence(
                context=context,
                subject=subject,
                evidence_id="ev-judicial-dishonest-1",
                domain="judicial",
                field="dishonest",
                claim="失信被执行人记录",
                value=dishonest[0],
                path="judicial.json",
                fragment="dishonest_records/0",
                status=status,
            )
            evidence.append(item)
            findings.append(
                Finding(
                    finding_id="finding-dishonest",
                    subject_id=subject.subject_id,
                    domain="judicial",
                    claim="企业存在失信被执行人记录",
                    value=_thaw(dishonest[0].get("performance_status")),
                    risk_class=RiskClass.ADMISSION,
                    severity=Severity.CRITICAL,
                    evidence_ids=(item.evidence_id,),
                )
            )
        if restrictions:
            item = self._evidence(
                context=context,
                subject=subject,
                evidence_id="ev-judicial-restriction-1",
                domain="judicial",
                field="restrictions",
                claim="限制高消费记录",
                value=restrictions[0],
                path="judicial.json",
                fragment="restrictions_on_high_consumption/0",
                status=status,
            )
            evidence.append(item)
            findings.append(
                Finding(
                    finding_id="finding-consumption-restriction",
                    subject_id=subject.subject_id,
                    domain="judicial",
                    claim="企业存在限制高消费记录",
                    value=True,
                    risk_class=RiskClass.ADMISSION,
                    severity=Severity.CRITICAL,
                    evidence_ids=(item.evidence_id,),
                )
            )
        unresolved = tuple(item for item in executions if item.get("status") == "unresolved")
        if unresolved:
            item = self._evidence(
                context=context,
                subject=subject,
                evidence_id="ev-judicial-execution-1",
                domain="judicial",
                field="executions",
                claim="未结被执行记录",
                value=unresolved[0],
                path="judicial.json",
                fragment="executions/0",
                status=status,
            )
            evidence.append(item)
            findings.append(
                Finding(
                    finding_id="finding-execution",
                    subject_id=subject.subject_id,
                    domain="judicial",
                    claim="企业存在未结被执行记录",
                    value={"amount_cny": _thaw(unresolved[0].get("amount_cny"))},
                    risk_class=RiskClass.ATTENTION,
                    severity=Severity.HIGH,
                    evidence_ids=(item.evidence_id,),
                )
            )
        actual_status = status
        if not evidence and status is SourceStatus.VERIFIED_RECORDS:
            actual_status = SourceStatus.VERIFIED_EMPTY
        return DomainInvestigation(
            task_id="single-judicial",
            domain="judicial",
            findings=tuple(findings),
            evidence=tuple(evidence),
            coverage_items=(
                CoverageItem(
                    domain="judicial",
                    capability="judicial_snapshot",
                    status=actual_status,
                    record_count=len(evidence),
                ),
            ),
            section_data={"judicial-risk": cast(dict[str, JsonValue], _thaw(data))},
        )

    def _operations(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        data: Mapping[str, object],
        status: SourceStatus,
    ) -> DomainInvestigation:
        evidence: list[Evidence] = []
        findings: list[Finding] = []
        abnormal = tuple(
            item
            for item in _as_records(data.get("abnormal_operations"))
            if item.get("removed_on") is None
        )
        penalties = _as_records(data.get("administrative_penalties"))
        financials = _as_records(data.get("financial_indicators"))
        if abnormal:
            item = self._evidence(
                context=context,
                subject=subject,
                evidence_id="ev-operations-abnormal-1",
                domain="operations",
                field="abnormal",
                claim="未移出经营异常记录",
                value=abnormal[0],
                path="operations.json",
                fragment="abnormal_operations/0",
                status=status,
            )
            evidence.append(item)
            findings.append(
                Finding(
                    finding_id="finding-operation-abnormal",
                    subject_id=subject.subject_id,
                    domain="operations",
                    claim="企业被列入经营异常名录且尚未移出",
                    value=_thaw(abnormal[0].get("reason")),
                    risk_class=RiskClass.ATTENTION,
                    severity=Severity.HIGH,
                    evidence_ids=(item.evidence_id,),
                )
            )
        if penalties:
            item = self._evidence(
                context=context,
                subject=subject,
                evidence_id="ev-operations-penalty-1",
                domain="operations",
                field="penalties",
                claim="行政处罚记录",
                value=penalties[0],
                path="operations.json",
                fragment="administrative_penalties/0",
                status=status,
            )
            evidence.append(item)
            findings.append(
                Finding(
                    finding_id="finding-administrative-penalty",
                    subject_id=subject.subject_id,
                    domain="operations",
                    claim="企业存在行政处罚记录",
                    value=_thaw(penalties[0].get("reason")),
                    risk_class=RiskClass.ATTENTION,
                    severity=Severity.MEDIUM,
                    evidence_ids=(item.evidence_id,),
                )
            )
        profits = [item.get("net_profit_cny_10k") for item in financials]
        numeric_profits = [value for value in profits if isinstance(value, int | float)]
        if (
            abnormal
            and len(numeric_profits) >= 3
            and numeric_profits[-1] < 0
            and all(previous > current for previous, current in pairwise(numeric_profits))
        ):
            item = self._evidence(
                context=context,
                subject=subject,
                evidence_id="ev-operations-financial-series",
                domain="operations",
                field="financials",
                claim="近三年净利润序列",
                value=numeric_profits,
                path="operations.json",
                fragment="financial_indicators",
                status=status,
            )
            evidence.append(item)
            findings.append(
                Finding(
                    finding_id="finding-profit-decline",
                    subject_id=subject.subject_id,
                    domain="operations",
                    claim="近三年净利润持续下降并转亏",
                    value=cast(JsonValue, numeric_profits),
                    risk_class=RiskClass.ATTENTION,
                    severity=Severity.MEDIUM,
                    evidence_ids=(item.evidence_id,),
                )
            )
        employee_item = self._evidence(
            context=context,
            subject=subject,
            evidence_id="ev-operations-employees",
            domain="operations",
            field="employee_count",
            claim="年报员工人数",
            value=data.get("employee_count"),
            path="operations.json",
            fragment="employee_count",
            status=status,
        )
        evidence.append(employee_item)
        findings.append(
            Finding(
                finding_id="finding-employee-count",
                subject_id=subject.subject_id,
                domain="operations",
                claim="企业员工人数已采集",
                value=_thaw(data.get("employee_count")),
                risk_class=RiskClass.NON_RISK,
                severity=Severity.INFO,
                evidence_ids=(employee_item.evidence_id,),
            )
        )
        notes_path = "corpus/operating-notes.md"
        if notes_path in context.scenario.available_paths:
            notes = context.scenario.read_text(notes_path)
            match = re.search(r"在岗人员约\s*(\d+)\s*人", notes)
            documented_count = int(match.group(1)) if match else None
            structured_count = data.get("employee_count")
            if documented_count is not None and documented_count != structured_count:
                corpus_item = self._evidence(
                    context=context,
                    subject=subject,
                    evidence_id="ev-corpus-employees",
                    domain="operations",
                    field="employee_count",
                    claim="补充材料员工人数",
                    value=documented_count,
                    path=notes_path,
                    fragment="employee-count",
                    status=status,
                )
                evidence.append(corpus_item)
                findings.append(
                    Finding(
                        finding_id="finding-employee-conflict",
                        subject_id=subject.subject_id,
                        domain="operations",
                        claim="员工人数在结构化年报与补充材料中不一致",
                        value={
                            "structured": _thaw(structured_count),
                            "document": documented_count,
                        },
                        risk_class=RiskClass.ATTENTION,
                        severity=Severity.MEDIUM,
                        evidence_ids=(employee_item.evidence_id, corpus_item.evidence_id),
                    )
                )
        return DomainInvestigation(
            task_id="single-operations",
            domain="operations",
            findings=tuple(findings),
            evidence=tuple(evidence),
            coverage_items=(
                CoverageItem(
                    domain="operations",
                    capability="operations_snapshot",
                    status=SourceStatus.VERIFIED_RECORDS,
                    record_count=len(evidence),
                ),
            ),
            section_data={
                "operational-risk": cast(dict[str, JsonValue], _thaw(data)),
                "operations-analysis": cast(dict[str, JsonValue], _thaw(data)),
            },
        )

    def _peers(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        data: Mapping[str, object],
        status: SourceStatus,
    ) -> DomainInvestigation:
        digest = hashlib.sha256(subject.subject_id.encode()).hexdigest()[:8]
        item = self._evidence(
            context=context,
            subject=subject,
            evidence_id=f"ev-peers-{digest}",
            domain="peers",
            field="metrics",
            claim="同类企业与行业参考指标",
            value=data,
            path="peers.json",
            fragment="metrics",
            status=status,
        )
        return DomainInvestigation(
            task_id="single-peers",
            domain="peers",
            findings=(
                Finding(
                    finding_id="finding-peer-benchmark",
                    subject_id=subject.subject_id,
                    domain="peers",
                    claim="同类企业参考指标已采集",
                    value=True,
                    risk_class=RiskClass.NON_RISK,
                    severity=Severity.INFO,
                    evidence_ids=(item.evidence_id,),
                ),
            ),
            evidence=(item,),
            coverage_items=(
                CoverageItem(
                    domain="peers",
                    capability="peers_snapshot",
                    status=SourceStatus.VERIFIED_RECORDS,
                    record_count=1,
                ),
            ),
            section_data={"peer-analysis": cast(dict[str, JsonValue], _thaw(data))},
        )


__all__ = ["ScenarioToolset"]
