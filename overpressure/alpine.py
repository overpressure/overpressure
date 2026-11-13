# SPDX-License-Identifier: Unlicense
"""Fetch Alpine Linux aarch64 packages without apk-tools.

* downloads APKINDEX files and the minirootfs from the Alpine CDN (HTTPS),
* verifies each APKINDEX's RSA signature against Alpine's pinned signing keys
  (:mod:`overpressure.apksign`),
* resolves dependencies against the packages already in the minirootfs,
* verifies every package through Alpine's checksum chain
  (APKINDEX "C:" -> SHA-1 of the control segment -> "datahash" SHA-256 of the
  data segment), and
* unpacks package payloads into an in-memory :class:`overpressure.cpio.Tree`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import tarfile
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from . import apksign

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .cpio import Tree

MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
USER_AGENT = "overpressure-build/1.0"
#: An .apk is three gzip streams: the signature, the control and the data segment.
APK_SEGMENTS = 3


@dataclass
class Package:
    """A package as an APKINDEX or the installed database records it."""

    name: str
    version: str
    arch: str
    checksum: str  # "Q1" + base64(sha1(control segment))
    depends: list[str]
    provides: list[str]
    repo_url: str = ""
    provider_priority: int = 0

    @property
    def filename(self) -> str:
        """Return the package file's name in its repository."""
        return f"{self.name}-{self.version}.apk"


@dataclass
class Index:
    """A repository index: packages by name, and the packages providing each name."""

    packages: dict[str, Package] = field(default_factory=dict)
    providers: dict[str, list[Package]] = field(default_factory=dict)

    def add(self, pkg: Package) -> None:
        """Add PKG unless an earlier repository has it."""
        cur = self.packages.get(pkg.name)
        if cur is not None:
            return  # first repository wins (main before community)
        self.packages[pkg.name] = pkg
        for token in [pkg.name, *pkg.provides]:
            self.providers.setdefault(_dep_name(token), []).append(pkg)


def _dep_name(token: str) -> str:
    return re.split(r"[<>=~]", token, maxsplit=1)[0]


