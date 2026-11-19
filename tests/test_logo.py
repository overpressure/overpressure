# SPDX-License-Identifier: Unlicense
"""The logo: the copies of overpressure/brand's drawings in docs/logo, and the window's PNG renderings."""

from __future__ import annotations

import unittest
from pathlib import Path

from overpressure.window import logo

REPO = Path(__file__).resolve().parents[1]
DRAWINGS = REPO / "docs" / "logo"


class DrawingTests(unittest.TestCase):
    def test_every_copy_is_an_svg_with_its_frame(self):
        # The README's logo (dark and light) and the mark the PICO 4's boot screen is checked against.
        frames = {
            "overpressure.svg": "0 0 512 512",
            "overpressure-light.svg": "0 0 512 512",
            "overpressure-mark.svg": "48 162 416 208",
        }
        for name, frame in frames.items():
            text = (DRAWINGS / name).read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{frame}">'), name)

    def test_only_the_copies_and_their_readme(self):
        # Everything else lives in overpressure/brand (docs/logo/README.md).
        self.assertEqual({p.name for p in DRAWINGS.iterdir()}, {*self.copies(), "README.md"})

    @staticmethod
    def copies() -> set[str]:
        """Return the drawings this repository keeps a copy of."""
        return {"overpressure.svg", "overpressure-light.svg", "overpressure-mark.svg"}


class RenderingTests(unittest.TestCase):
    def test_every_rendering_is_there_at_its_size(self):
        for size in logo.APP:
            self.assertEqual(logo.png_size(logo.ICONS / f"app-{size}.png"), (size, size))
        for size in logo.APP_LIGHT:
            self.assertEqual(logo.png_size(logo.ICONS / f"app-light-{size}.png"), (size, size))
        for height in logo.MARK:
            self.assertEqual(logo.png_size(logo.ICONS / f"mark-{height}.png"), (2 * height, height))

    def test_windows_icon_holds_every_size(self):
        self.assertEqual(logo.ico_sizes(logo.ICO), list(logo.ICO_SIZES))

    def test_the_window_icon_set(self):
        paths = logo.window_icons()
        self.assertEqual([logo.png_size(p)[0] for p in paths], list(logo.WINDOW_ICON))

    def test_nothing_else_in_the_folder(self):
        expected = {f"app-{s}.png" for s in logo.APP} | {f"app-light-{s}.png" for s in logo.APP_LIGHT}
        expected |= {f"mark-{h}.png" for h in logo.MARK} | {logo.ICO.name}
        self.assertEqual({p.name for p in logo.ICONS.iterdir()}, expected)

    def test_a_png_check_refuses_other_files(self):
        with self.assertRaises(ValueError):
            logo.png_size(DRAWINGS / "overpressure.svg")


class ChoiceTests(unittest.TestCase):
    def test_the_nearest_rendering_and_the_larger_on_a_tie(self):
        self.assertEqual(logo.nearest((18, 23, 27), 18), 18)
        self.assertEqual(logo.nearest((18, 23, 27), 22.5), 23)
        self.assertEqual(logo.nearest((10, 20), 15), 20)

    def test_the_header_mark_follows_the_scaling(self):
        self.assertEqual(logo.mark(24, 1.0).name, "mark-24.png")
        self.assertEqual(logo.mark(24, 1.25).name, "mark-30.png")
        self.assertEqual(logo.mark(24, 1.75).name, "mark-42.png")
        self.assertEqual(logo.mark(24, 3.0).name, "mark-72.png")

    def test_the_app_icon_by_scaling_and_tile(self):
        self.assertEqual(logo.app_icon(64, 1.0).name, "app-64.png")
        self.assertEqual(logo.app_icon(64, 1.25).name, "app-80.png")
        self.assertEqual(logo.app_icon(64, 3.0, light=True).name, "app-light-192.png")
        self.assertEqual(logo.app_icon(16, 1.0).name, "app-16.png")

    def test_small_sizes_take_the_small_drawing(self):
        # The full drawing's strap mounts and rims blur below 64 px: the window icon's small sizes are small.
        self.assertTrue(all(size <= logo.SMALL_UP_TO for size in logo.WINDOW_ICON[:3]))


if __name__ == "__main__":
    unittest.main()
