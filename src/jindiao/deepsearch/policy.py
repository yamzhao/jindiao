"""Deterministic policy that turns acquisition gaps into explicit supplement tasks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.contracts.acquisition import (
    SubmoduleAvailability,
    SubmoduleContext,
    SupplementTask,
    SupplementTaskReason,
)
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import SourceStatus, SourceType


class SupplementPolicy:
    """Create stable, bounded tasks; never infer source success from missing data."""

    def __init__(
        self,
        *,
        annual_report_social_security_enabled: bool,
        max_gap_tasks: int,
        allowed_gap_submodules: frozenset[str] | None = None,
    ) -> None:
        if max_gap_tasks < 0:
            raise ValueError("max_gap_tasks cannot be negative")
        allowed = allowed_gap_submodules or frozenset(ACQUISITION_CATALOG.default_plan_ids)
        unknown = allowed - set(ACQUISITION_CATALOG.acquisition_ids)
        if unknown:
            raise ValueError(f"supplement policy contains unknown submodules: {sorted(unknown)}")
        self.annual_report_social_security_enabled = annual_report_social_security_enabled
        self.max_gap_tasks = max_gap_tasks
        self.allowed_gap_submodules = allowed

    def plan(
        self,
        *,
        subject: ResolvedSubject,
        report_as_of: date,
        submodules: tuple[SubmoduleContext, ...],
        conflict_evidence_ids_by_submodule: Mapping[str, tuple[str, ...]] | None = None,
        required_fact_gaps: Mapping[str, tuple[str, ...]] | None = None,
    ) -> tuple[SupplementTask, ...]:
        by_id = {item.submodule_id: item for item in submodules}
        if len(by_id) != len(submodules):
            raise ValueError("supplement policy submodule ids must be unique")
        unknown = set(by_id) - set(ACQUISITION_CATALOG.acquisition_ids)
        if unknown:
            raise ValueError(f"supplement policy received unknown submodules: {sorted(unknown)}")

        tasks: list[SupplementTask] = []
        baseline_count = 0
        if self.annual_report_social_security_enabled and "annual_reports" in by_id:
            tasks.append(
                SupplementTask(
                    task_id=self._task_id(
                        subject.subject_id,
                        "annual_reports",
                        SupplementTaskReason.BASELINE_ENRICHMENT.value,
                    ),
                    reason=SupplementTaskReason.BASELINE_ENRICHMENT,
                    target_submodule_id="annual_reports",
                    subject_id=subject.subject_id,
                    report_as_of=report_as_of,
                    allowed_tools=("tianyancha_annual_report_social_security",),
                    allowed_sources=(SourceType.PUBLIC_WEB,),
                    requested_fields=("operations.annual_reports.social_security",),
                    max_tool_calls=1,
                )
            )
            baseline_count = 1

        conflicts = conflict_evidence_ids_by_submodule or {}
        required = required_fact_gaps or {}
        for submodule_id in ACQUISITION_CATALOG.default_plan_ids:
            if len(tasks) - baseline_count >= self.max_gap_tasks:
                break
            context = by_id.get(submodule_id)
            if context is None or submodule_id not in self.allowed_gap_submodules:
                continue
            if context.availability is SubmoduleAvailability.VERIFIED_EMPTY:
                continue

            gap_type: str | None = None
            trigger_status: SourceStatus | None = None
            conflict_ids = tuple(conflicts.get(submodule_id, ()))
            requested_fields = tuple(required.get(submodule_id, ()))
            if conflict_ids:
                gap_type = "evidence_conflict"
            elif requested_fields:
                gap_type = "required_fact_missing"
            elif context.availability is SubmoduleAvailability.CAPABILITY_ABSENT:
                gap_type = "capability_absent"
                trigger_status = SourceStatus.CAPABILITY_ABSENT
            elif context.availability is SubmoduleAvailability.SOURCE_ERROR:
                gap_type = "source_error"
                trigger_status = SourceStatus.SOURCE_ERROR
            elif (
                context.availability is SubmoduleAvailability.AVAILABLE
                and context.unresolved_gap_ids
            ):
                gap_type = "partial_coverage"
            if gap_type is None:
                continue

            domain = self._domain(submodule_id)
            fields = requested_fields or (f"{domain}.{submodule_id}",)
            tasks.append(
                SupplementTask(
                    task_id=self._task_id(subject.subject_id, submodule_id, gap_type),
                    reason=SupplementTaskReason.EVIDENCE_GAP,
                    target_submodule_id=submodule_id,
                    subject_id=subject.subject_id,
                    report_as_of=report_as_of,
                    allowed_tools=("bounded_web_search",),
                    allowed_sources=(SourceType.PUBLIC_WEB,),
                    requested_fields=fields,
                    max_tool_calls=2,
                    gap_type=gap_type,
                    trigger_status=trigger_status,
                    conflict_evidence_ids=conflict_ids,
                )
            )
        return tuple(tasks)

    @staticmethod
    def _domain(submodule_id: str) -> str:
        return ACQUISITION_CATALOG.get(submodule_id).domain

    @staticmethod
    def _task_id(subject_id: str, submodule_id: str, reason: str) -> str:
        digest = hashlib.sha256(f"{subject_id}|{submodule_id}|{reason}".encode()).hexdigest()[:16]
        return f"supplement:{submodule_id}:{digest}"


__all__ = ["SupplementPolicy"]
