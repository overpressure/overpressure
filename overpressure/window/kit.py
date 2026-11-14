# SPDX-License-Identifier: Unlicense
"""The window's components, built from theme.py's tokens: the views put these together and style nothing themselves.

Buttons (primary, secondary, quiet, the header's links) with hover and pressed shades, cards,
clickable list rows, status icons, pills, notes in a tone, code blocks with a copy button,
progress bars, fields, pictures, a scrolling area that shows its bar only when needed, and the
reflow that keeps a page's column readable in a wide window. CustomTkinter is handed in as a module
(window.main imports it), so this module imports without it.
"""

from __future__ import annotations

import contextlib
import sys
import warnings
from typing import TYPE_CHECKING, Any, Callable

from overpressure.window.installer import DONE, FAILED, RUNNING, SKIPPED
from overpressure.window.theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_PRESSED,
    ACCENT_SOFT,
    BODY,
    BORDER,
    BORDER_STRONG,
    BORDER_SUBTLE,
    BOX,
    CHECK,
    CONTROL,
    CONTROL_SMALL,
    DANGER_FILL,
    FIELD,
    GLYPH,
    HOVER,
    LABEL,
    MONO,
    NARROWEST,
    ON_ACCENT,
    PILL,
    PROGRESS,
    RADIUS_CARD,
    RADIUS_CONTROL,
    RAISED,
    SMALL,
    SMALL_CONTROL,
    SUNKEN,
    TEXT,
    TEXT_MUTED,
    TEXT_SECONDARY,
    TONES,
    Families,
    Space,
    families,
)

if TYPE_CHECKING:
    from pathlib import Path

    from overpressure.window.theme import Pair

#: The kinds of button: the view's one main action, an outlined one, a text-only one, the header's links.
PRIMARY, SECONDARY, QUIET, HEADER = "primary", "secondary", "quiet", "header"
#: A button's look by kind: fill, hover, pressed, text, border colour (None: no border), text size, strong.
BUTTONS: dict[str, tuple[Pair | str, Pair, Pair, Pair | str, Pair | None, int, bool]] = {
    PRIMARY: (ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ON_ACCENT, None, BODY, True),
    SECONDARY: ("transparent", HOVER, BORDER_SUBTLE, TEXT, BORDER_STRONG, LABEL, True),
    QUIET: ("transparent", HOVER, BORDER_SUBTLE, TEXT_SECONDARY, None, LABEL, False),
    HEADER: ("transparent", HOVER, BORDER_SUBTLE, TEXT_SECONDARY, None, LABEL, False),
}
CLICK = "hand2"  # the pointer over a row that opens something
PRIMARY_WIDTH = 96  # the least width of a main button


