# SPDX-License-Identifier: Unlicense
"""The window's widgets: the header, the headset question, the installer view, the panel for asks, Advanced mode.

The widgets only draw the models (headsets.Choice, installer.Model, advanced.Advanced) and pass on
what is pressed. Their look is kit.py's components on theme.py's tokens. CustomTkinter is handed
in as a module (window.main imports it), so this module imports without it.
"""

from __future__ import annotations

import collections
import contextlib
import importlib.metadata
import sys
import time
from typing import TYPE_CHECKING, Any, Callable

from overpressure import disclaimer
from overpressure.backup import ARCHIVE_SUFFIX
from overpressure.headset import INSTALL, AfterInstall
from overpressure.window import logo
from overpressure.window.advanced import INSTALLER, busy
from overpressure.window.advanced_view import AdvancedView
from overpressure.window.headsets import INTRO, LAST_USED, QUESTION, Choice, Download, Entry, Probe
from overpressure.window.installer import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED,
    STOP_GRACE,
    STOPPED,
    BackupJob,
    Model,
    Task,
    bar_state,
    drain,
)
from overpressure.window.kit import (
    HEADER,
    PRIMARY,
    PRIMARY_WIDTH,
    QUIET,
    SECONDARY,
    Code,
    Icon,
    Kit,
    Note,
    Reflow,
    Ring,
)
from overpressure.window.theme import (
    ACCENT,
    ACCENT_SOFT,
    ACCENT_TEXT,
    APP_ICON,
    BACKDROP,
    BADGE,
    BODY,
    BORDER,
    BORDER_STRONG,
    BORDER_SUBTLE,
    CARDS,
    CHEVRON,
    CONTENT,
    DANGER,
    DETAILS,
    HEADER_HEIGHT,
    HEADING,
    INFO,
    LABEL,
    LEVELS,
    LOGO,
    PAGE,
    PANEL,
    RADIUS_CONTROL,
    RADIUS_PANEL,
    RAISED,
    RING,
    SCREEN_MARGIN,
    SIDEBAR,
    SMALL,
    STEP_ICON,
    SUNKEN,
    TASK_ICON,
    TEXT,
    TEXT_MUTED,
    TEXT_SECONDARY,
    TITLE,
    WARNING,
    WARNING_TONE,
    WINDOW,
    WINDOW_MIN,
    Space,
)

if TYPE_CHECKING:
    import customtkinter as ctk

    from overpressure.headset import Headset
    from overpressure.window.advanced import Advanced
    from overpressure.window.installer import Engine

SUBTITLE = "Linux and SteamOS-compatible software for your {name}"
#: What the details show before the installer has run anything.
EMPTY_DETAILS = "Nothing has run yet. Each step, and the output of the programs it starts, shows here."
#: The window's views: the installer, Advanced mode, the headset page (which headset it works on) and the
#: headset's page of what to do (its workflows: install, upgrade, back to stock, and its backup).
INSTALLER_VIEW, ADVANCED_VIEW, CHOOSER_VIEW, ACTIONS_VIEW = "installer", "advanced", "chooser", "actions"
#: The headset's page of what to do.
ACTIONS_QUESTION = "What would you like to do?"
ACTIONS_INTRO = "Each choice opens its own steps, and each can be stopped and started again where it stopped."
BACKUP_TITLE = "Your backup"
BACKUP_THERE = "On this computer: {folder}. It is your way back: export a copy and keep it somewhere safe."
BACKUP_NONE = "No backup on this computer yet: the install makes one, or import the one you exported."
#: What the backup panel says while a job runs and when it has ended.
JOB_TITLES = {"export": "Exporting your backup", "import": "Importing a backup"}
JOB_TEXT = "This reads every file of the backup and checks it; it takes a few minutes."
#: The header's way back to the headset page, on every other page.
BACK = "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK} Headsets"
#: The stepper's caption over the stages.
STAGES = "Install"
#: A task's state in words at the right of its row, once it has one.
TASK_WORDS = {DONE: "done", SKIPPED: "already done", FAILED: "failed"}
#: CustomTkinter's appearance names.
LIGHT = "Light"


def version() -> str:
    """Return overpressure's version as installed (the start scripts install it), or "" when it is not."""
    try:
        return importlib.metadata.version("overpressure")
    except importlib.metadata.PackageNotFoundError:
        return ""


