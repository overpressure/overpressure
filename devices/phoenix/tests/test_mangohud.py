# SPDX-License-Identifier: Unlicense
"""MangoHud: a pinned, offline package."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKGBUILDS = ROOT / "userspace/pkgbuilds"


class MangoHudTests(unittest.TestCase):
    def test_package_is_pinned_and_offline(self):
        recipe = (PKGBUILDS / "pico4-mangohud/PKGBUILD").read_text(encoding="utf-8")
        self.assertRegex(recipe, r"sha256sums=\('[0-9a-f]{64}'\)")
        # The archive's own subprojects only: makepkg's sources are the whole input.
        self.assertIn("--wrap-mode=nodownload", recipe)
        self.assertIn("-Dmangoapp=true", recipe)
        # The Frame's spdlog/fmt mismatch is why this package exists (docs/session.md).
        self.assertIn("-Duse_system_spdlog=disabled", recipe)

    def test_image_keeps_the_session_starting_mangoapp(self):
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("build_package pico4-mangohud", build)
        self.assertNotRegex(build, r"sed -i [^\n]*--mangoapp")
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertIn("pico4/pico4-mangohud", packages)


if __name__ == "__main__":
    unittest.main()
