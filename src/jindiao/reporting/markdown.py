"""Render the audit-friendly Markdown report from a ReportViewModel only."""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

from jindiao.contracts.evidence import Evidence, SourceStatus, SourceType
from jindiao.contracts.investigation import Finding, RiskClass
from jindiao.contracts.report_policy import ReportPolicy
from jindiao.contracts.reporting import DecisionBand, ReportViewModel
from jindiao.reporting.gaps import GapAnnotation, GapAnnotationBuilder, coverage_gap_text
from jindiao.reporting.product_markdown import ProductMarkdownRenderer, ProductReportView

REPORT_RENDERER_VERSION = "report-renderer-v1.1"

_BAND_LABELS: Final = {
    DecisionBand.PASS: "通过",
    DecisionBand.MANUAL_REVIEW: "人工复核",
    DecisionBand.REJECT: "拒绝",
}
_RISK_LABELS: Final = {
    RiskClass.ADMISSION: "准入类",
    RiskClass.ATTENTION: "关注类",
    RiskClass.NON_RISK: "非风险事实",
}
_SOURCE_LABELS: Final = {
    SourceType.TIANYANCHA: "天眼查 MCP",
    SourceType.PUBLIC_WEB: "公开网页",
    SourceType.MOCK: "Mock",
    SourceType.DERIVED: "派生",
    SourceType.USER_INPUT: "业务输入",
}
_SOURCE_STATUS_LABELS: Final = {
    SourceStatus.VERIFIED_RECORDS.value: "已核验有记录",
    SourceStatus.VERIFIED_EMPTY.value: "已核验无记录",
    SourceStatus.CAPABILITY_ABSENT.value: "能力缺失(已补足)",
    SourceStatus.SOURCE_ERROR.value: "数据源失败",
    SourceStatus.DEGRADED_MOCK.value: "数据源降级(已补足)",
}


def _value_text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record_text(value: JsonValue) -> str:
    """Keep untrusted multiline source text from changing Markdown structure."""

    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return _value_text(value)


