from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from jindiao.application.errors import (
    EntityNotFoundError,
    ScenarioIntegrityError,
)
from jindiao.contracts.entities import EnterpriseInput
from jindiao.scenarios import ScenarioRepository


def write_scenario(
    root: Path,
    *,
    scenario_id: str = "normal-enterprise",
    company_name: str = "示例科技有限公司",
    status: str = "存续",
) -> Path:
    scenario_dir = root / scenario_id
    scenario_dir.mkdir(parents=True)
    company = json.dumps(
        {"company_name": company_name, "registration_status": status},
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    (scenario_dir / "company.json").write_bytes(company)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "enterprise_key": {
            "company_name": company_name,
            "unified_social_credit_code": "91110000EXAMPLE01",
            "aliases": [company_name.removesuffix("有限公司")],
        },
        "version": "v1.0.0",
        "as_of_date": "2026-09-03",
        "files": [
            {
                "path": "company.json",
                "sha256": hashlib.sha256(company).hexdigest(),
                "role": "runtime",
            }
        ],
    }
    (scenario_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return scenario_dir


def test_repository_matches_enterprise_and_loads_content_addressed_snapshot(
    tmp_path: Path,
) -> None:
    write_scenario(tmp_path)
    repository = ScenarioRepository(tmp_path)

    snapshot = repository.resolve(
        EnterpriseInput(company_name="示例科技"),
        version="v1.0.0",
    )

    assert snapshot.scenario_snapshot_id.startswith("normal-enterprise:v1.0.0:")
    assert snapshot.manifest.as_of_date.isoformat() == "2026-09-03"
    company = snapshot.read_json("company.json")
    assert company["registration_status"] == "存续"
    with pytest.raises(TypeError):
        company["registration_status"] = "注销"  # type: ignore[index]


def test_repository_rejects_enterprise_mismatch_and_unknown_file(tmp_path: Path) -> None:
    write_scenario(tmp_path)
    repository = ScenarioRepository(tmp_path)

    with pytest.raises(ScenarioIntegrityError, match="enterprise key"):
        repository.load(
            "normal-enterprise",
            EnterpriseInput(company_name="其他企业有限公司"),
        )

    snapshot = repository.load(
        "normal-enterprise",
        EnterpriseInput(company_name="示例科技有限公司"),
    )
    with pytest.raises(ScenarioIntegrityError, match="not declared"):
        snapshot.read_text("operations.json")


def test_repository_loads_explicit_live_fallback_template_without_rebinding_enterprise(
    tmp_path: Path,
) -> None:
    write_scenario(tmp_path)

    snapshot = ScenarioRepository(tmp_path).load_template("normal-enterprise")

    assert snapshot.manifest.scenario_id == "normal-enterprise"
    assert snapshot.read_json("company.json")["company_name"] == "示例科技有限公司"


def test_repository_stops_on_hash_mismatch(tmp_path: Path) -> None:
    scenario_dir = write_scenario(tmp_path)
    (scenario_dir / "company.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ScenarioIntegrityError, match="hash mismatch"):
        ScenarioRepository(tmp_path).load(
            "normal-enterprise",
            EnterpriseInput(unified_social_credit_code="91110000EXAMPLE01"),
        )


def test_repository_isolates_enterprises_and_requires_unique_match(tmp_path: Path) -> None:
    write_scenario(tmp_path, scenario_id="normal-enterprise")
    write_scenario(
        tmp_path,
        scenario_id="risk-enterprise",
        company_name="风险科技有限公司",
        status="经营异常",
    )
    repository = ScenarioRepository(tmp_path)

    normal = repository.resolve(EnterpriseInput(company_name="示例科技有限公司"))
    risk = repository.resolve(EnterpriseInput(company_name="风险科技有限公司"))

    assert normal.read_json("company.json")["registration_status"] == "存续"
    assert risk.read_json("company.json")["registration_status"] == "经营异常"
    with pytest.raises(EntityNotFoundError):
        repository.resolve(EnterpriseInput(company_name="不存在的企业"))
