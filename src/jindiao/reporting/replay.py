"""Safe frozen inputs and output-based, deterministic reporting experiments."""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import time
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.report_policy import ReportingPolicyBinding, ReportPolicy
from jindiao.contracts.reporting import ReportViewModel
from jindiao.paths import project_root
from jindiao.reporting.catalog import DEFAULT_REPORT_CATALOG_PATH
from jindiao.reporting.gaps import GapAnnotationBuilder
from jindiao.reporting.markdown import MarkdownReportRenderer
from jindiao.reporting.product_markdown import SECTION_TITLES, ProductReportView
from jindiao.security import redact_json, redact_text

SUITE_PATH = project_root() / "config/report-replay-suite-v1.json"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


class ReplayLimitError(ValueError):
    """A bounded replay input or output exceeds the Demo limit."""


class ReplayDeadlineError(ValueError):
    """The cooperative replay budget expired at a case boundary."""


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def implementation_fingerprint() -> str:
    paths = [
        Path(__file__),
        Path(__file__).with_name("markdown.py"),
        Path(__file__).with_name("gaps.py"),
        DEFAULT_REPORT_CATALOG_PATH,
        SUITE_PATH,
    ]
    return digest(
        canonical({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    )


def validate_references(view: ReportViewModel | ProductReportView) -> None:
    if isinstance(view, ProductReportView):
        view.validate_references()
        return
    evidence_ids = {item.evidence_id for item in view.evidence}
    finding_ids = {item.finding_id for item in view.findings}
    if len(evidence_ids) != len(view.evidence) or len(finding_ids) != len(view.findings):
        raise ValueError("duplicate report references")
    if (
        not set(view.decision.major_risk_finding_ids) <= finding_ids
        or not set(view.risk_summary.top_finding_ids) <= finding_ids
    ):
        raise ValueError("invalid summary references")
    for section in view.sections:
        if (
            not set(section.evidence_ids) <= evidence_ids
            or not set(section.finding_ids) <= finding_ids
        ):
            raise ValueError("invalid section references")
    for finding in view.findings:
        if not set(finding.evidence_ids) <= evidence_ids:
            raise ValueError("invalid finding references")
    for hit in view.decision.rule_hits:
        if hit.finding_id not in finding_ids or not set(hit.evidence_ids) <= evidence_ids:
            raise ValueError("invalid rule references")


class ReplaySnapshot(ContractModel):
    schema_version: Literal[1, 2] = 1
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_:.\-]+$")
    binding: ReportingPolicyBinding
    view: ProductReportView | ReportViewModel
    view_sha256: str
    report: str
    report_sha256: str
    fingerprint: str

    @classmethod
    def freeze(
        cls,
        *,
        run_id: str,
        view: ProductReportView | ReportViewModel,
        binding: ReportingPolicyBinding,
        report: str,
    ) -> Self:
        safe: ProductReportView | ReportViewModel = TypeAdapter(
            ProductReportView | ReportViewModel
        ).validate_python(redact_json(view.model_dump(mode="json")))
        safe_report = redact_text(report)
        if MarkdownReportRenderer().render(safe, policy=binding.policy) != safe_report:
            raise ValueError("safe report reproduction failed")
        result = cls(
            schema_version=2 if isinstance(safe, ProductReportView) else 1,
            run_id=run_id,
            binding=binding,
            view=safe,
            view_sha256=digest(canonical(safe.model_dump(mode="json"))),
            report=safe_report,
            report_sha256=digest(safe_report),
            fingerprint=implementation_fingerprint(),
        )
        if len(result.model_dump_json().encode()) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot too large")
        return result

    @model_validator(mode="after")
    def verify(self) -> Self:
        if (self.schema_version == 2) != isinstance(self.view, ProductReportView):
            raise ValueError("replay version does not match its view")
        validate_references(self.view)
        if self.view_sha256 != digest(canonical(self.view.model_dump(mode="json"))):
            raise ValueError("view hash mismatch")
        if self.report_sha256 != digest(self.report):
            raise ValueError("report hash mismatch")
        if MarkdownReportRenderer().render(self.view, policy=self.binding.policy) != self.report:
            raise ValueError("snapshot report reproduction failed")
        return self


class ExpectedGap(ContractModel):
    gap_id: str
    section_id: str
    text: str


class ReplayCase(ContractModel):
    case_id: str
    view: ProductReportView | ReportViewModel
    expected: tuple[ExpectedGap, ...]


class CaseEvaluation(ContractModel):
    case_id: str
    before: str
    after: str
    diff: str
    before_sha256: str
    after_sha256: str
    denominator: int
    applicable: bool
    before_numerator: int
    after_numerator: int
    before_rate: float | None
    after_rate: float | None
    improved: bool
    protected: bool
    reasons: tuple[str, ...]


class ReplayEvaluation(ContractModel):
    passed: bool
    reasons: tuple[str, ...]
    fingerprint: str
    cases: tuple[CaseEvaluation, ...]
    elapsed_ms: int


def load_suite(path: Path = SUITE_PATH) -> tuple[ReplayCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("cases_sha256") != digest(canonical(raw["cases"])):
        raise ValueError("suite hash mismatch")
    cases = tuple(ReplayCase.model_validate(item) for item in raw["cases"])
    if len(cases) != 8 or len({case.case_id for case in cases}) != 8:
        raise ValueError("suite must have eight independent cases")
    for case in cases:
        validate_references(case.view)
    return cases


_BLOCK = re.compile(
    r"\n\n<!-- jindiao:gap-begin id=(gap-[a-f0-9]{64}) section=([a-z][a-z0-9_-]*) -->"
    r"\n> 数据缺口\(原附录披露\):\n> ([^\n]*)\n<!-- jindiao:gap-end -->"
)


def _inspect(case: ReplayCase, markdown: str) -> tuple[str, set[tuple[str, str]], bool]:
    expected = {(item.gap_id, item.section_id): item.text for item in case.expected}
    titles = (
        {title: identity for identity, title in SECTION_TITLES.items()}
        if isinstance(case.view, ProductReportView)
        else {section.title: section.section_id for section in case.view.sections}
    )
    found: set[tuple[str, str]] = set()
    valid = True
    for match in _BLOCK.finditer(markdown):
        pair = (match[1], match[2])
        headings = re.findall(r"^## (.+)$", markdown[: match.start()], re.M)
        actual_section = titles.get(headings[-1]) if headings else None
        text = expected.get(pair)
        escaped = html.escape(text).replace("\r", "\\r").replace("\n", "\\n") if text else None
        if pair in found or actual_section != pair[1] or match[3] != escaped:
            valid = False
        else:
            found.add(pair)
    stripped = _BLOCK.sub("", markdown)
    # Unparsed/malformed markers and markers already present in source text fail closed.
    if "jindiao:gap-" in stripped:
        valid = False
    return stripped, found, valid


def evaluate_case(case: ReplayCase, before: str, after: str) -> CaseEvaluation:
    stripped_before, prior, valid_before = _inspect(case, before)
    stripped_after, later, valid_after = _inspect(case, after)
    reasons = []
    if stripped_before != stripped_after:
        reasons.append("mandatory_content_changed")
    if not valid_before or not valid_after:
        reasons.append("invalid_gap_blocks")
    if before.count("Mock 数据提示") != after.count("Mock 数据提示"):
        reasons.append("mock_disclosure_changed")
    denominator = len(case.expected)
    return CaseEvaluation(
        case_id=case.case_id,
        before=before,
        after=after,
        diff="".join(
            difflib.unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                fromfile="before.md",
                tofile="after.md",
            )
        ),
        before_sha256=digest(before),
        after_sha256=digest(after),
        denominator=denominator,
        applicable=denominator > 0,
        before_numerator=len(prior),
        after_numerator=len(later),
        before_rate=len(prior) / denominator if denominator else None,
        after_rate=len(later) / denominator if denominator else None,
        improved=bool(denominator and len(later) > len(prior)),
        protected=not reasons,
        reasons=tuple(reasons),
    )


def replay(snapshot: ReplaySnapshot, *, target_section_ids: tuple[str, ...]) -> ReplayEvaluation:
    start = time.monotonic()
    if snapshot.fingerprint != implementation_fingerprint():
        raise ValueError("implementation fingerprint changed")
    renderer = MarkdownReportRenderer()
    baseline = renderer.render(snapshot.view, policy=snapshot.binding.policy)
    if baseline != snapshot.report:
        raise ValueError("baseline reproduction failed")
    expected = tuple(
        ExpectedGap(gap_id=gap.gap_id, section_id=section, text=gap.text)
        for gap in GapAnnotationBuilder().build(snapshot.view)
        for section in gap.section_ids
    )
    cases = (ReplayCase(case_id="source", view=snapshot.view, expected=expected), *load_suite())
    if sum(len(case.model_dump_json().encode()) for case in cases) > MAX_TOTAL_BYTES:
        raise ReplayLimitError("replay input too large")
    evaluated = []
    reasons = []
    output_size = 0
    for case in cases:
        view_hash = digest(canonical(case.view.model_dump(mode="json")))
        before = renderer.render(case.view, policy=snapshot.binding.policy)
        after = renderer.render(
            case.view, policy=ReportPolicy(gap_placement="section_and_appendix")
        )
        result = evaluate_case(case, before, after)
        if digest(canonical(case.view.model_dump(mode="json"))) != view_hash:
            reasons.append("structured_facts_changed")
        output_size += len(result.model_dump_json().encode())
        if output_size > MAX_TOTAL_BYTES:
            raise ReplayLimitError("replay output too large")
        if time.monotonic() - start > 10:
            raise ReplayDeadlineError("replay deadline exceeded")
        evaluated.append(result)
    source = evaluated[0]
    _, before_pairs, _ = _inspect(cases[0], source.before)
    _, after_pairs, _ = _inspect(cases[0], source.after)
    if not target_section_ids or any(
        sum(section == target for _, section in after_pairs)
        <= sum(section == target for _, section in before_pairs)
        for target in target_section_ids
    ):
        reasons.append("source_target_not_improved")
    if not any(case.improved for case in evaluated[1:]):
        reasons.append("no_independent_improvement")
    if any(
        not case.protected or case.after_numerator < case.before_numerator for case in evaluated
    ):
        reasons.append("regression_or_protection_failed")
    return ReplayEvaluation(
        passed=not reasons,
        reasons=tuple(reasons),
        fingerprint=snapshot.fingerprint,
        cases=tuple(evaluated),
        elapsed_ms=int((time.monotonic() - start) * 1000),
    )
