"""One-acquisition paired execution for fair single-versus-multi comparison."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace

from jindiao.application.context import RunContext
from jindiao.application.formal_pipeline import (
    FormalDueDiligencePipeline,
    FormalPipelineRun,
)
from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.execution import (
    ComparisonFingerprint,
    FormalComparisonEligibility,
    PairedExecutionCost,
)
from jindiao.contracts.results import OrchestrationMode
from jindiao.investigation import (
    CHECK_CATALOG,
    validate_accepted_investigation_results,
)
from jindiao.orchestration.base import RunBudget, RuntimeEventSink


@dataclass(frozen=True, slots=True)
class PairedComparisonRun:
    """Two isolated investigation arms sharing one content-addressed snapshot."""

    pair_id: str
    snapshot: EnterpriseContextSnapshot
    fingerprint: ComparisonFingerprint
    single: FormalPipelineRun
    multi: FormalPipelineRun
    formal_eligibility: FormalComparisonEligibility

    @property
    def execution_cost(self) -> PairedExecutionCost:
        return PairedExecutionCost(
            shared_acquisition_cost=self.snapshot.shared_acquisition_cost,
            single_investigation_cost=self.single.investigation_cost,
            multi_investigation_cost=self.multi.investigation_cost,
        )


class PairedComparisonRunner:
    """Acquire exactly once and fork only after the snapshot becomes immutable."""

    def __init__(
        self,
        *,
        pipeline: FormalDueDiligencePipeline,
        fingerprint_factory: (
            Callable[
                [OrchestrationMode, EnterpriseContextSnapshot],
                ComparisonFingerprint,
            ]
            | None
        ) = None,
    ) -> None:
        self._pipeline = pipeline
        self._fingerprint_factory = fingerprint_factory

    async def run(
        self,
        context: RunContext,
        *,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
    ) -> PairedComparisonRun:
        acquisition = await self._pipeline.acquire(context, event_sink=event_sink)
        snapshot = acquisition.snapshot
        pair_id = self._pair_id(context, snapshot)
        fingerprint_factory = self._fingerprint_factory or (
            lambda _mode, target: self._pipeline.comparison_fingerprint(
                context,
                snapshot=target,
                budget=budget,
            )
        )
        fingerprints = {mode: fingerprint_factory(mode, snapshot) for mode in OrchestrationMode}
        differences = self.fingerprint_differences(
            fingerprints[OrchestrationMode.SINGLE],
            fingerprints[OrchestrationMode.MULTI],
        )
        if differences:
            raise ValueError("paired comparison fingerprint mismatch: " + ", ".join(differences))
        arms: dict[OrchestrationMode, FormalPipelineRun] = {}
        for mode in OrchestrationMode:
            arm_context = replace(context, run_id=f"{context.run_id}:{mode.value}")
            arm = await self._pipeline.investigate(
                arm_context,
                snapshot=snapshot,
                mode=mode,
                budget=budget.model_copy(deep=True),
                event_sink=event_sink,
            )
            arms[mode] = FormalPipelineRun(
                snapshot=arm.snapshot,
                agent_results=arm.agent_results,
                outcome=arm.outcome,
                investigation_cost=arm.investigation_cost,
                comparison_metadata=arm.comparison_metadata.model_copy(
                    update={
                        "paired_comparison": True,
                        "comparison_pair_id": pair_id,
                    }
                ),
            )
        single = arms[OrchestrationMode.SINGLE]
        multi = arms[OrchestrationMode.MULTI]
        return PairedComparisonRun(
            pair_id=pair_id,
            snapshot=snapshot,
            fingerprint=fingerprints[OrchestrationMode.SINGLE],
            single=single,
            multi=multi,
            formal_eligibility=self._formal_eligibility(
                snapshot=snapshot,
                fingerprint=fingerprints[OrchestrationMode.SINGLE],
                arms={
                    OrchestrationMode.SINGLE: single,
                    OrchestrationMode.MULTI: multi,
                },
            ),
        )

    @staticmethod
    def _formal_eligibility(
        *,
        snapshot: EnterpriseContextSnapshot,
        fingerprint: ComparisonFingerprint,
        arms: dict[OrchestrationMode, FormalPipelineRun],
    ) -> FormalComparisonEligibility:
        reasons: list[str] = []
        provider_identity = f"{fingerprint.model_provider} {fingerprint.model_name}".lower()
        if any(marker in provider_identity for marker in ("fake", "offline", "mock")):
            reasons.append("pair:fake_or_offline_model")

        for mode in OrchestrationMode:
            arm = arms[mode]
            prefix = f"{mode.value}:"
            if not arm.comparison_metadata.formal_agent_run:
                reasons.append(prefix + "non_formal_agent_run")
            if arm.investigation_cost.successful_llm_requests == 0:
                reasons.append(prefix + "no_successful_llm_request")
            if arm.investigation_cost.provider_usage_requests == 0:
                reasons.append(prefix + "no_provider_usage")
            if arm.investigation_cost.total_tokens == 0:
                reasons.append(prefix + "zero_tokens")
            limits = fingerprint.investigation_budget
            cost = arm.investigation_cost
            if any(
                (
                    cost.llm_requests > limits.max_llm_requests,
                    cost.input_tokens > limits.max_input_tokens,
                    cost.output_tokens > limits.max_output_tokens,
                    cost.total_tokens > limits.max_total_tokens,
                    cost.wall_time_ms > limits.max_wall_time_ms,
                    cost.schema_retries > limits.max_schema_retries,
                    cost.repair_rounds > limits.max_repair_rounds,
                )
            ):
                reasons.append(prefix + "investigation_budget_exceeded")
            try:
                validate_accepted_investigation_results(
                    snapshot=snapshot,
                    check_catalog=CHECK_CATALOG,
                    agent_results=arm.agent_results,
                )
            except ValueError:
                reasons.append(prefix + "invalid_fixed_check_submissions")

        stable_reasons = tuple(dict.fromkeys(reasons))
        return FormalComparisonEligibility(
            eligible=not stable_reasons,
            reasons=stable_reasons,
        )

    @staticmethod
    def _pair_id(
        context: RunContext,
        snapshot: EnterpriseContextSnapshot,
    ) -> str:
        digest = hashlib.sha256(
            (
                f"{context.request_id}|{context.run_id}|{snapshot.snapshot_id}|"
                f"{snapshot.snapshot_sha256}"
            ).encode()
        ).hexdigest()
        return f"pair:{digest[:24]}"

    @staticmethod
    def fingerprint_differences(
        single: ComparisonFingerprint,
        multi: ComparisonFingerprint,
    ) -> tuple[str, ...]:
        single_values = single.model_dump(mode="json")
        multi_values = multi.model_dump(mode="json")
        return tuple(
            key
            for key in sorted({*single_values, *multi_values})
            if single_values.get(key) != multi_values.get(key)
        )


__all__ = [
    "FormalComparisonEligibility",
    "PairedComparisonRun",
    "PairedComparisonRunner",
]
