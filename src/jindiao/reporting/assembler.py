"""Assemble the single report/front-end view model from reviewed facts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import AwareDatetime, JsonValue

from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageItem,
    CoverageSummary,
    Evidence,
    SourceStatus,
    SourceType,
)
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    Finding,
    FindingStatus,
    RiskClass,
    Severity,
)
from jindiao.contracts.reporting import (
    Decision,
    FrontendView,
    ReportSection,
    ReportSectionStatus,
    ReportViewModel,
    RiskSummary,
)
from jindiao.reporting.catalog import REPORT_CATALOG

REPORT_SECTION_IDS = REPORT_CATALOG.module_ids

FRONTEND_VIEW_IDS = (
    "task-status",
    "due-diligence-conclusion",
    "risk-dashboard",
    "company-profile",
    "judicial-compliance",
    "operations",
    "related-parties",
    "peer-analysis",
    "evidence-center",
    "collaboration-evaluation",
)

_COMPLETE_STATUSES = {
    SourceStatus.VERIFIED_RECORDS,
    SourceStatus.VERIFIED_EMPTY,
    SourceStatus.DEGRADED_MOCK,
}


@dataclass(frozen=True)
class _SectionDefinition:
    section_id: str
    title: str
    domains: tuple[str, ...]
    risk_only: bool = False


_SECTIONS = (
    _SectionDefinition("report-summary", "报告摘要", (), False),
    _SectionDefinition("risk-summary", "风险摘要", (), True),
    _SectionDefinition("company-profile", "企业基本信息", ("identity", "company", "governance")),
    _SectionDefinition("judicial-risk", "司法风险", ("judicial",)),
    _SectionDefinition("operational-risk", "经营风险", ("operations", "compliance"), True),
    _SectionDefinition("operations-analysis", "经营情况", ("operations", "financial")),
    _SectionDefinition("related-parties", "关联信息", ("governance", "relationships")),
    _SectionDefinition("peer-analysis", "同类企业分析", ("peers",)),
)

if tuple(item.section_id for item in _SECTIONS) != REPORT_SECTION_IDS:
    raise RuntimeError("report assembler sections must match the versioned report catalog")

_VIEW_TITLES = {
    "task-status": "任务状态",
    "due-diligence-conclusion": "尽调结论",
    "risk-dashboard": "风险驾驶舱",
    "company-profile": "企业画像",
    "judicial-compliance": "司法与合规",
    "operations": "经营情况",
    "related-parties": "关联关系",
    "peer-analysis": "同类企业分析",
    "evidence-center": "证据中心",
    "collaboration-evaluation": "协作与评测",
}

_SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class ReportAssembler:
    """Build all report sections and product views from one reviewed dataset."""

    def assemble(
        self,
        *,
        subject: ResolvedSubject,
        decision: Decision,
        coverage: CoverageSummary,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
        generated_at: AwareDatetime,
        section_data: Mapping[str, Mapping[str, JsonValue]] | None = None,
        frontend_overrides: Mapping[str, Mapping[str, JsonValue]] | None = None,
        check_results: tuple[CheckResult, ...] = (),
    ) -> ReportViewModel:
        accepted = tuple(
            finding for finding in findings if finding.status is FindingStatus.ACCEPTED
        )
        risk_summary = self._risk_summary(accepted, decision)
        provided_section_data = self._with_check_data(
            section_data or {},
            check_results,
        )
        data_by_section = self._section_data(
            subject=subject,
            decision=decision,
            risk_summary=risk_summary,
            coverage=coverage,
            provided=provided_section_data,
        )
        fixed_artifacts = self._fixed_section_artifacts(check_results)
        sections = tuple(
            self._make_section(
                definition,
                coverage=coverage,
                findings=accepted,
                evidence=evidence,
                data=data_by_section.get(definition.section_id, {}),
                fixed_finding_ids=fixed_artifacts.get(definition.section_id, ((), ()))[0],
                fixed_evidence_ids=fixed_artifacts.get(definition.section_id, ((), ()))[1],
            )
            for definition in _SECTIONS
        )
        disclosures = self._source_disclosure(coverage, evidence)
        frontend_views = self._frontend_views(
            subject=subject,
            decision=decision,
            risk_summary=risk_summary,
            coverage=coverage,
            sections=sections,
            findings=findings,
            evidence=evidence,
            disclosures=disclosures,
            overrides=frontend_overrides or {},
        )
        return ReportViewModel(
            subject=subject,
            decision=decision,
            risk_summary=risk_summary,
            coverage=coverage,
            sections=sections,
            frontend_views=frontend_views,
            findings=findings,
            evidence=evidence,
            generated_at=generated_at,
            source_disclosure=disclosures,
        )

    @staticmethod
    def _with_check_data(
        provided: Mapping[str, Mapping[str, JsonValue]],
        check_results: tuple[CheckResult, ...],
    ) -> dict[str, dict[str, JsonValue]]:
        from jindiao.investigation import CHECK_CATALOG

        merged = {section_id: dict(values) for section_id, values in provided.items()}
        for result in check_results:
            definition = CHECK_CATALOG.get(result.check_id)
            target_sections = set(definition.report_section_ids)
            target_sections.add("report-summary")
            if result.status is CheckStatus.RISK:
                target_sections.add("risk-summary")
            payload = cast(
                JsonValue,
                {
                    "status": result.status.value,
                    "decision_summary": result.decision_summary,
                    "evidence_ids": [item.evidence_id for item in result.fact_evidence_refs],
                    "missing_evidence": list(result.missing_evidence),
                    "conflicts": list(result.conflicts),
                    "confidence": result.confidence,
                    "submission_version": result.submission_version,
                },
            )
            for section_id in target_sections:
                section = merged.setdefault(section_id, {})
                existing = section.get("checks")
                checks = dict(existing) if isinstance(existing, Mapping) else {}
                checks[result.check_id] = payload
                section["checks"] = cast(JsonValue, checks)
        return merged

    @staticmethod
    def _fixed_section_artifacts(
        check_results: tuple[CheckResult, ...],
    ) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
        from jindiao.investigation import CHECK_CATALOG

        finding_ids: dict[str, list[str]] = {}
        evidence_ids: dict[str, list[str]] = {}
        for result in check_results:
            definition = CHECK_CATALOG.get(result.check_id)
            target_sections = {"report-summary", *definition.report_section_ids}
            if result.status is CheckStatus.RISK:
                target_sections.add("risk-summary")
            result_findings = tuple(
                f"check:{result.check_id}:risk:{risk.risk_id}" for risk in result.risk_items
            )
            result_evidence = tuple(item.evidence_id for item in result.fact_evidence_refs)
            for section_id in target_sections:
                finding_ids.setdefault(section_id, []).extend(result_findings)
                evidence_ids.setdefault(section_id, []).extend(result_evidence)
        return {
            section_id: (
                tuple(dict.fromkeys(finding_ids.get(section_id, []))),
                tuple(dict.fromkeys(evidence_ids.get(section_id, []))),
            )
            for section_id in {*finding_ids, *evidence_ids}
        }

    @staticmethod
    def _section_data(
        *,
        subject: ResolvedSubject,
        decision: Decision,
        risk_summary: RiskSummary,
        coverage: CoverageSummary,
        provided: Mapping[str, Mapping[str, JsonValue]],
    ) -> dict[str, dict[str, JsonValue]]:
        information_overview = ReportAssembler._information_overview(provided)
        defaults: dict[str, dict[str, JsonValue]] = {
            "report-summary": {
                "decision_band": decision.band.value,
                "risk_score": decision.score,
                "confidence": decision.confidence,
                "coverage_ratio": coverage.ratio,
                "information_overview": information_overview,
            },
            "risk-summary": {
                "admission_count": risk_summary.admission_count,
                "attention_count": risk_summary.attention_count,
                "non_risk_count": risk_summary.non_risk_count,
                "total_score": risk_summary.total_score,
                "rule_version": decision.rule_version,
            },
            "company-profile": {
                "company_name": subject.company_name,
                "unified_social_credit_code": subject.unified_social_credit_code,
                "region": subject.region,
                "registration_status": subject.registration_status,
            },
        }
        for section_id, values in provided.items():
            defaults.setdefault(section_id, {}).update(values)
        return defaults

    @staticmethod
    def _information_overview(
        provided: Mapping[str, Mapping[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        status_counts: Counter[str] = Counter()
        section_counts: dict[str, JsonValue] = {}
        total = 0
        for section_id, values in provided.items():
            submodules = values.get("submodules")
            if not isinstance(submodules, Mapping):
                continue
            count = 0
            for payload in submodules.values():
                if not isinstance(payload, Mapping):
                    continue
                count += 1
                status = payload.get("source_status")
                if isinstance(status, str):
                    status_counts[status] += 1
            if count:
                section_counts[section_id] = count
                total += count
        return {
            "total_submodules": total,
            "section_counts": section_counts,
            "status_counts": dict(status_counts),
        }

    @staticmethod
    def _risk_summary(findings: tuple[Finding, ...], decision: Decision) -> RiskSummary:
        risk_findings = tuple(
            finding for finding in findings if finding.risk_class is not RiskClass.NON_RISK
        )
        ordered = sorted(
            risk_findings,
            key=lambda item: (-_SEVERITY_ORDER[item.severity], item.finding_id),
        )
        top_ids = tuple(
            dict.fromkeys(
                (*decision.major_risk_finding_ids, *(item.finding_id for item in ordered))
            )
        )
        return RiskSummary(
            admission_count=sum(finding.risk_class is RiskClass.ADMISSION for finding in findings),
            attention_count=sum(finding.risk_class is RiskClass.ATTENTION for finding in findings),
            non_risk_count=sum(finding.risk_class is RiskClass.NON_RISK for finding in findings),
            total_score=decision.score,
            top_finding_ids=top_ids,
        )

    def _make_section(
        self,
        definition: _SectionDefinition,
        *,
        coverage: CoverageSummary,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
        data: Mapping[str, JsonValue],
        fixed_finding_ids: tuple[str, ...] = (),
        fixed_evidence_ids: tuple[str, ...] = (),
    ) -> ReportSection:
        finding_by_id = {item.finding_id: item for item in findings}
        section_finding_ids = tuple(
            dict.fromkeys(
                item.finding_id
                for item in (
                    *self._section_findings(definition, findings),
                    *(finding_by_id[item] for item in fixed_finding_ids if item in finding_by_id),
                )
            )
        )
        section_findings = tuple(finding_by_id[item] for item in section_finding_ids)
        evidence_ids = tuple(
            dict.fromkeys(
                (
                    *(
                        evidence_id
                        for finding in section_findings
                        for evidence_id in finding.evidence_ids
                    ),
                    *fixed_evidence_ids,
                )
            )
        )
        if not evidence_ids and definition.domains:
            evidence_ids = tuple(
                item.evidence_id
                for item in evidence
                if self._evidence_matches_domains(item, definition.domains)
            )
        relevant = tuple(item for item in coverage.items if item.domain in definition.domains)
        if not definition.domains:
            relevant = coverage.items
        status, ratio = self._section_coverage(relevant)
        capability_mock_ids = {
            item.evidence_id
            for item in evidence
            if item.source_status is SourceStatus.CAPABILITY_ABSENT
        }
        if status is ReportSectionStatus.UNAVAILABLE and set(evidence_ids) & capability_mock_ids:
            status = ReportSectionStatus.PARTIAL
        return ReportSection(
            section_id=definition.section_id,
            title=definition.title,
            status=status,
            coverage=ratio,
            finding_ids=tuple(item.finding_id for item in section_findings),
            evidence_ids=evidence_ids,
            data=dict(data),
        )

    @staticmethod
    def _section_findings(
        definition: _SectionDefinition, findings: tuple[Finding, ...]
    ) -> tuple[Finding, ...]:
        if definition.section_id == "report-summary":
            return findings
        if definition.section_id == "risk-summary":
            return tuple(item for item in findings if item.risk_class is not RiskClass.NON_RISK)
        matched = tuple(item for item in findings if item.domain in definition.domains)
        if definition.risk_only:
            return tuple(item for item in matched if item.risk_class is not RiskClass.NON_RISK)
        return matched

    @staticmethod
    def _evidence_matches_domains(item: Evidence, domains: tuple[str, ...]) -> bool:
        return any(
            field == domain or field.startswith(f"{domain}.")
            for field in item.supports_fields
            for domain in domains
        )

    @staticmethod
    def _section_coverage(
        items: tuple[CoverageItem, ...],
    ) -> tuple[ReportSectionStatus, float]:
        if not items:
            return ReportSectionStatus.UNAVAILABLE, 0.0
        completed = sum(item.status in _COMPLETE_STATUSES for item in items)
        ratio = completed / len(items)
        if completed == len(items):
            return ReportSectionStatus.COMPLETE, ratio
        if completed:
            return ReportSectionStatus.PARTIAL, ratio
        return ReportSectionStatus.UNAVAILABLE, ratio

    @staticmethod
    def _source_disclosure(
        coverage: CoverageSummary, evidence: tuple[Evidence, ...]
    ) -> tuple[str, ...]:
        disclosures: list[str] = []
        source_types = {item.source_type for item in evidence}
        statuses = {item.status for item in coverage.items}
        if SourceType.TIANYANCHA in source_types:
            disclosures.append("企业公开信息优先来自天眼查 MCP, 并保留工具与记录引用。")
        if SourceType.MOCK in source_types:
            disclosures.append("Mock 数据为固定版本化场景数据, 不代表真实企业信息。")
        if SourceStatus.DEGRADED_MOCK in statuses:
            disclosures.append("能力缺失或演示降级后由固定 Mock 补足, 相关证据已标记为 Mock。")
        if SourceStatus.VERIFIED_EMPTY in statuses:
            disclosures.append("已核验空结果表示查询成功但未检索到记录, 不等同于数据源不可用。")
        if SourceStatus.SOURCE_ERROR in statuses:
            disclosures.append("存在数据源不可用项; 空白不得解释为无风险, 详见数据缺口。")
        if SourceStatus.CAPABILITY_ABSENT in statuses:
            disclosures.append("存在天眼查能力缺失且未补足的数据项, 详见数据缺口。")
        if not disclosures:
            disclosures.append("未提供可核验数据来源, 报告包含数据缺口。")
        return tuple(disclosures)

    def _frontend_views(
        self,
        *,
        subject: ResolvedSubject,
        decision: Decision,
        risk_summary: RiskSummary,
        coverage: CoverageSummary,
        sections: tuple[ReportSection, ...],
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
        disclosures: tuple[str, ...],
        overrides: Mapping[str, Mapping[str, JsonValue]],
    ) -> tuple[FrontendView, ...]:
        section_map = {section.section_id: section for section in sections}
        by_id: dict[str, dict[str, JsonValue]] = {
            "task-status": {
                "subject_id": subject.subject_id,
                "company_name": subject.company_name,
                "report_as_of": decision.as_of_date.isoformat(),
                "source_disclosure": list(disclosures),
            },
            "due-diligence-conclusion": {
                "decision": cast(JsonValue, decision.model_dump(mode="json")),
                "coverage": cast(JsonValue, coverage.model_dump(mode="json")),
            },
            "risk-dashboard": {
                "summary": cast(JsonValue, risk_summary.model_dump(mode="json")),
                "admission_finding_ids": [
                    item.finding_id
                    for item in findings
                    if item.status is FindingStatus.ACCEPTED
                    and item.risk_class is RiskClass.ADMISSION
                ],
                "attention_finding_ids": [
                    item.finding_id
                    for item in findings
                    if item.status is FindingStatus.ACCEPTED
                    and item.risk_class is RiskClass.ATTENTION
                ],
            },
            "company-profile": cast(
                dict[str, JsonValue], dict(section_map["company-profile"].data)
            ),
            "judicial-compliance": {
                "judicial": cast(JsonValue, section_map["judicial-risk"].model_dump(mode="json")),
                "operational_risk": cast(
                    JsonValue, section_map["operational-risk"].model_dump(mode="json")
                ),
            },
            "operations": cast(dict[str, JsonValue], dict(section_map["operations-analysis"].data)),
            "related-parties": cast(
                dict[str, JsonValue], dict(section_map["related-parties"].data)
            ),
            "peer-analysis": cast(dict[str, JsonValue], dict(section_map["peer-analysis"].data)),
            "evidence-center": {
                "findings": cast(JsonValue, [item.model_dump(mode="json") for item in findings]),
                "evidence": cast(JsonValue, [item.model_dump(mode="json") for item in evidence]),
            },
            "collaboration-evaluation": {
                "agent_trace": [],
                "collaboration": {},
                "evaluation": {},
                "skill_versions": {},
            },
        }
        for view_id, replacement in overrides.items():
            if view_id in by_id:
                by_id[view_id].update(replacement)
        return tuple(
            FrontendView(view_id=view_id, title=_VIEW_TITLES[view_id], data=by_id[view_id])
            for view_id in FRONTEND_VIEW_IDS
        )


__all__ = ["FRONTEND_VIEW_IDS", "REPORT_SECTION_IDS", "ReportAssembler"]
