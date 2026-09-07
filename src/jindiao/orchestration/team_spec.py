"""openJiuwen AgentTeams blueprint for enterprise due diligence."""

from __future__ import annotations

import json

from openjiuwen.agent_teams.models import ModelRouterConfig
from openjiuwen.agent_teams.models.pool import ModelPoolEntry
from openjiuwen.agent_teams.paths import configure_global_skills_dir
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    LeaderSpec,
    StorageSpec,
    TeamAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.schema.team import TeamMemberSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, TeamModelConfig

from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration.agent_team_model import (
    AGENT_TEAM_BUDGETED_MODEL_PROVIDER,
    register_agent_team_model_client,
)
from jindiao.orchestration.react_model import model_client_provider
from jindiao.paths import project_root
from jindiao.prompts import PromptBundle, load_prompt_bundle

_SKILLS_ROOT = project_root() / "skills"

_MEMBERS = (
    (
        "corporate-agent",
        "Corporate Investigator",
        "corporate",
        "Investigates registration, ownership, governance and control.",
    ),
    (
        "judicial-compliance-agent",
        "Judicial and Compliance Investigator",
        "judicial-compliance",
        "Investigates litigation, enforcement, dishonesty, penalties and compliance.",
    ),
    (
        "financial-operations-agent",
        "Financial and Operations Investigator",
        "financial-operations",
        "Investigates profitability, solvency, cash flow, revenue and employment.",
    ),
    (
        "related-peer-agent",
        "Related and Peer Investigator",
        "related-peer",
        "Investigates related parties, control relationships and peer benchmarks.",
    ),
    (
        "reviewer-agent",
        "Independent Evidence Reviewer",
        "reviewer",
        "Reviews identity, evidence support, time, amount, status, duplication and conflicts.",
    ),
)

_SPECIALIST_TOOLS = [
    "read_assigned_snapshot_context",
    "submit_check_result",
]

_ROLE_PERMISSIONS = {
    "leader": ["submit_check_assignments", "read_investigation_progress"],
    "corporate-agent": _SPECIALIST_TOOLS,
    "judicial-compliance-agent": _SPECIALIST_TOOLS,
    "financial-operations-agent": _SPECIALIST_TOOLS,
    "related-peer-agent": _SPECIALIST_TOOLS,
    "reviewer-agent": ["read_check_submissions", "submit_review"],
}


class _BoundedInvestigationAgentSpec(DeepAgentSpec):  # type: ignore[misc]
    """Keep AgentTeams coordination while removing non-investigation abilities.

    The AgentTeams configurator in the supported openJiuwen version injects a
    local ``sys_operation`` and team Skill/workspace rails after reading the
    blueprint, even when ``enable_sys_operation`` is false.  Investigation
    members must only see their frozen-snapshot business tools plus native
    task/message coordination, so enforce that boundary at the final capability
    resolution point.
    """

    def resolve_parts(self, context: object | None = None) -> object:
        from openjiuwen.agent_teams.rails.builtin_elements import (
            SKILL_USE,
            SYS_OPERATION,
        )
        from openjiuwen.agent_teams.rails.elements import (
            TEAM_SKILL_USE,
            TEAM_WORKSPACE,
        )

        forbidden_rails = {
            SYS_OPERATION,
            SKILL_USE,
            TEAM_SKILL_USE,
            TEAM_WORKSPACE,
        }
        sanitized = self.model_copy(
            update={
                "sys_operation": None,
                "enable_sys_operation": False,
                "skills": [],
                "enable_skill_discovery": False,
                "enable_task_loop": False,
                "rails": [
                    rail
                    for rail in self.rails or []
                    if getattr(rail, "type", None) not in forbidden_rails
                ],
            }
        )
        return DeepAgentSpec.resolve_parts(sanitized, context)


