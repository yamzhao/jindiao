"""Run-bound, evidence-only access to Tianyancha MCP business capabilities."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import (
    EvidenceProvenance,
    SubmoduleAvailability,
    SubmoduleContext,
)
from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    Evidence,
    SourceStatus,
)

from .capabilities import (
    CapabilityRoutingConfig,
    CompanyCapabilityManifest,
    CompanyCapabilityService,
    ReportSubmoduleRoute,
)
from .client import McpCallResult, TianyanchaMcpError
from .entity import McpToolCaller, TianyanchaEntityResolver
from .normalizer import EvidenceDomain, TianyanchaEvidenceNormalizer
from .redaction import redact_sensitive

if TYPE_CHECKING:
    from jindiao.orchestration.base import CancellationToken


def _check_cancellation(token: object | None) -> None:
    if token is None:
        return
    raise_if_cancelled = getattr(token, "raise_if_cancelled", None)
    if callable(raise_if_cancelled):
        raise_if_cancelled()
    elif bool(getattr(token, "cancelled", False)):
        raise asyncio.CancelledError()


ENTERPRISE_CONTEXT_AGENT_ID = "enterprise-context-agent"

_NO_PAGINATION = frozenset(
    {
        "get_company_registration_info",
        "get_registration_snapshot",
        "get_risk_overview",
        "get_risk_detail",
        "get_competitors",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def _sha256(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode()).hexdigest()}"


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return str(value)


class GatewayBudget(ContractModel):
    max_mcp_calls: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    max_pages: int = Field(default=5, ge=1, le=100)
    page_size: int = Field(default=20, ge=1, le=100)


class GatewayInvocation(ContractModel):
    invocation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    mcp_tool_name: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    arguments_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    source_status: SourceStatus
    called_at: AwareDatetime


class GatewaySubmoduleObservation(ContractModel):
    submodule_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    capability: str | None = None
    source_status: SourceStatus
    availability: SubmoduleAvailability
    completeness: CoverageCompleteness
    gap_reasons: tuple[CoverageGapReason, ...] = ()
    facts: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    invocation_ids: tuple[str, ...] = ()
    error: str | None = None

    @model_validator(mode="after")
    def validate_source_shape(self) -> GatewaySubmoduleObservation:
        if self.availability is SubmoduleAvailability.AVAILABLE and not self.evidence:
            raise ValueError("available gateway observation requires Evidence")
        if self.source_status is SourceStatus.VERIFIED_EMPTY and self.evidence:
            raise ValueError("verified_empty gateway observation cannot contain Evidence")
        if self.completeness is CoverageCompleteness.PARTIAL and not self.gap_reasons:
            raise ValueError("partial gateway observation requires gap reasons")
        return self

    def to_submodule_context(self) -> SubmoduleContext:
        return SubmoduleContext(
            submodule_id=self.submodule_id,
            availability=self.availability,
            completeness=self.completeness,
            facts=self.facts,
            evidence_ids=tuple(item.evidence_id for item in self.evidence),
            provenance=tuple(
                EvidenceProvenance(
                    evidence_id=item.evidence_id,
                    source_type=item.source_type,
                    source_status=item.source_status,
                    source_tool=item.source_tool,
                    source_record_id=item.source_record_id,
                    content_hash=item.content_hash,
                    source_chain=item.source_chain,
                )
                for item in self.evidence
            ),
            unresolved_gap_ids=(
                (f"gap:{self.submodule_id}:{self.source_status.value}",)
                if self.availability
                in {
                    SubmoduleAvailability.CAPABILITY_ABSENT,
                    SubmoduleAvailability.SOURCE_ERROR,
                    SubmoduleAvailability.NOT_REQUESTED,
                }
                or self.completeness is not CoverageCompleteness.COMPLETE
                else ()
            ),
        )


class _GatewayCaller(McpToolCaller):
    def __init__(self, gateway: TianyanchaMcpGateway) -> None:
        self._gateway = gateway

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        result, _ = await self._gateway._call_mcp(
            mcp_tool_name=name,
            capability=name,
            arguments=arguments,
            subject_id=(
                self._gateway.subject.subject_id
                if self._gateway.subject is not None
                else "unresolved"
            ),
            page=None,
        )
        return result


class TianyanchaMcpGateway:
    """Authorize and normalize MCP calls without producing Findings or risks."""

    def __init__(
        self,
        *,
        client: McpToolCaller,
        routing: CapabilityRoutingConfig,
        run_id: str,
        agent_id: str,
        report_as_of: date,
        budget: GatewayBudget,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not run_id.strip():
            raise ValueError("gateway run_id must not be empty")
        if agent_id != ENTERPRISE_CONTEXT_AGENT_ID:
            raise AgentExecutionError(
                "only enterprise-context-agent may access Tianyancha MCP",
                details={"agent_id": agent_id, "run_id": run_id},
            )
        route_index: dict[str, tuple[str, ReportSubmoduleRoute]] = {}
        for domain, domain_route in routing.routes.items():
            for route in domain_route.submodules:
                if route.submodule_id in route_index:
                    raise ValueError(f"duplicate routed submodule: {route.submodule_id}")
                route_index[route.submodule_id] = (domain, route)
        missing_routes = set(ACQUISITION_CATALOG.default_plan_ids) - set(route_index)
        if missing_routes:
            raise ValueError(
                f"Tianyancha routing misses acquisition items: {sorted(missing_routes)}"
            )

        self._client = client
        self._routing = routing
        self._route_index = route_index
        self.run_id = run_id
        self.agent_id = agent_id
        self.report_as_of = report_as_of
        self.budget = budget
        self._clock = clock
        self._subject: ResolvedSubject | None = None
        self._manifest: CompanyCapabilityManifest | None = None
        self._enterprise: EnterpriseInput | None = None
        self._mcp_calls = 0
        self._budget_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(budget.max_concurrency)
        self._initialize_lock = asyncio.Lock()
        self._initialize_task: (
            asyncio.Task[tuple[ResolvedSubject, CompanyCapabilityManifest]] | None
        ) = None
        self._cache_lock = asyncio.Lock()
        self._cache: dict[tuple[str, str], GatewaySubmoduleObservation] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[GatewaySubmoduleObservation]] = {}
        self._invocations: list[GatewayInvocation] = []
        self._cancellation_token: CancellationToken | None = None

    def set_cancellation_token(self, token: CancellationToken | None) -> None:
        """Bind the current Run cancellation token to all future MCP calls."""

        self._cancellation_token = token

    @property
    def subject(self) -> ResolvedSubject | None:
        return self._subject

    @property
    def manifest(self) -> CompanyCapabilityManifest | None:
        return self._manifest

    @property
    def invocations(self) -> tuple[GatewayInvocation, ...]:
        return tuple(self._invocations)

    @property
    def mcp_calls(self) -> int:
        return self._mcp_calls

    async def initialize(
        self,
        enterprise: EnterpriseInput,
    ) -> tuple[ResolvedSubject, CompanyCapabilityManifest]:
        _check_cancellation(self._cancellation_token)
        async with self._initialize_lock:
            if self._enterprise is not None and self._enterprise != enterprise:
                raise AgentExecutionError(
                    "gateway is already bound to another enterprise subject",
                    details={"run_id": self.run_id},
                )
            self._enterprise = enterprise
            if self._initialize_task is None:
                self._initialize_task = asyncio.create_task(self._initialize(enterprise))
            task = self._initialize_task
        return await task

    async def _initialize(
        self,
        enterprise: EnterpriseInput,
    ) -> tuple[ResolvedSubject, CompanyCapabilityManifest]:
        caller = _GatewayCaller(self)
        subject = await TianyanchaEntityResolver(caller, clock=self._clock).resolve(enterprise)
        self._subject = subject
        manifest = await CompanyCapabilityService(
            caller,
            ttl=timedelta(minutes=5),
            clock=self._clock,
        ).get(subject)
        if manifest.subject_id != subject.subject_id:
            raise AgentExecutionError("capability manifest subject does not match resolved subject")
        self._manifest = manifest
        return subject, manifest

    async def acquire_submodule(
        self,
        submodule_id: str,
        *,
        subject_id: str,
        capability: str | None = None,
    ) -> GatewaySubmoduleObservation:
        _check_cancellation(self._cancellation_token)
        subject, manifest = self._require_initialized()
        if subject_id != subject.subject_id:
            raise AgentExecutionError(
                "gateway request subject does not match its bound subject",
                details={"requested_subject_id": subject_id},
            )
        try:
            domain, route = self._route_index[submodule_id]
        except KeyError as error:
            raise AgentExecutionError(
                "gateway request targets an unknown report submodule",
                details={"submodule_id": submodule_id},
            ) from error

        allowed_for_route = tuple(
            name for name in route.preferred_tool_names if name in manifest.tool_names
        )
        selected = capability or (allowed_for_route[0] if allowed_for_route else None)
        if selected is None:
            return self._absent_observation(route)
        if selected not in manifest.tool_names or selected not in route.preferred_tool_names:
            raise AgentExecutionError(
                "gateway capability is not declared for this subject and submodule",
                details={"capability": selected, "submodule_id": submodule_id},
            )

        cache_key = (domain, selected)
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return self._for_route(cached, route)
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._query_capability(
                        domain=domain,
                        route=route,
                        capability=selected,
                        subject=subject,
                    )
                )
                self._inflight[cache_key] = task
        try:
            observed = await task
        finally:
            if task.done():
                async with self._cache_lock:
                    self._inflight.pop(cache_key, None)
        async with self._cache_lock:
            self._cache.setdefault(cache_key, observed)
        return self._for_route(observed, route)

    def _require_initialized(self) -> tuple[ResolvedSubject, CompanyCapabilityManifest]:
        if self._subject is None or self._manifest is None:
            raise AgentExecutionError("gateway must resolve subject and capabilities first")
        return self._subject, self._manifest

    async def _query_capability(
        self,
        *,
        domain: str,
        route: ReportSubmoduleRoute,
        capability: str,
        subject: ResolvedSubject,
    ) -> GatewaySubmoduleObservation:
        evidence: list[Evidence] = []
        invocation_ids: list[str] = []
        gap_reason_set: set[CoverageGapReason] = set()
        record_count = 0
        source_metadata: dict[str, JsonValue] = {}
        page_numbers = (
            (None,) if capability in _NO_PAGINATION else tuple(range(1, self.budget.max_pages + 1))
        )
        try:
            for page in page_numbers:
                business_arguments: dict[str, int] = {}
                if page is not None:
                    business_arguments = {"page": page, "page_size": self.budget.page_size}
                wrapper = {
                    "tool_name": capability,
                    "company_name": subject.company_name,
                    "arguments": business_arguments,
                }
                result, invocation = await self._call_mcp(
                    mcp_tool_name="call_tool",
                    capability=capability,
                    arguments=wrapper,
                    subject_id=subject.subject_id,
                    page=page,
                )
                invocation_ids.append(invocation.invocation_id)
                batch = TianyanchaEvidenceNormalizer().normalize(
                    domain=EvidenceDomain(domain),
                    subject=subject,
                    tool_name=capability,
                    result=result,
                    queried_at=invocation.called_at,
                    as_of_date=self.report_as_of,
                    raw_snapshot_ref=(f"mcp://tianyancha/{capability}/{invocation.invocation_id}"),
                    source_parameters_hash=invocation.arguments_sha256,
                )
                known_ids = {item.evidence_id for item in evidence}
                for item in batch.evidence:
                    if item.evidence_id not in known_ids:
                        evidence.append(item)
                        known_ids.add(item.evidence_id)
                record_count += batch.record_count
                source_metadata = {
                    "available_years": list(batch.available_years),
                    "selected_years": list(batch.selected_years),
                    "links": list(batch.links),
                    "statement_scope": batch.statement_scope,
                    "amount_multiplier": batch.amount_multiplier,
                    "partial_reasons": list(batch.partial_reasons),
                }
                if batch.pagination is not None:
                    source_metadata["total_count"] = batch.pagination.total_count
                if "missing_period" in batch.partial_reasons:
                    gap_reason_set.add(CoverageGapReason.MISSING_PERIOD)
                if set(batch.partial_reasons) & {"year_directory_only", "link_only", "count_only"}:
                    gap_reason_set.add(CoverageGapReason.MISSING_FIELDS)
                if "count_only" in batch.partial_reasons:
                    # Another page cannot repair a response with no detail rows.
                    # Keep the explicit gap instead of spending on blind retries.
                    break
                if page is None or batch.pagination is None:
                    break
                more = batch.pagination.is_truncated(returned_count=len(evidence))
                if not more:
                    break
                if page == self.budget.max_pages:
                    gap_reason_set.add(CoverageGapReason.PAGINATION_TRUNCATED)
        except TianyanchaMcpError as error:
            return GatewaySubmoduleObservation(
                submodule_id=route.submodule_id,
                module_id=route.section_id,
                capability=capability,
                source_status=SourceStatus.SOURCE_ERROR,
                availability=SubmoduleAvailability.SOURCE_ERROR,
                completeness=CoverageCompleteness.UNKNOWN,
                gap_reasons=(CoverageGapReason.SOURCE_UNAVAILABLE,),
                facts={
                    "record_count": record_count,
                    "records": [_json_value(item.value) for item in evidence],
                    "source_metadata": source_metadata,
                },
                evidence=tuple(evidence),
                invocation_ids=tuple(invocation_ids),
                error=str(redact_sensitive(error.message)),
            )

        gap_reasons = tuple(sorted(gap_reason_set, key=str))
        status = (
            SourceStatus.VERIFIED_RECORDS
            if record_count or gap_reasons
            else SourceStatus.VERIFIED_EMPTY
        )
        if status is SourceStatus.VERIFIED_EMPTY:
            # An explicit zero count remains in source_metadata, not in the
            # observation's business Evidence (the empty contract forbids it).
            evidence = []
        return GatewaySubmoduleObservation(
            submodule_id=route.submodule_id,
            module_id=route.section_id,
            capability=capability,
            source_status=status,
            availability=(
                SubmoduleAvailability.AVAILABLE
                if status is SourceStatus.VERIFIED_RECORDS
                else SubmoduleAvailability.VERIFIED_EMPTY
            ),
            completeness=(
                CoverageCompleteness.PARTIAL if gap_reasons else CoverageCompleteness.COMPLETE
            ),
            gap_reasons=gap_reasons,
            facts={
                "source_status": status.value,
                "source_tool": capability,
                "record_count": record_count,
                "records": [_json_value(item.value) for item in evidence],
                "source_metadata": source_metadata,
            },
            evidence=tuple(evidence),
            invocation_ids=tuple(invocation_ids),
        )

    async def _call_mcp(
        self,
        *,
        mcp_tool_name: str,
        capability: str,
        arguments: dict[str, Any],
        subject_id: str,
        page: int | None,
    ) -> tuple[McpCallResult, GatewayInvocation]:
        _check_cancellation(self._cancellation_token)
        await self._claim_call(capability)
        called_at = self._clock()
        arguments_sha256 = _sha256(arguments)
        invocation_id = (
            "mcp-"
            + hashlib.sha256(
                (
                    f"{self.run_id}|{self.agent_id}|{capability}|{arguments_sha256}|"
                    f"{self._mcp_calls}"
                ).encode()
            ).hexdigest()[:24]
        )
        try:
            async with self._semaphore:
                _check_cancellation(self._cancellation_token)
                raw_result = await self._client.call_tool(mcp_tool_name, arguments)
                _check_cancellation(self._cancellation_token)
        except TianyanchaMcpError:
            self._invocations.append(
                GatewayInvocation(
                    invocation_id=invocation_id,
                    run_id=self.run_id,
                    agent_id=self.agent_id,
                    subject_id=subject_id,
                    capability=capability,
                    mcp_tool_name=mcp_tool_name,
                    page=page,
                    arguments_sha256=arguments_sha256,
                    source_status=SourceStatus.SOURCE_ERROR,
                    called_at=called_at,
                )
            )
            raise

        safe_result = McpCallResult.model_validate(
            cast(dict[str, object], redact_sensitive(raw_result.model_dump(mode="python")))
        )
        content_sha256 = _sha256(safe_result.model_dump(mode="json"))
        status = (
            SourceStatus.VERIFIED_RECORDS
            if safe_result.text or self._contains_records(safe_result.structured_content)
            else SourceStatus.VERIFIED_EMPTY
        )
        invocation = GatewayInvocation(
            invocation_id=invocation_id,
            run_id=self.run_id,
            agent_id=self.agent_id,
            subject_id=subject_id,
            capability=capability,
            mcp_tool_name=mcp_tool_name,
            page=page,
            arguments_sha256=arguments_sha256,
            content_sha256=content_sha256,
            source_status=status,
            called_at=called_at,
        )
        self._invocations.append(invocation)
        return safe_result, invocation

    async def _claim_call(self, capability: str) -> None:
        async with self._budget_lock:
            if self._mcp_calls >= self.budget.max_mcp_calls:
                raise AgentExecutionError(
                    "Tianyancha MCP acquisition budget exhausted",
                    details={"capability": capability, "limit": self.budget.max_mcp_calls},
                )
            self._mcp_calls += 1

    @staticmethod
    def _contains_records(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        for key in ("items", "tools", "capabilities"):
            if isinstance(value.get(key), list) and bool(value[key]):
                return True
        return any(
            TianyanchaMcpGateway._contains_records(value.get(key)) for key in ("result", "data")
        )

    @staticmethod
    def _absent_observation(route: ReportSubmoduleRoute) -> GatewaySubmoduleObservation:
        return GatewaySubmoduleObservation(
            submodule_id=route.submodule_id,
            module_id=route.section_id,
            source_status=SourceStatus.CAPABILITY_ABSENT,
            availability=SubmoduleAvailability.CAPABILITY_ABSENT,
            completeness=CoverageCompleteness.UNKNOWN,
            facts={"source_status": SourceStatus.CAPABILITY_ABSENT.value, "records": []},
        )

    @staticmethod
    def _for_route(
        observation: GatewaySubmoduleObservation,
        route: ReportSubmoduleRoute,
    ) -> GatewaySubmoduleObservation:
        if observation.submodule_id == route.submodule_id:
            return observation
        return observation.model_copy(
            update={"submodule_id": route.submodule_id, "module_id": route.section_id}
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


__all__ = [
    "ENTERPRISE_CONTEXT_AGENT_ID",
    "GatewayBudget",
    "GatewayInvocation",
    "GatewaySubmoduleObservation",
    "TianyanchaMcpGateway",
]
