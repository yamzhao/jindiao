"""Locate external configuration/Skill assets in source and installed deployments."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """An installed wheel uses the deployment's explicit asset root."""
    configured = os.environ.get("JINDIAO_PROJECT_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[2]
