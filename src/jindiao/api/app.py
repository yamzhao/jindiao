"""FastAPI Run resources and due-diligence protocol adapters."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from jindiao.application.errors import JindiaoError, error_to_record, http_status_for_error
from jindiao.application.run_coordinator import RunCoordinator
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.results import DueDiligenceRequest, OrchestrationMode
from jindiao.contracts.runs import (
    ExecutionProfile,
    ExecutionProfileConfig,
    RunCreateRequest,
)
from jindiao.observability.artifacts import AgentArtsSessionStore
from jindiao.observability.run_store import JsonlEventStore, JsonRunRepository
from jindiao.paths import project_root
from jindiao.scenarios import ScenarioRepository

from .reporting_demo import router as reporting_demo_router
from .sse import stream_sse_events

RESULT_PATH = "/api/v1/due-diligence/result"
RUNS_PATH = "/api/v2/due-diligence/runs"


def create_app(
    *,
    service: DueDiligenceService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    runtime_settings = settings or (service.settings if service is not None else Settings())
    artifact_store = (
        AgentArtsSessionStore(runtime_settings.artifact_root, shared=True)
        if runtime_settings.shared_storage_backend in {"session", "sfs"}
        else None
    )
    runtime_service = service or DueDiligenceService(
        settings=runtime_settings,
        scenarios=ScenarioRepository(runtime_settings.mock_data_root / "scenarios"),
        risk_rules_path=project_root() / "config/risk-rules-v1.json",
        artifact_store=artifact_store,
    )
    app = FastAPI(title="Jindiao Enterprise Due Diligence", version="0.1.0")
    app.state.due_diligence_service = runtime_service
    repository = None
    event_store = None
    if runtime_settings.shared_storage_backend != "memory":
        state_root = runtime_settings.artifact_root / "run-state"
        repository = JsonRunRepository(state_root / "runs")
        event_store = JsonlEventStore(
            state_root / "events",
            max_queue=runtime_settings.max_event_queue,
        )
    profile = ExecutionProfile(runtime_settings.execution_profile)
    app.state.run_coordinator = RunCoordinator(
        runtime_service,
        repository=repository,
        event_store=event_store,
        profile_config=ExecutionProfileConfig(
            profile=profile,
            allow_background_tasks=(
                profile is ExecutionProfile.DETACHED and runtime_settings.detached_probe_passed
            ),
            shared_storage=runtime_settings.shared_storage_backend in {"session", "sfs"},
            session_required=runtime_settings.shared_storage_backend == "session",
            max_event_queue=runtime_settings.max_event_queue,
        ),
    )
    runtime_service.attach_coordinator(app.state.run_coordinator)
    app.include_router(reporting_demo_router)

    @app.exception_handler(JindiaoError)
    async def handle_jindiao_error(_: Request, error: JindiaoError) -> JSONResponse:
        record = error_to_record(error)
        return JSONResponse(
            status_code=http_status_for_error(error),
            content={"error": record.model_dump(mode="json")},
        )

    @app.post(RESULT_PATH, response_model=None)
    async def result_endpoint(
        body: DueDiligenceRequest,
        request: Request,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
    ) -> Response:
        accept = request.headers.get("accept", "application/json").casefold()
        wants_sse = "text/event-stream" in accept
        wants_json = "application/json" in accept or "*/*" in accept
        if not wants_sse and not wants_json:
            raise HTTPException(status_code=406, detail="Accept must allow JSON or SSE")
        due_diligence: DueDiligenceService = request.app.state.due_diligence_service
        if wants_sse:
            return EventSourceResponse(
                stream_sse_events(
                    due_diligence.stream(body, mode=mode),
                    disconnect_check=request.is_disconnected,
                )
            )
        result = await due_diligence.run(body, mode=mode)
        return JSONResponse(content=result.model_dump(mode="json"))

    def principal(request: Request) -> tuple[str, str | None]:
        return (
            request.headers.get("x-hw-agentgateway-user-id", "anonymous"),
            request.headers.get("x-hw-agentarts-session-id"),
        )

    @app.post(RUNS_PATH, status_code=202, response_model=None)
    async def create_run_endpoint(
        body: RunCreateRequest,
        request: Request,
        response: Response,
    ) -> Response:
        owner_id, session_id = principal(request)
        coordinator: RunCoordinator = request.app.state.run_coordinator
        resource = await coordinator.create(
            body,
            owner_id=owner_id,
            idempotency_key=request.headers.get("idempotency-key"),
            session_id=session_id,
        )
        coordinator.start(resource.run_id)
        return JSONResponse(
            status_code=202,
            content=resource.model_dump(mode="json"),
            headers={"Location": resource.links["self"].href},
        )

    @app.get("/api/v2/due-diligence/runs/{run_id}", response_model=None)
    async def get_run_endpoint(run_id: str, request: Request) -> Response:
        owner_id, _ = principal(request)
        coordinator: RunCoordinator = request.app.state.run_coordinator
        resource = await coordinator.get(
            run_id, owner_id=owner_id, session_id=principal(request)[1]
        )
        return JSONResponse(content=resource.model_dump(mode="json"))

    @app.get("/api/v2/due-diligence/runs/{run_id}/events", response_model=None)
    async def run_events_endpoint(run_id: str, request: Request) -> Response:
        owner_id, _ = principal(request)
        raw_after = request.query_params.get("after") or request.headers.get("last-event-id") or "0"
        try:
            after = max(0, int(raw_after))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="after must be an integer") from error
        coordinator: RunCoordinator = request.app.state.run_coordinator
        # Authorize before committing HTTP 200/SSE headers. Exceptions raised by
        # the lazy event iterator can no longer become a normal JSON 404 response.
        await coordinator.get(run_id, owner_id=owner_id, session_id=principal(request)[1])
        return EventSourceResponse(
            stream_sse_events(
                coordinator.subscribe(
                    run_id, owner_id=owner_id, session_id=principal(request)[1], after=after
                ),
                disconnect_check=request.is_disconnected,
            ),
            ping=15,
        )

    @app.get("/api/v2/due-diligence/runs/{run_id}/result", response_model=None)
    async def run_result_endpoint(run_id: str, request: Request) -> Response:
        owner_id, _ = principal(request)
        coordinator: RunCoordinator = request.app.state.run_coordinator
        resource = await coordinator.get(
            run_id, owner_id=owner_id, session_id=principal(request)[1]
        )
        result = await coordinator.get_result(
            run_id, owner_id=owner_id, session_id=principal(request)[1]
        )
        if result is None:
            if resource.error is not None:
                return JSONResponse(
                    status_code=500,
                    content={"error": resource.error.model_dump(mode="json")},
                )
            return JSONResponse(
                status_code=202,
                content={
                    "status": resource.status.value,
                    "run_id": run_id,
                    "links": {
                        key: value.model_dump(mode="json") for key, value in resource.links.items()
                    },
                },
            )
        return JSONResponse(content=result.model_dump(mode="json"))

    @app.post("/api/v2/due-diligence/runs/{run_id}/cancel", response_model=None)
    async def cancel_run_endpoint(run_id: str, request: Request) -> Response:
        owner_id, _ = principal(request)
        coordinator: RunCoordinator = request.app.state.run_coordinator
        resource = await coordinator.cancel(
            run_id, owner_id=owner_id, session_id=principal(request)[1]
        )
        return JSONResponse(content=resource.model_dump(mode="json"))

    @app.post("/invocations", response_model=None)
    async def invocations_endpoint(request: Request) -> Response:
        accept = request.headers.get("accept", "application/json").casefold()
        if not any(value in accept for value in ("text/event-stream", "application/json", "*/*")):
            raise HTTPException(status_code=406, detail="Accept must allow JSON or SSE")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError) as error:
            raise HTTPException(
                status_code=422, detail="Request body must be valid JSON"
            ) from error
        if isinstance(payload, dict) and isinstance(payload.get("input"), dict):
            payload = payload["input"]
        try:
            body = RunCreateRequest.model_validate(payload)
        except ValidationError as error:
            # Avoid reflecting caller-provided secrets in validation input fields.
            raise HTTPException(status_code=422, detail="Invalid Run request") from error
        owner_id, session_id = principal(request)
        coordinator: RunCoordinator = request.app.state.run_coordinator
        resource = await coordinator.create(body, owner_id=owner_id, session_id=session_id)
        coordinator.start(resource.run_id)
        if "text/event-stream" in accept:
            return EventSourceResponse(
                stream_sse_events(
                    coordinator.subscribe(
                        resource.run_id, owner_id=owner_id, session_id=session_id
                    ),
                    disconnect_check=request.is_disconnected,
                ),
                ping=15,
            )
        return JSONResponse(status_code=202, content=resource.model_dump(mode="json"))

    @app.get("/ping", response_model=None)
    async def ping_endpoint(request: Request) -> Response:
        coordinator: RunCoordinator = request.app.state.run_coordinator
        status = (
            "HealthyBusy"
            if coordinator.active_runs(profile=ExecutionProfile.DETACHED)
            else "Healthy"
        )
        return JSONResponse(content={"status": status})

    return app


app = create_app()

__all__ = ["RESULT_PATH", "RUNS_PATH", "app", "create_app"]
