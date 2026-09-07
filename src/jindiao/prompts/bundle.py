"""Load immutable prompt assets without mixing runtime facts into System text."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.results import AgentResultPhase
from jindiao.investigation.catalog import CHECK_CATALOG

DEFAULT_PROMPT_BUNDLE_PATH = Path(__file__).resolve().parent / "bundle_v1"


class PromptFileSpec(ContractModel):
    version: str = Field(min_length=1)
    path: str = Field(min_length=1)
    required_fragments: tuple[str, ...] = Field(min_length=1)


class InvestigationManifest(ContractModel):
    core: PromptFileSpec
    roles: dict[str, PromptFileSpec]


class PromptBundleManifest(ContractModel):
    schema_version: Literal[1]
    bundle_version: str = Field(min_length=1)
    acquisition: dict[str, PromptFileSpec]
    investigation: InvestigationManifest
    checks: dict[str, PromptFileSpec]


class PromptArtifact(ContractModel):
    prompt_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    system_prompt: str = Field(min_length=1)


class InvestigationPrompt(ContractModel):
    role: str = Field(min_length=1)
    core_version: str = Field(min_length=1)
    core_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role_version: str = Field(min_length=1)
    role_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    system_prompt: str = Field(min_length=1)


class PromptInvocation(ContractModel):
    phase: AgentResultPhase
    role: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    system_prompt: str = Field(min_length=1)
    user_payload_json: str = Field(min_length=2)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class PromptBundle:
    """Validated static prompt set with explicit role composition."""

    def __init__(self, *, root: Path, manifest: PromptBundleManifest) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self._acquisition = {
            role: self._load_artifact(f"acquisition/{role}", spec)
            for role, spec in manifest.acquisition.items()
        }
        self._core = self._load_artifact("investigation/core", manifest.investigation.core)
        self._roles = {
            role: self._load_artifact(f"investigation/roles/{role}", spec)
            for role, spec in manifest.investigation.roles.items()
        }
        self._checks = {
            prompt_id: self._load_artifact(prompt_id, spec)
            for prompt_id, spec in manifest.checks.items()
        }
        expected_check_prompts = {check.prompt_template_id for check in CHECK_CATALOG.checks}
        actual_check_prompts = set(self._checks)
        if actual_check_prompts != expected_check_prompts:
            missing = sorted(expected_check_prompts - actual_check_prompts)
            extra = sorted(actual_check_prompts - expected_check_prompts)
            raise ValueError(
                f"prompt bundle check references mismatch; missing={missing}, extra={extra}"
            )

    @property
    def check_prompt_ids(self) -> tuple[str, ...]:
        return tuple(self._checks)

    def acquisition(self, role: str) -> PromptArtifact:
        try:
            return self._acquisition[role]
        except KeyError as error:
            raise KeyError(f"unknown acquisition prompt role: {role}") from error

    def investigation(self, role: str) -> InvestigationPrompt:
        try:
            role_prompt = self._roles[role]
        except KeyError as error:
            raise KeyError(f"unknown investigation prompt role: {role}") from error
        system_prompt = f"{self._core.system_prompt}\n\n{role_prompt.system_prompt}"
        return InvestigationPrompt(
            role=role,
            core_version=self._core.version,
            core_sha256=self._core.sha256,
            role_version=role_prompt.version,
            role_sha256=role_prompt.sha256,
            prompt_version=f"{self._core.version}+{role_prompt.version}",
            prompt_sha256=_sha256(system_prompt),
            system_prompt=system_prompt,
        )

    def check_prompt(self, prompt_id: str) -> PromptArtifact:
        try:
            return self._checks[prompt_id]
        except KeyError as error:
            raise KeyError(f"unknown check prompt: {prompt_id}") from error

    def build_invocation(
        self,
        *,
        phase: AgentResultPhase,
        role: str,
        runtime_data: dict[str, object],
    ) -> PromptInvocation:
        if phase is AgentResultPhase.ACQUISITION:
            acquisition_prompt = self.acquisition(role)
            prompt_version = acquisition_prompt.version
            prompt_sha256 = acquisition_prompt.sha256
            system_prompt = acquisition_prompt.system_prompt
        else:
            investigation_prompt = self.investigation(role)
            prompt_version = investigation_prompt.prompt_version
            prompt_sha256 = investigation_prompt.prompt_sha256
            system_prompt = investigation_prompt.system_prompt
        return PromptInvocation(
            phase=phase,
            role=role,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            system_prompt=system_prompt,
            user_payload_json=json.dumps(
                runtime_data,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def _load_artifact(self, prompt_id: str, spec: PromptFileSpec) -> PromptArtifact:
        path = (self.root / spec.path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"prompt path escapes bundle root: {spec.path}")
        if not path.is_file():
            raise ValueError(f"prompt file does not exist: {spec.path}")
        content = path.read_text(encoding="utf-8").strip()
        missing = [fragment for fragment in spec.required_fragments if fragment not in content]
        if missing:
            raise ValueError(f"{prompt_id} missing required prompt fragments: {missing}")
        return PromptArtifact(
            prompt_id=prompt_id,
            version=spec.version,
            sha256=_sha256(content),
            system_prompt=content,
        )


def load_prompt_bundle(path: Path = DEFAULT_PROMPT_BUNDLE_PATH) -> PromptBundle:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"prompt bundle manifest does not exist: {manifest_path}")
    manifest = PromptBundleManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    return PromptBundle(root=path, manifest=manifest)


__all__ = [
    "DEFAULT_PROMPT_BUNDLE_PATH",
    "InvestigationPrompt",
    "PromptArtifact",
    "PromptBundle",
    "PromptInvocation",
    "load_prompt_bundle",
]
