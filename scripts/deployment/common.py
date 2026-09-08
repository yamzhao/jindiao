"""Immutable runtime snapshots and bounded, non-shell command execution."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILES = ("Dockerfile", "requirements.txt", "pyproject.toml", "uv.lock", "README.md")
TREES = ("src", "config", "mock_data", "skills")


def excluded(path: Path) -> bool:
    return any(
        part.startswith(".")
        or part in {"__pycache__", "logs", "artifacts", "node_modules"}
        or part.endswith((".pyc", ".pem", ".key", ".env"))
        or ".log" in part
        for part in path.parts
    )


def source_files(root: Path) -> list[Path]:
    paths = [root / name for name in FILES]
    for name in TREES:
        base = root / name
        if base.is_symlink() or not base.is_dir():
            raise ValueError(f"Missing runtime directory or symlink: {name}")
        for path in sorted(base.rglob("*")):
            if excluded(path.relative_to(root)):
                continue
            if path.is_symlink():
                raise ValueError("Runtime snapshot refuses symlinks")
            if path.is_file():
                paths.append(path)
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("Missing required runtime file or symlink")
    return sorted(paths)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path, archive: Path) -> dict[str, Any]:
    hashes: dict[str, str] = {}
    with tarfile.open(archive, "x:gz") as bundle:
        for path in source_files(root):
            name = path.relative_to(root).as_posix()
            content = path.read_bytes()
            hashes[name] = hashlib.sha256(content).hexdigest()
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            bundle.addfile(info, io.BytesIO(content))
        manifest = {"schema_version": 1, "files": hashes}
        data = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo("release-manifest.json")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    verify_source(root, manifest)
    return manifest


def verify_source(root: Path, manifest: dict[str, Any]) -> None:
    actual = {path.relative_to(root).as_posix(): sha256(path) for path in source_files(root)}
    if actual != manifest["files"]:
        raise ValueError("Runtime source changed after snapshot; create a fresh release")


def extract_snapshot(archive: Path, destination: Path) -> None:
    """Extract only regular relative members to a new directory; verify exact contents."""
    destination.mkdir(mode=0o700)
    names: set[str] = set()
    with tarfile.open(archive) as bundle:
        for member in bundle:
            path = Path(member.name)
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or member.name in names
            ):
                raise ValueError("Unsafe snapshot member")
            names.add(member.name)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            content = bundle.extractfile(member)
            if content is None:
                raise ValueError("Unreadable snapshot member")
            with target.open("xb") as output:
                while chunk := content.read(1024 * 1024):
                    output.write(chunk)
    manifest = json.loads((destination / "release-manifest.json").read_text())
    if names != set(manifest["files"]) | {"release-manifest.json"}:
        raise ValueError("Snapshot manifest member mismatch")
    verify_source(destination, manifest)


def release_name(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC):%Y%m%d-%H%M%S}-{os.urandom(3).hex()}"


class Runner:
    def remote_tag_exists(self, image: str) -> bool:
        try:
            process = subprocess.run(
                ["docker", "manifest", "inspect", image],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Cannot establish registry tag availability (timeout)") from None
        if process.returncode == 0:
            return True
        message = process.stderr.lower()
        if any(token in message for token in ("unauthorized", "denied", "forbidden")):
            raise RuntimeError("Cannot establish registry tag availability (authentication)")
        if "manifest unknown" in message or "no such manifest:" in message:
            return False
        raise RuntimeError("Cannot establish registry tag availability; check registry access")

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        timeout: int = 1800,
    ) -> str:
        try:
            result = subprocess.run(
                args,
                cwd=cwd,
                env=env,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"{Path(args[0]).name} timed out; inspect state before retrying"
            ) from None
        if result.returncode:
            raise RuntimeError(
                f"{Path(args[0]).name} failed (exit {result.returncode}); "
                "raw output withheld because it may contain credentials"
            )
        return result.stdout.strip()


def wait_healthy(runner: Runner, container: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = json.loads(
            runner.run(["docker", "inspect", "--format", "{{json .State}}", container])
        )
        if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
            return
        if not state.get("Running") or state.get("Health", {}).get("Status") == "unhealthy":
            raise RuntimeError("Candidate container failed its process health check")
        time.sleep(1)
    raise RuntimeError("Candidate container health check timed out")


def require_local_engine(runner: Runner) -> None:
    endpoint = os.environ.get("DOCKER_HOST", "") if not os.environ.get("DOCKER_CONTEXT") else ""
    if not endpoint:
        context = runner.run(["docker", "context", "show"], timeout=15)
        endpoint = runner.run(
            ["docker", "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"],
            timeout=15,
        )
    if not endpoint.startswith("unix:///"):
        raise ValueError(
            "This entry point requires a local Unix Docker socket, not a remote context"
        )


def write_receipt(root: Path, release: str, payload: dict[str, Any]) -> Path:
    directory = root / "artifacts" / "deployments" / release
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / "receipt.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target
