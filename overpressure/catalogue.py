# SPDX-License-Identifier: Unlicense
"""The headsets overpressure knows, their releases, and one of them downloaded into devices/<codename>/.

Each headset is its own repository (overpressure/<codename>) with its own releases: tags that are calendar
versions (2026.12.0, a beta 2026.12.0b3; docs/releasing.md), each with a source zip and its SHA-256, as
overpressure's own. "headset add"
puts one into devices/<codename>/, checked against its checksum and its headset.json:

  stable    the newest release that is not a beta and that this overpressure runs (the default); never a
            beta, not even while there is no stable release yet: then there is nothing to download
  beta      the newest release this overpressure runs, betas included
  preview   the newest commit of main, or of another branch (--branch NAME): development state, untested on the
            headset, no checksum
  VERSION   a release by its version (--version 2026.12.0b3), also an older one

A development checkout (a Git clone in devices/<codename>/) is never replaced: Git updates it. What
was downloaded is recorded in devices/<codename>/.release.json: its tag, its channel (or that a version was
asked for: pinned) and its zip's SHA-256. "headset update" takes what the recorded channel offers now when
it is newer; a pinned release is kept. identity() names the download for the installer's progress.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import REPO, web
from . import version as own_version
from .headset import MANIFEST, api_problem, manifest, refusal

STABLE, BETA, PREVIEW = "stable", "beta", "preview"
CHANNELS = (STABLE, BETA, PREVIEW)
#: What a download leaves in the headset's folder: which release it is.
RECORD = ".release.json"
#: What devices/<codename> is when it is a Git clone: a development checkout, never replaced.
CHECKOUT = "development checkout"
GITHUB = "https://api.github.com"
#: Where a tag's files are read one by one (its headset.json, before anything is downloaded).
RAW = "https://raw.githubusercontent.com"


@dataclass(frozen=True)
class Known:
    """A headset overpressure can download: its codename, its name, its repository on GitHub."""

    codename: str
    name: str
    repository: str
    soc: str = ""  #: its SoC, for the app's list before anything is downloaded
    year: int = 0  #: the year it came out


#: Every headset there is; a new one is added here once its repository has a release.
CATALOGUE = {
    k.codename: k for k in (Known("phoenix", "PICO 4", "overpressure/phoenix", "Qualcomm Snapdragon XR2", 2022),)
}
#: Headsets in development: their repository has no release yet, so only their preview (main) downloads.
IN_DEVELOPMENT = {
    k.codename: k
    for k in (Known("hollywood", "Meta Quest 2", "overpressure/hollywood", "Qualcomm Snapdragon XR2", 2020),)
}


def lookup(codename: str) -> Known | None:
    """Return the headset CODENAME names, with releases or in development, or None."""
    return CATALOGUE.get(codename) or IN_DEVELOPMENT.get(codename)


@dataclass(frozen=True)
class Release:
    """A downloadable state of a headset: a tagged release, or main (preview, no checksum)."""

    tag: str
    prerelease: bool
    zip_url: str
    sha_url: str = ""
    problem: str = ""  #: why this overpressure cannot run it (its headset.json's api), "" when it can

    @property
    def version(self) -> str:
        """Return the version the tag names (2026.12.0b3; the old v1.0-beta: 1.0-beta), or the tag (main)."""
        return self.tag.removeprefix("v")


def fetch(url: str, timeout: float = 60) -> bytes:
    """Return the body of URL (https only)."""
    if not url.startswith("https://"):
        msg = f"not an https URL: {url}"
        raise ValueError(msg)
    headers = {"User-Agent": "overpressure", "Accept": "*/*"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(GITHUB + "/"):  # CI's own token, for GitHub's API only: its limit without one is low
        headers["Authorization"] = f"Bearer {token}"
    request = web.Request(url, headers=headers)
    return web.read(request, timeout)


def compatibility(known: Known, tag: str, get: Callable[[str], bytes] = fetch) -> str:
    """Return why this overpressure cannot run KNOWN's TAG (its headset.json's api), "" when it can."""
    try:
        data = json.loads(get(f"{RAW}/{known.repository}/{tag}/{MANIFEST}"))
    except (OSError, ValueError):
        return f"its {MANIFEST} could not be read"
    return api_problem(data.get("api") if isinstance(data, dict) else None)


def releases(known: Known, get: Callable[[str], bytes] = fetch) -> list[Release]:
    """Return KNOWN's published releases with a zip and checksum, newest version first, marked runnable or not."""
    found = json.loads(get(f"{GITHUB}/repos/{known.repository}/releases?per_page=100"))
    out = []
    for item in found:
        assets = {a["name"]: a["browser_download_url"] for a in item.get("assets", [])}
        zips = [name for name in assets if name.endswith(".zip") and name + ".sha256" in assets]
        if item.get("draft") or not zips:
            continue
        tag = item["tag_name"]
        zip_url, sha_url = assets[zips[0]], assets[zips[0] + ".sha256"]
        out.append(Release(tag, bool(item.get("prerelease")), zip_url, sha_url, compatibility(known, tag, get)))
    # By version, not by when each was published: a fix to an older line may come out after a newer release.
    return sorted(out, key=lambda r: version_key(r.version) or OLDEST, reverse=True)


