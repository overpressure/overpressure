# SPDX-License-Identifier: Unlicense
"""Which headset the window works on: the headset page it opens on, the last choice kept as a window preference.

The window always opens on "Which headset do you have?", also with one headset: only an explicit
--device skips it. The headset chosen last is marked and listed first; the choice is a preference
of the window (out/window.json), not a setting of the command line, so .env stays the owner's.
Each headset with an installer has its plan read on that page (install --plan, which runs
nothing), so its entry says where it stopped last time. The header's back link returns to the
page from everywhere. Every headset of the catalogue is listed, downloaded or not: each entry says
which release devices/ holds and downloads another (Download, on a thread: the newest stable, the
newest beta, main as a preview, or an older release). Nothing here needs Tk: app.py draws it, and
tests drive it without a display.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from overpressure import REPO, catalogue
from overpressure.headset import Command, Headset, Info, Step, discover, tools_step
from overpressure.window.advanced import HEADSET, busy
from overpressure.window.installer import DONE, FAILED, Model, drain

if TYPE_CHECKING:
    from collections.abc import Sequence

    from overpressure.headset import Registry
    from overpressure.window.installer import Engine

#: The window's preferences, under the repository root (out/ is private and ignored).
PREFS = Path("out") / "window.json"
#: The preference that names the headset chosen last.
LAST = "last_headset"
QUESTION = "Which headset do you have?"
INTRO = (
    "The installer and Advanced mode work on the headset you choose here. "
    "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK} Headsets at the top of the window comes back to this page."
)
LAST_USED = "Last used"
NOT_DOWNLOADED = "Not downloaded yet: Download gets the release chosen beside it"
#: The release choices every headset has, before (or without) its list of releases.
NEWEST, NEWEST_BETA, PREVIEW_MAIN = "Newest stable", "Newest beta", "Preview (main, untested)"
CHANNEL_OF = {NEWEST: catalogue.STABLE, NEWEST_BETA: catalogue.BETA, PREVIEW_MAIN: catalogue.PREVIEW}


@dataclass(frozen=True)
class Entry:
    """A headset as the page lists it: its name, SoC and summary, whether it has an installer, whether it was last."""

    codename: str
    name: str
    about: str
    installer: bool
    last: bool = False
    release: str = ""  #: what devices/ holds of it, in words ("release 1.0", "development checkout")
    refused: str = ""  #: why the release it holds cannot run here
    downloaded: bool = True

    @property
    def mode(self) -> str:
        """Return what choosing it opens, in words."""
        if not self.downloaded:
            return NOT_DOWNLOADED
        if self.refused:
            return self.refused
        return "Installer and Advanced mode" if self.installer else "Advanced mode only: it has no installer yet"

    @property
    def usable(self) -> bool:
        """Tell whether choosing it opens it: downloaded, in a release this overpressure runs."""
        return self.downloaded and not self.refused


def read_last(prefs: Path | None) -> str:
    """Return the codename PREFS names as chosen last, or "" when it is missing, broken or names none."""
    if prefs is None:
        return ""
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get(LAST) if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def write_last(prefs: Path, codename: str) -> None:
    """Keep CODENAME as chosen last in PREFS, with the file's other preferences; a failed write is no error."""
    data: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        found = json.loads(prefs.read_text(encoding="utf-8"))
        if isinstance(found, dict):
            data = found
    data[LAST] = codename
    with contextlib.suppress(OSError):  # a preference: the window works without it
        prefs.parent.mkdir(parents=True, exist_ok=True)
        prefs.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))  # write_text has newline= from 3.10 on


