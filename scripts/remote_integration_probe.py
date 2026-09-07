"""Exercise a deployed Mock runtime over real HTTP; save only safe test summaries.

Usage: python scripts/remote_integration_probe.py http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

import httpx

RUNS = "/api/v2/due-diligence/runs"
TERMINAL_EVENTS = {"run.completed", "run.partial", "run.failed", "run.cancelled"}


def events(response: httpx.Response) -> list[dict[str, Any]]:
    response.raise_for_status()
    assert "text/event-stream" in response.headers.get("content-type", "")
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    checks: list[dict[str, Any]] = []
    probe_id = uuid.uuid4().hex

    def check(name: str, action: Callable[[], Any]) -> None:
        started = time.monotonic()
        try:
            detail = action()
            checks.append({"name": name, "passed": True, "detail": detail})
        except Exception as error:
            # Never serialize response bodies, request headers or exception messages.
            checks.append({"name": name, "passed": False, "error_type": type(error).__name__})
        checks[-1]["duration_ms"] = round((time.monotonic() - started) * 1000)
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    headers = {
        "X-Hw-Agentgateway-User-Id": "ecs-integration",
        "X-Hw-Agentarts-Session-Id": probe_id,
    }
    body = {
        "enterprise": {"company_name": "金调绿洲科技有限公司"},
        "scenario_id": "normal-enterprise",
        "mode": "single",
    }
    with httpx.Client(
        base_url=args.base_url, headers=headers, timeout=60, trust_env=False
    ) as client:

        def expect_status(method: str, path: str, expected: int, **kwargs: Any) -> dict[str, int]:
            response = client.request(method, path, **kwargs)
            assert response.status_code == expected, (response.status_code, expected)
            return {"status_code": response.status_code}

        def health() -> dict[str, Any]:
            response = client.get("/ping")
            assert response.status_code == 200
            assert response.json()["status"] == "Healthy"
            return cast(dict[str, Any], response.json())

        check("ping", health)
        check(
            "v2.invalid_mode",
            lambda: expect_status("POST", RUNS, 422, json={**body, "mode": "invalid"}),
        )
        check(
            "v2.detached_gate",
            lambda: expect_status(
                "POST", RUNS, 409, json={**body, "execution_profile": "detached"}
            ),
        )
        check(
            "invocations.invalid_body", lambda: expect_status("POST", "/invocations", 422, json={})
        )
        check(
            "invocations.invalid_json",
            lambda: expect_status(
                "POST",
                "/invocations",
                422,
                content="{",
                headers={"Content-Type": "application/json"},
            ),
        )
        check(
            "invocations.accept",
            lambda: expect_status(
                "POST", "/invocations", 406, json=body, headers={"Accept": "text/plain"}
            ),
        )

        def exercise_run(mode: str, scenario: str) -> dict[str, Any]:
            company_name = {
                "normal-enterprise": "金调绿洲科技有限公司",
                "evidence-conflict": "金调双源制造有限公司",
            }[scenario]
            request = {
                **body,
                "enterprise": {"company_name": company_name},
                "mode": mode,
                "scenario_id": scenario,
            }
            key = f"{probe_id}-{mode}-{scenario}"
            created = client.post(RUNS, json=request, headers={"Idempotency-Key": key})
            assert created.status_code == 202
            run_id = created.json()["run_id"]
            run_path = f"{RUNS}/{run_id}"
            repeated = client.post(RUNS, json=request, headers={"Idempotency-Key": key})
            assert repeated.status_code == 202 and repeated.json()["run_id"] == run_id
            conflicting = client.post(
                RUNS, json={**request, "language": "en-US"}, headers={"Idempotency-Key": key}
            )
            assert conflicting.status_code == 409
            observed = events(client.get(f"{run_path}/events"))
            assert observed and observed[-1]["event_type"] in TERMINAL_EVENTS
            assert [item["sequence"] for item in observed] == list(range(1, len(observed) + 1))
            assert len([item for item in observed if item["event_type"] in TERMINAL_EVENTS]) == 1
            status = client.get(run_path)
            result = client.get(f"{run_path}/result")
            assert status.status_code == result.status_code == 200
            assert result.json()["meta"]["run_id"] == run_id
            assert result.json()["meta"]["mode"] == mode
            assert status.json()["status"] == result.json()["meta"]["status"]
            assert status.json()["result_available"]
            assert len(result.json()["sections"]) == 8
            report_event = next(
                item for item in observed if item["event_type"] == "report.completed"
            )
            assert report_event["payload"]["result"] == result.json()
            cursor = observed[len(observed) // 2]["sequence"]
            replay = events(
                client.get(f"{run_path}/events", headers={"Last-Event-ID": str(cursor)})
            )
            assert replay == [item for item in observed if item["sequence"] > cursor]

            for suffix in ("", "/result", "/events"):
                check(
                    f"{mode}.{scenario}.ownership{suffix}",
                    partial(
                        expect_status,
                        "GET",
                        run_path + suffix,
                        404,
                        headers={"X-Hw-Agentgateway-User-Id": "other"},
                        timeout=5,
                    ),
                )
            check(
                f"{mode}.{scenario}.session",
                lambda: expect_status(
                    "GET", run_path, 404, headers={"X-Hw-Agentarts-Session-Id": "other"}
                ),
            )

            def exhausted_replay() -> dict[str, int]:
                replay = events(
                    client.get(
                        f"{run_path}/events", params={"after": observed[-1]["sequence"]}, timeout=5
                    )
                )
                assert replay == []
                return {"event_count": 0}

            check(f"{mode}.{scenario}.terminal_cursor", exhausted_replay)
            for _ in range(2):
                cancelled = client.post(f"{run_path}/cancel")
                assert cancelled.status_code == 200
                assert cancelled.json()["status"] == status.json()["status"]
            return {
                "run_id": run_id,
                "status": status.json()["status"],
                "events": len(observed),
                "sections": 8,
            }

        for mode in ("single", "multi"):
            for scenario in ("normal-enterprise", "evidence-conflict"):
                check(
                    f"v2.{mode}.{scenario}",
                    partial(exercise_run, mode, scenario),
                )

        def legacy(mode: str, streaming: bool) -> dict[str, Any]:
            request = {key: value for key, value in body.items() if key != "mode"}
            response = client.post(
                "/api/v1/due-diligence/result",
                params={"mode": mode},
                json=request,
                headers={"Accept": "text/event-stream" if streaming else "application/json"},
            )
            assert response.status_code == 200
            if streaming:
                observed = events(response)
                assert observed[-1]["event_type"] == "report.completed"
                result = observed[-1]["payload"]["result"]
            else:
                result = response.json()
            assert result["meta"]["mode"] == mode and len(result["sections"]) == 8
            return {"status": result["meta"]["status"], "run_id": result["meta"]["run_id"]}

        for mode in ("single", "multi"):
            for streaming in (False, True):
                check(
                    f"v1.{mode}.{'sse' if streaming else 'json'}",
                    partial(legacy, mode, streaming),
                )

        def invocation(streaming: bool) -> dict[str, Any]:
            response = client.post(
                "/invocations",
                json={"input": body},
                headers={"Accept": "text/event-stream" if streaming else "application/json"},
            )
            if streaming:
                observed = events(response)
                assert observed[-1]["event_type"] == "run.completed"
                return {"event_count": len(observed)}
            assert response.status_code == 202
            observed = events(client.get(response.json()["links"]["events"]["href"]))
            assert observed[-1]["event_type"] == "run.completed"
            return {"run_id": response.json()["run_id"]}

        check("invocations.json_envelope", lambda: invocation(False))
        check("invocations.sse_envelope", lambda: invocation(True))

    passed = sum(item["passed"] for item in checks)
    report = {
        "tested_at": datetime.now(UTC).isoformat(),
        "probe_id": probe_id,
        "profile": "attached/mock",
        "passed": passed,
        "failed": len(checks) - passed,
        "checks": checks,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"passed": passed, "failed": len(checks) - passed}), flush=True)
    return int(passed != len(checks))


if __name__ == "__main__":
    raise SystemExit(main())
