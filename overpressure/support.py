# SPDX-License-Identifier: Unlicense
"""Logs for a bug report, in one zip: the installer's records and the headset's own, this computer's identifiers masked.

    python -m overpressure support-bundle FILE

The zip holds about.txt (this overpressure, the headset's release, this computer's system), the installer's
records of the headset under installer/ (out/install/<codename>/: each step's log and every workflow's state)
and what the headset adds under headset/ (Headset.support_files: its own logs, its device's bundle). Every text
file is masked: the values of the serial settings (.env and the environment), what the headset names
(Headset.support_masks) and this computer's home folder; a binary file goes in as it is (a headset's own bundle
masks itself). Nothing is sent anywhere: the owner reads the file and attaches it to a bug report.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import REPO, catalogue, install, localconfig, version

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .headset import Headset

#: The window's button, and how a headset's texts point at it (the window's words stay here, as backup's do).
SAVE_LABEL = "Save logs\N{HORIZONTAL ELLIPSIS}"
HOW_TO_SAVE = f"{SAVE_LABEL} in the window writes them into one file (python -m overpressure support-bundle FILE)"
SUFFIX = ".zip"
#: What a masked value becomes.
SERIAL, HOME = "<serial>", "~"
#: A shorter value is not masked: it would match all over the logs and says nothing about the headset.
SHORTEST = 6


def identifiers(env: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Return what to mask in the logs and what it becomes: the serial settings' values and the home folder."""
    try:
        settings = localconfig.parse(env.read_text(encoding="utf-8"))
    except OSError:
        settings = {}
    found: dict[str, str] = {}
    for key, value in [*settings.items(), *environ.items()]:
        if "SERIAL" in key.upper() and len(value.strip()) >= SHORTEST:
            found[value.strip()] = SERIAL
    home = str(Path.home())
    if len(home) >= SHORTEST:
        found[home] = HOME
        found[home.replace("\\", "/")] = HOME
    return found


def mask(text: str, found: Mapping[str, str]) -> str:
    """Return @p text with every value of @p found replaced by its mask, the longest first."""
    for value in sorted(found, key=len, reverse=True):
        text = text.replace(value, found[value])
    return text


def about(headset: Headset, root: Path) -> str:
    """Return the bundle's about.txt: this overpressure, the headset's release and this computer's system."""
    codename = headset.info.codename
    lines = [
        version.describe(root),
        f"headset: {headset.info.name} ({codename}), {catalogue.identity(codename, root) or 'not downloaded'}",
        f"installed with: {install.installed_release(codename) or 'no finished install on this computer'}",
        f"computer: {platform.platform()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
    ]
    return "\n".join(lines) + "\n"


def bundle(  # noqa: PLR0913  # the headset and the file; where things are keyword-only, for tests
    headset: Headset,
    target: Path,
    *,
    root: Path = REPO,
    records: Path = install.OUT,
    env: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Write the bundle of @p headset to @p target (a zip); return its members."""
    found = identifiers(
        env if env is not None else localconfig.ROOT / ".env", os.environ if environ is None else environ
    )
    unmasked = None
    try:
        found.update({value: text for value, text in headset.support_masks().items() if len(value) >= SHORTEST})
    except Exception as exc:  # noqa: BLE001  # the logs are still worth saving: the owner is told to read them
        unmasked = f"the headset's own identifiers could not be listed, so they are not masked: {exc}\n"
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        if unmasked is not None:
            (work / "unmasked.txt").write_text(unmasked, encoding="utf-8")
        (work / "about.txt").write_text(about(headset, root), encoding="utf-8")
        logs = records / headset.info.codename
        if logs.is_dir():
            shutil.copytree(logs, work / "installer", ignore=shutil.ignore_patterns("*.lock"))
        own = work / "headset"
        own.mkdir()
        try:
            headset.support_files(own)
        except Exception as exc:  # noqa: BLE001  # the installer's logs are worth sending without the headset's own
            (own / "error.txt").write_text(f"the headset's own files are missing: {exc}\n", encoding="utf-8")
        members = []
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(p for p in work.rglob("*") if p.is_file()):
                name = path.relative_to(work).as_posix()
                data = path.read_bytes()
                with contextlib.suppress(UnicodeDecodeError):  # a binary goes in as it is
                    data = mask(data.decode("utf-8"), found).encode("utf-8")
                archive.writestr(name, data)
                members.append(name)
    return members


def main(headset: Headset, argv: list[str]) -> int:
    """Write the support bundle to FILE and list what it holds."""
    parser = argparse.ArgumentParser(prog="python -m overpressure support-bundle", description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path, help="the zip to write")
    args = parser.parse_args(argv)
    target = args.file if args.file.suffix else args.file.with_suffix(SUFFIX)
    print("Collecting the installer's logs and the headset's own", flush=True)
    try:
        members = bundle(headset, target)
    except OSError as exc:
        print(f"error: the logs could not be saved: {exc}", file=sys.stderr)
        return 1
    for name in members:
        print(f"  {name}")
    print("Your headset's serial numbers and other identifiers and your home folder are masked; read it first.")
    print(f"Saved to {target}")
    return 0
