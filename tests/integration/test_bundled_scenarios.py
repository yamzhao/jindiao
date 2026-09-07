from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from jindiao.contracts.entities import EnterpriseInput
from jindiao.scenarios import ExpectedResultLoader, ScenarioRepository

SCENARIOS_ROOT = Path("mock_data/scenarios")
CASES = (
    ("normal-enterprise", "金调绿洲科技有限公司", "pass"),
    ("judicial-high-risk", "金调震岳工程有限公司", "reject"),
    ("operational-abnormal", "金调星火商贸有限公司", "manual_review"),
    ("evidence-conflict", "金调双源制造有限公司", "manual_review"),
    ("stable-industrial", "金调澄明工业有限公司", "manual_review"),
    ("stable-services", "金调远帆服务有限公司", "manual_review"),
    ("judicial-restriction", "金调云岑建设有限公司", "reject"),
    ("operating-pressure", "金调沧澜供应链有限公司", "manual_review"),
    ("ambiguous-north", "金调同名实业(北方)有限公司", "pass"),
    ("ambiguous-south", "金调同名实业(南方)有限公司", "pass"),
)


@pytest.mark.parametrize(("scenario_id", "company_name", "expected_band"), CASES)
def test_bundled_scenario_is_complete_and_hash_verified(
    scenario_id: str,
    company_name: str,
    expected_band: str,
) -> None:
    snapshot = ScenarioRepository(SCENARIOS_ROOT).load(
        scenario_id,
        EnterpriseInput(company_name=company_name),
        version="v1.0.0",
    )

    assert snapshot.available_paths == (
        "company.json",
        "corpus/operating-notes.md",
        "governance.json",
        "judicial.json",
        "operations.json",
        "peers.json",
    )
    assert snapshot.read_json("company.json")["company_name"] == company_name
    assert snapshot.read_json("governance.json")["shareholders"]
    assert "financial_indicators" in snapshot.read_json("operations.json")
    assert "peer_companies" in snapshot.read_json("peers.json")
    assert company_name in snapshot.read_text("corpus/operating-notes.md")

    expected = ExpectedResultLoader(SCENARIOS_ROOT).load(scenario_id, version="v1.0.0")
    decision = expected.read_json("expected/decision.json")
    findings = expected.read_json("expected/findings.json")
    assert isinstance(decision, Mapping)
    assert isinstance(findings, tuple)
    assert decision["band"] == expected_band
    assert findings


def test_conflict_scenario_keeps_disagreeing_sources_visible() -> None:
    snapshot = ScenarioRepository(SCENARIOS_ROOT).load(
        "evidence-conflict",
        EnterpriseInput(company_name="金调双源制造有限公司"),
    )
    operations = snapshot.read_json("operations.json")
    notes = snapshot.read_text("corpus/operating-notes.md")

    assert operations["employee_count"] == 86
    assert "112 人" in notes
