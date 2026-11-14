# SPDX-License-Identifier: Unlicense
"""The headsets overpressure knows, their releases, and one of them downloaded into devices/<codename>/.

Each headset is its own repository (overpressure/<codename>) with its own releases: tags
vMAJOR.MINOR[-beta[.N]], each with a source zip and its SHA-256, as overpressure's own. "headset add"
puts one into devices/<codename>/, checked against its checksum and its headset.json:

  stable    the newest release that is not a beta (the default)
  beta      the newest release, betas included
  preview   the newest commit of main: development state, untested on the headset, no checksum
  VERSION   a release by its version (--version 1.0-beta), also an older one

A development checkout (a Git clone in devices/<codename>/) is never replaced: Git updates it. What
was downloaded is recorded in devices/<codename>/.release.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import REPO
from .headset import manifest, refusal

STABLE, BETA, PREVIEW = "stable", "beta", "preview"
CHANNELS = (STABLE, BETA, PREVIEW)
#: What a download leaves in the headset's folder: which release it is.
RECORD = ".release.json"
GITHUB = "https://api.github.com"


@dataclass(frozen=True)
class Known:
    """A headset overpressure can download: its codename, its name, its repository on GitHub."""

    codename: str
    name: str
    repository: str


#: Every headset there is; a new one is added here once its repository has a release.
CATALOGUE = {k.codename: k for k in (Known("phoenix", "PICO 4", "overpressure/phoenix"),)}


@dataclass(frozen=True)
class Release:
    """A downloadable state of a headset: a tagged release, or main (preview, no checksum)."""

    tag: str
    prerelease: bool
    zip_url: str
    sha_url: str = ""

    @property
    def version(self) -> str:
        """Return the version the tag names (v1.0-beta: 1.0-beta), or the tag (main)."""
        return self.tag.removeprefix("v")


def fetch(url: str, timeout: float = 60) -> bytes:
    """Return the body of URL (https only)."""
    if not url.startswith("https://"):
        msg = f"not an https URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": "overpressure", "Accept": "*/*"})  # noqa: S310  # https only
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  # https only, above
        return response.read()


def releases(known: Known, get: Callable[[str], bytes] = fetch) -> list[Release]:
    """Return KNOWN's published releases with a zip and its checksum, newest first."""
    found = json.loads(get(f"{GITHUB}/repos/{known.repository}/releases?per_page=100"))
    out = []
    for item in found:
        assets = {a["name"]: a["browser_download_url"] for a in item.get("assets", [])}
        zips = [name for name in assets if name.endswith(".zip") and name + ".sha256" in assets]
        if item.get("draft") or not zips:
            continue
        out.append(
            Release(item["tag_name"], bool(item.get("prerelease")), assets[zips[0]], assets[zips[0] + ".sha256"])
        )
    return out


def preview(known: Known) -> Release:
    """Return KNOWN's main as a download: GitHub's zip of the branch."""
    return Release(
        "main", prerelease=True, zip_url=f"https://codeload.github.com/{known.repository}/zip/refs/heads/main"
    )


def pick(found: list[Release], channel: str = STABLE, version: str = "") -> Release:
    """Return the release of FOUND that CHANNEL or VERSION names; LookupError when there is none."""
    if version:
        chosen = [r for r in found if r.version == version.removeprefix("v")]
    else:
        chosen = [r for r in found if channel == BETA or not r.prerelease]
    if not chosen:
        wanted = f"release {version}" if version else f"{channel} release"
        known = ", ".join(r.version for r in found) or "none"
        msg = f"no {wanted} (published: {known})"
        raise LookupError(msg)
    return chosen[0]


