"""Run artifact persistence for replay, inspection, and benchmarking."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import JsonValue

from jindiao.contracts.report_policy import ReportingPolicyBinding
from jindiao.contracts.reporting import ReportViewModel
from jindiao.contracts.results import DueDiligenceResult
from jindiao.reporting.replay import MAX_SNAPSHOT_BYTES, ReplaySnapshot

from .trace import JsonlRunTrace, redact_json, redact_text


def _safe_segment(value: str, label: str) -> str:
    if not value or PurePosixPath(value).name != value or value in {".", ".."}:
        raise ValueError(f"{label} must be one safe path segment")
    return value


class RunArtifactStore:
    """Own the complete on-disk public artifact set for each run."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root = root.resolve()
        self._clock = clock

    def begin(self, *, request_id: str, run_id: str) -> JsonlRunTrace:
        _safe_segment(request_id, "request_id")
        run_id = _safe_segment(run_id, "run_id")
        return JsonlRunTrace(
            path=self.root / run_id / "trace.jsonl",
            request_id=request_id,
            run_id=run_id,
            clock=self._clock,
        )

    def complete(
        self,
        result: DueDiligenceResult,
        *,
        metrics: Mapping[str, JsonValue],
        replay_view: ReportViewModel | None = None,
        binding: ReportingPolicyBinding | None = None,
    ) -> DueDiligenceResult:
        run_root = self._run_root(result.meta.run_id)
        if replay_view is not None and binding is not None:
            try:
                snapshot = ReplaySnapshot.freeze(
                    run_id=result.meta.run_id,
                    view=replay_view,
                    binding=binding,
                    report=result.report_markdown,
                )
                self._write_json(run_root / "report-replay.json", snapshot.model_dump(mode="json"))
                snapshot_path = run_root / "report-replay.json"
                if snapshot_path.stat().st_size > MAX_SNAPSHOT_BYTES:
                    raise ValueError("serialized snapshot too large")
                if ReplaySnapshot.model_validate_json(snapshot_path.read_text()) != snapshot:
                    raise ValueError("serialized snapshot changed")
                result = result.model_copy(
                    update={
                        "meta": result.meta.model_copy(
                            update={"report_replay_available": True, "report_replay_reason": None}
                        )
                    }
                )
            except (ValueError, OSError):
                result = result.model_copy(
                    update={
                        "meta": result.meta.model_copy(
                            update={
                                "report_replay_available": False,
                                "report_replay_reason": "snapshot_unavailable",
                            }
                        )
                    }
                )
                (run_root / "report-replay.json.tmp").unlink(missing_ok=True)
                (run_root / "report-replay.json").unlink(missing_ok=True)
        self._write_json(run_root / "result.json", result.model_dump(mode="json"))
        self._write_json(run_root / "metrics.json", dict(metrics))
        self._write_text(run_root / "report.md", redact_text(result.report_markdown))
        self._write_manifest(run_root)
        return result

    def load_replay(self, run_id: str) -> ReplaySnapshot | None:
        root = self.root / _safe_segment(run_id, "run_id")
        if root.is_symlink() or any(path.is_symlink() for path in root.parents):
            return None
        if not root.is_dir() or any(path.is_symlink() for path in root.iterdir()):
            return None
        if not self.verify_manifest(run_id):
            return None
        path = root / "report-replay.json"
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                return None
            snapshot = ReplaySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            if snapshot.run_id != run_id or (root / "report.md").read_text() != snapshot.report:
                return None
            return snapshot
        except (OSError, ValueError):
            return None

    def fail(
        self,
        *,
        run_id: str,
        error_code: str,
        message: str,
        metrics: Mapping[str, JsonValue],
    ) -> None:
        run_root = self._run_root(run_id)
        self._write_json(
            run_root / "error.json",
            {"error_code": error_code, "message": redact_text(message)},
        )
        self._write_json(run_root / "metrics.json", dict(metrics))
        self._write_manifest(run_root)

    def verify_manifest(self, run_id: str) -> bool:
        """Validate every persisted public artifact against its manifest."""

        run_root = self.root / _safe_segment(run_id, "run_id")
        manifest_path = run_root / "manifest.json"
        if not manifest_path.is_file():
            return False
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = raw.get("files") if isinstance(raw, dict) else None
            if not isinstance(files, dict):
                return False
            for name, expected in files.items():
                if not isinstance(name, str) or not isinstance(expected, str):
                    return False
                path = run_root / _safe_segment(name, "manifest file")
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    return False
            actual = {
                path.name
                for path in run_root.iterdir()
                if path.is_file() and path.name != "manifest.json"
            }
            return actual == set(files)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

    def load_result(self, run_id: str) -> DueDiligenceResult | None:
        """Read a result only after manifest/hash validation succeeds."""

        if not self.verify_manifest(run_id):
            return None
        path = self.root / _safe_segment(run_id, "run_id") / "result.json"
        if not path.is_file():
            return None
        return DueDiligenceResult.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _run_root(self, run_id: str) -> Path:
        run_root = self.root / _safe_segment(run_id, "run_id")
        run_root.mkdir(parents=True, exist_ok=True)
        return run_root

    @staticmethod
    def _write_manifest(run_root: Path) -> None:
        files: dict[str, str] = {}
        for path in sorted(run_root.iterdir()):
            if path.name == "manifest.json" or path.name.endswith(".tmp") or not path.is_file():
                continue
            files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        RunArtifactStore._write_json(
            run_root / "manifest.json",
            {"version": 1, "files": files},
        )

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        safe_value = redact_json(value)
        RunArtifactStore._write_text(
            path,
            json.dumps(safe_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)


LocalRunStore = RunArtifactStore


class AgentArtsSessionStore(RunArtifactStore):
    """File-backed adapter for a mounted AgentArts session/SFS directory.

    The adapter is deliberately explicit: callers must provide a shared mount and
    opt into it; it never silently turns a container-local path into a detached
    durability guarantee.
    """

    def __init__(
        self,
        root: Path,
        *,
        shared: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not shared:
            raise ValueError("AgentArts session store requires an explicitly shared root")
        super().__init__(root, clock=clock)


__all__ = ["AgentArtsSessionStore", "LocalRunStore", "RunArtifactStore"]
