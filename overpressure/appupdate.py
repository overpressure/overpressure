# SPDX-License-Identifier: Unlicense
"""The app's own updates: a newer overpressure release that carries the app (docs/tool.md#the-apps-updates).

A release of overpressure/overpressure carries the installed app for each system and MANIFEST, the updater's
manifest: the release's version, its notes and each system's installer with its signature (scripts/app-manifest.py
writes it in the release workflow). The engine picks the release, the app's core installs it: it downloads the
installer the manifest names, checks its signature against the key the app was built with, and replaces itself.

Which release: the newest that is newer than this one, on this one's channel. A stable app takes stable releases
only; a beta (or a preview, a development build) takes betas too, and a stable release once it is the newest.
A release without MANIFEST (one from before the app, or one whose app build failed) is not offered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import CODE
from . import version as own_version
from .catalogue import GITHUB, fetch

if TYPE_CHECKING:
    from collections.abc import Callable

#: overpressure's own repository, whose releases carry the app.
REPOSITORY = "overpressure/overpressure"
#: The updater's manifest among a release's files.
MANIFEST = "overpressure-app.json"
#: How much of a release's notes the app shows; the rest is on its page.
NOTES = 1200


@dataclass(frozen=True)
class Update:
    """A newer release with the app in it."""

    version: str  #: its version in the machine spelling (2026.12.0b3)
    words: str  #: in the display spelling (2026.12.0 beta 3)
    manifest: str  #: the URL of its MANIFEST, which the app's core reads
    page: str  #: the release's page
    notes: str  #: the start of its notes

    def as_dict(self) -> dict[str, str]:
        """Return it as the app's server answers it."""
        return {
            "version": self.version,
            "semver": semver(self.version),
            "words": self.words,
            "manifest": self.manifest,
            "page": self.page,
            "notes": self.notes,
        }


def semver(text: str) -> str:
    """Return the version @p text names as the app's bundle spells it (semver): 2026.12.0b3 is 2026.12.0-beta.3.

    The app's installers carry it; a preview is 2026.12.0-dev.N. The engine compares versions, not the app.
    """
    found = own_version.parse(text)
    if found is None:
        msg = f"not a version: {text}"
        raise ValueError(msg)
    release = found.release + (0,) * (3 - len(found.release))
    words = ".".join(map(str, release[:3]))
    if found.dev is not None:
        return f"{words}-dev.{found.dev}"
    return f"{words}-beta.{found.beta}" if found.beta is not None else words


def _notes(body: str) -> str:
    """Return the start of a release's notes, cut at a line."""
    body = body.strip()
    if len(body) <= NOTES:
        return body
    return body[: body.rfind("\n", 0, NOTES) if "\n" in body[:NOTES] else NOTES].rstrip() + "\n…"


def newest(current: str, get: Callable[[str], bytes] = fetch) -> Update | None:
    """Return the newest release newer than @p current on its channel that carries the app, None for none."""
    own = own_version.parse(current)
    if own is None:
        return None
    betas = own.channel != own_version.STABLE
    found = json.loads(get(f"{GITHUB}/repos/{REPOSITORY}/releases?per_page=100"))
    offers = []
    for item in found if isinstance(found, list) else []:
        tag = str(item.get("tag_name", ""))
        parsed = own_version.parse(tag)
        assets = {a.get("name"): a.get("browser_download_url", "") for a in item.get("assets", [])}
        if item.get("draft") or parsed is None or MANIFEST not in assets or parsed.key <= own.key:
            continue
        if item.get("prerelease") and not betas:
            continue
        offers.append((parsed, item, assets[MANIFEST]))
    if not offers:
        return None
    parsed, item, manifest = max(offers, key=lambda offer: offer[0].key)
    return Update(
        parsed.machine(),
        parsed.display(),
        manifest,
        str(item.get("html_url", "")),
        _notes(str(item.get("body") or "")),
    )


def check(get: Callable[[str], bytes] = fetch) -> Update | None:
    """Return the update for this overpressure (its own files' version), None for none."""
    return newest(own_version.current(CODE), get)
