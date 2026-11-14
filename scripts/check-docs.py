#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Every relative link in the Markdown documents leads somewhere: the file, and its heading if named.

python scripts/check-docs.py [--repo DIR]   (DIR: another repository, a headset's in devices/)

A link may not leave its repository: on GitHub it would lead nowhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
CODE = re.compile(r"`[^`\n]*`")


def anchors(text: str) -> set[str]:
    """GitHub's anchors for the document's headings."""
    found: set[str] = set()
    for line in FENCE.sub("", text).splitlines():
        match = re.match(r"#{1,6}\s+(.*)", line)
        if match:
            title = re.sub(r"[`*_]|\[|\]\([^)]*\)", "", match.group(1)).strip().lower()
            title = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
            found.add(title)
    return found


def broken(path: Path, text: str, root: Path = REPO) -> list[str]:
    """Return the links of the document at @p path (in the repository at @p root) that lead nowhere."""
    bad = []
    body = CODE.sub("", FENCE.sub("", text))
    for target in LINK.findall(body):
        if re.match(r"[a-z]+:", target) or target.startswith("<"):
            continue
        name, _, anchor = target.partition("#")
        file = (path.parent / name).resolve() if name else path
        if not file.is_relative_to(root.resolve()):
            bad.append(f"{target}: outside the repository")
        elif not file.exists():
            bad.append(f"{target}: no such file")
        elif anchor and file.suffix == ".md" and anchor.lower() not in anchors(file.read_text(encoding="utf-8")):
            bad.append(f"{target}: no such heading")
    return bad


def main(argv: list[str]) -> int:
    """Check every tracked or new Markdown document (of --repo DIR) and print what is broken."""
    repo = Path(argv[argv.index("--repo") + 1]).resolve() if "--repo" in argv else REPO
    ls_files = ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"]
    result = subprocess.run(ls_files, check=True, stdout=subprocess.PIPE)  # noqa: S603  # git from PATH, no shell
    names = result.stdout.decode().split("\0")
    count = 0
    for name in names:
        path = repo / name
        if not name or not path.is_file():
            continue
        for problem in broken(path, path.read_text(encoding="utf-8"), repo):
            print(f"{name}: {problem}")
            count += 1
    print(f"{count} broken link(s)")
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
