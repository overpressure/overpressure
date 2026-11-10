# SPDX-License-Identifier: Unlicense
"""The logo in the window: PNG renderings of docs/logo's drawings, picked for the display's scaling.

Tk draws no SVG and scales no image, so icons/ holds each picture at the sizes the window asks for
(docs/logo/README.md says how they are rendered): the app icon for the title bar, the taskbar and
the dock (the small drawing up to 48 px), the header's mark, and the app icon on the headset page in
its light and dark tiles. A size in points becomes the rendering nearest to it in pixels. No widgets
here, so the tests check the files and the choice without a display.
"""

from __future__ import annotations

import contextlib
import struct
import sys
from pathlib import Path

ICONS = Path(__file__).resolve().parent / "icons"
#: Windows' icon: every size its title bar and taskbar ask for, in one file.
ICO = ICONS / "overpressure.ico"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
#: The app icon's renderings (pixels): the small drawing up to 48, the full one from 64.
APP = (16, 20, 24, 32, 40, 48, 64, 80, 96, 112, 128, 144, 160, 192, 256)
SMALL_UP_TO = 48
#: The light tile's renderings, for the headset page in the light appearance.
APP_LIGHT = (64, 80, 96, 112, 128, 144, 160, 192)
#: The mark's renderings by height (pixels); each is twice as wide as high.
MARK = (18, 23, 27, 32, 36, 41, 45, 54)
#: What Tk's iconphoto gets (the dock on macOS, the window lists on Linux).
WINDOW_ICON = (16, 32, 48, 64, 128, 256)
#: Windows shows a window under the program that runs it (python.exe) unless the process names its own app.
APP_ID = "overpressure.installer"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def nearest(sizes: tuple[int, ...], want: float) -> int:
    """Return the size in SIZES nearest to WANT pixels, the larger one on a tie."""
    return min(sizes, key=lambda size: (abs(size - want), -size))


def app_icon(points: int, scaling: float, *, light: bool = False) -> Path:
    """Return the app icon's rendering for POINTS at SCALING, on the LIGHT tile or the dark one."""
    if light:
        return ICONS / f"app-light-{nearest(APP_LIGHT, points * scaling)}.png"
    return ICONS / f"app-{nearest(APP, points * scaling)}.png"


def mark(height: int, scaling: float) -> Path:
    """Return the mark's rendering for HEIGHT points at SCALING."""
    return ICONS / f"mark-{nearest(MARK, height * scaling)}.png"


def window_icons() -> list[Path]:
    """Return the window icon's renderings for Tk's iconphoto, smallest first."""
    return [ICONS / f"app-{size}.png" for size in WINDOW_ICON]


def png_size(path: Path) -> tuple[int, int]:
    """Return the width and height of the PNG at PATH."""
    head = path.read_bytes()[:24]
    if head[:8] != PNG_SIGNATURE or head[12:16] != b"IHDR":
        msg = f"{path} is not a PNG file"
        raise ValueError(msg)
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def ico_sizes(path: Path) -> list[int]:
    """Return the sizes an ICO file at PATH holds (256 is stored as 0)."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        msg = f"{path} is not an icon file"
        raise ValueError(msg)
    return [data[6 + 16 * i] or 256 for i in range(count)]


def name_the_app() -> None:
    """On Windows, give the process its own app ID, so that the taskbar shows the window's icon, not Python's."""
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415  # Windows only

        with contextlib.suppress(OSError, AttributeError):  # an old Windows without app IDs keeps Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
