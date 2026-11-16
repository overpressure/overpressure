# SPDX-License-Identifier: Unlicense
"""Which overpressure this is: its version in the release tags' spelling, its channel and its commit.

pyproject.toml holds the version in Python's spelling. A release is tagged with it (docs/releasing.md); between
releases, main carries the next pre-release as a development version (1.0b2.dev0), so a preview never passes
for the release before it. A release zip carries its commit in COMMIT (git archive fills it in,
export-subst in .gitattributes); a checkout asks git.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: The commit and its time a release zip was made from (git archive's export-subst); unfilled in a checkout.
COMMIT = "$Format:%H$"
COMMITTED = "$Format:%cI$"
_VERSION = re.compile(r'^\[project\][^\[]*?^version = "(?P<version>[^"]+)"', re.MULTILINE | re.DOTALL)
_PEP440 = re.compile(r"^(?P<base>\d+\.\d+)(?:b(?P<beta>\d+))?(?P<dev>\.dev\d+)?$")

STABLE, BETA, PREVIEW = "stable", "beta", "preview"


def python_version(root: Path = REPO) -> str:
    """Return pyproject.toml's version (1.0b1), "" when it cannot be read."""
    try:
        match = _VERSION.search((root / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError:
        return ""
    return match["version"] if match else ""


def tag_spelling(version: str) -> str:
    """Return a Python version in the release tags' spelling: 1.0b1 is 1.0-beta, 1.0b2 1.0-beta.2."""
    match = _PEP440.match(version)
    if match is None:
        return version
    if match["beta"] is None:
        return match["base"]
    return f"{match['base']}-beta" + (f".{match['beta']}" if match["beta"] != "1" else "")


def channel(version: str) -> str:
    """Return the channel a version belongs to: preview (a development version of main), beta or stable."""
    match = _PEP440.match(version)
    if match is None or match["dev"]:
        return PREVIEW
    return BETA if match["beta"] else STABLE


def commit(root: Path = REPO) -> str:
    """Return the commit this overpressure was made from: the zip's, else the checkout's, else ""."""
    if not COMMIT.startswith("$"):
        return COMMIT
    try:
        done = subprocess.run(  # noqa: S603  # git with fixed arguments
            ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607  # git from PATH, as everywhere
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def describe(root: Path = REPO) -> str:
    """Return this overpressure in words: "overpressure 1.0-beta (beta, commit 0123abcd)"."""
    version = python_version(root)
    if not version:
        return "overpressure (version unknown)"
    which = channel(version)
    spelled = tag_spelling(version.split(".dev")[0])
    name = f"preview of {spelled}" if which == PREVIEW else spelled
    sha = commit(root)
    return f"overpressure {name} ({which}" + (f", commit {sha[:12]})" if sha else ")")
