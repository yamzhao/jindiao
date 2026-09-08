"""Validate acquisition layers and create one content-addressed context snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime

from pydantic import JsonValue

from jindiao.acquisition.business_input import business_input_evidence
from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.agents.deepsearch_agent import DeepSearchSupplementOutcome
from jindiao.agents.enterprise_context import EnterpriseContextAcquisitionResult
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    EvidenceProvenance,
    SubmoduleContext,
    SupplementTask,
    SupplementTaskReason,
)
from jindiao.contracts.business import BusinessContext
from jindiao.contracts.evidence import CoverageCompleteness, Evidence, SourceStatus, SourceType
from jindiao.contracts.execution import ExecutionCost
from jindiao.contracts.results import AgentInvestigationResult


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class ContextFreezer:
    """Merge source layers without mutating their authoritative availability state."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        low_confidence_threshold: float = 0.5,
    ) -> None:
        if not 0 <= low_confidence_threshold <= 1:
            raise ValueError("low_confidence_threshold must be between zero and one")
        self._clock = clock
        self._low_confidence_threshold = low_confidence_threshold

    def freeze(
        self,
        acquisition: EnterpriseContextAcquisitionResult,
        *,
        supplement_tasks: tuple[SupplementTask, ...],
        supplement_outcomes: tuple[DeepSearchSupplementOutcome, ...],
        deepsearch_agent_result: AgentInvestigationResult | None,
        shared_acquisition_cost: ExecutionCost,
        business_context: BusinessContext | None = None,
        run_id: str = "unbound",
    ) -> EnterpriseContextSnapshot:
        self._validate_acquisition(acquisition)
        task_by_id = {item.task_id: item for item in supplement_tasks}
        outcome_by_id = {item.task_id: item for item in supplement_outcomes}
        if len(task_by_id) != len(supplement_tasks):
            raise ValueError("supplement task ids must be unique")
        if len(outcome_by_id) != len(supplement_outcomes):
            raise ValueError("supplement outcome task ids must be unique")
        if set(task_by_id) != set(outcome_by_id):
            raise ValueError("every frozen SupplementTask requires exactly one outcome")

        evidence_by_id = {item.evidence_id: item for item in acquisition.evidence}
        frozen_business_context = business_context or BusinessContext()
        for item in business_input_evidence(
            frozen_business_context,
            subject_id=acquisition.subject.subject_id,
            run_id=run_id,
            queried_at=self._clock(),
        ):
            evidence_by_id[item.evidence_id] = item
        submodules = {item.submodule_id: item for item in acquisition.submodules}
        unresolved_gaps = {
            gap for item in acquisition.submodules for gap in item.unresolved_gap_ids
        }
        unresolved_conflicts: set[str] = set()

        for task in supplement_tasks:
            outcome = outcome_by_id[task.task_id]
            self._validate_task_outcome(
                task,
                outcome,
                subject_id=acquisition.subject.subject_id,
                report_as_of=acquisition.report_as_of,
            )
            for supplemental_evidence in outcome.evidence:
                self._validate_evidence(
                    supplemental_evidence,
                    subject_id=acquisition.subject.subject_id,
                    report_as_of=task.report_as_of,
                )
                current = evidence_by_id.get(supplemental_evidence.evidence_id)
                if current is not None and current != supplemental_evidence:
                    raise ValueError(
                        f"Evidence id collision while freezing: {supplemental_evidence.evidence_id}"
                    )
                evidence_by_id.setdefault(
                    supplemental_evidence.evidence_id,
                    supplemental_evidence,
                )
            base_context = submodules[task.target_submodule_id]
            submodules[task.target_submodule_id] = self._merge_supplement(
                base_context,
                task=task,
                outcome=outcome,
            )
            if outcome.evidence and not outcome.unresolved:
                unresolved_gaps.difference_update(base_context.unresolved_gap_ids)
            if outcome.unresolved or outcome.source_status in {
                SourceStatus.VERIFIED_EMPTY,
                SourceStatus.SOURCE_ERROR,
            }:
                unresolved_gaps.add(f"gap:{task.task_id}:{outcome.source_status.value}")
            if any(item.confidence < self._low_confidence_threshold for item in outcome.evidence):
                unresolved_gaps.add(f"gap:{task.task_id}:low_confidence")
            conflicts = {*task.conflict_evidence_ids, *outcome.conflicts_with}
            for evidence_id in conflicts:
                if evidence_id not in evidence_by_id:
                    raise ValueError(
                        f"supplement conflict references unknown Evidence: {evidence_id}"
                    )
            unresolved_conflicts.update(conflicts)

        ordered_submodules = tuple(
            submodules[submodule_id] for submodule_id in acquisition.planned_submodule_ids
        )
        combined_evidence = tuple(evidence_by_id.values())
        agent_results = (acquisition.agent_result,) + (
            (deepsearch_agent_result,) if deepsearch_agent_result is not None else ()
        )
        known_evidence = set(evidence_by_id)
        for result in agent_results:
            result.require_known_evidence(known_evidence)

        content = {
            "schema_version": 1,
            "subject": acquisition.subject.model_dump(mode="json"),
            "report_as_of": acquisition.report_as_of.isoformat(),
            "acquisition_catalog_version": acquisition.acquisition_catalog_version,
            "planned_submodule_ids": list(acquisition.planned_submodule_ids),
            "source_manifest_version": acquisition.source_manifest_version,
            "submodules": [item.model_dump(mode="json") for item in ordered_submodules],
            "evidence": [item.model_dump(mode="json") for item in combined_evidence],
            "supplement_tasks": [item.model_dump(mode="json") for item in supplement_tasks],
            "unresolved_gaps": sorted(unresolved_gaps),
            "unresolved_conflicts": sorted(unresolved_conflicts),
            "acquisition_agent_results": [item.model_dump(mode="json") for item in agent_results],
            "shared_acquisition_cost": shared_acquisition_cost.model_dump(mode="json"),
            "business_context": frozen_business_context.model_dump(mode="json"),
        }
        digest = hashlib.sha256(_canonical_json(content).encode()).hexdigest()
        return EnterpriseContextSnapshot(
            schema_version=1,
            snapshot_id=f"snapshot:{digest[:24]}",
            snapshot_sha256=digest,
            subject=acquisition.subject,
            report_as_of=acquisition.report_as_of,
            created_at=self._clock(),
            acquisition_catalog_version=acquisition.acquisition_catalog_version,
            planned_submodule_ids=acquisition.planned_submodule_ids,
            source_manifest_version=acquisition.source_manifest_version,
            submodules=ordered_submodules,
            evidence=combined_evidence,
            supplement_tasks=supplement_tasks,
            unresolved_gaps=tuple(sorted(unresolved_gaps)),
            unresolved_conflicts=tuple(sorted(unresolved_conflicts)),
            acquisition_agent_results=agent_results,
            shared_acquisition_cost=shared_acquisition_cost,
            business_context=frozen_business_context,
        )

    @staticmethod
    def _validate_acquisition(acquisition: EnterpriseContextAcquisitionResult) -> None:
        if acquisition.acquisition_catalog_version != ACQUISITION_CATALOG.catalog_version:
            raise ValueError("acquisition catalog version does not match runtime")
        actual = tuple(item.submodule_id for item in acquisition.submodules)
        expected = ACQUISITION_CATALOG.plan(acquisition.planned_submodule_ids)
        if actual != acquisition.planned_submodule_ids or actual != expected:
            raise ValueError("acquisition must preserve its versioned plan order")
        for evidence in acquisition.evidence:
            if evidence.subject_id != acquisition.subject.subject_id:
                raise ValueError("acquisition contains Evidence for another subject")

    @staticmethod
    def _validate_task_outcome(
        task: SupplementTask,
        outcome: DeepSearchSupplementOutcome,
        *,
        subject_id: str,
        report_as_of: date,
    ) -> None:
        if task.subject_id != subject_id:
            raise ValueError("SupplementTask belongs to another subject")
        if task.report_as_of != report_as_of:
            raise ValueError("SupplementTask report cutoff does not match acquisition")
        if outcome.task_id != task.task_id:
            raise ValueError("supplement outcome task does not match")
        if outcome.target_submodule_id != task.target_submodule_id:
            raise ValueError("supplement outcome targets another submodule")
        if (
            task.reason is SupplementTaskReason.EVIDENCE_GAP
            and task.trigger_status is SourceStatus.VERIFIED_EMPTY
        ):
            raise ValueError("verified_empty cannot be replaced by DeepSearch")

    @staticmethod
    def _validate_evidence(
        evidence: Evidence,
        *,
        subject_id: str,
        report_as_of: date,
    ) -> None:
        if evidence.subject_id != subject_id:
            raise ValueError("supplement Evidence belongs to another subject")
        if evidence.source_type is not SourceType.PUBLIC_WEB:
            raise ValueError("supplement Evidence must remain in the public-Web layer")
        if evidence.as_of_date is not None and evidence.as_of_date > report_as_of:
            raise ValueError("supplement Evidence is newer than the report cutoff")

    @staticmethod
    def _merge_supplement(
        context: SubmoduleContext,
        *,
        task: SupplementTask,
        outcome: DeepSearchSupplementOutcome,
    ) -> SubmoduleContext:
        supplemental_ids = tuple(
            dict.fromkeys(
                (*context.supplemental_evidence_ids, *(e.evidence_id for e in outcome.evidence))
            )
        )
        provenance = list(context.provenance)
        existing_provenance = {item.evidence_id for item in provenance}
        provenance.extend(
            EvidenceProvenance(
                evidence_id=item.evidence_id,
                source_type=item.source_type,
                source_status=item.source_status,
                source_tool=item.source_tool,
                source_record_id=item.source_record_id,
                content_hash=item.content_hash,
                source_chain=item.source_chain,
                supplement_task_id=task.task_id,
            )
            for item in outcome.evidence
            if item.evidence_id not in existing_provenance
        )
        facts = dict(context.facts)
        fact_group: dict[str, JsonValue] = {
            "source_status": outcome.source_status.value,
            **outcome.scope,
            "evidence_ids": [item.evidence_id for item in outcome.evidence],
        }
        key = (
            "social_security"
            if task.reason is SupplementTaskReason.BASELINE_ENRICHMENT
            else f"supplement_{task.task_id.rsplit(':', maxsplit=1)[-1]}"
        )
        if key == "social_security":
            requested_fields = tuple(task.requested_fields)
            covered_fields = tuple(
                field
                for field in requested_fields
                if outcome.source_status is SourceStatus.VERIFIED_EMPTY
                or any(field in item.supports_fields for item in outcome.evidence)
            )
            if outcome.source_status in {
                SourceStatus.CAPABILITY_ABSENT,
                SourceStatus.SOURCE_ERROR,
            }:
                local_completeness = CoverageCompleteness.UNKNOWN
            elif len(covered_fields) == len(requested_fields):
                local_completeness = CoverageCompleteness.COMPLETE
            else:
                local_completeness = CoverageCompleteness.PARTIAL
            fact_group.update(
                {
                    "requested_fields": list(requested_fields),
                    "covered_fields": list(covered_fields),
                    "coverage_completeness": local_completeness.value,
                    # The provider covers only one fact group, never the full annual report.
                    "submodule_coverage": CoverageCompleteness.PARTIAL.value,
                }
            )
        facts[key] = fact_group
        conflicts = tuple(
            dict.fromkeys(
                (
                    *context.conflict_evidence_ids,
                    *task.conflict_evidence_ids,
                    *outcome.conflicts_with,
                )
            )
        )
        return context.model_copy(
            update={
                "completeness": CoverageCompleteness.PARTIAL,
                "facts": facts,
                "supplemental_evidence_ids": supplemental_ids,
                "provenance": tuple(provenance),
                "supplement_task_ids": tuple(
                    dict.fromkeys((*context.supplement_task_ids, task.task_id))
                ),
                "conflict_evidence_ids": conflicts,
            }
        )


__all__ = ["ContextFreezer"]
