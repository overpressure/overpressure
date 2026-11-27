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

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from . import REPO
from . import version as own_version

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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


def _move(src: Path, dst: Path) -> None:
    """Move SRC to DST: a rename on the same disk, else a copy that replaces SRC only once it is complete."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dst)
    except OSError:
        partial = dst.with_name(dst.name + ".partial")
        shutil.copytree(src, partial) if src.is_dir() else shutil.copy2(src, partial)
        partial.replace(dst)
        shutil.rmtree(src) if src.is_dir() else src.unlink()


def _copy(src: Path, dst: Path) -> None:
    """Copy SRC (a file or a folder) to DST, keeping what DST holds already."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=_keep_existing)
    else:
        shutil.copy2(src, dst)


def _keep_existing(src: str, dst: str) -> None:
    """Copy one file of a folder unless the target has it already."""
    if not Path(dst).exists():
        shutil.copy2(src, dst)


def _settings_and_headsets(old: Path, root: Path) -> list[str]:
    """Copy OLD's settings, downloaded headsets and installer records into ROOT; return what came."""
    brought: list[str] = []
    if (old / SETTINGS).is_file() and not (root / SETTINGS).exists():
        _copy(old / SETTINGS, root / SETTINGS)
        brought.append("the settings (.env)")
    for folder in sorted((old / DEVICES).glob("*/")) if (old / DEVICES).is_dir() else []:
        # Downloaded headsets (a release or a preview); a development checkout is the developer's own.
        if not (folder / "headset.json").is_file() or (folder / ".git").exists():
            continue
        if not (root / DEVICES / folder.name).exists():
            _copy(folder, root / DEVICES / folder.name)
            brought.append(f"the headset {folder.name} (devices/{folder.name})")
    if (old / INSTALLER).is_dir():
        _copy(old / INSTALLER, root / INSTALLER)
        brought.append("the installer's records (out/install)")
    return brought


def carry(
    old: Path,
    root: Path = REPO,
    *,
    headset_files: Callable[[], Iterable[Path]] = list,
    say: Callable[[str], None] = print,
) -> list[str]:
    """Bring the owner's data from OLD into ROOT; return what came, in words.

    The backup moves, the rest is copied; nothing ROOT has is replaced. HEADSET_FILES returns, once the
    headsets are here, the other files their way back needs (absolute, under ROOT): those come from the same
    place in OLD.
    """
    problem = check(old, root) if not is_overpressure(old) or old.resolve() == root.resolve() else ""
    if problem:
        raise ValueError(problem)
    brought = _settings_and_headsets(old, root)
    for path in headset_files():
        relative = path.resolve().relative_to(root.resolve())
        if relative.parts[:1] == BACKUP.parts:
            continue  # it comes with the backup
        if (old / relative).exists() and not path.exists():
            _copy(old / relative, path)
            brought.append(f"{relative.as_posix()}")
    if (old / BACKUP).exists() and not (root / BACKUP).exists():
        say(f"Moving the backup from {old / BACKUP} ...")
        _move(old / BACKUP, root / BACKUP)
        (old / MOVED).write_text(
            f"This overpressure's backup (backup/) moved to {root / BACKUP}, with the newer overpressure.\n",
            encoding="utf-8",
        )
        brought.append("the backup (backup/, moved)")
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
