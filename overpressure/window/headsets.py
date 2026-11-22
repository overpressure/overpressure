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
from overpressure import version as own_version
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
    "Open your headset to install, update or restore it. "
    "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK} Headsets at the top of the window brings you back here."
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
    """Return the page's release choices: the channels, then each published release (FOUND) that runs here."""
    return [NEWEST, NEWEST_BETA, PREVIEW_MAIN, *(r.version for r in found or () if not r.problem)]


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
        return f"Downloaded release {own_version.display(self.release.version)}" if self.release else ""


def default_choice(found: list[catalogue.Release] | None, record: dict) -> str:
    """Return the release a card offers first: what its download recorded, else the newest stable, else beta.

    RECORD is the headset's .release.json (catalogue.record_of): a pinned version, else its channel. Without
    one, the newest stable; while only betas run here, the newest beta, so the first choice is not refused.
    """
    choices = release_choices(found)
    if record.get("pinned") and str(record.get("tag", "")).removeprefix("v") in choices:
        return str(record["tag"]).removeprefix("v")
    by_channel = {channel: label for label, channel in CHANNEL_OF.items()}
    if record.get("channel") in by_channel:
        return by_channel[str(record["channel"])]
    runs = [r for r in found or () if not r.problem]
    return NEWEST_BETA if runs and all(r.prerelease for r in runs) else NEWEST


def update_line(newer: catalogue.Release | None) -> str:
    """Return a card's line on an update its recorded channel offers, "" for none."""
    if newer is None:
        return ""
    if newer.tag == "main":
        return "Update available: main has changed"
    return f"Update available: release {own_version.display(newer.version)}"


#: The status pills' tones (overpressure/window/theme.py's names).
NEUTRAL, INFO, SUCCESS, WARNING, DANGER = "neutral", "info", "success", "warning", "danger"
OTHER_VERSIONS, HIDE_VERSIONS = "Other versions", "Hide versions"
#: What the release choice says about its choices, under it.
VERSIONS_NOTE = "Newest stable is recommended. Beta and preview versions are for testing and can break things."


def support_name(entry: Entry) -> str:
    """Return what the page calls ENTRY's files on this computer: "PICO 4 support"."""
    return f"{entry.name} support"


@dataclass(frozen=True)
class Footer:
    """A headset card's support panel, in plain words: the headset's support on this computer, not the headset.

    What overpressure downloads for a headset (its release in devices/) lives on this computer; the
    headset itself only changes when the installer runs. So the panel is titled as the support on this
    computer, and each download says what it changes and what it does not. It shows at most one status
    and one action: an update to download, the download running, or trying again; other versions stay
    behind OTHER_VERSIONS unless nothing is there to choose otherwise.
    """

    heading: str  #: "PICO 4 support on this computer"
    title: str  #: "Version 1.0-beta.2", "Not downloaded yet", "Developer copy"
    detail: str = ""  #: beside the title: "kept at this version", "updated with Git"
    status: str = ""  #: the pill: up to date, a new version, downloading, downloaded, failed
    tone: str = NEUTRAL
    action: str = ""  #: the one button: "Download 1.0-beta.3", "Download PICO 4 support", "Try again"
    action_choice: str = ""  #: what that button downloads (a release choice), "" for the card's release choice
    caption: str = ""  #: under it: what the download changes, or what to do next
    busy: bool = False  #: a download runs: a progress bar, the buttons off
    error: str = ""  #: why the last download failed
    picker: bool = False  #: the release choice shows without asking
    versions: bool = True  #: other versions can be downloaded (not into a developer copy)


def offered(record: dict) -> str:
    """Return the release choice an update downloads: the newest of the download's recorded channel."""
    return {channel: label for label, channel in CHANNEL_OF.items()}.get(str(record.get("channel")), NEWEST)


