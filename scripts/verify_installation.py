"""Verify that the installed application does not rely on sibling source trees."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from jindiao.api.app import RESULT_PATH


def main() -> None:
    manifests = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("requirements.txt", "pyproject.toml", "uv.lock")
    )
    forbidden = ("/Users/", "../agent-core", "../deepsearch", "file://")
    violations = [value for value in forbidden if value in manifests]
    if violations:
        raise SystemExit(f"local dependency references found: {violations}")
    print(f"openjiuwen={version('openjiuwen')}")
    print(f"openjiuwen-deepsearch={version('openjiuwen-deepsearch')}")
    print(f"jindiao={version('jindiao')}")
    print(f"result_path={RESULT_PATH}")


if __name__ == "__main__":
    main()
