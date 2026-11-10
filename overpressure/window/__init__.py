# SPDX-License-Identifier: Unlicense
"""The window: the installer, and every step of the headset in its Advanced mode.

One CustomTkinter window with two views, switched by the link in its header. The installer view
runs ``python -m overpressure install --json`` as a child and draws its events; Advanced mode lists
the headset's steps and the dependency checks and runs one step at a time. Neither does anything a
terminal cannot: the installer is the engine's command line, a step shows the command line it runs.

- installer.py, advanced.py: the two views' models and the processes behind them; headsets.py:
  which headset the window works on (the headset page it opens on, the last choice). No widgets,
  so tests drive them without a display.
- app.py (the header, the installer view, the panel for asks), advanced_view.py: the widgets.
- theme.py: the design tokens (spacing, radii, colours for light and dark, the type scale and the
  fonts picked per platform); kit.py: the components built from them, which the widgets reuse;
  logo.py: the logo's PNG renderings (icons/), picked for the display's scaling.

Only the widgets need CustomTkinter (the ``gui`` extra, which the start scripts install). It is
imported when the window opens, so the rest of the tool imports without it or without Tk.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from overpressure import localconfig
from overpressure.headset import INSTALL
from overpressure.window.advanced import Advanced, DemoProcess, demo_checks
from overpressure.window.app import ADVANCED_VIEW, INSTALLER_VIEW, Window
from overpressure.window.headsets import PREFS, Choice
from overpressure.window.installer import BackupJob, ChildEngine, DemoBackupJob, DemoEngine, Engine

if TYPE_CHECKING:
    from overpressure.headset import Headset, Registry

#: How to get CustomTkinter, for a host without it.
HOW = (
    "the start scripts (Install.cmd, Install.command, install.sh) bring it; in a development environment, "
    "uv sync --extra gui does"
)


def available() -> str:
    """Return why the window cannot open here, or "" when it can."""
    if importlib.util.find_spec("_tkinter") is None:
        return "this Python has no Tk"
    if importlib.util.find_spec("customtkinter") is None:
        return "CustomTkinter is not installed"
    return ""


def main(
    registry: Registry, headset: Headset | None, *, demo: bool = False, fresh: bool = False, advanced: bool = False
) -> int:
    """Open the window for HEADSET (--device), else on the page that asks which of REGISTRY's; return when closed.

    The headset chosen last is kept in out/window.json and marked on that page. DEMO uses the fake
    engine, a fake step program and fixed checks: nothing runs, and its choice is not kept. Its
    installer starts as if an earlier run had done four stages (the start page), unless FRESH.
    ADVANCED opens Advanced mode first (after the choice).
    """
    import tkinter as tk  # noqa: PLC0415  # Tk's errors; Python without Tk imports the rest of this package

    import customtkinter  # noqa: PLC0415  # the gui extra, only for the window

    def engine_for(chosen: Headset | None, workflow: str = INSTALL) -> Engine:
        if demo:
            info = chosen.info if chosen is not None else None
            return DemoEngine(
                recorded=not fresh,
                codename=info.codename if info else "demo",
                name=info.name if info else "headset",
                workflow=workflow,
            )
        return ChildEngine(chosen.info.codename if chosen is not None else None, workflow)

    side = (
        Advanced(registry, headset, process=DemoProcess(), checks=demo_checks) if demo else Advanced(registry, headset)
    )
    # The last choice is the window's preference, not the command line's setting: .env stays the owner's.
    choice = Choice(registry, headset, prefs=localconfig.ROOT / PREFS, save=not demo)
    view = ADVANCED_VIEW if advanced else INSTALLER_VIEW
    try:
        window = Window(
            customtkinter,
            engine_for(headset),
            side,
            view=view,
            choice=choice,
            engines=engine_for,
            jobs=DemoBackupJob if demo else BackupJob,
        )
    except tk.TclError as exc:
        print(f"error: no display for the window ({exc}); the command line does everything it does", file=sys.stderr)
        return 2
    rough = rough_fonts(window.root)
    if rough:
        print(f"note: {rough}", file=sys.stderr)
    window.root.mainloop()
    return 0


#: What a Linux process has loaded: a Tk with Xft has libXft there.
MAPS = Path("/proc/self/maps")


def font_system(root: object, maps: Path = MAPS) -> str:
    """Return how ROOT's Tk draws fonts on X11: "xft" (smooth) or "x11" (bitmap), "" elsewhere or unknown.

    Tk 9 says so itself (tk::pkgconfig); Tk 8.6 does not, but one built with Xft has libXft loaded.
    """
    tk = root.tk  # ty: ignore[unresolved-attribute]  # a Tk root
    try:
        return str(tk.call("::tk::pkgconfig", "get", "fontsystem"))
    except Exception:  # noqa: BLE001, S110  # Tk 8.6 has no such key: look at what is loaded
        pass
    if str(tk.call("tk", "windowingsystem")) != "x11":
        return ""
    try:
        loaded = maps.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "xft" if "libXft" in loaded else "x11"


#: What makes a Linux Tk draw smooth fonts, by distribution.
TK_PACKAGES = "python3-tk (Debian, Ubuntu, openSUSE), python3-tkinter (Fedora), tk (Arch)"


def rough_fonts(root: object) -> str:
    """Return why ROOT's Tk draws rough fonts and shapes (an X11 Tk without Xft), or "" when it does not.

    uv's own Python on Linux has such a Tk: bitmap fonts, and CustomTkinter's corners and shapes,
    drawn with a font, break. A distribution's Tk draws with Xft; install.sh takes it where it is.
    """
    if font_system(root) != "x11":
        return ""
    return (
        "this Python's Tk draws without smooth fonts (no Xft), so the window looks rough. Install your "
        f"distribution's Tk for Python ({TK_PACKAGES}) and start install.sh again."
    )
