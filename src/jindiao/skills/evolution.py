"""Governed Skill evolution with isolated candidates and explicit activation."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

_VERSION_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


class CandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTIVATED = "activated"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    source: str
    reference: str
    text: str
    received_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    baseline_score: float
    candidate_score: float
    metrics: dict[str, float]

    @property
    def passed(self) -> bool:
        return self.candidate_score >= self.baseline_score


@dataclass(frozen=True, slots=True)
class SkillCandidate:
    candidate_id: str
    skill_name: str
    source_version: str
    candidate_version: str
    source_hash: str
    candidate_hash: str
    feedback: tuple[FeedbackRecord, ...]
    evaluation: EvaluationRecord
    changed_files: tuple[str, ...]
    status: CandidateStatus
    rejection_reasons: tuple[str, ...]
    created_at: str
    directory: Path
    approved_by: str | None = None
    activated_at: str | None = None
    rollback_approved_by: str | None = None
    rolled_back_at: str | None = None

    @property
    def skill_directory(self) -> Path:
        return self.directory / self.skill_name


class EvolutionGovernanceError(ValueError):
    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


class EvolutionGovernanceGuard:
    """Reject candidate changes that could weaken deterministic controls."""

    _FORBIDDEN_PATHS = (
        "config/risk",
        "src/jindiao/risk",
        "src/jindiao/tianyancha",
    )
    _FORBIDDEN_CONTENT: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("risk_rules", re.compile(r"\b(?:risk rule|risk_rules?|风险规则)\b", re.I)),
        (
            "thresholds_or_bands",
            re.compile(r"\b(?:thresholds?|decision bands?|score bands?)\b|阈值|分档", re.I),
        ),
        (
            "source_priority_or_fallback",
            re.compile(r"\b(?:source priority|fallback authorization)\b|来源优先级|回退授权", re.I),
        ),
        (
            "mock_marking",
            re.compile(r"\b(?:is_mock|source_status|mock marking)\b|Mock\s*标识", re.I),
        ),
        (
            "evidence_gate",
            re.compile(
                r"\b(?:evidence acceptance|accepted finding|evidence gate)\b|证据门槛", re.I
            ),
        ),
        (
            "credentials",
            re.compile(
                r"\b(?:authorization header|api[_ -]?key|access[_ -]?token|secret)\b|密钥|凭据",
                re.I,
            ),
        ),
        ("redaction", re.compile(r"\b(?:redaction|unredacted)\b|脱敏", re.I)),
    )

    def assert_allowed(self, *, changed_files: Sequence[str], candidate_addition: str) -> None:
        violations: list[str] = []
        for raw_path in changed_files:
            path = PurePosixPath(raw_path).as_posix().lstrip("./")
            if any(path.startswith(prefix) for prefix in self._FORBIDDEN_PATHS):
                violations.append(f"forbidden_path:{path}")
        for rule_name, pattern in self._FORBIDDEN_CONTENT:
            if pattern.search(candidate_addition):
                violations.append(f"forbidden_content:{rule_name}")
        if violations:
            raise EvolutionGovernanceError(tuple(dict.fromkeys(violations)))


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _next_patch(version: str) -> str:
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid semantic version: {version}")
    return f"{match.group('major')}.{match.group('minor')}.{int(match.group('patch')) + 1}"


def _added_text(original: str, candidate: str) -> str:
    return "\n".join(
        line[2:]
        for line in difflib.ndiff(original.splitlines(), candidate.splitlines())
        if line.startswith("+ ")
    )


class SkillEvolutionRegistry:
    """Persist candidates separately and guard stable Skill activation/rollback."""

    def __init__(
        self,
        *,
        stable_root: Path,
        candidate_root: Path,
        versions_root: Path,
        governance: EvolutionGovernanceGuard | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.stable_root = stable_root.resolve()
        self.candidate_root = candidate_root.resolve()
        self.versions_root = versions_root.resolve()
        self.governance = governance or EvolutionGovernanceGuard()
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or (lambda: datetime.now(UTC))

    def propose(
        self,
        *,
        skill_name: str,
        new_skill_md: str,
        feedback: Sequence[FeedbackRecord],
        evaluation: EvaluationRecord,
        changed_files: Sequence[str] = ("SKILL.md",),
    ) -> SkillCandidate:
        stable = self._stable_skill(skill_name)
        original = (stable / "SKILL.md").read_text(encoding="utf-8")
        source_version = (stable / "VERSION").read_text(encoding="utf-8").strip()
        source_hash = _directory_hash(stable)
        candidate_id = self._id_factory()
        directory = self.candidate_root / candidate_id
        if directory.exists():
            raise ValueError(f"candidate already exists: {candidate_id}")
        candidate_skill = directory / skill_name
        candidate_skill.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(stable, candidate_skill)
        (candidate_skill / "SKILL.md").write_text(new_skill_md, encoding="utf-8")
        candidate_version = _next_patch(source_version)
        (candidate_skill / "VERSION").write_text(f"{candidate_version}\n", encoding="utf-8")

        rejection_reasons: list[str] = []
        try:
            self.governance.assert_allowed(
                changed_files=changed_files,
                candidate_addition=_added_text(original, new_skill_md),
            )
        except EvolutionGovernanceError as error:
            rejection_reasons.extend(f"governance:{item}" for item in error.violations)
        if not evaluation.passed:
            rejection_reasons.append("evaluation_regression")
        status = (
            CandidateStatus.REJECTED if rejection_reasons else CandidateStatus.AWAITING_APPROVAL
        )
        candidate = SkillCandidate(
            candidate_id=candidate_id,
            skill_name=skill_name,
            source_version=source_version,
            candidate_version=candidate_version,
            source_hash=source_hash,
            candidate_hash=_directory_hash(candidate_skill),
            feedback=tuple(feedback),
            evaluation=evaluation,
            changed_files=tuple(changed_files),
            status=status,
            rejection_reasons=tuple(rejection_reasons),
            created_at=self._clock().isoformat(),
            directory=directory,
        )
        self._save(candidate)
        self._save_stable_metadata(
            skill_name=skill_name,
            version=source_version,
            content_hash=source_hash,
            approved_by=None,
            activated_candidate_id=None,
        )
        return candidate

    def activate(self, candidate_id: str, *, approved_by: str) -> SkillCandidate:
        if not approved_by.strip():
            raise ValueError("a human approver is required")
        candidate = self.load(candidate_id)
        if candidate.status is not CandidateStatus.AWAITING_APPROVAL:
            raise ValueError("candidate is not awaiting approval")
        stable = self._stable_skill(candidate.skill_name)
        if _directory_hash(stable) != candidate.source_hash:
            raise ValueError("stable Skill changed after candidate creation")
        if _directory_hash(candidate.skill_directory) != candidate.candidate_hash:
            raise ValueError("candidate content hash mismatch")
        backup = self.versions_root / candidate.skill_name / candidate.source_version
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if _directory_hash(backup) != candidate.source_hash:
                raise ValueError("version backup hash mismatch")
        else:
            shutil.copytree(stable, backup)
        shutil.rmtree(stable)
        shutil.copytree(candidate.skill_directory, stable)
        activated = self._replace_candidate(
            candidate,
            status=CandidateStatus.ACTIVATED,
            approved_by=approved_by.strip(),
            activated_at=self._clock().isoformat(),
        )
        self._save(activated)
        self._save_stable_metadata(
            skill_name=candidate.skill_name,
            version=candidate.candidate_version,
            content_hash=candidate.candidate_hash,
            approved_by=approved_by.strip(),
            activated_candidate_id=candidate.candidate_id,
        )
        return activated

    def rollback(self, *, skill_name: str, version: str, approved_by: str) -> None:
        if not approved_by.strip():
            raise ValueError("a human approver is required")
        stable = self._stable_skill(skill_name)
        backup = self.versions_root / skill_name / version
        if not backup.is_dir():
            raise ValueError(f"version backup does not exist: {version}")
        current_version = (stable / "VERSION").read_text(encoding="utf-8").strip()
        current_backup = self.versions_root / skill_name / current_version
        current_backup.parent.mkdir(parents=True, exist_ok=True)
        if not current_backup.exists():
            shutil.copytree(stable, current_backup)
        shutil.rmtree(stable)
        shutil.copytree(backup, stable)
        rolled_back_at = self._clock().isoformat()
        audit_path = self.versions_root / skill_name / "rollback-audit.jsonl"
        with audit_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "approved_by": approved_by.strip(),
                        "from_version": current_version,
                        "rolled_back_at": rolled_back_at,
                        "skill_name": skill_name,
                        "to_version": version,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        self._mark_rolled_back(skill_name, current_version, approved_by.strip(), rolled_back_at)
        self._save_stable_metadata(
            skill_name=skill_name,
            version=version,
            content_hash=_directory_hash(stable),
            approved_by=approved_by.strip(),
            activated_candidate_id=None,
        )

    def load(self, candidate_id: str) -> SkillCandidate:
        directory = self.candidate_root / candidate_id
        metadata_path = directory / "metadata.json"
        if not metadata_path.is_file():
            raise ValueError(f"candidate does not exist: {candidate_id}")
        raw: object = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("candidate metadata must be an object")
        evaluation = raw.get("evaluation")
        feedback = raw.get("feedback")
        if not isinstance(evaluation, dict) or not isinstance(feedback, list):
            raise ValueError("candidate metadata is incomplete")
        return SkillCandidate(
            candidate_id=str(raw["candidate_id"]),
            skill_name=str(raw["skill_name"]),
            source_version=str(raw["source_version"]),
            candidate_version=str(raw["candidate_version"]),
            source_hash=str(raw["source_hash"]),
            candidate_hash=str(raw["candidate_hash"]),
            feedback=tuple(FeedbackRecord(**item) for item in feedback),
            evaluation=EvaluationRecord(**evaluation),
            changed_files=tuple(str(item) for item in raw["changed_files"]),
            status=CandidateStatus(str(raw["status"])),
            rejection_reasons=tuple(str(item) for item in raw["rejection_reasons"]),
            created_at=str(raw["created_at"]),
            approved_by=str(raw["approved_by"]) if raw.get("approved_by") else None,
            activated_at=str(raw["activated_at"]) if raw.get("activated_at") else None,
            rollback_approved_by=(
                str(raw["rollback_approved_by"]) if raw.get("rollback_approved_by") else None
            ),
            rolled_back_at=(str(raw["rolled_back_at"]) if raw.get("rolled_back_at") else None),
            directory=directory,
        )

    def _stable_skill(self, skill_name: str) -> Path:
        if not skill_name or PurePosixPath(skill_name).name != skill_name:
            raise ValueError("skill_name must be one path segment")
        stable = self.stable_root / skill_name
        if not stable.is_dir():
            raise ValueError(f"stable Skill does not exist: {skill_name}")
        return stable

    def _save(self, candidate: SkillCandidate) -> None:
        payload: dict[str, object] = {
            "activated_at": candidate.activated_at,
            "approved_by": candidate.approved_by,
            "candidate_hash": candidate.candidate_hash,
            "candidate_id": candidate.candidate_id,
            "candidate_version": candidate.candidate_version,
            "changed_files": list(candidate.changed_files),
            "created_at": candidate.created_at,
            "evaluation": asdict(candidate.evaluation),
            "feedback": [asdict(item) for item in candidate.feedback],
            "rejection_reasons": list(candidate.rejection_reasons),
            "rollback_approved_by": candidate.rollback_approved_by,
            "rolled_back_at": candidate.rolled_back_at,
            "skill_name": candidate.skill_name,
            "source_hash": candidate.source_hash,
            "source_version": candidate.source_version,
            "status": candidate.status.value,
        }
        candidate.directory.mkdir(parents=True, exist_ok=True)
        (candidate.directory / "metadata.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _replace_candidate(
        candidate: SkillCandidate,
        *,
        status: CandidateStatus,
        approved_by: str,
        activated_at: str,
        rollback_approved_by: str | None = None,
        rolled_back_at: str | None = None,
    ) -> SkillCandidate:
        return SkillCandidate(
            candidate_id=candidate.candidate_id,
            skill_name=candidate.skill_name,
            source_version=candidate.source_version,
            candidate_version=candidate.candidate_version,
            source_hash=candidate.source_hash,
            candidate_hash=candidate.candidate_hash,
            feedback=candidate.feedback,
            evaluation=candidate.evaluation,
            changed_files=candidate.changed_files,
            status=status,
            rejection_reasons=candidate.rejection_reasons,
            created_at=candidate.created_at,
            directory=candidate.directory,
            approved_by=approved_by,
            activated_at=activated_at,
            rollback_approved_by=rollback_approved_by,
            rolled_back_at=rolled_back_at,
        )

    def _mark_rolled_back(
        self, skill_name: str, version: str, approved_by: str, rolled_back_at: str
    ) -> None:
        if not self.candidate_root.is_dir():
            return
        for directory in self.candidate_root.iterdir():
            metadata = directory / "metadata.json"
            if not metadata.is_file():
                continue
            candidate = self.load(directory.name)
            if (
                candidate.skill_name == skill_name
                and candidate.candidate_version == version
                and candidate.status is CandidateStatus.ACTIVATED
            ):
                rolled_back = self._replace_candidate(
                    candidate,
                    status=CandidateStatus.ROLLED_BACK,
                    approved_by=candidate.approved_by or approved_by,
                    activated_at=candidate.activated_at or rolled_back_at,
                    rollback_approved_by=approved_by,
                    rolled_back_at=rolled_back_at,
                )
                self._save(rolled_back)

    def _save_stable_metadata(
        self,
        *,
        skill_name: str,
        version: str,
        content_hash: str,
        approved_by: str | None,
        activated_candidate_id: str | None,
    ) -> None:
        registry = self.versions_root / skill_name
        registry.mkdir(parents=True, exist_ok=True)
        (registry / "stable-metadata.json").write_text(
            json.dumps(
                {
                    "activated_candidate_id": activated_candidate_id,
                    "approved_by": approved_by,
                    "content_hash": content_hash,
                    "skill_name": skill_name,
                    "status": "stable",
                    "updated_at": self._clock().isoformat(),
                    "version": version,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class EvolutionRailBundle:
    evolution: Any
    interrupt: Any
    review_runtime: Any


class OpenJiuwenEvolutionRailFactory:
    """Wire official openJiuwen evolution rails to an isolated candidate Skill."""

    def __init__(
        self,
        *,
        candidate_root: Path,
        review_runtime_type: Callable[..., Any] | None = None,
        evolution_rail_type: Callable[..., Any] | None = None,
        interrupt_rail_type: Callable[..., Any] | None = None,
    ) -> None:
        self._candidate_root = candidate_root.resolve()
        if review_runtime_type is None:
            from openjiuwen.harness.rails.evolution import EvolutionReviewRuntime

            review_runtime_type = EvolutionReviewRuntime
        if evolution_rail_type is None or interrupt_rail_type is None:
            from openjiuwen.harness.rails import EvolutionInterruptRail, TeamSkillEvolutionRail

            evolution_rail_type = evolution_rail_type or TeamSkillEvolutionRail
            interrupt_rail_type = interrupt_rail_type or EvolutionInterruptRail
        self._review_runtime_type = review_runtime_type
        self._evolution_rail_type = evolution_rail_type
        self._interrupt_rail_type = interrupt_rail_type

    def create(
        self,
        *,
        candidate_skill_dir: Path,
        llm: object,
        model: str,
        trajectory_span_processor: object,
        team_id: str,
    ) -> EvolutionRailBundle:
        candidate_skill_dir = candidate_skill_dir.resolve()
        if not candidate_skill_dir.is_dir():
            raise ValueError("candidate Skill directory does not exist")
        try:
            candidate_skill_dir.relative_to(self._candidate_root)
        except ValueError as error:
            raise ValueError("evolution rails may only target the candidate root") from error
        review_runtime = self._review_runtime_type()
        evolution = self._evolution_rail_type(
            str(candidate_skill_dir),
            llm=llm,
            model=model,
            trajectory_span_processor=trajectory_span_processor,
            review_runtime=review_runtime,
            signal_trigger=True,
            review_trigger=True,
            auto_save=False,
            team_id=team_id,
        )
        interrupt = self._interrupt_rail_type(
            review_runtime=review_runtime,
            submission_service=evolution.approval_submission_service,
            auto_save=False,
            language="cn",
        )
        return EvolutionRailBundle(
            evolution=evolution,
            interrupt=interrupt,
            review_runtime=review_runtime,
        )


__all__ = [
    "CandidateStatus",
    "EvaluationRecord",
    "EvolutionGovernanceError",
    "EvolutionGovernanceGuard",
    "EvolutionRailBundle",
    "FeedbackRecord",
    "OpenJiuwenEvolutionRailFactory",
    "SkillCandidate",
    "SkillEvolutionRegistry",
]
