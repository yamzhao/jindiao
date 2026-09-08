from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name: str) -> ModuleType:
    assert (ROOT / f"scripts/deployment/{name}.py").is_file(), "deployment helper not implemented"
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module(f"deployment.{name}")


@pytest.mark.parametrize("entry", ["local", "ecs-package", "agentarts"])
def test_entrypoint_help_from_other_directory(entry: str, tmp_path: Path) -> None:
    path = ROOT / "bin" / entry
    assert path.is_file(), "bin entry point not implemented"
    assert os.access(path, os.X_OK)
    result = subprocess.run([str(path), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def source_tree(root: Path) -> None:
    for name in ("Dockerfile", "pyproject.toml", "requirements.txt", "uv.lock", "README.md"):
        (root / name).write_text(name)
    for name in ("src", "config", "mock_data", "skills"):
        (root / name).mkdir()
        (root / name / "data.txt").write_text(name)


def test_snapshot_is_whitelisted_and_includes_untracked_runtime_source(tmp_path: Path) -> None:
    common = module("common")
    source_tree(tmp_path)
    (tmp_path / ".env").write_text("MODEL_API_KEY=never-publish")
    (tmp_path / "src" / ".env.local").write_text("never-publish")
    (tmp_path / "src" / "new.py").write_text("# uncommitted work")
    (tmp_path / "src" / "llm.log.2").write_text("private report")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "report.json").write_text("private report")
    archive = tmp_path / "snapshot.tar.gz"
    manifest = common.snapshot(tmp_path, archive)
    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
    assert "src/new.py" in names
    assert "release-manifest.json" in names
    assert not any(".env" in name or "log.2" in name or "artifacts" in name for name in names)
    assert manifest["files"]["src/new.py"]
    common.verify_source(tmp_path, manifest)
    (tmp_path / "src" / "new.py").write_text("# changed after snapshot")
    with pytest.raises(ValueError, match="changed"):
        common.verify_source(tmp_path, manifest)


def test_snapshot_rejects_symlinks(tmp_path: Path) -> None:
    common = module("common")
    source_tree(tmp_path)
    (tmp_path / "src" / "secret.py").symlink_to(tmp_path / "README.md")
    with pytest.raises(ValueError, match="symlink"):
        common.snapshot(tmp_path, tmp_path / "snapshot.tar.gz")


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "root@host;touch bad", "host\ncommand"])
def test_ecs_server_config_rejects_ssh_fields(host: str) -> None:
    ecs = module("ecs_host")
    with pytest.raises(ValueError):
        ecs.validate_config({"host": host, "runtime_env": "/opt/jindiao/runtime/live.env"})


def test_ecs_defaults_preserve_existing_daemon_and_loopback() -> None:
    config = module("ecs_host").validate_config({"runtime_env": "/opt/jindiao/runtime/live.env"})
    assert config["docker_host"] == "unix:///run/jindiao-docker.sock"
    assert config["docker_bin"] == "/opt/jindiao/tools/docker/docker"
    assert config["port"] == 8080
    assert config["preflight_port"] != config["port"]


@pytest.mark.parametrize("path", ["/", "/opt", "/opt/jindiao/../else", "relative"])
def test_ecs_rejects_unsafe_remote_root(path: str) -> None:
    with pytest.raises(ValueError):
        module("ecs_host").validate_config(
            {"runtime_env": "/opt/jindiao/runtime/live.env", "remote_root": path}
        )


def test_agentarts_verifies_remote_single_arch_digest_and_config() -> None:
    agentarts = module("agentarts")
    payload = {
        "Descriptor": {
            "digest": "sha256:" + "a" * 64,
            "platform": {"os": "linux", "architecture": "arm64"},
        },
        "SchemaV2Manifest": {"config": {"digest": "sha256:" + "b" * 64}},
    }
    assert agentarts.verify_manifest(json.dumps(payload), "sha256:" + "b" * 64).endswith("a" * 64)
    with pytest.raises(ValueError, match="config"):
        agentarts.verify_manifest(json.dumps(payload), "sha256:" + "c" * 64)
    payload["Descriptor"]["platform"]["architecture"] = "amd64"  # type: ignore[index]
    with pytest.raises(ValueError, match="arm64"):
        agentarts.verify_manifest(json.dumps(payload), "sha256:" + "b" * 64)


