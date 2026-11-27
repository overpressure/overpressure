#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""The app's release files: its version for the bundle, and the updater's manifest (docs/releasing.md).

    python scripts/app-manifest.py version                      the bundle's version (semver), from pyproject.toml
    python scripts/app-manifest.py collect --system S --out DIR this system's update file and signature, renamed
    python scripts/app-manifest.py write --tag TAG DIR          DIR's files into DIR/overpressure-app.json

`tauri build` with createUpdaterArtifacts signs each system's update file (the Windows installer, the macOS app as
a .tar.gz, the Linux AppImage) with the key in TAURI_SIGNING_PRIVATE_KEY, beside it as FILE.sig. collect copies
the one it made under the name the release carries (overpressure-app-SYSTEM-CPU.EXT); write reads every such pair
and names each in the manifest with its URL among TAG's files. The app's core installs from that manifest once its
engine has picked the release (overpressure/appupdate.py).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from overpressure import appupdate  # noqa: E402  # this checkout's
from overpressure import version as own_version  # noqa: E402

#: Where tauri build leaves each system's update file, under target/release/bundle/, and what it is in the release.
UPDATE_FILES = {
    "windows-x86_64": ("nsis/*-setup.exe", "setup.exe"),
    "darwin-aarch64": ("macos/*.app.tar.gz", "app.tar.gz"),
    "linux-x86_64": ("appimage/*.AppImage", "AppImage"),
}
PREFIX = "overpressure-app-"
DOWNLOADS = "https://github.com/overpressure/overpressure/releases/download"


def bundle_version(root: Path = ROOT) -> str:
    """Return the bundle's version: pyproject.toml's, in semver."""
    return appupdate.semver(own_version.python_version(root))


def collect(system: str, bundle: Path, out: Path) -> Path:
    """Copy SYSTEM's update file and its signature from BUNDLE into OUT under the release's names; return the file."""
    pattern, extension = UPDATE_FILES[system]
    found = sorted(bundle.glob(pattern))
    if len(found) != 1 or not found[0].with_name(found[0].name + ".sig").is_file():
        msg = f"one signed {pattern} under {bundle}, found {[p.name for p in found]}"
        raise SystemExit(msg)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{PREFIX}{system}.{extension}"
    shutil.copy2(found[0], target)
    shutil.copy2(found[0].with_name(found[0].name + ".sig"), target.with_name(target.name + ".sig"))
    return target


def manifest(tag: str, folder: Path, notes: str = "", now: dt.datetime | None = None) -> dict:
    """Return the updater's manifest for TAG from the update files and signatures in FOLDER."""
    platforms = {}
    for system, (_pattern, extension) in UPDATE_FILES.items():
        name = f"{PREFIX}{system}.{extension}"
        signature = folder / (name + ".sig")
        if (folder / name).is_file() and signature.is_file():
            platforms[system] = {
                "signature": signature.read_text(encoding="utf-8").strip(),
                "url": f"{DOWNLOADS}/{tag}/{name}",
            }
    if not platforms:
        msg = f"no signed update file in {folder}"
        raise SystemExit(msg)
    when = (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0)
    return {
        "version": appupdate.semver(tag),
        "notes": notes,
        "pub_date": when.isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }


def main(argv: list[str]) -> int:
    """Run one of the commands."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    collecting = commands.add_parser("collect")
    collecting.add_argument("--system", required=True, choices=sorted(UPDATE_FILES))
    collecting.add_argument("--bundle", type=Path, default=ROOT / "app/src-tauri/target/release/bundle")
    collecting.add_argument("--out", type=Path, required=True)
    writing = commands.add_parser("write")
    writing.add_argument("--tag", required=True)
    writing.add_argument("--notes", type=Path, help="the release's notes (Markdown)")
    writing.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    if args.command == "version":
        print(bundle_version())
    elif args.command == "collect":
        print(collect(args.system, args.bundle, args.out))
    else:
        notes = args.notes.read_text(encoding="utf-8") if args.notes else ""
        data = manifest(args.tag, args.folder, notes)
        path = args.folder / appupdate.MANIFEST
        with path.open("w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(data, indent=1) + "\n")
        print(f"{path}: {', '.join(data['platforms'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
