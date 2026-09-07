from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def test_installed_package_reads_external_assets_from_explicit_project_root(tmp_path: Path) -> None:
    project_root = Path.cwd()
    site_packages = tmp_path / "site-packages"
    shutil.copytree(
        project_root / "src/jindiao",
        site_packages / "jindiao",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import jindiao; "
            "assert 'site-packages' in str(jindiao.__file__); "
            "from jindiao.reporting.catalog import REPORT_CATALOG; "
            "assert REPORT_CATALOG.module_count == 8; "
            "from jindiao.investigation.catalog import load_check_catalog; "
            "assert load_check_catalog(); "
            "from jindiao.orchestration.team_spec import _SKILLS_ROOT; "
            "assert _SKILLS_ROOT.is_dir()",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(site_packages),
            "JINDIAO_PROJECT_ROOT": str(project_root),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


def test_dependency_manifests_have_no_local_source_dependency() -> None:
    combined = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("requirements.txt", "pyproject.toml", "uv.lock")
    )

    assert "/Users/" not in combined
    assert "../agent-core" not in combined
    assert "../deepsearch" not in combined
    assert "file://" not in combined
    assert re.search(r"openjiuwen(?:\[[^\]]+\])?==0\.1\.17", combined)
    assert "cbab9c29219873af3da8a96eba85232a0f7154bc" in combined


def test_env_example_contains_only_placeholders_for_credentials() -> None:
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "TIANYANCHA_MCP_AUTHORIZATION=" in content
    assert "MODEL_API_KEY=" in content
    assert ("mc" + "po1_") not in content
    assert not re.search(r"(?:AUTHORIZATION|API_KEY)=[^<\s][^\n]+", content)


def test_container_is_single_service_non_root_and_uses_process_healthcheck() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "HEALTHCHECK" in dockerfile
    assert "USER jindiao" in dockerfile
    assert "uvicorn" in dockerfile
    assert compose.count("    build:") == 1
    assert all(name not in compose.casefold() for name in ("postgres", "redis", "mysql", "kafka"))


def test_readme_documents_one_api_skills_benchmark_and_mock_demo() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "POST /api/v1/due-diligence/result" in readme
    assert "make mock-demo" in readme
    assert "make benchmark" in readme
    assert "tyc-evidence-acquisition" in readme
    assert "evidence-backed-due-diligence" in readme
    assert "feedback-evolved-reporting" in readme
    assert "0.76125" in readme and "0.65625" in readme


def test_compare_agents_script_reuses_the_official_benchmark_entrypoint() -> None:
    script = Path("scripts/compare_agents.py")

    assert script.is_file()
    content = script.read_text(encoding="utf-8")
    assert "from jindiao.evaluation.benchmark import main" in content
    assert "BenchmarkRunner(" not in content


def test_community_and_product_documents_exist() -> None:
    required = (
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "docs/product-design.md",
        "docs/api/README.md",
        "docs/architecture/README.md",
        "docs/deployment/README.md",
        "docs/evaluation/README.md",
    )
    assert all(Path(path).is_file() and Path(path).stat().st_size > 200 for path in required)


def test_repository_sources_contain_no_private_path_or_real_credential() -> None:
    private_home = "/" + "Users" + "/" + "yamzhao"
    credential_prefix = "mc" + "po1_"
    ignored_parts = {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "artifacts",
    }
    text_names = {"Dockerfile", "LICENSE", "Makefile", "README.md", "SECURITY.md"}
    text_suffixes = {
        ".example",
        ".json",
        ".jsonl",
        ".md",
        ".py",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
    violations: list[str] = []
    for path in Path(".").rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        if path.name not in text_names and path.suffix not in text_suffixes:
            continue
        content = path.read_text(encoding="utf-8")
        if private_home in content or credential_prefix in content:
            violations.append(path.as_posix())

    assert violations == []


def test_frozen_release_manifest_matches_versioned_components() -> None:
    manifest = json.loads(Path("release/frozen-manifest-v1.json").read_text(encoding="utf-8"))

    assert manifest["release_id"] == "jindiao-demo-v1"
    for path, expected in manifest["files"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
    for path, expected in manifest["trees"].items():
        assert _tree_digest(Path(path)) == expected
