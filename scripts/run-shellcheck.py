#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Shellcheck over every tracked shell script, at its errors' level: warnings are not a verdict here.

Stage 1 runs on BusyBox ash, so its scripts are checked as such (shellcheck's dash is the closest).
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def shell(data: bytes):
    """The shell of a script by its first line, or None for anything else."""
    first = data.split(b"\n", 1)[0]
    if first.startswith(b"#!"):
        if b"bash" in first:
            return "bash"
        if first.rstrip().endswith((b"/sh", b" sh", b"ash")):
            return "dash"
    return None


def main() -> int:
    names = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], check=True, stdout=subprocess.PIPE).stdout
    code = 0
    count = 0
    for name in names.decode().split("\0"):
        path = REPO / name
        if not name or not path.is_file() or path.suffix not in ("", ".sh"):
            continue
        kind = shell(path.read_bytes()[:200])
        if not kind:
            continue
        count += 1
        code |= subprocess.call(["shellcheck", "--severity=error", "--shell", kind, str(path)])
    print(f"{count} scripts checked")
    return 1 if code else 0


if __name__ == "__main__":
    sys.exit(main())
