"""Packaging boundary checks; these do not replace an actual Docker build."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_bff_import_does_not_initialize_agents() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import jindiao.bff.app; "
            "assert 'jindiao.api.app' not in sys.modules; "
            "assert 'jindiao.application.service' not in sys.modules; "
            "assert 'openjiuwen' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_docker_bff_copies_only_allowlisted_modules() -> None:
    dockerfile = Path("deploy/bff/Dockerfile")
    assert dockerfile.exists(), "BFF Dockerfile missing"
    text = dockerfile.read_text()
    assert "jindiao.bff.app:create_app" in text
    assert '"--workers", "1"' in text and '"--no-proxy-headers"' in text
    assert "USER 10001:10001" in text and "HEALTHCHECK" in text
    copies = [line for line in text.splitlines() if line.startswith("COPY ")]
    assert len(copies) == 5
    assert not any(".env" in line or line.startswith("COPY . ") for line in copies)
    requirements = Path("deploy/bff/requirements.txt").read_text()
    assert "openjiuwen" not in requirements and "fastapi==0.115.11" in requirements
