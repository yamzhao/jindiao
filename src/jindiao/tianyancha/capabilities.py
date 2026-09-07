"""Company capability discovery, caching, and configured domain routing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject

from .client import McpCallResult
from .entity import McpToolCaller


class CompanyCapability(ContractModel):
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)


class CompanyCapabilityManifest(ContractModel):
    subject_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    fetched_at: AwareDatetime
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tools: tuple[CompanyCapability, ...]

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)


class ReportSubmoduleRoute(ContractModel):
    """Stable report destination and ordered Tianyancha candidates for one submodule."""

    submodule_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    preferred_tool_names: tuple[str, ...] = Field(min_length=1)
    mock_keys: tuple[str, ...] = ()


class DomainCapabilityRoute(ContractModel):
    preferred_tool_names: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    submodules: tuple[ReportSubmoduleRoute, ...] = ()

    @model_validator(mode="after")
    def require_selector(self) -> DomainCapabilityRoute:
        if not self.preferred_tool_names and not self.keywords and not self.submodules:
            raise ValueError("domain capability route requires names or keywords")
        identifiers = [item.submodule_id for item in self.submodules]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("domain capability route contains duplicate submodule ids")
        return self


class CapabilityRoutingConfig(ContractModel):
    schema_version: Literal[1]
    routes: dict[str, DomainCapabilityRoute]

    @classmethod
    def from_file(cls, path: Path) -> CapabilityRoutingConfig:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def select(
        self,
        manifest: CompanyCapabilityManifest,
        domain: str,
    ) -> tuple[CompanyCapability, ...]:
        route = self.routes.get(domain)
        if route is None:
            return ()
        actual = {tool.name: tool for tool in manifest.tools}
        selected: list[CompanyCapability] = []
        for _, tool in self.select_submodules(manifest, domain):
            if tool is not None and tool not in selected:
                selected.append(tool)
        for name in route.preferred_tool_names:
            if name in actual and actual[name] not in selected:
                selected.append(actual[name])
        normalized_keywords = tuple(keyword.casefold() for keyword in route.keywords)
        for tool in manifest.tools:
            haystack = f"{tool.name} {tool.description}".casefold()
            if any(keyword in haystack for keyword in normalized_keywords) and tool not in selected:
                selected.append(tool)
        return tuple(selected)

    def select_submodules(
        self,
        manifest: CompanyCapabilityManifest,
        domain: str,
    ) -> tuple[tuple[ReportSubmoduleRoute, CompanyCapability | None], ...]:
        """Resolve one preferred available capability while preserving absent modules."""

        route = self.routes.get(domain)
        if route is None:
            return ()
        actual = {tool.name: tool for tool in manifest.tools}
        return tuple(
            (
                submodule,
                next(
                    (actual[name] for name in submodule.preferred_tool_names if name in actual),
                    None,
                ),
            )
            for submodule in route.submodules
        )


def _find_tool_records(value: object) -> list[Mapping[str, object]] | None:
    if not isinstance(value, Mapping):
        return None
    for collection_key in ("tools", "items", "capabilities"):
        items = value.get(collection_key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, Mapping)]
    for object_key in ("result", "data"):
        found = _find_tool_records(value.get(object_key))
        if found is not None:
            return found
    return None


def _markdown_tool_records(text: str) -> list[dict[str, object]]:
    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    for index in range(len(rows) - 2):
        headers = [cell.strip() for cell in rows[index].strip("|").split("|")]
        separators = [cell.strip() for cell in rows[index + 1].strip("|").split("|")]
        if "tool_name" not in headers or len(headers) != len(separators):
            continue
        if not all(set(cell) <= {"-", ":"} for cell in separators):
            continue
        records: list[dict[str, object]] = []
        for row in rows[index + 2 :]:
            cells = [cell.strip().strip("`") for cell in row.strip("|").split("|")]
            if len(cells) != len(headers):
                break
            records.append(dict(zip(headers, cells, strict=True)))
        return records
    return []


def _record_to_capability(record: Mapping[str, object]) -> CompanyCapability | None:
    raw_name = record.get("tool_name", record.get("name"))
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    normalized_name = raw_name.strip()
    if normalized_name == "tool_name" or set(normalized_name) <= {"-", ":"}:
        return None
    raw_description = record.get("description", record.get("能力说明", ""))
    description = str(raw_description) if raw_description is not None else ""
    input_schema = record.get("input_schema", record.get("inputSchema", {}))
    if not isinstance(input_schema, dict):
        input_schema = {}
    return CompanyCapability.model_validate(
        {
            "name": normalized_name,
            "description": description.strip(),
            "input_schema": input_schema,
        }
    )


class CompanyCapabilityService:
    """Fetch and cache the per-company internal-tool manifest."""

    def __init__(
        self,
        client: McpToolCaller,
        *,
        ttl: timedelta,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("capability cache TTL must be positive")
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, CompanyCapabilityManifest] = {}

    async def get(self, subject: ResolvedSubject) -> CompanyCapabilityManifest:
        now = self._clock()
        cached = self._cache.get(subject.subject_id)
        if cached is not None and now - cached.fetched_at < self._ttl:
            return cached
        company_id = subject.subject_id.removeprefix("tyc:")
        result = await self._client.call_tool(
            "get_company_capabilities",
            {"company_id": company_id, "company_name": subject.company_name},
        )
        manifest = self.parse_result(subject, result, fetched_at=now)
        self._cache[subject.subject_id] = manifest
        return manifest

    @staticmethod
    def parse_result(
        subject: ResolvedSubject,
        result: McpCallResult,
        *,
        fetched_at: datetime,
    ) -> CompanyCapabilityManifest:
        records = _find_tool_records(result.structured_content)
        if records is None:
            records = []
            for text in result.text:
                records.extend(_markdown_tool_records(text))
        by_name: dict[str, CompanyCapability] = {}
        for record in records:
            capability = _record_to_capability(record)
            if capability is not None:
                by_name[capability.name] = capability
        tools = tuple(sorted(by_name.values(), key=lambda tool: tool.name))
        fingerprint_data = [tool.model_dump(mode="json") for tool in tools]
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_data,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return CompanyCapabilityManifest(
            subject_id=subject.subject_id,
            company_name=subject.company_name,
            fetched_at=fetched_at,
            fingerprint=fingerprint,
            tools=tools,
        )


__all__ = [
    "CapabilityRoutingConfig",
    "CompanyCapability",
    "CompanyCapabilityManifest",
    "CompanyCapabilityService",
    "DomainCapabilityRoute",
    "ReportSubmoduleRoute",
]
