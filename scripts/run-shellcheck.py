#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Shellcheck over every tracked shell script, down to its warnings (its style notes are not a verdict).

Stage 1 runs on BusyBox ash, so its scripts are checked as such (shellcheck's dash is the closest).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def shell(data: bytes) -> str | None:
    """Return the shell of a script by its first line, or None for anything else."""
    first = data.split(b"\n", 1)[0]
    if first.startswith(b"#!"):
        if b"bash" in first:
            return "bash"
        if first.rstrip().endswith((b"/sh", b" sh", b"ash")):
            return "dash"
    return None


def main() -> int:
    """Check every tracked shell script; fail when any has a warning or an error."""
    # In the build container a worktree's repository is at OVERPRESSURE_GIT_DIR (overpressure/linux.py).
    git_dir = os.environ.get("OVERPRESSURE_GIT_DIR")
    where = ["--git-dir", git_dir, "--work-tree", str(REPO)] if git_dir else ["-C", str(REPO)]
    ls_files = ["git", *where, "ls-files", "-z"]
    names = subprocess.run(ls_files, check=True, stdout=subprocess.PIPE).stdout  # noqa: S603  # git from PATH, no shell
    code = 0
    count = 0
    for name in names.decode().split("\0"):
        path = REPO / name
        # .command: macOS's start script (Install.command)
        if not name or not path.is_file() or path.suffix not in ("", ".sh", ".command"):
            continue
        kind = shell(path.read_bytes()[:200])
        if not kind:
            continue
        count += 1
        check = ["shellcheck", "--severity=warning", "--shell", kind, str(path)]
        code |= subprocess.call(check)  # noqa: S603  # shellcheck from PATH on a tracked file, no shell
    print(f"{count} scripts checked")
    return 1 if code else 0


if __name__ == "__main__":
    sys.exit(main())
