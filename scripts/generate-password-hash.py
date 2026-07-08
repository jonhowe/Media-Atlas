#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import secrets


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Media Atlas admin password hash.")
    parser.add_argument("--iterations", type=int, default=390000)
    args = parser.parse_args()
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        return 1
    if not password:
        print("Password cannot be empty.")
        return 1
    salt = secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), args.iterations)
    print(f"pbkdf2_sha256${args.iterations}${salt}${b64(digest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
