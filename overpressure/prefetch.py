# SPDX-License-Identifier: Unlicense
"""A headset's downloads, on this computer before an install writes the headset.

A headset that builds its own system (overpressure's build/build-on-headset.sh) would download what it needs while
it builds, after its maintenance system replaced the stock one: a connection that dropped then stopped the install
halfway. Prefetch downloads all of it first, here, where a failure changes nothing on the headset: the pinned
commit of overpressure/packages, its release's packages, Holo's rootfs for the build host with the build host's
packages, and Valve's package databases with every upstream package the headset's packages.lock names (and
protobuf 25, whose libraries adb takes). The files lie as the headset's build folder keeps them (BUILD paths), so
an install sends them as they are and the build finds each in the cache it already checks: it downloads none again.

Each file is checked: a package by the lock's or the release's SHA-256 (and the databases' for protobuf and the
build host's packages), the rootfs by packages.sh's pin, SHA256SUMS by the headset's packages.json. A file that is
there and whole is not downloaded again; a failed download stops here and the next run fetches only the rest.
Files of an earlier pin are removed. Since interface 1.7.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import http.client
import io
import json
import re
import tarfile
import time
import urllib.request
from typing import IO, TYPE_CHECKING, Callable

from . import progress, web
from .pacman import Database, Download, closure, latest, lock, repositories

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

#: The pin's files in a cache, one path per line: what an install sends.
MANIFEST = "manifest.txt"
#: The upstream packages, beside build-userspace.sh's environment, which a new environment's unpacking would empty:
#: build-userspace.sh links them into its package cache (its --cachedir).
PACKAGE_CACHE = "root/downloads/packages"
#: The image's package databases, for build-userspace.sh --databases.
DATABASES = "root/downloads/databases"
#: The build host's databases, packages and repository, for build-on-headset.sh.
HOST = "downloads/host"
CHUNK = 1 << 20
#: Downloads at once, as pacman.conf's ParallelDownloads.
PARALLEL = 4
#: A body that breaks off is tried again for as long as overpressure's downloads try (web.RETRY).
RETRY_SECONDS = 120

Log = Callable[[str], None]
Opener = Callable[..., IO[bytes]]


def local(path: str) -> str:
    """Return the name this computer keeps @p path under: a ":" (a package's epoch) is no file name's on Windows."""
    return path.replace(":", "%3A")


def shell_value(script: str, name: str) -> str:
    """Return the value packages.sh gives NAME (NAME=value), with $HOLO_SNAPSHOT filled in."""
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(\S+)$", script, re.MULTILINE))
    value = values.get(name, "").replace("$HOLO_SNAPSHOT", values.get("HOLO_SNAPSHOT", ""))
    if not value:
        msg = f"the pinned build/packages.sh gives no {name}"
        raise ValueError(msg)
    return value


class Prefetch:
    """A headset's downloads for its pins into @p cache, through @p opener (web's, the tests' fake).

    @p codename names its folder in overpressure/packages (headsets/CODENAME); @p pin is its packages.json,
    @p build_host its build-host.json (the build host's repository for the rootfs the pin builds in); @p extra are
    files of the pinned commit the headset's build takes too: (path in the build folder, path in the commit), each
    with {commit} filled in.
    """

    def __init__(  # noqa: PLR0913  # the headset's pins and where things are, keyword-only
        self,
        *,
        codename: str,
        cache: Path,
        pin: Path,
        build_host: Path,
        extra: Sequence[tuple[str, str]] = (),
        opener: Opener = web.open_once,
        log: Log = print,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Keep where the files go and what they come from."""
        self.codename = codename
        self.cache = cache
        self.pin = json.loads(pin.read_text(encoding="utf-8"))
        self.build_host = json.loads(build_host.read_text(encoding="utf-8"))
        self.extra = tuple(extra)
        self.opener = opener
        self.log = log
        self.retry = web.Retry(seconds=RETRY_SECONDS, log=log, sleep=sleep, clock=clock)
        #: The small files this run fetched or found: the pin's besides the plan's.
        self.kept: set[str] = set()

    # -- the small files, kept per pin -------------------------------------------------------------
    def small(self, path: str, url: str) -> bytes:
        """Return the file @p path, downloaded from @p url unless it is there."""
        target = self.cache / local(path)
        if not target.is_file():
            data = web.read(url, 60, self.retry, self.opener)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.kept.add(path)
        return target.read_bytes()

    def archive_file(self, archive: bytes, path: str) -> str:
        """Return the file @p path of GitHub's archive of the pinned commit."""
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                if member.name.split("/", 1)[-1] == path:
                    handle = tar.extractfile(member)
                    if handle is not None:
                        return handle.read().decode("utf-8")
        msg = f"the pinned commit of {self.pin['repository']} has no {path}"
        raise ValueError(msg)

    def plan(self) -> list[Download]:
        """Return every file the headset's build would download for the pin, the small ones fetched already."""
        repository, tag, commit = self.pin["repository"], self.pin["tag"], self.pin["commit"]
        raw = f"https://raw.githubusercontent.com/{repository}/{commit}"
        setup = self.small(f"downloads/packages-{commit}.sh", f"{raw}/build/packages.sh").decode("utf-8")
        for path, source in self.extra:
            self.small(path.format(commit=commit), f"{raw}/{source}")
        archive = self.small(
            f"cache/packages/{commit}.tar.gz", f"https://github.com/{repository}/archive/{commit}.tar.gz"
        )
        pacman_conf = self.archive_file(archive, f"headsets/{self.codename}/pacman.conf")
        locked = lock(self.archive_file(archive, f"headsets/{self.codename}/packages.lock"))

        release = f"https://github.com/{repository}/releases/download/{tag}"
        sums = self.small(f"cache/packages-release/{tag}/SHA256SUMS", f"{release}/SHA256SUMS")
        if hashlib.sha256(sums).hexdigest() != self.pin["sha256sums"]:
            (self.cache / local(f"cache/packages-release/{tag}/SHA256SUMS")).unlink()
            msg = f"{repository} {tag}'s SHA256SUMS is not the one userspace/packages.json pins"
            raise ValueError(msg)
        plan = [
            Download(f"cache/packages-release/{tag}/{name}", f"{release}/{name}", sum_)
            for sum_, name in lock(sums.decode("utf-8"))
            if name.endswith(".pkg.tar.zst") or (name.startswith("overpressure-") and name.endswith(".db"))
        ]

        snapshot, rootfs = shell_value(setup, "HOLO_SNAPSHOT"), shell_value(setup, "HOLO_SHA256")
        plan.append(Download(f"root/packages/holo-{snapshot}.rootfs.zst", shell_value(setup, "HOLO_URL"), rootfs))

        databases = [
            Database.parse(
                name, server, self.small(f"{DATABASES}/{name}.db", Database(name, server, {}).url(f"{name}.db"))
            )
            for name, server in repositories(pacman_conf)
        ]
        files: dict[str, tuple[Database, dict[str, list[str]]]] = {}
        for database in reversed(databases):  # pacman takes the first repository that has a package
            files.update({file: (database, fields) for file, fields in database.by_file().items()})
        for sum_, name in locked:
            if name not in files:
                msg = f"packages.lock names {name}, which Valve's repositories no longer publish"
                raise ValueError(msg)
            database, fields = files[name]
            item = database.download(fields, PACKAGE_CACHE)
            plan.append(Download(item.path, item.url, sum_, item.size))
        hotfixes = [d for d in databases if d.name.startswith("deckard-")]
        if hotfixes:  # adb's protobuf 25 libraries (build-userspace.sh), outside the lock
            plan.append(hotfixes[0].download(latest(hotfixes[0], "protobuf", "25."), PACKAGE_CACHE))

        if self.build_host["rootfs"] == rootfs:
            server = self.build_host["server"]
            self.write(f"{HOST}/server", server.encode("utf-8") + b"\n")
            host = [
                Database.parse(
                    name, server, self.small(f"{HOST}/sync/{name}.db", Database(name, server, {}).url(f"{name}.db"))
                )
                for name in self.build_host["repositories"]
            ]
            plan += [
                database.download(fields, f"{HOST}/pkg")
                for database, fields in closure(self.build_host["packages"], host)
            ]
        else:
            self.log("the build host's repository is pinned for another rootfs: the headset downloads its python")
        return plan

    def write(self, path: str, data: bytes) -> None:
        """Write @p data to @p path unless it holds that already."""
        target = self.cache / local(path)
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.kept.add(path)

    # -- the large files ---------------------------------------------------------------------------------
    def whole(self, item: Download) -> bool:
        """Tell whether @p item is there with its SHA-256."""
        target = self.cache / local(item.path)
        if not target.is_file():
            return False
        if not item.sha256:
            return True
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest() == item.sha256

    def fetch(self, item: Download, meter: progress.Meter) -> None:
        """Download @p item through a .part file, checked; a body that breaks off is tried again."""
        target = self.cache / local(item.path)
        part = target.with_name(target.name + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        end = self.retry.clock() + self.retry.seconds
        wait = 2.0
        while True:
            digest, written = hashlib.sha256(), 0
            try:
                with web.urlopen(item.url, 120, self.retry, self.opener) as response, part.open("wb") as sink:
                    for chunk in iter(lambda: response.read(CHUNK), b""):  # the response of this try
                        sink.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        meter.add(len(chunk))
                break
            except web.DownloadError:
                part.unlink(missing_ok=True)
                raise
            except (OSError, http.client.HTTPException) as exc:
                meter.add(-written)
                if not web.transient(exc) or self.retry.clock() + wait > end:
                    part.unlink(missing_ok=True)
                    msg = f"{item.url}: {web.describe(exc)}"
                    raise web.DownloadError(msg) from exc
                self.log(f"   {item.url}: {web.describe(exc)}, again in {wait:.0f} s")
                self.retry.sleep(wait)
                wait = min(wait * 2, 30.0)
        if item.sha256 and digest.hexdigest() != item.sha256:
            part.unlink(missing_ok=True)
            msg = f"{item.url} is not the pinned file (SHA-256 {digest.hexdigest()})"
            raise web.DownloadError(msg)
        part.replace(target)

    def sized(self, items: list[Download]) -> list[Download]:
        """Return @p items, each of unknown size with its Content-Length (a HEAD request), for the progress."""

        def size(item: Download) -> Download:
            if item.size:
                return item
            request = urllib.request.Request(item.url, method="HEAD")  # noqa: S310  # https only, from the pins
            with web.urlopen(request, 60, self.retry, self.opener) as response:
                length = int(getattr(response, "headers", {}).get("Content-Length") or 0)
            return Download(item.path, item.url, item.sha256, length)

        with concurrent.futures.ThreadPoolExecutor(PARALLEL) as pool:
            return list(pool.map(size, items))

    def run(self) -> list[str]:
        """Download what is missing; return the pin's files (the manifest), the cache rid of others."""
        plan = self.plan()
        missing = self.sized([item for item in plan if not self.whole(item)])
        total = sum(item.size for item in missing)
        if missing:
            self.log(f"downloading {len(missing)} of {len(plan)} files for the headset ({total >> 20} MiB)")
            meter = progress.Meter(
                total,
                lambda done, all_: f"downloading the headset's packages: {progress.mib(done, all_)}",
                out=self.log,
            )
            with concurrent.futures.ThreadPoolExecutor(PARALLEL) as pool:
                for future in [pool.submit(self.fetch, item, meter) for item in missing]:
                    future.result()
        manifest = sorted({item.path for item in plan} | self.kept)
        self.prune({local(path) for path in manifest})
        self.write(MANIFEST, "".join(f"{path}\n" for path in manifest).encode("utf-8"))
        size = sum((self.cache / local(path)).stat().st_size for path in manifest)
        self.log(f"{len(manifest)} files for the headset ({size >> 20} MiB) in {self.cache}")
        return manifest

    def prune(self, keep: set[str]) -> None:
        """Remove the files of earlier pins, and parts of downloads that stopped."""
        for path in sorted(self.cache.rglob("*"), reverse=True):
            relative = path.relative_to(self.cache).as_posix()
            if path.is_file() and relative != MANIFEST and relative not in keep:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()


def manifest(cache: Path) -> list[str]:
    """Return the files the last prefetch left for the pin, or none: what an install sends."""
    try:
        listed = (cache / MANIFEST).read_text(encoding="utf-8").split()
    except OSError:
        return []
    return listed if all((cache / local(path)).is_file() for path in listed) else []
