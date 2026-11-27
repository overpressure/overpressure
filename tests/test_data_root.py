# SPDX-License-Identifier: Unlicense
"""overpressure's two roots: its own files (CODE) and the owner's data (REPO), apart only for the app or on request."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import overpressure
from overpressure import BUNDLED, CODE, data_root, linux


class DataRootTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.code = Path(folder.name) / "engine" / "root"
        self.code.mkdir(parents=True)
        self.home = Path(folder.name) / "home"

    def root(self, platform: str, environ: dict[str, str] | None = None) -> Path:
        return data_root(self.code, environ or {}, platform, lambda: self.home)

    def test_a_checkout_or_a_release_keeps_its_data_below_itself(self):
        for platform in ("win32", "darwin", "linux"):
            self.assertEqual(self.root(platform), self.code)

    def test_the_apps_engine_puts_its_data_into_the_owners_folder(self):
        (self.code / BUNDLED).write_text("the app's engine\n", encoding="utf-8")
        local = str(self.home / "Local")
        # Not the per-user installer's own folder (%LOCALAPPDATA%\overpressure), which its updates replace.
        self.assertEqual(self.root("win32", {"LOCALAPPDATA": local}), Path(local) / "overpressure-data")
        self.assertEqual(self.root("darwin"), self.home / "Library" / "Application Support" / "overpressure")
        self.assertEqual(self.root("linux"), self.home / ".local" / "share" / "overpressure")
        self.assertEqual(self.root("linux", {"XDG_DATA_HOME": "/data"}), Path("/data") / "overpressure")

    def test_overpressure_home_wins_everywhere(self):
        (self.code / BUNDLED).write_text("the app's engine\n", encoding="utf-8")
        elsewhere = self.home / "elsewhere"
        self.assertEqual(self.root("win32", {"OVERPRESSURE_HOME": str(elsewhere)}), elsewhere.resolve())

    def test_the_container_sees_a_checkout_either_way(self):
        self.assertEqual(linux.code_mounts(CODE, CODE), [])
        mounts = linux.code_mounts(CODE, self.home)
        self.assertIn(f"{CODE / 'overpressure'}:/work/overpressure:ro", mounts)
        self.assertIn(f"{CODE / 'devices' / '__init__.py'}:/work/devices/__init__.py:ro", mounts)
        self.assertEqual(mounts[::2], ["-v"] * (len(mounts) // 2))

    def test_this_checkout_is_its_own_data_root(self):
        # The host tests run in a checkout: both roots are the repository, unless OVERPRESSURE_HOME names another.
        if not os.environ.get("OVERPRESSURE_HOME"):
            self.assertEqual(overpressure.REPO, CODE)


class ElsewhereTests(unittest.TestCase):
    def test_a_data_root_elsewhere_holds_the_data_and_children_still_find_overpressure(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "OVERPRESSURE_HOME")}
            env["OVERPRESSURE_HOME"] = folder
            probe = (
                "import json, subprocess, sys, overpressure, devices; "
                "child = subprocess.run([sys.executable, '-m', 'overpressure', '--version'], cwd=overpressure.REPO, "
                "capture_output=True, text=True); "
                "print(json.dumps({'repo': str(overpressure.REPO), 'code': str(overpressure.CODE), "
                "'devices': list(devices.__path__), 'child': child.returncode, 'out': child.stdout}))"
            )
            done = subprocess.run(  # noqa: S603  # this Python with the probe above
                [sys.executable, "-c", probe], cwd=CODE, env=env, capture_output=True, text=True, check=True
            )
            found = json.loads(done.stdout)
            self.assertEqual(Path(found["repo"]), Path(folder).resolve())
            self.assertEqual(Path(found["code"]), CODE)
            self.assertIn(str(Path(folder).resolve() / "devices"), found["devices"])  # downloaded headsets import
            self.assertEqual(found["child"], 0, found)  # a child in the data root runs overpressure from CODE
            self.assertIn("overpressure", found["out"])


if __name__ == "__main__":
    unittest.main()
