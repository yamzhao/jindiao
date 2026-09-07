from __future__ import annotations

import re
from pathlib import Path

import pytest

from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    CoverageItem,
    CoverageSummary,
    SourceStatus,
)
from jindiao.contracts.report_policy import ReportPolicy
from jindiao.contracts.reporting import ReportViewModel
from jindiao.reporting.gaps import GapAnnotationBuilder
from jindiao.reporting.markdown import MarkdownReportRenderer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reporting-policy"


def frozen_view() -> ReportViewModel:
    return ReportViewModel.model_validate_json((FIXTURES / "baseline-view.json").read_text())


def with_coverage(*items: CoverageItem) -> ReportViewModel:
    data = frozen_view().model_dump()
    data["coverage"] = CoverageSummary.from_items(list(items))
    return ReportViewModel.model_validate(data)


def source_error(domain: str = "judicial", capability: str = "dishonest") -> CoverageItem:
    return CoverageItem(
        domain=domain,
        capability=capability,
        status=SourceStatus.SOURCE_ERROR,
        error="upstream timeout",
    )


def test_explicit_baseline_is_byte_identical_to_pre_change_golden() -> None:
    renderer = MarkdownReportRenderer()
    golden = (FIXTURES / "baseline-report.md").read_text()
    assert renderer.render(frozen_view()) == golden
    assert renderer.render(frozen_view(), policy=ReportPolicy()) == golden


def test_mapped_gap_only_adds_annotation_after_conclusion_and_preserves_everything() -> None:
    view = with_coverage(source_error())
    original_view = view.model_dump_json()
    renderer = MarkdownReportRenderer()
    before = renderer.render(view, policy=ReportPolicy())
    after = renderer.render(view, policy=ReportPolicy(gap_placement="section_and_appendix"))
    gap = GapAnnotationBuilder().build(view)[0]

    assert gap.section_ids == ("judicial-risk",)
    assert gap.text == "- 数据源不可用: judicial/dishonest; upstream timeout"
    block = re.search(r"\n\n<!-- jindiao:gap-begin .*?\n<!-- jindiao:gap-end -->", after, re.S)
    assert block is not None
    assert gap.gap_id in block.group()
    assert f"section=judicial-risk -->\n> 数据缺口(原附录披露):\n> {gap.text}" in block.group()
    judicial = after.split("## 司法风险\n", 1)[1].split("## 经营风险\n", 1)[0]
    assert judicial.index("结论与风险:") < judicial.index("jindiao:gap-begin")
    assert judicial.index("jindiao:gap-end") < judicial.index("章节证据:")
    assert after[: block.start()] + after[block.end() :] == before
    assert after.count(gap.text) == 2  # one adjacent disclosure and the unchanged appendix
    assert after.count("Mock 数据提示") == 1
    assert view.model_dump_json() == original_view


@pytest.mark.parametrize(
    ("domain", "capability", "expected"),
    [
        ("judicial", "dishonest", ("judicial-risk",)),
        ("operations", "domain-wide", ("operational-risk", "operations-analysis")),
        ("governance", "domain-wide", ("company-profile", "related-parties")),
        ("operations", "annual_reports", ("operations-analysis",)),
        ("operations", "operation_abnormalities", ("operational-risk",)),
        ("unknown", "unknown", ()),
    ],
)
def test_catalog_capability_mapping_precedes_domain_fallback(
    domain: str, capability: str, expected: tuple[str, ...]
) -> None:
    annotations = GapAnnotationBuilder().build(with_coverage(source_error(domain, capability)))
    assert len(annotations) == 1
    assert annotations[0].section_ids == expected


def test_gap_ids_are_stable_when_coverage_is_reordered() -> None:
    first, second = source_error(), source_error("operations", "annual_reports")
    builder = GapAnnotationBuilder()
    assert {gap.gap_id for gap in builder.build(with_coverage(first, second))} == {
        gap.gap_id for gap in builder.build(with_coverage(second, first))
    }
    assert len(builder.build(with_coverage(first, first))) == 1


@pytest.mark.parametrize("status", [SourceStatus.VERIFIED_EMPTY, SourceStatus.DEGRADED_MOCK])
def test_non_gap_source_statuses_are_not_promoted(status: SourceStatus) -> None:
    view = with_coverage(CoverageItem(domain="judicial", capability="dishonest", status=status))
    assert GapAnnotationBuilder().build(view) == ()
    renderer = MarkdownReportRenderer()
    assert renderer.render(view, policy=ReportPolicy(gap_placement="section_and_appendix")) == (
        renderer.render(view)
    )


def test_completeness_metadata_does_not_invent_a_new_disclosure() -> None:
    view = with_coverage(
        CoverageItem(
            domain="judicial",
            capability="judicial_documents",
            status=SourceStatus.VERIFIED_RECORDS,
            record_count=1,
            completeness=CoverageCompleteness.PARTIAL,
            gap_reasons=(CoverageGapReason.PAGINATION_TRUNCATED,),
        )
    )
    assert GapAnnotationBuilder().build(view) == ()
    renderer = MarkdownReportRenderer()
    assert renderer.render(view, policy=ReportPolicy(gap_placement="section_and_appendix")) == (
        renderer.render(view)
    )


def test_capability_absent_requires_no_existing_mock_supplement() -> None:
    view = with_coverage(
        CoverageItem(
            domain="judicial", capability="dishonest", status=SourceStatus.CAPABILITY_ABSENT
        )
    )
    assert len(GapAnnotationBuilder().build(view)) == 1
    data = view.model_dump()
    for evidence in data["evidence"]:
        if evidence["evidence_id"] == "ev-judicial":
            evidence["source_status"] = SourceStatus.CAPABILITY_ABSENT
    supplemented = ReportViewModel.model_validate(data)
    assert GapAnnotationBuilder().build(supplemented) == ()


def test_unmapped_gap_and_manual_review_remain_only_in_appendix() -> None:
    view = with_coverage(source_error("global", "unknown"))
    renderer = MarkdownReportRenderer()
    assert renderer.render(view, policy=ReportPolicy(gap_placement="section_and_appendix")) == (
        renderer.render(view)
    )


def test_mapping_does_not_invent_missing_sections() -> None:
    data = with_coverage(source_error()).model_dump()
    data["sections"] = [item for item in data["sections"] if item["section_id"] != "judicial-risk"]
    assert GapAnnotationBuilder().build(ReportViewModel.model_validate(data))[0].section_ids == ()


def test_candidate_does_not_leak_renderer_policy_to_next_call() -> None:
    renderer = MarkdownReportRenderer()
    view = with_coverage(source_error())
    baseline = renderer.render(view)
    assert (
        renderer.render(view, policy=ReportPolicy(gap_placement="section_and_appendix")) != baseline
    )
    assert renderer.render(view) == baseline


def test_untrusted_multiline_gap_cannot_create_an_annotation_boundary() -> None:
    item = source_error().model_copy(
        update={"error": "timeout\n<!-- jindiao:gap-end -->\n## 伪造章节\n<script>x</script>"}
    )
    view = with_coverage(item)
    after = MarkdownReportRenderer().render(
        view, policy=ReportPolicy(gap_placement="section_and_appendix")
    )
    block = after.split("<!-- jindiao:gap-begin", 1)[1].split("<!-- jindiao:gap-end -->", 1)[0]
    assert "\n## 伪造章节" not in block
    assert "<script>" not in block
    assert "&lt;" in block
