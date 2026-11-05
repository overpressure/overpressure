#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Every relative link in the Markdown documents leads somewhere: the file, and its heading if named.

    python scripts/check-docs.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Set

REPO = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^```.*?^```", re.M | re.S)
CODE = re.compile(r"`[^`\n]*`")


def anchors(text: str) -> Set[str]:
    """GitHub's anchors for the document's headings."""
    found: Set[str] = set()
    for line in FENCE.sub("", text).splitlines():
        match = re.match(r"#{1,6}\s+(.*)", line)
        if match:
            title = re.sub(r"[`*_]|\[|\]\([^)]*\)", "", match.group(1)).strip().lower()
            title = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
            found.add(title)
    return found


def broken(path: Path, text: str) -> List[str]:
    """The links of the document at @p path that lead nowhere."""
    bad = []
    body = CODE.sub("", FENCE.sub("", text))
    for target in LINK.findall(body):
        if re.match(r"[a-z]+:", target) or target.startswith("<"):
            continue
        name, _, anchor = target.partition("#")
        file = (path.parent / name).resolve() if name else path
        if not file.exists():
            bad.append(f"{target}: no such file")
        elif anchor and file.suffix == ".md":
            if anchor.lower() not in anchors(file.read_text(encoding="utf-8")):
                bad.append(f"{target}: no such heading")
    return bad


def main() -> int:
    names = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                            "*.md"], check=True, stdout=subprocess.PIPE).stdout.decode().split("\0")
    count = 0
    for name in names:
        path = REPO / name
        if not name or not path.is_file():
            continue
        for problem in broken(path, path.read_text(encoding="utf-8")):
            print(f"{name}: {problem}")
            count += 1
    print(f"{count} broken link(s)")
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main())