def _agent_template(
    agent_id: str | None,
    description: str,
    *,
    tools: list[BuiltinToolSpec] | None = None,
    model: TeamModelConfig | None = None,
) -> DeepAgentSpec:
    return _BoundedInvestigationAgentSpec(
        card=(
            AgentCard(id=agent_id, name=agent_id, description=description)
            if agent_id is not None
            else None
        ),
        model=model,
        system_prompt=(
            "Return only structured task artifacts. Never set the final score or decision, "
            "and never reveal private reasoning."
        ),
        tools=list(tools or []),
        max_iterations=24,
        enable_task_planning=False,
        enable_sys_operation=False,
        restrict_to_sandbox=True,
    )


def build_due_diligence_team_spec(
    *,
    team_name: str,
    model_name: str,
    max_review_rounds: int,
    model_provider: str | None = None,
    model_base_url: str | None = None,
    model_api_key: str | None = None,
    model_temperature: float = 0,
    model_timeout_seconds: int = 300,
    tianyancha_annual_report_enabled: bool = False,
    tianyancha_annual_report_lookback_years: int = 5,
    tianyancha_annual_report_timeout_seconds: int = 15,
    prompt_bundle: PromptBundle | None = None,
    investigation_runtime_key: str | None = None,
) -> TeamAgentSpec:
    """Build a serializable, bounded AgentTeams configuration."""

    configure_global_skills_dir(_SKILLS_ROOT)
    # These source-specific settings are intentionally accepted for migration
    # compatibility, but acquisition DeepSearch is no longer part of this team.
    del (
        tianyancha_annual_report_enabled,
        tianyancha_annual_report_lookback_years,
        tianyancha_annual_report_timeout_seconds,
    )

    model_router = _model_router(
        model_name=model_name,
        model_provider=model_provider,
        model_base_url=model_base_url,
        model_api_key=model_api_key,
        model_temperature=model_temperature,
        model_timeout_seconds=model_timeout_seconds,
    )
    prompts = prompt_bundle or load_prompt_bundle()
    leader_prompt = _role_prompt(prompts, "leader")

    runtime_model_pool = _runtime_model_pool(
        runtime_key=investigation_runtime_key,
        member_names=("leader", *(item[0] for item in _MEMBERS)),
        model_name=model_name,
        model_provider=model_provider,
        model_base_url=model_base_url,
        model_api_key=model_api_key,
        model_temperature=model_temperature,
        model_timeout_seconds=model_timeout_seconds,
    )
    runtime_model_names = {
        str(item.metadata["member_name"]): item.model_name for item in runtime_model_pool
    }
    members = [
        TeamMemberSpec(
            member_name=member_name,
            display_name=display_name,
            desc=description,
            prompt=_role_prompt(prompts, role),
            model_name=runtime_model_names.get(member_name, model_name or None),
        )
        for member_name, display_name, role, description in _MEMBERS
    ]
    runtime_tools = (
        [
            BuiltinToolSpec(
                type="jindiao.investigation_runtime_tools",
                params={"runtime_key": investigation_runtime_key},
            )
        ]
        if investigation_runtime_key
        else []
    )
    if investigation_runtime_key:
        model_router = None
    agents = {
        "leader": _agent_template(
            "jindiao-leader-template",
            "Due diligence leader",
            tools=runtime_tools,
        ),
        "teammate": _agent_template(
            None,
            "Due diligence specialist",
            tools=runtime_tools,
        ),
    }
    return TeamAgentSpec(
        team_name=team_name,
        agents=agents,
        leader=LeaderSpec(
            member_name="leader",
            display_name="Due Diligence Leader",
            desc="Plans bounded capability-aware investigations and targeted repairs.",
            prompt=leader_prompt,
            model_name=runtime_model_names.get("leader", model_name or None),
        ),
        predefined_members=members,
        model_router=model_router,
        model_pool=runtime_model_pool,
        model_pool_strategy="by_model_name" if runtime_model_pool else "round_robin",
        team_mode="predefined",
        dispatch_mode="scheduled",
        spawn_mode="inprocess",
        # The versioned Reviewer member and submission blackboard are the
        # authoritative review loop. Framework task verification would create
        # a second, unowned in_review lifecycle that cannot converge.
        enable_task_verification=False,
        default_max_review_rounds=max_review_rounds,
        transport=TransportSpec(type="inprocess"),
        # AgentTeams members execute concurrently. openJiuwen 0.1.17 maps
        # ``memory`` to a shared single-connection SQLite database, where
        # overlapping member transactions can hide or roll back another
        # member's durable task/message writes. File-backed SQLite uses the
        # framework's serialized writer and independent reader pool.
        storage=StorageSpec(type="sqlite"),
        metadata={
            "application": "jindiao",
            "contract": "fixed-check-agent-result-v1",
            "private_reasoning_logging": False,
            "dispatch_mode": "scheduled",
            "role_permissions": _ROLE_PERMISSIONS,
            "prompt_versions": {
                role: prompts.investigation(role).prompt_version
                for role in (
                    "leader",
                    "corporate",
                    "judicial-compliance",
                    "financial-operations",
                    "related-peer",
                    "reviewer",
                )
            },
        },
        language="zh",
    )