def downloading(download: Download, newer: catalogue.Release | None, update: str) -> str:
    """Return what DOWNLOAD fetches: the version of the update NEWER when it is that (UPDATE), else its choice."""
    if newer is not None and download.wanted == update:
        return "main" if newer.tag == "main" else own_version.display(newer.version)
    return download.wanted.lower() if download.wanted in CHANNEL_OF else own_version.display(download.wanted)


def footer(  # noqa: C901, PLR0911, PLR0912  # one return per state the card can be in
    entry: Entry,
    record: dict,
    updates: dict[str, catalogue.Release | None],
    *,
    checking: bool,
    download: Download | None = None,
) -> Footer:
    """Return ENTRY's support panel from its download's RECORD (.release.json), the UPDATES read so far.

    CHECKING: the releases are still being read. DOWNLOAD: the card's download, running or ended.
    """
    heading = f"{support_name(entry)} on this computer"
    upgrade = f"Afterwards, open the {entry.name} and choose Upgrade to update the headset."
    if entry.release == catalogue.CHECKOUT:
        return Footer(heading, "Developer copy", "updated with Git", versions=False)
    if not entry.downloaded:
        title, detail = "Not downloaded yet", ""
    else:
        version = own_version.display(entry.release.removeprefix("release ").removeprefix("preview "))
        title = f"Version {version}" if entry.release else "Version unknown"
        detail = "kept at this version" if record.get("pinned") else ""
    picker = bool(entry.refused)
    if download is not None and not download.done:
        what = downloading(download, updates.get(entry.codename), offered(record))
        caption = "Only this computer changes." if not entry.downloaded else f"Only this computer changes. {upgrade}"
        return Footer(heading, title, detail, f"Downloading {what}", INFO, caption=caption, busy=True, versions=False)
    if download is not None and download.error:
        return Footer(
            heading, title, detail, "Download failed", DANGER, "Try again", download.wanted, error=download.error
        )
    if download is not None and download.release is not None:
        if getattr(download, "first", False):  # the first download: the installer can start
            caption = f"Ready: open the {entry.name} to install."
        else:
            caption = f"Your headset has not changed yet: open the {entry.name} and choose Upgrade to update it."
        return Footer(heading, title, detail, "Downloaded", SUCCESS, caption=caption, picker=picker)
    if not entry.downloaded:
        return Footer(
            heading,
            title,
            action=f"Download {support_name(entry)}",
            action_choice=offered(record),
            caption="Needed before you can install. Nothing on your headset changes.",
        )
    if entry.refused:
        return Footer(
            heading, title, detail, "Needs a newer overpressure", WARNING,
            caption="Download a version that works with this overpressure:", picker=True,
        )  # fmt: skip
    newer = updates.get(entry.codename)
    if newer is not None:
        version = "the newest main" if newer.tag == "main" else own_version.display(newer.version)
        return Footer(
            heading,
            title,
            detail,
            f"New version {'available' if newer.tag == 'main' else version}",
            INFO,
            f"Download {version}",
            offered(record),
            caption=f"Only this computer changes. {upgrade}",
        )
    if entry.codename in updates and record and not record.get("pinned"):
        return Footer(heading, title, detail, "Up to date", SUCCESS)
    if checking and record and not record.get("pinned"):
        return Footer(heading, title, detail, "Checking for updates", NEUTRAL)
    return Footer(heading, title, detail)


@dataclass(frozen=True)
class Offer:
    """What an upgrade would bring the headset: the version it runs, and the one on this computer."""

    have: str  #: "1.0-beta.2", "a preview of main"
    new: str  #: "1.0-beta.3", "a newer preview of main"
    newer: bool  #: the version here is newer, not an older one chosen on purpose


def version_words(identity: str) -> str:
    """Return a headset tooling's identity (catalogue.identity: "v1.0-beta.2 0123abcd", "main 0123abcd") in words."""
    if identity.startswith("release "):  # catalogue.installed's words, without a download's record
        return own_version.display(identity.removeprefix("release "))
    tag = identity.split(" ", 1)[0]
    return "a preview of main" if tag == "main" else own_version.display(tag.removeprefix("v"))


