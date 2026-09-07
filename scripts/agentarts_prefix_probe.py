"""AgentArts PREFIX_MATCH multi-path smoke probe.

Local usage::

    python scripts/agentarts_prefix_probe.py http://127.0.0.1:8080

Gateway usage (the gateway owns the ``/invocations`` prefix)::

    python scripts/agentarts_prefix_probe.py https://gateway.example \
      --custom-prefix /runtimes/RUNTIME/invocations

The application itself registers ``/api/v2/...`` and never registers a
``/runtimes/{runtime}`` prefix.  The probe prints only IDs, status codes and
event sequence metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values

from jindiao.security.tls import create_tls_context


def _join(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path


def _sse_summary(response: httpx.Response) -> dict[str, object]:
    event_types: list[str] = []
    sequences: list[int] = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if isinstance(payload, dict):
            event_types.append(str(payload.get("event_type", "unknown")))
            sequence = payload.get("sequence")
            if isinstance(sequence, int):
                sequences.append(sequence)
    return {
        "status_code": response.status_code,
        "event_count": len(event_types),
        "first_event": event_types[0] if event_types else None,
        "last_event": event_types[-1] if event_types else None,
        "last_sequence": sequences[-1] if sequences else None,
        "valid": (
            response.status_code == 200
            and "text/event-stream" in response.headers.get("content-type", "")
            and bool(event_types)
            and event_types[0] == "run.accepted"
            and event_types[-1] in {"run.completed", "run.partial"}
            and sequences == list(range(1, len(event_types) + 1))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="Origin before the optional AgentArts gateway prefix")
    parser.add_argument(
        "--custom-prefix",
        default="",
        help="Gateway prefix ending in /invocations; leave empty for direct local calls",
    )
    parser.add_argument(
        "--env-file", type=Path, help="Read AGENTARTS_AUTHORIZATION from this explicit dotenv file"
    )
    parser.add_argument("--endpoint", help="Optional fixed AgentArts endpoint, e.g. Latest")
    parser.add_argument(
        "--tls-trust-store",
        choices=("certifi", "system"),
        default="certifi",
        help="Certificate validation backend; neither choice disables TLS verification",
    )
    parser.add_argument(
        "--tls-ca-file",
        type=Path,
        help="Explicit additional PEM certificate file; requires certifi trust store",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    prefix = args.custom_prefix.rstrip("/")
    auth = os.environ.get("AGENTARTS_AUTHORIZATION", "")
    if args.env_file is not None:
        if not args.env_file.is_file():
            print(json.dumps({"error_type": "MissingEnvFile"}))
            return 1
        auth = auth or dotenv_values(args.env_file).get("AGENTARTS_AUTHORIZATION") or ""
    if prefix and (not auth or httpx.URL(base).scheme != "https"):
        print(json.dumps({"error_type": "GatewayRequiresHTTPSAndAuthorization"}))
        return 1
    if prefix:
        auth = auth if auth.startswith("Bearer ") else "Bearer " + auth
        if not re.fullmatch(r"Bearer [!-~]{16,4096}", auth):
            print(json.dumps({"error_type": "InvalidAuthorizationFormat"}))
            return 1
    if args.endpoint and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.endpoint):
        print(json.dumps({"error_type": "InvalidEndpoint"}))
        return 1
    runs_path = _join(prefix, "/api/v2/due-diligence/runs")
    probe_id = uuid.uuid4().hex
    headers = {
        "Accept": "application/json",
        "Idempotency-Key": f"prefix-probe-{probe_id}",
        "X-Hw-Agentarts-Session-Id": f"prefix-probe-{probe_id}",
        "X-Hw-Agentgateway-User-Id": "prefix-probe-user",
    }
    # Do not send gateway credentials during credential-free direct local probes.
    if prefix:
        headers["Authorization"] = auth
    body = {
        "enterprise": {"company_name": "金调绿洲科技有限公司"},
        "scenario_id": "normal-enterprise",
        "mode": "single",
    }
    with httpx.Client(
        base_url=base,
        timeout=120,
        trust_env=False,
        follow_redirects=False,
        verify=create_tls_context(args.tls_trust_store, ca_file=args.tls_ca_file),
        params={"endpoint": args.endpoint} if args.endpoint else None,
    ) as client:
        health = client.get(_join(prefix, "/ping"), headers=headers)
        if health.status_code != 200:
            print(json.dumps({"ping": health.status_code}))
            return 1
        created = client.post(runs_path, json=body, headers=headers)
        if created.status_code not in {200, 202}:
            print(json.dumps({"ping": health.status_code, "create": created.status_code}))
            return 1
        resource = created.json()
        run_id = resource["run_id"]
        run_path = f"{runs_path}/{run_id}"
        status = client.get(run_path, headers=headers)
        events = client.get(
            f"{run_path}/events",
            headers={**headers, "Accept": "text/event-stream"},
        )
        result = client.get(f"{run_path}/result", headers=headers)

        cancel_headers = {**headers, "Idempotency-Key": f"prefix-probe-cancel-{probe_id}"}
        cancel_created = client.post(runs_path, json=body, headers=cancel_headers)
        if cancel_created.status_code not in {200, 202}:
            print(json.dumps({"cancel_create": cancel_created.status_code}))
            return 1
        cancel_run_id = cancel_created.json().get("run_id")
        cancelled = client.post(
            f"{runs_path}/{cancel_run_id}/cancel",
            headers=cancel_headers,
        )

    event_summary = _sse_summary(events)
    print(
        json.dumps(
            {
                "ping": health.status_code,
                "create": created.status_code,
                "query": status.status_code,
                "events": event_summary,
                "result": result.status_code,
                "cancel": cancelled.status_code,
                "run_id": run_id,
                "cancel_run_id": cancel_run_id,
            },
            ensure_ascii=False,
        )
    )
    return int(
        health.status_code != 200
        or status.status_code != 200
        or events.status_code != 200
        or not event_summary["valid"]
        or result.status_code != 200
        or cancelled.status_code != 200
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as error:
        # Never log response bodies, credentials, full URLs or exception messages.
        print(json.dumps({"error_type": type(error).__name__}))
        sys.exit(1)
