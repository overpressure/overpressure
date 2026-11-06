# SPDX-License-Identifier: Unlicense
"""The window's widgets: the header, the headset question, the installer view, the panel for asks, Advanced mode.

The widgets only draw the models (headsets.Choice, installer.Model, advanced.Advanced) and pass on
what is pressed. Their look is kit.py's components on theme.py's tokens. CustomTkinter is handed
in as a module (window.main imports it), so this module imports without it.
"""

from __future__ import annotations

import collections
import contextlib
import time
from typing import TYPE_CHECKING, Any, Callable

from overpressure.headset import AfterInstall
from overpressure.window.advanced import INSTALLER, busy
from overpressure.window.advanced_view import AdvancedView
from overpressure.window.headsets import INTRO, LAST_USED, QUESTION, Choice, Probe
from overpressure.window.installer import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    SKIPPED,
    STOP_GRACE,
    STOPPED,
    Model,
    Task,
    drain,
)
from overpressure.window.kit import HEADER, PRIMARY, PRIMARY_WIDTH, QUIET, SECONDARY, Code, Icon, Kit, Note, Reflow
from overpressure.window.theme import (
    ACCENT,
    ACCENT_SOFT,
    ACCENT_TEXT,
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
    LENS,
    LEVELS,
    LOGO,
    PAGE,
    PANEL,
    RADIUS_CONTROL,
    RADIUS_PANEL,
    RAISED,
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
#: The window's views: the installer, Advanced mode, and the headset page (which headset it works on).
INSTALLER_VIEW, ADVANCED_VIEW, CHOOSER_VIEW = "installer", "advanced", "chooser"
#: The header's way back to the headset page, on every other page.
BACK = "\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK} Headsets"
#: The stepper's caption over the stages.
STAGES = "Install"
#: A task's state in words at the right of its row, once it has one.
TASK_WORDS = {DONE: "done", SKIPPED: "already done", FAILED: "failed"}


class Window:
    """The window: the header, and the installer view (stepper, the current stage or the end, the details).

    Advanced mode's view is an AdvancedView beside it; the header's link switches between the two.
    The window works on the headset CHOICE holds: unless --device chose one it opens on the headset
    page, and the header's back link returns there from every other page. Each headset keeps its
    engine and its details while another one is chosen; its recorded progress is the engine's.
    """

    def __init__(  # noqa: PLR0913  # the engine and the models; the view, the choice and the engine factory by name
        self,
        ctk_module: Any,  # noqa: ANN401  # the customtkinter module, imported lazily
        engine: Engine,
        advanced: Advanced,
        *,
        view: str = INSTALLER_VIEW,
        choice: Choice | None = None,
        engines: Callable[[Headset], Engine] | None = None,
    ) -> None:
        """Build the window with the installer's ENGINE and ADVANCED mode's model, showing VIEW first.

        CHOICE is the headset page's model (else Advanced mode's headset, chosen); with its headset
        chosen VIEW shows at once, else after the first choice. ENGINES returns the installer's
        engine for a headset (else ENGINE serves every one, and the page reads no plans ahead).
        """
        self.ctk: Any = ctk_module
        self.engine, self.advanced = engine, advanced
        self.choice = choice or Choice(advanced.steps.registry, advanced.steps.headset)
        self.engines = engines
        self.engine_of: dict[str, Engine] = {}  # codename -> its engine, made once
        if self.choice.headset is not None:
            self.engine_of[self.choice.headset.info.codename] = engine
        self.probes: dict[str, Probe] = {}  # codename -> the headset page's read of its plan
        self.logs: dict[str, list[str]] = {}  # codename -> its details, while another headset is chosen
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
        if self.choice.asking():
            self.probing = False  # the page reads each headset's plan; the installer's waits for the choice
            self.show(CHOOSER_VIEW)
        elif not self.choice.installer():
            self.probing = False
            self.show(ADVANCED_VIEW)  # a headset without an installer: Advanced mode only
        else:
            self.show(view)
            engine.start(["--plan"])
        if not self.choice.asking():
            self.first_view = INSTALLER_VIEW  # shown already: a later choice opens the installer
        self.root.after(100, self._tick)

    # -- layout ----------------------------------------------------------------------------
    def first_size(self) -> str:
        """Return the window's first size: WINDOW where the screen has room, else what it has, at least WINDOW_MIN."""
        scaling = float(self.ctk.ScalingTracker.get_window_scaling(self.root))
        screen = (self.root.winfo_screenwidth() / scaling, self.root.winfo_screenheight() / scaling)
        room = (screen[0] - SCREEN_MARGIN[0], screen[1] - SCREEN_MARGIN[1])
        width, height = (max(min(want, have), least) for want, have, least in zip(WINDOW, room, WINDOW_MIN))
        return f"{int(width)}x{int(height)}"

    def _build(self) -> None:
        kit = self.kit
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)
        self._build_header()
        kit.line(self.root).grid(row=1, column=0, sticky="ew")
        self._build_chooser()
        self._build_body()
        self._build_drawer()

    def _build_body(self) -> None:
        kit = self.kit
        self.body = body = kit.frame(self.root, PAGE)
        body.grid_columnconfigure(2, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.stepper = kit.frame(body, RAISED)
        self.stepper.configure(width=SIDEBAR)
        self.stepper.grid(row=0, column=0, sticky="ns")
        self.stepper.pack_propagate(False)  # noqa: FBT003  # Tk's own positional flag: the width stays
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
        self.note_text = self.note.text
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
        reflow = Reflow(kit, self.scroll.canvas, CONTENT, sides=(Space.XXXL, Space.XXXL))
        reflow.add(page)
        reflow.add(bottom, shift=Space.L)  # a quiet button's text lines up with the page's
        reflow.wrap(self.summary)

    def _build_drawer(self) -> None:
        kit = self.kit
        # The details: the child programs' output in a drawer under the view, its height a share of the window.
        self.drawer = kit.frame(self.root, SUNKEN)
        kit.line(self.drawer).pack(fill="x")
        self.details_block = Code(kit, self.drawer, "Details", height=DETAILS[0], wrap="none", radius=0)
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
        visor = self.ctk.CTkFrame(bar, width=LOGO[0], height=LOGO[1], corner_radius=RADIUS_CONTROL, fg_color=ACCENT)
        visor.pack(side="left")  # the headset: a visor with two lenses
        for x in (0.3, 0.7):
            lens = self.ctk.CTkFrame(visor, width=LENS, height=LENS, corner_radius=LENS // 2, fg_color=RAISED)
            lens.place(relx=x, rely=0.5, anchor="center")
        kit.label(bar, "overpressure", size=HEADING, strong=True).pack(side="left", padx=(Space.M, Space.M))
        self.divider = kit.line(bar, vertical=True)
        self.divider.configure(height=LOGO[1])
        self.divider.pack(side="left", padx=(0, Space.M))
        self.subtitle = kit.label(bar, size=LABEL, colour=TEXT_MUTED)
        self.subtitle.pack(side="left")
        self.mode_link = kit.button(bar, "Advanced mode", self.switch, kind=HEADER)
        self.back_link = kit.button(bar, BACK, self.to_headsets, kind=HEADER)  # packed by render_link

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
        kit.label(column, QUESTION, size=TITLE, strong=True).grid(row=0, column=0, sticky="ew")
        intro = kit.label(column, INTRO, size=BODY, colour=TEXT_SECONDARY, wrap=CARDS)
        intro.grid(row=1, column=0, sticky="ew", pady=(Space.S, Space.XL))
        self.headset_list = kit.frame(column)
        self.headset_list.grid(row=2, column=0, sticky="ew")
        self.headset_list.grid_columnconfigure(0, weight=1)
        self.headset_cards: dict[str, Any] = {}  # codename -> its card
        self.chooser_flow = Reflow(kit, scroll.canvas, CARDS, sides=(Space.XXXL, Space.XXXL), center=True)
        self.chooser_flow.add(column)
        self.chooser_flow.wrap(intro)

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
        entries = self.choice.entries()
        lead = next((e.codename for e in entries if e.last), entries[0].codename if len(entries) == 1 else "")
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
            probe = self.probes.get(entry.codename)
            line = probe.line() if probe is not None else ""
            about = kit.label(card, entry.about, size=LABEL, colour=TEXT_SECONDARY)
            parts = [
                heading,
                about,
                kit.label(card, entry.mode, size=LABEL, colour=TEXT if entry.installer else WARNING),
            ]
            if line:
                parts.append(kit.label(card, line, size=SMALL, colour=TEXT_MUTED))
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
            primary = entry.codename == lead
            button = kit.button(
                card, "Choose", lambda c=entry.codename: self.choose(c), kind=PRIMARY if primary else SECONDARY
            )
            button.grid(row=0, column=1, rowspan=len(parts), padx=(Space.L, Space.XL))
            self.hover_card(card, (*parts, *marks), entry.codename)
            self.headset_cards[entry.codename] = card

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
        """Show VIEW: the installer, Advanced mode, or the headset page (which reads the headsets' plans)."""
        self.view = view
        self.body.grid_forget()
        self.drawer.grid_forget()
        self.advanced_view.frame.grid_forget()
        self.chooser.grid_forget()
        if view == ADVANCED_VIEW:
            self.advanced_view.frame.grid(row=2, column=0, sticky="nsew")
            self.advanced_view.render()
        elif view == CHOOSER_VIEW:
            self.chooser.grid(row=2, column=0, sticky="nsew")
            self.probe_headsets()
            self.render_chooser()
        else:
            self.body.grid(row=2, column=0, sticky="nsew")
            if self.details_shown:
                self.show_drawer()
                self.fill_details()
        self.render_subtitle()
        self.render_link()

    def switch(self) -> None:
        """Switch between the installer and Advanced mode; whatever runs keeps running."""
        if self.view != CHOOSER_VIEW:
            self.show(INSTALLER_VIEW if self.view == ADVANCED_VIEW else ADVANCED_VIEW)

    def render_subtitle(self) -> None:
        """Name the headset the window works on beside the title; the headset page names none."""
        name = "" if self.view == CHOOSER_VIEW else self.model.headset.get("name", "")
        if not name and self.view != CHOOSER_VIEW and self.choice.headset is not None:
            name = self.choice.headset.info.name
        self.subtitle.configure(text=SUBTITLE.format(name=name) if name else "")
        self.divider.configure(fg_color=BORDER_SUBTLE if name else "transparent")

    def render_link(self) -> None:
        """Name the other view in the header (the installer's when it waits for an answer), and the way back.

        A headset without an installer has no other view. Every page but the headset page has the
        back link to it, also with one headset; the headset page has neither.
        """
        self.mode_link.pack_forget()
        self.back_link.pack_forget()
        if self.view == CHOOSER_VIEW:
            return
        waiting = self.view != INSTALLER_VIEW and bool(self.model.ask)
        if self.view == INSTALLER_VIEW:
            text = "Advanced mode"
        else:
            text = "Installer (waiting for you)" if waiting else "Installer"
        self.kit.retext(self.mode_link, text)
        self.mode_link.configure(
            fg_color=ACCENT_SOFT if waiting else "transparent", text_color=ACCENT_TEXT if waiting else TEXT_SECONDARY
        )
        if self.view == INSTALLER_VIEW or self.choice.installer():
            self.mode_link.pack(side="right")
        if self.choice.registry.headsets:
            self.back_link.pack(side="right", padx=(0, Space.XS))

    # -- the headset ---------------------------------------------------------------------
    def engine_for(self, headset: Headset) -> Engine:
        """Return HEADSET's engine, the same one each time (the window's only one without ENGINES)."""
        if self.engines is None:
            return self.engine
        codename = headset.info.codename
        if codename not in self.engine_of:
            self.engine_of[codename] = self.engines(headset)
        return self.engine_of[codename]

    def probe_headsets(self) -> None:
        """Read the plan of every headset with an installer that has none read yet: where it stopped last time.

        Only with an engine for each headset (ENGINES); the engines are idle while the page shows.
        """
        if self.engines is None:
            return
        for codename, headset in self.choice.registry.headsets.items():
            if codename not in self.probes and self.choice.installer(headset):
                self.probes[codename] = Probe(self.engine_for(headset)).start()

    def to_headsets(self) -> bool:
        """Go back to the headset page; refused (and said so) while the installer or a step runs.

        Nothing runs then, and nothing is lost: the headset's details stay for its next choice, a
        plan still being read goes on for the page, and its recorded progress is the engine's.
        """
        if self.view == CHOOSER_VIEW:
            return True
        refusal = self.choice.refusal(installer_running=self.model.running, step_running=self.advanced.run.running)
        if refusal:
            if self.view == ADVANCED_VIEW:
                self.advanced_view.show_panel(
                    "One thing at a time", refusal, accept="OK", decline="", on_answer=lambda **_answer: None
                )
            else:
                self.notice("One thing at a time", refusal)
            return False
        self.leave()
        self.show(CHOOSER_VIEW)
        return True

    def leave(self) -> None:
        """Put the chosen headset aside: its details kept, a plan probe under way handed to the headset page."""
        self.hide_panel()
        self.advanced_view.hide_panel()
        headset = self.choice.headset
        if headset is None:
            return
        codename = headset.info.codename
        self.logs[codename] = list(self.model.log)
        if self.probing and self.engines is not None:
            self.probes[codename] = Probe(self.engine, self.model)
            self.probing = False

    def choose(self, codename: str) -> bool:
        """Work on the headset CODENAME: its start page (its plan, read on the headset page or now), else Advanced mode.

        Refused while something runs. It becomes the headset used last (out/window.json, not .env).
        """
        if self.choice.refusal(installer_running=self.model.running, step_running=self.advanced.run.running):
            return False
        if self.view != CHOOSER_VIEW:
            self.leave()
        headset = self.choice.choose(codename)
        self.advanced.steps.choose(codename)
        self.advanced_view.refill()
        self.engine = self.engine_for(headset)
        self.hide_panel()
        self.stopped_at = 0.0
        view, self.first_view = self.first_view, INSTALLER_VIEW
        if self.choice.installer(headset):
            probe = self.probes.pop(codename, None) or Probe(self.engine).start()
            self.model, self.probing = probe.model, not probe.done
            earlier = self.logs.pop(codename, [])
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
        """Return what the finished page says and offers: the chosen headset's own words."""
        headset = self.choice.headset
        return headset.after_install() if headset is not None else AfterInstall()

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
        for child in self.stepper.winfo_children():
            child.destroy()
        stages = self.model.stages
        top = kit.frame(self.stepper)
        top.pack(fill="x", padx=Space.M, pady=(Space.XL, 0))
        if stages:
            kit.label(top, STAGES, size=SMALL, colour=TEXT_MUTED, strong=True).pack(
                fill="x", padx=Space.M, pady=(0, Space.S)
            )
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
            self.stop_button.pack(side="right", padx=(0, Space.S))

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
        self.task_rows[task.id] = {"icon": icon, "title": title, "right": right, "bar": bar, "row": 1}
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
        bar = row["bar"]
        if not running:
            if row.get("shown"):
                bar.stop()
                bar.grid_forget()
                row["shown"] = False
            return
        if not row.get("shown"):
            row["shown"] = True
            bar.grid(row=row["row"], column=1, columnspan=2, sticky="ew", pady=(Space.S, 0))
            if task.fraction is None:
                bar.start()
        if task.fraction is not None:
            if bar.cget("mode") != "determinate":
                bar.stop()
                bar.configure(mode="determinate")
            bar.set(task.fraction)

    def render_end(self, status: str) -> None:
        """Show the end of a run: the finished headset and what comes next, or the failure."""
        self.clear_right()
        finished = self.model.finished or {}
        self.badge.set(status).frame.grid(row=0, column=0, sticky="w", pady=(0, Space.L))
        if status == DONE:
            after = self.after_install()
            self.title.configure(text=after.title)
            self.summary.configure(text=after.text)
            # The engine has no modes for these: they open Advanced mode at the headset's steps.
            for action in after.actions:
                if self.advanced.steps.has(action.step):
                    self.kit.button(self.quiet, action.label, lambda s=action.step: self.open_advanced(s)).pack(
                        side="left", padx=(0, Space.S)
                    )
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
    ) -> tuple[Any, dict[str, Any]]:
        """Return a panel over PARENT, and its entries: TITLE, TEXT, a WARNING box, the form's FIELDS, the buttons.

        The panel dims the view behind it and holds a centred card. Answering calls ON_ANSWER with
        whether it was accepted and the entries' values; the caller removes the panel there.
        Without ACCEPT it is a notice with no buttons.
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
        kit.label(card, text, size=BODY, colour=TEXT_SECONDARY, wrap=wrap).pack(fill="x", padx=pad)
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
    ) -> None:
        """Show a panel over the installer view; ASK is the engine's ask it shows, else it is the window's own."""
        self.hide_panel()
        self.panel_ask = ask

        def answered(accepted: bool, values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            self.hide_panel()
            on_answer(accepted, values)

        self.panel, entries = self.build_panel(
            self.content,
            title,
            text,
            fields=fields,
            accept=accept,
            decline=decline,
            on_answer=answered,
            warning=warning,
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
        self.panel = self.panel_ask = None

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
        self.details.xview_moveto(0)  # the last line's end may be far right: the lines' starts stay in view
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

    def close(self) -> None:
        """Close the window, asking first while the installer or a step runs."""
        if self.closing:
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
            elif name != "log":
                changed = True
            if name == "finished" and self.model.running and not self.stopped_at:
                self.stopped_at = time.monotonic()  # its run has ended: it exits by itself, else it is ended
        return changed, logged

    def _tick(self) -> None:
        """Apply what the engine, a step's program and the checks have sent, then redraw; every 100 ms."""
        changed, logged = self.receive()
        probed = [codename for codename, probe in self.probes.items() if probe.receive()]
        if probed and self.view == CHOOSER_VIEW:
            self.render_chooser()  # where each headset stopped last time
        self.advanced_view.tick()
        if self.closing and not self.model.running and not self.advanced.run.running:
            self.hide_panel()
            self.root.destroy()
            return
        if self.stopped_at and time.monotonic() - self.stopped_at > STOP_GRACE:
            self.engine.end()  # it has said it stopped: nothing of it is running any more
        if changed and not self.closing:
            self.render()
        if logged and self.details_shown and self.view == INSTALLER_VIEW:
            self.fill_details()
        self.root.after(100, self._tick)