def upgrade_offer(installed: str, current: str) -> Offer | None:
    """Return what an upgrade brings when the headset runs another version (INSTALLED) than this computer has.

    INSTALLED is install.installed_release: what the headset's last finished install or upgrade ran with;
    CURRENT is catalogue.identity: the tooling on this computer now. None when they are the same, either
    is unknown, or either is a development checkout (its identity does not follow its commits).
    """
    if not installed or not current or installed == current or catalogue.CHECKOUT in (installed, current):
        return None
    have, new = version_words(installed), version_words(current)
    if have == new:  # main again, or the same tag rebuilt: what is here came later
        later = f"a newer {new.removeprefix('a ')}" if new.startswith("a ") else f"a newer {new}"
        return Offer(have, later, newer=True)
    old_key, new_key = catalogue.version_key(have), catalogue.version_key(new)
    return Offer(have, new, newer=old_key is None or new_key is None or new_key > old_key)


def upgrade_line(offer: Offer) -> str:
    """Return the Upgrade card's line on what it brings."""
    return f"Your headset runs {offer.have}. Upgrade brings it to {offer.new}, the version on this computer."


def status_line(  # noqa: PLR0911  # one line per state
    entry: Entry, model: Model | None, offer: Offer | None = None, *, installed: bool = False
) -> tuple[str, str]:
    """Return a card's line on the headset itself, in plain words, and its tone; ("", "") for none.

    MODEL: the headset's install plan as read on the page (its recorded run), None while unread. OFFER:
    what an upgrade would bring (upgrade_offer). INSTALLED: a finished install or upgrade put overpressure's
    system on it (install.installed_release).
    """
    if not entry.downloaded:
        return "", ""
    if entry.refused:
        return "This version cannot run with this overpressure", WARNING
    if not entry.installer:
        return "No guided installer yet: expert tools only", NEUTRAL
    stage = model.stage() if model is not None and model.recorded() else None
    if stage is not None and stage.status == FAILED:
        return f"The last run stopped with a problem in {stage.title}: open it to see what happened", WARNING
    if stage is not None and stage.status != DONE:
        return f"Unfinished: continue from {stage.title} where you left off", INFO
    if offer is not None and offer.newer:
        return f"Upgrade ready: your headset runs {offer.have}, this computer has {offer.new}", INFO
    if installed or (model is not None and model.recorded()):
        return "Installed with overpressure", SUCCESS
    return "Install, update or restore it with the guided installer", NEUTRAL


def open_label(entry: Entry, model: Model | None) -> str:
    """Return the card's button: Continue for an unfinished run, else Open."""
    if entry.installer and model is not None and model.recorded():
        stage = model.stage()
        if stage is not None and stage.status != DONE:
            return "Continue"
    return "Open"


class Releases:
    """Each catalogue headset's published releases and updates, read once on a thread for the headset page."""

    def __init__(
        self,
        get: Callable[[catalogue.Known], list[catalogue.Release]] = catalogue.releases,
        check: Callable[[str], catalogue.Release | None] = catalogue.update,
    ) -> None:
        """Read releases with GET and each download's update with CHECK (the catalogue's, from GitHub)."""
        self.get, self.check = get, check
        self.found: dict[str, list[catalogue.Release]] = {}
        self.updates: dict[str, catalogue.Release | None] = {}
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
                with contextlib.suppress(LookupError, ValueError, OSError):
                    self.updates[codename] = self.check(codename)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    @property
    def done(self) -> bool:
        """Tell whether the releases have been read (or the reading has ended)."""
        return self.thread is not None and not self.thread.is_alive()

    def choices(self, codename: str) -> list[str]:
        """Return CODENAME's release choices as known so far."""
        return release_choices(self.found.get(codename))

    def first(self, codename: str, root: Path = REPO) -> str:
        """Return the choice CODENAME's card offers first (default_choice)."""
        return default_choice(self.found.get(codename), catalogue.record_of(codename, root))


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