def oldest_supported(found: list[Release]) -> Release | None:
    """Return the oldest release of FOUND (newest first) this overpressure runs: what its CI keeps running."""
    return next((r for r in reversed(found) if not r.problem), None)


#: The branch a preview is of unless another is asked for.
MAIN = "main"
#: What a branch's name may be here: Git's rules, narrowed to what a URL takes as it is.
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def branch_problem(branch: str) -> str:
    """Return why @p branch cannot be a branch to download, "" when it can."""
    if not _BRANCH.fullmatch(branch) or ".." in branch or branch.endswith((".lock", "/", ".")) or "//" in branch:
        return f"not a branch name: {branch!r}"
    return ""


def preview(known: Known, branch: str = MAIN) -> Release:
    """Return KNOWN's BRANCH (main unless named) as a download: GitHub's zip of the branch."""
    problem = branch_problem(branch)
    if problem:
        raise ValueError(problem)
    return Release(
        branch, prerelease=True, zip_url=f"https://codeload.github.com/{known.repository}/zip/refs/heads/{branch}"
    )


def branches(known: Known, get: Callable[[str], bytes] = fetch) -> list[str]:
    """Return KNOWN's branches, main first, the others by name."""
    found = json.loads(get(f"{GITHUB}/repos/{known.repository}/branches?per_page=100"))
    names = [str(b.get("name", "")) for b in found if isinstance(b, dict)] if isinstance(found, list) else []
    names = [n for n in names if n and not branch_problem(n)]
    return sorted(names, key=lambda n: (n != MAIN, n.lower()))


def is_preview(record: dict) -> bool:
    """Tell whether a download's RECORD is a preview (a branch), not a release."""
    return record.get("channel") == PREVIEW or record.get("tag") == MAIN


def preview_branch(record: dict) -> str:
    """Return the branch a preview's RECORD was downloaded from (main for those from before branches)."""
    return str(record.get("branch") or MAIN)


def runs_any_beta(found: list[Release]) -> bool:
    """Tell whether FOUND has a beta this overpressure runs."""
    return any(r.prerelease and not r.problem for r in found)


def pick(found: list[Release], channel: str = STABLE, version: str = "") -> Release:
    """Return the release of FOUND that CHANNEL (its newest that runs here) or VERSION names.

    LookupError when there is none, or the one named cannot run here (and why).
    """
    if version:
        chosen = [r for r in found if r.version == version.removeprefix("v")]
        if chosen and chosen[0].problem:
            msg = f"release {chosen[0].version}: {chosen[0].problem}"
            raise LookupError(msg)
    else:
        runs = [r for r in found if not r.problem]
        chosen = [r for r in runs if channel == BETA or not r.prerelease]  # stable is never a beta
    if not chosen:
        wanted = f"release {version}" if version else f"{channel} release this overpressure runs"
        known = ", ".join(r.version + (" (cannot run here)" if r.problem else "") for r in found) or "none"
        hint = "; betas only with --channel beta" if channel == STABLE and not version and runs_any_beta(found) else ""
        msg = f"no {wanted} (published: {known}){hint}"
        raise LookupError(msg)
    return chosen[0]


