"""Local single-operator demo state; deliberately not a production registry."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.report_policy import (
    ReportFeedbackRequest,
    ReportingPolicyBinding,
    ReportPolicy,
)
from jindiao.reporting.gaps import GapAnnotationBuilder
from jindiao.reporting.replay import (
    MAX_TOTAL_BYTES,
    ReplayDeadlineError,
    ReplayEvaluation,
    ReplayLimitError,
    ReplaySnapshot,
    canonical,
    digest,
    implementation_fingerprint,
    replay,
)
from jindiao.security import redact_text


def bootstrap() -> ReportingPolicyBinding:
    return ReportingPolicyBinding.freeze(ReportPolicy(), version="1.1.0", revision=0)


class DemoCandidate(ContractModel):
    evolution_id: str
    source_run_id: str
    owner_id: str
    session_id: str | None
    request_sha256: str
    feedback: ReportFeedbackRequest
    snapshot: ReplaySnapshot
    candidate: ReportingPolicyBinding
    status: Literal["awaiting_approval", "rejected", "failed"]
    reason_codes: tuple[str, ...]
    evaluation: ReplayEvaluation | None
    evaluation_sha256: str | None
    created_at: str


class DemoState(ContractModel):
    active: ReportingPolicyBinding = Field(default_factory=bootstrap)
    applied_evolution_id: str | None = None
    reason: str = "bootstrap"
    changed_at: str | None = None


class ReportingDemoStore:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    def _path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("demo symlink paths are not allowed")
        return path

    @contextmanager
    def _lock(self) -> Iterator[None]:
        path = self._path(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("demo busy") from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _read(self, path: Path) -> dict[str, object]:
        if path.stat().st_size > MAX_TOTAL_BYTES * 2:
            raise ValueError("demo file too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid demo file envelope")
        payload = raw.get("payload")
        if not isinstance(payload, dict) or raw.get("sha256") != digest(canonical(payload)):
            raise ValueError("demo file hash mismatch")
        return payload

    def _write(self, path: Path, model: ContractModel) -> None:
        payload = model.model_dump(mode="json")
        content = canonical({"payload": payload, "sha256": digest(canonical(payload))})
        if len(content.encode()) > MAX_TOTAL_BYTES * 2:
            raise ValueError("demo file too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def state(self) -> DemoState:
        path = self._path("state.json")
        return DemoState.model_validate(self._read(path)) if path.exists() else DemoState()

    def active(self) -> ReportingPolicyBinding:
        return self.state().active

    def get(self, evolution_id: str) -> DemoCandidate:
        if not re.fullmatch(r"evo-[a-f0-9]{64}", evolution_id):
            raise ValueError("invalid evolution id")
        return DemoCandidate.model_validate(
            self._read(self._path("candidates", f"{evolution_id}.json"))
        )

    def propose(
        self,
        snapshot: ReplaySnapshot,
        feedback: ReportFeedbackRequest,
        *,
        key: str,
        owner: str,
        session: str | None,
    ) -> tuple[DemoCandidate, bool]:
        if not key.strip() or len(key) > 128:
            raise ValueError("invalid idempotency key")
        identity = canonical([owner, session, snapshot.run_id, key])
        evolution_id = "evo-" + digest(identity).removeprefix("sha256:")
        request_hash = digest(canonical(feedback.model_dump(mode="json")))
        with self._lock():
            path = self._path("candidates", f"{evolution_id}.json")
            if path.exists():
                existing = self.get(evolution_id)
                if existing.request_sha256 != request_hash:
                    raise ValueError("idempotency conflict")
                return existing, False
            active = self.active()
            if snapshot.binding != active:
                raise ValueError("baseline stale")
            safe_feedback = ReportFeedbackRequest.model_validate(
                {
                    **feedback.model_dump(),
                    "text": redact_text(feedback.text),
                    "source": redact_text(feedback.source),
                }
            )
            evaluation = None
            status: Literal["awaiting_approval", "rejected", "failed"] = "rejected"
            reasons: tuple[str, ...] = ()
            try:
                evaluation = replay(snapshot, target_section_ids=feedback.target_section_ids)
                status = "awaiting_approval" if evaluation.passed else "rejected"
                reasons = evaluation.reasons
                if active.policy.gap_placement == "section_and_appendix":
                    reasons = ("no_change",)
                elif evaluation.cases[0].denominator == 0:
                    reasons = (
                        "unmapped_gap"
                        if GapAnnotationBuilder().build(snapshot.view)
                        else "no_applicable_gap",
                    )
            except ReplayLimitError:
                status, reasons = "failed", ("replay_limit_exceeded",)
            except ReplayDeadlineError:
                status, reasons = "failed", ("replay_deadline_exceeded",)
            except Exception:
                # Evaluation is an isolated resource; never leak raw exception text or
                # abandon the idempotency receipt when the evaluator fails.
                status, reasons = "failed", ("replay_failed",)
            # Deterministic unique version per candidate, not an active-version counter.
            version = "1.1." + str(int(evolution_id[-24:], 16) + 1)
            candidate = DemoCandidate(
                evolution_id=evolution_id,
                source_run_id=snapshot.run_id,
                owner_id=owner,
                session_id=session,
                request_sha256=request_hash,
                feedback=safe_feedback,
                snapshot=snapshot,
                candidate=ReportingPolicyBinding.freeze(
                    ReportPolicy(gap_placement="section_and_appendix"),
                    version=version,
                    revision=active.revision + 1,
                ),
                status=status,
                reason_codes=reasons,
                evaluation=evaluation,
                evaluation_sha256=(
                    digest(canonical(evaluation.model_dump(mode="json"))) if evaluation else None
                ),
                created_at=datetime.now(UTC).isoformat(),
            )
            self._write(path, candidate)
            return candidate, True

    def apply(self, evolution_id: str, *, reason: str) -> DemoState:
        if not reason.strip():
            raise ValueError("reason required")
        with self._lock():
            candidate = self.get(evolution_id)
            state = self.state()
            if state.applied_evolution_id == evolution_id:
                return state
            if candidate.status != "awaiting_approval" or candidate.evaluation is None:
                raise ValueError("candidate not awaiting approval")
            if candidate.snapshot.binding != state.active:
                raise ValueError("baseline stale")
            expected_binding = ReportingPolicyBinding.freeze(
                ReportPolicy(gap_placement="section_and_appendix"),
                version="1.1." + str(int(evolution_id[-24:], 16) + 1),
                revision=state.active.revision + 1,
            )
            if candidate.candidate != expected_binding:
                raise ValueError("candidate binding mismatch")
            if candidate.evaluation.fingerprint != implementation_fingerprint():
                raise ValueError("implementation fingerprint changed")
            if candidate.evaluation_sha256 != digest(
                canonical(candidate.evaluation.model_dump(mode="json"))
            ):
                raise ValueError("evaluation hash mismatch")
            verified = replay(
                candidate.snapshot, target_section_ids=candidate.feedback.target_section_ids
            )
            if not verified.passed or verified.cases != candidate.evaluation.cases:
                raise ValueError("evaluation verification failed")
            state = DemoState(
                active=candidate.candidate,
                applied_evolution_id=evolution_id,
                reason=redact_text(reason),
                changed_at=datetime.now(UTC).isoformat(),
            )
            self._write(self._path("state.json"), state)
            return state

    def reset(self, *, reason: str) -> DemoState:
        if not reason.strip():
            raise ValueError("reason required")
        with self._lock():
            active = self.active()
            state = DemoState(
                active=ReportingPolicyBinding.freeze(
                    ReportPolicy(), version="1.1.0", revision=active.revision + 1
                ),
                reason=redact_text(reason),
                changed_at=datetime.now(UTC).isoformat(),
            )
            self._write(self._path("state.json"), state)
            return state
