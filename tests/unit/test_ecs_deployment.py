from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def host_module() -> ModuleType:
    assert (ROOT / "scripts/deployment/ecs_host.py").exists(), "ECS publisher not implemented"
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module("deployment.ecs_host")


def run_tree(root: Path, status: str = "completed") -> None:
    run = root / "run-state/runs/run-001"
    run.mkdir(parents=True)
    (run / "metadata.json").write_text(json.dumps({"status": status}))
    (run / "result.json").write_text('{"report":"retained"}')
    (root / "run-state/runs/idempotency.json").write_text('{"owner:key":["run-001","hash"]}')


def test_volume_copy_preserves_all_historical_files_and_source(tmp_path: Path) -> None:
    module = host_module()
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    run_tree(source)
    before = module.tree_manifest(source)
    result = module.copy_volume(source, destination)
    assert result["files"] == 3
    assert module.tree_manifest(source) == before == module.tree_manifest(destination)
    assert (destination / "run-state/runs/idempotency.json").exists()


@pytest.mark.parametrize("status", ["accepted", "running", "unexpected-new-status"])
def test_active_or_unknown_run_status_blocks_copy(tmp_path: Path, status: str) -> None:
    module = host_module()
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    run_tree(source, status)
    with pytest.raises(ValueError, match="nonterminal"):
        module.copy_volume(source, destination)
    assert not list(destination.iterdir())


