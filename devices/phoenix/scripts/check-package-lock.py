#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""The upstream packages of a build are the tested ones (docs/userspace.md#package-lock).

    check-package-lock.py HASHES LOCK [--write NEW_LOCK]

HASHES and LOCK hold sha256sum lines. Project packages (pico4-*) are built here and stay out of it.
With --write the build's packages become a new lock file for review, instead of being checked.
"""

import sys
from pathlib import Path


def read(text: str) -> dict[str, str]:
    """File name -> SHA-256, without the project's own packages."""
    entries = {}
    for line in text.splitlines():
        if line.strip():
            sha, name = line.split()
            name = name.lstrip("./").rsplit("/", 1)[-1]
            if not name.startswith("pico4-"):
                entries[name] = sha
    return entries


def differences(built: dict[str, str], lock: dict[str, str]) -> list[str]:
    """List what keeps this build from being the tested one; a locked package the build no longer holds is fine."""
    problems = []
    for name, sha in sorted(built.items()):
        if name not in lock:
            problems.append(f"{name}: not in the lock")
        elif lock[name] != sha:
            problems.append(f"{name}: differs from the lock")
    return problems


def main(argv: list[str]) -> int:
    """Check the build's packages against the lock, or write them as a new lock."""
    built = read(Path(argv[0]).read_text(encoding="utf-8"))
    if argv[2:4] and argv[2] == "--write":
        Path(argv[3]).write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(built.items())), encoding="utf-8")
        print(f"package lock: wrote {argv[3]}")
        return 0
    problems = differences(built, read(Path(argv[1]).read_text(encoding="utf-8")))
    for problem in problems:
        print("package lock: " + problem, file=sys.stderr)
    if problems:
        print("The build holds packages nobody tested. --update-lock writes a new lock for review.", file=sys.stderr)
        return 1
    print(f"package lock: {len(built)} packages as tested")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
