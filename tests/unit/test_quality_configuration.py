from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_pytest_has_a_coverage_gate() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]

    assert "--cov=src/jindiao" in addopts
    assert "--cov-report=term-missing" in addopts
    assert "--cov-fail-under=80" in addopts


def test_pytest_cov_is_declared_for_both_supported_install_flows() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    dev_dependencies = pyproject["dependency-groups"]["dev"]
    requirements = (PROJECT_ROOT / "requirements.txt").read_text()

    assert any(item.startswith("pytest-cov") for item in dev_dependencies)
    assert "pytest-cov" in requirements


def test_make_check_runs_every_quality_gate() -> None:
    makefile = (PROJECT_ROOT / "Makefile").read_text()

    assert "check: lint type-check test" in makefile
    assert "ruff check ." in makefile
    assert "mypy src tests" in makefile
    assert "pytest" in makefile
