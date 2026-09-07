from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from jindiao.scenarios.manifest import (
    EnterpriseKey,
    ScenarioFile,
    ScenarioFileRole,
    ScenarioManifest,
)

SHA256 = "a" * 64


def test_manifest_defines_versioned_runtime_and_expected_files() -> None:
    manifest = ScenarioManifest(
        schema_version=1,
        scenario_id="normal-enterprise",
        enterprise_key=EnterpriseKey(
            company_name="示例科技有限公司",
            unified_social_credit_code="91110000EXAMPLE01",
            aliases=("示例科技",),
        ),
        version="v1.0.0",
        as_of_date=date(2026, 9, 3),
        files=(
            ScenarioFile(path="company.json", sha256=SHA256),
            ScenarioFile(path="corpus/annual-report.md", sha256=SHA256),
            ScenarioFile(
                path="expected/findings.json",
                sha256=SHA256,
                role=ScenarioFileRole.EXPECTED,
            ),
        ),
    )

    assert manifest.runtime_paths == ("company.json", "corpus/annual-report.md")
    assert manifest.expected_paths == ("expected/findings.json",)


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("../other/company.json", ScenarioFileRole.RUNTIME),
        ("/tmp/company.json", ScenarioFileRole.RUNTIME),
        ("expected/decision.json", ScenarioFileRole.RUNTIME),
        ("company.json", ScenarioFileRole.EXPECTED),
    ],
)
def test_manifest_rejects_unsafe_or_role_inconsistent_paths(
    path: str,
    role: ScenarioFileRole,
) -> None:
    with pytest.raises(ValidationError):
        ScenarioFile(path=path, sha256=SHA256, role=role)


def test_manifest_rejects_duplicate_paths_and_unsupported_versions() -> None:
    file = ScenarioFile(path="company.json", sha256=SHA256)
    values = {
        "schema_version": 1,
        "scenario_id": "normal-enterprise",
        "enterprise_key": {"company_name": "示例科技有限公司"},
        "version": "v1.0.0",
        "as_of_date": "2026-09-03",
        "files": [file, file],
    }

    with pytest.raises(ValidationError, match="unique"):
        ScenarioManifest.model_validate(values)
    with pytest.raises(ValidationError):
        ScenarioManifest.model_validate(values | {"schema_version": 2, "files": [file]})
    with pytest.raises(ValidationError):
        ScenarioManifest.model_validate(values | {"version": "latest", "files": [file]})


def test_checked_in_manifest_schema_matches_public_contract() -> None:
    schema_path = Path("mock_data/scenarios/schema.json")
    checked_in = json.loads(schema_path.read_text(encoding="utf-8"))

    assert checked_in == ScenarioManifest.model_json_schema()
