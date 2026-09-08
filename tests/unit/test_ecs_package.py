from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def helper(name: str) -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module("deployment." + name)


def make_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    package_module = helper("ecs")
    assert hasattr(package_module, "create_package"), "offline ECS packaging not implemented"
    root = tmp_path / "workspace"
    root.mkdir()
    for name in ("Dockerfile", "pyproject.toml", "requirements.txt", "uv.lock", "README.md"):
        (root / name).write_text(name)
    for name in ("src", "config", "mock_data", "skills"):
        (root / name).mkdir()
        (root / name / "uncommitted.txt").write_text(name)
    (root / ".env").write_text("MODEL_API_KEY=not-in-package")
    shutil.copytree(ROOT / "deploy/ecs", root / "deploy/ecs")
    shutil.copyfile(ROOT / "deploy/service-common.sh", root / "deploy/service-common.sh")
    (root / "scripts/deployment").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/deployment/ecs_host.py", root / "scripts/deployment/ecs_host.py"
    )

    def forbid_process(*args: object, **kwargs: object) -> None:
        raise AssertionError("packaging must not invoke SSH, Docker, or any subprocess")

    with monkeypatch.context() as context:
        context.setattr(subprocess, "run", forbid_process)
        archive = package_module.create_package(root, tmp_path / "output", "ecs-test")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(archive) as bundle:
        # Test-created package has been inspected; do not use this to extract external input.
        for member in bundle.getmembers():
            assert member.isfile() and member.name.startswith("ecs-test/")
            target = extracted / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = bundle.extractfile(member)
            assert stream is not None
            target.write_bytes(stream.read())
            target.chmod(member.mode)
    return archive, extracted / "ecs-test"


def test_offline_package_is_complete_and_excludes_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, package = make_package(tmp_path, monkeypatch)
    assert {path.name for path in package.iterdir()} == {
        "source.tar.gz",
        "ecs_host.py",
        "deploy.sh",
        "start.sh",
        "restart.sh",
        "stop.sh",
        "_service.sh",
        "_service-common.sh",
        "config.example.json",
        "package.json",
    }
    checksum = Path(str(archive) + ".sha256").read_text().split()
    assert checksum == [hashlib.sha256(archive.read_bytes()).hexdigest(), archive.name]
    assert os.access(package / "deploy.sh", os.X_OK)
    for name in ("start.sh", "restart.sh", "stop.sh", "_service.sh", "_service-common.sh"):
        assert os.access(package / name, os.X_OK)
    manifest = json.loads((package / "package.json").read_text())
    assert manifest["release"] == "ecs-test"
    assert "host" not in json.loads((package / "config.example.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((package / name).read_bytes()).hexdigest() == digest
    with tarfile.open(package / "source.tar.gz") as source:
        assert "src/uncommitted.txt" in source.getnames()
        assert not any(".env" in name for name in source.getnames())


def test_packaging_does_not_overwrite_existing_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, _ = make_package(tmp_path, monkeypatch)
    before = archive.read_bytes()
    with pytest.raises((ValueError, FileExistsError)):
        helper("ecs").create_package(tmp_path / "workspace", archive.parent, "ecs-test")
    assert archive.read_bytes() == before


def test_server_package_verification_rejects_modified_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = make_package(tmp_path, monkeypatch)
    controller = helper("ecs_host")
    assert hasattr(controller, "verify_package"), "server package verification not implemented"
    assert controller.verify_package(package)["release"] == "ecs-test"
    (package / "ecs_host.py").write_text("modified")
    with pytest.raises(ValueError, match="checksum"):
        controller.verify_package(package)


def test_server_help_dry_run_and_maintenance_guard_without_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = make_package(tmp_path, monkeypatch)
    env = {**os.environ, "JINDIAO_ECS_PYTHON": sys.executable, "PATH": "/usr/bin:/bin"}
    before = {path.name for path in package.iterdir()}
    for options in (["--help"], ["--config", str(package / "config.example.json"), "--dry-run"]):
        result = subprocess.run(
            [str(package / "deploy.sh"), *options],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout or "DRY RUN" in result.stdout
    result = subprocess.run(
        [str(package / "deploy.sh"), "--config", str(package / "config.example.json"), "--apply"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "maintenance-confirmed" in result.stderr
    assert {path.name for path in package.iterdir()} == before


def test_controller_avoids_python37_and_newer_runtime_features() -> None:
    path = ROOT / "scripts/deployment/ecs_host.py"
    tree = ast.parse(path.read_text(), feature_version=(3, 6))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in ast.walk(tree)
    ), "Python 3.6 cannot import future annotations"
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "typing"
        and any(alias.name == "Protocol" for alias in node.names)
        for node in ast.walk(tree)
    ), "typing.Protocol is unavailable on Python 3.6"
    assert "capture_output=" not in path.read_text()
    assert "text=True" not in path.read_text()


def test_old_ssh_entrypoint_is_removed() -> None:
    assert not (ROOT / "bin/ecs").exists(), "old one-command SSH deployment remains exposed"
    assert not (ROOT / "deploy/ecs/config.example.toml").exists()
    code = (ROOT / "scripts/deployment/ecs.py").read_text()
    assert "SSH_OPTIONS" not in code and '"scp"' not in code
