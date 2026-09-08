"""Shared evidence-acquisition task and frozen enterprise-context contracts."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .base import ContractModel
from .business import BusinessContext
from .entities import ResolvedSubject
from .evidence import CoverageCompleteness, Evidence, SourceStatus, SourceType
from .execution import ExecutionCost
from .results import AgentInvestigationResult, AgentResultPhase


class SupplementTaskReason(StrEnum):
    BASELINE_ENRICHMENT = "baseline_enrichment"
    EVIDENCE_GAP = "evidence_gap"


class SubmoduleAvailability(StrEnum):
    AVAILABLE = "available"
    VERIFIED_EMPTY = "verified_empty"
    CAPABILITY_ABSENT = "capability_absent"
    SOURCE_ERROR = "source_error"
    NOT_REQUESTED = "not_requested"


class SupplementTask(ContractModel):
    task_id: str = Field(min_length=1)
    reason: SupplementTaskReason
    target_submodule_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    subject_id: str = Field(min_length=1)
    report_as_of: date
    allowed_tools: tuple[str, ...] = Field(min_length=1)
    allowed_sources: tuple[SourceType, ...] = Field(min_length=1)
    requested_fields: tuple[str, ...] = Field(min_length=1)
    max_tool_calls: int = Field(ge=1)
    gap_type: str | None = None
    trigger_status: SourceStatus | None = None
    conflict_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_reason_fields(self) -> SupplementTask:
        if self.reason is SupplementTaskReason.EVIDENCE_GAP and not self.gap_type:
            raise ValueError("evidence_gap tasks require gap_type")
        if self.reason is SupplementTaskReason.BASELINE_ENRICHMENT and self.gap_type is not None:
            raise ValueError("baseline_enrichment tasks cannot declare gap_type")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("supplement task allowed_tools must be unique")
        if len(self.requested_fields) != len(set(self.requested_fields)):
            raise ValueError("supplement task requested_fields must be unique")
        return self


class EvidenceProvenance(ContractModel):
    evidence_id: str = Field(min_length=1)
    source_type: SourceType
    source_status: SourceStatus
    source_tool: str | None = None
    source_record_id: str | None = None
    content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    source_chain: tuple[str, ...] = ()
    supplement_task_id: str | None = None


class SubmoduleContext(ContractModel):
    submodule_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    availability: SubmoduleAvailability
    completeness: CoverageCompleteness
    facts: dict[str, JsonValue] = Field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    supplemental_evidence_ids: tuple[str, ...] = ()
    provenance: tuple[EvidenceProvenance, ...] = ()
    supplement_task_ids: tuple[str, ...] = ()
    unresolved_gap_ids: tuple[str, ...] = ()
    conflict_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_availability(self) -> SubmoduleContext:
        if self.availability is SubmoduleAvailability.AVAILABLE and not self.evidence_ids:
            raise ValueError("available submodule context requires Evidence")
        if self.availability is SubmoduleAvailability.VERIFIED_EMPTY and self.evidence_ids:
            raise ValueError("verified_empty submodule context cannot reference Evidence records")
        provenance_ids = tuple(item.evidence_id for item in self.provenance)
        if len(provenance_ids) != len(set(provenance_ids)):
            raise ValueError("submodule provenance Evidence ids must be unique")
        if not set(provenance_ids) <= {
            *self.evidence_ids,
            *self.supplemental_evidence_ids,
        }:
            raise ValueError("submodule provenance references undeclared Evidence")
        all_evidence_ids = (*self.evidence_ids, *self.supplemental_evidence_ids)
        if len(all_evidence_ids) != len(set(all_evidence_ids)):
            raise ValueError("submodule Evidence ids must be unique across source layers")
        return self


class EnterpriseContextSnapshot(ContractModel):
    schema_version: Literal[1]
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject: ResolvedSubject
    report_as_of: date
    created_at: AwareDatetime
    acquisition_catalog_version: str = Field(min_length=1)
    planned_submodule_ids: tuple[str, ...] = Field(min_length=1)
    source_manifest_version: str = Field(min_length=1)
    submodules: tuple[SubmoduleContext, ...]
    evidence: tuple[Evidence, ...]
    supplement_tasks: tuple[SupplementTask, ...]
    unresolved_gaps: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    acquisition_agent_results: tuple[AgentInvestigationResult, ...] = ()
    shared_acquisition_cost: ExecutionCost = Field(default_factory=ExecutionCost.zero)
    business_context: BusinessContext = Field(default_factory=BusinessContext)

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def submodule(self, submodule_id: str) -> SubmoduleContext:
        for item in self.submodules:
            if item.submodule_id == submodule_id:
                return item
        raise KeyError(submodule_id)

    @model_validator(mode="after")
    def validate_snapshot_references(self) -> EnterpriseContextSnapshot:
        submodule_ids = tuple(item.submodule_id for item in self.submodules)
        if len(submodule_ids) != len(set(submodule_ids)):
            raise ValueError("enterprise context snapshot submodule ids must be unique")
        if submodule_ids != self.planned_submodule_ids:
            raise ValueError("snapshot submodules must preserve the frozen acquisition plan")
        if len(self.planned_submodule_ids) != len(set(self.planned_submodule_ids)):
            raise ValueError("snapshot acquisition plan ids must be unique")

        evidence_ids = tuple(item.evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("enterprise context snapshot Evidence ids must be unique")
        known_evidence = set(evidence_ids)
        referenced_evidence = {
            evidence_id
            for submodule in self.submodules
            for evidence_id in (
                *submodule.evidence_ids,
                *submodule.supplemental_evidence_ids,
                *submodule.conflict_evidence_ids,
            )
        }
        unknown_evidence = referenced_evidence - known_evidence
        if unknown_evidence:
            raise ValueError(f"snapshot references unknown Evidence: {sorted(unknown_evidence)}")

        wrong_subject = {
            item.evidence_id for item in self.evidence if item.subject_id != self.subject.subject_id
        }
        if wrong_subject:
            raise ValueError(
                f"snapshot contains Evidence for another subject: {sorted(wrong_subject)}"
            )

        task_ids = tuple(task.task_id for task in self.supplement_tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("enterprise context snapshot supplement task ids must be unique")
        known_submodules = set(submodule_ids)
        for task in self.supplement_tasks:
            if task.subject_id != self.subject.subject_id:
                raise ValueError("supplement task subject does not match snapshot subject")
            if task.report_as_of != self.report_as_of:
                raise ValueError("supplement task report_as_of does not match snapshot")
            if task.target_submodule_id not in known_submodules:
                raise ValueError("supplement task targets an unknown snapshot submodule")
        for agent_result in self.acquisition_agent_results:
            if agent_result.phase is not AgentResultPhase.ACQUISITION:
                raise ValueError("snapshot agent contributions must belong to acquisition")
            agent_result.require_known_evidence(known_evidence)
        return self


__all__ = [
    "EnterpriseContextSnapshot",
    "EvidenceProvenance",
    "SubmoduleAvailability",
    "SubmoduleContext",
    "SupplementTask",
    "SupplementTaskReason",
]
