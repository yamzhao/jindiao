"""Immutable state captured once at the beginning of an investigation run."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from jindiao.contracts.report_policy import ReportingPolicyBinding, ReportPolicy

from .settings import Settings

if TYPE_CHECKING:
    from jindiao.contracts.entities import EnterpriseInput
    from jindiao.scenarios import ScenarioSnapshot


@dataclass(frozen=True, slots=True)
class RunPolicy:
    """Non-secret execution settings copied from mutable process configuration."""

    model_name: str
    agent_runtime_mode: Literal["formal", "deterministic_harness"]
    formal_agent_run: bool
    max_concurrency: int
    request_timeout_seconds: int
    max_tool_calls: int
    max_repair_rounds: int
    max_llm_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_schema_retries: int
    max_snapshot_reads: int
    allow_degraded_mock: bool


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's stable scenario, rule, Skill, and execution-policy snapshot."""

    request_id: str
    run_id: str
    scenario_snapshot_id: str
    scenario: ScenarioSnapshot
    report_as_of: date
    rule_version: str
    skill_versions: Mapping[str, str]
    policy: RunPolicy
    requested_enterprise: EnterpriseInput | None = None
    reporting_policy: ReportingPolicyBinding = field(
        default_factory=lambda: ReportingPolicyBinding.freeze(
            ReportPolicy(), version="1.1.0", revision=0
        )
    )

    @classmethod
    def from_settings(
        cls,
        *,
        request_id: str,
        run_id: str,
        scenario: ScenarioSnapshot,
        settings: Settings,
        skill_versions: Mapping[str, str],
        requested_enterprise: EnterpriseInput | None = None,
        report_as_of: date | None = None,
        reporting_policy: ReportingPolicyBinding | None = None,
    ) -> RunContext:
        if not request_id or not run_id:
            raise ValueError("request_id and run_id must not be empty")
        binding = reporting_policy or ReportingPolicyBinding.freeze(
            ReportPolicy(), version="1.1.0", revision=0
        )
        return cls(
            request_id=request_id,
            run_id=run_id,
            scenario_snapshot_id=scenario.scenario_snapshot_id,
            scenario=scenario,
            report_as_of=report_as_of or scenario.manifest.as_of_date,
            rule_version=settings.rule_version,
            skill_versions=MappingProxyType(
                {**skill_versions, "feedback-evolved-reporting": binding.version}
            ),
            reporting_policy=binding,
            policy=RunPolicy(
                model_name=settings.model_name,
                agent_runtime_mode=settings.agent_runtime_mode,
                formal_agent_run=settings.formal_agent_run,
                max_concurrency=settings.max_concurrency,
                request_timeout_seconds=settings.request_timeout_seconds,
                max_tool_calls=settings.max_tool_calls,
                max_repair_rounds=settings.max_repair_rounds,
                max_llm_requests=settings.max_llm_requests,
                max_input_tokens=settings.max_input_tokens,
                max_output_tokens=settings.max_output_tokens,
                max_total_tokens=settings.max_total_tokens,
                max_schema_retries=settings.max_schema_retries,
                max_snapshot_reads=settings.max_snapshot_reads,
                allow_degraded_mock=settings.allow_degraded_mock,
            ),
            requested_enterprise=requested_enterprise,
        )


__all__ = ["RunContext", "RunPolicy"]
