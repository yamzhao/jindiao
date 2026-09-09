"""Opt-in local or integration-proxy feedback; release operations are CLI-only."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jindiao.application.run_coordinator import RunCoordinator
from jindiao.contracts.product import ProductResult
from jindiao.contracts.report_policy import ReportFeedbackRequest
from jindiao.contracts.results import RunStatus
from jindiao.reporting.demo_store import DemoCandidate, ReportingDemoStore
from jindiao.reporting.product_markdown import SECTION_TITLES, ProductReportView

router = APIRouter()


def _store(request: Request) -> ReportingDemoStore:
    service = request.app.state.due_diligence_service
    store: ReportingDemoStore | None = service.reporting_demo_store
    if store is None:
        raise HTTPException(503, detail="reporting_demo_disabled")
    service_settings = service.settings
    try:
        peer = ipaddress.ip_address(request.client.host) if request.client else None
        local = bool(peer and peer.is_loopback)
        # In the development Docker compose setup, the host browser reaches
        # the container through Docker's private bridge (typically 172.16/12),
        # so the peer is not literally loopback. The service is still bound to
        # host loopback; accept private bridge peers only for this dev-only demo.
        if not local and service_settings.environment == "development":
            local = bool(peer and peer.is_private)
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, detail="reporting_demo_local_only")
    if any(name.lower().startswith(("x-forwarded-", "forwarded")) for name in request.headers):
        raise HTTPException(403, detail="reporting_demo_proxy_not_supported")
    settings = service_settings
    if settings.reporting_public_enabled:
        # The integration ingress preserves Host and terminates at the real loopback peer.
        # Identity headers retain the existing Run isolation contract; they are NOT login auth.
        origin = settings.reporting_public_origin
        expected = urlsplit(origin)
        if request.headers.get("host", "").lower() != expected.netloc.lower():
            raise HTTPException(403, detail="reporting_public_invalid_host")
        if (
            request.headers.get("origin", origin) != origin
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(403, detail="reporting_demo_same_origin_only")
        owner, session = _principal(request)
        if (
            not owner.strip()
            or owner == "anonymous"
            or not session
            or not session.strip()
            or len(owner) > 128
            or len(session) > 128
        ):
            raise HTTPException(401, detail="reporting_identity_required")
        return store
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(403, detail="reporting_demo_local_only")
    origin = request.headers.get("origin")
    if origin is not None:
        allowed_origins = {str(request.base_url).rstrip("/")}
        # The local xingyao UI runs on Vite (5173) and proxies /api to the
        # loopback backend (8080). Both peers are local, so this is still a
        # same-user development flow rather than a public cross-origin API.
        parsed_origin = urlsplit(origin)
        if parsed_origin.scheme == "http" and parsed_origin.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            allowed_origins.update(
                f"http://{host}:5173" for host in ("localhost", "127.0.0.1", "[::1]")
            )
        if origin.rstrip("/") not in allowed_origins:
            raise HTTPException(403, detail="reporting_demo_same_origin_only")
    return store


def _principal(request: Request) -> tuple[str, str | None]:
    return (
        request.headers.get("x-hw-agentgateway-user-id", "anonymous"),
        request.headers.get("x-hw-agentarts-session-id"),
    )


def _response(
    candidate: DemoCandidate,
    store: ReportingDemoStore,
    *,
    reports: bool = False,
    case_id: str | None = None,
) -> dict[str, object]:
    evaluation = candidate.evaluation.model_dump(mode="json") if candidate.evaluation else None
    if evaluation is not None:
        cases = evaluation["cases"]
        if case_id is not None:
            cases = [case for case in cases if case["case_id"] == case_id]
            if not cases:
                raise HTTPException(422, detail="unknown_case_id")
        if not reports:
            cases = [
                {
                    key: value
                    for key, value in case.items()
                    if key not in {"before", "after", "diff"}
                }
                for case in cases
            ]
        evaluation["cases"] = cases
    active = store.active()
    return {
        "evolution_id": candidate.evolution_id,
        "status": candidate.status,
        "reason_codes": candidate.reason_codes,
        "source_run_id": candidate.source_run_id,
        "feedback": candidate.feedback.model_dump(mode="json"),
        "baseline_version": candidate.snapshot.binding.version,
        "candidate_version": candidate.candidate.version,
        "candidate_policy_sha256": candidate.candidate.policy_sha256,
        "evaluation_sha256": candidate.evaluation_sha256,
        "evaluation": evaluation,
        "active_revision": active.revision,
        "active_version": active.version,
        "is_active": active == candidate.candidate,
        "detail_url": f"/api/v2/skill-evolutions/{candidate.evolution_id}",
    }


@router.post("/api/v2/due-diligence/runs/{run_id}/feedback", response_model=None)
async def submit_feedback(
    run_id: str, body: ReportFeedbackRequest, request: Request
) -> JSONResponse:
    store = _store(request)
    owner, session = _principal(request)
    coordinator: RunCoordinator = request.app.state.run_coordinator
    resource = await coordinator.get(run_id, owner_id=owner, session_id=session)
    result = await coordinator.get_result(run_id, owner_id=owner, session_id=session)
    if (
        resource.status not in {RunStatus.COMPLETED, RunStatus.PARTIAL}
        or not resource.result_available
        or result is None
    ):
        raise HTTPException(409, detail="run_not_feedback_ready")
    snapshot = coordinator.service.load_report_replay(run_id)
    if snapshot is None or (
        not isinstance(result, ProductResult) and not result.meta.report_replay_available
    ):
        raise HTTPException(409, detail="run_not_replayable")
    if resource.reporting_policy != snapshot.binding:
        raise HTTPException(409, detail="run_not_replayable")
    section_ids = (
        set(SECTION_TITLES)
        if isinstance(snapshot.view, ProductReportView)
        else {item.section_id for item in snapshot.view.sections}
    )
    evidence_ids = (
        {item.id for item in snapshot.view.evidence}
        if isinstance(snapshot.view, ProductReportView)
        else {item.evidence_id for item in snapshot.view.evidence}
    )
    if not set(body.target_section_ids) <= section_ids:
        raise HTTPException(422, detail="invalid_section_reference")
    if not set(body.evidence_ids) <= evidence_ids:
        raise HTTPException(422, detail="invalid_evidence_reference")
    key = request.headers.get("idempotency-key", "")
    if not key.strip() or len(key) > 128:
        raise HTTPException(422, detail="idempotency_key_required")
    try:
        candidate, created = await run_in_threadpool(
            store.propose, snapshot, body, key=key, owner=owner, session=session
        )
        data = _response(candidate, store)
    except ValueError as error:
        message = str(error)
        if "busy" in message:
            raise HTTPException(429, detail="evaluation_busy") from error
        if "too large" in message:
            raise HTTPException(413, detail="replay_limit_exceeded") from error
        raise HTTPException(409, detail="baseline_stale_or_idempotency_conflict") from error
    except OSError as error:
        raise HTTPException(500, detail="demo_storage_failed") from error
    status_code = 201 if created else 200
    if candidate.status == "failed":
        status_code = 413 if "replay_limit_exceeded" in candidate.reason_codes else 500
    return JSONResponse(content=data, status_code=status_code)


@router.get("/api/v2/skill-evolutions/{evolution_id}", response_model=None)
async def get_evolution(
    evolution_id: str,
    request: Request,
    include: Literal["reports"] | None = None,
    case_id: str | None = None,
) -> JSONResponse:
    store = _store(request)
    try:
        candidate = store.get(evolution_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, detail="evolution_not_found") from None
    owner, session = _principal(request)
    coordinator: RunCoordinator = request.app.state.run_coordinator
    await coordinator.get(candidate.source_run_id, owner_id=owner, session_id=session)
    if candidate.owner_id != owner or candidate.session_id != session:
        raise HTTPException(404, detail="evolution_not_found")
    return JSONResponse(
        content=_response(
            candidate, store, reports=include == "reports" or case_id is not None, case_id=case_id
        )
    )
