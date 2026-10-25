"""The stock firmware's partitions from PICO's public full OTA, in place of the user's backup.

    python -m pico4 stock [--firmware NAME] OUTPUT_DIR

The extraction scripts (scripts/extract-*.sh) take the stock files they need from these images
(docs/development.md#stock-firmware). Everything they take is the same on every PICO 4 with that
firmware; the headset's own data (persist) is read on the headset at boot instead. OUTPUT_DIR
receives:

  system.img, vendor.img   the system and vendor partitions (ext4), rebuilt from the OTA's
                           brotli-compressed block images and their transfer lists
  modem.bin                firmware-update/NON-HLOS.bin (FAT): the DSP and Wi-Fi firmware
  dsp.bin                  firmware-update/dspso.bin (ext4): the DSPs' process shells
  recovery.img, boot.img   the stock recovery and boot images
  manifest.json            the firmware, the OTA's URL and the sha256 of every file

The OTA members are kept under OUTPUT_DIR/download (about 3.9 GB) and fetched again only when
missing or corrupt. Decompressing needs the brotli command (Debian/Ubuntu: apt install brotli).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path
from typing import BinaryIO, Iterable

from . import payload

CATALOG = Path(__file__).with_name("firmware.json")
DEFAULT_FIRMWARE = "5.13.7-global-seko"
BLOCK = 4096
CHUNK = 8 * 1024 * 1024

#: Output file -> OTA member, copied as they are.
COPIED = {
    "modem.bin": "firmware-update/NON-HLOS.bin",
    "dsp.bin": "firmware-update/dspso.bin",
    "recovery.img": "recovery.img",
    "boot.img": "boot.img",
}
#: Output image -> the block image's base name in the OTA.
BLOCK_IMAGES = {"system.img": "system", "vendor.img": "vendor"}


def parse_rangeset(text: str) -> list[tuple[int, int]]:
    """A transfer list's range set ("4,0,10,20,30": two ranges) as (first, end) block pairs."""
    values = [int(v) for v in text.split(",")]
    if values[0] != len(values) - 1 or values[0] % 2:
        raise ValueError(f"malformed range set {text!r}")
    return [(values[i], values[i + 1]) for i in range(1, len(values), 2)]


def apply_transfer_list(transfer_list: str, new_data: BinaryIO, out: BinaryIO) -> int:
    """Rebuild a partition image from a full OTA's transfer list and its decompressed new data
    (as AOSP's block-based OTA writes it: 'new' takes the next blocks of the data in order,
    'zero' and 'erase' leave zeros). Returns the image's size in bytes."""
    lines = transfer_list.splitlines()
    version, total = int(lines[0]), int(lines[1])
    if version < 1 or version > 4:
        raise ValueError(f"unsupported transfer list version {version}")
    commands = lines[4:] if version >= 2 else lines[2:]
    for line in commands:
        if not line.strip():
            continue
        cmd, _, arg = line.partition(" ")
        if cmd == "new":
            for first, end in parse_rangeset(arg):
                out.seek(first * BLOCK)
                left = (end - first) * BLOCK
                while left:
                    data = new_data.read(min(left, CHUNK))
                    if not data:
                        raise ValueError("the new data ends before the transfer list")
                    out.write(data)
                    left -= len(data)
        elif cmd in ("zero", "erase"):
            continue  # a fresh file is zero already
        else:
            raise ValueError(f"a full OTA's transfer list has no {cmd!r} command")
    size = total * BLOCK
    out.seek(0, io.SEEK_END)
    if out.tell() < size:  # trailing zero blocks: extend (truncate need not)
        out.seek(size - 1)
        out.write(b"\0")
    out.truncate(size)
    return size


def _member_ok(path: Path, info: zipfile.ZipInfo) -> bool:
    if not path.exists() or path.stat().st_size != info.file_size:
        return False
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            crc = zlib.crc32(chunk, crc)
    return crc == info.CRC


