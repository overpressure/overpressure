#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""A release's version, its tag against the version file, and its notes and changelog (docs/releasing.md).

    python scripts/release.py bump beta|stable|next [--version-file pyproject.toml|headset.json] [--today DATE]
    python scripts/release.py check TAG [--version-file pyproject.toml|headset.json]
    python scripts/release.py notes TAG [--title NAME] [--api MAJOR.MINOR]    in the repository's root: notes.md,
                                                                                   CHANGELOG.md
    python scripts/release.py close TAG --repo OWNER/NAME [--dry-run]

Versions are calendar versions as Home Assistant's (overpressure/version.py): a release YYYY.M.PATCH, a beta
YYYY.M.PATCHbN, main between releases a development version; the tag is the version itself. pyproject.toml and a
headset's headset.json hold it. `bump` writes the version file's next version and prints it: `beta` the next
beta (a new series takes this month and its next free patch; a running one its next bN), `stable` the release
(a running beta series keeps its name), `next` the development version after the release the file names.
`check` prints `prerelease` for a beta, `release` otherwise, and fails when the tag is not a calendar release or
names another version than the version file. The releases before the switch (v1.0-beta.N) still sort first.
`notes --api` (a headset's release) names in the notes the oldest overpressure release that runs that headset
interface, from this overpressure checkout's tags, and fails while none does: overpressure is released first.

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
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # overpressure's own version module (the standard library only), also from a headset's CI

from overpressure import version  # noqa: E402  # after the path above

if TYPE_CHECKING:
    from collections.abc import Callable

GIT_CLIFF = ("uvx", "git-cliff@2.14.2")
_PROJECT_VERSION = re.compile(r'^\[project\][^\[]*?^version = "(?P<version>[^"]+)"', re.MULTILINE | re.DOTALL)
_HEADSET_VERSION = re.compile(r'("version":\s*")[^"]*(")')
#: git-cliff's --ignore-tags for betas in both spellings: v1.0-beta.2 and 2026.12.0b3.
BETA_TAGS = r"-beta|\db\d+$"
HEADER = (
    "# Changelog\n\nGenerated from the conventional commits by git-cliff; the first release's entry is its own words.\n"
)


def release_version(tag: str) -> version.Version | None:
    """Return the release a tag names (either scheme), None for a tag that is no release (a development version)."""
    found = version.parse(tag)
    return found if found is not None and found.dev is None else None


def key(tag: str) -> version.Key:
    """Order release tags: by version, a beta before its release, the old scheme before the calendar one."""
    found = release_version(tag)
    if found is None:
        message = f"{tag} is not a release tag"
        raise ValueError(message)
    return found.key


def project_version(pyproject: str) -> str:
    """Read the version in pyproject.toml's [project] table."""
    match = _PROJECT_VERSION.search(pyproject)
    if match is None:
        message = "pyproject.toml has no version in [project]"
        raise ValueError(message)
    return match["version"]


def file_version(text: str, *, headset: bool = False) -> str:
    """Return the version a version file holds: headset.json's (@p headset) or pyproject.toml's."""
    return str(json.loads(text)["version"]) if headset else project_version(text)


def check(tag: str, text: str, *, headset: bool = False) -> str:
    """`prerelease` or `release` for a calendar release tag that is the version file's version (@p headset)."""
    found = release_version(tag)
    if found is None or not found.calendar or found.machine() != tag:
        message = f"{tag} is not a release tag: YYYY.M.PATCH or YYYY.M.PATCHbN, as Python spells it (2026.12.0b3)"
        raise ValueError(message)
    have = file_version(text, headset=headset)
    if have != tag:
        message = f"{tag} is not the version {'headset.json' if headset else 'pyproject.toml'} says: {have}"
        raise ValueError(message)
    return "prerelease" if found.channel == version.BETA else "release"


def _next_patch(tags: list[str], today: dt.date) -> int:
    """Return the first free patch of @p today's month among the release tags."""
    used = [
        found.release[2]
        for found in map(release_version, tags)
        if found is not None and found.calendar and found.release[:2] == (today.year, today.month)
    ]
    return max(used) + 1 if used else 0


def bump(kind: str, have: str, tags: list[str], today: dt.date) -> str:
    """Return the version after @p have for @p kind: the next beta, the release, or the next development version.

    A beta series keeps its name until its release: @p have is then its development version (2026.12.0b4.dev0). A new
    series takes @p today's month and its first patch no release tag has used.
    """
    found = version.parse(have)
    if found is None:
        message = f"{have} is not a version"
        raise ValueError(message)
    running = found.calendar and found.beta is not None  # a beta series: its next beta, or its release
    if kind == "next":
        if found.dev is not None or not found.calendar:
            message = f"{have} is not a release: the development version comes after one"
            raise ValueError(message)
        if found.beta is not None:
            return version.Version(found.release, found.beta + 1, 0).machine()
        year, month, patch = found.release
        return version.Version((year, month, patch + 1), None, 0).machine()
    if kind not in ("beta", "stable"):
        message = f"{kind}: bump beta, stable or next"
        raise ValueError(message)
    release = found.release if running else (today.year, today.month, _next_patch(tags, today))
    if kind == "stable":
        return version.Version(release).machine()
    return version.Version(release, found.beta if running else 0).machine()


def write_version(text: str, new: str, *, headset: bool = False) -> str:
    """Return the version file @p text with @p new as its version, everything else as it is."""
    if headset:
        changed, count = _HEADSET_VERSION.subn(lambda m: f"{m[1]}{new}{m[2]}", text, count=1)
    else:
        changed, count = re.subn(
            r'(^\[project\][^\[]*?^version = ")[^"]+(")',
            lambda m: f"{m[1]}{new}{m[2]}",
            text,
            count=1,
            flags=re.MULTILINE | re.DOTALL,
        )
    if count != 1:
        message = "the version file has no version to change"
        raise ValueError(message)
    return changed


def ranges(tag: str, tags: list[str]) -> tuple[str | None, str]:
    """Return the git range of @p tag's notes (None for the first release) and the first release's tag.

    @p tags are the repository's tags; those that are not release tags are ignored.
    """
    releases = sorted({t for t in [*tags, tag] if release_version(t) is not None}, key=key)
    first = releases[0]
    if tag == first:
        return None, first
    before = releases[: releases.index(tag)]
    if version.channel(tag) == version.BETA:
        return f"{before[-1]}..{tag}", first
    stable = [t for t in before if version.channel(t) != version.BETA]
    return f"{(stable or [first])[-1]}..{tag}", first


_SUPPORTED = re.compile(r"^SUPPORTED: dict\[int, int\] = (\{[^}]*\})", re.MULTILINE)


def supported(headset_py: str) -> dict[int, int]:
    """Return the headset interfaces an overpressure's headset.py runs (SUPPORTED: each major's newest minor)."""
    match = _SUPPORTED.search(headset_py)
    if match is None:
        return {}
    pairs = re.findall(r"(\d+)\s*:\s*(\d+)", match[1])
    return {int(major): int(minor) for major, minor in pairs}


def needs(api: str, tags: list[str], headset_py_at: Callable[[str], str]) -> str:
    """Return the oldest overpressure release (of @p tags) whose headset.py runs interface @p api, "" for none."""
    major, _, minor = api.partition(".")
    if not (major.isdigit() and minor.isdigit()):
        message = f"{api} is not a headset interface: MAJOR.MINOR"
        raise ValueError(message)
    for tag in sorted((t for t in tags if release_version(t) is not None), key=key):
        if supported(headset_py_at(tag)).get(int(major), -1) >= int(minor):
            return tag
    return ""


def needs_line(api: str) -> str:
    """Return the notes' line on the overpressure a headset release needs (interface @p api), from REPO's tags."""
    tags = git("-C", str(REPO), "tag", "-l").split()
    tag = needs(api, tags, lambda t: git("-C", str(REPO), "show", f"{t}:overpressure/headset.py"))
    if not tag:
        message = f"no overpressure release runs headset interface {api} yet: release overpressure first"
        raise ValueError(message)
    return f"Needs overpressure {version.display(tag)} or newer (headset interface {api})."


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
    notes_range, first = ranges(tag, git("tag", "-l").split())
    _, body = own_words(tag)
    first_subject, first_body = own_words(first)
    first_entry = f"## {first.removeprefix('v')}\n\n{first_subject}\n\n{first_body}".rstrip() + "\n"
    if notes_range is None:
        return f"{body}\n", f"{HEADER}\n{first_entry}"
    # One section for a release: its betas' commits are its own.
    ignore = () if version.channel(tag) == version.BETA else (f"--ignore-tags={BETA_TAGS}",)
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
    bumping = sub.add_parser("bump", help="write the version file's next version and print it")
    bumping.add_argument("kind", choices=("beta", "stable", "next"))
    bumping.add_argument("--version-file", default=str(REPO / "pyproject.toml"))
    bumping.add_argument("--today", type=dt.date.fromisoformat, help="the release's day (UTC), for tests; today")
    checking = sub.add_parser("check", help="the tag names the version file's version")
    checking.add_argument("tag")
    checking.add_argument("--version-file", default=str(REPO / "pyproject.toml"))
    noting = sub.add_parser("notes", help="notes.md and CHANGELOG.md, in the current repository")
    noting.add_argument("tag")
    noting.add_argument("--title", default="overpressure", help="the title's name when the tag has no subject")
    noting.add_argument("--api", help="a headset release's interface: the notes name the overpressure it needs")
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
        if args.command == "bump":
            path = Path(args.version_file)
            headset = path.name == "headset.json"
            text = path.read_text(encoding="utf-8")
            today = args.today or dt.datetime.now(dt.timezone.utc).date()
            new = bump(args.kind, file_version(text, headset=headset), git("tag", "-l").split(), today)
            path.write_text(write_version(text, new, headset=headset), encoding="utf-8", newline="\n")
            print(new)
            return 0
        if args.command == "check":
            path = Path(args.version_file)
            print(check(args.tag, path.read_text(encoding="utf-8"), headset=path.name == "headset.json"))
            return 0
        text, changelog = notes(args.tag)
        if args.api:
            text = f"{needs_line(args.api)}\n\n{text}"
        Path("notes.md").write_text(text, encoding="utf-8", newline="\n")
        Path("CHANGELOG.md").write_text(changelog, encoding="utf-8", newline="\n")
        # The release's title: the tag's first line (docs/releasing.md), else the name and version.
        print(own_words(args.tag)[0] or f"{args.title} {version.display(args.tag)}")
    except (ValueError, subprocess.CalledProcessError, OSError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
