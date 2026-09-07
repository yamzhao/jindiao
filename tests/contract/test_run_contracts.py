from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from jindiao.contracts.entities import EnterpriseInput
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
        enterprise=EnterpriseInput(company_name="示例科技有限公司"),
        mode=OrchestrationMode.MULTI,
        execution_profile=ExecutionProfile.ATTACHED,
    )
    assert request.mode is OrchestrationMode.MULTI
    assert request.execution_profile is ExecutionProfile.ATTACHED

    with pytest.raises(ValidationError):
        RunCreateRequest(
            enterprise=EnterpriseInput(company_name="示例科技有限公司"),
            mode=cast(OrchestrationMode, "parallel"),
        )


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
