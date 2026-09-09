"""Pure deterministic gates for accepted fixed-check Agent submissions."""

from __future__ import annotations

from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
)
from jindiao.contracts.evidence import SourceType
from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    DueDiligenceCheckCatalog,
)
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)

EVIDENCE_GATE_INCONCLUSIVE_SUMMARY = "证据门禁未通过。无法支持 risk/no_risk 结论。"


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _context_evidence_ids(
    snapshot: EnterpriseContextSnapshot,
    submodule_id: str,
) -> tuple[str, ...]:
    context = snapshot.submodule(submodule_id)
    return (*context.evidence_ids, *context.supplemental_evidence_ids)


def validate_check_result_scope(
    *,
    snapshot: EnterpriseContextSnapshot,
    check_catalog: DueDiligenceCheckCatalog,
    result: CheckResult,
) -> None:
    """Recheck immutable identity, schema and Evidence scope at finalization."""

    if result.snapshot_id != snapshot.snapshot_id:
        raise ValueError("check submission references another snapshot")
    if result.snapshot_sha256 != snapshot.snapshot_sha256:
        raise ValueError("check submission snapshot hash does not match")
    if result.subject_id != snapshot.subject.subject_id:
        raise ValueError("check submission references another subject")
    if result.check_catalog_version != check_catalog.catalog_version:
        raise ValueError("check submission uses another CheckCatalog version")
    try:
        definition = check_catalog.get(result.check_id)
    except KeyError as error:
        raise ValueError(f"check submission references unknown check: {result.check_id}") from error
    if result.output_schema_version != definition.output_schema_version:
        raise ValueError("check submission output schema version does not match")

    known_evidence = {item.evidence_id for item in snapshot.evidence}
    cited = {item.evidence_id for item in result.fact_evidence_refs}
    unknown = cited - known_evidence
    if unknown:
        raise ValueError(f"check submission references unknown Evidence: {sorted(unknown)}")
    authorized_submodules = {
        *definition.required_submodule_ids,
        *definition.optional_submodule_ids,
    }
    authorized_evidence = {
        evidence_id
        for submodule_id in authorized_submodules
        for evidence_id in _context_evidence_ids(snapshot, submodule_id)
    }
    outside = cited - authorized_evidence
    if outside:
        raise ValueError(f"check submission Evidence is outside check scope: {sorted(outside)}")


def downgrade_unsupported_check_conclusion(
    *,
    snapshot: EnterpriseContextSnapshot,
    check_catalog: DueDiligenceCheckCatalog,
    result: CheckResult,
) -> CheckResult:
    """Turn a scoped but unsupported model conclusion into a safe terminal result."""

    if result.status is CheckStatus.INCONCLUSIVE:
        return result

    definition = check_catalog.get(result.check_id)
    required_contexts = tuple(
        snapshot.submodule(item) for item in definition.required_submodule_ids
    )
    cited = {item.evidence_id for item in result.fact_evidence_refs}
    missing_evidence = list(result.missing_evidence)
    blocked = False

    unavailable_states = {
        SubmoduleAvailability.CAPABILITY_ABSENT,
        SubmoduleAvailability.SOURCE_ERROR,
        SubmoduleAvailability.NOT_REQUESTED,
    }
    for context in required_contexts:
        if context.availability in unavailable_states:
            blocked = True
            missing_evidence.append(f"required:{context.submodule_id}:{context.availability.value}")
            continue
        if (
            definition.evidence_requirements.require_all_required_submodules
            and context.availability is SubmoduleAvailability.AVAILABLE
            and not (set(_context_evidence_ids(snapshot, context.submodule_id)) & cited)
        ):
            blocked = True
            missing_evidence.append(f"required:{context.submodule_id}:missing_evidence")

    minimum = definition.evidence_requirements.minimum_evidence_count
    if len(cited) < minimum:
        blocked = True
        missing_evidence.append(f"minimum_evidence_count:{minimum}")

    snapshot_conflicts = tuple(
        sorted(
            {
                evidence_id
                for submodule_id in (
                    *definition.required_submodule_ids,
                    *definition.optional_submodule_ids,
                )
                for evidence_id in snapshot.submodule(submodule_id).conflict_evidence_ids
            }
        )
    )
    conflicts = _unique((*result.conflicts, *snapshot_conflicts))
    if conflicts:
        blocked = True

    if result.status is CheckStatus.NO_RISK and cited:
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
        public_web_only = all(
            evidence_by_id[evidence_id].source_type is SourceType.PUBLIC_WEB
            for evidence_id in cited
        )
        if (
            public_web_only
            and not definition.evidence_requirements.allow_public_web_only_for_no_risk
        ):
            blocked = True
            missing_evidence.append("authoritative_evidence_required")

    if not blocked:
        return result
    return result.model_copy(
        update={
            "status": CheckStatus.INCONCLUSIVE,
            "decision_summary": EVIDENCE_GATE_INCONCLUSIVE_SUMMARY,
            "risk_items": (),
            "missing_evidence": _unique(tuple(missing_evidence)),
            "conflicts": conflicts,
        }
    )


