# SPDX-License-Identifier: Unlicense
"""The window's design: text contrast in both appearances, the fonts picked per platform, no stray colours, reflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from overpressure.window import kit, theme

WINDOW = Path(__file__).resolve().parents[1] / "overpressure" / "window"
#: Body text needs this contrast on its surface (WCAG AA).
AA = 4.5


def luminance(colour: str) -> float:
    """Return the relative luminance of a #rrggbb COLOUR."""
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    red, green, blue = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)  # the sRGB curve
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    """Return the contrast ratio of two colours."""
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class ContrastTests(unittest.TestCase):
    """Every text colour is readable on every surface it stands on, light and dark."""

    def assert_readable(self, text: theme.Pair | str, surface: theme.Pair, name: str) -> None:
        pairs = (text, text) if isinstance(text, str) else text
        for mode, (fore, back) in enumerate(zip(pairs, surface)):
            with self.subTest(name, mode="dark" if mode else "light"):
                self.assertGreaterEqual(contrast(fore, back), AA, f"{fore} on {back}")

    def test_text_on_every_surface(self):
        surfaces = {
            "page": theme.PAGE,
            "raised": theme.RAISED,
            "sunken": theme.SUNKEN,
            "field": theme.FIELD,
            "hover": theme.HOVER,
            "selected": theme.ACCENT_SOFT,
        }
        texts = {"text": theme.TEXT, "secondary": theme.TEXT_SECONDARY, "muted": theme.TEXT_MUTED}
        for text_name, text in texts.items():
            for surface_name, surface in surfaces.items():
                self.assert_readable(text, surface, f"{text_name} on {surface_name}")

    def test_status_colours_on_their_tints_and_on_cards(self):
        for name, tone in theme.TONES.items():
            self.assert_readable(tone.text, tone.soft, f"{name} on its tint")
            self.assert_readable(tone.text, theme.RAISED, f"{name} on a card")
            self.assert_readable(tone.text, theme.PAGE, f"{name} on the page")

    def test_the_main_buttons_text(self):
        for fill in (theme.ACCENT, theme.ACCENT_HOVER, theme.ACCENT_PRESSED):
            self.assert_readable(theme.ON_ACCENT, fill, "on the accent")

    def test_every_check_status_has_a_tone(self):
        for tone, word in theme.STATUS.values():
            self.assertIn(tone, theme.TONES)
            self.assertTrue(word)


class FontTests(unittest.TestCase):
    """The best UI and monospaced fonts the platform has; Tk's defaults when it has none of them."""

    def test_windows_prefers_segoe_ui_variable_with_its_semibold_and_display_faces(self):
        have = ["Arial", "Segoe UI", "Segoe UI Semibold", "Segoe UI Variable Text", "Segoe UI Variable Text Semibold"]
        have += ["Segoe UI Variable Display Semib", "Consolas", "Cascadia Mono"]
        found = theme.families(have, "win32")
        self.assertEqual(
            found,
            theme.Families(
                "Segoe UI Variable Text",
                "Segoe UI Variable Text Semibold",
                "Segoe UI Variable Display Semib",
                "Cascadia Mono",
            ),
        )
        self.assertEqual(found.face(theme.TITLE, strong=True), ("Segoe UI Variable Display Semib", "normal"))
        self.assertEqual(found.face(theme.BODY, strong=True), ("Segoe UI Variable Text Semibold", "normal"))
        self.assertEqual(found.face(theme.BODY), ("Segoe UI Variable Text", "normal"))
        self.assertEqual(found.face(theme.MONO, mono=True), ("Cascadia Mono", "normal"))

    def test_older_windows_has_segoe_ui(self):
        found = theme.families(["Segoe UI", "Segoe UI Semibold", "Consolas"], "win32")
        self.assertEqual(
            (found.text, found.strong, found.display, found.mono), ("Segoe UI", "Segoe UI Semibold", "", "Consolas")
        )

    def test_macos_uses_the_system_font_tk_names(self):
        found = theme.families(["Helvetica Neue", "Menlo", "SF Mono"], "darwin", system=".AppleSystemUIFont")
        self.assertEqual((found.text, found.mono), (".AppleSystemUIFont", "SF Mono"))
        self.assertEqual(found.face(theme.HEADING, strong=True), (".AppleSystemUIFont", "bold"))
        self.assertEqual(theme.families(["Helvetica Neue"], "darwin").text, "Helvetica Neue")

    def test_linux_takes_the_first_it_has(self):
        found = theme.families(["DejaVu Sans", "Noto Sans", "Ubuntu", "DejaVu Sans Mono"], "linux")
        self.assertEqual((found.text, found.strong, found.mono), ("Noto Sans", "Noto Sans", "DejaVu Sans Mono"))
        self.assertEqual(theme.families(["inter"], "freebsd14").text, "inter")  # any case, any other Unix

    def test_nothing_known_falls_back_to_tks_defaults(self):
        found = theme.families([], "linux", system="Sans", fixed="Monospace")
        self.assertEqual((found.text, found.mono), ("Sans", "Monospace"))
        self.assertEqual(theme.families([], "linux").text, "TkDefaultFont")


