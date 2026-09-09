from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

ROOT = Path(__file__).resolve().parents[2]


class DockerCall(TypedDict):
    args: list[str]
    model: str | None
    bind: str | None
    mount: str | None


@pytest.mark.parametrize("directory", ["bin", "deploy/ecs"])
@pytest.mark.parametrize("action", ["start", "restart", "stop"])
def test_shell_help_from_unrelated_directory(directory: str, action: str, tmp_path: Path) -> None:
    script = ROOT / directory / f"{action}.sh"
    assert script.is_file(), "shell lifecycle entry point not implemented"
    assert os.access(script, os.X_OK)
    result = subprocess.run([str(script), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


@pytest.fixture
def shell_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "bin", project / "bin")
    shutil.copytree(ROOT / "deploy", project / "deploy")
    (project / "scripts/deployment").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/deployment/ecs_host.py", project / "scripts/deployment/ecs_host.py"
    )
    shutil.copyfile(ROOT / "compose.yaml", project / "compose.yaml")
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    state_file = tmp_path / "docker-state.json"
    state_file.write_text(json.dumps({"exists": True, "running": True, "health": "healthy"}))
    log = tmp_path / "commands.jsonl"
    fake = tools_dir / "docker"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['COMMAND_LOG']).open('a') as f:
    f.write(json.dumps({'args':args,'model':os.environ.get('MODEL_API_KEY'),
      'bind':os.environ.get('JINDIAO_BIND_HOST'),'mount':os.environ.get('JINDIAO_ARTIFACT_MOUNT')})+'\\n')
if args[:1] == ['--host']: args = args[2:]
state_path = Path(os.environ['DOCKER_STATE'])
state = json.loads(state_path.read_text())
if os.environ.get('DOCKER_FAIL') == (args[0] if args else ''): sys.exit(7)
if args[:2] == ['context','show']: print('desktop')
elif args[:2] == ['context','inspect']: print(os.environ.get('TEST_ENDPOINT','unix:///local.sock'))
elif args[:2] == ['container','ls']:
    if state['exists']:
        local = 'label=com.docker.compose.project=jindiao-local' in args
        print('abc123' if local else 'abc123 jindiao-ecs')
elif args[:1] == ['inspect']:
    if 'Running' in args[args.index('--format')+1]:
        print(('true' if state['running'] else 'false')+' '+state['health'])
elif args[:1] == ['exec']:
    if os.environ.get('ACTIVE_RUNS') == '1': sys.exit(2)
elif args[:1] == ['compose']:
    state.update(exists=True,running=True)
elif args[:1] == ['start'] or args[:1] == ['restart']:
    state.update(running=True)
elif args[:1] == ['stop']:
    state.update(running=False)