def download_member(src: payload.Source, info: zipfile.ZipInfo, path: Path, log=print) -> None:
    """One zip member into @path, its CRC-32 checked against the zip directory; kept if it is there."""
    if _member_ok(path, info):
        log(f"  {info.filename}: already downloaded")
        return
    local = src.read_at(info.header_offset, 30)
    if local[:4] != b"PK\x03\x04":
        raise ValueError(f"bad local file header for {info.filename}")
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    start = info.header_offset + 30 + name_len + extra_len
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError(f"{info.filename}: unsupported zip compression method {info.compress_type}")
    inflater = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    crc, done, written = 0, 0, 0
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with tmp.open("wb") as out:
        while done < info.compress_size:
            chunk = src.read_at(start + done, min(CHUNK, info.compress_size - done))
            done += len(chunk)
            data = inflater.decompress(chunk) if inflater else chunk
            crc = zlib.crc32(data, crc)
            out.write(data)
            written += len(data)
            if done % (256 * 1024 * 1024) < CHUNK or done == info.compress_size:
                log(f"  {info.filename}: {done * 100 // info.compress_size:3d}% ({done / 1e6:.0f} MB)")
        if inflater:
            tail = inflater.flush()
            crc = zlib.crc32(tail, crc)
            out.write(tail)
            written += len(tail)
    if written != info.file_size or crc != info.CRC:
        tmp.unlink()
        raise ValueError(f"{info.filename}: CRC/size mismatch after download (corrupt transfer?)")
    tmp.replace(path)


def rebuild_image(download: Path, name: str, out_path: Path, log=print) -> None:
    """vendor.new.dat.br + vendor.transfer.list -> an ext4 image, through the brotli command."""
    brotli = shutil.which("brotli")
    if brotli is None:
        raise SystemExit("the brotli command is required (Debian/Ubuntu: apt install brotli)")
    transfer = (download / f"{name}.transfer.list").read_text(encoding="ascii")
    tmp = out_path.with_name(out_path.name + ".part")
    log(f">> {out_path.name} from {name}.new.dat.br")
    proc = subprocess.Popen([brotli, "-d", "-c", str(download / f"{name}.new.dat.br")], stdout=subprocess.PIPE)
    try:
        with tmp.open("wb") as out:
            size = apply_transfer_list(transfer, proc.stdout, out)
        rest = proc.stdout.read(1)
    finally:
        proc.stdout.close()
        code = proc.wait()
    if code != 0:
        raise SystemExit(f"brotli failed on {name}.new.dat.br ({code})")
    if rest:
        raise ValueError(f"{name}.new.dat.br holds more data than its transfer list places")
    with tmp.open("rb") as f:
        f.seek(0x438)  # ext4 superblock magic
        if f.read(2) != b"\x53\xef":
            raise ValueError(f"{out_path.name} is not an ext4 image")
    tmp.replace(out_path)
    log(f"  {out_path.name}: {size / 1e6:.0f} MB")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def members_needed() -> Iterable[str]:
    yield from COPIED.values()
    for base in BLOCK_IMAGES.values():
        yield f"{base}.new.dat.br"
        yield f"{base}.transfer.list"


def build(firmware: str, output: Path, location: str | None = None, log=print) -> dict:
    builds = json.loads(CATALOG.read_text(encoding="utf-8"))["builds"]
    if firmware not in builds:
        raise SystemExit(f"unknown --firmware {firmware!r}; known: {', '.join(builds)}")
    location = location or builds[firmware]["url"]
    output.mkdir(parents=True, exist_ok=True)
    download = output / "download"
    src = payload.HttpSource(location, log) if location.startswith(("http://", "https://")) \
        else payload.FileSource(location)
    with zipfile.ZipFile(io.BufferedReader(payload.SourceFile(src), buffer_size=65536)) as zf:
        infos = {name: zf.getinfo(name) for name in members_needed()}
    log(f">> {firmware}: {sum(i.compress_size for i in infos.values()) / 1e9:.1f} GB of OTA members")
    for name, info in infos.items():
        download_member(src, info, download / name, log)
    for out_name, member in COPIED.items():
        shutil.copyfile(download / member, output / out_name)
    # A rebuilt image stays when the manifest says it is this firmware's (and it is unchanged).
    previous = {}
    if (output / "manifest.json").exists():
        old = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        previous = old["files"] if old.get("firmware") == firmware and old.get("source") == location else {}
    for out_name, base in BLOCK_IMAGES.items():
        path = output / out_name
        if not path.exists() or previous.get(out_name) != sha256(path):
            rebuild_image(download, base, path, log)
    files = {name: sha256(output / name) for name in (*COPIED, *BLOCK_IMAGES)}
    manifest = {"schema": 1, "firmware": firmware, "source": location, "files": files}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log(f">> {output}: {', '.join(files)}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pico4 stock", description=__doc__.split("\n\n")[0])
    ap.add_argument("output", type=Path)
    ap.add_argument("--firmware", default=DEFAULT_FIRMWARE, help=f"firmware.json entry (default {DEFAULT_FIRMWARE})")
    ap.add_argument("--ota", help="a local copy of the OTA zip instead of the catalog's URL")
    args = ap.parse_args(argv)
    build(args.firmware, args.output, args.ota)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
