"""Application-level role boundaries for the three specialist agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from jindiao.application.context import RunContext
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.entities import ResolvedSubject
from jindiao.orchestration.base import (
    CancellationToken,
    check_cancellation,
    investigate_with_cancellation,
    require_deterministic_harness,
)

if TYPE_CHECKING:
    from jindiao.orchestration.base import DomainInvestigation


class DomainToolset(Protocol):
    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation: ...


class SpecialistAgent:
    """Compatibility facade for deterministic domain-artifact fixtures only."""

    formal_agent_run = False
    agent_id: str
    allowed_domains: frozenset[str]
    tool_whitelist: frozenset[str]

    def __init__(self, *, toolset: DomainToolset) -> None:
        self._toolset = toolset

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
        if domain not in self.allowed_domains:
            raise AgentExecutionError(
                "specialist attempted an unauthorized domain",
                details={"agent_id": self.agent_id, "domain": domain},
            )
        return await investigate_with_cancellation(
            self._toolset, context, subject, domain, cancellation_token
        )


class GovernanceAgent(SpecialistAgent):
    agent_id = "governance-agent"
    allowed_domains = frozenset({"governance"})
    tool_whitelist = frozenset({"governance_snapshot"})


class JudicialComplianceAgent(SpecialistAgent):
    agent_id = "judicial-compliance-agent"
    allowed_domains = frozenset({"judicial"})
    tool_whitelist = frozenset({"judicial_snapshot", "compliance_snapshot"})


class OperationsPeerAgent(SpecialistAgent):
    agent_id = "operations-peer-agent"
    allowed_domains = frozenset({"operations", "peers"})
    tool_whitelist = frozenset({"operations_snapshot", "peers_snapshot"})


__all__ = [
    "GovernanceAgent",
    "JudicialComplianceAgent",
    "OperationsPeerAgent",
    "SpecialistAgent",
]
