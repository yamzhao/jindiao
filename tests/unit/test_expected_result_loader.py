from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from jindiao.application.errors import EvaluationIntegrityError
from jindiao.contracts.entities import EnterpriseInput
from jindiao.scenarios import ExpectedResultLoader, ScenarioRepository


def write_scenario_with_expected(root: Path) -> Path:
    scenario_dir = root / "normal-enterprise"
    expected_dir = scenario_dir / "expected"
    expected_dir.mkdir(parents=True)
    company = b'{"registration_status":"active"}'
    findings = b'[{"finding_id":"finding-1"}]'
    (scenario_dir / "company.json").write_bytes(company)
    (expected_dir / "findings.json").write_bytes(findings)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "scenario_id": "normal-enterprise",
        "enterprise_key": {"company_name": "示例科技有限公司"},
        "version": "v1.0.0",
        "as_of_date": "2026-09-03",
        "files": [
            {
                "path": "company.json",
                "sha256": hashlib.sha256(company).hexdigest(),
                "role": "runtime",
            },
            {
                "path": "expected/findings.json",
                "sha256": hashlib.sha256(findings).hexdigest(),
                "role": "expected",
            },
        ],
    }
    (scenario_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return scenario_dir


def test_runtime_snapshot_denies_expected_answers(tmp_path: Path) -> None:
    write_scenario_with_expected(tmp_path)
    snapshot = ScenarioRepository(tmp_path).load(
        "normal-enterprise",
        EnterpriseInput(company_name="示例科技有限公司"),
    )

    assert snapshot.available_paths == ("company.json",)
    with pytest.raises(EvaluationIntegrityError, match="expected"):
        snapshot.read_json("expected/findings.json")


def test_expected_loader_is_separate_read_only_and_hash_verified(tmp_path: Path) -> None:
    scenario_dir = write_scenario_with_expected(tmp_path)
    loader = ExpectedResultLoader(tmp_path)

    expected = loader.load("normal-enterprise", version="v1.0.0")

    assert expected.available_paths == ("expected/findings.json",)
    findings = expected.read_json("expected/findings.json")
    assert isinstance(findings, tuple)
    first = findings[0]
    assert isinstance(first, Mapping)
    assert first["finding_id"] == "finding-1"
    with pytest.raises(TypeError):
        first["finding_id"] = "changed"  # type: ignore[index]

    (scenario_dir / "expected/findings.json").write_text("[]", encoding="utf-8")
    with pytest.raises(EvaluationIntegrityError, match="hash mismatch"):
        loader.load("normal-enterprise", version="v1.0.0")


def test_expected_loader_rejects_runtime_paths_and_wrong_version(tmp_path: Path) -> None:
    write_scenario_with_expected(tmp_path)
    expected = ExpectedResultLoader(tmp_path).load("normal-enterprise")

    with pytest.raises(EvaluationIntegrityError, match="not declared"):
        expected.read_json("company.json")
    with pytest.raises(EvaluationIntegrityError, match="version"):
        ExpectedResultLoader(tmp_path).load("normal-enterprise", version="v2.0.0")