class Choice:
    """The window's headset: chosen, or the headset page still open; the one chosen last."""

    def __init__(
        self,
        registry: Registry,
        headset: Headset | None = None,
        *,
        prefs: Path | None = None,
        save: bool = True,
        known: dict[str, catalogue.Known] | None = None,
    ) -> None:
        """Choose among REGISTRY's headsets, HEADSET already chosen (--device); PREFS holds the last choice.

        Without PREFS (tests) nothing is read or written; without SAVE (the demo) the file is read
        but the choice stays in this window. KNOWN is the catalogue the page downloads from (the demo
        and tests: none).
        """
        self.known = catalogue.CATALOGUE if known is None else known
        self.releases = Releases()
        self.registry = registry
        self.headset = headset
        self.prefs = prefs
        self.save = save
        found = read_last(prefs)
        self.last = found if found in registry.headsets else ""  # an unknown codename marks nothing
        self._installers: dict[str, bool] = {}

    def asking(self) -> bool:
        """Tell whether the window opens on the headset page: none chosen by --device, some to choose or download."""
        return self.headset is None and bool(self.registry.headsets or self.known)

    def installer(self, headset: Headset | None = None) -> bool:
        """Tell whether HEADSET (else the chosen one) has an installer; without a headset the view stays."""
        headset = headset or self.headset
        if headset is None:
            return True
        codename = headset.info.codename
        if codename not in self._installers:
            self._installers[codename] = bool(tuple(headset.install_plan()))
        return self._installers[codename]

    def entries(self, root: Path = REPO) -> list[Entry]:
        """Return the headsets as the page lists them: the one chosen last first, then by name.

        The catalogue's that devices/ (under ROOT) does not hold, or holds in a release this
        overpressure cannot run, follow: they only download.
        """
        found = []
        for codename, headset in self.registry.headsets.items():
            info = headset.info
            about = " · ".join(x for x in (info.soc, info.summary) if x)
            release = catalogue.installed(codename, root)
            found.append(Entry(codename, info.name, about, self.installer(headset), codename == self.last, release))
        for codename, item in self.known.items():
            if codename not in self.registry.headsets:
                refused = self.registry.refused.get(codename, "")
                release = catalogue.installed(codename, root) if refused else ""
                downloaded = bool(refused)
                about = item.repository
                found.append(
                    Entry(
                        codename,
                        item.name,
                        about,
                        installer=False,
                        release=release,
                        refused=refused,
                        downloaded=downloaded,
                    )
                )
        return sorted(found, key=lambda e: (not e.usable, not e.last, e.name.lower(), e.codename))

    def rediscover(self) -> None:
        """Read devices/ again after a download: the headsets' modules afresh, in the same registry."""
        for name in [m for m in sys.modules if m == "devices" or m.startswith("devices.")]:
            del sys.modules[name]
        found = discover()
        self.registry.headsets.clear()
        self.registry.headsets.update(found.headsets)
        self.registry.refused.clear()
        self.registry.refused.update(found.refused)
        self._installers.clear()
        if self.headset is not None:
            self.headset = self.registry.headsets.get(self.headset.info.codename)

    @staticmethod
    def refusal(*, installer_running: bool, step_running: bool) -> str:
        """Return why the headset cannot change now (something runs), or "" when it can."""
        return busy(HEADSET, installer_running=installer_running, step_running=step_running)

    def choose(self, codename: str) -> Headset:
        """Choose the headset CODENAME; it is the last one from now on (in PREFS, unless the demo's)."""
        self.headset = self.registry.get(codename)
        self.last = codename
        if self.prefs is not None and self.save:
            write_last(self.prefs, codename)
        return self.headset


def release_choices(found: list[catalogue.Release] | None) -> list[str]:
    """Return the page's release choices: the channels, then each published release's version (FOUND)."""
    return [NEWEST, NEWEST_BETA, PREVIEW_MAIN, *(r.version for r in found or ())]


