#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""A release's tag against the version it names, and its notes and changelog (docs/releasing.md).

    python scripts/release.py check TAG [--version-file pyproject.toml|headset.json]
    python scripts/release.py notes TAG [--title NAME]     in the repository's root: notes.md, CHANGELOG.md
    python scripts/release.py close TAG --repo OWNER/NAME [--dry-run]

Tags are `vMAJOR.MINOR` and `vMAJOR.MINOR-beta[.N]`, N from 2: `-beta` is the first beta. pyproject.toml holds
the same version in Python's spelling (`v1.0-beta` is `1.0b1`, `v1.0-beta.2` is `1.0b2`), a headset's
headset.json the tag's own (`1.0-beta.2`). `check` prints `prerelease` for a beta, `release` otherwise, and
fails when the tag is not a version or names another one than the version file.

`notes` writes the release's notes and the zip's CHANGELOG.md from the conventional commits (git-cliff, the
repository's cliff.toml). The changelog starts at the first release: its notes are its tag's own words.
After it, a beta's notes list what changed since the release before it, a stable release's what changed since
the stable release before it (or the first release), betas included. CHANGELOG.md holds every release after
the first by git-cliff, then the first release's own words.

`close` runs after the release is published: every open issue of the repository whose Stage is
"Awaiting release" (fixed on main, docs: CLAUDE.md's Backlog) gets a comment naming the release and is
closed as completed. It reads the token from GH_TOKEN or GITHUB_TOKEN; --dry-run only lists the issues.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

REPO = Path(__file__).resolve().parent.parent
GIT_CLIFF = ("uvx", "git-cliff@2.14.2")
_TAG = re.compile(r"^v(?P<base>\d+\.\d+)(?:-beta(?:\.(?P<n>[2-9]|[1-9]\d+))?)?$")
_PROJECT_VERSION = re.compile(r'^\[project\][^\[]*?^version = "(?P<version>[^"]+)"', re.MULTILINE | re.DOTALL)
HEADER = (
    "# Changelog\n\nGenerated from the conventional commits by git-cliff; the first release's entry is its own words.\n"
)


def python_version(tag: str) -> str:
    """Python's spelling of the version a release tag names."""
    match = _TAG.match(tag)
    if match is None:
        message = f"{tag} is not a release tag: vMAJOR.MINOR or vMAJOR.MINOR-beta[.N] (N from 2)"
        raise ValueError(message)
    if "-beta" not in tag:
        return match["base"]
    return f"{match['base']}b{match['n'] or 1}"


def key(tag: str) -> tuple[int, int, float]:
    """Order release tags: by version, a beta before its stable release."""
    match = _TAG.match(tag)
    if match is None:
        message = f"{tag} is not a release tag"
        raise ValueError(message)
    major, minor = (int(part) for part in match["base"].split("."))
    beta = float("inf") if "-beta" not in tag else int(match["n"] or 1)
    return major, minor, beta


def project_version(pyproject: str) -> str:
    """Read the version in pyproject.toml's [project] table."""
    match = _PROJECT_VERSION.search(pyproject)
    if match is None:
        message = "pyproject.toml has no version in [project]"
        raise ValueError(message)
    return match["version"]


def check(tag: str, text: str, *, headset: bool = False) -> str:
    """`prerelease` or `release` for a tag that names the version file's version (@p headset: headset.json)."""
    wanted = python_version(tag)
    if headset:
        have = json.loads(text)["version"]
        if tag != f"v{have}":
            message = f"{tag} is not v{have} (headset.json)"
            raise ValueError(message)
    else:
        have = project_version(text)
        if wanted != have:
            message = f"{tag} is version {wanted}, but pyproject.toml says {have}"
            raise ValueError(message)
    return "prerelease" if "-beta" in tag else "release"


def ranges(tag: str, tags: list[str]) -> tuple[str | None, str]:
    """Return the git range of @p tag's notes (None for the first release) and the first release's tag.

    @p tags are the repository's tags; those that are not release tags are ignored.
    """
    releases = sorted({t for t in [*tags, tag] if _TAG.match(t)}, key=key)
    first = releases[0]
    if tag == first:
        return None, first
    before = releases[: releases.index(tag)]
    if "-beta" in tag:
        return f"{before[-1]}..{tag}", first
    stable = [t for t in before if "-beta" not in t]
    return f"{(stable or [first])[-1]}..{tag}", first


def git(*args: str) -> str:
    """Run git in the current directory; return its output."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout  # noqa: S603, S607  # git, ours


def cliff(*args: str) -> str:
    """Run git-cliff with the repository's cliff.toml; return its output."""
    return subprocess.run([*GIT_CLIFF, *args], check=True, capture_output=True, text=True).stdout  # noqa: S603  # pinned tool


def own_words(tag: str) -> tuple[str, str]:
    """Return an annotated tag's subject and body."""
    subject = git("tag", "-l", "--format=%(contents:subject)", tag).strip()
    body = git("tag", "-l", "--format=%(contents:body)", tag).strip()
    return subject, body


def notes(tag: str) -> tuple[str, str]:
    """Return @p tag's release notes and the zip's CHANGELOG.md."""
    notes_range, first = ranges(tag, git("tag", "-l", "v*").split())
    _, body = own_words(tag)
    first_subject, first_body = own_words(first)
    first_entry = f"## {first.removeprefix('v')}\n\n{first_subject}\n\n{first_body}".rstrip() + "\n"
    if notes_range is None:
        return f"{body}\n", f"{HEADER}\n{first_entry}"
    ignore = () if "-beta" in tag else ("--ignore-tags=-beta",)  # one section: the betas' commits are this one's
    changes = cliff(*ignore, "--strip", "header", notes_range)
    later = cliff("--strip", "header", f"{first}..{tag}")
    return f"{body}\n\n{changes}", f"{HEADER}\n{later.strip()}\n\n{first_entry}"


#: The organisation's issue field for where an issue stands, and the value of a fix that waits for a release.
STAGE = "Stage"
AWAITING_RELEASE = "Awaiting release"
API = "https://api.github.com"


def github(token: str) -> Callable[[str, str, dict | None], Any]:
    """Return a GitHub REST caller authenticated with @p token."""

    def call(method: str, path: str, body: dict | None = None) -> Any:  # noqa: ANN401  # GitHub's JSON as it is
        url = path if path.startswith("https://") else f"{API}/{path}"
        request = urllib.request.Request(  # noqa: S310  # only GitHub's API, built from a fixed https prefix
            url,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=60) as answer:  # noqa: S310  # the request above
            data = answer.read()
        return json.loads(data) if data else None

    return call


def environment_token() -> str:
    """Return the GitHub token from GH_TOKEN or GITHUB_TOKEN; refuse without one."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        msg = "no token: set GH_TOKEN or GITHUB_TOKEN"
        raise ValueError(msg)
    return token


def awaiting_release(repo: str, call: Callable[[str, str, dict | None], Any]) -> list[int]:
    """Return the open issues of @p repo whose Stage is Awaiting release, oldest first."""
    found = []
    page = 1
    while True:
        issues = call("GET", f"repos/{repo}/issues?state=open&per_page=100&page={page}", None)
        for issue in issues:
            if "pull_request" in issue:
                continue
            values = call("GET", f"repos/{repo}/issues/{issue['number']}/issue-field-values", None)
            if any(
                v.get("issue_field_name") == STAGE
                and (v.get("single_select_option") or {}).get("name") == AWAITING_RELEASE
                for v in values
            ):
                found.append(issue["number"])
        if len(issues) < 100:  # noqa: PLR2004  # GitHub's largest page
            return sorted(found)
        page += 1


def close_released(
    tag: str, repo: str, call: Callable[[str, str, dict | None], Any], *, dry_run: bool = False
) -> list[int]:
    """Comment the release @p tag on every issue that awaited it and close it as completed; return them."""
    numbers = awaiting_release(repo, call)
    url = f"https://github.com/{repo}/releases/tag/{tag}"
    for number in numbers:
        print(f"#{number}: released in {tag}" + (" (dry run)" if dry_run else ""))
        if dry_run:
            continue
        call("POST", f"repos/{repo}/issues/{number}/comments", {"body": f"Released in [{tag}]({url})."})
        call("PATCH", f"repos/{repo}/issues/{number}", {"state": "closed", "state_reason": "completed"})
    return numbers


def main(argv: list[str] | None = None) -> int:
    """Run the command named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    checking = sub.add_parser("check", help="the tag names the version file's version")
    checking.add_argument("tag")
    checking.add_argument("--version-file", default=str(REPO / "pyproject.toml"))
    noting = sub.add_parser("notes", help="notes.md and CHANGELOG.md, in the current repository")
    noting.add_argument("tag")
    noting.add_argument("--title", default="overpressure", help="the title's name when the tag has no subject")
    closing = sub.add_parser("close", help="close the issues awaiting this release, with a comment naming it")
    closing.add_argument("tag")
    closing.add_argument("--repo", required=True, help="OWNER/NAME")
    closing.add_argument("--dry-run", action="store_true", help="only list the issues")
    args = parser.parse_args(argv)
    try:
        if args.command == "close":
            closed = close_released(args.tag, args.repo, github(environment_token()), dry_run=args.dry_run)
            print(f"{len(closed)} issue(s) awaited {args.tag}")
            return 0
        if args.command == "check":
            path = Path(args.version_file)
            print(check(args.tag, path.read_text(encoding="utf-8"), headset=path.name == "headset.json"))
            return 0
        text, changelog = notes(args.tag)
        Path("notes.md").write_text(text, encoding="utf-8", newline="\n")
        Path("CHANGELOG.md").write_text(changelog, encoding="utf-8", newline="\n")
        # The release's title: the tag's first line (docs/releasing.md), else the name and version.
        print(own_words(args.tag)[0] or f"{args.title} {args.tag.removeprefix('v')}")
    except (ValueError, subprocess.CalledProcessError, OSError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
