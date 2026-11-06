#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""The changelog of a release, from its conventional commits (docs/releasing.md).

    python scripts/changelog.py [--since TAG] [--version VERSION]

The documents state what is; Git holds how it came to be. A release's notes are this script's output,
on the release's page and in CHANGELOG.md: fixes and features for users, the rest in one count.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO = Path(__file__).resolve().parent.parent
_COMMIT = re.compile(r"^(?P<kind>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?: (?P<subject>.+)$")
SECTIONS = {"feat": "Features", "fix": "Fixes"}


def notes(subjects: Iterable[str], version: str | None = None) -> str:
    """Markdown for these commit subjects, newest first."""
    grouped: dict[str, list[str]] = {title: [] for title in SECTIONS.values()}
    other = 0
    for subject in subjects:
        match = _COMMIT.match(subject)
        section = SECTIONS.get(match["kind"]) if match else None
        if match and section:
            grouped[section].append(match["subject"] + (" (breaking)" if match["breaking"] else ""))
        else:
            other += 1
    lines = [f"## {version}", ""] if version else []
    for title, items in grouped.items():
        if items:
            lines += [f"### {title}", ""] + [f"- {item}" for item in items] + [""]
    if other:
        lines += [f"{other} other commit(s): documentation, tests, refactoring and tooling.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the notes of the commits since the given tag."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="the previous release's tag; empty: the whole history")
    ap.add_argument("--version")
    args = ap.parse_args(argv)
    revisions = f"{args.since}..HEAD" if args.since else "HEAD"
    log = ["git", "-C", str(REPO), "log", "--format=%s", revisions]
    result = subprocess.run(log, check=True, stdout=subprocess.PIPE, text=True)  # noqa: S603  # git from PATH, no shell
    print(notes(result.stdout.splitlines(), args.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