class TokenTests(unittest.TestCase):
    """The views take their colours from the theme: none of them holds one of its own."""

    def test_no_colour_literals_outside_the_theme(self):
        for path in sorted(WINDOW.glob("*.py")):
            if path.name == "theme.py":
                continue
            with self.subTest(path.name):
                self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}\b", path.read_text(encoding="utf-8")), [])

    def test_the_spacing_scale(self):
        scale = [theme.Space.XS, theme.Space.S, theme.Space.M, theme.Space.L, theme.Space.XL, theme.Space.XXL]
        self.assertEqual([*scale, theme.Space.XXXL], [4, 8, 12, 16, 24, 32, 48])


class Widget:
    """A gridded widget or a label, as Reflow sees one."""

    def __init__(self, manager: str = "grid") -> None:
        self.manager, self.options = manager, {}

    def winfo_manager(self) -> str:
        return self.manager

    def winfo_exists(self) -> bool:
        return True

    def grid(self, **options: object) -> None:
        self.options.update(options)

    def configure(self, **options: object) -> None:
        self.options.update(options)

    def bind(self, *_args: object, **_options: object) -> None:
        pass


class Scaled:
    """A kit whose widgets are at SCALING."""

    def __init__(self, scaling: float) -> None:
        self.factor = scaling

    def scaling(self, _widget: object) -> float:
        return self.factor


class ReflowTests(unittest.TestCase):
    """A page's column keeps a readable width in a wide window and wraps its texts to it."""

    def event(self, width: int) -> object:
        return type("Event", (), {"width": width})()

    def test_a_wide_window_leaves_the_rest_as_margin(self):
        page, hidden, label = Widget(), Widget(manager=""), Widget()
        flow = kit.Reflow(Scaled(2.0), Widget(), 700, sides=(48, 48))  # ty: ignore[invalid-argument-type]  # fakes
        flow.add(page)
        flow.add(hidden)
        flow.wrap(label, inset=20)
        flow.fit(self.event(2 * 1200))  # 1200 points at 200 %
        self.assertEqual(page.options["padx"], (48, 1200 - 48 - 700))
        self.assertEqual(label.options["wraplength"], 680)
        self.assertNotIn("padx", hidden.options)  # not shown: it takes its margins when it is gridded
        self.assertEqual(flow.padx, (48, 452))  # for a widget gridded later

    def test_a_narrow_window_shrinks_the_column_and_centred_splits_the_rest(self):
        page, label = Widget(), Widget()
        flow = kit.Reflow(Scaled(1.0), Widget(), 700, sides=(48, 48), center=True)  # ty: ignore[invalid-argument-type]  # fakes
        flow.add(page, shift=16)
        flow.wrap(label)
        flow.fit(self.event(600))
        self.assertEqual((page.options["padx"], label.options["wraplength"]), ((32, 48), 504))
        flow.fit(self.event(1000))
        self.assertEqual((page.options["padx"], label.options["wraplength"]), ((134, 150), 700))


if __name__ == "__main__":
    unittest.main()
