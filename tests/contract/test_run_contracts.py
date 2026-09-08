from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from jindiao.contracts.events import LifecycleEventType, RunEvent
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.contracts.runs import (
    ActorView,
    ExecutionProfile,
    ExecutionProfileConfig,
    RunCreateRequest,
    RunLink,
    RunResource,
    RunStage,
)


def test_run_create_request_requires_supported_mode_and_safe_profile() -> None:
    request = RunCreateRequest(
        customerName="示例科技有限公司",
        mode=OrchestrationMode.MULTI,
        execution_profile=ExecutionProfile.ATTACHED,
    )
    assert request.mode is OrchestrationMode.MULTI
    assert request.execution_profile is ExecutionProfile.ATTACHED

    with pytest.raises(ValidationError):
        RunCreateRequest(
            customerName="示例科技有限公司",
            mode=cast(OrchestrationMode, "parallel"),
        )


def test_flat_form_maps_units_and_preserves_business_meaning() -> None:
    request = RunCreateRequest.model_validate(
        {
            "customerName": "  示例科技有限公司  ",
            "uscc": " abc123 ",
            "product": " 流动资金贷款 ",
            "amount": 1.0001,
            "term": 12,
            "manager": " 王某某 ",
            "branch": " 城东支行 ",
        }
    )
    assert request.customerName == "示例科技有限公司" and request.uscc == "ABC123"
    internal = request.to_execution_request()
    assert internal.enterprise.company_name == request.customerName
    assert internal.enterprise.unified_social_credit_code == "ABC123"
    assert internal.enterprise.region is None
    plan = internal.business_context
    assert plan.application_amount == 10001
    assert plan.application_term_months == 12
    assert plan.business_product == "流动资金贷款"
    assert plan.customer_manager == "王某某" and plan.reporting_org == "城东支行"
    assert plan.application_type is None  # Product is not the application occurrence type.
    assert internal.language == "zh-CN" and not internal.allow_degraded_mock
    assert internal.skill_feedback is None
    # BFF validates and serializes in form units; conversion occurs only at execution entry.
    forwarded = RunCreateRequest.model_validate_json(request.model_dump_json())
    assert forwarded.amount == 1.0001
    assert forwarded.to_execution_request().business_context.application_amount == 10001


def test_empty_optional_fields_and_either_identifier() -> None:
    by_code = RunCreateRequest(uscc=" abC ", customerName="  ", product="", manager=" ", branch="")
    assert by_code.uscc == "ABC" and by_code.customerName is None
    assert by_code.product is None and by_code.manager is None and by_code.branch is None
    assert by_code.to_execution_request().business_context.application_amount is None
    omitted = RunCreateRequest(customerName="企业")
    explicit = RunCreateRequest(
        customerName="企业",
        amount=None,
        term=None,
        product=None,
        manager=None,
        branch=None,
        uscc=None,
    )
    assert omitted == explicit


