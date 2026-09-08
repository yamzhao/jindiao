"""Fixed, policy-independent mapping of existing appendix gaps to report sections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType

from jindiao.contracts.evidence import CoverageItem, Evidence, SourceStatus, SourceType
from jindiao.contracts.reporting import ReportViewModel
from jindiao.reporting.catalog import REPORT_CATALOG
from jindiao.reporting.product_markdown import ProductReportView, gap_annotations

GAP_MAPPING_VERSION = "gap-mapping-v1"

# Mirrors the report assembler's domain coverage, excluding summary sections.
# Exact catalog capabilities take precedence over this coarser domain mapping.
_DOMAIN_SECTIONS = MappingProxyType(
    {
        "identity": ("company-profile",),
        "company": ("company-profile",),
        "governance": ("company-profile", "related-parties"),
        "judicial": ("judicial-risk",),
        "operations": ("operational-risk", "operations-analysis"),
        "compliance": ("operational-risk",),
        "financial": ("operations-analysis",),
        "relationships": ("related-parties",),
        "peers": ("peer-analysis",),
    }
)


def coverage_gap_text(item: CoverageItem, evidence: tuple[Evidence, ...]) -> str | None:
    """Preserve the existing appendix semantics, including Mock supplementation."""

    if item.status is SourceStatus.SOURCE_ERROR:
        return f"- 数据源不可用: {item.domain}/{item.capability}; {item.error or '未提供原因'}"
    if item.status is SourceStatus.CAPABILITY_ABSENT:
        has_mock_fallback = any(
            entry.source_type is SourceType.MOCK
            and entry.source_status is SourceStatus.CAPABILITY_ABSENT
            and any(field.startswith(f"{item.domain}.") for field in entry.supports_fields)
            for entry in evidence
        )
        if not has_mock_fallback:
            return f"- 数据能力缺失: {item.domain}/{item.capability}, 无可用补充数据。"
    return None


@dataclass(frozen=True, slots=True)
class GapAnnotation:
    gap_id: str
    section_ids: tuple[str, ...]
    text: str


class GapAnnotationBuilder:
    """Map only already disclosed coverage gaps; never infer findings or risks."""

    def build(self, view: ReportViewModel | ProductReportView) -> tuple[GapAnnotation, ...]:
        if isinstance(view, ProductReportView):
            return tuple(
                GapAnnotation(identity, (section,), text)
                for identity, section, text in gap_annotations(view)
            )
        present = {section.section_id for section in view.sections}
        annotations: dict[str, GapAnnotation] = {}
        for item in view.coverage.items:
            text = coverage_gap_text(item, view.evidence)
            if text is None:
                continue
            sections: tuple[str, ...]
            if item.capability in REPORT_CATALOG.submodule_ids:
                sections = (REPORT_CATALOG.module_for_submodule(item.capability).module_id,)
            else:
                sections = _DOMAIN_SECTIONS.get(item.domain, ())
            identity = json.dumps(
                [GAP_MAPPING_VERSION, item.domain, item.capability, item.status.value, text],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            gap_id = "gap-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            annotations[gap_id] = GapAnnotation(
                gap_id=gap_id,
                section_ids=tuple(section for section in sections if section in present),
                text=text,
            )
        return tuple(annotations.values())


__all__ = ["GAP_MAPPING_VERSION", "GapAnnotation", "GapAnnotationBuilder", "coverage_gap_text"]
