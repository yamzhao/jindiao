# ruff: noqa: UP006, UP021, UP022, UP035, UP045
# Compatibility: this standalone file must import and run on Python 3.6 without extra packages.
"""Standalone ECS controller (Python 3.6+) and in-container volume verifier.

This file is uploaded with the source archive; it does not install host software.
Only the Docker subprocess boundary is abstracted. No business requests are sent.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set

DEFAULTS = {
    "remote_root": "/opt/jindiao",
    "container": "jindiao-ecs",
    "docker_bin": "/opt/jindiao/tools/docker/docker",
    "docker_host": "unix:///run/jindiao-docker.sock",
    "port": 8080,
    "preflight_port": 18081,
}  # type: Dict[str, Any]
PACKAGE_FILES = {
    "source.tar.gz",
    "ecs_host.py",
    "deploy.sh",
    "config.example.json",
    "start.sh",
    "restart.sh",
    "stop.sh",
    "_service.sh",
    "_service-common.sh",
}
TERMINAL = {"completed", "partial", "failed", "cancelled"}
SETTINGS_CHECK = (
    "from pathlib import Path; from jindiao.application.settings import Settings; "
    "s=Settings(_env_file=None); "
    "assert s.execution_profile == 'attached'; "
    "assert s.shared_storage_backend == 'local'; "
    "assert s.artifact_root == Path('/app/artifacts'); "
    "assert s.data_source_mode != 'tianyancha' or "
    "(s.agent_runtime_mode == 'formal' and not s.allow_degraded_mock); "
    "print('runtime-profile-verified')"
)


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - (set(DEFAULTS) | {"runtime_env"}):
        raise ValueError("Unknown ECS config field; SSH hosts and passwords are not supported")
    config = dict(DEFAULTS, **raw)
    for key in ("remote_root", "runtime_env", "docker_bin"):
        value = str(config.get(key, ""))
        path = PurePosixPath(value)
        if (
            not re.fullmatch(r"/[a-zA-Z0-9_./-]+", value)
            or ".." in path.parts
            or len(path.parts) < 3
            or str(path) != value
        ):
            raise ValueError("Invalid scoped absolute path: " + key)
    socket = str(config["docker_host"])
    if not re.fullmatch(r"unix:///[a-zA-Z0-9_./-]+", socket) or ".." in socket.split("/"):
        raise ValueError("Only a local Unix Docker socket is supported")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,60}", str(config["container"])):
        raise ValueError("Invalid container name")
    for key in ("port", "preflight_port"):
        if type(config[key]) is not int or not 1024 <= config[key] <= 65535:
            raise ValueError("Invalid " + key)
    if config["port"] == config["preflight_port"]:
        raise ValueError("Preflight and service ports must differ")
    return config


def verify_package(directory: Path) -> Dict[str, Any]:
    descriptor = directory / "package.json"
    if descriptor.is_symlink() or not descriptor.is_file():
        raise ValueError("Missing package.json; use an extracted ECS release package")
    package = json.loads(descriptor.read_text())
    if not isinstance(package, dict) or package.get("schema_version") != 2:
        raise ValueError("Invalid ECS package manifest")
    release = package.get("release", "")
    if (
        not isinstance(release, str)
        or not re.fullmatch(r"ecs-[a-zA-Z0-9][a-zA-Z0-9_.-]{0,59}", release)
        or directory.name != release
    ):
        raise ValueError("Package directory must match its safe release identifier")
    files = package.get("files", {})
    if not isinstance(files, dict) or set(files) != PACKAGE_FILES:
        raise ValueError("Invalid ECS package file inventory")
    for name, expected in files.items():
        path = directory / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not isinstance(expected, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or digest_file(path) != expected
        ):
            raise ValueError("ECS package checksum mismatch")
    return package


def tree_manifest(root: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid volume root or symlink")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Volume contains a symlink; manual migration required")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest_file(path)
        elif not path.is_dir():
            raise ValueError("Volume contains a special file; manual migration required")
    return result


def check_volume(root: Path) -> Dict[str, int]:
    runs = root / "run-state/runs"
    if not runs.is_dir():
        raise ValueError("Run state directory is absent; cannot prove the existing service is idle")
    count = 0
    for path in runs.glob("*/metadata.json"):
        if json.loads(path.read_text()).get("status") not in TERMINAL:
            raise ValueError("Existing volume has nonterminal Run state; keep the old service")
        count += 1
    return {"runs": count, "active": 0}


def copy_volume(source: Path, destination: Path) -> Dict[str, Any]:
    check_volume(source)
    before = tree_manifest(source)
    if destination.is_symlink() or any(destination.iterdir()):
        raise ValueError("New data volume must be empty")
    # Candidate UID/GID are checked first; preserve ownership and mode as well as content.
    for path in sorted(source.rglob("*")):
        target = destination / path.relative_to(source)
        metadata = path.stat()
        if path.is_dir():
            target.mkdir(exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        if os.geteuid() == 0:
            os.chown(target, metadata.st_uid, metadata.st_gid)
        os.chmod(target, stat.S_IMODE(metadata.st_mode))
    if tree_manifest(destination) != before or tree_manifest(source) != before:
        raise ValueError("Copied volume failed historical content verification")
    metadata = source.stat()
    if os.geteuid() == 0:
        os.chown(destination, metadata.st_uid, metadata.st_gid)
    os.chmod(destination, stat.S_IMODE(metadata.st_mode))
    digest = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    return {"files": len(before), "sha256": digest}


def unpack(archive: Path, destination: Path, expected_digest: str) -> None:
    if digest_file(archive) != expected_digest:
        raise ValueError("Uploaded source archive digest differs")
    destination.mkdir(mode=0o700)
    names: Set[str] = set()
    with tarfile.open(archive) as bundle:
        for member in bundle:
            path = Path(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or member.name in names
            ):
                raise ValueError("Unsafe source archive member")
            names.add(member.name)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.extractfile(member)
            if data is None:
                raise ValueError("Unreadable source archive member")
            with target.open("xb") as output:
                shutil.copyfileobj(data, output)
    manifest = json.loads((destination / "release-manifest.json").read_text())
    expected = manifest["files"]
    if names != set(expected) | {"release-manifest.json"}:
        raise ValueError("Source archive manifest mismatch")
    if any(digest_file(destination / name) != value for name, value in expected.items()):
        raise ValueError("Source archive content mismatch")


def verify_volume(source: Path, destination: Path) -> Dict[str, Any]:
    before = tree_manifest(source)
    after = tree_manifest(destination)
    if any(after.get(name) != digest for name, digest in before.items()):
        raise ValueError("New service changed historical files during startup")
    return {"historical_files_verified": len(before)}


class Engine:
    def run(self, args: List[str], *, timeout: int = 1800) -> str:
        raise NotImplementedError

    def inspect(self, name: str, field: str) -> Any:
        raise NotImplementedError


class Docker(Engine):
    def __init__(self, config: Dict[str, Any]) -> None:
        self.prefix = [config["docker_bin"], "--host", config["docker_host"]]

    def run(self, args: List[str], *, timeout: int = 1800) -> str:
        try:
            process = subprocess.run(
                [*self.prefix, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Docker operation timed out; inspect release state") from None
        if process.returncode:
            # docker inspect/env and application startup can contain secrets; never echo raw output.
            raise RuntimeError(f"Docker {args[0]} failed (exit {process.returncode})")
        return process.stdout.strip()

    def inspect(self, name: str, field: str) -> Any:
        return json.loads(self.run(["inspect", "--format", "{{json " + field + "}}", name]))


def healthy(engine: Engine, name: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = engine.inspect(name, ".State")
        if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
            return
        if not state.get("Running") or state.get("Health", {}).get("Status") == "unhealthy":
            raise RuntimeError("Container failed process health check")
        time.sleep(1)
    raise RuntimeError("Container health check timed out")


def save_receipt(directory: Path, payload: Dict[str, Any]) -> None:
    temporary = directory / "receipt.tmp"
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    temporary.replace(directory / "receipt.json")


def create_container(
    engine: Engine,
    name: str,
    image: str,
    volume: str,
    directory: Path,
    port: int,
) -> None:
    health_command = (
        'python -c "import urllib.request; '
        f"r=urllib.request.urlopen('http://127.0.0.1:{port}/ping',timeout=2); "
        'raise SystemExit(0 if r.status == 200 else 1)"'
    )
    engine.run(
        [
            "create",
            "--name",
            name,
            "--restart=no",
            "--network",
            "host",
            "--mount",
            f"type=volume,src={volume},dst=/app/artifacts",
            "--env-file",
            str(directory / "runtime.env"),
            "--health-cmd",
            health_command,
            "--health-interval=5s",
            "--health-start-period=20s",
            "--health-timeout=3s",
            "--health-retries=3",
            image,
            "python",
            "-m",
            "uvicorn",
            "jindiao.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--no-proxy-headers",
            "--no-access-log",
        ]
    )


def volume_tool(
    engine: Engine,
    image: str,
    directory: Path,
    source: str,
    destination: Optional[str] = None,
    *,
    verify: bool = False,
) -> Dict[str, Any]:
    args = [
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0:0",
        "--mount",
        f"type=volume,src={source},dst=/source,readonly",
        "--mount",
        f"type=bind,src={directory / 'ecs_host.py'},dst=/controller.py,readonly",
    ]
    if destination is not None:
        suffix = "readonly" if verify else "volume-nocopy"
        args += ["--mount", f"type=volume,src={destination},dst=/destination,{suffix}"]
    action = "verify-volume" if verify else ("copy-volume" if destination else "check-volume")
    args += [image, "python", "/controller.py", action]
    return dict(json.loads(engine.run(args)))


def publish(config: Dict[str, Any], directory: Path, engine: Engine) -> Dict[str, Any]:
    name, release = config["container"], config["release"]
    backup = f"{name}-before-{release}"
    preflight = f"{name}-preflight-{release}"
    failed = f"{name}-failed-{release}"
    volume, probe_volume = f"{name}-data-{release}", f"{name}-probe-{release}"
    image = f"jindiao:{release}"
    containers = set(engine.run(["container", "ls", "-a", "--format", "{{.Names}}"]).splitlines())
    volumes = set(engine.run(["volume", "ls", "--format", "{{.Name}}"]).splitlines())
    if {backup, preflight, failed} & containers or {volume, probe_volume} & volumes:
        raise ValueError("Release destination already exists; inspect before retrying")
    if name not in containers:
        raise ValueError("Existing service is missing; this tool does not bootstrap an ECS")
    healthy(engine, name)
    if engine.inspect(name, ".HostConfig.NetworkMode") != "host":
        raise ValueError("Existing service must use host networking; manual migration required")
    mounts = [
        item for item in engine.inspect(name, ".Mounts") if item["Destination"] == "/app/artifacts"
    ]
    if len(mounts) != 1 or mounts[0]["Type"] != "volume" or not mounts[0]["RW"]:
        raise ValueError("Existing artifacts must use one writable named volume")
    source = mounts[0]["Name"]
    old_image = str(engine.inspect(name, ".Image"))
    restart = engine.inspect(name, ".HostConfig.RestartPolicy")
    if restart["Name"] not in {"unless-stopped", "always", "no"}:
        raise ValueError("Unsupported existing restart policy")
    engine.run(["exec", name, "python", "-c", SETTINGS_CHECK])
    old_uid = engine.run(["exec", name, "id", "-u"])
    old_gid = engine.run(["exec", name, "id", "-g"])
    receipt: Dict[str, Any] = {
        "release": release,
        "status": "building",
        "previous_image": old_image,
        "previous_volume": source,
        "backup_container": backup,
        "new_volume": volume,
        "image": image,
        "acceptance": "process-health-and-config-only",
        "live_smoke": False,
    }
    save_receipt(directory, receipt)
    old_may_be_stopped = False
    try:
        engine.run(
            [
                "build",
                "--network",
                "none",
                "--platform",
                "linux/amd64",
                "--build-arg",
                "JINDIAO_BASE_IMAGE=jindiao:deps-amd64",
                "--build-arg",
                "JINDIAO_INSTALL_DEPS=false",
                "-t",
                image,
                str(directory / "source"),
            ]
        )
        if engine.run(["image", "inspect", "--format", "{{.Architecture}}", image]) != "amd64":
            raise ValueError("ECS image must be linux/amd64")
        receipt["image_id"] = engine.run(["image", "inspect", "--format", "{{.Id}}", image])
        engine.run(["volume", "create", probe_volume])
        create_container(
            engine, preflight, image, probe_volume, directory, config["preflight_port"]
        )
        engine.run(["start", preflight])
        healthy(engine, preflight)
        engine.run(["exec", preflight, "python", "-c", SETTINGS_CHECK])
        engine.run(["exec", preflight, "python", "-m", "pip", "check"])
        uid = engine.run(["exec", preflight, "id", "-u"])
        gid = engine.run(["exec", preflight, "id", "-g"])
        if uid == "0" or uid != old_uid or gid != old_gid:
            raise ValueError("Candidate UID/GID differs or is root; manual data migration required")
        engine.run(["stop", "--time", "30", preflight])
        # Clients must already be quiesced: the application has no atomic admission/drain endpoint.
        volume_tool(engine, old_image, directory, source)
        receipt["status"] = "switching"
        save_receipt(directory, receipt)
        old_may_be_stopped = True
        engine.run(["stop", "--time", "30", name])
        engine.run(["volume", "create", volume])
        receipt["migration"] = volume_tool(engine, old_image, directory, source, volume)
        engine.run(["update", "--restart=no", name])
        engine.run(["rename", name, backup])
        create_container(engine, name, image, volume, directory, config["port"])
        engine.run(["start", name])
        healthy(engine, name)
        engine.run(["exec", name, "python", "-c", SETTINGS_CHECK])
        receipt["history_readback"] = volume_tool(
            engine,
            old_image,
            directory,
            source,
            volume,
            verify=True,
        )
        engine.run(["update", "--restart=unless-stopped", name])
        receipt["status"] = "published"
        save_receipt(directory, receipt)
        return receipt
    except BaseException:
        receipt["status"] = "failed-before-switch"
        try:
            current = set(
                engine.run(["container", "ls", "-a", "--format", "{{.Names}}"]).splitlines()
            )
            try:
                if preflight in current:
                    engine.run(["stop", "--time", "30", preflight])
            except BaseException:
                # Probe cleanup is subordinate to restoring the original service.
                receipt["preflight_cleanup"] = "manual-stop-required"
            if old_may_be_stopped:
                if backup in current:
                    if name in current:
                        engine.run(["update", "--restart=no", name])
                        engine.run(["stop", "--time", "30", name])
                        engine.run(["rename", name, failed])
                    engine.run(["rename", backup, name])
                engine.run(["update", "--restart=" + restart["Name"], name])
                engine.run(["start", name])
                healthy(engine, name)
                receipt["status"] = "rolled-back"
        except BaseException:
            receipt["status"] = "rollback-incomplete-manual-recovery-required"
        save_receipt(directory, receipt)
        raise


def deploy(config: Dict[str, Any], directory: Path) -> Dict[str, Any]:
    root = Path(config["remote_root"])
    if directory != root / "releases" / config["release"]:
        raise ValueError("Controller is not inside the selected release directory")
    with (root / "deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((directory / name).exists() for name in ("source", "runtime.env", "receipt.json")):
            raise ValueError("Release has already been prepared; inspect it and use a new package")
        runtime = Path(config["runtime_env"])
        metadata = runtime.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("Server runtime env must be a regular, non-symlink 0600 file")
        if metadata.st_uid != os.geteuid():
            raise ValueError("Server runtime env must be owned by the deploying user")
        unpack(directory / "source.tar.gz", directory / "source", config["archive_sha256"])
        # Snapshot credentials ONLY on the server; never included in source or returned receipt.
        descriptor = os.open(directory / "runtime.env", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output, runtime.open("rb") as source:
            shutil.copyfileobj(source, output)
        return publish(config, directory, Docker(config))


def interrupted(_signum: int, _frame: Any) -> None:
    raise InterruptedError("Deployment interrupted")


def server_cli(argv: List[str], directory: Path) -> int:
    parser = argparse.ArgumentParser(description="Deploy an uploaded package on this ECS (no SSH).")
    parser.add_argument("--config", type=Path, default=directory / "config.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Build and switch the existing service")
    mode.add_argument(
        "--dry-run", action="store_true", help="Verify package/config only; no writes"
    )
    parser.add_argument("--maintenance-confirmed", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.maintenance_confirmed:
        raise ValueError("--apply requires --maintenance-confirmed; stop client traffic first")
    config = validate_config(json.loads(args.config.read_text()))
    package = verify_package(directory)
    config.update(release=package["release"], archive_sha256=package["files"]["source.tar.gz"])
    if not args.apply:
        print("DRY RUN: package checksums verified; " + json.dumps(config, sort_keys=True))
        print("No Docker command, host write, source extraction, or service change was performed.")
        return 0
    print(json.dumps(deploy(config, directory), sort_keys=True))
    return 0


def main() -> int:
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        action = sys.argv[1]
        if action == "check-volume":
            result: Dict[str, Any] = check_volume(Path("/source"))
        elif action == "copy-volume":
            result = copy_volume(Path("/source"), Path("/destination"))
        elif action == "verify-volume":
            result = verify_volume(Path("/source"), Path("/destination"))
        elif action == "deploy":
            return server_cli(sys.argv[2:], Path(__file__).resolve().parent)
        elif action == "service-config":
            parser = argparse.ArgumentParser(
                description="Read validated non-secret service settings"
            )
            parser.add_argument("--config", type=Path, required=True)
            args = parser.parse_args(sys.argv[2:])
            config = validate_config(json.loads(args.config.read_text()))
            for key in ("remote_root", "docker_bin", "docker_host", "container"):
                print(config[key])
            return 0
        else:
            raise ValueError("Unknown controller action")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "message": str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else "Inspect the package, config or release receipt; raw output is withheld",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
