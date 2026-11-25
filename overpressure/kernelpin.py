# SPDX-License-Identifier: Unlicense
"""A headset's pinned release of overpressure/linux, Valve's kernel with the project's layers.

A headset on Valve's kernel pins one of overpressure/linux's tags in its kernel/release.json: the repository, the
tag, its commit, the build image it was built with and every file's SHA-256. The installer downloads those files
from the tag's release and checks each (fetch, current); `kernel build` builds the same commit in the same image
on this computer instead (build) and checks the result against the same hashes, how an owner verifies a release
(mismatches with built: Image.gz differs, BUILD_DIFFERS).

The contracts are what no build checks: a name or a path one side uses and the other provides (a firmware file, a
V4L2 control, a sysfs name). Each side is a list of (file, pattern): the kernel's side reads the pinned commit's
files from GitHub, without its history (source_file, kept per commit); the headset's its own files or, behind
IN_PACKAGES, those of the overpressure/packages commit it pins. A renamed side fails before anything is installed.
Since interface 1.7.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from . import REPO, backup, tools, web
from .linux import IMAGE_OVERRIDE

if TYPE_CHECKING:
    from pathlib import Path

Log = Callable[[str], None]
Opener = Callable[..., object]

#: A contract path in overpressure/packages at the pinned commit, not in the headset's repository.
IN_PACKAGES = "packages:"
#: What a build of the pinned commit gives that the release does not: Valve's configuration signs modules
#: with a key each build generates and builds into Image.gz, so only the other files match the pin.
BUILD_DIFFERS = ("Image.gz",)


@dataclass(frozen=True)
class Release:
    """A pinned release: overpressure/linux's (kernel/release.json) or overpressure/packages' (packages.json)."""

    repository: str
    tag: str
    commit: str
    image: str
    files: dict[str, str]  #: name -> SHA-256

    def url(self, name: str) -> str:
        """Return the download URL of the release's file @p name."""
        tag = urllib.parse.quote(self.tag, safe="")
        return f"https://github.com/{self.repository}/releases/download/{tag}/{name}"

    def source_url(self, path: str) -> str:
        """Return the URL of the pinned commit's file @p path (the tree as it is, no history)."""
        return f"https://raw.githubusercontent.com/{self.repository}/{self.commit}/{path}"


def release(path: Path) -> Release:
    """Return the pin in @p path: a kernel's release.json, or a packages.json (no image, no files)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return Release(data["repository"], data["tag"], data["commit"], data.get("image", ""), dict(data.get("files", {})))


def sha256(path: Path) -> str:
    """Return @p path's SHA-256 (empty for an empty file, which no pin names)."""
    return backup.digest(path)["sha256"] if path.stat().st_size else ""


def mismatches(folder: Path, pin: Release, *, built: bool = False) -> list[str]:
    """Return the files in @p folder that are missing or differ from the pin, as messages.

    @p built: the folder is a build of the pinned commit, whose Image.gz has its own signing key (BUILD_DIFFERS).
    """
    found = []
    for name, digest in pin.files.items():
        path = folder / name
        if not path.is_file():
            found.append(f"{name}: missing")
        elif sha256(path) != digest and not (built and name in BUILD_DIFFERS):
            found.append(f"{name}: sha256 {sha256(path)}, pinned {digest}")
    return found


def current(folder: Path, pin: Release) -> bool:
    """Tell whether @p folder holds the pinned kernel's files."""
    return not mismatches(folder, pin)


def fetch(folder: Path, pin: Release, *, log: Log = print, opener: Opener | None = None) -> list[str]:
    """Download the pinned release's files into @p folder, each checked by hash; return their names.

    A file that is there with its pinned hash is kept; nothing is written that fails its hash.
    """
    folder.mkdir(parents=True, exist_ok=True)
    log(f">> {pin.repository} {pin.tag} ({pin.commit[:12]})")
    fetched = []
    for name, digest in pin.files.items():
        path = folder / name
        if path.is_file() and sha256(path) == digest:
            log(f"   {name}: there")
            continue
        data = tools.download(pin.url(name), digest, log, *([opener] if opener else []))
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        partial.replace(path)
        fetched.append(name)
    return fetched


def build_argv(step: str, work: str) -> list[str]:
    """Return the command line that runs the headset's kernel build @p step (a script) here, in the build container."""
    return [sys.executable, "-m", "overpressure", "container", "run", "--", "bash", step, work]


def build(step: str, work: str, pin: Release, call: Callable[..., int] = subprocess.call) -> int:
    """Build the pinned commit with @p step in the image the release was built with; return its exit status."""
    env = dict(os.environ, **{IMAGE_OVERRIDE: pin.image})
    return call(build_argv(step, work), env=env, cwd=REPO)


# -- the contracts ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Contract:
    """A name or path the headset uses and the kernel must provide.

    Each side is a list of (file, pattern); every match's first group is a value. The contract holds when the
    headset's values (after @p here_value) are all among the kernel's (after @p kernel_value), and both sides
    found some.
    """

    name: str
    kernel: tuple[tuple[str, str], ...]
    here: tuple[tuple[str, str], ...]
    kernel_value: Callable[[str], str] = field(default=lambda value: value)
    here_value: Callable[[str], str] = field(default=lambda value: value)


def numbered(name: str) -> str:
    """Return a workqueue's name as the kernel formats it: ring0 -> ring%d."""
    return re.sub(r"\d+$", "%d", name)


def values(text: str, pattern: str) -> list[str]:
    """Return the first group of every match of @p pattern in @p text, split where a match lists several."""
    found = []
    for match in re.finditer(pattern, text, re.MULTILINE):
        found += match.group(1).split()
    return found


def local_text(path: str, root: Path) -> str:
    """Return a file under @p root, or every file of a folder, as text."""
    target = root / path
    files = sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else [target]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def source_file(path: str, pin: Release, *, cache: Path, opener: Opener | None = None) -> str:
    """Return the pinned commit's file @p path, kept in @p cache per commit."""
    kept = cache / pin.commit / path
    if not kept.is_file():
        url = pin.source_url(path)
        if opener is None:
            data = web.read(url, 60)
        else:
            with opener(url, timeout=60) as response:  # ty: ignore[invalid-context-manager]  # urlopen's response
                data = response.read()
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
    return kept.read_text(encoding="utf-8", errors="replace")


def check(
    contracts: tuple[Contract, ...], read: Callable[[str], str], root: Path, packages: Callable[[str], str]
) -> list[str]:
    """Return the broken contracts, each as one line that names both sides.

    @p read reads the kernel's files, @p root holds the headset's, @p packages reads overpressure/packages' (the
    paths behind IN_PACKAGES).
    """

    def here_text(path: str) -> str:
        return packages(path.removeprefix(IN_PACKAGES)) if path.startswith(IN_PACKAGES) else local_text(path, root)

    broken = []
    for contract in contracts:
        kernel = {contract.kernel_value(v) for path, pattern in contract.kernel for v in values(read(path), pattern)}
        here = {contract.here_value(v) for path, pattern in contract.here for v in values(here_text(path), pattern)}
        if not here:
            broken.append(f"{contract.name}: this repository's side found nothing ({contract.here[0][0]})")
        elif not kernel:
            broken.append(f"{contract.name}: the kernel's side found nothing ({contract.kernel[0][0]})")
        elif not here <= kernel:
            missing = ", ".join(sorted(here - kernel))
            broken.append(
                f"{contract.name}: this repository uses {missing}; the kernel has {', '.join(sorted(kernel))}"
            )
    return broken
