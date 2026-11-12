#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""What Valve has moved since the image's pins, for the weekly upgrade check (upgrade-check.sh).

    python upstream.py lock-diff OLD_LOCK NEW_LOCK         the packages a new lock changes

`lock-diff` prints a Markdown summary, the packages the session depends on first. Valve's kernel is
followed in overpressure/linux, not here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: The packages the session depends on most, listed first in a summary.
KEY_PACKAGES = (
    "deckard-steamvr-rel",
    "deckard-steamvr-session",
    "deckard-steam-rel",
    "deckard-mesa-linux-aarch64",
    "deckard-mesa-android-aarch64",
    "deckard-vulkan-layers-linux-aarch64",
    "lepton-compat",
    "steamos-customizations-deckard",
    "steamos-manager",
    "pipewire",
    "systemd",
)
_PACKAGE_FILE = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+-[^-]+)-(?:aarch64|any)\.pkg\.tar\.(?:zst|xz)$")
LOCK_FIELDS = 2  #: a lock line: SHA-256 and file name
COMMAND_ARGS = 3  #: a command and its two files


def lock_versions(lock: str) -> dict[str, str]:
    """Return each package's version in a packages.lock (lines of SHA-256 and file name)."""
    versions = {}
    for line in lock.splitlines():
        parts = line.split()
        if len(parts) == LOCK_FIELDS:
            match = _PACKAGE_FILE.match(parts[1])
            if match:
                versions[match["name"]] = match["version"]
    return versions


def lock_diff(old: str, new: str) -> str:
    """Return a Markdown summary of what a new lock changes against the old one."""
    before, after = lock_versions(old), lock_versions(new)
    changed = sorted(n for n in before.keys() & after.keys() if before[n] != after[n])
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    if not (changed or added or removed):
        return "No package moved since packages.lock.\n"
    order = sorted(changed, key=lambda n: (n not in KEY_PACKAGES, n))
    lines = [f"{len(changed)} changed, {len(added)} added, {len(removed)} removed since packages.lock:", ""]
    lines += [f"- {'**' + n + '**' if n in KEY_PACKAGES else n}: {before[n]} -> {after[n]}" for n in order]
    lines += [f"- {n}: new, {after[n]}" for n in added]
    lines += [f"- {n}: gone (was {before[n]})" for n in removed]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run one of the commands."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) == COMMAND_ARGS and args[0] == "lock-diff":
        old, new = (Path(a).read_text(encoding="utf-8") for a in args[1:])
        sys.stdout.write(lock_diff(old, new))
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main())
