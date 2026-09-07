"""Hash-verified, read-only loading for local enterprise scenarios."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import TypeAlias

from pydantic import ValidationError

from jindiao.application.errors import (
    EntityAmbiguousError,
    EntityNotFoundError,
    EvaluationIntegrityError,
    ScenarioIntegrityError,
)
from jindiao.contracts.entities import EnterpriseInput

from .manifest import ScenarioFileRole, ScenarioManifest

JsonScalar: TypeAlias = str | int | float | bool | None
FrozenJson: TypeAlias = JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]

_SCENARIO_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _normalize_name(value: str) -> str:
    return "".join(value.split()).casefold()


def _normalize_credit_code(value: str) -> str:
    return "".join(value.split()).upper()


def _freeze_json(value: object) -> FrozenJson:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ScenarioIntegrityError("scenario JSON object keys must be strings")
        frozen = {str(key): _freeze_json(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    raise ScenarioIntegrityError("scenario JSON contains an unsupported value")


def _safe_relative_path(path: str) -> PurePosixPath:
    pure_path = PurePosixPath(path)
    if (
        "\\" in path
        or pure_path.is_absolute()
        or ".." in pure_path.parts
        or "." in pure_path.parts
        or path != pure_path.as_posix()
    ):
        raise ScenarioIntegrityError("scenario file path is unsafe")
    return pure_path


def _read_manifest_file(root: Path, scenario_id: str) -> ScenarioManifest:
    if not _SCENARIO_ID.fullmatch(scenario_id):
        raise ScenarioIntegrityError("scenario id is invalid")
    manifest_path = root / scenario_id / "manifest.json"
    try:
        raw: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ScenarioManifest.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ScenarioIntegrityError(
            "scenario manifest is missing or invalid",
            details={"scenario_id": scenario_id},
        ) from error
    if manifest.scenario_id != scenario_id:
        raise ScenarioIntegrityError("manifest scenario id does not match its directory")
    return manifest


@dataclass(frozen=True, slots=True)
class ScenarioSnapshot:
    """A content-addressed in-memory view shared by every runtime participant."""

    scenario_snapshot_id: str
    manifest: ScenarioManifest
    _contents: Mapping[str, bytes] = field(repr=False)

    @property
    def available_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._contents))

    def read_bytes(self, path: str) -> bytes:
        pure_path = _safe_relative_path(path)
        if pure_path.parts[0] == "expected":
            raise EvaluationIntegrityError("runtime access to expected answers is forbidden")
        try:
            return self._contents[path]
        except KeyError as error:
            raise ScenarioIntegrityError(
                "scenario file is not declared for runtime access",
                details={"path": path},
            ) from error

    def read_text(self, path: str) -> str:
        try:
            return self.read_bytes(path).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ScenarioIntegrityError(
                "scenario text file is not valid UTF-8",
                details={"path": path},
            ) from error

    def read_json(self, path: str) -> Mapping[str, FrozenJson]:
        try:
            parsed: object = json.loads(self.read_text(path))
        except json.JSONDecodeError as error:
            raise ScenarioIntegrityError(
                "scenario JSON file is invalid",
                details={"path": path},
            ) from error
        frozen = _freeze_json(parsed)
        if not isinstance(frozen, Mapping):
            raise ScenarioIntegrityError(
                "scenario JSON root must be an object",
                details={"path": path},
            )
        return frozen


class ScenarioRepository:
    """Resolve enterprise keys and load validated runtime-only snapshots."""

    def __init__(self, scenarios_root: Path) -> None:
        self._root = scenarios_root.resolve()

    def resolve(
        self,
        enterprise: EnterpriseInput,
        *,
        version: str | None = None,
    ) -> ScenarioSnapshot:
        matches: list[str] = []
        if not self._root.is_dir():
            raise EntityNotFoundError("no local scenario matches the enterprise")
        for scenario_dir in sorted(self._root.iterdir()):
            if not scenario_dir.is_dir():
                continue
            manifest = _read_manifest_file(self._root, scenario_dir.name)
            if self._matches(manifest, enterprise) and (
                version is None or manifest.version == version
            ):
                matches.append(manifest.scenario_id)
        if not matches:
            raise EntityNotFoundError("no local scenario matches the enterprise")
        if len(matches) > 1:
            raise EntityAmbiguousError(
                "multiple local scenarios match the enterprise",
                details={"scenario_ids": matches},
            )
        return self.load(matches[0], enterprise, version=version)

    def load(
        self,
        scenario_id: str,
        enterprise: EnterpriseInput,
        *,
        version: str | None = None,
    ) -> ScenarioSnapshot:
        manifest = _read_manifest_file(self._root, scenario_id)
        self._validate_version(manifest, version)
        if not self._matches(manifest, enterprise):
            raise ScenarioIntegrityError("scenario enterprise key does not match the request")
        return self._load_snapshot(manifest)

    def load_template(
        self,
        scenario_id: str,
        *,
        version: str | None = None,
    ) -> ScenarioSnapshot:
        """Load an explicit, immutable supplement template for a live subject."""

        manifest = _read_manifest_file(self._root, scenario_id)
        self._validate_version(manifest, version)
        return self._load_snapshot(manifest)

    def _load_snapshot(self, manifest: ScenarioManifest) -> ScenarioSnapshot:
        scenario_dir = (self._root / manifest.scenario_id).resolve()
        contents: dict[str, bytes] = {}
        for entry in manifest.files:
            if entry.role is ScenarioFileRole.EXPECTED:
                continue
            file_path = self._resolve_declared_file(scenario_dir, entry.path)
            try:
                content = file_path.read_bytes()
            except OSError as error:
                raise ScenarioIntegrityError(
                    "declared scenario file cannot be read",
                    details={"path": entry.path},
                ) from error
            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash != entry.sha256:
                raise ScenarioIntegrityError(
                    "scenario file hash mismatch",
                    details={"path": entry.path},
                )
            contents[entry.path] = content

        fingerprint_input = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(fingerprint_input.encode()).hexdigest()
        snapshot_id = f"{manifest.scenario_id}:{manifest.version}:{fingerprint}"
        return ScenarioSnapshot(
            scenario_snapshot_id=snapshot_id,
            manifest=manifest,
            _contents=MappingProxyType(contents),
        )

    @staticmethod
    def _validate_version(manifest: ScenarioManifest, version: str | None) -> None:
        if version is not None and manifest.version != version:
            raise ScenarioIntegrityError(
                "scenario version does not match the requested version",
                details={"requested_version": version},
            )

    @staticmethod
    def _matches(manifest: ScenarioManifest, enterprise: EnterpriseInput) -> bool:
        key = manifest.enterprise_key
        if enterprise.company_name is not None:
            names = tuple(value for value in (key.company_name, *key.aliases) if value)
            if _normalize_name(enterprise.company_name) not in {
                _normalize_name(value) for value in names
            }:
                return False
        if enterprise.unified_social_credit_code is not None:
            if key.unified_social_credit_code is None or _normalize_credit_code(
                enterprise.unified_social_credit_code
            ) != _normalize_credit_code(key.unified_social_credit_code):
                return False
        return True

    @staticmethod
    def _resolve_declared_file(scenario_dir: Path, relative_path: str) -> Path:
        pure_path = _safe_relative_path(relative_path)
        file_path = (scenario_dir / Path(*pure_path.parts)).resolve()
        try:
            file_path.relative_to(scenario_dir)
        except ValueError as error:
            raise ScenarioIntegrityError("scenario file escapes its directory") from error
        return file_path


__all__ = ["FrozenJson", "JsonScalar", "ScenarioRepository", "ScenarioSnapshot"]
