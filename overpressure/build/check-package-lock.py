#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""The upstream packages of a headset's build are the tested ones (build-userspace.sh; a headset's package lock).

    check-package-lock.py HASHES LOCK [--skip PREFIX] [--write NEW_LOCK]

HASHES and LOCK hold sha256sum lines. Packages whose name starts with PREFIX (the project's, built here) stay out
of it. With --write the build's packages become a new lock file for review, instead of being checked.
"""

import argparse
import sys
from pathlib import Path


def read(text: str, skip: str = "") -> dict[str, str]:
    """File name -> SHA-256, without the packages named @p skip... (the project's own)."""
    entries = {}
    for line in text.splitlines():
        if line.strip():
            sha, name = line.split()
            name = name.lstrip("./").rsplit("/", 1)[-1]
            if not (skip and name.startswith(skip)):
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
    ap = argparse.ArgumentParser(prog="check-package-lock.py")
    ap.add_argument("hashes", type=Path)
    ap.add_argument("lock", type=Path)
    ap.add_argument("--skip", default="", help="the project's packages' name prefix")
    ap.add_argument("--write", type=Path, help="write the build's packages as a new lock instead")
    args = ap.parse_args(argv)
    built = read(args.hashes.read_text(encoding="utf-8"), args.skip)
    if args.write:
        args.write.write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(built.items())), encoding="utf-8")
        print(f"package lock: wrote {args.write}")
        return 0
    problems = differences(built, read(args.lock.read_text(encoding="utf-8"), args.skip))
    for problem in problems:
        print("package lock: " + problem, file=sys.stderr)
    if problems:
        print("The build holds packages nobody tested. --update-lock writes a new lock for review.", file=sys.stderr)
        return 1
    print(f"package lock: {len(built)} packages as tested")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