def installed(codename: str, root: Path = REPO) -> str:
    """Return what devices/CODENAME holds, in words: its release, a checkout, or "" for nothing."""
    folder = root / "devices" / codename
    if (folder / ".git").exists():
        return "development checkout"
    try:
        record = json.loads((folder / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        version = manifest(folder).get("version")
        return f"release {version}" if version else ""
    tag = record.get("tag", "")
    return "preview (main)" if tag == "main" else f"release {tag.removeprefix('v')}"


def _extract(data: bytes, target: Path) -> None:
    """Unpack the zip DATA into TARGET without its one top folder, keeping the programs' execute bits."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            parts = PurePosixPath(info.filename).parts[1:]  # the archive's top folder (phoenix-1.0-beta/)
            if not parts or info.is_dir():
                continue
            if any(p in ("..", "") for p in parts) or info.filename.startswith("/"):
                msg = f"unsafe path in the archive: {info.filename}"
                raise ValueError(msg)
            path = target.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(info))
            if (info.external_attr >> 16) & 0o111:
                path.chmod(0o755)


def add(  # noqa: PLR0913  # what to download, from where, into where
    codename: str,
    *,
    channel: str = STABLE,
    version: str = "",
    root: Path = REPO,
    get: Callable[[str], bytes] = fetch,
    log: Callable[[str], object] = print,
) -> Release:
    """Download CODENAME's release (CHANNEL, else VERSION) into ROOT/devices/CODENAME; return it.

    The zip is checked against its SHA-256 and its headset.json against this overpressure's
    interface before it replaces what was there; a development checkout is refused.
    """
    known = CATALOGUE.get(codename)
    if known is None:
        msg = f"no headset {codename!r} (known: {', '.join(sorted(CATALOGUE))})"
        raise LookupError(msg)
    folder = root / "devices" / codename
    if (folder / ".git").exists():
        msg = f"devices/{codename} is a development checkout: update it with git, it is not replaced"
        raise RuntimeError(msg)
    release = preview(known) if channel == PREVIEW and not version else pick(releases(known, get), channel, version)
    log(f"downloading {known.name} {release.version} from {known.repository}")
    data = get(release.zip_url)
    digest = hashlib.sha256(data).hexdigest()
    if release.sha_url:
        expected = get(release.sha_url).decode("ascii", "replace").split()[:1]
        if expected != [digest]:
            msg = f"{release.zip_url}: SHA-256 {digest} is not the published {expected[0] if expected else 'none'}"
            raise RuntimeError(msg)
    fresh, old = folder.with_name(f".{codename}.new"), folder.with_name(f".{codename}.old")
    for stale in (fresh, old):
        shutil.rmtree(stale, ignore_errors=True)
    fresh.mkdir(parents=True)
    try:
        _extract(data, fresh)
        refused = refusal(codename, manifest(fresh))
        if refused:
            raise RuntimeError(refused)
        record = {"tag": release.tag, "channel": PREVIEW if release.tag == "main" else channel, "sha256": digest}
        (fresh / RECORD).write_bytes((json.dumps(record, indent=1) + "\n").encode("utf-8"))
        if folder.exists():
            folder.rename(old)
        try:
            fresh.rename(folder)
        except OSError:
            if old.exists():
                old.rename(folder)  # the headset it had, back
            raise
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    log(f"devices/{codename}: {installed(codename, root)}")
    return release


USAGE = """usage: python -m overpressure headset list | releases CODENAME
       python -m overpressure headset add CODENAME [--channel stable|beta|preview | --version V]

  list                 every headset overpressure knows, and what devices/ holds of it
  releases CODENAME    its published releases, newest first
  add CODENAME         download it into devices/CODENAME: the newest stable release, a channel's
                       newest (beta: betas too, preview: main, untested), or a release by version"""


def main(argv: list[str], root: Path = REPO, get: Callable[[str], bytes] = fetch) -> int:
    """Run the headset command."""
    if argv[:1] == ["list"]:
        for known in CATALOGUE.values():
            print(f"{known.codename:<12} {known.name:<12} {installed(known.codename, root) or 'not downloaded'}")
        return 0
    parser = argparse.ArgumentParser(prog="python -m overpressure headset", usage=USAGE)
    parser.add_argument("action", choices=("releases", "add"))
    parser.add_argument("codename", choices=sorted(CATALOGUE))
    parser.add_argument("--channel", choices=CHANNELS, default=STABLE)
    parser.add_argument("--version", default="")
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    args = parser.parse_args(argv)
    try:
        if args.action == "releases":
            for release in releases(CATALOGUE[args.codename], get):
                print(f"{release.version:<16} {'beta' if release.prerelease else 'stable'}")
            return 0
        add(args.codename, channel=args.channel, version=args.version, root=root, get=get)
    except (LookupError, RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