def validate_check_result_evidence_gate(
    *,
    snapshot: EnterpriseContextSnapshot,
    check_catalog: DueDiligenceCheckCatalog,
    result: CheckResult,
) -> None:
    """Apply missing-data, conflict and source-quality policy deterministically."""

    definition = check_catalog.get(result.check_id)
    required_contexts = tuple(
        snapshot.submodule(item) for item in definition.required_submodule_ids
    )
    unavailable = tuple(
        item.submodule_id
        for item in required_contexts
        if item.availability
        in {
            SubmoduleAvailability.CAPABILITY_ABSENT,
            SubmoduleAvailability.SOURCE_ERROR,
            SubmoduleAvailability.NOT_REQUESTED,
        }
    )
    conflict_ids = {
        evidence_id
        for submodule_id in (
            *definition.required_submodule_ids,
            *definition.optional_submodule_ids,
        )
        for evidence_id in snapshot.submodule(submodule_id).conflict_evidence_ids
    }
    cited = {item.evidence_id for item in result.fact_evidence_refs}
    minimum = definition.evidence_requirements.minimum_evidence_count

    if result.status in {CheckStatus.RISK, CheckStatus.NO_RISK}:
        if unavailable:
            states = {
                item.submodule_id: item.availability.value
                for item in required_contexts
                if item.submodule_id in unavailable
            }
            raise ValueError(f"required source status blocks conclusion: {states}")
        if conflict_ids or result.conflicts:
            raise ValueError("unresolved Evidence conflict blocks risk/no_risk conclusion")
        if len(cited) < minimum:
            raise ValueError(
                f"missing required Evidence: expected at least {minimum}, got {len(cited)}"
            )
        if definition.evidence_requirements.require_all_required_submodules:
            missing_submodules = {
                item.submodule_id
                for item in required_contexts
                if item.availability is SubmoduleAvailability.AVAILABLE
                and not (set(_context_evidence_ids(snapshot, item.submodule_id)) & cited)
            }
            if missing_submodules:
                raise ValueError(
                    f"missing required Evidence for submodules: {sorted(missing_submodules)}"
                )
    if result.status is CheckStatus.NO_RISK:
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
        if (
            cited
            and not definition.evidence_requirements.allow_public_web_only_for_no_risk
            and all(
                evidence_by_id[evidence_id].source_type is SourceType.PUBLIC_WEB
                for evidence_id in cited
            )
        ):
            raise ValueError("public-Web Evidence alone cannot support no_risk")
    if result.status is CheckStatus.INCONCLUSIVE:
        has_deterministic_gap = bool(
            unavailable
            or conflict_ids
            or result.missing_evidence
            or result.conflicts
            or len(cited) < minimum
        )
        if not has_deterministic_gap:
            raise ValueError("inconclusive submission must identify an Evidence gap")


def validate_accepted_investigation_results(
    *,
    snapshot: EnterpriseContextSnapshot,
    check_catalog: DueDiligenceCheckCatalog,
    agent_results: tuple[AgentInvestigationResult, ...],
    allow_partial: bool = False,
) -> tuple[CheckResult, ...]:
    """Return catalog-ordered checks only after the final common hard gate."""

    if check_catalog.acquisition_catalog_version != snapshot.acquisition_catalog_version:
        raise ValueError("check catalog does not match snapshot AcquisitionCatalog")

    by_check: dict[str, CheckResult] = {}
    risk_owners: dict[str, tuple[str, str]] = {}
    for agent_result in agent_results:
        if agent_result.phase is not AgentResultPhase.INVESTIGATION:
            continue
        if agent_result.status is not AgentStatus.COMPLETED and not allow_partial:
            raise ValueError(
                f"investigation agent is not terminal-completed: {agent_result.agent_id}"
            )
        for result in agent_result.check_results:
            if result.check_id in by_check:
                raise ValueError(f"duplicate terminal check: {result.check_id}")
            definition = check_catalog.get(result.check_id)
            if (
                agent_result.role != "single-investigator"
                and agent_result.role != definition.owner_role
            ):
                raise ValueError(
                    f"check {result.check_id} submitted by wrong role: {agent_result.role}"
                )
            validate_check_result_scope(
                snapshot=snapshot,
                check_catalog=check_catalog,
                result=result,
            )
            validate_check_result_evidence_gate(
                snapshot=snapshot,
                check_catalog=check_catalog,
                result=result,
            )
            by_check[result.check_id] = result
            for risk in result.risk_items:
                previous = risk_owners.get(risk.risk_id)
                if previous is not None:
                    raise ValueError(
                        "duplicate risk ids across accepted checks: "
                        f"{risk.risk_id} in {previous[1]} and {result.check_id}"
                    )
                risk_owners[risk.risk_id] = (agent_result.agent_id, result.check_id)

    enabled = tuple(item.check_id for item in check_catalog.checks if item.enabled)
    missing = set(enabled) - set(by_check)
    extra = set(by_check) - set(enabled)
    if missing and not allow_partial:
        raise ValueError(f"missing terminal checks: {sorted(missing)}")
    if extra:
        raise ValueError(f"disabled checks cannot be adjudicated: {sorted(extra)}")
    return tuple(by_check[check_id] for check_id in enabled if check_id in by_check)


__all__ = [
    "EVIDENCE_GATE_INCONCLUSIVE_SUMMARY",
    "downgrade_unsupported_check_conclusion",
    "validate_accepted_investigation_results",
    "validate_check_result_evidence_gate",
    "validate_check_result_scope",
]
