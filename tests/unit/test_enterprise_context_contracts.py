from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.reporting.catalog import REPORT_CATALOG

NOW = datetime(2026, 9, 5, tzinfo=UTC)
REPORT_AS_OF = date(2026, 8, 31)


def _contracts() -> Any:
    try:
        from jindiao.contracts import acquisition
    except ImportError:
        pytest.fail("enterprise context contracts are not implemented", pytrace=False)
    return acquisition


def _subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:123",
        company_name="公开样本有限公司",
        unified_social_credit_code="91110000LIVE000001",
        region="北京市",
        registration_status="存续",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


def _empty_submodules(module: Any) -> tuple[Any, ...]:
    return tuple(
        module.SubmoduleContext(
            submodule_id=submodule_id,
            availability=module.SubmoduleAvailability.NOT_REQUESTED,
            completeness="unknown",
        )
        for submodule_id in REPORT_CATALOG.submodule_ids
    )


def test_supplement_task_distinguishes_baseline_enrichment_from_evidence_gap() -> None:
    module = _contracts()

    baseline = module.SupplementTask(
        task_id="supplement:annual-reports:social-security",
        reason=module.SupplementTaskReason.BASELINE_ENRICHMENT,
        target_submodule_id="annual_reports",
        subject_id="tyc:123",
        report_as_of=REPORT_AS_OF,
        allowed_tools=("tianyancha_annual_report_social_security",),
        allowed_sources=("public_web",),
        requested_fields=("social_security",),
        max_tool_calls=1,
    )

    assert baseline.reason.value == "baseline_enrichment"
    assert baseline.gap_type is None

    with pytest.raises(ValueError, match="evidence_gap tasks require gap_type"):
        module.SupplementTask(
            task_id="supplement:gap:financial-summary",
            reason=module.SupplementTaskReason.EVIDENCE_GAP,
            target_submodule_id="financial_summary",
            subject_id="tyc:123",
            report_as_of=REPORT_AS_OF,
            allowed_tools=("bounded_web_search",),
            allowed_sources=("public_web",),
            requested_fields=("profit",),
            max_tool_calls=2,
        )


def test_enterprise_context_snapshot_is_immutable_and_contains_canonical_48() -> None:
    module = _contracts()
    submodules = _empty_submodules(module)

    snapshot = module.EnterpriseContextSnapshot(
        schema_version=1,
        snapshot_id="snapshot:tyc-123:2026-08-31",
        snapshot_sha256="a" * 64,
        subject=_subject(),
        report_as_of=REPORT_AS_OF,
        created_at=NOW,
        report_catalog_version=REPORT_CATALOG.catalog_version,
        source_manifest_version="tianyancha-capabilities-v1",
        submodules=submodules,
        evidence=(),
        supplement_tasks=(),
        unresolved_gaps=(),
        unresolved_conflicts=(),
    )

    assert len(snapshot.submodules) == 48
    assert snapshot.submodule("annual_reports").availability.value == "not_requested"
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.submodules[0].availability = module.SubmoduleAvailability.AVAILABLE


def test_snapshot_rejects_unknown_evidence_references() -> None:
    module = _contracts()
    submodules = list(_empty_submodules(module))
    submodules[0] = module.SubmoduleContext(
        submodule_id=submodules[0].submodule_id,
        availability=module.SubmoduleAvailability.AVAILABLE,
        completeness="partial",
        evidence_ids=("ev-missing",),
        facts={"status": "存续"},
    )

    with pytest.raises(ValueError, match="unknown Evidence"):
        module.EnterpriseContextSnapshot(
            schema_version=1,
            snapshot_id="snapshot:invalid",
            snapshot_sha256="b" * 64,
            subject=_subject(),
            report_as_of=REPORT_AS_OF,
            created_at=NOW,
            report_catalog_version=REPORT_CATALOG.catalog_version,
            source_manifest_version="tianyancha-capabilities-v1",
            submodules=tuple(submodules),
            evidence=(),
            supplement_tasks=(),
            unresolved_gaps=(),
            unresolved_conflicts=(),
        )


def test_snapshot_requires_exactly_forty_eight_unique_submodules() -> None:
    module = _contracts()

    with pytest.raises(ValueError, match="exactly 48"):
        module.EnterpriseContextSnapshot(
            schema_version=1,
            snapshot_id="snapshot:invalid",
            snapshot_sha256="c" * 64,
            subject=_subject(),
            report_as_of=REPORT_AS_OF,
            created_at=NOW,
            report_catalog_version=REPORT_CATALOG.catalog_version,
            source_manifest_version="tianyancha-capabilities-v1",
            submodules=_empty_submodules(module)[:-1],
            evidence=(),
            supplement_tasks=(),
            unresolved_gaps=(),
            unresolved_conflicts=(),
        )
