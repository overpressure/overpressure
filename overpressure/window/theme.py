# SPDX-License-Identifier: Unlicense
"""The window's design tokens: spacing, radii, sizes, colours as (light, dark) pairs, the type scale and its fonts.

Every view takes its look from here (kit.py builds the components from these tokens), so no view
holds a colour, a size or a padding of its own. CustomTkinter picks a pair's light or dark half
by the system's appearance. The look is flat and calm: raised surfaces (the header, the sidebars,
cards) on a slightly darker page, a sunken surface for program output and command lines, one
accent colour for the one main button of a view and for what is current or chosen, and soft
tints for notes and status pills. Body text has a contrast of at least 4.5:1 on every surface it
stands on, in both modes (tests/test_theme.py checks it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from overpressure import doctor

if TYPE_CHECKING:
    from collections.abc import Iterable

#: A colour for the light and the dark appearance.
Pair = tuple[str, str]


class Space:
    """The spacing scale, in points: every padding and gap is one of these."""

    XS, S, M, L, XL, XXL, XXXL = 4, 8, 12, 16, 24, 32, 48


#: Corner radii: controls (buttons, fields, rows), cards, panels (the asks' card); and the border width.
RADIUS_CONTROL, RADIUS_CARD, RADIUS_PANEL = 6, 10, 14
BORDER = 1
#: Sizes of the app shell and its controls.
CONTROL, CONTROL_SMALL = 36, 30  # a button's or a field's height
HEADER_HEIGHT = 56  # the header bar's height
SIDEBAR = 264  # the stepper's and Advanced mode's left side
CONTENT = 760  # the widest a page's content gets: wider windows leave the rest as margin
CARDS = 820  # the headset page's column of cards
WIDE = 980  # Advanced mode's pages: command lines and output want the room
PANEL = 500  # the text width of an ask's card
STEP_ICON, TASK_ICON, BADGE = 24, 20, 40  # status icons: the stepper's, a task's, the end pages'
RING = 52  # the stepper's meter of the whole run
PROGRESS = 6  # a progress bar's height
SMALL_CONTROL, PILL, GLYPH, BOX = 26, 22, 20, 20  # a code block's button, a pill, a note's glyph, a check box
LOGO = (48, 24)  # the header's mark: the logo's headset without its tile (docs/logo)
APP_ICON = 64  # the app icon on the headset page
DOT, MARK = 8, (3, 16)  # a run's status dot; the bar that marks Advanced mode's selected entry
FORM_LABEL = 170  # the width of a step's parameter names
STATUS_COLUMN, NAME_COLUMN = 76, 190  # the checks table's status pills and names
COMMAND, OUTPUT, FIX = 56, 120, 44  # the code blocks' heights: a command line, a step's output, a fix
NARROWEST = 160  # the least width a text wraps at
DETAILS = (120, 240)  # the details drawer's height: at least, at most (a third of the window between)
WINDOW = (1120, 760)  # the window's first size, where the screen has room
SCREEN_MARGIN = (80, 120)  # what the first size leaves of the screen (the taskbar, the title bar)
WINDOW_MIN = (900, 600)

# -- colours ---------------------------------------------------------------------------------
PAGE: Pair = ("#f5f6f8", "#16171a")  # the page behind the content
RAISED: Pair = ("#ffffff", "#1f2024")  # the header, the sidebars, cards, an ask's card
SUNKEN: Pair = ("#eef0f3", "#111214")  # program output, command lines
FIELD: Pair = ("#ffffff", "#18191c")  # a text field, a choice
HOVER: Pair = ("#e9ebef", "#2a2c31")  # a row or a quiet button under the pointer
BACKDROP: Pair = ("#d9dce2", "#0b0c0e")  # behind an ask: the content, dimmed
BORDER_SUBTLE: Pair = ("#e3e5e9", "#2e3036")
BORDER_STRONG: Pair = ("#c5c9d0", "#474b54")  # controls' outlines, a pending step's circle
TEXT: Pair = ("#16181d", "#ececef")
TEXT_SECONDARY: Pair = ("#4a4f58", "#b8bcc4")  # summaries, descriptions
TEXT_MUTED: Pair = ("#62676f", "#8f949d")  # captions, estimates, what is still to come
ACCENT: Pair = ("#2563eb", "#2f6ae0")  # the one main button, what is done or chosen
ACCENT_HOVER: Pair = ("#1d4fd7", "#2860d0")
ACCENT_PRESSED: Pair = ("#1a43b8", "#2154b8")
ACCENT_TEXT: Pair = ("#1d4fd7", "#8ab4ff")  # accent-coloured text on a surface
ACCENT_SOFT: Pair = ("#e9f0fe", "#1d2840")  # the selected row, an info note
ON_ACCENT = "#ffffff"
SUCCESS: Pair = ("#137333", "#5fd38d")
SUCCESS_SOFT: Pair = ("#e6f4ea", "#15281d")
WARNING: Pair = ("#8a5300", "#f0c060")
WARNING_SOFT: Pair = ("#fff5dd", "#2f2614")
WARNING_BORDER: Pair = ("#f0cd80", "#5c4718")
DANGER: Pair = ("#c4262e", "#ff8b85")
DANGER_SOFT: Pair = ("#fdecec", "#33191b")
DANGER_FILL: Pair = ("#d4343c", "#e5534b")  # a failed step's icon


@dataclass(frozen=True)
class Tone:
    """A status colour: its text, its soft tint, a tint's border and the glyph of its note."""

    text: Pair
    soft: Pair
    border: Pair
    glyph: str