def record_of(codename: str, root: Path = REPO) -> dict:
    """Return what the download into devices/CODENAME recorded (.release.json), {} without one."""
    try:
        data = json.loads((root / "devices" / codename / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def installed(codename: str, root: Path = REPO) -> str:
    """Return what devices/CODENAME holds, in words: its release, a checkout, or "" for nothing."""
    folder = root / "devices" / codename
    if (folder / ".git").exists():
        return CHECKOUT
    record = record_of(codename, root)
    if not record:
        found = manifest(folder).get("version")
        return f"release {found}" if found else ""
    tag = str(record.get("tag", ""))
    if is_preview(record):
        branch, words = preview_branch(record), preview_words(codename, root)
        if branch != MAIN:
            return f"branch {branch}" + (f": {words}" if words else "")
        return f"preview {words}" if words else "preview (main)"
    return f"release {tag.removeprefix('v')}"


def preview_words(codename: str, root: Path = REPO) -> str:
    """Return a downloaded preview in words, from its headset.json: "2026.12.0 preview from 3 December 2026 (4d8f977)".

    "" when devices/CODENAME is no preview or its headset.json names no development version.
    """
    folder = root / "devices" / codename
    data = manifest(folder)
    found = str(data.get("version", ""))
    parsed = own_version.parse(found)
    if not is_preview(record_of(codename, root)) or parsed is None or parsed.dev is None:
        return ""
    when = own_version.committed_at(str(data.get("date", "")))
    commit = str(data.get("commit", ""))
    return own_version.preview_words(found, when, "" if commit.startswith("$") else commit)


def identity(codename: str, root: Path = REPO) -> str:
    """Return what the installer's progress records of devices/CODENAME: its download (tag and zip), or the checkout.

    Another release, or a new preview of main, changes it; a development checkout is one, whatever its commit.
    """
    if (root / "devices" / codename / ".git").exists():
        return CHECKOUT
    record = record_of(codename, root)
    if record:
        return f"{record.get('tag', '')} {str(record.get('sha256', ''))[:16]}"
    return installed(codename, root)


#: Where a release without a version sorts: after every one with a version, read newest first.
OLDEST: own_version.Key = ((-1,), (0, 0), 0.0)


def version_key(version: str) -> own_version.Key | None:
    """Return a release version (2026.12.0b3, the old 1.0-beta.2) as a key that sorts it among the others, or None.

    The calendar versions sort after the releases before the switch (overpressure/version.py).
    """
    return own_version.key(version)


def update(codename: str, root: Path = REPO, get: Callable[[str], bytes] = fetch) -> Release | None:
    """Return what devices/CODENAME's recorded channel offers now when it is newer, else None.

    None too for a development checkout, a pinned release (a version was asked for) and nothing recorded.
    A preview is newer when its branch (main unless another was asked for) has another commit than the one
    downloaded.
    """
    record, known = record_of(codename, root), lookup(codename)
    if known is None or not record or record.get("pinned") or (root / "devices" / codename / ".git").exists():
        return None
    channel = str(record.get("channel", STABLE))
    if is_preview(record):
        branch = preview_branch(record)
        head = json.loads(get(f"{GITHUB}/repos/{known.repository}/commits/{branch}")).get("sha", "")
        have = str(manifest(root / "devices" / codename).get("commit", ""))
        return preview(known, branch) if head and head != have else None
    try:
        newest = pick(releases(known, get), channel)
    except LookupError:
        return None
    want, have = version_key(newest.version), version_key(str(record.get("tag", "")))
    return newest if want is not None and (have is None or want > have) else None


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


def _replace(folder: Path, data: bytes, record: dict) -> None:
    """Unpack the zip DATA as FOLDER with RECORD, checked first; what was there stays if anything fails."""
    codename = folder.name
    fresh, old = folder.with_name(f".{codename}.new"), folder.with_name(f".{codename}.old")
    for stale in (fresh, old):
        shutil.rmtree(stale, ignore_errors=True)
    fresh.mkdir(parents=True)
    try:
        _extract(data, fresh)
        refused = refusal(codename, manifest(fresh))
        if refused:
            raise RuntimeError(refused)
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


def add(  # noqa: PLR0913  # what to download, from where, into where
    codename: str,
    *,
    channel: str = STABLE,
    version: str = "",
    branch: str = "",
    root: Path = REPO,
    get: Callable[[str], bytes] = fetch,
    log: Callable[[str], object] = print,
) -> Release:
    """Download CODENAME's release (CHANNEL, else VERSION) or BRANCH into ROOT/devices/CODENAME; return it.

    A BRANCH is a preview of that branch (the preview channel is main's). The zip is checked against its SHA-256
    and its headset.json against this overpressure's interface before it replaces what was there; a development
    checkout is refused. A headset in development has no releases: only previews.
    """
    known = lookup(codename)
    if known is None:
        msg = f"no headset {codename!r} (known: {', '.join(sorted(CATALOGUE | IN_DEVELOPMENT))})"
        raise LookupError(msg)
    if branch:
        channel, version = PREVIEW, ""
    if codename in IN_DEVELOPMENT and (channel != PREVIEW or version):
        msg = f"the {known.name} is in development: it has no releases yet, only its preview (--channel preview)"
        raise LookupError(msg)
    folder = root / "devices" / codename
    if (folder / ".git").exists():
        msg = f"devices/{codename} is a development checkout: update it with git, it is not replaced"
        raise RuntimeError(msg)
    preview_of = branch or MAIN
    if channel == PREVIEW and not version:
        release = preview(known, preview_of)
    else:
        release = pick(releases(known, get), channel, version)
    log(f"downloading {known.name} {release.version} from {known.repository}")
    data = get(release.zip_url)
    digest = hashlib.sha256(data).hexdigest()
    if release.sha_url:
        expected = get(release.sha_url).decode("ascii", "replace").split()[:1]
        if expected != [digest]:
            msg = f"{release.zip_url}: SHA-256 {digest} is not the published {expected[0] if expected else 'none'}"
            raise RuntimeError(msg)
    record = {"tag": release.tag, "channel": channel, "sha256": digest}
    if channel == PREVIEW and not version:
        record["branch"] = preview_of
    if version:
        record["pinned"] = True  # asked for by version: kept until another is asked for
    _replace(folder, data, record)
    log(f"devices/{codename}: {installed(codename, root)}")
    return release


USAGE = """usage: python -m overpressure headset list | releases CODENAME | update CODENAME
       python -m overpressure headset add CODENAME [--channel stable|beta|preview | --version V | --branch NAME]

  list                 every headset overpressure knows, what devices/ holds of it, and its update
  releases CODENAME    its published releases, newest first, and whether this overpressure runs each
  add CODENAME         download it into devices/CODENAME: the newest stable release (never a beta: none
                       while there is no stable release), a channel's newest (beta: betas too, preview:
                       main, untested), a release by version (pinned: update keeps it), or a branch's newest
                       commit (a preview of it, untested); a headset in development has only previews
  update CODENAME      download what its recorded channel offers now, when it is newer"""


def _list(root: Path, get: Callable[[str], bytes]) -> int:
    for known in (*CATALOGUE.values(), *IN_DEVELOPMENT.values()):
        here = installed(known.codename, root) or "not downloaded"
        line = f"{known.codename:<12} {known.name:<12} {here}"
        line += ", in development: preview only" if known.codename in IN_DEVELOPMENT else ""
        with contextlib.suppress(LookupError, ValueError, OSError):  # offline: what is here, without updates
            newer = update(known.codename, root, get)
            line += f", update: {newer.version}" if newer else ""
        print(line)
    return 0


def _releases(codename: str, get: Callable[[str], bytes], *, oldest: bool) -> int:
    known = lookup(codename)
    found = releases(known, get) if codename in CATALOGUE and known else []
    if oldest:  # for CI: the tag of the oldest release this overpressure keeps running
        first = oldest_supported(found)
        print(first.tag if first else "")
        return 0
    for release in found:
        kind = "beta" if release.prerelease else "stable"
        print(f"{release.version:<16} {kind:<7} {release.problem or 'runs here'}")
    return 0


def _update(codename: str, root: Path, get: Callable[[str], bytes]) -> int:
    if update(codename, root, get) is None:
        print(f"devices/{codename}: {installed(codename, root) or 'nothing'}, nothing newer")
        return 0
    record = record_of(codename, root)
    branch = preview_branch(record) if is_preview(record) else ""
    add(codename, channel=str(record.get("channel", STABLE)), branch=branch, root=root, get=get)
    return 0


def main(argv: list[str], root: Path = REPO, get: Callable[[str], bytes] = fetch) -> int:
    """Run the headset command."""
    if argv[:1] == ["list"]:
        return _list(root, get)
    parser = argparse.ArgumentParser(prog="python -m overpressure headset", usage=USAGE)
    parser.add_argument("action", choices=("releases", "add", "update"))
    parser.add_argument("codename", choices=sorted(CATALOGUE | IN_DEVELOPMENT))
    parser.add_argument("--channel", choices=CHANNELS, default=STABLE)
    parser.add_argument("--version", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--oldest-supported", action="store_true", help=argparse.SUPPRESS)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    args = parser.parse_args(argv)
    try:
        if args.action == "releases":
            return _releases(args.codename, get, oldest=args.oldest_supported)
        if args.action == "update":
            return _update(args.codename, root, get)
        add(args.codename, channel=args.channel, version=args.version, branch=args.branch, root=root, get=get)
    except (LookupError, RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
