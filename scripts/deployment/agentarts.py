"""Build/check/push an ARM64 image; never creates or switches an AgentArts runtime."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from .common import (
    Runner,
    extract_snapshot,
    release_name,
    require_local_engine,
    snapshot,
    verify_source,
    wait_healthy,
    write_receipt,
)


def verify_manifest(raw: str, image_id: str) -> str:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a single linux/arm64 manifest, not an index")
    descriptor = data.get("Descriptor", {})
    platform = descriptor.get("platform", {})
    if platform.get("os") != "linux" or platform.get("architecture") != "arm64":
        raise ValueError("Remote manifest is not linux/arm64")
    manifest = data.get("SchemaV2Manifest") or data.get("OCIManifest") or {}
    if manifest.get("config", {}).get("digest") != image_id:
        raise ValueError("Remote config digest differs from the verified local image")
    digest = str(descriptor.get("digest", ""))
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Invalid remote manifest digest")
    return digest


def main(root: Path, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="Registry/repository:unique-tag; never latest"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--build-only", action="store_true", help="Build and check locally; do not push"
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]+/[a-z0-9_./-]+:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", args.image
    ):
        raise ValueError(
            "Use registry/repository:unique-tag (credentials and URLs are not accepted)"
        )
    if args.image.rsplit(":", 1)[1].lower() == "latest":
        raise ValueError("Use an immutable release tag, not latest")
    if not args.apply:
        delivery = "keep local" if args.build_only else "push + remote manifest check"
        print(f"DRY RUN: snapshot → buildx linux/arm64 → isolated Mock health → {delivery}")
        print(f"Image: {args.image}; AgentArts runtime/versions/credentials are NOT modified.")
        return 0
    runner = Runner()
    require_local_engine(runner)
    release = release_name("agentarts")
    receipt = {"release": release, "image": args.image, "status": "prepared"}
    # Refuse collisions; SWR tag immutability is still needed to guard concurrent publishers.
    existing = runner.run(["docker", "image", "ls", "--quiet", args.image])
    if existing:
        raise ValueError("Local image tag already exists; choose a fresh release tag")
    if not args.build_only and runner.remote_tag_exists(args.image):
        raise ValueError("Registry tag already exists; choose a fresh release tag")
    with tempfile.TemporaryDirectory(prefix="jindiao-agentarts-") as temporary:
        archive = Path(temporary) / "source.tar.gz"
        manifest = snapshot(root, archive)
        context = Path(temporary) / "context"
        extract_snapshot(archive, context)
        print(f"AgentArts {release}: building isolated ARM64 snapshot", flush=True)
        runner.run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/arm64",
                "--load",
                "--provenance=false",
                "--sbom=false",
                "-t",
                args.image,
                str(context),
            ]
        )
        image_id = runner.run(["docker", "image", "inspect", "--format", "{{.Id}}", args.image])
        architecture = runner.run(
            ["docker", "image", "inspect", "--format", "{{.Architecture}}", args.image]
        )
        if architecture != "arm64":
            raise ValueError("Built image is not ARM64")
        name = f"jindiao-check-{release}"
        created = False
        try:
            runner.run(
                [
                    "docker",
                    "create",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "-e",
                    "MODEL_PROVIDER=offline_mock",
                    "-e",
                    "MODEL_NAME=deterministic-mock",
                    "-e",
                    "JINDIAO_AGENT_RUNTIME_MODE=deterministic_harness",
                    "-e",
                    "JINDIAO_DATA_SOURCE_MODE=mock",
                    "-e",
                    "JINDIAO_STORAGE_BACKEND=memory",
                    "-e",
                    "JINDIAO_EXECUTION_PROFILE=attached",
                    args.image,
                ]
            )
            created = True
            runner.run(["docker", "start", name])
            wait_healthy(runner, name)
            runner.run(["docker", "exec", name, "python", "-m", "pip", "check"])
            uid = runner.run(["docker", "exec", name, "id", "-u"])
            if uid == "0":
                raise ValueError("Candidate image runs as root")
        finally:
            if created:
                runner.run(["docker", "rm", "--force", name])
        verify_source(root, manifest)
        receipt.update(status="built-and-healthy", image_id=image_id)
        write_receipt(root, release, {**receipt, "source": manifest})
        if not args.build_only:
            if runner.remote_tag_exists(args.image):
                raise ValueError("Registry tag appeared during build; refusing to overwrite")
            print("Pushing image using existing Docker registry credentials", flush=True)
            runner.run(["docker", "push", args.image])
            digest = verify_manifest(
                runner.run(["docker", "manifest", "inspect", "--verbose", args.image]), image_id
            )
            receipt.update(status="image-delivered", manifest_digest=digest)
    path = write_receipt(root, release, {**receipt, "source": manifest})
    print(f"Receipt: {path}; finish runtime deployment using docs/deployment/agentarts.md")
    return 0
