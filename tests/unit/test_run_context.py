from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jindiao.application import RunContext, Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.scenarios import ScenarioRepository, ScenarioSnapshot


def load_snapshot(root: Path) -> ScenarioSnapshot:
    scenario_dir = root / "normal-enterprise"
    scenario_dir.mkdir()
    content = b'{"registration_status":"active"}'
    (scenario_dir / "company.json").write_bytes(content)
    manifest = {
        "schema_version": 1,
        "scenario_id": "normal-enterprise",
        "enterprise_key": {"company_name": "示例科技有限公司"},
        "version": "v1.0.0",
        "as_of_date": "2026-09-03",
        "files": [
            {
                "path": "company.json",
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    (scenario_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ScenarioRepository(root).load(
        "normal-enterprise",
        EnterpriseInput(company_name="示例科技有限公司"),
    )


def test_run_context_freezes_snapshot_rules_skills_and_runtime_policy(tmp_path: Path) -> None:
    snapshot = load_snapshot(tmp_path)
    settings = Settings(
        model_name="test-model",
        rule_version="rules-v1",
        max_tool_calls=21,
        max_concurrency=3,
        allow_degraded_mock=False,
    )
    skill_versions = {"reporting": "1.2.0"}

    context = RunContext.from_settings(
        request_id="request-1",
        run_id="run-1",
        scenario=snapshot,
        settings=settings,
        skill_versions=skill_versions,
    )
    settings.rule_version = "rules-v2"
    settings.max_tool_calls = 99
    skill_versions["reporting"] = "9.9.9"

    assert context.scenario_snapshot_id == snapshot.scenario_snapshot_id
    assert context.rule_version == "rules-v1"
    assert context.skill_versions["reporting"] == "1.2.0"
    assert context.policy.agent_runtime_mode == "deterministic_harness"
    assert context.policy.formal_agent_run is False
    assert context.policy.max_tool_calls == 21
    assert context.policy.max_llm_requests == settings.max_llm_requests
    assert context.policy.max_total_tokens == settings.max_total_tokens
    assert context.policy.max_snapshot_reads == settings.max_snapshot_reads
    assert context.scenario.read_json("company.json")["registration_status"] == "active"
    with pytest.raises(TypeError):
        context.skill_versions["reporting"] = "2.0.0"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        context.rule_version = "rules-v3"  # type: ignore[misc]
