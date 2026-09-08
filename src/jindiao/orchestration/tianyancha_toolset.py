"""Tianyancha-first investigation toolset with explicit Mock supplements."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from pydantic import JsonValue

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.agents.deepsearch_agent import DeepSearchCapabilityAgent, DeepSearchEvidenceAgent
from jindiao.application.context import RunContext
from jindiao.application.errors import SourceUnavailableError, error_to_record
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    CoverageItem,
    SourceStatus,
)
from jindiao.contracts.investigation import Finding, RiskClass, Severity
from jindiao.deepsearch import (
    AnnualReportSocialSecurityOutcome,
    AnnualReportSocialSecurityProvider,
    BoundedEvidenceSupplementService,
    EvidenceGap,
    SupplementOutcome,
)
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    CompanyCapabilityManifest,
    CompanyCapabilityService,
    EvidenceDomain,
    McpToolCaller,
    NormalizedEvidenceBatch,
    ReportSubmoduleRoute,
    SourceObservation,
    SourceStateMachine,
    TianyanchaEntityResolver,
    TianyanchaEvidenceNormalizer,
    TianyanchaMcpError,
)

from .base import (
    CancellationToken,
    DomainInvestigation,
    check_cancellation,
    require_deterministic_harness,
)
from .scenario_toolset import ScenarioToolset

_DOMAINS = ("governance", "judicial", "operations", "peers")
_NO_PAGINATION = frozenset(
    {
        "get_company_registration_info",
        "get_registration_snapshot",
        "get_risk_overview",
        "get_risk_detail",
        "get_competitors",
    }
)


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_json_value(item) for item in value]
    raise TypeError(f"unsupported submodule value: {type(value).__name__}")


class ClosableMcpToolCaller(McpToolCaller, Protocol):
    async def aclose(self) -> None: ...


class TianyanchaHybridToolset:
    """Legacy deterministic hybrid facade; formal runs use Context Agent/Gateway."""

    formal_agent_run = False

    def __init__(
        self,
        *,
        client: ClosableMcpToolCaller,
        routing: CapabilityRoutingConfig,
        scenario_toolset: ScenarioToolset | None = None,
        deepsearch_agent: DeepSearchCapabilityAgent | None = None,
        supplement_service: BoundedEvidenceSupplementService | None = None,
        annual_report_provider: AnnualReportSocialSecurityProvider | None = None,
        max_concurrency: int = 4,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if deepsearch_agent is not None and (
            supplement_service is not None or annual_report_provider is not None
        ):
            raise ValueError("deepsearch_agent cannot be combined with raw DeepSearch dependencies")
        self._client = client
        self._routing = routing
        self._scenario_toolset = scenario_toolset or ScenarioToolset(clock=clock)
        self._deepsearch_agent = deepsearch_agent
        if self._deepsearch_agent is None and (
            supplement_service is not None or annual_report_provider is not None
        ):
            self._deepsearch_agent = DeepSearchEvidenceAgent(
                supplement_service=supplement_service,
                annual_report_provider=annual_report_provider,
            )
        self._clock = clock
        self._resolver = TianyanchaEntityResolver(client, clock=clock)
        self._capabilities = CompanyCapabilityService(
            client,
            ttl=timedelta(minutes=5),
            clock=clock,
        )
        self._manifest: CompanyCapabilityManifest | None = None
        self._manifest_lock = asyncio.Lock()
        # Domain agents run concurrently, so one semaphore must be shared by all of them.
        self._tool_semaphore = asyncio.Semaphore(max_concurrency)

    @property
    def mock_domains(self) -> frozenset[str]:
        return frozenset(_DOMAINS)

    @property
    def deepsearch_agent_enabled(self) -> bool:
        return self._deepsearch_agent is not None

    @property
    def annual_report_provider_enabled(self) -> bool:
        return bool(self._deepsearch_agent and self._deepsearch_agent.annual_report_enabled)

    async def resolve_subject(
        self,
        context: RunContext,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ResolvedSubject:
        require_deterministic_harness(context, component=type(self).__name__)
        check_cancellation(cancellation_token)
        company = context.scenario.manifest.enterprise_key
        from jindiao.contracts.entities import EnterpriseInput

        requested = context.requested_enterprise or EnterpriseInput(
            company_name=company.company_name
        )
        return await self._resolver.resolve(
            EnterpriseInput(
                company_name=requested.company_name,
                unified_social_credit_code=requested.unified_social_credit_code,
                region=requested.region,
            )
        )

    async def domain_capabilities(
        self,
        subject: ResolvedSubject,
    ) -> Mapping[str, tuple[str, ...]]:
        manifest = await self._get_manifest(subject)
        planned = set(ACQUISITION_CATALOG.default_plan_ids)
        return {
            domain: tuple(
                dict.fromkeys(
                    tool.name
                    for route, tool in self._routing.select_submodules(manifest, domain)
                    if route.submodule_id in planned and tool is not None
                )
            )
            for domain in _DOMAINS
        }

    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> DomainInvestigation:
        require_deterministic_harness(context, component=type(self).__name__)
        check_cancellation(cancellation_token)
        if domain not in _DOMAINS:
            raise ValueError(f"unsupported Tianyancha domain: {domain}")
        manifest = await self._get_manifest(subject)
        planned = set(ACQUISITION_CATALOG.default_plan_ids)
        routes = tuple(
            (route, tool)
            for route, tool in self._routing.select_submodules(manifest, domain)
            if route.submodule_id in planned
        )
        supplement = await self._scenario_toolset.investigate(context, subject, domain)
        selected_by_name = {tool.name: tool for _, tool in routes if tool is not None}
        outcomes = await self._query_tools(
            context,
            subject,
            domain,
            tuple(selected_by_name),
        )

        coverage: list[CoverageItem] = []
        submodules_by_section: dict[str, dict[str, JsonValue]] = {}
        mock_statuses: list[SourceStatus] = []
        pending_gaps: list[tuple[ReportSubmoduleRoute, EvidenceGap]] = []
        for route, tool in routes:
            outcome = outcomes.get(tool.name) if tool is not None else None
            (
                status,
                records,
                evidence_ids,
                error_message,
                completeness,
                gap_reasons,
            ) = self._submodule_result(
                context=context,
                route=route,
                tool_name=tool.name if tool is not None else None,
                outcome=outcome,
                supplement=supplement,
            )
            if status in {SourceStatus.CAPABILITY_ABSENT, SourceStatus.DEGRADED_MOCK}:
                mock_statuses.append(status)
            capability = tool.name if tool is not None else f"submodule:{route.submodule_id}"
            coverage.append(
                CoverageItem(
                    domain=domain,
                    capability=capability,
                    status=status,
                    record_count=(
                        len(records)
                        if status is SourceStatus.DEGRADED_MOCK
                        else (len(records) if status is SourceStatus.VERIFIED_RECORDS else 0)
                    ),
                    error=error_message,
                    completeness=completeness,
                    gap_reasons=gap_reasons,
                    fallback_reason=(
                        "source_error_degraded_mock"
                        if status is SourceStatus.DEGRADED_MOCK
                        else (
                            "tianyancha_capability_absent_mock_used"
                            if status is SourceStatus.CAPABILITY_ABSENT
                            else None
                        )
                    ),
                )
            )
            submodules_by_section.setdefault(route.section_id, {})[route.submodule_id] = {
                "title": route.title,
                "source_status": status.value,
                "source_tool": tool.name if tool is not None else None,
                "record_count": len(records),
                "records": records,
                "evidence_ids": list(evidence_ids),
                "coverage_completeness": completeness.value,
                "gap_reasons": [reason.value for reason in gap_reasons],
            }
            if (
                self._deepsearch_agent is not None
                and self._deepsearch_agent.supplement_enabled
                and status is SourceStatus.VERIFIED_RECORDS
                and completeness is CoverageCompleteness.PARTIAL
                and isinstance(outcome, NormalizedEvidenceBatch)
            ):
                pending_gaps.append(
                    (
                        route,
                        self._evidence_gap(
                            subject=subject,
                            domain=domain,
                            route=route,
                            capability=capability,
                            batch=outcome,
                            reason=gap_reasons[0],
                        ),
                    )
                )

        supplemental_outcomes = await self._research_gaps(
            context=context,
            subject=subject,
            pending_gaps=tuple(pending_gaps),
        )
        supplemental_evidence = tuple(
            evidence for _, outcome in supplemental_outcomes for evidence in outcome.evidence
        )
        for route, supplement_outcome in supplemental_outcomes:
            submodule = cast(
                dict[str, JsonValue],
                submodules_by_section[route.section_id][route.submodule_id],
            )
            submodule.update(
                {
                    "supplement_evidence_ids": [
                        item.evidence_id for item in supplement_outcome.evidence
                    ],
                    "supplement_queries": [
                        item.text for item in supplement_outcome.executed_queries
                    ],
                    "supplement_rounds": supplement_outcome.rounds_executed,
                    "supplement_unresolved": supplement_outcome.unresolved,
                    "supplement_errors": list(supplement_outcome.errors),
                }
            )
            if supplement_outcome.evidence:
                coverage.append(
                    CoverageItem(
                        domain=domain,
                        capability=f"supplement:{route.submodule_id}",
                        status=SourceStatus.VERIFIED_RECORDS,
                        record_count=len(supplement_outcome.evidence),
                        completeness=CoverageCompleteness.COMPLETE,
                    )
                )

        annual_report_outcome = await self._annual_report_social_security(
            context=context,
            subject=subject,
            domain=domain,
        )
        annual_report_evidence = (
            annual_report_outcome.evidence if annual_report_outcome is not None else ()
        )
        if annual_report_outcome is not None:
            self._append_annual_report_result(
                submodules_by_section=submodules_by_section,
                coverage=coverage,
                outcome=annual_report_outcome,
            )

        unique_batches = tuple(
            item for item in outcomes.values() if isinstance(item, NormalizedEvidenceBatch)
        )
        live_evidence = tuple(evidence for batch in unique_batches for evidence in batch.evidence)
        live_findings = tuple(
            self._live_finding(domain, subject, tool_name, batch)
            for tool_name, batch in outcomes.items()
            if isinstance(batch, NormalizedEvidenceBatch) and batch.evidence
        )
        use_mock = bool(mock_statuses)
        mock_source_status = (
            SourceStatus.DEGRADED_MOCK
            if SourceStatus.DEGRADED_MOCK in mock_statuses
            else SourceStatus.CAPABILITY_ABSENT
        )
        mock_evidence = (
            tuple(
                item.model_copy(update={"source_status": mock_source_status})
                for item in supplement.evidence
            )
            if use_mock
            else ()
        )
        status_counts: dict[str, JsonValue] = {
            status.value: sum(item.status is status for item in coverage) for status in SourceStatus
        }
        section_data = self._patch_section_data(
            {section_id: {} for section_id in submodules_by_section},
            subject,
            source_summary={
                "domain": domain,
                "tools_attempted": list(selected_by_name),
                "tool_count": len(selected_by_name),
                "submodule_count": len(routes),
                "status_counts": status_counts,
                "mock_supplement": use_mock,
                "web_supplement": bool(supplemental_outcomes),
                "annual_report_supplement": annual_report_outcome is not None,
            },
        )
        for section_id, submodules in submodules_by_section.items():
            section_data[section_id]["submodules"] = submodules
        errors = tuple(
            error_to_record(item)
            for item in outcomes.values()
            if isinstance(item, TianyanchaMcpError) and not context.policy.allow_degraded_mock
        )
        if annual_report_outcome is not None and annual_report_outcome.error is not None:
            errors += (
                error_to_record(
                    SourceUnavailableError(
                        "Tianyancha annual report source is unavailable",
                        details={
                            "provider": "tianyancha.annual_report.web",
                            "reason": annual_report_outcome.error,
                        },
                    )
                ),
            )
        return DomainInvestigation(
            task_id=f"live-{domain}",
            domain=domain,
            findings=live_findings + (supplement.findings if use_mock else ()),
            evidence=(
                live_evidence + mock_evidence + supplemental_evidence + annual_report_evidence
            ),
            coverage_items=tuple(coverage),
            section_data=section_data,
            errors=errors,
        )

    async def _research_gaps(
        self,
        *,
        context: RunContext,
        subject: ResolvedSubject,
        pending_gaps: tuple[tuple[ReportSubmoduleRoute, EvidenceGap], ...],
    ) -> tuple[tuple[ReportSubmoduleRoute, SupplementOutcome], ...]:
        if self._deepsearch_agent is None or not self._deepsearch_agent.supplement_enabled:
            return ()
        outcomes: list[tuple[ReportSubmoduleRoute, SupplementOutcome]] = []
        for route, gap in pending_gaps:
            outcome = await self._deepsearch_agent.research_gap(
                subject=subject,
                gap=gap,
                queried_at=self._clock(),
                report_as_of=context.report_as_of,
            )
            outcomes.append((route, outcome))
        return tuple(outcomes)

    async def _annual_report_social_security(
        self,
        *,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> AnnualReportSocialSecurityOutcome | None:
        if (
            domain != "operations"
            or self._deepsearch_agent is None
            or not self._deepsearch_agent.annual_report_enabled
        ):
            return None
        try:
            return await self._deepsearch_agent.fetch_annual_report_social_security(
                subject=subject,
                queried_at=self._clock(),
                report_as_of=context.report_as_of,
            )
        except Exception:
            return AnnualReportSocialSecurityOutcome(
                source_status=SourceStatus.SOURCE_ERROR,
                checked_years=(context.report_as_of.year - 1,),
                error="annual_report_provider_failed",
            )

    @staticmethod
    def _append_annual_report_result(
        *,
        submodules_by_section: dict[str, dict[str, JsonValue]],
        coverage: list[CoverageItem],
        outcome: AnnualReportSocialSecurityOutcome,
    ) -> None:
        has_records = outcome.source_status is SourceStatus.VERIFIED_RECORDS
        completeness = (
            CoverageCompleteness.UNKNOWN
            if outcome.source_status is SourceStatus.SOURCE_ERROR
            else CoverageCompleteness.COMPLETE
        )
        gap_reasons = (
            (CoverageGapReason.SOURCE_UNAVAILABLE,)
            if outcome.source_status is SourceStatus.SOURCE_ERROR
            else ()
        )
        coverage.append(
            CoverageItem(
                domain="operations",
                capability="tianyancha.annual_report.social_security",
                status=outcome.source_status,
                record_count=len(outcome.evidence) if has_records else 0,
                error=outcome.error,
                completeness=completeness,
                gap_reasons=gap_reasons,
            )
        )
        report_data = (
            _to_json_value(outcome.report.model_dump(mode="json"))
            if outcome.report is not None
            else None
        )
        annual_reports = cast(
            dict[str, JsonValue],
            submodules_by_section.setdefault("operations-analysis", {}).setdefault(
                "annual_reports",
                {
                    "title": "工商年报",
                    "source_status": outcome.source_status.value,
                    "source_tool": None,
                    "record_count": 0,
                    "records": [],
                    "evidence_ids": [],
                    "coverage_completeness": CoverageCompleteness.PARTIAL.value,
                    "gap_reasons": [CoverageGapReason.MISSING_FIELDS.value],
                },
            ),
        )
        annual_reports["social_security"] = {
            "title": "最近年度报告社保信息",
            "source_status": outcome.source_status.value,
            "source_tool": "tianyancha.annual_report.web",
            "record_count": len(outcome.evidence) if has_records else 0,
            "records": [report_data] if has_records and report_data is not None else [],
            "evidence_ids": [item.evidence_id for item in outcome.evidence],
            "coverage_completeness": completeness.value,
            "submodule_coverage": CoverageCompleteness.PARTIAL.value,
            "gap_reasons": [reason.value for reason in gap_reasons],
            "checked_years": list(outcome.checked_years),
            "report_found": outcome.report is not None,
            "report_year": outcome.report.report_year if outcome.report is not None else None,
            "publicized_at": (
                outcome.report.publicized_at.isoformat()
                if outcome.report is not None and outcome.report.publicized_at is not None
                else None
            ),
            "error": outcome.error,
        }

    @staticmethod
    def _evidence_gap(
        *,
        subject: ResolvedSubject,
        domain: str,
        route: ReportSubmoduleRoute,
        capability: str,
        batch: NormalizedEvidenceBatch,
        reason: CoverageGapReason,
    ) -> EvidenceGap:
        supports_fields = tuple(
            sorted({field for evidence in batch.evidence for field in evidence.supports_fields})
        ) or (f"{domain}.records",)
        digest = hashlib.sha256(
            (
                f"{subject.subject_id}|{domain}|{route.submodule_id}|{capability}|{reason.value}"
            ).encode()
        ).hexdigest()[:16]
        return EvidenceGap(
            gap_id=f"gap-{digest}",
            subject_id=subject.subject_id,
            domain=domain,
            submodule_id=route.submodule_id,
            capability=capability,
            topic=route.title,
            supports_fields=supports_fields,
            reason=reason,
        )

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        finally:
            if self._deepsearch_agent is not None:
                await self._deepsearch_agent.aclose()

    async def _query_tools(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
        tool_names: tuple[str, ...],
    ) -> dict[str, NormalizedEvidenceBatch | TianyanchaMcpError]:
        async def query(tool_name: str) -> tuple[str, NormalizedEvidenceBatch | TianyanchaMcpError]:
            try:
                async with self._tool_semaphore:
                    result = await self._client.call_tool(
                        "call_tool",
                        {
                            "tool_name": tool_name,
                            "company_name": subject.company_name,
                            "arguments": self._arguments_for(tool_name),
                        },
                    )
                normalized = TianyanchaEvidenceNormalizer().normalize(
                    domain=EvidenceDomain(domain),
                    subject=subject,
                    tool_name=tool_name,
                    result=result,
                    queried_at=self._clock(),
                    as_of_date=context.report_as_of,
                    raw_snapshot_ref=f"mcp://tianyancha/{tool_name}",
                )
                return tool_name, normalized
            except TianyanchaMcpError as error:
                return tool_name, error

        return dict(await asyncio.gather(*(query(name) for name in tool_names)))

    def _submodule_result(
        self,
        *,
        context: RunContext,
        route: ReportSubmoduleRoute,
        tool_name: str | None,
        outcome: NormalizedEvidenceBatch | TianyanchaMcpError | None,
        supplement: DomainInvestigation,
    ) -> tuple[
        SourceStatus,
        list[JsonValue],
        tuple[str, ...],
        str | None,
        CoverageCompleteness,
        tuple[CoverageGapReason, ...],
    ]:
        if tool_name is None:
            records = self._mock_records(context, supplement, route)
            return (
                SourceStatus.CAPABILITY_ABSENT,
                records,
                self._mock_evidence_ids(supplement, records),
                None,
                CoverageCompleteness.UNKNOWN,
                (),
            )
        if isinstance(outcome, TianyanchaMcpError):
            if context.policy.allow_degraded_mock:
                records = self._mock_records(context, supplement, route)
                return (
                    SourceStatus.DEGRADED_MOCK,
                    records,
                    self._mock_evidence_ids(supplement, records),
                    None,
                    CoverageCompleteness.UNKNOWN,
                    (),
                )
            return (
                SourceStatus.SOURCE_ERROR,
                [],
                (),
                outcome.message,
                CoverageCompleteness.UNKNOWN,
                (CoverageGapReason.SOURCE_UNAVAILABLE,),
            )
        if not isinstance(outcome, NormalizedEvidenceBatch):
            raise AssertionError(f"missing outcome for selected tool: {tool_name}")
        source = SourceStateMachine.transition(
            SourceObservation(
                capability_available=True,
                query_succeeded=True,
                record_count=outcome.record_count,
            )
        )
        records = [item.value for item in outcome.evidence]
        truncated = bool(
            outcome.pagination
            and outcome.pagination.is_truncated(returned_count=outcome.record_count)
        )
        return (
            source.status,
            records,
            tuple(item.evidence_id for item in outcome.evidence),
            None,
            (CoverageCompleteness.PARTIAL if truncated else CoverageCompleteness.COMPLETE),
            ((CoverageGapReason.PAGINATION_TRUNCATED,) if truncated else ()),
        )

    @staticmethod
    def _mock_evidence_ids(
        supplement: DomainInvestigation,
        records: list[JsonValue],
    ) -> tuple[str, ...]:
        if not records:
            return ()
        return tuple(item.evidence_id for item in supplement.evidence)

    @staticmethod
    def _mock_records(
        context: RunContext,
        supplement: DomainInvestigation,
        route: ReportSubmoduleRoute,
    ) -> list[JsonValue]:
        records: list[JsonValue] = []
        section = supplement.section_data.get(route.section_id, {})
        for key in route.mock_keys:
            if key == "company":
                records.append(_to_json_value(context.scenario.read_json("company.json")))
                continue
            if key not in section:
                continue
            value = section[key]
            if isinstance(value, list):
                records.extend(value)
            elif isinstance(value, dict):
                records.append(value)
            else:
                records.append({"field": key, "value": value})
        return records

    @staticmethod
    def _live_finding(
        domain: str,
        subject: ResolvedSubject,
        tool_name: str,
        normalized: NormalizedEvidenceBatch,
    ) -> Finding:
        finding_digest = hashlib.sha256(f"{domain}|{tool_name}".encode()).hexdigest()[:12]
        return Finding(
            finding_id=f"finding-tyc-{finding_digest}",
            subject_id=subject.subject_id,
            domain=domain,
            claim=f"天眼查 {tool_name} 业务记录已采集",
            value={"tool_name": tool_name, "record_count": normalized.record_count},
            risk_class=RiskClass.NON_RISK,
            severity=Severity.INFO,
            evidence_ids=tuple(item.evidence_id for item in normalized.evidence),
        )

    async def _get_manifest(self, subject: ResolvedSubject) -> CompanyCapabilityManifest:
        if self._manifest is not None:
            return self._manifest
        async with self._manifest_lock:
            if self._manifest is None:
                self._manifest = await self._capabilities.get(subject)
            return self._manifest

    @staticmethod
    def _arguments_for(tool_name: str) -> dict[str, int]:
        if tool_name in _NO_PAGINATION:
            return {}
        return {"page": 1, "page_size": 20}

    @staticmethod
    def _patch_section_data(
        values: Mapping[str, Mapping[str, JsonValue]],
        subject: ResolvedSubject,
        *,
        source_summary: Mapping[str, JsonValue],
    ) -> dict[str, dict[str, JsonValue]]:
        patched = {section_id: dict(data) for section_id, data in values.items()}
        company = patched.get("company-profile")
        if company is not None:
            company.update(
                {
                    "company_name": subject.company_name,
                    "unified_social_credit_code": subject.unified_social_credit_code,
                    "region": subject.region,
                    "registration_status": subject.registration_status,
                }
            )
        for data in patched.values():
            data["source_summary"] = dict(source_summary)
        return patched


__all__ = ["TianyanchaHybridToolset"]
