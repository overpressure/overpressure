# SPDX-License-Identifier: Unlicense
"""scripts/app-manifest.py: the updater's manifest names each system's signed update file among the release's."""

import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("app_manifest", ROOT / "scripts" / "app-manifest.py")
assert SPEC is not None
assert SPEC.loader is not None
app_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_manifest)


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)

    def test_collected_files_go_into_the_manifest_with_their_signatures(self):
        bundle = self.temp / "bundle"
        (bundle / "nsis").mkdir(parents=True)
        (bundle / "nsis" / "overpressure_2026.12.0-beta.3_x64-setup.exe").write_bytes(b"installer")
        (bundle / "nsis" / "overpressure_2026.12.0-beta.3_x64-setup.exe.sig").write_text("c2lnbmF0dXJl\n")
        out = self.temp / "out"
        collected = app_manifest.collect("windows-x86_64", bundle, out)
        self.assertEqual(collected.name, "overpressure-app-windows-x86_64.setup.exe")
        when = dt.datetime(2026, 12, 3, 11, 54, tzinfo=dt.timezone.utc)
        data = app_manifest.manifest("2026.12.0b3", out, "Notes", when)
        self.assertEqual(data["version"], "2026.12.0-beta.3")
        self.assertEqual(data["pub_date"], "2026-12-03T11:54:00Z")
        self.assertEqual(
            data["platforms"],
            {
                "windows-x86_64": {
                    "signature": "c2lnbmF0dXJl",
                    "url": "https://github.com/overpressure/overpressure/releases/download/2026.12.0b3/"
                    "overpressure-app-windows-x86_64.setup.exe",
                }
            },
        )

    def test_an_unsigned_or_missing_build_is_refused(self):
        (self.temp / "nsis").mkdir()
        (self.temp / "nsis" / "overpressure_1_x64-setup.exe").write_bytes(b"")
        with self.assertRaises(SystemExit):
            app_manifest.collect("windows-x86_64", self.temp, self.temp / "out")
        with self.assertRaises(SystemExit):
            app_manifest.manifest("2026.12.0", self.temp / "out")

    def test_the_bundle_takes_pyprojects_version_in_semver(self):
        self.assertRegex(app_manifest.bundle_version(), r"^\d+\.\d+\.\d+(-(beta|dev)\.\d+)?$")


if __name__ == "__main__":
    unittest.main()
