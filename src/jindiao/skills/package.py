"""Repository-independent loading and contract validation for packaged Skills."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_REQUIRED_FILES = ("SKILL.md", "VERSION", "CHANGELOG.md", "mount.json")


def _safe_resource(root: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise ValueError("skill mount resource must be a string")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or value != relative.as_posix():
        raise ValueError("skill mount resource must be a safe relative path")
    target = (root / Path(*relative.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("skill mount resource escapes package root") from error
    if not target.is_file():
        raise ValueError(f"skill resource does not exist: {value}")
    return target


def _frontmatter(skill_md: str) -> dict[str, str]:
    if not skill_md.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    parts = skill_md.split("---", 2)
    if len(parts) != 3:
        raise ValueError("SKILL.md frontmatter is not closed")
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip().strip('"')
    if not fields.get("name") or not fields.get("description"):
        raise ValueError("SKILL.md requires name and description")
    return fields


@dataclass(frozen=True, slots=True)
class SkillPackage:
    root: Path
    name: str
    description: str
    version: str
    kind: Literal["agent", "team"]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    evaluation_case_count: int

    @classmethod
    def load(cls, root: Path) -> SkillPackage:
        root = root.resolve()
        for relative in _REQUIRED_FILES:
            if not (root / relative).is_file():
                raise ValueError(f"skill package is missing {relative}")
        frontmatter = _frontmatter((root / "SKILL.md").read_text(encoding="utf-8"))
        if frontmatter["name"] != root.name:
            raise ValueError("skill name must match its directory")
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        if not _SEMVER.fullmatch(version):
            raise ValueError("skill VERSION must be semantic versioning")
        mount_value: object = json.loads((root / "mount.json").read_text(encoding="utf-8"))
        if not isinstance(mount_value, dict) or mount_value.get("schema_version") != 1:
            raise ValueError("skill mount.json is invalid")
        kind = mount_value.get("kind")
        if kind not in {"agent", "team"}:
            raise ValueError("skill kind must be agent or team")
        entrypoint = _safe_resource(root, mount_value.get("entrypoint"))
        if entrypoint != root / "SKILL.md":
            raise ValueError("skill entrypoint must be SKILL.md")
        input_path = _safe_resource(root, mount_value.get("input_schema"))
        output_path = _safe_resource(root, mount_value.get("output_schema"))
        input_schema = json.loads(input_path.read_text(encoding="utf-8"))
        output_schema = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise ValueError("skill schemas must be JSON objects")
        Draft202012Validator.check_schema(input_schema)
        Draft202012Validator.check_schema(output_schema)
        evals_value: object = json.loads(
            (root / "references/evals.json").read_text(encoding="utf-8")
        )
        if not isinstance(evals_value, dict) or not isinstance(evals_value.get("cases"), list):
            raise ValueError("skill evaluation set is invalid")
        return cls(
            root=root,
            name=frontmatter["name"],
            description=frontmatter["description"],
            version=version,
            kind=cast(Literal["agent", "team"], kind),
            input_schema=cast(dict[str, Any], input_schema),
            output_schema=cast(dict[str, Any], output_schema),
            evaluation_case_count=len(evals_value["cases"]),
        )

    def validate_input(self, value: object) -> None:
        Draft202012Validator(self.input_schema).validate(value)

    def validate_output(self, value: object) -> None:
        Draft202012Validator(self.output_schema).validate(value)

    def validate_example(self) -> None:
        input_value: object = json.loads(
            (self.root / "references/example.input.json").read_text(encoding="utf-8")
        )
        output_value: object = json.loads(
            (self.root / "references/example.output.json").read_text(encoding="utf-8")
        )
        self.validate_input(input_value)
        self.validate_output(output_value)


__all__ = ["SkillPackage"]
