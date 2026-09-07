from __future__ import annotations

from openjiuwen.harness.schema.build_context import BuildContext
from test_multi_investigator_team import ROLE_AGENTS, snapshot

from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration import BudgetLedger, RunBudget
from jindiao.orchestration.investigation_team_tools import (
    InvestigationTeamState,
    build_investigation_runtime_tools,
    register_investigation_runtime_tool_provider,
    register_investigation_team_state,
    unregister_investigation_team_state,
)
from jindiao.orchestration.react_model import JINDIAO_OPENAI_COMPATIBLE_PROVIDER
from jindiao.orchestration.team_spec import build_due_diligence_team_spec


def _nested_tool_properties(schema: dict[str, object], field: str) -> dict[str, object]:
    properties = schema["properties"]
    assert isinstance(properties, dict)
    field_schema = properties[field]
    assert isinstance(field_schema, dict)
    ref = field_schema["$ref"]
    definition = str(ref).rsplit("/", maxsplit=1)[-1]
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    model_schema = definitions[definition]
    assert isinstance(model_schema, dict)
    nested_properties = model_schema["properties"]
    assert isinstance(nested_properties, dict)
    return nested_properties


def _state() -> InvestigationTeamState:
    return InvestigationTeamState.create(
        runtime_key="runtime:tools",
        run_id="run-agent-teams-tools",
        snapshot=snapshot(),
        check_catalog=CHECK_CATALOG,
        role_agent_ids=ROLE_AGENTS,
        reviewer_agent_id="reviewer-agent",
        prompt_versions_by_member={
            "leader": "investigation-core-v1+leader-v3",
            "corporate-agent": "investigation-core-v1+corporate-v3",
            "judicial-compliance-agent": ("investigation-core-v1+judicial-compliance-v3"),
            "financial-operations-agent": ("investigation-core-v1+financial-operations-v3"),
            "related-peer-agent": "investigation-core-v1+related-peer-v3",
            "reviewer-agent": "investigation-core-v1+reviewer-v3",
        },
        budget_ledger=BudgetLedger(
            RunBudget(
                max_tool_calls=100,
                max_concurrency=4,
                timeout_seconds=30,
                max_repair_rounds=2,
            )
        ),
    )


def test_runtime_provider_binds_exact_minimum_tools_by_member() -> None:
    state = _state()
    register_investigation_runtime_tool_provider()
    register_investigation_team_state(state)
    try:
        leader = build_investigation_runtime_tools(
            {"runtime_key": state.runtime_key},
            BuildContext(member_name="leader", role="leader"),
        )
        specialist = build_investigation_runtime_tools(
            {"runtime_key": state.runtime_key},
            BuildContext(member_name="financial-operations-agent", role="teammate"),
        )
        reviewer = build_investigation_runtime_tools(
            {"runtime_key": state.runtime_key},
            BuildContext(member_name="reviewer-agent", role="teammate"),
        )

        assert {item.card.name for item in leader} == {
            "submit_check_assignments",
            "read_investigation_progress",
        }
        assert {item.card.name for item in specialist} == {
            "read_assigned_snapshot_context",
            "submit_check_result",
        }
        assert {item.card.name for item in reviewer} == {
            "read_check_submissions",
            "submit_review",
        }
        leader_submit = next(
            item for item in leader if item.card.name == "submit_check_assignments"
        )
        specialist_submit = next(
            item for item in specialist if item.card.name == "submit_check_result"
        )
        reviewer_submit = next(item for item in reviewer if item.card.name == "submit_review")
        assert leader_submit.card.input_params["properties"] == {}
        assert "snapshot_id" not in _nested_tool_properties(
            specialist_submit.card.input_params,
            "result",
        )
        assert "snapshot_id" not in _nested_tool_properties(
            reviewer_submit.card.input_params,
            "review",
        )
    finally:
        unregister_investigation_team_state(state.runtime_key)


def test_runtime_provider_rejects_unregistered_or_unknown_member() -> None:
    state = _state()
    register_investigation_team_state(state)
    try:
        try:
            build_investigation_runtime_tools(
                {"runtime_key": state.runtime_key},
                BuildContext(member_name="deepsearch-agent", role="teammate"),
            )
        except ValueError as error:
            assert "not authorized" in str(error)
        else:
            raise AssertionError("unknown member unexpectedly received investigation tools")
    finally:
        unregister_investigation_team_state(state.runtime_key)

    try:
        build_investigation_runtime_tools(
            {"runtime_key": state.runtime_key},
            BuildContext(member_name="leader", role="leader"),
        )
    except ValueError as error:
        assert "not registered" in str(error)
    else:
        raise AssertionError("unregistered runtime unexpectedly resolved tools")


def test_runtime_team_models_share_the_registered_budget_ledger() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-budgeted-team",
        model_name="scripted-model",
        model_provider="scripted-inner-provider",
        model_base_url="https://model.invalid/v1",
        model_api_key="test-key",
        max_review_rounds=2,
        investigation_runtime_key="runtime:budgeted-model",
    )

    assert spec.model_router is None
    assert spec.model_pool_strategy == "by_model_name"
    by_member = {item.metadata["member_name"]: item for item in spec.model_pool}
    for member_name in (
        "leader",
        "corporate-agent",
        "judicial-compliance-agent",
        "financial-operations-agent",
        "related-peer-agent",
        "reviewer-agent",
    ):
        model = by_member[member_name].to_team_model_config()
        client = model.model_client_config
        assert client.client_provider == "jindiao_budgeted_agent_team"
        assert client.runtime_key == "runtime:budgeted-model"
        assert client.inner_provider == "scripted-inner-provider"
        assert client.inner_model_name == "scripted-model"
        assert client.member_name == member_name


def test_openai_runtime_team_models_delegate_to_proxy_safe_client() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-openai-compatible-team",
        model_name="qwen-plus",
        model_provider="OpenAI",
        model_base_url="https://model.example/v1",
        model_api_key="test-key",
        max_review_rounds=2,
        investigation_runtime_key="runtime:proxy-safe-model",
    )

    for entry in spec.model_pool:
        client = entry.to_team_model_config().model_client_config
        assert client.inner_provider == JINDIAO_OPENAI_COMPATIBLE_PROVIDER
        assert client.upstream_provider == "OpenAI"
