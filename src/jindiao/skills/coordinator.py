"""Deprecated compatibility entry; real reporting feedback uses the v2 Demo API."""

from __future__ import annotations

from pathlib import Path

from jindiao.contracts.results import (
    SkillEvolutionFeedback,
    SkillEvolutionStatus,
    SkillEvolutionSummary,
)


class ReportingSkillEvolutionCoordinator:
    """Reject legacy unbound feedback without reading or creating candidates."""

    def __init__(self, *, stable_root: Path, artifact_root: Path) -> None:
        # Retain the constructor ABI for existing imports; these paths are never opened.
        del stable_root, artifact_root

    def propose(self, feedback: SkillEvolutionFeedback, *, run_id: str) -> SkillEvolutionSummary:
        del feedback, run_id
        return SkillEvolutionSummary(
            status=SkillEvolutionStatus.REJECTED,
            active_version="unbound",
            reason_codes=("use_feedback_api",),
            change_summary="Legacy feedback is deprecated; use the v2 feedback endpoint.",
        )


__all__ = ["ReportingSkillEvolutionCoordinator"]