state_path.write_text(json.dumps(state))
"""
    )
    fake.chmod(0o755)
    flock = tools_dir / "flock"
    flock.write_text("#!/bin/sh\nexit 0\n")
    flock.chmod(0o755)
    config = project / "deploy/ecs/config.json"
    config.write_text(
        json.dumps(
            {
                "remote_root": str(project),
                "docker_bin": str(fake),
                "docker_host": "unix:///run/jindiao-docker.sock",
                "container": "jindiao-ecs",
                "runtime_env": "/opt/jindiao/runtime/not-read.env",
            }
        )
    )
    env = {
        **os.environ,
        "PATH": f"{tools_dir}:/usr/bin:/bin",
        "COMMAND_LOG": str(log),
        "DOCKER_STATE": str(state_file),
        "JINDIAO_ECS_PYTHON": sys.executable,
        "MODEL_API_KEY": "do-not-forward",
        "MODEL_NAME": "wrong-model",
    }
    env.pop("DOCKER_HOST", None)
    env.pop("DOCKER_CONTEXT", None)
    return project, env


def invoke(
    project: Path, env: dict[str, str], directory: str, action: str, *args: str
) -> subprocess.CompletedProcess[str]:
    script = project / directory / f"{action}.sh"
    assert script.exists(), "shell lifecycle entry point not implemented"
    return subprocess.run(
        [str(script), *args], cwd=project.parent, env=env, capture_output=True, text=True
    )


def commands(env: dict[str, str]) -> list[DockerCall]:
    path = Path(env["COMMAND_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("directory", ["bin", "deploy/ecs"])
@pytest.mark.parametrize("action", ["start", "restart", "stop"])
def test_lifecycle_dry_run_does_not_call_docker_or_create_env(
    shell_environment: tuple[Path, dict[str, str]],
    directory: str,
    action: str,
) -> None:
    project, env = shell_environment
    result = invoke(project, env, directory, action, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert commands(env) == []
    assert not (project / ".env.local").exists()
    assert not (project / "deployment.lock").exists()


def test_local_start_defaults_to_real_without_overwriting_config(
    shell_environment: tuple[Path, dict[str, str]],
) -> None:
    project, env = shell_environment
    (project / ".env").write_text("existing-real-config")
    result = invoke(project, env, "bin", "start", "--port", "18080")
    assert result.returncode == 0, result.stderr
    assert (project / ".env").read_text() == "existing-real-config"
    assert not (project / ".env.local").exists()
    call = next(item for item in commands(env) if "up" in item["args"])
    assert call["model"] is None
    assert call["bind"] == "127.0.0.1" and call["mount"] == "local-artifacts"
    assert "--wait" in call["args"]
    assert str(project / "deploy/local/compose.live.yaml") in call["args"]
    assert str(project / "deploy/local/compose.local-deps.yaml") in call["args"]
    (project / ".env").write_text("custom-config")
    assert invoke(project, env, "bin", "start").returncode == 0
    assert (project / ".env").read_text() == "custom-config"


def test_local_start_uses_existing_dependency_image_offline() -> None:
    overlay = ROOT / "deploy/local/compose.local-deps.yaml"
    dockerfile = ROOT / "deploy/local/Dockerfile.local"
    assert overlay.is_file()
    assert dockerfile.is_file()
    overlay_text = overlay.read_text()
    dockerfile_text = dockerfile.read_text()
    assert "jindiao:local-source" in overlay_text
    assert "network: none" in overlay_text
    assert "FROM jindiao:local" in dockerfile_text
    assert "pip install" not in dockerfile_text
    assert "uv " not in dockerfile_text


def test_local_real_refuses_missing_env_and_mock_is_explicit(
    shell_environment: tuple[Path, dict[str, str]],
) -> None:
    project, env = shell_environment
    assert invoke(project, env, "bin", "start").returncode != 0
    assert not (project / ".env").exists() and not (project / ".env.local").exists()
    result = invoke(project, env, "bin", "start", "--mock")
    assert result.returncode == 0, result.stderr
    assert any(
        str(project / "deploy/local/compose.mock.yaml") in call["args"] for call in commands(env)
    )


@pytest.mark.parametrize("directory", ["bin", "deploy/ecs"])
@pytest.mark.parametrize("action", ["restart", "stop"])
def test_controls_only_existing_container_and_keeps_data(
    shell_environment: tuple[Path, dict[str, str]],
    directory: str,
    action: str,
) -> None:
    project, env = shell_environment
    args = ["--maintenance-confirmed"] if directory == "deploy/ecs" else []
    result = invoke(project, env, directory, action, *args)
    assert result.returncode == 0, result.stderr
    calls = [item["args"] for item in commands(env)]
    assert any(action in call and call[-1] == "abc123" for call in calls)
    assert not any(
        token in call for call in calls for token in ("rm", "prune", "build", "up", "down")
    )
    assert not (project / ".env.local").exists()
    assert json.loads(Path(env["DOCKER_STATE"]).read_text())["running"] == (action == "restart")


@pytest.mark.parametrize("action", ["restart", "stop"])
def test_ecs_requires_maintenance_and_refuses_active_runs(
    shell_environment: tuple[Path, dict[str, str]], action: str
) -> None:
    project, env = shell_environment
    result = invoke(project, env, "deploy/ecs", action)
    assert result.returncode != 0 and "maintenance-confirmed" in result.stderr
    assert commands(env) == []
    result = invoke(
        project, {**env, "ACTIVE_RUNS": "1"}, "deploy/ecs", action, "--maintenance-confirmed"
    )
    assert result.returncode != 0
    assert not any(action in call["args"] for call in commands(env))
    assert json.loads(Path(env["DOCKER_STATE"]).read_text())["running"]


@pytest.mark.parametrize("directory", ["bin", "deploy/ecs"])
def test_stop_is_idempotent_and_restart_checks_health(
    shell_environment: tuple[Path, dict[str, str]], directory: str
) -> None:
    project, env = shell_environment
    state = Path(env["DOCKER_STATE"])
    state.write_text(json.dumps({"exists": False, "running": False, "health": "healthy"}))
    args = ["--maintenance-confirmed"] if directory == "deploy/ecs" else []
    assert invoke(project, env, directory, "stop", *args).returncode == 0
    assert invoke(project, env, directory, "restart", *args).returncode != 0
    state.write_text(json.dumps({"exists": True, "running": True, "health": "unhealthy"}))
    assert invoke(project, env, directory, "restart", *args).returncode != 0


def test_local_rejects_remote_context_and_propagates_docker_failure(
    shell_environment: tuple[Path, dict[str, str]],
) -> None:
    project, env = shell_environment
    result = invoke(project, {**env, "TEST_ENDPOINT": "ssh://production"}, "bin", "start")
    assert result.returncode != 0 and "Unix" in result.stderr
    result = invoke(project, {**env, "DOCKER_FAIL": "restart"}, "bin", "restart")
    assert result.returncode != 0


def test_local_start_no_longer_requires_python_orchestration() -> None:
    assert not (ROOT / "scripts/deployment/local.py").exists()
    assert "scripts/deploy.py" not in (ROOT / "bin/local").read_text()


@pytest.mark.skipif(shutil.which("docker") is None, reason="optional real Compose validation")
def test_live_compose_forces_real_mode_and_requires_credentials(tmp_path: Path) -> None:
    dotenv = tmp_path / "runtime.env"
    dotenv.write_text(
        "MODEL_PROVIDER=OpenAI\nMODEL_NAME=test-real\nMODEL_BASE_URL=https://model.invalid/v1\n"
        "MODEL_API_KEY=test-only\nTIANYANCHA_MCP_AUTHORIZATION=test-only\n"
        "JINDIAO_DATA_SOURCE_MODE=mock\nJINDIAO_AGENT_RUNTIME_MODE=deterministic_harness\n"
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MODEL_", "JINDIAO_", "TIANYANCHA_", "COMPOSE_"))
    }
    env["JINDIAO_ENV_FILE"] = str(dotenv)
    command = [
        "docker",
        "compose",
        "--env-file",
        str(dotenv),
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "deploy/local/compose.live.yaml"),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    settings = json.loads(result.stdout)["services"]["jindiao"]["environment"]
    assert settings["JINDIAO_AGENT_RUNTIME_MODE"] == "formal"
    assert settings["JINDIAO_DATA_SOURCE_MODE"] == "tianyancha"
    assert settings["JINDIAO_ALLOW_DEGRADED_MOCK"] == "false"
    dotenv.write_text("MODEL_PROVIDER=OpenAI\n")
    assert subprocess.run(command, env=env, capture_output=True).returncode != 0
