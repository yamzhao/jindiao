"""Reproduce the local feedback loop without credentials, sockets or existing state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.evidence import SourceStatus
from jindiao.observability.artifacts import RunArtifactStore
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.paths import project_root
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 6, tzinfo=UTC)
HEADERS = {"x-hw-agentgateway-user-id": "demo", "x-hw-agentarts-session-id": "demo"}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def run_demo(output: Path) -> dict[str, Any]:
    from jindiao.api.app import create_app

    artifact_root = output / "artifacts"
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        environment="test",
        model_provider="offline_mock",
        model_name="deterministic-mock",
        agent_runtime_mode="deterministic_harness",
        data_source_mode="mock",
        reporting_demo_enabled=True,
        shared_storage_backend="local",
        execution_profile="attached",
        skill_evolution_auto_approve=False,
        artifact_root=artifact_root,
    )
    service = DueDiligenceService(
        settings=settings,
        scenarios=ScenarioRepository(project_root() / "mock_data/scenarios"),
        risk_rules_path=project_root() / "config/risk-rules-v1.json",
        clock=lambda: NOW,
        toolset_factory=lambda: ScenarioToolset(
            clock=lambda: NOW,
            source_status_overrides={"judicial": SourceStatus.SOURCE_ERROR},
        ),
    )
    app = create_app(service=service)
    coordinator = app.state.run_coordinator
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=HEADERS,
    ) as client:

        async def new_run(label: str) -> dict[str, Any]:
            response = await client.post(
                "/api/v2/due-diligence/runs",
                json={
                    "enterprise": {"company_name": "金调绿洲科技有限公司"},
                    "scenario_id": "normal-enterprise",
                },
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
            await coordinator.execute(run_id)
            response = await client.get(f"/api/v2/due-diligence/runs/{run_id}/result")
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            assert RunArtifactStore(artifact_root).verify_manifest(run_id)
            write_json(output / f"{label}-result.json", result)
            resource = await client.get(f"/api/v2/due-diligence/runs/{run_id}")
            write_json(output / f"{label}-run.json", resource.json())
            return result

        async def command(*args: str) -> dict[str, Any]:
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "jindiao.reporting.demo_cli",
                    "--demo",
                    "--artifact-root",
                    str(artifact_root),
                    *args,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            receipt: dict[str, Any] = json.loads(completed.stdout)
            return receipt

        source = await new_run("source")
        run_id = source["meta"]["run_id"]
        old_events = await coordinator.event_store.read_after(run_id, 0)
        response = await client.post(
            f"/api/v2/due-diligence/runs/{run_id}/feedback",
            headers={"Idempotency-Key": "demo-placement"},
            json={
                "kind": "gap_disclosure_placement",
                "text": "司法缺口请就近披露",
                "target_section_ids": ["judicial-risk"],
            },
        )
        response.raise_for_status()
        feedback = response.json()
        assert feedback["status"] == "awaiting_approval"
        detail = await client.get(feedback["detail_url"], params={"include": "reports"})
        detail.raise_for_status()
        evaluation = detail.json()["evaluation"]
        assert evaluation["passed"] and len(evaluation["cases"]) == 9
        write_json(output / "evaluation.json", detail.json())
        source_case = evaluation["cases"][0]
        for field, filename in (
            ("before", "before.md"),
            ("after", "after.md"),
            ("diff", "diff.patch"),
        ):
            (output / filename).write_text(source_case[field], encoding="utf-8")
        pending = await new_run("before-apply")
        assert pending["report_markdown"] == source["report_markdown"] == source_case["before"]
        receipt = await command("apply", feedback["evolution_id"], "--reason", "独立离线演示确认")
        write_json(output / "apply-receipt.json", receipt)
        updated = await new_run("after-apply")
        assert updated["report_markdown"] == source_case["after"]
        assert updated["decision"] == source["decision"]
        assert updated["evidence"] == source["evidence"]
        assert (
            updated["meta"]["skill_versions"]["feedback-evolved-reporting"]
            == feedback["candidate_version"]
        )
        write_json(
            output / "reset-receipt.json", await command("reset", "--reason", "演示恢复基线")
        )
        restored = await new_run("after-reset")
        assert restored["report_markdown"] == source["report_markdown"]
        historical = await client.get(f"/api/v2/due-diligence/runs/{run_id}/result")
        assert historical.json() == source
        assert await coordinator.event_store.read_after(run_id, 0) == old_events
        summary = {
            "verified": True,
            "transport": "in-process ASGI; offline synthetic data",
            "case_count": len(evaluation["cases"]),
            "source_unchanged": True,
            "source_run_id": run_id,
            "evolution_id": feedback["evolution_id"],
            "versions": {
                label: result["meta"]["skill_versions"]["feedback-evolved-reporting"]
                for label, result in (
                    ("source", source),
                    ("before_apply", pending),
                    ("after_apply", updated),
                    ("after_reset", restored),
                )
            },
            "final_state": await command("show"),
        }
        write_json(output / "summary.json", summary)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; must not exist")
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.exit(2, "Refusing to overwrite an existing Demo directory.\n")
    # app.py also exports a default ASGI app. Import it only from the new empty
    # directory so that its Settings cannot read the caller's local .env.
    os.chdir(output)
    summary = asyncio.run(run_demo(output))
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
