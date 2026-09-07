"""Explicit local commands: python -m jindiao.reporting.demo_cli --help."""

from __future__ import annotations

import argparse
from pathlib import Path

from jindiao.reporting.demo_store import ReportingDemoStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Local single-operator reporting Demo only")
    parser.add_argument("--demo", action="store_true", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    apply = commands.add_parser("apply")
    apply.add_argument("evolution_id")
    apply.add_argument("--reason", required=True)
    reset = commands.add_parser("reset")
    reset.add_argument("--reason", required=True)
    args = parser.parse_args()
    store = ReportingDemoStore(args.artifact_root / "reporting-demo")
    try:
        if args.command == "apply":
            result = store.apply(args.evolution_id, reason=args.reason)
        elif args.command == "reset":
            result = store.reset(reason=args.reason)
        else:
            result = store.state()
    except (OSError, ValueError) as error:
        parser.exit(1, f"Demo operation refused: {type(error).__name__}: {error}\n")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