class Kit:
    """The components, for one window: its fonts (picked once from what the system has) and its widgets."""

    def __init__(self, ctk_module: Any, root: Any) -> None:  # noqa: ANN401  # customtkinter, imported lazily; its root
        """Pick the fonts for ROOT's display; CTK_MODULE builds the widgets."""
        import tkinter.font  # noqa: PLC0415  # Tk only with the window: the tool imports without it

        self.ctk = ctk_module
        self.root = root
        # Tk's own faces, the fallbacks (nametofont has root= from Python 3.10 on only).
        default = tkinter.font.Font(root=root, name="TkDefaultFont", exists=True).actual("family")
        fixed = tkinter.font.Font(root=root, name="TkFixedFont", exists=True).actual("family")
        self.families: Families = families(tkinter.font.families(root), sys.platform, system=default, fixed=fixed)
        self.fonts: dict[tuple[int, bool, bool], Any] = {}
        self.images: dict[Path, Any] = {}  # the pictures' Tk images: Tk forgets one that Python no longer holds

    # -- type ----------------------------------------------------------------------------------
    def font(self, size: int, *, strong: bool = False, mono: bool = False) -> Any:  # noqa: ANN401  # a CTkFont
        """Return the font for text of SIZE (theme's type scale), STRONG or MONO; one object each, shared."""
        key = (size, strong, mono)
        if key not in self.fonts:
            family, weight = self.families.face(size, strong=strong, mono=mono)
            self.fonts[key] = self.ctk.CTkFont(family=family, size=size, weight=weight)
        return self.fonts[key]

    def label(  # noqa: PLR0913  # a label is its parts
        self,
        parent: Any,  # noqa: ANN401  # a Tk widget
        text: str = "",
        *,
        size: int = BODY,
        colour: Pair | str = TEXT,
        strong: bool = False,
        wrap: int = 0,
        mono: bool = False,
    ) -> Any:  # noqa: ANN401  # a CTkLabel
        """Return a left-aligned text; WRAP (points) breaks it into lines."""
        return self.ctk.CTkLabel(
            parent,
            text=text,
            font=self.font(size, strong=strong, mono=mono),
            text_color=colour,
            anchor="w",
            justify="left",
            wraplength=wrap,
            height=1,  # as high as its text: CustomTkinter's default is a button's height
        )

    def scaling(self, widget: Any) -> float:  # noqa: ANN401  # a CustomTkinter widget
        """Return WIDGET's scaling: points to pixels."""
        return float(self.ctk.ScalingTracker.get_widget_scaling(widget))

    # -- pictures ------------------------------------------------------------------------------
    def image(self, path: Path) -> Any:  # noqa: ANN401  # a tkinter.PhotoImage
        """Return the Tk image of the PNG file at PATH, loaded once."""
        if path not in self.images:
            import tkinter as tk  # noqa: PLC0415  # Tk only with the window: the tool imports without it

            self.images[path] = tk.PhotoImage(master=self.root, file=str(path))
        return self.images[path]

    def picture(self, parent: Any, path: Path) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a label showing the PNG file at PATH pixel for pixel, on its parent's colour (the logo's renderings).

        CustomTkinter scales only its CTkImage, which needs Pillow; the caller picks the rendering for the
        display's scaling instead (logo.py).
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # CustomTkinter's advice to use CTkImage
            return self.ctk.CTkLabel(parent, text="", image=self.image(path), width=1, height=1, fg_color="transparent")

    def show(self, picture: Any, path: Path) -> None:  # noqa: ANN401  # a picture's label
        """Show the PNG file at PATH in PICTURE (a label from picture())."""
        with warnings.catch_warnings(), contextlib.suppress(Exception):  # it may be gone with its page
            warnings.simplefilter("ignore")  # CustomTkinter's advice to use CTkImage
            picture.configure(image=self.image(path))

    # -- buttons -------------------------------------------------------------------------------
    def button(
        self,
        parent: Any,  # noqa: ANN401  # a Tk widget
        text: str,
        command: Callable[[], object],
        *,
        kind: str = SECONDARY,
        small: bool = False,
    ) -> Any:  # noqa: ANN401  # a CTkButton
        """Return a button of KIND (PRIMARY, SECONDARY, QUIET, HEADER), SMALL for a code block's or a row's."""
        fill, hover, pressed, text_colour, border, size, strong = BUTTONS[kind]
        button = self.ctk.CTkButton(
            parent,
            text=text,
            font=self.font(SMALL if small else size, strong=strong),
            fg_color=fill,
            hover_color=hover,
            text_color=text_colour,
            text_color_disabled=TEXT_MUTED,
            border_width=BORDER if border else 0,
            border_color=border or BORDER_SUBTLE,
            corner_radius=RADIUS_CONTROL,
            height=SMALL_CONTROL if small else CONTROL_SMALL if kind == HEADER else CONTROL,
            command=command,
        )
        self.retext(button, text)

        def press(_event: object, colour: Pair = pressed) -> None:
            with contextlib.suppress(Exception):  # it may be gone with its panel
                button.configure(hover_color=colour)

        button.bind("<ButtonPress-1>", press)
        button.bind("<ButtonRelease-1>", lambda event: press(event, hover))
        return button

    def retext(self, button: Any, text: str) -> None:  # noqa: ANN401  # a CTkButton
        """Give BUTTON its TEXT, with room around it (CustomTkinter pads a button's text by its radius only)."""
        font = button.cget("font")
        pad = Space.S if button.cget("height") < CONTROL_SMALL else Space.L
        least = PRIMARY_WIDTH if button.cget("fg_color") == ACCENT else 0
        button.configure(text=text, width=max(font.measure(text) + 2 * pad, least))

    # -- surfaces ------------------------------------------------------------------------------
    def frame(self, parent: Any, colour: Pair | str = "transparent") -> Any:  # noqa: ANN401  # Tk widgets
        """Return a plain frame (a layout box) in COLOUR, square."""
        return self.ctk.CTkFrame(parent, fg_color=colour, corner_radius=0, border_width=0, width=0, height=0)

    def card(self, parent: Any, *, colour: Pair = RAISED, radius: int = RADIUS_CARD) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a card: a raised surface with a subtle border and rounded corners."""
        return self.ctk.CTkFrame(
            parent, fg_color=colour, border_color=BORDER_SUBTLE, border_width=BORDER, corner_radius=radius
        )

    def line(self, parent: Any, *, vertical: bool = False) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a one-point divider; it is packed or gridded to stretch."""
        size = {"width": BORDER} if vertical else {"height": BORDER}
        return self.ctk.CTkFrame(parent, fg_color=BORDER_SUBTLE, corner_radius=0, border_width=0, **size)

    def pill(self, parent: Any, text: str, tone: str) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a status pill: TEXT in the TONE's colour on its soft tint (Last used, writes to the headset, ok)."""
        colours = TONES[tone]
        return self.ctk.CTkLabel(
            parent,
            text=text,
            font=self.font(SMALL, strong=True),
            fg_color=colours.soft,
            text_color=colours.text,
            corner_radius=RADIUS_CARD,
            height=PILL,
        )

    def progress(self, parent: Any) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a thin progress bar in the accent colour, indeterminate until it has a fraction."""
        return self.ctk.CTkProgressBar(
            parent,
            height=PROGRESS,
            corner_radius=PROGRESS // 2,
            border_width=0,
            progress_color=ACCENT,
            fg_color=SUNKEN,
            mode="indeterminate",
        )

    def textbox(self, parent: Any, *, height: int, wrap: str) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a read-only monospaced text on the sunken surface (output, command lines, fixes)."""
        box = self.ctk.CTkTextbox(
            parent,
            height=height,
            font=self.font(MONO, mono=True),
            fg_color=SUNKEN,
            text_color=TEXT,
            border_width=0,
            corner_radius=0,
            wrap=wrap,
            scrollbar_button_color=BORDER_STRONG,
            scrollbar_button_hover_color=TEXT_MUTED,
        )
        box.configure(state="disabled")
        return box

    def scroll(self, parent: Any, colour: Pair, *, fill: bool = False) -> Scroll:  # noqa: ANN401  # Tk widgets
        """Return a scrolling area in COLOUR whose bar shows only when its content is taller than it; see Scroll."""
        return Scroll(self, parent, colour, fill=fill)

    # -- fields ----------------------------------------------------------------------------------
    def focus_ring(self, widget: Any) -> None:  # noqa: ANN401  # a CTkEntry or CTkComboBox
        """Outline WIDGET in the accent colour while it has the keyboard."""
        widget.bind("<FocusIn>", lambda _e: widget.configure(border_color=ACCENT))
        widget.bind("<FocusOut>", lambda _e: widget.configure(border_color=BORDER_STRONG))

    def entry(self, parent: Any, **options: Any) -> Any:  # noqa: ANN401  # Tk widgets, CTkEntry's options
        """Return a text field."""
        entry = self.ctk.CTkEntry(
            parent,
            height=CONTROL,
            font=self.font(BODY),
            fg_color=FIELD,
            text_color=TEXT,
            border_color=BORDER_STRONG,
            border_width=BORDER,
            corner_radius=RADIUS_CONTROL,
            **options,
        )
        self.focus_ring(entry)
        return entry

    def choice(self, parent: Any, values: list[str], variable: Any) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a drop-down choice of VALUES bound to VARIABLE."""
        box = self.ctk.CTkComboBox(
            parent,
            values=values,
            variable=variable,
            height=CONTROL,
            font=self.font(BODY),
            dropdown_font=self.font(BODY),
            fg_color=FIELD,
            text_color=TEXT,
            border_color=BORDER_STRONG,
            border_width=BORDER,
            corner_radius=RADIUS_CONTROL,
            button_color=BORDER_STRONG,
            button_hover_color=TEXT_MUTED,
            dropdown_fg_color=RAISED,
            dropdown_hover_color=HOVER,
            dropdown_text_color=TEXT,
        )
        self.focus_ring(box)
        return box

    def flag(self, parent: Any, variable: Any) -> Any:  # noqa: ANN401  # Tk widgets
        """Return a check box bound to VARIABLE ("1" on, "0" off)."""
        return self.ctk.CTkCheckBox(
            parent,
            text="",
            variable=variable,
            onvalue="1",
            offvalue="0",
            width=BOX + 2,
            checkbox_width=BOX,
            checkbox_height=BOX,
            corner_radius=RADIUS_CONTROL - 1,
            border_width=2 * BORDER,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            border_color=BORDER_STRONG,
            checkmark_color=ON_ACCENT,
        )

    # -- rows ------------------------------------------------------------------------------------
    def clickable(  # noqa: PLR0913  # a row, what it is made of, what a click does, its colours
        self,
        row: Any,  # noqa: ANN401  # a CTkFrame
        parts: tuple[Any, ...],
        on_click: Callable[[], object],
        *,
        selected: Callable[[], bool] = lambda: False,
        rest: Pair | str = "transparent",
        chosen: Pair | str = ACCENT_SOFT,
    ) -> None:
        """Make ROW (and its PARTS) a list row: shaded under the pointer, CHOSEN while SELECTED, ON_CLICK on a click."""

        def shade(*, inside: bool) -> None:
            with contextlib.suppress(Exception):  # it may be gone with its list
                row.configure(fg_color=chosen if selected() else HOVER if inside else rest)

        for widget in (row, *parts):
            widget.bind("<Enter>", lambda _e: shade(inside=True))
            widget.bind("<Leave>", lambda _e: shade(inside=False))
            widget.bind("<Button-1>", lambda _e: on_click())
            with contextlib.suppress(Exception):  # a widget without a pointer option
                widget.configure(cursor=CLICK)


class Icon:
    """A status circle drawn by CustomTkinter (smooth at any scaling): a check, a dot, a mark or empty."""

    def __init__(self, kit: Kit, parent: Any, size: int) -> None:  # noqa: ANN401  # a Tk widget
        """Build the circle of SIZE points in PARENT."""
        ctk = kit.ctk
        self.frame = ctk.CTkFrame(
            parent, width=size, height=size, corner_radius=size // 2, border_width=2 * BORDER, fg_color="transparent"
        )
        # The glyph as tight as its text, on the circle's fill: its box stays inside the circle.
        self.mark = ctk.CTkLabel(
            self.frame,
            text="",
            width=1,
            height=1,
            corner_radius=0,
            fg_color="transparent",
            font=kit.font(max(round(size * 0.5), SMALL - 2), strong=True),
            text_color=ON_ACCENT,
            padx=0,
            pady=0,
        )
        dot = max(size // 2 - 3, 6)
        self.dot = ctk.CTkFrame(self.frame, width=dot, height=dot, corner_radius=dot // 2, fg_color=ACCENT)
        self.state = ""

    def set(self, state: str) -> Icon:
        """Show STATE: done (check), skipped (muted check), running (dot), failed (mark), pending (empty)."""
        if state == self.state:
            return self
        self.state = state
        self.mark.place_forget()
        self.dot.place_forget()
        fills = {DONE: ACCENT, SKIPPED: TEXT_MUTED, FAILED: DANGER_FILL}
        fill = fills.get(state)
        if fill is not None:
            self.frame.configure(fg_color=fill, border_color=fill)
            self.mark.configure(text="!" if state == FAILED else CHECK)
            self.mark.place(relx=0.5, rely=0.5, anchor="center")
        elif state == RUNNING:
            self.frame.configure(fg_color=ACCENT_SOFT, border_color=ACCENT)
            self.dot.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.frame.configure(fg_color="transparent", border_color=BORDER_STRONG)
        return self


class Ring:
    """A round meter of how much of a run is done: a track, the share in the accent colour, the share in its middle.

    CustomTkinter has no round meter, so a Tk canvas draws it, in pixels for the display's scaling and in
    the colours of the appearance it shows, which it follows.
    """

    def __init__(self, kit: Kit, parent: Any, size: int) -> None:  # noqa: ANN401  # a Tk widget
        """Build the ring of SIZE points in PARENT, on the RAISED surface."""
        import tkinter as tk  # noqa: PLC0415  # Tk only with the window: the tool imports without it

        self.kit = kit
        self.pixels = round(size * kit.scaling(parent))
        self.canvas = tk.Canvas(parent, width=self.pixels, height=self.pixels, highlightthickness=0, borderwidth=0)
        family, weight = kit.families.face(LABEL, strong=True)
        self.font = (family, -round(self.pixels * 0.26), weight)  # a negative size is in pixels
        self.share = 0.0
        self.draw()
        kit.ctk.AppearanceModeTracker.add(lambda _mode: self.draw(), self.canvas)

    def set(self, share: float) -> None:
        """Show SHARE (0 to 1) done; a change below a whole percent waits for the next."""
        share = min(max(share, 0.0), 1.0)
        if round(share * 100) != round(self.share * 100):
            self.share = share
            self.draw()

    def draw(self) -> None:
        """Draw the ring for its share in the appearance shown now."""
        if not self.canvas.winfo_exists():
            return
        light = self.kit.ctk.get_appearance_mode() == "Light"

        def colour(pair: tuple[str, str]) -> str:
            return pair[0] if light else pair[1]

        canvas, size = self.canvas, self.pixels
        line = max(round(size * 0.1), 3)
        box = (line / 2 + 1, line / 2 + 1, size - line / 2 - 1, size - line / 2 - 1)
        canvas.delete("all")
        canvas.configure(background=colour(RAISED))
        canvas.create_oval(*box, outline=colour(BORDER_SUBTLE), width=line)
        if self.share >= 1:
            canvas.create_oval(*box, outline=colour(ACCENT), width=line)
        elif self.share > 0:
            canvas.create_arc(*box, start=90, extent=-360 * self.share, style="arc", outline=colour(ACCENT), width=line)
        canvas.create_text(size / 2, size / 2, text=f"{self.share:.0%}", fill=colour(TEXT), font=self.font)


class Note:
    """A note box in a tone (info, warning, danger): a round glyph and the text, on the tone's tint."""

    def __init__(self, kit: Kit, parent: Any, *, wrap: int) -> None:  # noqa: ANN401  # a Tk widget
        """Build the box in PARENT, its text wrapped at WRAP points until it is laid out, then to its width."""
        ctk = kit.ctk
        self.kit = kit
        self.frame = ctk.CTkFrame(parent, border_width=BORDER, corner_radius=RADIUS_CARD)
        self.frame.grid_columnconfigure(1, weight=1)
        self.glyph = ctk.CTkLabel(
            self.frame, text="", width=GLYPH, height=GLYPH, corner_radius=GLYPH // 2, font=kit.font(SMALL, strong=True)
        )
        self.glyph.grid(row=0, column=0, sticky="nw", padx=(Space.M, Space.M - 2), pady=Space.M)
        self.text = kit.label(self.frame, size=LABEL, wrap=wrap)
        self.text.grid(row=0, column=1, sticky="ew", padx=(0, Space.L), pady=Space.M)
        self.frame.bind("<Configure>", self.fit)
        self.tone = ""

    def fit(self, event: Any) -> None:  # noqa: ANN401  # a Tk event
        """Wrap the text to the room the box leaves it."""
        scaling = self.kit.scaling(self.frame)
        start = max(self.text.winfo_x(), self.glyph.winfo_reqwidth() + round((2 * Space.M - 2) * scaling))
        room = round((event.width - start) / scaling) - Space.L - 2 * BORDER
        if room > 0 and abs(room - self.text.cget("wraplength")) > 1:
            self.text.configure(wraplength=room)

    def set(self, text: str, tone: str) -> Note:
        """Show TEXT in TONE."""
        colours = TONES[tone]
        if tone != self.tone:
            self.tone = tone
            self.frame.configure(fg_color=colours.soft, border_color=colours.border)
            self.glyph.configure(text=colours.glyph, fg_color=colours.text, text_color=colours.soft)
            self.text.configure(text_color=colours.text)
        self.text.configure(text=text)
        return self


