"""Risk decision and report view contracts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .base import ContractModel
from .entities import ResolvedSubject
from .evidence import CoverageSummary, Evidence
from .investigation import Finding


class DecisionBand(StrEnum):
    PASS = "pass"
    MANUAL_REVIEW = "manual_review"
    REJECT = "reject"


class ReportSectionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ReportSubmoduleDefinition(ContractModel):
    submodule_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)


class ReportModuleDefinition(ContractModel):
    module_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    title: str = Field(min_length=1)
    submodules: tuple[ReportSubmoduleDefinition, ...] = ()

    @model_validator(mode="after")
    def require_unique_submodules(self) -> ReportModuleDefinition:
        submodule_ids = [item.submodule_id for item in self.submodules]
        if len(submodule_ids) != len(set(submodule_ids)):
            raise ValueError("report module submodule ids must be unique")
        return self


class ReportCatalog(ContractModel):
    schema_version: Literal[1]
    catalog_version: str = Field(min_length=1)
    module_count: int = Field(ge=1)
    submodule_count: int = Field(ge=1)
    modules: tuple[ReportModuleDefinition, ...] = Field(min_length=1)

    @property
    def module_ids(self) -> tuple[str, ...]:
        return tuple(module.module_id for module in self.modules)

    @property
    def submodule_ids(self) -> tuple[str, ...]:
        return tuple(
            submodule.submodule_id for module in self.modules for submodule in module.submodules
        )

    def module_for_submodule(self, submodule_id: str) -> ReportModuleDefinition:
        for module in self.modules:
            if any(item.submodule_id == submodule_id for item in module.submodules):
                return module
        raise KeyError(submodule_id)

    @model_validator(mode="after")
    def validate_catalog_shape(self) -> ReportCatalog:
        if len(self.modules) != self.module_count:
            raise ValueError(
                f"declared module_count {self.module_count} does not match "
                f"{len(self.modules)} modules"
            )
        if len(self.module_ids) != len(set(self.module_ids)):
            raise ValueError("report module ids must be unique")
        if len(self.submodule_ids) != len(set(self.submodule_ids)):
            raise ValueError("report catalog submodule ids must be unique")
        if len(self.submodule_ids) != self.submodule_count:
            raise ValueError(
                f"declared submodule_count {self.submodule_count} does not match "
                f"{len(self.submodule_ids)} submodules"
            )
        return self


class ReportStructure(ContractModel):
    catalog_version: str = Field(min_length=1)
    module_count: int = Field(ge=1)
    submodule_count: int = Field(ge=1)
    modules: tuple[ReportModuleDefinition, ...] = Field(min_length=1)

    @classmethod
    def from_catalog(cls, catalog: ReportCatalog) -> ReportStructure:
        return cls(
            catalog_version=catalog.catalog_version,
            module_count=catalog.module_count,
            submodule_count=catalog.submodule_count,
            modules=catalog.modules,
        )

    @model_validator(mode="after")
    def validate_structure_shape(self) -> ReportStructure:
        ReportCatalog(
            schema_version=1,
            catalog_version=self.catalog_version,
            module_count=self.module_count,
            submodule_count=self.submodule_count,
            modules=self.modules,
        )
        return self


class RuleHit(ContractModel):
    rule_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    points: int = Field(ge=0)
    explanation: str = Field(min_length=1)


class Decision(ContractModel):
    band: DecisionBand
    score: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    rule_version: str = Field(min_length=1)
    rule_hits: tuple[RuleHit, ...]
    major_risk_finding_ids: tuple[str, ...]
    pending_review_items: tuple[str, ...]
    as_of_date: date

    @model_validator(mode="after")
    def validate_score_sum(self) -> Decision:
        calculated = sum(hit.points for hit in self.rule_hits)
        if self.score != calculated:
            raise ValueError(
                f"decision score {self.score} does not match rule hit sum {calculated}"
            )
        return self


class RiskSummary(ContractModel):
    admission_count: int = Field(ge=0)
    attention_count: int = Field(ge=0)
    non_risk_count: int = Field(ge=0)
    total_score: int = Field(ge=0)
    top_finding_ids: tuple[str, ...]


class ReportSection(ContractModel):
    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: ReportSectionStatus
    coverage: float = Field(ge=0, le=1)
    finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    data: dict[str, JsonValue] = Field(default_factory=dict)


class FrontendView(ContractModel):
    view_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    data: dict[str, JsonValue] = Field(default_factory=dict)


class ReportViewModel(ContractModel):
    subject: ResolvedSubject
    decision: Decision
    risk_summary: RiskSummary
    coverage: CoverageSummary
    sections: tuple[ReportSection, ...]
    frontend_views: tuple[FrontendView, ...] = ()
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    generated_at: AwareDatetime
    source_disclosure: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_sections(self) -> ReportViewModel:
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("report section ids must be unique")
        view_ids = [view.view_id for view in self.frontend_views]
        if len(view_ids) != len(set(view_ids)):
            raise ValueError("frontend view ids must be unique")

        if self.findings or self.evidence:
            finding_ids = {finding.finding_id for finding in self.findings}
            evidence_ids = {item.evidence_id for item in self.evidence}
            for section in self.sections:
                if not set(section.finding_ids) <= finding_ids:
                    raise ValueError("report section references unknown findings")
                if not set(section.evidence_ids) <= evidence_ids:
                    raise ValueError("report section references unknown evidence")
        return self


__all__ = [
    "Decision",
    "DecisionBand",
    "FrontendView",
    "ReportCatalog",
    "ReportModuleDefinition",
    "ReportSection",
    "ReportSectionStatus",
    "ReportStructure",
    "ReportSubmoduleDefinition",
    "ReportViewModel",
    "RiskSummary",
    "RuleHit",
]