def _role_prompt(bundle: PromptBundle, role: str) -> str:
    base = bundle.investigation(role).system_prompt
    if role in {"leader", "reviewer"}:
        definitions = tuple(item for item in CHECK_CATALOG.checks if item.enabled)
    else:
        definitions = tuple(
            item for item in CHECK_CATALOG.checks if item.enabled and item.owner_role == role
        )
    catalog_json = json.dumps(
        [item.model_dump(mode="json") for item in definitions],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"{base}\n\n固定核查目录: {catalog_json}"


def _model_router(
    *,
    model_name: str,
    model_provider: str | None,
    model_base_url: str | None,
    model_api_key: str | None,
    model_temperature: float,
    model_timeout_seconds: int,
) -> ModelRouterConfig | None:
    connection = (model_provider, model_base_url, model_api_key)
    if not any(connection):
        return None
    if not model_name.strip() or not all(value and value.strip() for value in connection):
        raise ValueError(
            "model_name, model_provider, model_base_url and model_api_key are all required "
            "for the model-backed AgentTeams runtime"
        )
    return ModelRouterConfig(
        api_base_url=model_base_url or "",
        api_key=model_api_key or "",
        api_provider=model_provider or "",
        model_names=[model_name],
        metadata={
            "client": {
                "timeout": model_timeout_seconds,
                "stream_first_chunk_timeout": model_timeout_seconds,
                "stream_idle_timeout": model_timeout_seconds,
            },
            "request": {"temperature": model_temperature},
        },
    )


def _runtime_model_pool(
    *,
    runtime_key: str | None,
    member_names: tuple[str, ...],
    model_name: str,
    model_provider: str | None,
    model_base_url: str | None,
    model_api_key: str | None,
    model_temperature: float,
    model_timeout_seconds: int,
) -> list[ModelPoolEntry]:
    if not runtime_key:
        return []
    connection = (model_provider, model_base_url, model_api_key)
    if not model_name.strip() or not all(value and value.strip() for value in connection):
        raise ValueError(
            "runtime-bound AgentTeams requires model_name, model_provider, "
            "model_base_url and model_api_key"
        )
    register_agent_team_model_client()
    return [
        ModelPoolEntry(
            model_name=f"{model_name}::{member_name}",
            api_key=model_api_key or "",
            api_base_url=model_base_url or "",
            api_provider=AGENT_TEAM_BUDGETED_MODEL_PROVIDER,
            metadata={
                "member_name": member_name,
                "client": {
                    "timeout": model_timeout_seconds,
                    "stream_first_chunk_timeout": model_timeout_seconds,
                    "stream_idle_timeout": model_timeout_seconds,
                    "runtime_key": runtime_key,
                    "member_name": member_name,
                    "inner_provider": model_client_provider(model_provider or ""),
                    "inner_model_name": model_name,
                    "upstream_provider": model_provider,
                },
                "request": {"temperature": model_temperature},
            },
        )
        for member_name in member_names
    ]


__all__ = ["build_due_diligence_team_spec"]
