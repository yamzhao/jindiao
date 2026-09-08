"""Standard-library entry point; never imports the business runtime or its dotenv."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if sys.version_info < (3, 11):  # noqa: UP036 - bootstrap runs before dependencies are installed
        print("Deployment tooling requires Python 3.11+ (JINDIAO_DEPLOY_PYTHON).", file=sys.stderr)
        return 2
    from deployment import agentarts, ecs

    handlers = {"ecs-package": ecs.main, "agentarts": agentarts.main}
    if len(sys.argv) < 2 or sys.argv[1] not in handlers:
        print("Usage: bin/start.sh | bin/ecs-package | bin/agentarts", file=sys.stderr)
        return 2
    try:
        return handlers[sys.argv[1]](Path(__file__).resolve().parents[1], sys.argv[2:])
    except (OSError, ValueError, RuntimeError) as exc:
        # Helpers only raise sanitized errors; never print command payloads or dotenv values.
        print(f"Deployment stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Inspect any remote release receipt before retrying.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