INFO, SUCCESS_TONE, WARNING_TONE, DANGER_TONE, NEUTRAL = "info", "success", "warning", "danger", "neutral"
TONES = {
    INFO: Tone(ACCENT_TEXT, ACCENT_SOFT, ("#bcd0fb", "#2c3d63"), "i"),
    SUCCESS_TONE: Tone(SUCCESS, SUCCESS_SOFT, ("#a8d8b5", "#24452f"), "\N{CHECK MARK}"),
    WARNING_TONE: Tone(WARNING, WARNING_SOFT, WARNING_BORDER, "!"),
    DANGER_TONE: Tone(DANGER, DANGER_SOFT, ("#f3b8b8", "#5a2a2d"), "!"),
    NEUTRAL: Tone(TEXT_SECONDARY, SUNKEN, BORDER_SUBTLE, "i"),
}
#: A note's level from the engine, as a tone; anything else is info.
LEVELS = {"warning": WARNING_TONE, "error": DANGER_TONE}
#: A check's status as its tone and its word in the checks list.
STATUS = {
    doctor.OK: (SUCCESS_TONE, "ok"),
    doctor.MISSING: (DANGER_TONE, "missing"),
    doctor.WARNING: (WARNING_TONE, "warning"),
    doctor.OPTIONAL: (NEUTRAL, "optional"),
}
CHECK = "\N{CHECK MARK}"
CHEVRON = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"

# -- type --------------------------------------------------------------------------------------
#: The type scale in points: a page's title, a heading, body text, a control's label, small text, code.
TITLE, HEADING, BODY, LABEL, SMALL, MONO = 22, 16, 14, 13, 12, 12

#: The UI font by platform, best first; macOS also has Tk's own name for its system font.
SANS = {
    "win32": ("Segoe UI Variable Text", "Segoe UI"),
    "darwin": (".AppleSystemUIFont", "SF Pro Text", "SF Pro", "Helvetica Neue"),
    "linux": ("Inter", "Cantarell", "Noto Sans", "Ubuntu", "DejaVu Sans"),
}
#: A semibold face of the UI font where the platform has one as its own family (Tk knows only bold).
SEMIBOLD = {
    "Segoe UI Variable Text": "Segoe UI Variable Text Semibold",
    "Segoe UI": "Segoe UI Semibold",
}
#: A display face for the titles, where the UI font has one (Windows' names are cut at 31 characters).
DISPLAY = {"Segoe UI Variable Text": "Segoe UI Variable Display Semib"}
MONOSPACE = ("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono")


@dataclass(frozen=True)
class Families:
    """The font families the window uses: the UI font, its strong face, its titles' face, the monospaced one.

    A strong face equal to the UI font means Tk's bold of it.
    """

    text: str
    strong: str
    display: str
    mono: str

    def face(self, size: int, *, strong: bool = False, mono: bool = False) -> tuple[str, str]:
        """Return the family and Tk weight for SIZE: the titles' display face, a strong one or the plain one."""
        if mono:
            return self.mono, "normal"
        if strong and size >= TITLE and self.display:
            return self.display, "normal"
        if strong:
            return self.strong, "bold" if self.strong == self.text else "normal"
        return self.text, "normal"


def pick(candidates: Iterable[str], available: Iterable[str], fallback: str) -> str:
    """Return the first of CANDIDATES that is AVAILABLE, else FALLBACK."""
    have = {name.lower(): name for name in available}
    return next((have[c.lower()] for c in candidates if c.lower() in have), fallback)


def families(available: Iterable[str], platform: str, *, system: str = "", fixed: str = "") -> Families:
    """Return the best fonts among AVAILABLE for PLATFORM (sys.platform); SYSTEM and FIXED are Tk's default faces.

    No font files are bundled: the window uses what the system has, Tk's defaults when none fits.
    """
    have = {*available, system} - {""}
    key = "win32" if platform.startswith("win") else platform if platform in SANS else "linux"
    text = pick(SANS[key], have, system or "TkDefaultFont")
    strong = SEMIBOLD.get(text, "")
    display = DISPLAY.get(text, "")
    return Families(
        text=text,
        strong=strong if strong in have else text,
        display=display if display in have else "",
        mono=pick(MONOSPACE, have, fixed or "TkFixedFont"),
    )
