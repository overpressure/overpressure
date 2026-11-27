# SPDX-License-Identifier: Unlicense
"""An owner's data from the overpressure folder this one replaces (docs/tool.md#a-newer-overpressure).

A newer release unpacks into a folder of its own (overpressure-VERSION), beside the one before it. What the
owner made lives in the earlier folder: the settings (.env), the headset's backup (backup/, the way back to
its own system), the installer's records (out/install/, which say what the headset runs) and the headsets
downloaded into devices/. A fresh folder finds the earlier one beside it and brings that over: the backup
moves (a rename on the same disk; it is too large to keep twice), the rest is copied, and nothing that is
here already is replaced. A headset names the other files its way back needs (Headset.backup_set): those
are copied too. The window asks on its first start in a fresh folder; `python -m overpressure carry-over
[FOLDER]` does it from a terminal.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from . import REPO, progress
from . import version as own_version

if TYPE_CHECKING:
    from collections.abc import Iterable

#: What a fresh folder decided about an earlier one: brought over, or declined (then never asked again).
RECORD = Path("out") / "carried-over.json"
#: The owner's own data, by its place in an overpressure folder.
SETTINGS, BACKUP, INSTALLER, DEVICES = Path(".env"), Path("backup"), Path("out") / "install", Path("devices")
#: The earlier folder's note where its backup went.
MOVED = "MOVED.txt"


def is_overpressure(folder: Path) -> bool:
    """Tell whether FOLDER is an overpressure folder (a release's or a checkout)."""
    return (folder / "overpressure" / "__init__.py").is_file() and (folder / "pyproject.toml").is_file()


def owners_data(folder: Path) -> list[Path]:
    """Return what of the owner's data FOLDER holds, by its place there."""
    return [part for part in (SETTINGS, BACKUP, INSTALLER) if (folder / part).exists()]


def fresh(root: Path = REPO) -> bool:
    """Tell whether ROOT is a release's folder with none of the owner's data, undecided about an earlier folder.

    A checkout (.git) is a developer's: never.
    """
    return not (root / ".git").exists() and not owners_data(root) and not (root / RECORD).exists()


def _newest(folder: Path) -> float:
    """Return when FOLDER's owner data last changed."""
    return max((folder / part).stat().st_mtime for part in owners_data(folder))


#: Where an earlier overpressure may be, and how deep: 1 for the folder's own folders, 2 for theirs too.
Place = tuple[Path, int]


def previous(root: Path = REPO, places: Iterable[Place] = ()) -> Path | None:
    """Return the earlier overpressure folder a fresh ROOT would take the data of, None for none.

    A release's folder looks beside itself (the zip unpacked next to the one before); the app's data folder,
    which has no such neighbours, looks in @p places too (portable_places: where a downloaded zip ends up). Hidden
    folders are skipped, and a folder costs one look at its overpressure/__init__.py: the search stays well under
    a second in a full home folder. Of several, the one whose data changed last.
    """
    if not fresh(root):
        return None
    here = root.resolve()
    found: dict[Path, None] = {}  # in order, once each
    for folder, depth in [(root.parent, 1), *places]:
        found.update(dict.fromkeys(_folders_in(folder, depth)))
    earlier = [p for p in found if is_overpressure(p) and owners_data(p) and p.resolve() != here]
    return max(earlier, key=_newest) if earlier else None


def _folders_in(folder: Path, depth: int) -> list[Path]:
    """Return the folders in @p folder, @p depth levels down (1: its own), skipping what cannot be read."""
    level, found = [folder], []
    for _ in range(depth):
        level = [child for parent in level for child in _children(parent)]
        found += level
    return found


def _children(folder: Path) -> list[Path]:
    """Return @p folder's folders but hidden ones (a dot, or Windows' hidden and system ones); none when unreadable."""
    try:
        with os.scandir(folder) as entries:
            return [Path(e.path) for e in entries if not e.name.startswith(".") and _visible_folder(e)]
    except OSError:
        return []


def _visible_folder(entry: os.DirEntry[str]) -> bool:
    """Tell whether @p entry is a folder that is not hidden (Windows' attributes come with the listing)."""
    try:
        if not entry.is_dir():
            return False
        attributes = getattr(entry.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return not attributes & (_HIDDEN | _SYSTEM)


_HIDDEN, _SYSTEM = 0x2, 0x4  # FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM (stat's constants are Windows-only)


def portable_places(home: Path | None = None) -> list[Place]:
    """Return where an owner's portable overpressure (the zip) usually is, and how deep to look there.

    In Downloads, on the desktop or in documents, or a folder in one of them; in the home folder itself.
    """
    home = home or Path.home()
    return [(home / "Downloads", 2), (home / "Desktop", 2), (home / "Documents", 2), (home, 1)]


def check(old: Path, root: Path = REPO) -> str:
    """Return why @p old cannot be carried over into ROOT, in the owner's words; "" when it can."""
    if old.resolve() == root.resolve():
        return "That is the folder this overpressure uses already."
    if not old.is_dir() or not is_overpressure(old):
        return f"{old} is not an overpressure folder: choose the folder that has Install.cmd or install.sh in it."
    if not owners_data(old):
        return f"{old} holds no backup, settings or installer records: there is nothing to bring over."
    return ""


def _record(root: Path, data: dict) -> None:
    """Write ROOT's decision about an earlier folder."""
    path = root / RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as out:  # write_text takes newline= from Python 3.10 on
        out.write(json.dumps(data, indent=1) + "\n")


def decline(old: Path, root: Path = REPO) -> None:
    """Remember that ROOT takes nothing from OLD: the window asks no more."""
    _record(root, {"declined": str(old)})


#: How far bringing the data over is: bytes done, bytes in all, what it does now in words.
Report = Callable[[int, int, str], None]
#: How often a copy reports, at most, in seconds.
REPORT_EVERY = 0.2


class _Count:
    """Bytes brought over so far, reported now and then."""

    def __init__(self, total: int, report: Report | None) -> None:
        self.done, self.total, self.report, self.what, self.last = 0, total, report, "", 0.0

    def start(self, what: str) -> None:
        """Say what comes next."""
        self.what = what
        self._tell()

    def add(self, size: int, *, final: bool = False) -> None:
        """Count SIZE more bytes as done."""
        self.done += size
        if final or time.monotonic() - self.last >= REPORT_EVERY:
            self._tell()

    def _tell(self) -> None:
        self.last = time.monotonic()
        if self.report is not None:
            self.report(min(self.done, self.total), self.total, self.what)


def _size(path: Path) -> int:
    """Return the bytes the files at @p path hold (a file, or a folder's files); 0 for nothing."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(folder) / name).stat().st_size
    return total


def _copy_file(src: str | Path, dst: str | Path, count: _Count) -> None:
    """Copy one file in chunks, counting its bytes, with its times and mode."""
    with open(src, "rb") as source, open(dst, "wb") as target:  # noqa: PTH123  # copytree hands over strings
        while True:
            chunk = source.read(progress.CHUNK)
            if not chunk:
                break
            target.write(chunk)
            count.add(len(chunk))
    shutil.copystat(src, dst)


def _move(src: Path, dst: Path, count: _Count) -> None:
    """Move SRC to DST: a rename on the same disk, else a copy that replaces SRC only once it is complete."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = _size(src)
    try:
        src.rename(dst)
    except OSError:
        partial = dst.with_name(dst.name + ".partial")
        if src.is_dir():
            shutil.copytree(src, partial, copy_function=lambda s, d: _copy_file(s, d, count))
        else:
            _copy_file(src, partial, count)
        partial.replace(dst)
        shutil.rmtree(src) if src.is_dir() else src.unlink()
    else:
        count.add(size)  # a rename: done at once


def _copy(src: Path, dst: Path, count: _Count) -> None:
    """Copy SRC (a file or a folder) to DST, keeping what DST holds already."""
    dst.parent.mkdir(parents=True, exist_ok=True)

    def keep_existing(source: str, target: str) -> None:
        if Path(target).exists():
            count.add(Path(source).stat().st_size)  # kept: counted as done
        else:
            _copy_file(source, target, count)

    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=keep_existing)
    else:
        _copy_file(src, dst, count)


def _plan(old: Path, root: Path) -> list[tuple[Path, Path, str]]:
    """Return what of OLD's settings, downloaded headsets and installer records ROOT takes: (from, to, words)."""
    plan: list[tuple[Path, Path, str]] = []
    if (old / SETTINGS).is_file() and not (root / SETTINGS).exists():
        plan.append((old / SETTINGS, root / SETTINGS, "the settings (.env)"))
    for folder in sorted((old / DEVICES).glob("*/")) if (old / DEVICES).is_dir() else []:
        # Downloaded headsets (a release or a preview); a development checkout is the developer's own.
        if not (folder / "headset.json").is_file() or (folder / ".git").exists():
            continue
        if not (root / DEVICES / folder.name).exists():
            plan.append((folder, root / DEVICES / folder.name, f"the headset {folder.name} (devices/{folder.name})"))
    if (old / INSTALLER).is_dir():
        plan.append((old / INSTALLER, root / INSTALLER, "the installer's records (out/install)"))
    return plan


def carry(
    old: Path,
    root: Path = REPO,
    *,
    headset_files: Callable[[], Iterable[Path]] = list,
    say: Callable[[str], None] = print,
    report: Report | None = None,
) -> list[str]:
    """Bring the owner's data from OLD into ROOT; return what came, in words.

    The backup moves, the rest is copied; nothing ROOT has is replaced. HEADSET_FILES returns, once the
    headsets are here, the other files their way back needs (absolute, under ROOT): those come from the same
    place in OLD. REPORT hears how far it is, by bytes: a move on the same disk is a rename, done at once; to
    another disk (the zip on a USB stick, say) it is a copy of every byte.
    """
    problem = check(old, root) if not is_overpressure(old) or old.resolve() == root.resolve() else ""
    if problem:
        raise ValueError(problem)
    plan = _plan(old, root)
    backup = (old / BACKUP).exists() and not (root / BACKUP).exists()
    count = _Count(sum(_size(src) for src, _dst, _words in plan) + (_size(old / BACKUP) if backup else 0), report)
    brought: list[str] = []
    for src, dst, words in plan:
        count.start(f"Copying {words}")
        _copy(src, dst, count)
        brought.append(words)
    for path in headset_files():
        relative = path.resolve().relative_to(root.resolve())
        if relative.parts[:1] == BACKUP.parts:
            continue  # it comes with the backup
        if (old / relative).exists() and not path.exists():
            count.total += _size(old / relative)
            count.start(f"Copying {relative.as_posix()}")
            _copy(old / relative, path, count)
            brought.append(f"{relative.as_posix()}")
    if backup:
        say(f"Moving the backup from {old / BACKUP} ...")
        count.start("Moving the backup")
        _move(old / BACKUP, root / BACKUP, count)
        (old / MOVED).write_text(
            f"This overpressure's backup (backup/) moved to {root / BACKUP}, with the newer overpressure.\n",
            encoding="utf-8",
        )
        brought.append("the backup (backup/, moved)")
    count.add(0, final=True)
    _record(root, {"from": str(old), "version": own_version.describe(old), "brought": brought})
    return brought


def question(old: Path) -> str:
    """Return the window's question about OLD, in the owner's words (the app's data folder: the same words)."""
    return (
        f"Your earlier overpressure is in {old}.\n\n"
        "Bring your headset's backup, your settings and what the installer knows about your headset over to "
        "this one? The backup moves here; the earlier folder keeps its own copy of the rest.\n\n"
        "Without them this overpressure starts from nothing: its installer would need a new backup, and the "
        "way back to your headset's own system needs the backup you made."
    )
