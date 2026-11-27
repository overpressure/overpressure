# SPDX-License-Identifier: Unlicense
"""scripts/bundle-app.py: the app's bundled Python comes from the uv the start scripts pin, on every system."""

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("bundle_app", ROOT / "scripts" / "bundle-app.py")
assert SPEC is not None
assert SPEC.loader is not None
bundle_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_app)


class PinTests(unittest.TestCase):
    def test_every_system_gets_the_start_scripts_uv(self):
        systems = {
            ("win32", "AMD64"): "uv-x86_64-pc-windows-msvc.zip",
            ("win32", "ARM64"): "uv-aarch64-pc-windows-msvc.zip",
            ("linux", "x86_64"): "uv-x86_64-unknown-linux-musl.tar.gz",
            ("linux", "aarch64"): "uv-aarch64-unknown-linux-musl.tar.gz",
            ("darwin", "x86_64"): "uv-x86_64-apple-darwin.tar.gz",
            ("darwin", "arm64"): "uv-aarch64-apple-darwin.tar.gz",
        }
        scripts = (ROOT / "Install.cmd").read_text(encoding="utf-8") + (ROOT / "install.sh").read_text(encoding="utf-8")
        for (system, machine), asset in systems.items():
            with self.subTest(system=system, machine=machine):
                version, found, digest = bundle_app.uv_pin(system, machine)
                self.assertEqual(found, asset)
                self.assertIn(digest, scripts)  # the very SHA-256 a start script checks
                self.assertIn(version, scripts)

    def test_a_system_without_a_pin_stops_the_bundle(self):
        for system, machine in (("linux", "riscv64"), ("win32", "x86")):
            with self.subTest(system=system, machine=machine), self.assertRaises(SystemExit):
                bundle_app.uv_pin(system, machine)

    def test_the_bundle_carries_what_the_engine_reads_at_run_time(self):
        for name in bundle_app.ROOT_FILES:
            self.assertTrue((ROOT / name).is_file(), name)
        # Tk is trimmed: outside the Tk window only these import it, each without failing when it is missing (the
        # doctor's check of the window's Tk, the elevated-rights dialog, the Tk window's carry-over question).
        imports = re.compile(r"^\s*(?:import|from) (?:tkinter|idlelib|turtledemo)\b", re.MULTILINE)
        users = {
            p.name
            for p in (ROOT / "overpressure").rglob("*.py")
            if "window" not in p.parts and imports.search(p.read_text(encoding="utf-8"))
        }
        self.assertEqual(users, {"doctor.py", "elevated.py", "__main__.py"})


if __name__ == "__main__":
    unittest.main()
