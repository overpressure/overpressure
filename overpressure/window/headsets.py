# SPDX-License-Identifier: Unlicense
"""Which headset the window works on: the headset page it opens on, the last choice kept as a window preference.

The window always opens on "Which headset do you have?", also with one headset: only an explicit
--device skips it. The headset chosen last is marked and listed first; the choice is a preference
of the window (out/window.json), not a setting of the command line, so .env stays the owner's.
Each headset with an installer has its plan read on that page (install --plan, which runs
nothing), so its entry says where it stopped last time. The header's back link returns to the
page from everywhere. Nothing here needs Tk: app.py draws it, and tests drive it without a display.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from overpressure.headset import Command, Headset, Info, Step, tools_step
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


@dataclass(frozen=True)
class Entry:
    """A headset as the page lists it: its name, SoC and summary, whether it has an installer, whether it was last."""

    codename: str
    name: str
    about: str
    installer: bool
    last: bool = False

    @property
    def mode(self) -> str:
        """Return what choosing it opens, in words."""
        return "Installer and Advanced mode" if self.installer else "Advanced mode only: it has no installer yet"


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
        self, registry: Registry, headset: Headset | None = None, *, prefs: Path | None = None, save: bool = True
    ) -> None:
        """Choose among REGISTRY's headsets, HEADSET already chosen (--device); PREFS holds the last choice.

        Without PREFS (tests) nothing is read or written; without SAVE (the demo) the file is read
        but the choice stays in this window.
        """
        self.registry = registry
        self.headset = headset
        self.prefs = prefs
        self.save = save
        found = read_last(prefs)
        self.last = found if found in registry.headsets else ""  # an unknown codename marks nothing
        self._installers: dict[str, bool] = {}

    def asking(self) -> bool:
        """Tell whether the window opens on the headset page: none chosen by --device, and some to choose from."""
        return self.headset is None and bool(self.registry.headsets)

    def installer(self, headset: Headset | None = None) -> bool:
        """Tell whether HEADSET (else the chosen one) has an installer; without a headset the view stays."""
        headset = headset or self.headset
        if headset is None:
            return True
        codename = headset.info.codename
        if codename not in self._installers:
            self._installers[codename] = bool(tuple(headset.install_plan()))
        return self._installers[codename]

    def entries(self) -> list[Entry]:
        """Return the registered headsets as the page lists them: the one chosen last first, then by name."""
        found = []
        for codename, headset in self.registry.headsets.items():
            info = headset.info
            about = " · ".join(x for x in (info.soc, info.summary) if x)
            found.append(Entry(codename, info.name, about, self.installer(headset), codename == self.last))
        return sorted(found, key=lambda e: (not e.last, e.name.lower(), e.codename))

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
