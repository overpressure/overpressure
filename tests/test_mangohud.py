import re
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
        # gamescope's queue to mangoapp needs the preloaded System V IPC.
        self.assertIn("pico4/pico4-sysvsem", packages)

    def test_message_queues_are_built_and_tested_with_the_semaphores(self):
        recipe = (PKGBUILDS / "pico4-sysvsem/PKGBUILD").read_text(encoding="utf-8")
        compile_line = re.search(r"-o libpico4-sysvsem\.so ([^\n]*)", recipe).group(1)
        self.assertIn("pico4-sysvmsg.c", compile_line)
        self.assertIn("test_pico4_sysvmsg", recipe)
        source = (ROOT / "src/sysvsem/pico4-sysvmsg.c").read_text(encoding="utf-8")
        for call in ("msgget", "msgsnd", "msgrcv", "msgctl"):
            self.assertRegex(source, rf"EXPORT [a-z_]+\n{call}\(")


if __name__ == "__main__":
    unittest.main()
