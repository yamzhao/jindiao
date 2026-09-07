from __future__ import annotations

import pytest
from test_multi_investigator_team import ROLE_AGENTS, check_result, snapshot

from jindiao.contracts.investigation import (
    CheckResult,
    CheckStatus,
    FactEvidenceRef,
    RiskClass,
    RiskItem,
    Severity,
)
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.investigation import (
    CHECK_CATALOG,
    validate_accepted_investigation_results,
)


def _agent_results() -> tuple[AgentInvestigationResult, ...]:
    results = []
    for role, agent_id in ROLE_AGENTS.items():
        checks = tuple(
            check_result(definition.check_id)
            for definition in CHECK_CATALOG.checks
            if definition.owner_role == role
        )
        results.append(
            AgentInvestigationResult(
                agent_id=agent_id,
                role=role,
                phase=AgentResultPhase.INVESTIGATION,
                status=AgentStatus.COMPLETED,
                task_ids=tuple(item.task_id for item in checks),
                check_results=checks,
                risk_items=tuple(risk for item in checks for risk in item.risk_items),
                fact_evidence_refs=tuple(
                    fact for item in checks for fact in item.fact_evidence_refs
                ),
                prompt_version=f"investigation-core-v1+{role}-v1",
            )
        )
    return tuple(results)


def _replace_check(
    agent_results: tuple[AgentInvestigationResult, ...],
    check_id: str,
    replacement: CheckResult,
) -> tuple[AgentInvestigationResult, ...]:
    updated = []
    for agent_result in agent_results:
        checks = tuple(
            replacement if item.check_id == check_id else item
            for item in agent_result.check_results
        )
        updated.append(
            agent_result.model_copy(
                update={
                    "check_results": checks,
                    "risk_items": tuple(risk for item in checks for risk in item.risk_items),
                    "fact_evidence_refs": tuple(
                        fact for item in checks for fact in item.fact_evidence_refs
                    ),
                }
            )
        )
    return tuple(updated)


def test_final_gate_accepts_exactly_one_terminal_result_for_every_fixed_check() -> None:
    accepted = validate_accepted_investigation_results(
        snapshot=snapshot(),
        check_catalog=CHECK_CATALOG,
        agent_results=_agent_results(),
    )

    assert tuple(item.check_id for item in accepted) == CHECK_CATALOG.check_ids


def test_final_gate_rejects_unknown_evidence_and_cross_snapshot_result() -> None:
    unknown_fact = FactEvidenceRef(
        evidence_id="ev-unknown",
        fact_path="governance.registration.registration_status",
        summary="unknown",
    )
    unknown = check_result("registration-status-normal").model_copy(
        update={"fact_evidence_refs": (unknown_fact,)}
    )
    with pytest.raises(ValueError, match="unknown Evidence"):
        validate_accepted_investigation_results(
            snapshot=snapshot(),
            check_catalog=CHECK_CATALOG,
            agent_results=_replace_check(_agent_results(), "registration-status-normal", unknown),
        )

    cross_snapshot = check_result("registration-status-normal").model_copy(
        update={"snapshot_id": "snapshot:other"}
    )
    with pytest.raises(ValueError, match="another snapshot"):
        validate_accepted_investigation_results(
            snapshot=snapshot(),
            check_catalog=CHECK_CATALOG,
            agent_results=_replace_check(
                _agent_results(), "registration-status-normal", cross_snapshot
            ),
        )


def test_final_gate_rejects_illegal_no_risk_and_missing_check() -> None:
    illegal_no_risk = check_result("profitability-decline").model_copy(
        update={
            "status": CheckStatus.NO_RISK,
            "missing_evidence": (),
            "decision_summary": "No evidence was treated as no risk.",
        }
    )
    with pytest.raises(ValueError, match=r"source status|missing required Evidence"):
        validate_accepted_investigation_results(
            snapshot=snapshot(),
            check_catalog=CHECK_CATALOG,
            agent_results=_replace_check(
                _agent_results(), "profitability-decline", illegal_no_risk
            ),
        )

    incomplete = tuple(
        item.model_copy(
            update={
                "task_ids": tuple(
                    task_id for task_id in item.task_ids if task_id != "check:profitability-decline"
                ),
                "check_results": tuple(
                    check
                    for check in item.check_results
                    if check.check_id != "profitability-decline"
                ),
            }
        )
        for item in _agent_results()
    )
    with pytest.raises(ValueError, match="missing terminal checks"):
        validate_accepted_investigation_results(
            snapshot=snapshot(),
            check_catalog=CHECK_CATALOG,
            agent_results=incomplete,
        )


def test_final_gate_rejects_duplicate_risk_ids_across_agents() -> None:
    target = snapshot()
    submodules = tuple(
        item.model_copy(
            update={
                "availability": "available",
                "completeness": "complete",
                "facts": {"registration_status": "存续"},
                "evidence_ids": ("ev-registration",),
                "unresolved_gap_ids": (),
            }
        )
        if item.submodule_id in {"registration", "registration_changes"}
        else item
        for item in target.submodules
    )
    target = target.model_copy(update={"submodules": submodules})
    fact = FactEvidenceRef(
        evidence_id="ev-registration",
        fact_path="governance.registration.registration_status",
        summary="工商登记证据",
    )

    def risky(check_id: str) -> CheckResult:
        risk = RiskItem(
            risk_id="risk-duplicate",
            check_id=check_id,
            title="Duplicate risk identity",
            status=CheckStatus.RISK,
            risk_class=RiskClass.ATTENTION,
            severity=Severity.MEDIUM,
            conclusion="A risk was identified.",
            evidence_ids=(fact.evidence_id,),
            confidence=0.8,
        )
        return check_result(check_id).model_copy(
            update={
                "status": CheckStatus.RISK,
                "decision_summary": "Risk found.",
                "risk_items": (risk,),
                "fact_evidence_refs": (fact,),
                "missing_evidence": (),
                "confidence": 0.8,
            }
        )

    duplicated = _replace_check(
        _replace_check(
            _agent_results(),
            "registration-status-normal",
            risky("registration-status-normal"),
        ),
        "registration-change-anomaly",
        risky("registration-change-anomaly"),
    )
    with pytest.raises(ValueError, match="duplicate risk ids"):
        validate_accepted_investigation_results(
            snapshot=target,
            check_catalog=CHECK_CATALOG,
            agent_results=duplicated,
        )
