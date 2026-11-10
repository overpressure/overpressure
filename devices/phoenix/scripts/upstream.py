#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""What Valve has moved since the image's pins, for the weekly upgrade check (upgrade-check.sh).

    python upstream.py kernel PACMAN_CONF KERNEL_STEP     Valve's current kernel against the pinned one
    python upstream.py lock-diff OLD_LOCK NEW_LOCK         the packages a new lock changes

`kernel` prints shell assignments: the pinned and the current version of linux-72-deckard (from the
Frame repository's database, named in pacman.conf) and the current one's source package, empty while
Valve has not published it. `lock-diff` prints a Markdown summary, the packages the session depends
on first.
"""

from __future__ import annotations

import io
import re
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

KERNEL = "linux-72-deckard"
REPO = "deckard-arch-hotfixes-release-0.3"
SOURCE = "https://steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/source/"
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
_PINNED_KERNEL = re.compile(rf"^PACKAGE={KERNEL}-(?P<version>\S+)\.src\.tar\.gz$", re.MULTILINE)
TIMEOUT = 60
LOCK_FIELDS = 2  #: a lock line: SHA-256 and file name
COMMAND_ARGS = 3  #: a command and its two files


def repo_server(pacman_conf: str, repo: str = REPO) -> str:
    """Return the first Server of @p repo in a pacman.conf."""
    section = None
    for raw in pacman_conf.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == repo and line.startswith("Server"):
            return line.split("=", 1)[1].strip().replace("$repo", repo).replace("$arch", "aarch64")
    message = f"pacman.conf has no Server for [{repo}]"
    raise ValueError(message)


def db_versions(db: bytes) -> dict[str, str]:
    """Return each package's version in a pacman repository database."""
    versions = {}
    with tarfile.open(fileobj=io.BytesIO(db)) as tar:
        for member in tar.getmembers():
            if not member.name.endswith("/desc"):
                continue
            handle = tar.extractfile(member)
            fields = handle.read().decode("utf-8").split("\n\n") if handle else []
            entry = {}
            for field in fields:
                key, _, value = field.strip().partition("\n")
                entry[key.strip("%")] = value.strip()
            if "NAME" in entry and "VERSION" in entry:
                versions[entry["NAME"]] = entry["VERSION"]
    return versions


def pinned_kernel(step_script: str) -> str:
    """Return the kernel version build-kernel-step.sh pins (its PACKAGE)."""
    match = _PINNED_KERNEL.search(step_script)
    if match is None:
        message = "no PACKAGE line for the kernel's source package"
        raise ValueError(message)
    return match["version"]


def source_url(version: str) -> str:
    """Return the URL of Valve's source package for a kernel version."""
    return f"{SOURCE}{KERNEL}-{version.replace('+', '%2B')}.src.tar.gz"


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


def fetch(url: str) -> bytes:
    """Return a URL's body."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310  # Valve's fixed https servers
        return response.read()


def published(url: str) -> bool:
    """Whether a URL answers a HEAD request."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=TIMEOUT):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        return False


def main(argv: list[str] | None = None) -> int:
    """Run one of the commands."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) == COMMAND_ARGS and args[0] == "kernel":
        conf, step = (Path(a).read_text(encoding="utf-8") for a in args[1:])
        current = db_versions(fetch(f"{repo_server(conf)}/{REPO}.db"))[KERNEL]
        url = source_url(current)
        print(f"kernel_pinned={pinned_kernel(step)}")
        print(f"kernel_current={current}")
        print(f"kernel_source={url if published(url) else ''}")
        return 0
    if len(args) == COMMAND_ARGS and args[0] == "lock-diff":
        old, new = (Path(a).read_text(encoding="utf-8") for a in args[1:])
        sys.stdout.write(lock_diff(old, new))
        return 0
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    sys.exit(main())