class Window:
    """The window: the header, and the installer view (stepper, the current stage or the end, the details).

    Advanced mode's view is an AdvancedView beside it; the header's link switches between the two.
    The window works on the headset CHOICE holds: unless --device chose one it opens on the headset
    page, and the header's back link returns there from every other page. Each headset keeps its
    engine and its details while another one is chosen; its recorded progress is the engine's.
    """

    #: The panel's text, bar and status line while it has a bar (a backup job's); None otherwise.
    panel_progress: dict[str, Any] | None = None
    #: When the clock last updated the running rows (time.monotonic()).
    ticked = 0.0

    def __init__(  # noqa: PLR0913  # the engine and the models; the view, the choice and the engine factory by name
        self,
        ctk_module: Any,  # noqa: ANN401  # the customtkinter module, imported lazily
        engine: Engine,
        advanced: Advanced,
        *,
        view: str = INSTALLER_VIEW,
        choice: Choice | None = None,
        engines: Callable[..., Engine] | None = None,
        jobs: Callable[[str, str, str], BackupJob] = BackupJob,
    ) -> None:
        """Build the window with the installer's ENGINE and ADVANCED mode's model, showing VIEW first.

        CHOICE is the headset page's model (else Advanced mode's headset, chosen); with its headset
        chosen VIEW shows at once, else after the first choice. ENGINES returns the installer's
        engine for a headset, and with workflow= for one of its other workflows (else ENGINE serves
        every one, and the page reads no plans ahead). JOBS makes a backup's export or import
        (codename, export or import, the file).
        """
        self.ctk: Any = ctk_module
        self.engine, self.advanced = engine, advanced
        self.choice = choice or Choice(advanced.steps.registry, advanced.steps.headset)
        self.engines = engines
        self.jobs = jobs
        self.engine_of: dict[tuple[str, str], Engine] = {}  # (codename, workflow) -> its engine, made once
        if self.choice.headset is not None:
            self.engine_of[self.choice.headset.info.codename, INSTALL] = engine
        self.probes: dict[str, Probe] = {}  # codename -> the headset page's read of its install's plan
        self.downloads: dict[str, Download] = {}  # codename -> its release's download, until its end is shown
        self.release_vars: dict[str, Any] = {}  # codename -> the release chosen on its card
        self.releases_shown = False  # the page shows the published releases (read once, on a thread)
        self.workflow_probes: dict[tuple[str, str], Probe] = {}  # the page of what to do: each workflow's
        self.logs: dict[tuple[str, str], list[str]] = {}  # its details, while another headset or workflow is chosen
        self.workflow = INSTALL  # what the installer view runs: the install, or another of the headset's workflows
        self.job: BackupJob | None = None  # a backup's export or import, while it runs and until its end is shown
        self.disclaimer: Any = None  # the disclaimer over the whole window, until it is accepted
        self.model = Model()
        self.view = INSTALLER_VIEW
        self.first_view = view  # the view the first choice opens (gui --advanced)
        self.closing = False  # the window closes once nothing runs any more
        self.stopped_at = 0.0  # when the stop was confirmed (finished), to end a lingering engine
        self.panel: Any = None  # the installer view's panel (an ask, or the window's own question), while shown
        self.panel_ask: dict[str, Any] | None = None  # the engine's ask the panel shows; None: the window's own
        self.secrets: list[Any] = []  # the form's entries, emptied after the answer
        self.details_shown = False
        self.task_rows: dict[str, dict[str, Any]] = {}
        ctk_module.set_appearance_mode("system")
        logo.name_the_app()  # before the window: Windows' taskbar shows the window's icon, not Python's
        self.root: ctk.CTk = ctk_module.CTk(fg_color=PAGE)
        self.kit = Kit(ctk_module, self.root)
        self.root.title("overpressure")
        self.root.geometry(self.first_size())
        self.root.minsize(*WINDOW_MIN)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # The plan is read first (install --plan), so the first page shows it; no button until then,
        # and nothing runs until the owner presses one.
        self.probing = True
        self._build()
        self.advanced_view = AdvancedView(self, self.root)
        self.render()
        self._open(view)
        self.show_disclaimer()
        self.root.after(100, self._tick)

    def _open(self, view: str) -> None:
        """Show the first page: the headset page, Advanced mode (no installer), else VIEW with its plan read."""
        if self.choice.asking():
            self.probing = False  # the page reads each headset's plan; the installer's waits for the choice
            self.show(CHOOSER_VIEW)
        elif not self.choice.installer():
            self.probing = False
            self.show(ADVANCED_VIEW)  # a headset without an installer: Advanced mode only
        else:
            self.show(view)
            self.engine.start(["--plan"])
        if not self.choice.asking():
            self.first_view = INSTALLER_VIEW  # shown already: a later choice opens the installer

    def show_disclaimer(self) -> None:
        """Cover the whole window with the disclaimer, on every start: accepted, or the window closes.

        Accepted, it holds for every program the window starts (the installer, a step, a backup job),
        as --accept-disclaimer does on the command line (overpressure/disclaimer.py).
        """

        def answer(accepted: bool, _values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            if not accepted:
                self.root.destroy()
                return
            disclaimer.accept()
            if self.disclaimer is not None:
                self.disclaimer.destroy()
            self.disclaimer = None

        self.disclaimer, _entries = self.build_panel(
            self.root,
            disclaimer.TITLE,
            disclaimer.TEXT,
            fields=[],
            accept=disclaimer.ACCEPT,
            decline=disclaimer.DECLINE,
            on_answer=answer,
        )
        self.disclaimer.lift()

    # -- layout ----------------------------------------------------------------------------
    def set_icon(self) -> None:
        """Give the window the app icon: in its title bar, the taskbar or the dock, and the window lists.

        Windows takes the .ico with every size it asks for (and CustomTkinter then leaves its own
        icon out); macOS and Linux take Tk's photo images.
        """
        if sys.platform == "win32":
            self.root.iconbitmap(default=str(logo.ICO))
        else:
            images = [self.kit.image(path) for path in logo.window_icons()]
            self.root.iconphoto(True, *images)  # noqa: FBT003  # Tk's own positional flag: later windows too

    def first_size(self) -> str:
        """Return the window's first size: WINDOW where the screen has room, else what it has, at least WINDOW_MIN."""
        scaling = float(self.ctk.ScalingTracker.get_window_scaling(self.root))
        screen = (self.root.winfo_screenwidth() / scaling, self.root.winfo_screenheight() / scaling)
        room = (screen[0] - SCREEN_MARGIN[0], screen[1] - SCREEN_MARGIN[1])
        width, height = (max(min(want, have), least) for want, have, least in zip(WINDOW, room, WINDOW_MIN))
        return f"{int(width)}x{int(height)}"

    def _build(self) -> None:
        kit = self.kit
        self.set_icon()
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)
        self._build_header()
        kit.line(self.root).grid(row=1, column=0, sticky="ew")
        self._build_chooser()
        self._build_actions()
        self._build_body()
        self._build_drawer()

    def _build_body(self) -> None:
        kit = self.kit
        self.body = body = kit.frame(self.root, PAGE)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self._build_stepper(body)
        kit.line(body, vertical=True).grid(row=0, column=1, sticky="ns")
        # The content: the page (scrolling when the window is short), then the action bar; panels cover both.
        self.content = content = kit.frame(body, PAGE)
        content.grid(row=0, column=2, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        self.scroll = kit.scroll(content, PAGE)
        self.scroll.frame.grid(row=0, column=0, sticky="nsew")
        self.scroll.frame.grid_columnconfigure(0, weight=1)
        page = kit.frame(self.scroll.frame)
        page.grid(row=0, column=0, sticky="ew", padx=Space.XXXL, pady=(Space.XXL, Space.XL))
        page.grid_columnconfigure(0, weight=1)
        self.badge = Icon(kit, page, BADGE)  # the end pages' mark over the title
        self.title = kit.label(page, size=TITLE, strong=True)
        self.title.grid(row=1, column=0, sticky="ew")
        self.summary = kit.label(page, size=BODY, colour=TEXT_SECONDARY, wrap=CONTENT)
        self.summary.grid(row=2, column=0, sticky="ew", pady=(Space.S, Space.XL))
        self.tasks = kit.frame(page)
        self.tasks.grid_columnconfigure(0, weight=1)
        self.note = Note(kit, page, wrap=CONTENT)
        kit.line(content).grid(row=1, column=0, sticky="ew")
        footer = kit.frame(content, RAISED)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        bottom = kit.frame(footer)
        bottom.grid(row=0, column=0, sticky="ew", padx=(Space.XXXL - Space.L, Space.XXXL), pady=Space.M)
        self.details_link = kit.button(bottom, "Show details", self.toggle_details, kind=QUIET)
        self.details_link.pack(side="left")
        self.primary = kit.button(bottom, "", self.press, kind=PRIMARY)
        self.stop_button = kit.button(bottom, "Stop", self.ask_to_stop)
        self.quiet = kit.frame(bottom)  # the start page's and the final page's other choices
        reflow = Reflow(kit, self.scroll.canvas, CONTENT, sides=(Space.XXXL, Space.XXXL), center=True)
        reflow.add(page)
        reflow.add(bottom, shift=Space.L)  # a quiet button's text lines up with the page's
        reflow.wrap(self.summary)

    def _build_stepper(self, body: Any) -> None:  # noqa: ANN401  # a CTkFrame
        """Build the left side: the whole run's ring over the stages (render_stepper fills them)."""
        kit = self.kit
        self.stepper = kit.frame(body, RAISED)
        self.stepper.configure(width=SIDEBAR)
        self.stepper.grid(row=0, column=0, sticky="ns")
        self.stepper.pack_propagate(False)  # noqa: FBT003  # Tk's own positional flag: the width stays
        # Over the stages: the meter of the whole run, the workflow's title and how many stages are done.
        self.overall = overall = kit.frame(self.stepper)
        self.ring = Ring(kit, overall, RING)
        self.ring.canvas.grid(row=0, column=0, rowspan=2, padx=(0, Space.M))
        self.workflow_label = kit.label(overall, size=BODY, strong=True)
        self.workflow_label.grid(row=0, column=1, sticky="sw")
        self.stages_label = kit.label(overall, size=SMALL, colour=TEXT_MUTED)
        self.stages_label.grid(row=1, column=1, sticky="nw")
        self.stage_list = kit.frame(self.stepper)
        self.stage_list.pack(fill="both", expand=True)

    def _build_drawer(self) -> None:
        kit = self.kit
        # The details: the child programs' output in a drawer under the view, its height a share of the window.
        self.drawer = kit.frame(self.root, SUNKEN)
        kit.line(self.drawer).pack(fill="x")
        self.details_block = Code(kit, self.drawer, "Details", height=DETAILS[0], wrap="word", radius=0)
        self.details_block.frame.pack(fill="both", expand=True)
        self.details = self.details_block.box
        self.details.configure(text_color=TEXT_SECONDARY)

    def _build_header(self) -> None:
        kit = self.kit
        header = kit.frame(self.root, RAISED)
        header.configure(height=HEADER_HEIGHT)
        header.grid(row=0, column=0, sticky="ew")
        header.pack_propagate(False)  # noqa: FBT003  # Tk's own positional flag: the same height on every page
        bar = kit.frame(header)
        bar.pack(fill="x", expand=True, padx=Space.XL)
        kit.picture(bar, logo.mark(LOGO[1], kit.scaling(self.root))).pack(side="left")  # the logo's headset
        kit.label(bar, "overpressure", size=HEADING, strong=True).pack(side="left", padx=(Space.M, Space.M))
        self.divider = kit.line(bar, vertical=True)
        self.divider.configure(height=LOGO[1])
        self.divider.pack(side="left", padx=(0, Space.M))
        self.subtitle = kit.label(bar, size=LABEL, colour=TEXT_MUTED)
        self.subtitle.pack(side="left")
        self.mode_link = kit.button(bar, "Advanced mode", self.switch, kind=HEADER)
        self.back_link = kit.button(bar, BACK, self.back, kind=HEADER)  # packed by render_link

    def _build_chooser(self) -> None:
        kit = self.kit
        self.chooser = kit.frame(self.root, PAGE)
        self.chooser.grid_columnconfigure(0, weight=1)
        self.chooser.grid_rowconfigure(0, weight=1)
        scroll = kit.scroll(self.chooser, PAGE)
        scroll.frame.grid(row=0, column=0, sticky="nsew")
        scroll.frame.grid_columnconfigure(0, weight=1)
        column = kit.frame(scroll.frame)
        column.grid(row=0, column=0, sticky="ew", padx=Space.XXXL, pady=(Space.XXL + Space.S, Space.XXL))
        column.grid_columnconfigure(0, weight=1)
        self._build_app_card(column).grid(row=0, column=0, sticky="w", pady=(0, Space.XL))
        kit.label(column, QUESTION, size=TITLE, strong=True).grid(row=1, column=0, sticky="ew")
        intro = kit.label(column, INTRO, size=BODY, colour=TEXT_SECONDARY, wrap=CARDS)
        intro.grid(row=2, column=0, sticky="ew", pady=(Space.S, Space.XL))
        self.headset_list = kit.frame(column)
        self.headset_list.grid(row=3, column=0, sticky="ew")
        self.headset_list.grid_columnconfigure(0, weight=1)
        self.headset_cards: dict[str, Any] = {}  # codename -> its card
        self.chooser_flow = Reflow(kit, scroll.canvas, CARDS, sides=(Space.XXXL, Space.XXXL), center=True)
        self.chooser_flow.add(column)
        self.chooser_flow.wrap(intro)

    def _build_app_card(self, parent: Any) -> Any:  # noqa: ANN401  # Tk widgets
        """Return the headset page's top: the app icon (its light tile in the light appearance), name and version."""
        kit = self.kit
        card = kit.frame(parent)
        scaling = kit.scaling(self.root)
        tiles = {light: logo.app_icon(APP_ICON, scaling, light=light) for light in (True, False)}
        icon = kit.picture(card, tiles[self.ctk.get_appearance_mode() == LIGHT])
        icon.grid(row=0, column=0, rowspan=2, padx=(0, Space.L))
        # The system's appearance can change while the window is open: the tile follows it.
        self.ctk.AppearanceModeTracker.add(lambda mode: kit.show(icon, tiles[mode == LIGHT]), icon)
        kit.label(card, "overpressure", size=HEADING, strong=True).grid(row=0, column=1, sticky="sw")
        shown = version()
        if shown:
            kit.label(card, f"Version {shown}", size=SMALL, colour=TEXT_MUTED).grid(row=1, column=1, sticky="nw")
        return card

    def render_chooser(self) -> None:
        """List the headsets: name, SoC and summary, what choosing one opens, where it stopped last time.

        The headset chosen last comes first, marked, with the page's one main button (so does the
        only headset); every entry opens with one click, on it or on its button.
        """
        kit = self.kit
        for child in self.headset_list.winfo_children():
            child.destroy()
        self.headset_cards.clear()
        self.chooser_flow.labels = [(label, inset) for label, inset in self.chooser_flow.labels if label.winfo_exists()]
        self.choice.releases.start(self.choice.known)
        entries = self.choice.entries()
        usable = [e for e in entries if e.usable]
        lead = next((e.codename for e in usable if e.last), usable[0].codename if len(usable) == 1 else "")
        for n, entry in enumerate(entries):
            card = kit.card(self.headset_list)
            card.grid(row=n, column=0, sticky="ew", pady=(0, Space.M))
            card.grid_columnconfigure(0, weight=1)
            heading = kit.frame(card)
            marks = [kit.label(heading, entry.name, size=HEADING, strong=True)]
            if entry.last:
                marks.append(kit.pill(heading, LAST_USED, INFO))
            for i, mark in enumerate(marks):
                mark.pack(side="left", padx=(Space.S if i else 0, 0))
            about = kit.label(card, entry.about, size=LABEL, colour=TEXT_SECONDARY)
            lines = [heading, about, *self._card_lines(card, entry)]
            parts = list(lines)
            if entry.release != "development checkout":  # Git updates a checkout
                parts.append(self._release_row(card, entry, primary=not entry.usable and not lead))
            last = len(parts) - 1
            for row, part in enumerate(parts):
                part.grid(
                    row=row,
                    column=0,
                    sticky="ew",
                    padx=(Space.XL, Space.S),
                    pady=(
                        Space.L + Space.XS if row == 0 else Space.S if row == 1 else Space.XS,
                        Space.L + Space.XS if row == last else 0,
                    ),
                )
            self.chooser_flow.wrap(about, inset=2 * Space.XL + PRIMARY_WIDTH + 2 * Space.L)  # the button beside it
            if entry.usable:
                primary = entry.codename == lead
                button = kit.button(
                    card, "Choose", lambda c=entry.codename: self.choose(c), kind=PRIMARY if primary else SECONDARY
                )
                button.grid(row=0, column=1, rowspan=len(parts), padx=(Space.L, Space.XL))
                self.hover_card(card, (*lines, *marks), entry.codename)
            self.headset_cards[entry.codename] = card

    def _card_lines(self, card: Any, entry: Entry) -> list[Any]:  # noqa: ANN401  # Tk widgets
        """Return the lines under a card's summary: what it opens, where it stopped, its release, a download."""
        kit = self.kit
        lines = [kit.label(card, entry.mode, size=LABEL, colour=TEXT if entry.installer else WARNING)]
        probe = self.probes.get(entry.codename)
        line = probe.line() if probe is not None else ""
        if line:
            lines.append(kit.label(card, line, size=SMALL, colour=TEXT_MUTED))
        if entry.release:
            lines.append(kit.label(card, entry.release.capitalize(), size=SMALL, colour=TEXT_MUTED))
        download = self.downloads.get(entry.codename)
        if download is not None:
            tone = WARNING if download.error else TEXT_MUTED
            lines.append(kit.label(card, download.line(), size=SMALL, colour=tone, wrap=CARDS))
        return lines

    def _release_row(self, card: Any, entry: Entry, *, primary: bool) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a card's release choice (the channels, then the published releases) and its Download button."""
        kit = self.kit
        row = kit.frame(card)
        values = self.choice.releases.choices(entry.codename)
        var = self.release_vars.get(entry.codename)
        if var is None or var.get() not in values:
            var = self.release_vars[entry.codename] = self.ctk.StringVar(value=values[0])
        kit.choice(row, values, var).pack(side="left")
        text = "Download" if entry.usable is False and not entry.refused else "Download this release"
        button = kit.button(
            row, text, lambda c=entry.codename: self.download(c), kind=PRIMARY if primary else SECONDARY
        )
        button.pack(side="left", padx=(Space.S, 0))
        download = self.downloads.get(entry.codename)
        if download is not None and not download.done:
            button.configure(state="disabled")
        return row

    def download(self, codename: str) -> bool:
        """Download the release chosen on CODENAME's card into devices/; refused while something runs."""
        if self.refused() or any(not d.done for d in self.downloads.values()):
            return False
        wanted = self.release_vars[codename].get()
        self.downloads[codename] = Download(codename, wanted).start()
        self.render_chooser()
        return True

    def downloaded(self) -> bool:
        """Take in the downloads that ended: devices/ read again, the page redrawn; whether any ended."""
        ended = [c for c, d in self.downloads.items() if d.done and not getattr(d, "taken", False)]
        for codename in ended:
            download = self.downloads[codename]
            download.taken = True  # ty: ignore[unresolved-attribute]  # the window's own mark on it
            if not download.error:
                self.choice.rediscover()
                self.probes.pop(codename, None)
        if ended and self.view == CHOOSER_VIEW:
            self.probe_headsets()
        return bool(ended)

    def _build_actions(self) -> None:
        kit = self.kit
        self.actions = kit.frame(self.root, PAGE)
        self.actions.grid_columnconfigure(0, weight=1)
        self.actions.grid_rowconfigure(0, weight=1)
        scroll = kit.scroll(self.actions, PAGE)
        scroll.frame.grid(row=0, column=0, sticky="nsew")
        scroll.frame.grid_columnconfigure(0, weight=1)
        column = kit.frame(scroll.frame)
        column.grid(row=0, column=0, sticky="ew", padx=Space.XXXL, pady=(Space.XXL + Space.S, Space.XXL))
        column.grid_columnconfigure(0, weight=1)
        self.actions_title = kit.label(column, ACTIONS_QUESTION, size=TITLE, strong=True)
        self.actions_title.grid(row=0, column=0, sticky="ew")
        intro = kit.label(column, ACTIONS_INTRO, size=BODY, colour=TEXT_SECONDARY, wrap=CARDS)
        intro.grid(row=1, column=0, sticky="ew", pady=(Space.S, Space.XL))
        self.action_list = kit.frame(column)
        self.action_list.grid(row=2, column=0, sticky="ew")
        self.action_list.grid_columnconfigure(0, weight=1)
        self.action_cards: dict[str, Any] = {}  # workflow -> its card
        self.actions_flow = Reflow(kit, scroll.canvas, CARDS, sides=(Space.XXXL, Space.XXXL), center=True)
        self.actions_flow.add(column)
        self.actions_flow.wrap(intro)

    def render_actions(self) -> None:
        """List what the chosen headset's installer does: a card per workflow, then its backup.

        Each card says what its workflow does and keeps, where it stopped last time, or why it
        cannot run now (then its button is off). The backup's card exports it or imports one.
        """
        kit = self.kit
        headset = self.choice.headset
        for child in self.action_list.winfo_children():
            child.destroy()
        self.action_cards.clear()
        self.actions_flow.labels = [(label, inset) for label, inset in self.actions_flow.labels if label.winfo_exists()]
        if headset is None:
            return
        self.actions_title.configure(text=ACTIONS_QUESTION)
        codename = headset.info.codename
        row = 0
        for workflow in headset.workflows():
            problem = headset.workflow_problem(workflow.key)
            probe = self.workflow_probes.get((codename, workflow.key))
            line = "" if problem or probe is None else probe.line()
            card = kit.card(self.action_list)
            card.grid(row=row, column=0, sticky="ew", pady=(0, Space.M))
            card.grid_columnconfigure(0, weight=1)
            heading = kit.frame(card)
            marks = [kit.label(heading, workflow.title, size=HEADING, strong=True)]
            if workflow.erases:
                marks.append(kit.pill(heading, "erases the headset", WARNING_TONE))
            for i, mark in enumerate(marks):
                mark.pack(side="left", padx=(Space.S if i else 0, 0))
            about = kit.label(card, workflow.summary, size=LABEL, colour=TEXT_SECONDARY)
            parts = [heading, about]
            if problem:
                parts.append(kit.label(card, problem, size=LABEL, colour=WARNING))
            elif line:
                parts.append(kit.label(card, line, size=SMALL, colour=TEXT_MUTED))
            self.place_parts(parts)
            self.actions_flow.wrap(about, inset=2 * Space.XL + PRIMARY_WIDTH + 2 * Space.L)
            if problem:
                self.actions_flow.wrap(parts[-1], inset=2 * Space.XL + PRIMARY_WIDTH + 2 * Space.L)
            button = kit.button(
                card,
                "Choose",
                lambda w=workflow.key: self.choose_workflow(w),
                kind=PRIMARY if row == 0 else SECONDARY,
            )
            button.grid(row=0, column=1, rowspan=len(parts), padx=(Space.L, Space.XL))
            if problem:
                button.configure(state="disabled")
            self.action_cards[workflow.key] = card
            row += 1
        self.render_backup_card(row)

    def place_parts(self, parts: list[Any]) -> None:
        """Grid a card's PARTS down its first column, with the cards' spacing."""
        last = len(parts) - 1
        for row, part in enumerate(parts):
            part.grid(
                row=row,
                column=0,
                sticky="ew",
                padx=(Space.XL, Space.S),
                pady=(
                    Space.L + Space.XS if row == 0 else Space.S if row == 1 else Space.XS,
                    Space.L + Space.XS if row == last else 0,
                ),
            )

    def render_backup_card(self, row: int) -> None:
        """Add the backup's card at ROW: where it is, and its export and import."""
        kit, headset = self.kit, self.choice.headset
        here = headset.backup_set() if headset is not None else None
        if here is None:
            return
        card = kit.card(self.action_list)
        card.grid(row=row, column=0, sticky="ew", pady=(Space.M, Space.M))
        card.grid_columnconfigure(0, weight=1)
        there = (here.folder / "manifest.json").is_file()
        text = BACKUP_THERE.format(folder=here.folder.name) if there else BACKUP_NONE
        about = kit.label(card, text, size=LABEL, colour=TEXT_SECONDARY)
        buttons = kit.frame(card)
        export = kit.button(buttons, "Export backup\N{HORIZONTAL ELLIPSIS}", self.export_backup)
        export.pack(side="left", padx=(0, Space.S))
        if not there:
            export.configure(state="disabled")
        kit.button(buttons, "Import backup\N{HORIZONTAL ELLIPSIS}", self.import_backup).pack(side="left")
        self.place_parts([kit.label(card, BACKUP_TITLE, size=HEADING, strong=True), about, buttons])
        buttons.grid_configure(pady=(Space.M, Space.L + Space.XS))
        self.actions_flow.wrap(about, inset=2 * Space.XL)
        self.action_cards["backup"] = card

    def hover_card(self, card: Any, parts: tuple[Any, ...], codename: str) -> None:  # noqa: ANN401  # Tk widgets
        """Outline CARD more strongly under the pointer; a click on it (or its PARTS) chooses CODENAME."""

        def outline(*, inside: bool) -> None:
            with contextlib.suppress(Exception):  # it may be gone with the list
                card.configure(border_color=BORDER_STRONG if inside else BORDER_SUBTLE)

        for widget in (card, *parts):
            widget.bind("<Enter>", lambda _e: outline(inside=True))
            widget.bind("<Leave>", lambda _e: outline(inside=False))
            widget.bind("<Button-1>", lambda _e: self.choose(codename))
            widget.configure(cursor="hand2")

    # -- the views -----------------------------------------------------------------------
    def show(self, view: str) -> None:
        """Show VIEW: the installer, Advanced mode, the headset page (which reads the headsets' plans) or what to do."""
        self.view = view
        self.body.grid_forget()
        self.drawer.grid_forget()
        self.advanced_view.frame.grid_forget()
        self.chooser.grid_forget()
        self.actions.grid_forget()
        if view == ADVANCED_VIEW:
            self.advanced_view.frame.grid(row=2, column=0, sticky="nsew")
            self.advanced_view.render()
        elif view == CHOOSER_VIEW:
            self.chooser.grid(row=2, column=0, sticky="nsew")
            self.probe_headsets()
            self.render_chooser()
        elif view == ACTIONS_VIEW:
            self.actions.grid(row=2, column=0, sticky="nsew")
            self.probe_workflows()
            self.render_actions()
        else:
            self.body.grid(row=2, column=0, sticky="nsew")
            if self.details_shown:
                self.show_drawer()
                self.fill_details()
        self.render_subtitle()
        self.render_link()

    def switch(self) -> None:
        """Switch between the installer and Advanced mode; whatever runs keeps running."""
        if self.view == ACTIONS_VIEW:
            self.show(ADVANCED_VIEW)
        elif self.view != CHOOSER_VIEW:
            self.show(INSTALLER_VIEW if self.view == ADVANCED_VIEW else ADVANCED_VIEW)

    def render_subtitle(self) -> None:
        """Name the headset the window works on beside the title; the headset page names none."""
        name = "" if self.view == CHOOSER_VIEW else self.model.headset.get("name", "")
        if not name and self.view != CHOOSER_VIEW and self.choice.headset is not None:
            name = self.choice.headset.info.name
        self.subtitle.configure(text=SUBTITLE.format(name=name) if name else "")
        self.divider.configure(fg_color=BORDER_SUBTLE if name else "transparent")

    def asks_what(self) -> bool:
        """Tell whether the chosen headset's installer does more than the install: the page of what to do shows."""
        headset = self.choice.headset
        return headset is not None and self.choice.installer(headset) and len(tuple(headset.workflows())) > 1

    def render_link(self) -> None:
        """Name the other view in the header (the installer's when it waits for an answer), and the way back.

        A headset without an installer has no other view. Every page but the headset page has the
        back link: to the page of what to do from the installer and Advanced mode where the headset
        has one, else (and from that page) to the headset page, also with one headset.
        """
        self.mode_link.pack_forget()
        self.back_link.pack_forget()
        if self.view == CHOOSER_VIEW:
            return
        waiting = self.view != INSTALLER_VIEW and bool(self.model.ask)
        if self.view in (INSTALLER_VIEW, ACTIONS_VIEW):
            text = "Advanced mode"
        else:
            text = "Installer (waiting for you)" if waiting else "Installer"
        self.kit.retext(self.mode_link, text)
        self.mode_link.configure(
            fg_color=ACCENT_SOFT if waiting else "transparent", text_color=ACCENT_TEXT if waiting else TEXT_SECONDARY
        )
        if self.view in (INSTALLER_VIEW, ACTIONS_VIEW) or self.choice.installer():
            self.mode_link.pack(side="right")
        back = BACK
        if self.view != ACTIONS_VIEW and self.asks_what() and self.choice.headset is not None:
            back = "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK} " + self.choice.headset.info.name
        self.kit.retext(self.back_link, back)
        self.back_link.pack(side="right", padx=(0, Space.XS))

    # -- the headset ---------------------------------------------------------------------
    def engine_for(self, headset: Headset, workflow: str = INSTALL) -> Engine:
        """Return the engine of HEADSET's WORKFLOW, the same one each time (the window's only one without ENGINES)."""
        if self.engines is None:
            return self.engine
        key = (headset.info.codename, workflow)
        if key not in self.engine_of:
            self.engine_of[key] = self.engines(headset) if workflow == INSTALL else self.engines(headset, workflow)
        return self.engine_of[key]

    def probe_headsets(self) -> None:
        """Read the plan of every headset with an installer that has none read yet: where it stopped last time.

        Only with an engine for each headset (ENGINES); the engines are idle while the page shows.
        """
        if self.engines is None:
            return
        for codename, headset in self.choice.registry.headsets.items():
            if codename not in self.probes and self.choice.installer(headset):
                self.probes[codename] = Probe(self.engine_for(headset)).start()

    def probe_workflows(self) -> None:
        """Read the plan of each of the chosen headset's workflows that has none read yet, for its card."""
        headset = self.choice.headset
        if self.engines is None or headset is None:
            return
        codename = headset.info.codename
        for workflow in headset.workflows():
            key = (codename, workflow.key)
            if key in self.workflow_probes:
                continue
            if workflow.key == INSTALL and codename in self.probes:
                self.workflow_probes[key] = self.probes[codename]  # the headset page's read of it
                continue
            self.workflow_probes[key] = Probe(self.engine_for(headset, workflow.key)).start()

    def refused(self) -> bool:
        """Say so and return True while the installer, a step or a backup's export or import runs."""
        refusal = self.choice.refusal(installer_running=self.model.running, step_running=self.advanced.run.running)
        if not refusal and self.job is not None and self.job.running:
            refusal = "The backup is being written. Wait for it to finish, then go on."
        if not refusal:
            return False
        if self.view == ADVANCED_VIEW:
            self.advanced_view.show_panel(
                "One thing at a time", refusal, accept="OK", decline="", on_answer=lambda **_answer: None
            )
        else:
            self.notice("One thing at a time", refusal)
        return True

    def back(self) -> bool:
        """Go one page back: from the installer and Advanced mode to what to do where the headset has it, else home."""
        if self.view in (INSTALLER_VIEW, ADVANCED_VIEW) and self.asks_what():
            if self.refused():
                return False
            self.leave()
            self.model, self.probing = Model(), False
            self.show(ACTIONS_VIEW)
            return True
        return self.to_headsets()

    def to_headsets(self) -> bool:
        """Go back to the headset page; refused (and said so) while the installer or a step runs.

        Nothing runs then, and nothing is lost: the headset's details stay for its next choice, a
        plan still being read goes on for the page, and its recorded progress is the engine's.
        """
        if self.view == CHOOSER_VIEW:
            return True
        if self.refused():
            return False
        self.leave()
        self.show(CHOOSER_VIEW)
        return True

    def leave(self) -> None:
        """Put the chosen workflow aside: its details kept, a plan probe under way handed to its page."""
        self.hide_panel()
        self.advanced_view.hide_panel()
        headset = self.choice.headset
        if headset is None or not self.model.stages:
            return
        codename = headset.info.codename
        self.logs[codename, self.workflow] = list(self.model.log)
        if self.probing and self.engines is not None:
            probe = Probe(self.engine, self.model)
            if self.workflow == INSTALL:
                self.probes[codename] = probe
            self.workflow_probes[codename, self.workflow] = probe
            self.probing = False
        elif self.engines is not None:
            # Its card says where it stands now, from a fresh read of its plan.
            self.workflow_probes.pop((codename, self.workflow), None)
            if self.workflow == INSTALL:
                self.probes.pop(codename, None)

    def choose(self, codename: str) -> bool:
        """Work on the headset CODENAME: what to do, its start page (its plan, read now or before), else Advanced mode.

        Refused while something runs. It becomes the headset used last (out/window.json, not .env).
        A headset whose installer does more than the install first asks what (the page of what to
        do); gui --advanced opens Advanced mode first, as before.
        """
        if self.choice.refusal(installer_running=self.model.running, step_running=self.advanced.run.running):
            return False
        if self.view != CHOOSER_VIEW:
            self.leave()
        headset = self.choice.choose(codename)
        self.advanced.steps.choose(codename)
        self.advanced_view.refill()
        self.hide_panel()
        self.stopped_at = 0.0
        if self.asks_what() and self.first_view != ADVANCED_VIEW:
            self.first_view = INSTALLER_VIEW
            self.workflow = INSTALL
            self.engine = self.engine_for(headset)
            self.model, self.probing = Model(), False
            self.show(ACTIONS_VIEW)
            return True
        return self.open_workflow(INSTALL)

    def choose_workflow(self, workflow: str) -> bool:
        """Open the chosen headset's WORKFLOW from the page of what to do; refused while it cannot run."""
        headset = self.choice.headset
        if headset is None:
            return False
        problem = headset.workflow_problem(workflow)
        if problem:
            self.notice("Not yet", problem)
            return False
        if self.refused():
            return False
        return self.open_workflow(workflow)

    def open_workflow(self, workflow: str) -> bool:
        """Show the chosen headset's WORKFLOW: its start page (its plan, read on a page or now), else Advanced mode."""
        headset = self.choice.headset
        if headset is None:
            return False
        codename = headset.info.codename
        self.workflow = workflow
        self.engine = self.engine_for(headset, workflow)
        view, self.first_view = self.first_view, INSTALLER_VIEW
        if self.choice.installer(headset):
            probe = self.workflow_probes.pop((codename, workflow), None)
            if workflow == INSTALL:
                probe = self.probes.pop(codename, None) or probe
            probe = probe or Probe(self.engine).start()
            self.model, self.probing = probe.model, not probe.done
            earlier = self.logs.pop((codename, workflow), [])
            if earlier:  # its details from before, then the plan's lines
                self.model.log = collections.deque([*earlier, *self.model.log], maxlen=self.model.log.maxlen)
            self.show(view)
        else:
            self.model, self.probing = Model(), False
            self.show(ADVANCED_VIEW)
        self.render()
        self.fill_details()
        return True

    def after_install(self) -> AfterInstall:
        """Return what the finished page says and offers: the chosen headset's own words for this workflow."""
        headset = self.choice.headset
        return headset.finished_page(self.workflow) if headset is not None else AfterInstall()

    def workflow_title(self) -> str:
        """Return the stepper's caption: the workflow's title."""
        headset = self.choice.headset
        workflows = {w.key: w.title for w in headset.workflows()} if headset is not None else {}
        return workflows.get(self.workflow, STAGES)

    def open_advanced(self, step: str | None = None) -> None:
        """Show Advanced mode, at STEP when the headset has it."""
        if step is not None:
            self.advanced.steps.select(step)
        self.show(ADVANCED_VIEW)

    # -- rendering -----------------------------------------------------------------------
    def render(self) -> None:
        """Show the model: the stepper, then the right side for the current view."""
        m = self.model
        self.render_subtitle()
        self.render_link()
        self.render_stepper()
        view = m.view()
        if view in (DONE, FAILED):
            self.render_end(view)
        elif view == "start":
            self.render_start()
        else:
            self.render_stage()
        if self.panel is not None and self.panel_ask is None:
            return  # the window's own question (stop?) stays until it is answered
        if view == "ask":
            if self.panel is None or self.panel_ask is not m.ask:
                self.show_ask()  # once per ask: a redraw keeps what was typed
        elif self.panel is not None:
            self.hide_panel()

    def render_stepper(self) -> None:
        """Draw the stages down the left side: a status icon each, joined by a line; the current one shaded."""
        kit = self.kit
        for child in self.stage_list.winfo_children():
            child.destroy()
        stages = self.model.stages
        if stages:
            self.overall.pack(fill="x", padx=Space.XL, pady=(Space.XL, Space.S), before=self.stage_list)
            self.workflow_label.configure(text=self.workflow_title())
            self.update_overall()
        else:
            self.overall.pack_forget()
        top = kit.frame(self.stage_list)
        top.pack(fill="x", padx=Space.M, pady=(Space.M if stages else Space.XL, 0))
        for n, stage in enumerate(stages):
            if n:  # the line between two stages, in the accent colour once the one above is done
                done = stages[n - 1].status in (DONE, SKIPPED)
                line = self.ctk.CTkFrame(
                    top, width=2 * BORDER, height=Space.M, corner_radius=0, fg_color=ACCENT if done else BORDER_STRONG
                )
                line.pack(anchor="w", padx=(Space.M + STEP_ICON // 2 - 1, 0))
            current = stage.id == self.model.current and self.model.view() == "stage"
            row = self.ctk.CTkFrame(
                top, fg_color=ACCENT_SOFT if current else "transparent", corner_radius=RADIUS_CONTROL
            )
            row.pack(fill="x")
            Icon(kit, row, STEP_ICON).set(stage.status).frame.pack(side="left", padx=(Space.M, 0), pady=Space.S)
            colour = DANGER if stage.status == FAILED else TEXT if current or stage.status != PENDING else TEXT_MUTED
            kit.label(row, stage.title, size=BODY, strong=current, colour=colour).pack(
                side="left", fill="x", padx=(Space.M, Space.S)
            )

    def update_overall(self) -> None:
        """Show how much of the whole run is done: the ring, and the stages done in words."""
        m = self.model
        self.ring.set(m.overall(m.clock()))
        self.stages_label.configure(text=f"{m.stages_done()} of {len(m.stages)} stages done")

    def clear_right(self) -> None:
        """Empty the task rows, the note, the end pages' mark and the final page's choices."""
        for row in self.task_rows.values():
            row["bar"].stop()
        for child in self.tasks.winfo_children():
            child.destroy()
        for child in self.quiet.winfo_children():
            child.destroy()
        self.quiet.pack_forget()
        self.stop_button.pack_forget()
        self.task_rows.clear()
        self.tasks.grid_forget()  # an emptied grid keeps its size: gone, it takes no room
        self.note.frame.grid_forget()
        self.badge.frame.grid_forget()
        self.summary.grid(pady=(Space.S, Space.XL))

    def show_note(self, text: str, tone: str) -> None:
        """Show the note box under the tasks: TEXT in TONE."""
        self.note.set(text, tone)
        self.note.frame.grid(row=4, column=0, sticky="ew", pady=(Space.L, 0))

    def render_stage(self) -> None:
        """Show the current stage: title, summary, its tasks, its last note, the button."""
        self.clear_right()
        m = self.model
        stage = m.stage()
        if stage is None:
            self.title.configure(text="Getting ready")
            self.summary.configure(text="Reading the install plan.")
        else:
            self.title.configure(text=stage.title)
            self.summary.configure(text=stage.summary)
            if stage.tasks:
                self.tasks.grid(row=3, column=0, sticky="ew")
                card = self.kit.card(self.tasks)
                card.pack(fill="x")
                for n, task in enumerate(stage.tasks):
                    if n:
                        self.kit.line(card).pack(fill="x", padx=Space.L)
                    self.task_row(card, task)
            text, tone = self.stage_note()
            if text:
                self.show_note(text, tone)
        self.set_primary("" if self.probing else m.button())
        label = m.stop_label()
        if label:
            self.kit.retext(self.stop_button, label)
            self.stop_button.configure(state="disabled" if m.stopping else "normal")
            beside = self.primary.winfo_manager() == "pack"  # the gap only beside the main button
            self.stop_button.pack(side="right", padx=(0, Space.S if beside else 0))

    def stage_note(self) -> tuple[str, str]:
        """Return the stage view's note and tone: the stop under way, how the last run stopped, else the last note."""
        m = self.model
        if m.stopping:
            later = " The step that writes to the headset finishes first." if m.stop_after_write else ""
            return "Stopping\N{HORIZONTAL ELLIPSIS}" + later, INFO
        if m.phase() == STOPPED and not m.running:
            where = str((m.finished or {}).get("where") or "")
            return (f"Stopped. {where}" if where else "Stopped. Continue picks up where it left off."), INFO
        stage = m.stage()
        if stage is None or not stage.notes:
            return "", INFO
        level, text = stage.notes[-1]
        return text, LEVELS.get(level, INFO)

    def render_start(self) -> None:
        """Show the start page: what an earlier run did, and the choices: continue, start over, Advanced mode."""
        self.clear_right()
        kit, m = self.kit, self.model
        self.title.configure(text="Welcome back")
        self.summary.configure(text=m.progress_text())
        self.tasks.grid(row=3, column=0, sticky="ew")
        kit.label(self.tasks, "Start over from\N{HORIZONTAL ELLIPSIS}", size=HEADING, strong=True).pack(
            fill="x", pady=(0, Space.S)
        )
        card = kit.card(self.tasks)
        card.pack(fill="x")
        for stage in m.restart_stages():
            row = self.ctk.CTkFrame(card, fg_color="transparent", corner_radius=RADIUS_CONTROL)
            row.pack(fill="x", padx=Space.XS + 2, pady=(Space.XS + 2, 0))
            icon = Icon(kit, row, TASK_ICON).set(stage.status)
            icon.frame.pack(side="left", padx=(Space.M - 2, Space.M), pady=Space.S + 2)
            parts = [icon.frame, kit.label(row, stage.title, size=BODY)]
            parts[-1].pack(side="left")
            if stage.writes:
                parts.append(kit.pill(row, "writes to the headset", WARNING_TONE))
                parts[-1].pack(side="left", padx=(Space.S, 0))
            parts.append(kit.label(row, CHEVRON, size=HEADING, colour=TEXT_MUTED))
            parts[-1].pack(side="right", padx=(0, Space.M))
            kit.clickable(row, tuple(parts), lambda s=stage.id: self.ask_restart(s), rest="transparent")
        kit.frame(card).pack(fill="x", pady=(0, Space.XS + 2))  # the card's last margin
        kit.button(self.quiet, "Advanced mode", self.open_advanced, kind=QUIET).pack(side="left")
        self.quiet.pack(side="left", padx=(Space.XS, 0))
        self.set_primary(m.button())

    def ask_restart(self, stage_id: str) -> None:
        """Ask before starting over from STAGE_ID; confirmed, the engine runs with --restart STAGE_ID."""
        title, text, warning = self.model.restart_question(stage_id)

        def answer(accepted: bool, _values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            if accepted:
                self.start(["--restart", stage_id])
            else:
                self.render()

        self.show_panel(
            title, text, fields=[], accept="Start over", decline="Cancel", on_answer=answer, warning=warning
        )

    def task_row(self, card: Any, task: Task) -> None:  # noqa: ANN401  # a CTkFrame
        """Add the row of TASK to CARD: its icon, title and estimate (or state), and a bar while it runs."""
        kit = self.kit
        row = kit.frame(card)
        row.pack(fill="x", padx=Space.L, pady=Space.M)
        row.grid_columnconfigure(1, weight=1)
        icon = Icon(kit, row, TASK_ICON)
        icon.frame.grid(row=0, column=0, sticky="w", padx=(0, Space.M))
        title = kit.label(row, task.title, size=BODY)
        title.grid(row=0, column=1, sticky="ew")
        right = kit.label(row, "", size=LABEL, colour=TEXT_MUTED)
        right.configure(anchor="e")
        right.grid(row=0, column=2, sticky="e", padx=(Space.M, 0))
        bar = kit.progress(row)
        status = kit.label(row, "", size=LABEL, colour=TEXT_MUTED)
        status.configure(anchor="w")
        self.task_rows[task.id] = {"icon": icon, "title": title, "right": right, "bar": bar, "status": status, "row": 1}
        self.update_task(task)

    def update_task(self, task: Task) -> None:
        """Update TASK's row in place (progress arrives often)."""
        row = self.task_rows.get(task.id)
        if row is None:
            return
        row["icon"].set(task.status)
        running = task.status == RUNNING
        text = TASK_WORDS.get(task.status) or (task.progress if running and task.progress else task.estimate)
        colour = DANGER if task.status == FAILED else ACCENT_TEXT if running and task.progress else TEXT_MUTED
        row["right"].configure(text=text, text_color=colour)
        if row.get("strong") != running:
            row["strong"] = running
            row["title"].configure(font=self.kit.font(BODY, strong=running))
        bar, status = row["bar"], row["status"]
        if not running:
            if row.get("shown"):
                bar.stop()
                bar.grid_forget()
                status.grid_forget()
                row["shown"], row["mode"] = False, None
            return
        if not row.get("shown"):
            row["shown"] = True
            bar.grid(row=row["row"], column=1, columnspan=2, sticky="ew", pady=(Space.S, 0))
            status.grid(row=row["row"] + 1, column=1, columnspan=2, sticky="ew", pady=(Space.XS, 0))
        fraction, line = bar_state(task, self.model.clock())
        status.configure(text=line)
        # A share fills the bar; without one it moves to and fro (the estimate's last stretch, or none).
        mode = "indeterminate" if fraction is None else "determinate"
        if row.get("mode") != mode:
            row["mode"] = mode
            bar.stop()
            bar.configure(mode=mode)
            if fraction is None:
                bar.start()
        if fraction is not None:
            bar.set(fraction)

    def update_job_panel(self, job: BackupJob, text: str | None = None) -> None:
        """Update the running backup job's panel in place: its line, its bar and its status line."""
        shown = self.panel_progress
        if shown is None:
            return
        if text is not None:
            shown["text"].configure(text=text)
        shown["status"].configure(text=job.status(time.monotonic()))
        if job.fraction is not None:
            bar = shown["bar"]
            if shown["mode"] != "determinate":
                shown["mode"] = "determinate"
                bar.stop()
                bar.configure(mode="determinate")
            bar.set(job.fraction)

    def tick_clock(self) -> None:
        """Count the time on: the running tasks' rows, the bars that follow their estimates, a backup job's panel."""
        self.ticked = time.monotonic()
        if self.view == INSTALLER_VIEW:
            self.tick_tasks()
        if self.job is not None and self.job.running:
            self.update_job_panel(self.job)

    def tick_tasks(self) -> None:
        """Update the running tasks' rows and the whole run's ring: their time counts on between the engine's events."""
        if self.model.stages:
            self.update_overall()
        for stage in self.model.stages:
            for task in stage.tasks:
                if task.status == RUNNING and task.id in self.task_rows:
                    self.update_task(task)

    def render_end(self, status: str) -> None:
        """Show the end of a run: the finished headset and what comes next, or the failure."""
        self.clear_right()
        finished = self.model.finished or {}
        self.badge.set(status).frame.grid(row=0, column=0, sticky="w", pady=(0, Space.L))
        if status == DONE:
            after = self.after_install()
            self.title.configure(text=after.title)
            self.summary.configure(text=after.text)
            # Another workflow opens its own start page; a step opens Advanced mode at it.
            headset = self.choice.headset
            workflows = {w.key for w in headset.workflows()} if headset is not None else set()
            for action in after.actions:
                if action.workflow in workflows:
                    command = lambda w=action.workflow: self.switch_workflow(w)  # noqa: E731  # one per button
                elif self.advanced.steps.has(action.step):
                    command = lambda s=action.step: self.open_advanced(s)  # noqa: E731  # one per button
                else:
                    continue
                self.kit.button(self.quiet, action.label, command).pack(side="left", padx=(0, Space.S))
            self.quiet.pack(side="left", padx=(Space.L, 0))
            self.set_primary("Done")
            return
        self.title.configure(text="Something went wrong")
        self.summary.configure(text=str(finished.get("error") or "The install stopped with an error."))
        self.summary.grid(pady=(Space.S, Space.S))
        where = str(finished.get("where") or "")
        if where:
            self.show_note(where, WARNING_TONE)
        self.set_primary("Retry")

    def switch_workflow(self, workflow: str) -> None:
        """Leave the finished workflow for WORKFLOW's start page (its plan read afresh)."""
        if self.refused():
            return
        self.leave()
        if self.choice.headset is not None:
            self.workflow_probes.pop((self.choice.headset.info.codename, workflow), None)
        self.choose_workflow(workflow)

    def set_primary(self, label: str) -> None:
        """Show the one main button with LABEL, or none for an empty label; the Stop button goes to its left."""
        self.primary.pack_forget()
        if label:
            self.kit.retext(self.primary, label)
            self.primary.pack(side="right")

    # -- panels over a view: the engine's asks, the window's own questions -----------------------
    def build_panel(  # noqa: PLR0913  # a panel is its parts
        self,
        parent: Any,  # noqa: ANN401  # a Tk widget
        title: str,
        text: str,
        *,
        fields: list[dict[str, Any]],
        accept: str,
        decline: str,
        on_answer: Callable[[bool, dict[str, str]], None],
        warning: str = "",
        progress: bool = False,
    ) -> tuple[Any, dict[str, Any]]:
        """Return a panel over PARENT, and its entries: TITLE, TEXT, a WARNING box, the form's FIELDS, the buttons.

        The panel dims the view behind it and holds a centred card. Answering calls ON_ANSWER with
        whether it was accepted and the entries' values; the caller removes the panel there.
        Without ACCEPT it is a notice with no buttons. PROGRESS adds a bar and a status line under
        the text, in self.panel_progress for their updates.
        """
        kit = self.kit
        panel = kit.frame(parent, BACKDROP)
        panel.place(relx=0, rely=0, relwidth=1, relheight=1)
        card = kit.card(panel, radius=RADIUS_PANEL)
        card.place(relx=0.5, rely=0.45, anchor="center")
        pad = Space.XXL
        room = parent.winfo_width() / kit.scaling(parent) - 2 * Space.XL - 2 * pad  # a narrow view: a narrower card
        wrap = round(min(PANEL, room)) if room > PANEL / 2 else PANEL
        kit.label(card, title, size=TITLE, strong=True, wrap=wrap).pack(fill="x", padx=pad, pady=(pad - 4, Space.S))
        body = kit.label(card, text, size=BODY, colour=TEXT_SECONDARY, wrap=wrap)
        body.pack(fill="x", padx=pad)
        if progress:
            bar = kit.progress(card)
            bar.pack(fill="x", padx=pad, pady=(Space.L, 0))
            bar.start()
            status = kit.label(card, "", size=LABEL, colour=TEXT_MUTED)
            status.configure(anchor="w")
            status.pack(fill="x", padx=pad, pady=(Space.XS, 0))
            self.panel_progress = {"text": body, "bar": bar, "status": status, "mode": "indeterminate"}
        if warning:
            Note(kit, card, wrap=wrap - 2 * Space.L - 2 * Space.M).set(warning, WARNING_TONE).frame.pack(
                fill="x", padx=pad, pady=(Space.L, 0)
            )
        entries: dict[str, Any] = {}
        for f in fields:
            label = str(f.get("label", f.get("name", ""))) + (" (optional)" if f.get("optional") else "")
            kit.label(card, label, size=LABEL, strong=True).pack(fill="x", padx=pad, pady=(Space.L, Space.XS + 2))
            entry = kit.entry(card, show="*" if f.get("secret") else "")
            entry.pack(fill="x", padx=pad)
            entries[str(f.get("name"))] = entry

        def answer(*, accepted: bool) -> None:
            values = {name: e.get() for name, e in entries.items()} if accepted else {}
            on_answer(accepted, values)

        if not accept:  # a notice: nothing to answer
            kit.frame(card).pack(pady=(0, pad - Space.S))
            return panel, entries
        buttons = kit.frame(card)
        buttons.pack(fill="x", padx=pad, pady=(Space.XL, pad - 4))
        kit.button(buttons, accept, lambda: answer(accepted=True), kind=PRIMARY).pack(side="right")
        if decline:
            kit.button(buttons, decline, lambda: answer(accepted=False)).pack(side="right", padx=(0, Space.S))
        if entries:
            next(iter(entries.values())).focus_set()
        return panel, entries

    def show_panel(  # noqa: PLR0913  # a panel is its parts
        self,
        title: str,
        text: str,
        *,
        fields: list[dict[str, Any]],
        accept: str,
        decline: str,
        on_answer: Callable[[bool, dict[str, str]], None],
        ask: dict[str, Any] | None = None,
        warning: str = "",
        progress: bool = False,
    ) -> None:
        """Show a panel over the view (the installer's, or the page of what to do); ASK is the engine's ask.

        Without ASK it is the window's own question or message; PROGRESS: as build_panel's.
        """
        self.hide_panel()
        self.panel_ask = ask

        def answered(accepted: bool, values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            self.hide_panel()
            on_answer(accepted, values)

        self.panel, entries = self.build_panel(
            self.actions if self.view == ACTIONS_VIEW else self.content,
            title,
            text,
            fields=fields,
            accept=accept,
            decline=decline,
            on_answer=answered,
            warning=warning,
            progress=progress,
        )
        self.secrets = list(entries.values())

    def notice(self, title: str, text: str) -> None:
        """Show the window's own message over the installer view, with OK."""

        def answer(_accepted: bool, _values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            self.render()  # the engine's own ask, if it was waiting, comes back

        self.show_panel(title, text, fields=[], accept="OK", decline="", on_answer=answer)

    def hide_panel(self) -> None:
        """Remove the panel; the form's entries are emptied first, so no value stays in a widget."""
        for entry in self.secrets:
            with contextlib.suppress(Exception):  # the widget may be gone with its window
                entry.delete(0, "end")
        self.secrets = []
        if self.panel is not None:
            self.panel.destroy()
        self.panel = self.panel_ask = self.panel_progress = None

    def show_ask(self) -> None:
        """Show the engine's pending ask as a panel; the answer goes to the engine's stdin."""
        ask = self.model.ask
        if ask is None:
            return
        kind = ask.get("kind")
        default = "Done" if kind == "action" else "Continue"

        def reply(accepted: bool, values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            self.engine.send(self.model.answer(accepted=accepted, values=values if kind == "form" else None))
            self.render()

        self.show_panel(
            str(ask.get("title", "")),
            str(ask.get("text", "")),
            fields=list(ask.get("fields") or []) if kind == "form" else [],
            accept=str(ask.get("accept") or default),
            decline=str(ask.get("decline") or "Cancel"),
            on_answer=reply,
            ask=ask,
        )

    # -- actions -------------------------------------------------------------------------
    def start(self, extra: list[str] | None = None) -> None:
        """Start an engine run (the same command line the terminal would use, plus EXTRA)."""
        if self.probing or self.model.running:
            return
        refusal = busy(INSTALLER, installer_running=False, step_running=self.advanced.run.running)
        if refusal:
            self.notice("One thing at a time", refusal)
            return
        self.model.begin()
        self.stopped_at = 0.0
        self.engine.start(extra or [])
        self.render()

    def press(self) -> None:
        """Act on the main button: start, continue (also on the start page) or retry a run, or close when done."""
        if self.model.view() == DONE:
            self.root.destroy()
            return
        self.start()

    def show_drawer(self) -> None:
        """Show the details drawer under the view, about a third of the window high."""
        height = self.root.winfo_height() / self.kit.scaling(self.root)
        self.details.configure(height=round(min(max(height / 3, DETAILS[0]), DETAILS[1])))
        self.drawer.grid(row=3, column=0, sticky="ew")

    def toggle_details(self) -> None:
        """Show or hide the log of the child programs."""
        self.details_shown = not self.details_shown
        if self.details_shown:
            self.show_drawer()
            self.fill_details()
        else:
            self.drawer.grid_forget()
        self.kit.retext(self.details_link, "Hide details" if self.details_shown else "Show details")

    def fill_details(self) -> None:
        """Put the model's log into the details view."""
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("end", "\n".join(self.model.log) or EMPTY_DETAILS)
        self.details.see("end")
        self.details.configure(state="disabled")

    def ask_to_stop(self, *, closing: bool = False) -> None:
        """Ask before stopping the installer; CLOSING: the window closes once the engine has stopped.

        A stop asks the engine (its stdin closes) and the window shows Stopping\N{HORIZONTAL ELLIPSIS} until the
        engine's finished event; a step that writes to the headset finishes first.
        """
        title, text = self.model.stop_question()

        def answer(accepted: bool, _values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            if not accepted:
                self.render()  # the engine's own ask, if it was waiting, comes back
                return
            self.model.request_stop()
            self.engine.stop()
            if closing:
                self.closing_notice()
            else:
                self.render()

        self.show_panel(title, text, fields=[], accept="Stop", decline="Keep going", on_answer=answer)

    def closing_notice(self) -> None:
        """Show that the window closes by itself once the engine has stopped."""
        self.closing = True
        first = " The step that writes to the headset finishes first." if self.model.stop_after_write else ""
        self.show_panel(
            "Stopping\N{HORIZONTAL ELLIPSIS}",
            f"The installer is stopping.{first} The window closes by itself.",
            fields=[],
            accept="",
            decline="",
            on_answer=lambda _a, _v: None,
        )

    # -- the backup: its export and import ------------------------------------------------
    def export_backup(self) -> None:
        """Ask where to, then export the chosen headset's backup there (python -m overpressure backup export)."""
        headset = self.choice.headset
        if headset is None or self.refused():
            return
        from tkinter import filedialog  # noqa: PLC0415  # Tk's own dialogs, only here

        name = f"{headset.info.codename}-backup{ARCHIVE_SUFFIX}"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export the backup",
            initialfile=name,
            defaultextension=".tar",
            filetypes=[("overpressure backup", "*.tar"), ("All files", "*")],
        )
        if path:
            self.start_job("export", path)

    def import_backup(self) -> None:
        """Ask which file, then import it as the chosen headset's backup (python -m overpressure backup import)."""
        headset = self.choice.headset
        if headset is None or self.refused():
            return
        from tkinter import filedialog  # noqa: PLC0415  # Tk's own dialogs, only here

        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import a backup",
            filetypes=[("overpressure backup", "*.tar"), ("All files", "*")],
        )
        if path:
            self.start_job("import", path)

    def start_job(self, action: str, path: str) -> None:
        """Start a backup's ACTION (export or import) with PATH; its panel shows until it has ended."""
        headset = self.choice.headset
        if headset is None:
            return
        self.job = self.jobs(headset.info.codename, action, path)
        self.job.start()
        self.job_panel()

    def job_panel(self) -> None:
        """Show the backup job's panel: its last line while it runs, its result once it has ended."""
        job = self.job
        if job is None:
            return
        title = JOB_TITLES.get(job.action, "Your backup")
        if job.running:
            line = job.last()
            text = JOB_TEXT + (f"\n\n{line}" if line else "")
            if self.panel_progress is None:
                self.show_panel(
                    title + "\N{HORIZONTAL ELLIPSIS}",
                    text,
                    fields=[],
                    accept="",
                    decline="",
                    on_answer=lambda _a, _v: None,
                    progress=True,
                )
            self.update_job_panel(job, text)
            return
        self.job = None

        def done(_accepted: bool, _values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            if self.view == ACTIONS_VIEW:
                self.render_actions()  # the backup's card says where it is now

        if job.code == 0:
            text = job.last() + (
                "\n\nKeep the file private: it holds your headset's backup and what reaches its maintenance system."
                if job.action == "export"
                else ""
            )
            self.show_panel(
                "Exported" if job.action == "export" else "Imported",
                text,
                fields=[],
                accept="OK",
                decline="",
                on_answer=done,
            )
        else:
            self.show_panel(
                "The backup was not " + ("exported" if job.action == "export" else "imported"),
                job.error() or "It stopped with an error.",
                fields=[],
                accept="OK",
                decline="",
                on_answer=done,
            )

    def close(self) -> None:
        """Close the window, asking first while the installer or a step runs."""
        if self.closing:
            return
        if self.job is not None and self.job.running:
            self.job_panel()
            return
        if self.model.running and self.model.stopping:
            self.show(INSTALLER_VIEW)
            self.closing_notice()  # asked to stop already: it closes when that is done
        elif self.model.running:
            self.show(INSTALLER_VIEW)
            self.ask_to_stop(closing=True)
        elif self.advanced.run.running:
            self.show(ADVANCED_VIEW)
            self.advanced_view.ask_to_close()
        else:
            self.root.destroy()

    # -- the engine's events ---------------------------------------------------------------
    def receive(self) -> tuple[bool, bool]:
        """Apply what the engine has sent; return whether to redraw and whether lines were logged.

        On the headset page the engines belong to its probes (with ENGINES): nothing is taken here.
        """
        changed = logged = False
        if self.view == CHOOSER_VIEW and self.engines is not None:
            return changed, logged
        for kind, value in drain(self.engine.items):
            if kind == "exit" and self.probing:
                self.probing, changed = False, True
                self.model.probed()  # the start page when a run was recorded before; nothing starts by itself
                continue
            if kind == "exit":
                self.model.exited(int(value))
                changed = True
                continue
            event = self.model.feed(str(value))
            name = event["event"]
            # Everything but progress also writes a line to the details (the model's log).
            logged = logged or name != "progress"
            if name == "progress":
                task = self.model.task(str(event.get("task")))
                if task is not None:
                    self.update_task(task)
                    self.update_overall()
            elif name != "log":
                changed = True
            if name == "finished" and self.model.running and not self.stopped_at:
                self.stopped_at = time.monotonic()  # its run has ended: it exits by itself, else it is ended
        return changed, logged

    def _tick(self) -> None:
        """Apply what the engine, a step's program and the checks have sent, then redraw; every 100 ms."""
        changed, logged = self.receive()
        probed = [codename for codename, probe in self.probes.items() if probe.receive()]
        releases = self.choice.releases.done and not self.releases_shown
        self.releases_shown |= releases
        if (probed or releases or self.downloaded()) and self.view == CHOOSER_VIEW:
            self.render_chooser()  # where each headset stopped last time, its releases, a download's end
        workflows = [key for key, probe in self.workflow_probes.items() if probe.receive()]
        if workflows and self.view == ACTIONS_VIEW and not (self.job is not None and self.job.running):
            self.render_actions()  # where each workflow stopped last time
        if self.job is not None and self.job.receive():
            self.job_panel()
        self.advanced_view.tick()
        if self.closing and not self.model.running and not self.advanced.run.running:
            self.hide_panel()
            self.root.destroy()
            return
        if self.stopped_at and time.monotonic() - self.stopped_at > STOP_GRACE:
            self.engine.end()  # it has said it stopped: nothing of it is running any more
        if changed and not self.closing:
            self.render()
        elif time.monotonic() - self.ticked >= 1:
            self.tick_clock()
        if logged and self.details_shown and self.view == INSTALLER_VIEW:
            self.fill_details()
        self.root.after(100, self._tick)
