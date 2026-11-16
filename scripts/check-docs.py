#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Every relative link in the Markdown documents leads somewhere: the file, and its heading if named.

python scripts/check-docs.py [--repo DIR]   (DIR: another repository, a headset's in devices/)

A link may not leave its repository: on GitHub it would lead nowhere. A headset's repository (one with a
headset.json) also has its README checked against docs/headset-readme-template.md, so that every headset's
README reads the same.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "docs" / "headset-readme-template.md"
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
CODE = re.compile(r"`[^`\n]*`")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
#: A row of "What works": one of these, and an issue linked from every ❌.
STATUS = ("✅", "🟡", "❌", "❔", "\N{HEAVY MINUS SIGN}")
ISSUE = re.compile(r"\]\(https://github\.com/[^/\s]+/[^/\s]+/issues/\d+\)")


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


def sections(text: str) -> list[tuple[str, list[list[list[str]]]]]:
    """Return the document's sections (## and ###, and before them its title's as ""), with their tables' rows."""
    found: list[tuple[str, list[list[list[str]]]]] = [("", [])]
    previous = ""
    for line in FENCE.sub("", COMMENT.sub("", text)).splitlines():
        heading = re.match(r"#{2,3}\s+(.*)", line)
        if heading:
            found.append((heading.group(1).strip(), []))
        elif line.startswith("|") and not re.fullmatch(r"[|\s:-]+", line):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            tables = found[-1][1]
            if not previous.startswith("|") or not tables:
                tables.append([])
            tables[-1].append(cells)
        previous = line
    return found


def matches(pattern: str, text: str) -> bool:
    """Whether @p text is the template's @p pattern, each {{...}} in it standing for any text."""
    parts = [re.escape(part) for part in PLACEHOLDER.split(pattern)]
    return re.fullmatch(".+".join(parts), text) is not None


def readme_problems(readme: str, template: str) -> list[str]:
    """Return how a headset's @p readme departs from the @p template (docs/headset-readme-template.md)."""
    problems = [f"a placeholder is left: {left}" for left in PLACEHOLDER.findall(COMMENT.sub("", readme))]
    ours, theirs = sections(readme), sections(template)
    titles = [title for title, _ in ours]
    if len(titles) != len(theirs) or not all(map(matches, [title for title, _ in theirs], titles)):
        names = ", ".join(titles[1:])
        expected = ", ".join(title for title, _ in theirs[1:])
        return [*problems, f"its sections are {names}; the template's are {expected}"]
    for (title, tables), (_, wanted) in zip(ours, theirs):
        if len(tables) < len(wanted):
            problems.append(f"{title}: {len(tables)} table(s), the template has {len(wanted)}")
            continue
        for table, rows in zip(tables, wanted):
            names = [row[0] for row in table]
            for index, row in enumerate(rows):
                if index >= len(names) or not matches(row[0], names[index]):
                    problems.append(f"{title}: row {index + 1} is not {row[0]!r} (the template's rows come first)")
                    break
            if rows[0][1:2] == [""] and rows[0][0] == "Feature":
                problems += status_problems(title, table[1:])
    return problems


def status_problems(title: str, rows: list[list[str]]) -> list[str]:
    """Return the rows of a "What works" table in section @p title without one status or a ❌ without an issue."""
    problems = []
    for row in rows:
        status, notes = [*row, "", ""][1:3]
        if status not in STATUS:
            problems.append(f"{title}: {row[0]}: the status is {status!r}, not one of {' '.join(STATUS)}")
        elif status == "❌" and not ISSUE.search(notes):
            problems.append(f"{title}: {row[0]}: doesn't work, and links no issue")
    return problems


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
    differs = []
    if (repo / "headset.json").is_file():
        readme = repo / "README.md"
        template = TEMPLATE.read_text(encoding="utf-8")
        differs = readme_problems(readme.read_text(encoding="utf-8"), template) if readme.is_file() else ["missing"]
        for problem in differs:
            print(f"README.md: {problem}")
        print(f"{len(differs)} departure(s) from {TEMPLATE.relative_to(REPO).as_posix()}")
    return 1 if count or differs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
