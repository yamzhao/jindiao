"""Private reviewed reporting inputs, persisted separately from the public result."""

from __future__ import annotations

from typing import Literal

from .base import ContractModel
from .evidence import CoverageSummary, Evidence
from .investigation import CheckResult, Finding
from .reporting import Decision


class ReviewedReportInputs(ContractModel):
    schema_version: Literal["reviewed-report-v1"] = "reviewed-report-v1"
    decision: Decision
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    checks: tuple[CheckResult, ...] = ()
    coverage: CoverageSummary
    incomplete: bool
    demo_partial_disclosure: str | None = None