class MarkdownReportRenderer:
    """Deterministically render all report values from one immutable view model."""

    def render(
        self,
        view: ReportViewModel | ProductReportView,
        *,
        policy: ReportPolicy | None = None,
    ) -> str:
        if isinstance(view, ProductReportView):
            return ProductMarkdownRenderer().render(view, policy=policy)
        selected_policy = policy or ReportPolicy()
        annotations = (
            GapAnnotationBuilder().build(view)
            if selected_policy.gap_placement == "section_and_appendix"
            else ()
        )
        finding_map = {item.finding_id: item for item in view.findings}
        evidence_map = {item.evidence_id: item for item in view.evidence}
        has_mock = any(item.source_type is SourceType.MOCK for item in view.evidence)
        lines = ["# 企业信用与风控尽调报告", ""]
        if has_mock:
            lines.extend(
                [
                    "> [!WARNING]",
                    (
                        "> **Mock 数据提示**: 本报告包含固定版本化 Mock 演示数据, "
                        "不代表目标企业真实信息。"
                    ),
                    "",
                ]
            )
        lines.extend(
            [
                f"- 企业名称: {view.subject.company_name}",
                f"- 统一社会信用代码: {view.subject.unified_social_credit_code or '未提供'}",
                f"- 报告数据时点: {view.decision.as_of_date.isoformat()}",
                f"- 风险总分: {view.decision.score}",
                f"- 决策分档: {_BAND_LABELS[view.decision.band]}",
                f"- 置信度: {view.decision.confidence:.2%}",
                f"- 规则版本: {view.decision.rule_version}",
                "",
            ]
        )
        for section in view.sections:
            lines.extend(
                [
                    f"## {section.title}",
                    "",
                    f"- 章节状态: {section.status.value}",
                    f"- 调查覆盖率: {section.coverage:.2%}",
                ]
            )
            for key, value in sorted(section.data.items()):
                if key in {"source_summary", "submodules"}:
                    continue
                lines.append(f"- {key}: {_value_text(value)}")
            submodules = section.data.get("submodules")
            if isinstance(submodules, Mapping):
                lines.extend(self._submodule_lines(submodules))
            section_findings = [
                finding_map[finding_id]
                for finding_id in section.finding_ids
                if finding_id in finding_map
            ]
            if section_findings:
                lines.extend(["", "结论与风险:"])
                lines.extend(self._finding_line(item) for item in section_findings)
            else:
                lines.extend(["", "结论与风险: 暂无可引用的已审核 Finding。"])
            for annotation in annotations:
                if section.section_id in annotation.section_ids:
                    lines.extend(self._annotation_lines(annotation, section.section_id))
            if section.evidence_ids:
                lines.extend(["", "章节证据:"])
                lines.extend(
                    self._evidence_line(evidence_map[evidence_id])
                    for evidence_id in section.evidence_ids
                    if evidence_id in evidence_map
                )
            if section.section_id == "risk-summary":
                lines.extend(["", "规则命中与追溯:"])
                if view.decision.rule_hits:
                    lines.extend(
                        (
                            f"- {hit.rule_id}: +{hit.points}; Finding={hit.finding_id}; "
                            f"Evidence={'、'.join(hit.evidence_ids)}; {hit.explanation}"
                        )
                        for hit in view.decision.rule_hits
                    )
                else:
                    lines.append("- 未命中计分规则。")
            lines.append("")

        lines.extend(["## 证据与来源说明", ""])
        lines.extend(
            f"- {item}" for item in view.source_disclosure if not (has_mock and "Mock" in item)
        )
        lines.append("")
        lines.extend(self._evidence_line(item) for item in view.evidence)
        lines.extend(["", "## 数据缺口与人工复核", ""])
        gap_lines = self._gap_lines(view)
        lines.extend(gap_lines or ["- 无待披露数据缺口或人工复核项。"])
        lines.extend(
            [
                "",
                "## 免责声明",
                "",
                "本报告用于企业信用与风控尽调辅助, 不替代人工授信、审计或法律意见。",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _annotation_lines(annotation: GapAnnotation, section_id: str) -> list[str]:
        # The copied source text is data: it must not forge sections or marker boundaries.
        text = html.escape(annotation.text).replace("\r", "\\r").replace("\n", "\\n")
        return [
            "",
            f"<!-- jindiao:gap-begin id={annotation.gap_id} section={section_id} -->",
            "> 数据缺口(原附录披露):",
            f"> {text}",
            "<!-- jindiao:gap-end -->",
        ]

    @staticmethod
    def _finding_line(finding: Finding) -> str:
        evidence = "、".join(finding.evidence_ids)
        return (
            f"- [{_RISK_LABELS[finding.risk_class]}] {finding.claim} "
            f"(Finding: {finding.finding_id}; Evidence: {evidence})"
        )

    @staticmethod
    def _evidence_line(item: Evidence) -> str:
        return (
            f"- [{item.evidence_id}] {item.claim}; 来源={_SOURCE_LABELS[item.source_type]}; "
            f"状态={item.source_status.value}; 查询时间={item.queried_at.isoformat()}; "
            f"引用={item.raw_ref}"
        )

    @staticmethod
    def _submodule_lines(submodules: Mapping[str, object]) -> list[str]:
        lines: list[str] = []
        for submodule_id, raw_payload in submodules.items():
            if not isinstance(raw_payload, Mapping):
                continue
            title = raw_payload.get("title")
            display_title = title if isinstance(title, str) and title else str(submodule_id)
            status = raw_payload.get("source_status")
            status_text = _SOURCE_STATUS_LABELS.get(str(status), str(status))
            source_tool = raw_payload.get("source_tool")
            records = raw_payload.get("records")
            evidence_ids = raw_payload.get("evidence_ids")
            lines.extend(
                [
                    "",
                    f"### {display_title}",
                    "",
                    f"- 数据状态: {status_text}",
                    f"- 来源工具: {source_tool or '未提供'}",
                ]
            )
            if isinstance(records, list) and records:
                lines.extend(
                    f"- 记录 {index}: {_record_text(record)}"
                    for index, record in enumerate(records, start=1)
                )
            else:
                lines.append("- 记录: 无")
            if isinstance(evidence_ids, list) and evidence_ids:
                lines.append(f"- Evidence: {'、'.join(str(item) for item in evidence_ids)}")
        return lines

    @staticmethod
    def _gap_lines(view: ReportViewModel) -> list[str]:
        lines: list[str] = []
        for item in view.coverage.items:
            if item.status is SourceStatus.VERIFIED_EMPTY:
                lines.append(
                    f"- 已核验无记录: {item.domain}/{item.capability}(查询成功, 非数据缺口)"
                )
            else:
                gap_text = coverage_gap_text(item, view.evidence)
                if gap_text is not None:
                    lines.append(gap_text)
        lines.extend(f"- 待人工确认: {item}" for item in view.decision.pending_review_items)
        return lines


__all__ = ["REPORT_RENDERER_VERSION", "MarkdownReportRenderer"]
