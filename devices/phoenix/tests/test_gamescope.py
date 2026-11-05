# SPDX-License-Identifier: Unlicense
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "userspace/pkgbuilds/pico4-gamescope"


class GamescopeTests(unittest.TestCase):
    """The Frame's gamescope, built here with the fix for a window added twice."""

    def test_the_frames_commit_and_pinned_wraps(self):
        recipe = (RECIPE / "PKGBUILD").read_text(encoding="utf-8")
        # Valve's 3.16.28-1 reports 3.16.28-43-gea3579ed.
        self.assertIn("_commit=ea3579ed612151eaa005d526a2533e8932854f58", recipe)
        self.assertIn('"gamescope::git+https://github.com/ValveSoftware/gamescope.git#commit=$_commit"', recipe)
        self.assertRegex(recipe, r"_glm=[0-9a-f]{40}\n")
        self.assertRegex(recipe, r"_stb=[0-9a-f]{40}\n")
        # Submodules at the superproject's commits, wraps from the sources: nothing downloaded.
        self.assertIn("git -c protocol.file.allow=always submodule update", recipe)
        self.assertIn("--wrap-mode=nodownload", recipe)
        # Linked statically as in Valve's binary; upstream insists on libliftoff and vkroots.
        self.assertIn("-Dforce_fallback_for=libliftoff,vkroots,wlroots,libdisplay-info,wayland-protocols", recipe)
        self.assertIn('provides=("gamescope=$pkgver")', recipe)
        self.assertIn("conflicts=(gamescope)", recipe)

    def test_a_window_is_added_once(self):
        patch = (RECIPE / "gamescope-add-win-once.patch").read_text(encoding="utf-8")
        self.assertIn("+++ b/src/steamcompmgr.cpp", patch)
        added = patch[patch.index("add_win(xwayland_ctx_t *ctx, Window id, Window prev, unsigned long sequence)"):]
        self.assertIn("+\tif (find_win(ctx, id, false))\n+\t\treturn;", added)
        # Before the new entry is allocated.
        self.assertLess(added.index("find_win(ctx, id, false)"), added.index("new steamcompmgr_win_t{}"))
        recipe = (RECIPE / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn('patch -p1 < "$srcdir/gamescope-add-win-once.patch"', recipe)

    def test_the_image_takes_it_instead_of_valves(self):
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertIn("pico4/pico4-gamescope", packages)
        self.assertNotIn("deckard-arch-hotfixes-release-0.3/gamescope", packages)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("build_package pico4-gamescope", build)
        # Its libraries in the build root (makepkg runs without -s).
        deps = re.search(r"in_b pacman \"-S\$sync\" --noconfirm --needed (.*?)\nin_b id builder", build, re.S).group(1)
        for dep in ("libei", "luajit", "libdecor", "seatd", "xorg-xwayland", "sdl2", "libavif"):
            self.assertIn(dep, deps.split())


if __name__ == "__main__":
    unittest.main()
