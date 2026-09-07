"""Benchmark-only loading of expected scenario answers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from jindiao.application.errors import EvaluationIntegrityError, ScenarioIntegrityError

from .manifest import ScenarioFileRole
from .repository import FrozenJson, _freeze_json, _read_manifest_file


@dataclass(frozen=True, slots=True)
class ExpectedResultSnapshot:
    """Read-only expected answers kept outside the application run context."""

    scenario_id: str
    version: str
    _contents: Mapping[str, bytes] = field(repr=False)

    @property
    def available_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._contents))

    def read_json(self, path: str) -> FrozenJson:
        try:
            content = self._contents[path]
        except KeyError as error:
            raise EvaluationIntegrityError(
                "expected file is not declared for evaluator access",
                details={"path": path},
            ) from error
        try:
            parsed: object = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvaluationIntegrityError(
                "expected JSON file is invalid",
                details={"path": path},
            ) from error
        return _freeze_json(parsed)


class ExpectedResultLoader:
    """Load hash-verified gold answers only after the application run completes."""

    def __init__(self, scenarios_root: Path) -> None:
        self._root = scenarios_root.resolve()

    def load(
        self,
        scenario_id: str,
        *,
        version: str | None = None,
    ) -> ExpectedResultSnapshot:
        try:
            manifest = _read_manifest_file(self._root, scenario_id)
        except ScenarioIntegrityError as error:
            raise EvaluationIntegrityError(
                "expected manifest is missing or invalid",
                details={"scenario_id": scenario_id},
            ) from error
        if version is not None and manifest.version != version:
            raise EvaluationIntegrityError("expected result version does not match")

        contents: dict[str, bytes] = {}
        scenario_dir = (self._root / scenario_id).resolve()
        for entry in manifest.files:
            if entry.role is not ScenarioFileRole.EXPECTED:
                continue
            file_path = (scenario_dir / Path(*entry.path.split("/"))).resolve()
            try:
                file_path.relative_to(scenario_dir)
                content = file_path.read_bytes()
            except (OSError, ValueError) as error:
                raise EvaluationIntegrityError(
                    "declared expected file cannot be read",
                    details={"path": entry.path},
                ) from error
            if hashlib.sha256(content).hexdigest() != entry.sha256:
                raise EvaluationIntegrityError(
                    "expected file hash mismatch",
                    details={"path": entry.path},
                )
            contents[entry.path] = content
        if not contents:
            raise EvaluationIntegrityError("scenario declares no expected result files")
        return ExpectedResultSnapshot(
            scenario_id=manifest.scenario_id,
            version=manifest.version,
            _contents=MappingProxyType(contents),
        )


__all__ = ["ExpectedResultLoader", "ExpectedResultSnapshot"]
