"""Compare completed Run HTTP responses before/after an operator-controlled restart.

First run remote_integration_probe.py with --output, then use this script's
capture and verify actions on either side of a container restart. Only Mock
Run identifiers and response hashes are stored; this script never restarts it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "verify"))
    parser.add_argument("base_url")
    parser.add_argument("integration_report", type=Path)
    parser.add_argument("baseline", type=Path)
    args = parser.parse_args()
    report = json.loads(args.integration_report.read_text(encoding="utf-8"))
    snapshots: dict[str, dict[str, str]] = {}
    with httpx.Client(
        base_url=args.base_url,
        headers={
            "X-Hw-Agentgateway-User-Id": "ecs-integration",
            "X-Hw-Agentarts-Session-Id": report["probe_id"],
        },
        timeout=10,
        trust_env=False,
    ) as client:
        for check in report["checks"]:
            if not check["passed"] or not check["name"].startswith("v2."):
                continue
            run_id = check.get("detail", {}).get("run_id")
            if not run_id:
                continue
            hashes = {}
            for suffix in ("", "/result", "/events"):
                response = client.get(f"/api/v2/due-diligence/runs/{run_id}{suffix}")
                response.raise_for_status()
                if suffix == "/events":
                    payload = [
                        json.loads(line[6:])
                        for line in response.text.splitlines()
                        if line.startswith("data: ")
                    ]
                else:
                    payload = response.json()
                canonical = json.dumps(payload, sort_keys=True).encode()
                hashes[suffix or "/status"] = hashlib.sha256(canonical).hexdigest()
            snapshots[run_id] = hashes
            if args.action == "verify":
                _, mode, scenario = check["name"].split(".", 2)
                company = {
                    "normal-enterprise": "金调绿洲科技有限公司",
                    "evidence-conflict": "金调双源制造有限公司",
                }[scenario]
                repeated = client.post(
                    "/api/v2/due-diligence/runs",
                    headers={"Idempotency-Key": f"{report['probe_id']}-{mode}-{scenario}"},
                    json={
                        "enterprise": {"company_name": company},
                        "scenario_id": scenario,
                        "mode": mode,
                    },
                )
                assert repeated.status_code == 202
                repeated_hash = hashlib.sha256(
                    json.dumps(repeated.json(), sort_keys=True).encode()
                ).hexdigest()
                assert repeated_hash == hashes["/status"], "Idempotent retry changed the Run"
    assert len(snapshots) == 4, "Expected four completed integration Runs"
    if args.action == "capture":
        args.baseline.write_text(json.dumps(snapshots, indent=2) + "\n", encoding="utf-8")
    else:
        assert snapshots == json.loads(args.baseline.read_text(encoding="utf-8"))
    print(json.dumps({"action": args.action, "passed": True, "runs": len(snapshots)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
