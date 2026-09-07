from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from jindiao.skills import SkillPackage

SKILLS = (
    Path("skills/agent/tyc-evidence-acquisition"),
    Path("skills/agent/tianyancha-annual-report-social-security"),
    Path("skills/team/evidence-backed-due-diligence"),
    Path("skills/team/feedback-evolved-reporting"),
)


@pytest.mark.parametrize("source", SKILLS)
def test_skill_package_is_complete_and_repository_independent(source: Path, tmp_path: Path) -> None:
    copied = tmp_path / source.name
    shutil.copytree(source, copied)

    package = SkillPackage.load(copied)

    assert package.name == source.name
    assert package.version == "1.0.0"
    assert package.kind in {"agent", "team"}
    assert package.evaluation_case_count >= 4
    package.validate_example()
    assert "/Users/" not in "\n".join(
        path.read_text(encoding="utf-8") for path in copied.rglob("*") if path.is_file()
    )


def test_skill_package_example_validation_rejects_contract_drift(tmp_path: Path) -> None:
    copied = tmp_path / "tyc-evidence-acquisition"
    shutil.copytree(SKILLS[0], copied)
    package = SkillPackage.load(copied)

    with pytest.raises(ValidationError):
        package.validate_input({"domain": "judicial"})