class Download:
    """A headset's release downloaded into devices/ on a thread (catalogue.add), for the headset page."""

    def __init__(self, codename: str, wanted: str, add: Callable[..., catalogue.Release] = catalogue.add) -> None:
        """Download CODENAME's WANTED release (one of release_choices); start() starts it."""
        self.codename, self.wanted, self.add = codename, wanted, add
        self.error = ""
        self.release: catalogue.Release | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> Download:
        """Start the download; return it."""
        self.thread.start()
        return self

    def _run(self) -> None:
        channel = CHANNEL_OF.get(self.wanted, catalogue.STABLE)
        version = "" if self.wanted in CHANNEL_OF else self.wanted
        try:
            self.release = self.add(self.codename, channel=channel, version=version, log=lambda _line: None)
        except (LookupError, RuntimeError, ValueError, OSError) as exc:
            self.error = str(exc) or type(exc).__name__

    @property
    def done(self) -> bool:
        """Tell whether the download has ended, well or not."""
        return not self.thread.is_alive()

    def line(self) -> str:
        """Return what the page says about it."""
        if not self.done:
            what = self.wanted.lower() if self.wanted in CHANNEL_OF else f"release {self.wanted}"
            return f"Downloading {what}..."
        if self.error:
            return f"The download failed: {self.error}"
        return f"Downloaded release {self.release.version}" if self.release else ""


class Releases:
    """Each catalogue headset's published releases, read once on a thread for the headset page's choices."""

    def __init__(self, get: Callable[[catalogue.Known], list[catalogue.Release]] = catalogue.releases) -> None:
        """Read releases with GET (the catalogue's, from GitHub)."""
        self.get = get
        self.found: dict[str, list[catalogue.Release]] = {}
        self.thread: threading.Thread | None = None

    def start(self, known: dict[str, catalogue.Known] | None = None) -> None:
        """Start reading KNOWN's releases (the catalogue's), once; without a network the channels stay."""
        if self.thread is not None:
            return
        known = catalogue.CATALOGUE if known is None else known

        def run() -> None:
            for codename, item in known.items():
                with contextlib.suppress(LookupError, ValueError, OSError):
                    self.found[codename] = self.get(item)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    @property
    def done(self) -> bool:
        """Tell whether the releases have been read (or the reading has ended)."""
        return self.thread is not None and not self.thread.is_alive()

    def choices(self, codename: str) -> list[str]:
        """Return CODENAME's release choices as known so far."""
        return release_choices(self.found.get(codename))


def last_time(model: Model) -> str:
    """Return the headset page's line on a probed plan: where the recorded run stopped, or "" for none."""
    if not model.recorded():
        return ""
    stage = model.stage()
    if stage is None or stage.status == DONE:
        return "Last time: every stage was done"
    if stage.status == FAILED:
        return f"Last time: a step of {stage.title} failed"
    return f"Last time: stopped in {stage.title}"


class Probe:
    """A headset's plan read for the headset page (install --plan, which runs nothing): its engine and its model.

    Choosing the headset takes the probe over as the installer's own, finished or still under way;
    going back to the page hands a probe still under way back.
    """

    def __init__(self, engine: Engine, model: Model | None = None) -> None:
        """Take ENGINE's plan probe into MODEL (else a new one): start() starts it, or it is one under way."""
        self.engine = engine
        self.model = model or Model()
        self.done = False

    def start(self) -> Probe:
        """Start reading the plan; return the probe."""
        self.engine.start(["--plan"])
        return self

    def receive(self) -> bool:
        """Apply what the engine has sent; return whether the probe has ended just now."""
        if self.done:
            return False
        for kind, value in drain(self.engine.items):
            if kind == "exit":
                self.model.probed()
                self.done = True
                return True
            self.model.feed(str(value))
        return False

    def line(self) -> str:
        """Return the page's line on where the headset stopped, once the plan is read."""
        return last_time(self.model) if self.done else ""


class DemoHeadset(Headset):
    """--demo-headsets' second headset: no installer, a few steps that run nothing in the demo."""

    info = Info(
        "example",
        "Example headset",
        "Example SoC",
        "a stand-in for a second headset in the demo: no installer, Advanced mode only",
    )

    def commands(self) -> dict[str, Command]:
        """Return its one command word; the demo never runs it."""
        return {"build": Command("image", "build its image")}

    def steps(self) -> Sequence[Step]:
        """Return a few steps for Advanced mode."""
        return (
            tools_step(),
            Step("image", "Boot image", "Image", ("build",), "Its boot image (the demo runs nothing)."),
            Step("flash", "Flash", "Headset", ("build", "--flash"), "Writes the boot image.", writes=True),
        )
