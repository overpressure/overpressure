# SPDX-License-Identifier: Unlicense
"""Advanced mode's widgets: the headset's steps down the left, the selected step or the checks on the right.

They draw advanced.Advanced and pass on what is pressed; every decision (what runs, what asks
first, what is refused) is the model's. A step that writes asks first in the window's own panel.
Their look is kit.py's components on theme.py's tokens.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Callable

from overpressure import REPO
from overpressure.window.advanced import (
    ASK,
    DOCTOR,
    DONE,
    FAILED,
    LOG_LINES,
    REFUSED,
    RUNNING,
    START,
    STOPPED,
    STOPPING,
    browse,
    relative,
)
from overpressure.window.kit import PRIMARY, Code, Reflow
from overpressure.window.theme import (
    ACCENT,
    ACCENT_SOFT,
    ACCENT_TEXT,
    BODY,
    BORDER_STRONG,
    COMMAND,
    DANGER,
    DOT,
    FIX,
    FORM_LABEL,
    HEADING,
    LABEL,
    MARK,
    NAME_COLUMN,
    NARROWEST,
    NEUTRAL,
    OUTPUT,
    PAGE,
    RADIUS_CONTROL,
    RAISED,
    SIDEBAR,
    SMALL,
    STATUS,
    STATUS_COLUMN,
    SUCCESS,
    TEXT,
    TEXT_MUTED,
    TEXT_SECONDARY,
    TITLE,
    WARNING,
    WIDE,
    Space,
)

if TYPE_CHECKING:
    from overpressure.headset import Param, Step
    from overpressure.window.app import Window

COPIED_MS = 1500  # how long a copy button says "Copied"
#: The empty states of the output and the fix.
EMPTY_LOG = "Nothing has run yet. Run starts the step; its output shows here."
EMPTY_FIX = "Select a line that is not ok to see how to fix it."
#: The run's state as the colour of the dot beside the status line.
RUN_COLOURS = {RUNNING: ACCENT, STOPPING: WARNING, DONE: SUCCESS, FAILED: DANGER, STOPPED: WARNING}


class AdvancedView:
    """Advanced mode: the headset and its steps on the left; a step's form, command line and log, or the checks."""

    def __init__(self, window: Window, parent: Any) -> None:  # noqa: ANN401  # a Tk widget
        """Build the view in PARENT; the window grids its frame when the view is shown."""
        self.window, self.ctk, self.model = window, window.ctk, window.advanced
        self.kit = window.kit
        self.panel: Any = None
        self.entries: dict[str, tuple[Any, Any]] = {}  # list entry key -> (its row, its label)
        self.marks: dict[str, Any] = {}  # list entry key -> the bar that marks it selected
        self.check_rows: dict[int, Any] = {}  # check index -> its row
        self.details: list[Any] = []  # the checks' detail labels, wrapped to the list's width
        self.vars: dict[str, Any] = {}  # the shown step's parameters: name -> its variable
        self.shown_step = ""  # the step whose form is built
        self.log_lines = 0
        kit = self.kit
        self.frame = kit.frame(parent, PAGE)
        self.frame.grid_columnconfigure(2, weight=1)
        self.frame.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        kit.line(self.frame, vertical=True).grid(row=0, column=1, sticky="ns")
        self.content = kit.frame(self.frame, PAGE)
        self.content.grid(row=0, column=2, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        # The pages scroll when the window is too short for them; otherwise the output takes the rest.
        self.scroll = scroll = kit.scroll(self.content, PAGE, fill=True)
        scroll.frame.grid(row=0, column=0, sticky="nsew")
        scroll.frame.grid_columnconfigure(0, weight=1)
        scroll.frame.grid_rowconfigure(0, weight=1)
        self.pages = scroll.frame
        self.reflow = Reflow(kit, scroll.canvas, WIDE, sides=(Space.XXL + Space.S, Space.XXL + Space.S))
        self._build_step_page()
        self._build_checks_page()
        self.reflow.add(self.step_page)
        self.reflow.add(self.checks_page)
        self.fill_list()

    # -- layout ----------------------------------------------------------------------------
    def _build_sidebar(self) -> None:
        kit = self.kit
        side = kit.frame(self.frame, RAISED)
        side.configure(width=SIDEBAR)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)  # noqa: FBT003  # Tk's own positional flag: the width stays
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(2, weight=1)
        top = kit.frame(side)
        top.grid(row=0, column=0, sticky="ew", padx=Space.XL, pady=(Space.XL, Space.L))
        kit.label(top, "Headset", size=SMALL, colour=TEXT_MUTED, strong=True).pack(fill="x")
        # The window's headset; the header's back link to the headset page chooses another.
        self.headset_name = kit.label(top, size=HEADING, strong=True)
        self.headset_name.pack(fill="x", pady=(Space.XS, 0))
        self.about = kit.label(top, size=SMALL, colour=TEXT_SECONDARY, wrap=SIDEBAR - 2 * Space.XL)
        self.about.pack(fill="x", pady=(Space.XS, 0))
        kit.line(side).grid(row=1, column=0, sticky="ew")
        self.list_scroll = kit.scroll(side, RAISED)
        self.list_scroll.frame.grid(row=2, column=0, sticky="nsew", padx=(Space.S, Space.XS), pady=(Space.S, Space.S))
        self.list = self.list_scroll.frame

    def _build_step_page(self) -> None:
        kit = self.kit
        page = self.step_page = kit.frame(self.pages)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(5, weight=1)
        self.title = kit.label(page, size=TITLE, strong=True)
        self.title.grid(row=0, column=0, sticky="ew")
        self.help = kit.label(page, size=BODY, colour=TEXT_SECONDARY, wrap=WIDE)
        self.help.grid(row=1, column=0, sticky="ew", pady=(Space.S, Space.L))
        self.reflow.wrap(self.help)
        self.form = kit.card(page)
        self.form.grid_columnconfigure(1, weight=1)
        self.command_block = Code(kit, page, "Command line", height=COMMAND, wrap="char", copy=self.copy)
        self.command_block.frame.grid(row=3, column=0, sticky="ew")
        self.command = self.command_block.box
        self.copy_command = self.command_block.copy
        actions = kit.frame(page)
        actions.grid(row=4, column=0, sticky="ew", pady=Space.M)
        self.status_dot = self.ctk.CTkFrame(
            actions, width=DOT, height=DOT, corner_radius=DOT // 2, fg_color=BORDER_STRONG
        )
        self.status_dot.pack(side="left", padx=(Space.XS, Space.S))
        self.status = kit.label(actions, size=LABEL, colour=TEXT_SECONDARY)
        self.status.pack(side="left", fill="x", expand=True)
        self.run_button = kit.button(actions, "Run", self.press_run, kind=PRIMARY)
        self.run_button.pack(side="right")
        self.stop_button = kit.button(actions, "Stop", self.press_stop)
        self.log_block = Code(kit, page, "Output", height=OUTPUT, wrap="word")
        self.log_block.frame.grid(row=5, column=0, sticky="nsew")
        self.log = self.log_block.box
        self.log.configure(text_color=TEXT_SECONDARY)
        self.log_block.show_empty(EMPTY_LOG)

    def _build_checks_page(self) -> None:
        kit = self.kit
        page = self.checks_page = kit.frame(self.pages)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        kit.label(page, "Check dependencies", size=TITLE, strong=True).grid(row=0, column=0, sticky="ew")
        self.summary = kit.label(page, size=BODY, colour=TEXT_SECONDARY, wrap=WIDE)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(Space.S, Space.L))
        self.reflow.wrap(self.summary)
        table = kit.card(page)
        table.grid(row=2, column=0, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(2, weight=1)
        head = kit.frame(table)
        head.grid(row=0, column=0, sticky="ew", padx=Space.M, pady=(Space.M, Space.S))
        for text, width in (("Status", STATUS_COLUMN), ("Check", NAME_COLUMN), ("Found", 0)):
            label = kit.label(head, text, size=SMALL, colour=TEXT_MUTED, strong=True)
            label.configure(width=width)
            label.pack(side="left", padx=(Space.S, 0))
        kit.line(table).grid(row=1, column=0, sticky="ew", padx=1)
        self.checks_scroll = kit.scroll(table, RAISED)
        self.checks_scroll.frame.grid(row=2, column=0, sticky="nsew", padx=(Space.XS, Space.XS), pady=Space.XS + 2)
        self.checks_list = self.checks_scroll.frame
        self.checks_list.grid_columnconfigure(0, weight=1)
        self.checks_scroll.canvas.bind("<Configure>", self.wrap_details, add="+")
        self.fix_block = Code(kit, page, "How to fix it", height=FIX, wrap="word", copy=self.copy_fix)
        self.fix_block.frame.grid(row=3, column=0, sticky="ew", pady=(Space.L, 0))
        self.fix = self.fix_block.box
        self.copy_fix_button = self.fix_block.copy
        actions = kit.frame(page)
        actions.grid(row=4, column=0, sticky="ew", pady=(Space.M, 0))
        self.check_button = kit.button(actions, "Check again", self.check, kind=PRIMARY)
        self.check_button.pack(side="right")

    # -- the list of steps -----------------------------------------------------------------
    def fill_list(self) -> None:
        """Build the list for the chosen headset: its groups and their entries."""
        for child in self.list.winfo_children():
            child.destroy()
        self.entries.clear()
        self.marks.clear()
        kit = self.kit
        for n, (group, entries) in enumerate(self.model.steps.groups()):
            kit.label(self.list, group, size=SMALL, colour=TEXT_MUTED, strong=True).pack(
                fill="x", padx=Space.M, pady=(Space.L if n else Space.XS, Space.XS)
            )
            for entry in entries:
                row = self.ctk.CTkFrame(self.list, fg_color="transparent", corner_radius=RADIUS_CONTROL)
                row.pack(fill="x", pady=1, padx=(0, Space.XS))
                mark = self.ctk.CTkFrame(
                    row, width=MARK[0], height=MARK[1], corner_radius=MARK[0] // 2 + 1, fg_color="transparent"
                )
                mark.pack(side="left", padx=(Space.XS, 0))
                label = kit.label(row, entry.title, size=LABEL, wrap=SIDEBAR - 2 * Space.XL - Space.S)
                label.pack(fill="x", padx=(Space.S, Space.S), pady=Space.S - 1)
                kit.clickable(
                    row,
                    (mark, label),
                    lambda k=entry.key: self.select(k),
                    selected=lambda k=entry.key: k == self.model.steps.selected,
                )
                self.entries[entry.key] = (row, label)
                self.marks[entry.key] = mark
        headset = self.model.steps.headset
        self.headset_name.configure(
            text=headset.info.name
            if headset is not None
            else "No headsets in devices/: download one on the headset page"
        )
        self.about.configure(text=self.model.steps.about())
        self.update_list()

    def update_list(self) -> None:
        """Mark the selected entry, the running step and the checks' count."""
        steps, run = self.model.steps, self.model.run
        for key, (row, label) in self.entries.items():
            selected = key == steps.selected
            row.configure(fg_color=ACCENT_SOFT if selected else "transparent")
            self.marks[key].configure(fg_color=ACCENT if selected else "transparent")
            if key == DOCTOR:
                text = self.model.checks.label(steps.codename())
            else:
                step = next((s for s in steps.steps() if s.key == key), None)
                text = step.title if step is not None else key
                if run.running and run.key == key:
                    text += " (running)"
            running = run.running and run.key == key
            label.configure(
                text=text, font=self.kit.font(LABEL, strong=selected), text_color=ACCENT_TEXT if running else TEXT
            )

    def refill(self) -> None:
        """List the steps of the headset the window has just chosen; its page follows when the view is shown."""
        self.hide_panel()
        self.shown_step = ""
        self.fill_list()

    def select(self, key: str) -> None:
        """Select the entry KEY: a step, or the checks."""
        if self.model.steps.select(key):
            self.render()
            self.scroll.canvas.yview_moveto(0)  # a new page starts at its top

    # -- rendering -------------------------------------------------------------------------
    def render(self) -> None:
        """Show the selected entry's page; the checks run the first time they are shown."""
        self.update_list()
        if self.model.steps.selected == DOCTOR:
            self.step_page.grid_forget()
            self.show_page(self.checks_page)
            codename = self.model.steps.codename()
            if codename not in self.model.checks.results:
                self.model.checks.start(self.model.steps.headset)
            self.render_checks()
            return
        self.checks_page.grid_forget()
        self.show_page(self.step_page)
        self.render_step()

    def show_page(self, page: Any) -> None:  # noqa: ANN401  # a CTkFrame
        """Grid PAGE in the content, in the column the window's width allows."""
        if page.winfo_manager() != "grid":
            page.grid(row=0, column=0, sticky="nsew", padx=self.reflow.padx, pady=(Space.XXL, Space.XL))

    def render_step(self) -> None:
        """Show the selected step: title, what it does, its parameters, its command line, the run."""
        step = self.model.steps.step()
        if step is None:
            return
        if step.key != self.shown_step:
            self.shown_step = step.key
            self.title.configure(text=step.title)
            self.help.configure(text=self.model.steps.help(step))
            self.build_form(step)
        self.set_text(self.command, self.model.steps.command())
        self.render_run()

    def build_form(self, step: Step) -> None:
        """Build STEP's parameters in a card: a text, a choice, a path with its browse button, a flag."""
        for child in self.form.winfo_children():
            child.destroy()
        self.vars = {}
        if not step.params:
            self.form.grid_forget()
            return
        self.form.grid(row=2, column=0, sticky="ew", pady=(0, Space.L))
        values = self.model.steps.values_of(step)
        for n, param in enumerate(step.params):
            self.param_row(2 * n, step, param, values[param.name], last=n == len(step.params) - 1)

    def param_row(self, row: int, step: Step, param: Param, value: str, *, last: bool = False) -> None:
        """Add PARAM's label, its widget and its help at ROW (and the row below), in the form's grid."""
        kit = self.kit
        top = Space.L if row == 0 else Space.M
        bottom = Space.L if last else 0
        label = kit.label(self.form, param.label, size=LABEL, strong=True)
        label.configure(width=FORM_LABEL)
        label.grid(row=row, column=0, sticky="w", padx=(Space.L, Space.L), pady=(top, 0 if param.help else bottom))
        var = self.ctk.StringVar(value=value)
        pad = {"pady": (top, 0 if param.help else bottom)}
        if param.kind == "flag":
            widget = kit.flag(self.form, var)
            widget.grid(row=row, column=1, sticky="w", **pad)
        elif param.kind == "choice":
            widget = kit.choice(self.form, list(param.choices), var)
            widget.grid(row=row, column=1, sticky="ew", **pad)
        else:
            widget = kit.entry(self.form, textvariable=var)
            widget.grid(row=row, column=1, sticky="ew", **pad)
        kind = browse(step, param)
        if kind:
            kit.button(self.form, "Browse", lambda: self.browse(var, kind)).grid(
                row=row, column=2, sticky="e", padx=(Space.S, 0), **pad
            )
        self.form.grid_columnconfigure(3, minsize=Space.L)  # the card's right margin
        if param.help:
            kit.label(self.form, param.help, size=SMALL, colour=TEXT_MUTED).grid(
                row=row + 1, column=1, columnspan=2, sticky="w", pady=(Space.XS, bottom)
            )
        var.trace_add("write", lambda *_a, n=param.name, v=var: self.set_value(n, v.get()))
        self.vars[param.name] = var

    def set_value(self, name: str, value: str) -> None:
        """Take a parameter's new VALUE; the command line follows."""
        self.model.steps.set_value(name, value)
        self.set_text(self.command, self.model.steps.command())

    def browse(self, var: Any, kind: str) -> None:  # noqa: ANN401  # a Tk variable
        """Pick a file or folder for a path parameter; inside the repository it is kept relative."""
        dialog = self.ctk.filedialog
        path = dialog.askopenfilename(initialdir=REPO) if kind == "file" else dialog.askdirectory(initialdir=REPO)
        if path:
            var.set(relative(path))

    def render_run(self) -> None:
        """Show the run: the status line and its dot, Run or Stop, and the log's new lines."""
        run = self.model.run
        self.status.configure(text=run.status())
        self.status_dot.configure(fg_color=RUN_COLOURS.get(run.state, BORDER_STRONG))
        if run.running:
            self.run_button.pack_forget()
            self.stop_button.pack(side="right")
            self.stop_button.configure(state="normal" if run.state == RUNNING else "disabled")
        else:
            self.stop_button.pack_forget()
            self.run_button.pack(side="right")
        lines, whole = run.take()
        if lines or whole:
            self.log.configure(state="normal")
            if whole:  # more came than the log keeps while it was not shown: what it keeps replaces it
                self.log.delete("1.0", "end")
                self.log_lines = 0
            self.log.insert("end", "".join(line + "\n" for line in lines))
            self.log_lines += len(lines)
            if self.log_lines > LOG_LINES:  # the log keeps the last lines only, as the model does
                self.log.delete("1.0", f"{self.log_lines - LOG_LINES + 1}.0")
                self.log_lines = LOG_LINES
            self.log.see("end")
            self.log.configure(state="disabled")
        self.log_block.show_empty("" if self.log_lines else EMPTY_LOG)

    def render_checks(self) -> None:
        """Show the checks: grouped by what they are needed for, each with its status pill; the selected one's fix."""
        checks, codename = self.model.checks, self.model.steps.codename()
        self.summary.configure(text=checks.summary(codename) or "Checking ...")
        self.check_button.configure(state="disabled" if codename in checks.checking else "normal")
        for child in self.checks_list.winfo_children():
            child.destroy()
        self.check_rows.clear()
        self.details = []
        kit = self.kit
        groups = checks.groups(codename)
        if not groups:
            kit.label(self.checks_list, "Checking ...", size=LABEL, colour=TEXT_MUTED).pack(
                fill="x", padx=Space.M, pady=Space.L
            )
        for n, (needed_for, group) in enumerate(groups):
            kit.label(
                self.checks_list, needed_for[:1].upper() + needed_for[1:], size=LABEL, colour=TEXT, strong=True
            ).pack(fill="x", padx=Space.M, pady=(Space.L if n else Space.S, Space.XS))
            for index, check in group:
                tone, word = STATUS.get(check.status, (NEUTRAL, check.status))
                row = self.ctk.CTkFrame(self.checks_list, fg_color="transparent", corner_radius=RADIUS_CONTROL)
                row.pack(fill="x", padx=Space.XS, pady=1)
                row.grid_columnconfigure(2, weight=1)
                pill = kit.pill(row, word, tone)
                pill.configure(width=STATUS_COLUMN - Space.S)
                name = kit.label(row, check.name, size=LABEL)
                name.configure(width=NAME_COLUMN)
                detail = kit.label(row, check.detail, size=SMALL, colour=TEXT_SECONDARY, wrap=self.detail_width())
                parts = (pill, name, detail)
                pill.grid(row=0, column=0, sticky="w", padx=(Space.S, Space.S), pady=Space.S - 1)
                name.grid(row=0, column=1, sticky="w", padx=(Space.S, 0))
                detail.grid(row=0, column=2, sticky="ew", padx=(Space.S, Space.S))
                kit.clickable(
                    row,
                    parts,
                    lambda i=index: self.select_check(i),
                    selected=lambda i=index: self.model.checks.selected.get(self.model.steps.codename()) == i,
                )
                self.details.append(detail)
                self.check_rows[index] = row
        self.mark_check()

    def detail_width(self) -> int:
        """Return the width the checks' details wrap at: what the list leaves after the pill and the name."""
        canvas = self.checks_scroll.canvas
        width = canvas.winfo_width() / self.kit.scaling(self.checks_list)
        return max(round(width - STATUS_COLUMN - NAME_COLUMN - 5 * Space.S), NARROWEST)

    def wrap_details(self, _event: object = None) -> None:
        """Wrap the checks' details to the list's new width."""
        width = self.detail_width()
        for label in self.details:
            with contextlib.suppress(Exception):  # it may be gone with the list
                label.configure(wraplength=width)

    def select_check(self, index: int) -> None:
        """Select the check INDEX and show its fix."""
        self.model.checks.select(self.model.steps.codename(), index)
        self.mark_check()

    def mark_check(self) -> None:
        """Shade the selected check and show how to fix it."""
        codename = self.model.steps.codename()
        selected = self.model.checks.selected.get(codename)
        for index, row in self.check_rows.items():
            row.configure(fg_color=ACCENT_SOFT if index == selected else "transparent")
        fix = self.model.checks.fix(codename)
        self.set_text(self.fix, fix)
        self.fix_block.show_empty("" if fix else EMPTY_FIX)

    @staticmethod
    def set_text(box: Any, text: str) -> None:  # noqa: ANN401  # a CTkTextbox
        """Replace a read-only text box's text."""
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    # -- actions -------------------------------------------------------------------------
    def clip(self, text: str, button: Any) -> None:  # noqa: ANN401  # a CTkButton
        """Put TEXT on the clipboard; BUTTON says so for a moment."""
        if not text:
            return
        root = self.window.root
        root.clipboard_clear()
        root.clipboard_append(text)
        self.kit.retext(button, "Copied")
        root.after(COPIED_MS, lambda: self.kit.retext(button, "Copy"))

    def copy(self) -> None:
        """Copy the selected step's command line."""
        self.clip(self.model.steps.command(), self.copy_command)

    def copy_fix(self) -> None:
        """Copy the selected check's fix."""
        self.clip(self.model.checks.fix(self.model.steps.codename()), self.copy_fix_button)

    def check(self) -> None:
        """Run the checks again."""
        if self.model.checks.start(self.model.steps.headset):
            self.render_checks()

    def press_run(self) -> None:
        """Run the selected step: refused while something runs, asked first when it writes."""
        kind, title, text = self.model.press(installer_running=self.window.model.running)
        if kind == REFUSED:
            self.show_panel(title, text, accept="OK", decline="", on_answer=lambda **_answer: None)
        elif kind == ASK:
            self.show_panel(title, text, accept="Run", decline="Cancel", on_answer=self.confirmed)
        elif kind == START:
            self.confirmed(accepted=True)

    def confirmed(self, *, accepted: bool) -> None:
        """Start the step once it is confirmed (or needs no confirmation)."""
        if accepted and self.model.start():
            self.update_list()
            self.render_run()

    def press_stop(self) -> None:
        """Stop the running step; one that writes to the headset asks first."""
        if not self.model.run.writes:
            self.stop()
            return
        self.show_panel(
            "Stop a step that writes?",
            "It writes to the headset now. Stopping it can leave what it writes half done; letting it finish is safer.",
            accept="Stop",
            decline="Keep going",
            on_answer=lambda accepted: self.stop() if accepted else None,
        )

    def stop(self) -> None:
        """Stop the running step's program."""
        self.model.stop()
        self.render_run()

    def ask_to_close(self) -> None:
        """Ask before closing while a step runs; the window closes once its program has exited."""
        text = "The window closes once the step's program has ended."
        if self.model.run.writes:
            text = "It writes to the headset now: stopping it can leave what it writes half done. " + text

        def answer(*, accepted: bool) -> None:
            if accepted:
                self.window.closing = True
                self.model.stop()
                self.render_run()

        self.show_panel(
            "Stop the step and close?", text, accept="Stop and close", decline="Keep going", on_answer=answer
        )

    # -- the panel -------------------------------------------------------------------------
    def show_panel(self, title: str, text: str, *, accept: str, decline: str, on_answer: Callable[..., object]) -> None:
        """Show a panel over the right side; ON_ANSWER gets accepted= once it is answered."""
        self.hide_panel()

        def answered(accepted: bool, _values: dict[str, str]) -> None:  # noqa: FBT001  # the panel's callback
            self.hide_panel()
            on_answer(accepted=accepted)

        self.panel, _ = self.window.build_panel(
            self.content, title, text, fields=[], accept=accept, decline=decline, on_answer=answered
        )

    def hide_panel(self) -> None:
        """Remove the panel."""
        if self.panel is not None:
            with contextlib.suppress(Exception):  # the widget may be gone with its window
                self.panel.destroy()
        self.panel = None

    # -- every 100 ms ------------------------------------------------------------------------
    def tick(self) -> None:
        """Take a step's output and the checks' results, and show them; fit the page's area to the page."""
        if self.frame.winfo_ismapped():
            self.scroll.fit()  # a filled area hears of no growth of its page: a form, a longer help
        ended = self.model.receive()
        if ended or self.model.run.unseen or self.model.run.overflowed:
            if ended:
                self.update_list()
            if self.model.steps.selected != DOCTOR:
                self.render_run()
            elif ended:
                self.status.configure(text=self.model.run.status())
        updated = self.model.checks.receive()
        if self.model.steps.codename() in updated:
            self.update_list()
            if self.model.steps.selected == DOCTOR:
                self.render_checks()
