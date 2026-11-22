# SPDX-License-Identifier: Unlicense
"""Versions across the project: calendar versions as Home Assistant's, their order, channel and two spellings.

A release is YYYY.M.PATCH (the year, the month without a leading zero, the month's releases counted from 0), a beta
the coming release with bN (2026.12.0b3), and main between releases the next one as a development version
(2026.12.0.dev0, 2026.12.0b4.dev0). A preview (a download of main, a checkout) shows its commit's time in place of
the development number (2026.12.0.dev202612031154), so previews sort by age. These strings are valid Python
versions as they stand: pyproject.toml, headset.json, the tags and the logs use them (the machine spelling); the
window, release titles and Save logs... use display() ("2026.12.0 beta 3").

The releases before the switch (v1.0-beta.N, 1.0bN in pyproject.toml) still parse and sort before every calendar
version. A release zip carries its commit and the commit's time in COMMIT and COMMITTED (export-subst in
.gitattributes); a checkout asks git.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: The commit and its time a release zip was made from (git archive's export-subst); unfilled in a checkout.
COMMIT = "$Format:%H$"
COMMITTED = "$Format:%cI$"
_PROJECT = re.compile(r'^\[project\][^\[]*?^version = "(?P<version>[^"]+)"', re.MULTILINE | re.DOTALL)
#: A Python version of either scheme: 2026.12.0, 2026.12.0b3, 2026.12.0b4.dev0, and the old 1.0, 1.0b5, 1.0b5.dev0.
_PYTHON = re.compile(r"^(?P<release>\d+(?:\.\d+){1,2})(?:b(?P<beta>\d+))?(?:\.dev(?P<dev>\d+))?$")
#: The old tags' spelling: v1.0, v1.0-beta (the first beta), v1.0-beta.2; with or without the v.
_OLD_TAG = re.compile(r"^v?(?P<release>\d+\.\d+)(?P<beta>-beta(?:\.(?P<n>\d+))?)?$")
#: The display spelling: 2026.12.0, 2026.12.0 beta 3, 1.0 beta 5, 1.0 beta (the old first beta).
_DISPLAY = re.compile(r"^(?P<release>\d+(?:\.\d+){1,2})(?: beta(?: (?P<beta>\d+))?)?$")

STABLE, BETA, PREVIEW = "stable", "beta", "preview"
#: How versions sort (Version.key): the release numbers, the phase (development, beta, release), the development number.
Key = tuple[tuple[int, ...], tuple[int, int], float]


@dataclass(frozen=True)
class Version:
    """One version: its release numbers, its beta and its development number (None where it has none)."""

    release: tuple[int, ...]
    beta: int | None = None
    dev: int | None = None

    @property
    def calendar(self) -> bool:
        """Tell whether this is a calendar version (YYYY.M.PATCH), not one of the releases before the switch."""
        return len(self.release) == 3 and self.release[0] >= 2000  # noqa: PLR2004  # YYYY.M.PATCH, a year

    @property
    def channel(self) -> str:
        """Return its channel: preview (a development version of main), beta or stable."""
        if self.dev is not None:
            return PREVIEW
        return BETA if self.beta is not None else STABLE

    @property
    def key(self) -> Key:
        """Return a key that sorts versions as Python does: X.devN, then XbN.devM, XbN, then X."""
        release = self.release + (0,) * (3 - len(self.release))
        if self.beta is not None:
            phase = (1, self.beta)
        elif self.dev is not None:
            phase = (0, 0)  # X.devN comes before X's first beta
        else:
            phase = (2, 0)
        return release, phase, float(self.dev) if self.dev is not None else float("inf")

    @property
    def base(self) -> str:
        """Return the release it is or leads to: 2026.12.0 for 2026.12.0b3 and 2026.12.0.dev0."""
        return ".".join(map(str, self.release))

    def machine(self) -> str:
        """Return the machine spelling (Python's): 2026.12.0b3, 2026.12.0.dev202612031154."""
        text = self.base + (f"b{self.beta}" if self.beta is not None else "")
        return text + (f".dev{self.dev}" if self.dev is not None else "")

    def display(self) -> str:
        """Return the display spelling: 2026.12.0, 2026.12.0 beta 3, 2026.12.0 preview; 1.0 beta for the old b1."""
        if self.beta is None:
            words = self.base
        elif not self.calendar and self.beta == 1:
            words = f"{self.base} beta"  # the old tags named the first beta v1.0-beta
        else:
            words = f"{self.base} beta {self.beta}"
        return f"{words} preview" if self.dev is not None else words


def parse(text: str) -> Version | None:
    """Return the version @p text names in any spelling (machine, an old tag, display), None for none."""
    text = text.strip()
    match = _PYTHON.match(text)
    if match:
        return Version(_numbers(match["release"]), _int(match["beta"]), _int(match["dev"]))
    match = _OLD_TAG.match(text)
    if match:
        beta = None if match["beta"] is None else int(match["n"] or 1)
        return Version(_numbers(match["release"]), beta)
    match = _DISPLAY.match(text)
    if match:
        beta = None if " beta" not in text else int(match["beta"] or 1)
        return Version(_numbers(match["release"]), beta)
    return None


def key(text: str) -> Key | None:
    """Return the sorting key of the version @p text names (Version.key), None for no version."""
    found = parse(text)
    return None if found is None else found.key


def _numbers(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _int(text: str | None) -> int | None:
    return None if text is None else int(text)


def channel(text: str) -> str:
    """Return the channel of the version @p text names; preview for no version."""
    found = parse(text)
    return PREVIEW if found is None else found.channel


def machine(text: str) -> str:
    """Return @p text in the machine spelling; unchanged when it names no version."""
    found = parse(text)
    return text if found is None else found.machine()


def display(text: str) -> str:
    """Return @p text in the display spelling; unchanged when it names no version."""
    found = parse(text)
    return text if found is None else found.display()


def tag_spelling(text: str) -> str:
    """Return a version as its release is tagged, without the old v: 2026.12.0b3; the old 1.0b2 is 1.0-beta.2."""
    found = parse(text)
    if found is None:
        return text
    if found.calendar:
        return found.machine()
    beta = "" if found.beta is None else "-beta" + (f".{found.beta}" if found.beta != 1 else "")
    return f"{found.base}{beta}"


def stamp(when: dt.datetime) -> int:
    """Return a commit's time as a preview's development number: YYYYMMDDHHMM in UTC."""
    return int(when.astimezone(dt.timezone.utc).strftime("%Y%m%d%H%M"))


def committed_at(text: str) -> dt.datetime | None:
    """Return the time @p text names: ISO 8601 (git's %cI) or seconds since 1970 (%ct); None for neither."""
    text = text.strip()
    if not text or text.startswith("$"):
        return None
    try:
        if text.isdigit():
            return dt.datetime.fromtimestamp(int(text), dt.timezone.utc)
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None


def preview_of(text: str, when: dt.datetime | None) -> str:
    """Return a development version @p text with its commit's time @p when as the development number."""
    found = parse(text)
    if found is None or found.dev is None or when is None:
        return text
    return Version(found.release, found.beta, stamp(when)).machine()


def preview_words(text: str, when: dt.datetime | None, sha: str = "") -> str:
    """Return a preview in the display spelling: "2026.12.0 preview from 3 December 2026 (4d8f977)"."""
    found = parse(text)
    words = found.display() if found is not None else text
    if when is not None:
        utc = when.astimezone(dt.timezone.utc)
        words += f" from {utc.day} {utc:%B %Y}"
    return words + (f" ({sha[:7]})" if sha else "")


def python_version(root: Path = REPO) -> str:
    """Return pyproject.toml's version (2026.12.0b3), "" when it cannot be read."""
    try:
        match = _PROJECT.search((root / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError:
        return ""
    return match["version"] if match else ""


def _git(root: Path, *words: str) -> str:
    try:
        done = subprocess.run(  # noqa: S603  # git with fixed arguments
            ["git", "-C", str(root), *words],  # noqa: S607  # git from PATH, as everywhere
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def commit(root: Path = REPO) -> str:
    """Return the commit this overpressure was made from: the zip's, else the checkout's, else ""."""
    if not COMMIT.startswith("$"):
        return COMMIT
    return _git(root, "rev-parse", "HEAD")


def commit_time(root: Path = REPO) -> dt.datetime | None:
    """Return the time of the commit this overpressure was made from, None when unknown."""
    return committed_at(COMMITTED) or committed_at(_git(root, "log", "-1", "--format=%cI"))


def current(root: Path = REPO) -> str:
    """Return this overpressure's version in the machine spelling; a preview with its commit's time."""
    version = python_version(root)
    return preview_of(version, commit_time(root)) if channel(version) == PREVIEW else version


def words(root: Path = REPO) -> str:
    """Return this overpressure's version in the display spelling: "2026.12.0 beta 3", a preview with its date."""
    version = python_version(root)
    if not version:
        return ""
    if channel(version) == PREVIEW:
        return preview_words(version, commit_time(root), commit(root))
    return display(version)


def describe(root: Path = REPO) -> str:
    """Return this overpressure in words: "overpressure 2026.12.0 beta 3 (2026.12.0b3, commit 0123abcd4567)"."""
    version = python_version(root)
    if not version:
        return "overpressure (version unknown)"
    sha = commit(root)
    spelled = preview_words(version, commit_time(root)) if channel(version) == PREVIEW else display(version)
    return f"overpressure {spelled} ({current(root)}" + (f", commit {sha[:12]})" if sha else ")")
