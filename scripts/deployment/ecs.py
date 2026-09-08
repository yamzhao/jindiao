"""Create an offline ECS deployment package; transport and login are operator-managed."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from .common import release_name, sha256, snapshot, verify_source


def create_package(root: Path, output_dir: Path, release: str) -> Path:
    if not re.fullmatch(r"ecs-[a-zA-Z0-9][a-zA-Z0-9_.-]{0,59}", release):
        raise ValueError("Release must be a safe ecs-prefixed identifier")
    archive = output_dir / f"{release}.tar.gz"
    checksum = Path(str(archive) + ".sha256")
    if archive.exists() or checksum.exists():
        raise FileExistsError("Release package already exists; choose a new release")
    with tempfile.TemporaryDirectory(prefix="jindiao-ecs-package-") as temporary:
        stage = Path(temporary) / release
        stage.mkdir(mode=0o700)
        manifest = snapshot(root, stage / "source.tar.gz")
        assets = {
            "ecs_host.py": root / "scripts/deployment/ecs_host.py",
            "deploy.sh": root / "deploy/ecs/deploy.sh",
            "config.example.json": root / "deploy/ecs/config.example.json",
            "start.sh": root / "deploy/ecs/start.sh",
            "restart.sh": root / "deploy/ecs/restart.sh",
            "stop.sh": root / "deploy/ecs/stop.sh",
            "_service.sh": root / "deploy/ecs/_service.sh",
            "_service-common.sh": root / "deploy/service-common.sh",
        }
        for name, source in assets.items():
            if source.is_symlink() or not source.is_file():
                raise ValueError("Missing or symlinked deployment asset")
            shutil.copyfile(source, stage / name)
        descriptor = {
            "schema_version": 2,
            "release": release,
            "files": {name: sha256(stage / name) for name in ("source.tar.gz", *assets)},
        }
        (stage / "package.json").write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n")
        staged_archive = Path(temporary) / archive.name
        with tarfile.open(staged_archive, "w:gz") as bundle:
            for source in sorted(stage.iterdir()):
                info = bundle.gettarinfo(str(source), arcname=f"{release}/{source.name}")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o755 if source.suffix == ".sh" else 0o644
                with source.open("rb") as stream:
                    bundle.addfile(info, stream)
        verify_source(root, manifest)
        digest = sha256(staged_archive)
        output_dir.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects existing packages; interruption never yields a valid checksum.
        with archive.open("xb") as output, staged_archive.open("rb") as package_stream:
            shutil.copyfileobj(package_stream, output)
        with checksum.open("x") as output:
            output.write(f"{digest}  {archive.name}\n")
    return archive


def main(root: Path, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=root / "artifacts/ecs-packages")
    parser.add_argument("--release", help="Optional unique ecs-prefixed identifier")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    release = args.release or release_name("ecs")
    if not re.fullmatch(r"ecs-[a-zA-Z0-9][a-zA-Z0-9_.-]{0,59}", release):
        raise ValueError("Release must be a safe ecs-prefixed identifier")
    directory = args.output_dir.resolve()
    if args.dry_run:
        print(f"DRY RUN: package current runtime source → {directory / (release + '.tar.gz')}")
        print("Offline only: no SSH, credentials, Docker build, upload, or service changes.")
        return 0
    archive = create_package(root, directory, release)
    print(f"Package: {archive}")
    print(f"Checksum: {archive}.sha256")
    print("Upload both manually; verify and extract on ECS, then run the included deploy.sh.")
    return 0
