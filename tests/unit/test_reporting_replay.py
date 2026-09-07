from __future__ import annotations

import json
from pathlib import Path

import pytest

from jindiao.contracts.report_policy import ReportingPolicyBinding, ReportPolicy
from jindiao.reporting.markdown import MarkdownReportRenderer
from jindiao.reporting.replay import (
    ReplaySnapshot,
    evaluate_case,
    implementation_fingerprint,
    load_suite,
    replay,
)


def source_snapshot() -> ReplaySnapshot:
    case = load_suite()[1]
    view = case.view
    return ReplaySnapshot.freeze(
        run_id="source-run",
        view=view,
        binding=ReportingPolicyBinding.freeze(ReportPolicy(), version="1.1.0", revision=0),
        report=MarkdownReportRenderer().render(view),
    )


def test_replay_runs_nine_cases_and_measures_actual_improvement() -> None:
    result = replay(source_snapshot(), target_section_ids=("judicial-risk",))
    assert result.passed
    assert len(result.cases) == 9
    assert result.cases[0].before_rate == 0
    assert result.cases[0].after_rate == 1
    assert result.cases[0].diff
    assert result.cases[1].before_rate is None
    assert result.cases[1].denominator == 0
    assert result.cases[1].model_dump()["applicable"] is False
    assert result.fingerprint == implementation_fingerprint()
    assert len({case.case_id for case in result.cases}) == 9


@pytest.mark.parametrize(
    "mutation", ["unchanged", "score", "evidence", "mock", "marker", "duplicate", "heading", "text"]
)
def test_independent_checker_rejects_mutated_markdown(mutation: str) -> None:
    case = load_suite()[6]  # risk + real references + Mock warning + gap
    before = MarkdownReportRenderer().render(case.view)
    after = MarkdownReportRenderer().render(
        case.view, policy=ReportPolicy(gap_placement="section_and_appendix")
    )
    if mutation == "unchanged":
        after = before
    elif mutation == "score":
        after = after.replace("风险总分: 80", "风险总分: 0")
    elif mutation == "evidence":
        after = after.replace("ev-judicial", "ev-forged")
    elif mutation == "mock":
        after = after.replace("Mock 数据提示", "数据已核验")
    elif mutation == "marker":
        after = after.replace("section=judicial-risk", "section=company-profile")
    elif mutation == "duplicate":
        from jindiao.reporting.replay import _BLOCK

        block = _BLOCK.search(after)
        assert block is not None
        after = after.replace(block[0], block[0] * 2)
    elif mutation == "heading":
        after = after.replace("## 司法风险", "## 伪造章节")
    else:
        after = after.replace("> - 数据源不可用", "> - 已核验无风险")
    result = evaluate_case(case, before, after)
    assert not (result.protected and result.improved)


def test_snapshot_checks_report_hash_and_references() -> None:
    snapshot = source_snapshot()
    payload = snapshot.model_dump(mode="json")
    payload["report"] = "forged"
    with pytest.raises(ValueError):
        ReplaySnapshot.model_validate(payload)
    view = snapshot.view.model_dump(mode="json")
    view["sections"][0]["evidence_ids"] = ["missing"]
    with pytest.raises(ValueError):
        ReplaySnapshot.freeze(
            run_id="bad",
            view=type(snapshot.view).model_validate(view),
            binding=snapshot.binding,
            report=snapshot.report,
        )


def test_snapshot_redacts_or_rejects_and_never_keeps_secrets() -> None:
    snapshot = source_snapshot()
    view = snapshot.view.model_copy(update={"source_disclosure": ("token=very-secret-token",)})
    with pytest.raises(ValueError, match="reproduc"):
        ReplaySnapshot.freeze(
            run_id="secret",
            view=view,
            binding=snapshot.binding,
            report=MarkdownReportRenderer().render(view),
        )


def test_suite_manifest_detects_modified_fixture(tmp_path: Path) -> None:
    from jindiao.reporting.replay import SUITE_PATH

    data = json.loads(SUITE_PATH.read_text())
    data["cases"][0]["case_id"] = "changed"
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="suite"):
        load_suite(path)


def test_unmapped_and_wrong_target_cannot_pass() -> None:
    assert not replay(source_snapshot(), target_section_ids=("company-profile",)).passed


def test_source_only_improvement_is_not_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    from jindiao.reporting import replay as replay_module

    no_gap_case = load_suite()[0]
    monkeypatch.setattr(replay_module, "load_suite", lambda: (no_gap_case,))
    assert not replay(source_snapshot(), target_section_ids=("judicial-risk",)).passed


@pytest.mark.parametrize("field", ["major_risk_finding_ids", "top_finding_ids"])
def test_snapshot_rejects_unknown_summary_references(field: str) -> None:
    from jindiao.reporting.replay import canonical, digest

    payload = source_snapshot().model_dump(mode="json")
    group = "decision" if field == "major_risk_finding_ids" else "risk_summary"
    payload["view"][group][field] = ["missing-finding"]
    payload["view_sha256"] = digest(canonical(payload["view"]))
    with pytest.raises(ValueError, match="references"):
        ReplaySnapshot.model_validate(payload)


def test_snapshot_rejects_unreproducible_report_even_with_matching_hash() -> None:
    from jindiao.reporting.replay import digest

    payload = source_snapshot().model_dump(mode="json")
    payload["report"] += "\nforged content\n"
    payload["report_sha256"] = digest(payload["report"])
    with pytest.raises(ValueError, match="reproduction"):
        ReplaySnapshot.model_validate(payload)