def _download(url: str, dest: str, log: Callable[[str], None] = print, *, fresh: bool = False) -> str:
    target = Path(dest)
    if target.exists() and not fresh:
        return dest
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310  # URLs below MIRROR, https
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310  # URLs below MIRROR, https
        data = resp.read()
    tmp = Path(dest + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    return dest


def parse_records(text: str) -> list[dict[str, str]]:
    """Return the records of an APKINDEX or installed database, each a dict by field letter."""
    records, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                records.append(cur)
                cur = {}
            continue
        key, _, value = line.partition(":")
        if key in cur and key in ("D", "p"):
            cur[key] += " " + value
        else:
            cur[key] = value
    if cur:
        records.append(cur)
    return records


def _record_to_package(rec: dict[str, str], repo_url: str = "") -> Package:
    return Package(
        name=rec.get("P", ""),
        version=rec.get("V", ""),
        arch=rec.get("A", ""),
        checksum=rec.get("C", ""),
        depends=rec.get("D", "").split(),
        provides=rec.get("p", "").split(),
        repo_url=repo_url,
        provider_priority=int(rec.get("k", 0) or 0),
    )


def load_index(cache: str, branch: str, repos: list[str], arch: str, log: Callable[[str], None] = print) -> Index:
    """Download the repositories' APKINDEX files, verify their signatures and read them.

    Always afresh: the branch's packages move, and the mirror drops the versions an older index names.
    """
    index = Index()
    keys = apksign.alpine_keys(arch)
    for repo in repos:
        url = f"{MIRROR}/{branch}/{repo}/{arch}"
        index_path = str(Path(cache, branch, repo, arch, "APKINDEX.tar.gz"))
        path = Path(_download(f"{url}/APKINDEX.tar.gz", index_path, log, fresh=True))
        raw = path.read_bytes()
        what = f"{branch}/{repo}/{arch}/APKINDEX.tar.gz"
        try:
            key = apksign.verify_index(raw, keys, what)
        except apksign.SignatureError:
            path.unlink()  # never keep an unverified index in the cache
            raise
        log(f"  {what}: signature by {key} verified")
        # Read APKINDEX only from the signed second stream, never from the signature stream.
        _signature, (_control, content) = gzip_members(raw)
        text = ""
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as tf:
            for m in _safe_members(tf):
                if m.name == "APKINDEX":
                    text = _member_bytes(tf, m).decode()
        for rec in parse_records(text):
            index.add(_record_to_package(rec, url))
    return index


def installed_db(tree: Tree) -> list[Package]:
    """Return the packages installed in TREE, from apk's database."""
    node = tree.get("lib/apk/db/installed")
    if node is None:
        return []
    return [_record_to_package(r) for r in parse_records(node.data.decode())]


def resolve(index: Index, wanted: list[str], installed: list[Package]) -> list[Package]:
    """Return the packages (deps first) needed for ``wanted`` on top of ``installed``."""
    satisfied: set[str] = set()
    for pkg in installed:
        satisfied.add(pkg.name)
        satisfied.update(_dep_name(t) for t in pkg.provides)
    order: list[Package] = []
    seen: set[str] = set()

    def visit(token: str, chain: tuple[str, ...]) -> None:
        if token.startswith("!"):
            return
        name = _dep_name(token)
        if name in satisfied or name in seen:
            return
        cands = index.providers.get(name)
        if not cands:
            msg = f"nothing provides {token!r} (needed by {' -> '.join(chain) or 'request'})"
            raise KeyError(msg)
        exact = [p for p in cands if p.name == name]
        pkg = exact[0] if exact else max(cands, key=lambda p: p.provider_priority)
        if pkg.name in seen:
            return
        seen.add(pkg.name)
        seen.add(name)
        for dep in pkg.depends:
            visit(dep, (*chain, pkg.name))
        satisfied.add(pkg.name)
        satisfied.update(_dep_name(t) for t in pkg.provides)
        order.append(pkg)

    for w in wanted:
        visit(w, ())
    return order


def gzip_members(raw: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield (compressed_bytes, decompressed_bytes) for each gzip member."""
    pos = 0
    while pos < len(raw):
        d = zlib.decompressobj(wbits=31)
        out = d.decompress(raw[pos:])
        end = len(raw) - len(d.unused_data)
        if end <= pos:
            break
        yield raw[pos:end], out
        pos = end


def fetch_package(pkg: Package, cache: str, log: Callable[[str], None] = print) -> bytes:
    """Download and verify a package; return the decompressed data tar."""
    dest = Path(cache, "apk", pkg.arch, pkg.filename)
    _download(f"{pkg.repo_url}/{pkg.filename}", str(dest), log)
    raw = dest.read_bytes()
    members = list(gzip_members(raw))
    if len(members) != APK_SEGMENTS:
        msg = f"{pkg.filename}: expected 3 gzip segments, found {len(members)}"
        raise ValueError(msg)
    (_sig, _), (control, control_tar), (data, data_tar) = members
    expect = pkg.checksum
    if expect.startswith("Q1"):
        got = "Q1" + base64.b64encode(hashlib.sha1(control).digest()).decode()  # noqa: S324  # Alpine's "C:" checksum
        if got != expect:
            dest.unlink()
            msg = f"{pkg.filename}: control checksum mismatch ({got} != {expect})"
            raise ValueError(msg)
    pkginfo = ""
    with tarfile.open(fileobj=io.BytesIO(control_tar), mode="r:") as tf:
        for m in _safe_members(tf):
            if m.name == ".PKGINFO":
                pkginfo = _member_bytes(tf, m).decode()
    datahash = re.search(r"^datahash = ([0-9a-f]{64})$", pkginfo, re.MULTILINE)
    if not datahash or hashlib.sha256(data).hexdigest() != datahash.group(1):
        dest.unlink()
        msg = f"{pkg.filename}: data segment hash mismatch"
        raise ValueError(msg)
    return data_tar


def _member_bytes(tf: tarfile.TarFile, m: tarfile.TarInfo) -> bytes:
    reader = tf.extractfile(m)
    if reader is None:
        msg = f"{m.name}: not a file in the archive"
        raise ValueError(msg)
    return reader.read()


def _safe_members(tf: tarfile.TarFile) -> Iterator[tarfile.TarInfo]:
    try:
        yield from tf
    except tarfile.ReadError:
        # apk control/signature segments are tar streams without the end marker.
        return


def _clean(name: str) -> str:
    """Normalise a tar member name to a relative path; reject path traversal."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        msg = f"refusing path traversal in archive member {name!r}"
        raise ValueError(msg)
    return "/".join(parts)


def unpack_tar(tree: Tree, tar_bytes: bytes) -> None:
    """Unpack a tar archive into ``tree``."""
    pending_hardlinks: list[tuple[str, str, tarfile.TarInfo]] = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tf:
        for m in _safe_members(tf):
            name = _clean(m.name)
            # Skip the root entry and apk metadata such as .PKGINFO / .SIGN.*
            if not name or ("/" not in name and name.startswith(".")):
                continue
            if m.isdir():
                path = tree.mkdir(name, m.mode, m.uid, m.gid)
                node = tree.get(path)
                if node is not None and node.is_dir:
                    node.mode = (node.mode & ~0o7777) | (m.mode & 0o7777)
            elif m.isreg():
                tree.add_file(name, _member_bytes(tf, m), m.mode, m.uid, m.gid)
            elif m.issym():
                tree.add_symlink(name, m.linkname, m.uid, m.gid)
            elif m.islnk():
                pending_hardlinks.append((name, _clean(m.linkname), m))
            elif m.ischr() or m.isblk():
                tree.add_device(name, "c" if m.ischr() else "b", m.devmajor, m.devminor, m.mode)
    _add_hardlinks(tree, pending_hardlinks)


def _add_hardlinks(tree: Tree, links: list[tuple[str, str, tarfile.TarInfo]]) -> None:
    """Add hard links as copies of their targets, once every file is in the tree."""
    for name, target, m in links:
        src = tree.get(tree.resolve(target))
        if src is not None and src.is_file:
            tree.add_file(name, src.data, m.mode, m.uid, m.gid)


def minirootfs(cache: str, branch: str, version: str, arch: str, log: Callable[[str], None] = print) -> bytes:
    """Download Alpine's minirootfs, check it against its published SHA-256 and return the tar."""
    fname = f"alpine-minirootfs-{version}-{arch}.tar.gz"
    url = f"{MIRROR}/{branch}/releases/{arch}/{fname}"
    path = _download(url, str(Path(cache, branch, "releases", fname)), log)
    raw = Path(path).read_bytes()
    sha_path = _download(url + ".sha256", path + ".sha256", log)
    sha256 = Path(sha_path).read_text().split()[0]
    got = hashlib.sha256(raw).hexdigest()
    if got != sha256:
        Path(path).unlink()
        msg = f"{fname}: sha256 mismatch ({got} != {sha256})"
        raise ValueError(msg)
    return zlib.decompress(raw, wbits=31)
