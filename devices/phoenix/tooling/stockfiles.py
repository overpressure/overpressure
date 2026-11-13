# SPDX-License-Identifier: Unlicense
"""PICO OS's files on the headset itself, not in the system image (docs/install.md#pico-oss-files).

    python -m overpressure stock-files [--backup DIR] [--assets DIR] OUTPUT.tar.gz

The system needs about 85 MB of PICO OS's files that the install overwrites: its system and
vendor partitions live in super. They are taken from the headset's own EDL backup (the same
files as PICO's OTA of that firmware: assets.from_backup), laid out as the system expects them,
and written to the data partition as /var/lib/pico4-stock by the maintenance stage 1
(pico4-install stock). The image holds symbolic links to them (userspace/stock-links.txt). PICO OS's modem,
dsp and bluetooth partitions, which nothing writes, are mounted read-only at boot instead
(preinit, under /run/pico4/firmware): the DSPs', the video codec's and the wireless chip's firmware come
from there. persist, the headset's own data, is read at boot as before.

The archive is the tree with a manifest.json of every file's SHA-256; the same assets give
the same archive. Everything in it is PICO's: it stays on this computer and the headset.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import IO, Callable

from overpressure import REPO
from overpressure.assets import AssetError

from . import assets

Log = Callable[[str], None]

#: Where the data partition's copy lives on the headset (the data partition's /var).
STOCK = "/var/lib/pico4-stock"
SCHEMA = 1

#: The tree, relative to /: (its path, the assets kind, the path in the kind's folder); a directory whole.
TREE = (
    *((f"usr/lib/firmware/{n}", "graphics-firmware", n) for n in assets.GPU_FIRMWARE),
    ("usr/lib/firmware/qcom/sm8250/pico4/imx471-mode.bin", "sensor-data", "camera/imx471-mode.bin"),
    ("usr/share/qcom/pico4/sensors", "sensor-data", "sensors"),
    ("usr/share/qcom/pico4/calibration", "sensor-data", "calibration"),
    ("usr/share/steamvr-monado/resources/rendermodels", "sensor-data", "rendermodels"),
    ("usr/lib/pico4-audio/acdb", "audio", "calibration"),
    ("usr/share/qcom/pico4-audio", "audio", "audiopd"),
    ("usr/lib/pico4-stock-slam/system", "stock-slam", "root/system"),
    ("usr/lib/pico4-stock-slam/vendor", "stock-slam", "root/vendor"),
)
#: What the tree leaves out: the dsp partition's, mounted at boot in its place.
LEFT_OUT = ("usr/lib/pico4-stock-slam/vendor/dsp",)
#: Symbolic links in the tree: the headset's own data in persist, mounted at boot.
TREE_LINKS = {
    "usr/share/qcom/pico4/sensors/registry": "/mnt/vendor/persist/sensors/registry/registry",
    "usr/share/qcom/pico4/sensors/sns_reg_version": "/mnt/vendor/persist/sensors/registry/sns_reg_version",
    "usr/share/qcom/pico4/calibration/pvr": "/mnt/vendor/persist/pvr",
}
#: Mount points the tracker's root binds over (stock-slam): the dsp partition, the image's libandroid.
MOUNT_POINTS = {
    "usr/lib/pico4-stock-slam/vendor/dsp/cdsp": "dir",
    "usr/lib/pico4-stock-slam/system/lib64/libandroid.so": "file",
}
#: Programs in the tree (the rest: 0644 files, 0755 directories, owned by root).
PROGRAMS = (
    "usr/lib/pico4-stock-slam/system/bin/bootstrap/linker64",
    "usr/lib/pico4-stock-slam/vendor/bin/cdsprpcd",
)
#: The system image's links (LINK TARGET lines), which scripts/build-userspace.sh makes.
LINKS_FILE = Path(__file__).resolve().parents[1] / "userspace" / "stock-links.txt"


def image_links(path: Path = LINKS_FILE) -> dict[str, str]:
    """Return the system image's links to PICO OS's files: path relative to its root -> where on the headset."""
    links = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            link, target = words
            links[link] = target
    return links


def _kinds() -> tuple[str, ...]:
    return tuple(dict.fromkeys(kind for _path, kind, _source in TREE))


def _left_out(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in LEFT_OUT)


def _copy(source: Path, target: Path, path: str, files: dict[str, Path]) -> None:
    """Collect @p source (a file or a tree) as @p path into @p files, leaving LEFT_OUT and links out."""
    if _left_out(path):
        return
    if source.is_symlink():
        return  # the assets' own links to persist: TREE_LINKS makes them
    if source.is_dir():
        for child in sorted(source.iterdir()):
            _copy(child, target / child.name, f"{path}/{child.name}", files)
    elif source.is_file():
        files[path] = source
    else:
        msg = f"{source} is missing (python -m overpressure assets)"
        raise AssetError(msg)


def collect(folders: dict[str, Path]) -> dict[str, Path]:
    """Return the tree's files, relative to /, from the assets kinds' @p folders."""
    files: dict[str, Path] = {}
    for path, kind, source in TREE:
        _copy(folders[kind] / source, Path(path), path, files)
    return files


def _info(name: str, kind: bytes, mode: int, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.size = kind, mode, size
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = 0
    return info


def _directories(paths: list[str]) -> list[str]:
    found = set()
    for path in paths:
        for parent in PurePosixPath(path).parents:
            if str(parent) != ".":
                found.add(str(parent))
    return sorted(found)


def write_archive(folders: dict[str, Path], version: str, target: IO[bytes]) -> dict:
    """Write the tree as a gzip-compressed tar to @p target; return its manifest (also its first member)."""
    files = collect(folders)
    hashes = {}
    for path, source in files.items():
        hashes[path] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {"schema": SCHEMA, "firmware": version, "files": dict(sorted(hashes.items())), "links": TREE_LINKS}
    data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
    # For the maintenance stage 1's sha256sum -c: it has no JSON reader.
    sums = "".join(f"{sha}  {path}\n" for path, sha in manifest["files"].items()).encode()
    entries = [*files, *TREE_LINKS, *MOUNT_POINTS]
    gz = gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0)  # Python 3.9: no parenthesized withs
    with gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(_info("manifest.json", tarfile.REGTYPE, 0o644, len(data)), io.BytesIO(data))
        tar.addfile(_info("SHA256SUMS", tarfile.REGTYPE, 0o644, len(sums)), io.BytesIO(sums))
        for directory in _directories(entries):
            tar.addfile(_info(directory, tarfile.DIRTYPE, 0o755))
        for path in sorted(files):
            mode = 0o755 if path in PROGRAMS else 0o644
            with files[path].open("rb") as stream:
                tar.addfile(_info(path, tarfile.REGTYPE, mode, files[path].stat().st_size), stream)
        for path, target_path in sorted(TREE_LINKS.items()):
            info = _info(path, tarfile.SYMTYPE, 0o777)
            info.linkname = target_path
            tar.addfile(info)
        for path, kind in sorted(MOUNT_POINTS.items()):
            if kind == "dir":
                tar.addfile(_info(path, tarfile.DIRTYPE, 0o755))
            else:
                tar.addfile(_info(path, tarfile.REGTYPE, 0o644), io.BytesIO(b""))
    return manifest


def manifest_sha256(manifest: dict) -> str:
    """Return the SHA-256 of the manifest as the archive (and the headset's copy) holds it."""
    return hashlib.sha256((json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()).hexdigest()


def from_backup(backup: Path, output: Path, *, log: Log = print) -> dict:
    """Write @p output (.tar.gz) from the headset's own backup @p backup; return its manifest."""
    with tempfile.TemporaryDirectory(prefix="pico4-stock-") as work:
        made = assets.from_backup(backup, Path(work), _kinds(), log=log)
        folders = {path.name.rsplit("-", 1)[0]: path for path in made}
        version = made[0].name.rsplit("-", 1)[1]
        return _write(folders, version, output, log)


def from_assets(root: Path, version: str, output: Path, *, log: Log = print) -> dict:
    """Write @p output (.tar.gz) from the assets folders in @p root (assets/KIND-@p version); return its manifest."""
    folders = {kind: root / f"{kind}-{version}" for kind in _kinds()}
    for kind, folder in folders.items():
        if not assets.current(folder, kind):
            msg = f"no current {folder} (python -m overpressure assets {kind})"
            raise AssetError(msg)
    return _write(folders, version, output, log)


def _write(folders: dict[str, Path], version: str, output: Path, log: Log) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    new = output.with_name(output.name + ".new")
    with new.open("wb") as stream:
        manifest = write_archive(folders, version, stream)
    new.replace(output)
    log(f">> {output}: {len(manifest['files'])} files of PICO OS {version} ({output.stat().st_size >> 20} MiB)")
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Run the stock-files command line."""
    ap = argparse.ArgumentParser(prog="python -m overpressure stock-files", description=__doc__.split("\n\n")[0])
    ap.add_argument("output", type=Path, help="the archive to write (.tar.gz)")
    ap.add_argument("--backup", type=Path, help="the headset's EDL backup (default: the one .env names)")
    ap.add_argument("--assets", type=Path, help="take the assets/KIND-VERSION folders here instead of the backup")
    ap.add_argument("--version", default="5.13.7", help="the assets folders' firmware number (with --assets)")
    args = ap.parse_args(argv)
    try:
        if args.assets:
            from_assets(args.assets, args.version, args.output)
        else:
            from . import localconfig  # noqa: PLC0415  # .env only when no backup is named

            backup = args.backup or localconfig.load(REPO / ".env", {}).path("backup")
            from_backup(backup, args.output)
    except (AssetError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
