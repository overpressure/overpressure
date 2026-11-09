#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""A release's tag against the version in pyproject.toml (docs/releasing.md).

    python scripts/release.py check TAG

Tags are `vMAJOR.MINOR` and `vMAJOR.MINOR-beta[.N]`; pyproject.toml holds the same version in Python's
spelling (`v1.0-beta` is `1.0b1`, `v1.0-beta.2` is `1.0b2`). Prints `prerelease` for a beta, `release`
otherwise, and fails when the tag is not a version or names another one than pyproject.toml.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_TAG = re.compile(r"^v(?P<base>\d+\.\d+)(?:-beta(?:\.(?P<n>[1-9]\d*))?)?$")
_PROJECT_VERSION = re.compile(r'^\[project\][^\[]*?^version = "(?P<version>[^"]+)"', re.MULTILINE | re.DOTALL)


def python_version(tag: str) -> str:
    """Python's spelling of the version a release tag names."""
    match = _TAG.match(tag)
    if match is None:
        message = f"{tag} is not a release tag: vMAJOR.MINOR or vMAJOR.MINOR-beta[.N]"
        raise ValueError(message)
    if "-beta" not in tag:
        return match["base"]
    return f"{match['base']}b{match['n'] or 1}"


def project_version(pyproject: str) -> str:
    """Read the version in pyproject.toml's [project] table."""
    match = _PROJECT_VERSION.search(pyproject)
    if match is None:
        message = "pyproject.toml has no version in [project]"
        raise ValueError(message)
    return match["version"]


def check(tag: str, pyproject: str) -> str:
    """`prerelease` or `release` for a tag that names pyproject.toml's version."""
    wanted = python_version(tag)
    have = project_version(pyproject)
    if wanted != have:
        message = f"{tag} is version {wanted}, but pyproject.toml says {have}"
        raise ValueError(message)
    return "prerelease" if "-beta" in tag else "release"


def main(argv: list[str] | None = None) -> int:
    """Run the check named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="the tag names pyproject.toml's version").add_argument("tag")
    args = parser.parse_args(argv)
    try:
        print(check(args.tag, (REPO / "pyproject.toml").read_text(encoding="utf-8")))
    except ValueError as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
