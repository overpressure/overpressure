# SPDX-License-Identifier: Unlicense
"""overpressure: Linux and a SteamOS-compatible userspace on standalone VR headsets.

The host side, pure Python (3.9+): the standard library, and the few pinned packages pyproject.toml names,
each imported where it is used with a fallback, so it runs on Windows, Linux and macOS: the formats every
Android-based headset shares (boot images, AVB footers, sparse images, OTA payloads, cpio, Alpine packages),
the registry of headsets in devices/, and the command line and the window that drive each headset's own
workflow (devices/<codename>/tooling).
"""

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

#: overpressure's own files: this package's folder's parent (a checkout, a release's folder, the app's engine).
CODE = Path(__file__).resolve().parent.parent
#: The app's engine marks its root with this file (scripts/bundle-app.py): its data goes to the owner's own folder.
BUNDLED = ".bundled"


def data_root(
    code: Path = CODE,
    environ: Mapping[str, str] = os.environ,
    platform: str = sys.platform,
    home: Callable[[], Path] = Path.home,
) -> Path:
    r"""Return where .env, the private folders and the headsets go: OVERPRESSURE_HOME, else the app's data folder.

    A checkout and a release's folder keep everything below their own root, as ever. The app's engine (@p code
    marked BUNDLED) lies inside the installed app, which an update replaces and an uninstall removes, so its data
    goes to the owner's: on Windows %LOCALAPPDATA%\overpressure-data (the per-user installer's own folder is
    %LOCALAPPDATA%\overpressure, and its "delete app data" removes the app identifier's), on macOS Application
    Support, elsewhere $XDG_DATA_HOME (~/.local/share).
    """
    if environ.get("OVERPRESSURE_HOME"):
        return Path(environ["OVERPRESSURE_HOME"]).expanduser().resolve()
    if not (code / BUNDLED).is_file():
        return code
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA") or home() / "AppData" / "Local") / "overpressure-data"
    if platform == "darwin":
        return home() / "Library" / "Application Support" / "overpressure"
    return Path(environ.get("XDG_DATA_HOME") or home() / ".local" / "share") / "overpressure"


#: The data root: .env, the private, ignored folders (out/, cache/, assets/, backup/) and the headsets in devices/.
#: In a checkout and a release's folder the same as CODE.
REPO = data_root()
if REPO != CODE:
    REPO.mkdir(parents=True, exist_ok=True)
    # Children run with the data root as their folder: overpressure's own files come from CODE.
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(CODE), os.environ.get("PYTHONPATH", "")) if p)