@pytest.mark.parametrize(
    "amount,term,expected_amount,expected_term",
    [
        ("2", "12", 2.0, 12),
        (" 1.0001 ", " 12 ", 1.0001, 12),
        ("5000.00", "012", 5000.0, 12),
        ("+2.5", "+12", 2.5, 12),
        (".5", "6", 0.5, 6),
        ("2e3", "12", 2000.0, 12),
        ("2", 12, 2.0, 12),
        (2, "12", 2.0, 12),
        (None, "12", None, 12),
        ("2", None, 2.0, None),
    ],
)
def test_flat_form_normalizes_numeric_strings(
    amount: object, term: object, expected_amount: float | None, expected_term: int | None
) -> None:
    request = RunCreateRequest.model_validate(
        {"customerName": "企业", "amount": amount, "term": term}
    )
    numeric = RunCreateRequest(customerName="企业", amount=expected_amount, term=expected_term)
    assert request == numeric
    assert request.model_dump(mode="json") == numeric.model_dump(mode="json")
    assert request.to_execution_request() == numeric.to_execution_request()
    assert request.amount is None or type(request.amount) is float
    assert request.term is None or type(request.term) is int


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"customerName": " ", "uscc": ""},
        {"customerName": 123},
        {"amount": 0},
        {"amount": -1},
        {"amount": True},
        {"amount": ""},
        {"amount": "  "},
        {"amount": "abc"},
        {"amount": "5,000"},
        {"amount": "2万元"},
        {"amount": "1_000"},
        {"amount": "0x10"},
        {"amount": "0"},
        {"amount": "-2"},
        {"amount": "NaN"},
        {"amount": "Infinity"},
        {"amount": "1e309"},
        {"amount": "1e308"},
        {"amount": "1e-999"},
        {"amount": float("inf")},
        {"amount": float("nan")},
        {"amount": 1e308},
        {"term": 0},
        {"term": -1},
        {"term": 1.5},
        {"term": 12.0},
        {"term": True},
        {"term": ""},
        {"term": "  "},
        {"term": "abc"},
        {"term": "0"},
        {"term": "-12"},
        {"term": "1.5"},
        {"term": "12.0"},
        {"term": "1e2"},
        {"term": "12个月"},
        {"term": "1_2"},
        {"term": "Infinity"},
        {"product": ["流动资金贷款"]},
        {"manager": {}},
    ],
)
def test_flat_form_rejects_invalid_values(fields: dict[str, Any]) -> None:
    payload = {"customerName": "企业", **fields} if fields else fields
    with pytest.raises(ValidationError):
        RunCreateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "name",
    [
        "enterprise",
        "business_context",
        "company_name",
        "region",
        "unified_social_credit_code",
        "application_amount",
        "language",
        "skill_feedback",
        "allow_degraded_mock",
    ],
)
def test_flat_form_rejects_removed_names_even_alongside_new_fields(name: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunCreateRequest.model_validate({"customerName": "企业", name: None})


def test_flat_run_schema_contains_only_form_and_necessary_control_fields() -> None:
    schema = RunCreateRequest.model_json_schema()
    assert set(schema["properties"]) == {
        "customerName",
        "uscc",
        "product",
        "amount",
        "term",
        "manager",
        "branch",
        "mode",
        "report_as_of",
        "execution_profile",
        "session_id",
        "scenario_id",
    }
    assert schema["additionalProperties"] is False
    assert "万元" in schema["properties"]["amount"]["description"]


def test_numeric_string_input_schema_preserves_numeric_serialization() -> None:
    validation = RunCreateRequest.model_json_schema(mode="validation")["properties"]
    serialization = RunCreateRequest.model_json_schema(mode="serialization")["properties"]
    for field, numeric_type in (("amount", "number"), ("term", "integer")):
        assert {branch["type"] for branch in validation[field]["anyOf"]} == {
            numeric_type,
            "string",
            "null",
        }
        assert {branch["type"] for branch in serialization[field]["anyOf"]} == {
            numeric_type,
            "null",
        }


def test_detached_profile_requires_shared_storage_and_background_execution() -> None:
    with pytest.raises(ValidationError):
        ExecutionProfileConfig(profile=ExecutionProfile.DETACHED)

    config = ExecutionProfileConfig(
        profile=ExecutionProfile.DETACHED,
        allow_background_tasks=True,
        shared_storage=True,
    )
    assert config.profile is ExecutionProfile.DETACHED


def test_run_resource_has_frontend_progress_and_links() -> None:
    now = datetime(2026, 9, 6, tzinfo=UTC)
    resource = RunResource(
        request_id="req-1",
        run_id="run-1",
        owner_id="user-1",
        mode=OrchestrationMode.SINGLE,
        status=RunStatus.ACCEPTED,
        stage=RunStage.ACCEPTED,
        created_at=now,
        links={"self": RunLink(href="/api/v2/due-diligence/runs/run-1")},
    )
    assert resource.progress.completed == 0
    assert resource.links["self"].href.endswith("run-1")


def test_run_event_exposes_version_actor_stage_and_stable_event_id() -> None:
    event = RunEvent(
        event_type=cast(LifecycleEventType, "run.started"),
        request_id="req-1",
        run_id="run-1",
        sequence=4,
        occurred_at=datetime(2026, 9, 6, tzinfo=UTC),
        stage=RunStage.INVESTIGATION,
        actor=ActorView(kind="agent", id="investigator", role="specialist"),
        payload={"status": "running"},
    )
    assert event.schema_version == 1
    assert event.event_id == "run-1:4"
    assert event.actor.id == "investigator"


def test_public_event_removes_private_payload_keys() -> None:
    event = RunEvent(
        event_type=cast(LifecycleEventType, "run.started"),
        request_id="req-1",
        run_id="run-1",
        sequence=1,
        occurred_at=datetime(2026, 9, 6, tzinfo=UTC),
        payload={"status": "running", "reasoning": "do not expose"},
    )
    assert "reasoning" not in event.payload