class Code:
    """A code block: a caption, a copy button (when it has one), the text on the sunken surface, an empty state."""

    def __init__(  # noqa: PLR0913  # a block is its parts
        self,
        kit: Kit,
        parent: Any,  # noqa: ANN401  # a Tk widget
        caption: str,
        *,
        height: int,
        wrap: str,
        copy: Callable[[], object] | None = None,
        radius: int = RADIUS_CARD,
    ) -> None:
        """Build the block in PARENT: CAPTION over the text box of HEIGHT points; COPY is the copy button's action."""
        ctk = kit.ctk
        self.frame = ctk.CTkFrame(
            parent,
            fg_color=SUNKEN,
            border_color=BORDER_SUBTLE,
            border_width=BORDER if radius else 0,
            corner_radius=radius,
        )
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(1, weight=1)
        inset = Space.M if radius else Space.L
        self.caption = kit.label(self.frame, caption, size=SMALL, colour=TEXT_MUTED, strong=True)
        self.caption.grid(row=0, column=0, sticky="w", padx=inset, pady=(Space.S, 0))
        self.copy = None
        if copy is not None:
            self.copy = kit.button(self.frame, "Copy", copy, kind=QUIET, small=True)
            self.copy.grid(row=0, column=1, sticky="e", padx=Space.S, pady=(Space.XS, 0))
        self.box = kit.textbox(self.frame, height=height, wrap=wrap)
        self.box.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(inset - 4, Space.XS), pady=(0, Space.S))
        self.empty = kit.label(self.frame, "", size=LABEL, colour=TEXT_MUTED)

    def show_empty(self, text: str) -> None:
        """Show TEXT in the middle of the empty block; "" hides it."""
        if text:
            self.empty.configure(text=text, fg_color=SUNKEN)
            self.empty.place(in_=self.box, relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty.place_forget()


class Scroll:
    """A vertical scrolling area whose bar shows only when the content is taller than the area.

    The bar keeps its place and turns into the area's colour while nothing scrolls: taking it out
    of the grid is not enough, Tk shows a bar removed from a hidden area again once the area shows.
    """

    def __init__(self, kit: Kit, parent: Any, colour: Pair, *, fill: bool = False) -> None:  # noqa: ANN401  # a Tk widget
        """Build the area in COLOUR in PARENT; its frame holds the content and grids like any frame.

        FILL: the content is at least as high as the area (a page whose log takes the rest), else as high as it needs.
        """
        self.colour, self.fill = colour, fill
        self.frame = kit.ctk.CTkScrollableFrame(
            parent,
            fg_color=colour,
            corner_radius=0,
            border_width=0,
            scrollbar_fg_color="transparent",
            scrollbar_button_color=colour,
            scrollbar_button_hover_color=colour,
        )
        self.canvas = self.frame._parent_canvas  # noqa: SLF001  # CTkScrollableFrame has no option for its height
        self.item = self.frame._create_window_id  # noqa: SLF001  # the same
        self.shown = False
        self.frame.bind("<Configure>", self.fit, add="+")
        self.canvas.bind("<Configure>", self.fit, add="+")

    def fit(self, _event: object = None) -> None:
        """Show the bar while the content is taller than the area; hidden, the view is at the top."""
        if not self.frame.winfo_exists():
            return  # gone with its window
        wanted, height = self.frame.winfo_reqheight(), self.canvas.winfo_height()
        if self.fill:
            self.canvas.itemconfigure(self.item, height=max(wanted, height))
        need = wanted > height + 1
        if need != self.shown:
            self.shown = need
            self.frame.configure(
                scrollbar_button_color=BORDER_STRONG if need else self.colour,
                scrollbar_button_hover_color=TEXT_MUTED if need else self.colour,
            )
            if not need:
                self.canvas.yview_moveto(0)


class Reflow:
    """A page's column in a HOST: at most WIDTH points wide, its margins growing with the window; its texts wrapped."""

    def __init__(
        self,
        kit: Kit,
        host: Any,  # noqa: ANN401  # a CTkFrame
        width: int,
        *,
        sides: tuple[int, int],
        center: bool = False,
    ) -> None:
        """Follow HOST's width: SIDES are the least margins, CENTER splits the rest instead of leaving it right."""
        self.kit, self.host, self.width, self.sides, self.center = kit, host, width, sides, center
        self.column = width
        self.padx = sides  # the margins now, for a widget gridded later
        self.targets: list[tuple[Any, int]] = []
        self.labels: list[tuple[Any, int]] = []
        host.bind("<Configure>", self.fit, add=True)

    def add(self, widget: Any, *, shift: int = 0) -> None:  # noqa: ANN401  # a gridded Tk widget
        """Keep WIDGET (gridded) in the column; SHIFT moves its left edge out (a quiet button's text lines up)."""
        self.targets.append((widget, shift))

    def wrap(self, label: Any, inset: int = 0) -> None:  # noqa: ANN401  # a CTkLabel
        """Wrap LABEL to the column, less INSET points."""
        self.labels.append((label, inset))
        label.configure(wraplength=max(self.column - inset, NARROWEST))

    def fit(self, event: Any) -> None:  # noqa: ANN401  # a Tk event
        """Lay the column out for the host's new width."""
        left, right = self.sides
        width = event.width / self.kit.scaling(self.host)
        column = max(min(self.width, width - left - right), 240)
        extra = max(width - left - right - column, 0)
        padx = (round(left + extra / 2), round(right + extra / 2)) if self.center else (left, round(right + extra))
        self.padx = padx
        for widget, shift in self.targets:
            if widget.winfo_exists() and widget.winfo_manager() == "grid":  # a page may be gone with its window
                # A parent wider than the host (the action bar beside a scrolling area's bar) keeps the column's edges.
                wider = max(widget.master.winfo_width() - event.width, 0) / self.kit.scaling(self.host)
                widget.grid(padx=(padx[0] - shift, round(padx[1] + wider)))
        if abs(column - self.column) >= 1:
            self.column = column
            for label, inset in self.labels:
                with contextlib.suppress(Exception):  # it may be gone
                    label.configure(wraplength=max(round(column - inset), NARROWEST))
