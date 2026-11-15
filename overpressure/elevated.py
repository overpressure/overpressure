# SPDX-License-Identifier: Unlicense
"""overpressure refuses to run with administrator rights: Run as administrator, sudo or root.

Nothing it does needs them. The headset's USB access comes from the platform's drivers or udev rules
(python -m overpressure doctor names them), and what a run writes into this folder (cache/, out/,
backup/, devices/) would belong to the administrator, so the next ordinary run could not change it. The
start scripts refuse the same way before they write anything. The build container is the exception:
its builds run as root inside it (overpressure/linux.py).
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

#: Files a container engine puts at the root of a container (Docker, Podman).
CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))
#: The exit status of a refused start.
REFUSED = 1


def in_container() -> bool:
    """Return whether this runs inside a container, as the build container's steps do."""
    return any(marker.exists() for marker in CONTAINER_MARKERS)


def elevated() -> bool:
    """Return whether this process has administrator rights: an elevated token, or root outside a container."""
    if sys.platform == "win32":
        shell32 = getattr(getattr(ctypes, "windll", None), "shell32", None)
        try:
            return bool(shell32 is not None and shell32.IsUserAnAdmin())
        except OSError:
            return False
    return os.geteuid() == 0 and not in_container()


def message(platform: str = sys.platform) -> str:
    """Return the refusal for @p platform: what is wrong, why, and how to start it instead."""
    if platform == "win32":
        how = (
            "Close this window and start it again as your own user: double-click Install.cmd, not Run as"
            " administrator (in a terminal: one that was not opened as administrator)."
        )
    else:
        how = (
            "Start it again as your own user, without sudo: sh install.sh, or python -m overpressure in your own"
            " terminal. For the headset's USB access, python -m overpressure doctor names the rules to add."
        )
    return (
        "overpressure does not run with administrator rights (Run as administrator, sudo or root). Nothing it"
        " does needs them, and what it writes into its folder would belong to the administrator, so later"
        f" runs could not change it. {how}"
    )


def show(text: str) -> None:
    """Show @p text in a dialog too, for the window started without a terminal; nothing when Tk is missing."""
    try:
        import tkinter as tk  # noqa: PLC0415  # only for this dialog
        from tkinter import messagebox  # noqa: PLC0415  # only for this dialog
    except ImportError:
        return
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("overpressure", text, parent=root)
        root.destroy()
    except tk.TclError:  # no display
        pass