def test_copy_refuses_existing_data_or_symlinks(tmp_path: Path) -> None:
    module = host_module()
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    run_tree(source)
    (destination / "existing").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        module.copy_volume(source, destination)
    assert (destination / "existing").read_text() == "keep"
    empty = tmp_path / "empty"
    empty.mkdir()
    (source / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        module.copy_volume(source, empty)


class FakeDocker:
    """Only the external Docker boundary is simulated; publisher transitions run unchanged."""

    def __init__(self, failure: str = "") -> None:
        self.failure = failure
        self.old = {
            "running": True,
            "image": "old-image",
            "restart": "unless-stopped",
            "volume": "old-data",
        }
        self.containers: dict[str, dict[str, Any]] = {"jindiao-ecs": self.old}
        self.volumes = {"old-data"}
        self.commands: list[list[str]] = []

    def inspect(self, name: str, field: str) -> Any:
        item = self.containers[name]
        if field == ".State":
            bad = (self.failure == "preflight" and "preflight" in name) or (
                self.failure == "switch" and name == "jindiao-ecs" and item is not self.old
            )
            return {
                "Running": item["running"],
                "Health": {"Status": "unhealthy" if bad else "healthy"},
            }
        if field == ".Mounts":
            return [
                {
                    "Type": "volume",
                    "Destination": "/app/artifacts",
                    "Name": item["volume"],
                    "RW": True,
                }
            ]
        if field == ".Image":
            return item["image"]
        if field == ".HostConfig.NetworkMode":
            return "host"
        if field == ".HostConfig.RestartPolicy":
            return {"Name": item["restart"], "MaximumRetryCount": 0}
        raise AssertionError(field)

    def run(self, args: list[str], **kwargs: Any) -> str:
        self.commands.append(args)
        if args[:2] == ["container", "ls"]:
            return "\n".join(self.containers)
        if args[:2] == ["volume", "ls"]:
            return "\n".join(sorted(self.volumes))
        if args[:2] == ["volume", "create"]:
            self.volumes.add(args[2])
        elif args[0] == "create":
            name = args[args.index("--name") + 1]
            volume = next(
                arg.split("src=")[1].split(",")[0] for arg in args if arg.startswith("type=volume")
            )
            self.containers[name] = {
                "running": False,
                "image": "new-image",
                "restart": "no",
                "volume": volume,
            }
        elif args[0] in {"start", "stop"}:
            self.containers[args[-1]]["running"] = args[0] == "start"
        elif args[0] == "rename":
            self.containers[args[2]] = self.containers.pop(args[1])
        elif args[0] == "update":
            self.containers[args[-1]]["restart"] = args[1].split("=", 1)[1]
        elif args[:2] == ["image", "inspect"]:
            return "amd64" if "Architecture" in args[3] else "new-image"
        elif args[0] == "exec":
            if args[-2:] == ["id", "-u"] or args[-2:] == ["id", "-g"]:
                return "999"
            if self.failure == "active" and args[-1] == "check-active":
                raise RuntimeError("active run")
        elif args[0] == "run":
            if self.failure == "copy" and "copy-volume" in args:
                raise RuntimeError("copy failed")
            if self.failure == "active" and "check-volume" in args:
                raise RuntimeError("active run")
            return '{"files": 3, "sha256": "verified"}'
        return ""


def publish(tmp_path: Path, docker: FakeDocker) -> dict[str, Any]:
    module = host_module()
    (tmp_path / "source").mkdir()
    (tmp_path / "runtime.env").write_text("private-not-printed")
    return dict(
        module.publish(
            {
                "container": "jindiao-ecs",
                "release": "ecs-test",
                "port": 8080,
                "preflight_port": 18081,
            },
            tmp_path,
            docker,
        )
    )


def test_successful_switch_preserves_backup_volume_and_changes_only_new_service(
    tmp_path: Path,
) -> None:
    docker = FakeDocker()
    receipt = publish(tmp_path, docker)
    assert receipt["status"] == "published"
    assert docker.containers["jindiao-ecs"]["image"] == "new-image"
    assert docker.containers["jindiao-ecs"]["running"]
    assert docker.containers["jindiao-ecs-before-ecs-test"] is docker.old
    assert docker.old["volume"] == "old-data"
    assert not docker.old["running"]
    assert docker.old["restart"] == "no"
    assert "old-data" in docker.volumes
    assert not any(command[:2] == ["volume", "rm"] for command in docker.commands)


def test_candidate_image_build_uses_host_network_for_ecs_dns(tmp_path: Path) -> None:
    docker = FakeDocker()
    publish(tmp_path, docker)
    build = next(command for command in docker.commands if command[0] == "build")
    assert build[:5] == ["build", "--network", "none", "--platform", "linux/amd64"]
    assert "JINDIAO_BASE_IMAGE=jindiao:deps-amd64" in build
    assert "JINDIAO_INSTALL_DEPS=false" in build


@pytest.mark.parametrize("failure", ["preflight", "active", "copy", "switch"])
def test_failed_publish_keeps_or_restores_original_service(tmp_path: Path, failure: str) -> None:
    docker = FakeDocker(failure)
    with pytest.raises(RuntimeError):
        publish(tmp_path, docker)
    assert docker.containers["jindiao-ecs"] is docker.old
    assert docker.old["running"]
    assert docker.old["restart"] == "unless-stopped"
    assert docker.old["volume"] == "old-data"
    if failure in {"preflight", "active"}:
        assert ["stop", "--time", "30", "jindiao-ecs"] not in docker.commands
    assert not any(
        "prune" in command or command[:2] == ["volume", "rm"] for command in docker.commands
    )


def test_existing_release_resources_are_not_reused(tmp_path: Path) -> None:
    docker = FakeDocker()
    docker.volumes.add("jindiao-ecs-data-ecs-test")
    with pytest.raises(ValueError, match="already exists"):
        publish(tmp_path, docker)
    assert docker.old["running"]


def test_server_apply_requires_maintenance_confirmation_without_docker(
    tmp_path: Path,
) -> None:
    entry = ROOT / "deploy/ecs/deploy.sh"
    assert entry.exists()
    result = subprocess.run(
        [str(entry), "--config", str(ROOT / "deploy/ecs/config.example.json"), "--apply"],
        cwd=tmp_path,
        env={**os.environ, "JINDIAO_ECS_PYTHON": sys.executable},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "maintenance-confirmed" in result.stderr


def test_volume_verification_detects_changed_history_after_startup(tmp_path: Path) -> None:
    module = host_module()
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    run_tree(source)
    module.copy_volume(source, destination)
    assert hasattr(module, "verify_volume"), "post-start history verification not implemented"
    module.verify_volume(source, destination)
    (destination / "run-state/runs/run-001/result.json").write_text("changed")
    with pytest.raises(ValueError, match="historical"):
        module.verify_volume(source, destination)


def test_rollback_still_runs_if_preflight_cleanup_fails(tmp_path: Path) -> None:
    class CleanupFailure(FakeDocker):
        def run(self, args: list[str], **kwargs: Any) -> str:
            if (
                args[0] == "stop"
                and "preflight" in args[-1]
                and any(name.startswith("jindiao-ecs-before") for name in self.containers)
            ):
                raise RuntimeError("probe cleanup failed")
            return super().run(args, **kwargs)

    docker = CleanupFailure("switch")
    with pytest.raises(RuntimeError):
        publish(tmp_path, docker)
    assert docker.containers["jindiao-ecs"] is docker.old
    assert docker.old["running"]


def test_controller_parses_as_python36_and_uses_real_settings_contract() -> None:
    module = host_module()
    ast.parse((ROOT / "scripts/deployment/ecs_host.py").read_text(), feature_version=(3, 6))
    environment = {
        **os.environ,
        "MODEL_PROVIDER": "offline_mock",
        "MODEL_NAME": "deterministic-mock",
        "JINDIAO_AGENT_RUNTIME_MODE": "deterministic_harness",
        "JINDIAO_DATA_SOURCE_MODE": "mock",
        "JINDIAO_EXECUTION_PROFILE": "attached",
        "JINDIAO_STORAGE_BACKEND": "local",
        "JINDIAO_ARTIFACT_ROOT": "/app/artifacts",
    }
    result = subprocess.run(
        [sys.executable, "-c", module.SETTINGS_CHECK],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "runtime-profile-verified"
