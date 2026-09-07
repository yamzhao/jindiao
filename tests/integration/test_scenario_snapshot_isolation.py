from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jindiao.application.errors import ScenarioIntegrityError
from jindiao.contracts.entities import EnterpriseInput
from jindiao.scenarios import ScenarioRepository


def write_scenario(root: Path, scenario_id: str, company_name: str, marker: str) -> Path:
    scenario_dir = root / scenario_id
    scenario_dir.mkdir(parents=True)
    content = json.dumps({"company_name": company_name, "marker": marker}).encode()
    (scenario_dir / "company.json").write_bytes(content)
    manifest = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "enterprise_key": {"company_name": company_name},
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
    return scenario_dir


def test_agents_share_identical_values_from_one_snapshot(tmp_path: Path) -> None:
    write_scenario(tmp_path, "normal-enterprise", "示例科技有限公司", "normal")
    snapshot = ScenarioRepository(tmp_path).load(
        "normal-enterprise",
        EnterpriseInput(company_name="示例科技有限公司"),
    )

    governance_agent_view = snapshot.read_json("company.json")
    operations_agent_view = snapshot.read_json("company.json")

    assert governance_agent_view == operations_agent_view
    assert governance_agent_view is not operations_agent_view
    assert snapshot.manifest.as_of_date == snapshot.manifest.as_of_date


def test_two_enterprise_snapshots_never_mix_content(tmp_path: Path) -> None:
    write_scenario(tmp_path, "normal-enterprise", "示例科技有限公司", "normal-only")
    write_scenario(tmp_path, "risk-enterprise", "风险科技有限公司", "risk-only")
    repository = ScenarioRepository(tmp_path)

    normal = repository.resolve(EnterpriseInput(company_name="示例科技有限公司"))
    risk = repository.resolve(EnterpriseInput(company_name="风险科技有限公司"))

    assert normal.read_json("company.json")["marker"] == "normal-only"
    assert risk.read_json("company.json")["marker"] == "risk-only"
    assert normal.scenario_snapshot_id != risk.scenario_snapshot_id


def test_loaded_snapshot_is_stable_when_disk_changes(tmp_path: Path) -> None:
    scenario_dir = write_scenario(
        tmp_path,
        "normal-enterprise",
        "示例科技有限公司",
        "before",
    )
    repository = ScenarioRepository(tmp_path)
    snapshot = repository.resolve(EnterpriseInput(company_name="示例科技有限公司"))

    (scenario_dir / "company.json").write_text('{"marker":"after"}', encoding="utf-8")

    assert snapshot.read_json("company.json")["marker"] == "before"
    with pytest.raises(ScenarioIntegrityError, match="hash mismatch"):
        repository.resolve(EnterpriseInput(company_name="示例科技有限公司"))