def test_all_dry_runs_are_offline(tmp_path: Path) -> None:
    for entry, args in (
        ("local", []),
        ("ecs-package", []),
        ("agentarts", ["--image", "registry.example/team/jindiao:test-arm64"]),
    ):
        path = ROOT / "bin" / entry
        assert path.exists(), "bin entry point not implemented"
        result = subprocess.run(
            [str(path), *args, "--dry-run"],
            cwd=tmp_path,
            env={**os.environ, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "DRY RUN" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("member", ["../escape", "/tmp/escape"])
def test_snapshot_extraction_refuses_path_traversal(tmp_path: Path, member: str) -> None:
    common = module("common")
    archive = tmp_path / "malicious.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo(member)
        info.size = 4
        bundle.addfile(info, io.BytesIO(b"data"))
    with pytest.raises(ValueError, match="Unsafe"):
        common.extract_snapshot(archive, tmp_path / "extracted")


def test_command_failure_does_not_echo_secret_stderr() -> None:
    common = module("common")
    with pytest.raises(RuntimeError) as error:
        common.Runner().run([sys.executable, "-c", "import sys; sys.exit('never-print-secret')"])
    assert "never-print-secret" not in str(error.value)
    assert "exit 1" in str(error.value)


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected"),
    [(0, "", True), (1, "manifest unknown", False), (1, "unauthorized: private", None)],
)
def test_registry_tag_check_fails_closed_on_authentication_errors(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stderr: str,
    expected: bool | None,
) -> None:
    common = module("common")

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["docker"], returncode, "{}", stderr)

    monkeypatch.setattr(common.subprocess, "run", fake_run)
    runner = common.Runner()
    assert hasattr(runner, "remote_tag_exists"), "registry collision guard not implemented"
    if expected is None:
        with pytest.raises(RuntimeError, match="registry"):
            runner.remote_tag_exists("registry/team/image:tag")
    else:
        assert runner.remote_tag_exists("registry/team/image:tag") is expected


def test_local_engine_refuses_remote_docker_context(monkeypatch: pytest.MonkeyPatch) -> None:
    common = module("common")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "ssh://production")
    assert hasattr(common, "require_local_engine"), "local Docker boundary not implemented"
    with pytest.raises(ValueError, match="local Unix"):
        common.require_local_engine(common.Runner())


class ImageRunner:
    def __init__(self, collision: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.collision = collision
        self.registry_checks = 0

    def remote_tag_exists(self, image: str) -> bool:
        self.registry_checks += 1
        return self.registry_checks == self.collision

    def run(self, args: list[str], **kwargs: Any) -> str:
        self.commands.append(args)
        if args[1:3] == ["context", "show"]:
            return "local"
        if args[1:3] == ["context", "inspect"]:
            return "unix:///var/run/docker.sock"
        if args[1:3] == ["image", "inspect"]:
            return "arm64" if "Architecture" in args[4] else "sha256:" + "b" * 64
        if args[1] == "inspect":
            return '{"Running":true,"Health":{"Status":"healthy"}}'
        if args[-2:] == ["id", "-u"]:
            return "999"
        if args[1:3] == ["manifest", "inspect"]:
            return json.dumps(
                {
                    "Descriptor": {
                        "digest": "sha256:" + "a" * 64,
                        "platform": {"os": "linux", "architecture": "arm64"},
                    },
                    "SchemaV2Manifest": {"config": {"digest": "sha256:" + "b" * 64}},
                }
            )
        return ""


def test_agentarts_apply_builds_verifies_and_records_delivery_without_runtime_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agentarts = module("agentarts")
    source_tree(tmp_path)
    runner = ImageRunner()
    monkeypatch.setattr(agentarts, "Runner", lambda: runner)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    assert (
        agentarts.main(tmp_path, ["--image", "registry.example/team/image:unique", "--apply"]) == 0
    )
    receipt = json.loads(next((tmp_path / "artifacts").rglob("receipt.json")).read_text())
    assert receipt["status"] == "image-delivered"
    assert receipt["manifest_digest"] == "sha256:" + "a" * 64
    assert any(command[1] == "push" for command in runner.commands)
    assert not any("--env-file" in command or "ssh" in command for command in runner.commands)
    create = next(command for command in runner.commands if command[1] == "create")
    assert create[create.index("--network") + 1] == "none"
    assert not any(argument in {"-p", "--publish"} for argument in create)


@pytest.mark.parametrize("collision", [1, 2])
def test_agentarts_never_pushes_over_existing_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision: int,
) -> None:
    agentarts = module("agentarts")
    source_tree(tmp_path)
    runner = ImageRunner(collision)
    monkeypatch.setattr(agentarts, "Runner", lambda: runner)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    with pytest.raises(ValueError, match=r"[Rr]egistry tag"):
        agentarts.main(tmp_path, ["--image", "registry.example/team/image:unique", "--apply"])
    assert not any(command[1] == "push" for command in runner.commands)


@pytest.mark.skipif(shutil.which("make") is None, reason="optional Makefile verification")
def test_make_default_does_not_start_a_service() -> None:
    result = subprocess.run(["make", "-n"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    assert "uv sync --frozen" in result.stdout
    assert "bin/local" not in result.stdout


def test_agentarts_accepts_oci_single_image_manifest() -> None:
    payload = {
        "Descriptor": {
            "digest": "sha256:" + "a" * 64,
            "platform": {"os": "linux", "architecture": "arm64"},
        },
        "OCIManifest": {"config": {"digest": "sha256:" + "b" * 64}},
    }
    assert module("agentarts").verify_manifest(json.dumps(payload), "sha256:" + "b" * 64)
