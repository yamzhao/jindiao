"""Generate a demo account hash interactively, without a command-line password."""

from __future__ import annotations

import argparse
import getpass
import hmac
import json
import re

from .security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a BFF users JSON object with a scrypt hash")
    parser.add_argument(
        "username", help="Named demo account (letters, numbers, underscore, hyphen)"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.username):
        parser.error("Invalid username")
    first = getpass.getpass("Password (12-256 characters): ")
    second = getpass.getpass("Confirm password: ")
    if not hmac.compare_digest(first.encode(), second.encode()):
        parser.error("Passwords do not match")
    try:
        encoded = hash_password(first)
    except ValueError:
        parser.error("Password must contain 12 to 256 characters")
    print(json.dumps({args.username: encoded}))


if __name__ == "__main__":
    main()
