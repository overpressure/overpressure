# SPDX-License-Identifier: Unlicense
"""The PICO 4's doctor checks: settings and private files, the tools, the Windows link, the build host, assets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from devices.phoenix.tooling import HEADSET, localconfig
from devices.phoenix.tooling import doctor as checks
from overpressure import backup, doctor, tools

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

IDENTITY = {
    "schema": 1,
    "serial": "ABC",
    "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/1:user/release-keys",
    "oem_state": "true",
    "chip_id": 1234567,
}


class FakeHost(doctor.Host):
    def __init__(
        self,
        platform: str = "linux",
        runs: dict[str, tuple[int, str]] | None = None,
        *,
        on_path: bool = True,
    ):
        super().__init__(platform=platform, run=self.fake_run, which=lambda t: f"/usr/bin/{t}" if on_path else None)
        self.runs = runs or {}

    def fake_run(self, argv: Sequence[str], _timeout: float) -> tuple[int, str]:
        key = Path(str(argv[0])).name
        return self.runs.get(key, (127, f"{key}: not found"))


def by_name(items: Iterable[doctor.Check]) -> dict[str, doctor.Check]:
    """Index checks by their names."""
    return {c.name: c for c in items}


class PicoDoctorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # Tools fetched into this checkout's cache must not stand in for the fakes.
        patcher = mock.patch.object(tools, "CACHE", self.root / "tools-cache")
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings(self, **values: str) -> localconfig.Settings:
        s = localconfig.Settings(**values)
        s.path = lambda name: (
            Path(getattr(s, name)) if Path(getattr(s, name)).is_absolute() else self.root / getattr(s, name)
        )
        return s

    def test_a_fresh_checkout_names_every_missing_setting_and_file(self):
        found = by_name(checks.settings_checks(FakeHost(on_path=False), self.settings(), self.root))
        for name in (
            ".env",
            "PICO4_ADB_SERIAL",
            "PICO4_FASTBOOT_SERIAL",
            "PICO4_EDL_SERIAL",
            "identity",
            "EDL backup",
            "Firehose loader",
            "engineering ABL",
            "edl-ng",
        ):
            self.assertEqual(found[name].status, doctor.MISSING, name)
            self.assertTrue(found[name].fix, name)

    def test_a_prepared_checkout_passes_and_a_changed_abl_does_not(self):
        (self.root / ".env").write_text("PICO4_ADB_SERIAL=ABC\n")
        (self.root / "backup/stock").mkdir(parents=True)
        (self.root / "backup/device.json").write_text(json.dumps(IDENTITY))
        (self.root / "backup/stock/boot.bin").write_bytes(b"boot")
        (self.root / "backup/stock/manifest.json").write_text(
            json.dumps({"schema": 1, "files": {"boot.bin": backup.digest(self.root / "backup/stock/boot.bin")}})
        )
        (self.root / "assets").mkdir()
        for name in ("firehose.elf", "engineering-abl.elf"):
            (self.root / "assets" / name).write_bytes(name.encode())
        (self.root / "edl-ng").write_bytes(b"x")
        abl_sha = backup.digest(self.root / "assets/engineering-abl.elf")["sha256"]
        s = self.settings(
            adb_serial="A", fastboot_serial="F", edl_serial="E", edl="edl-ng", engineering_abl_sha256=abl_sha
        )
        found = checks.settings_checks(FakeHost(), s, self.root)
        self.assertEqual([c.name for c in doctor.failed(found)], [])
        (self.root / "backup/stock/boot.bin").write_bytes(b"boot, longer")
        (self.root / "assets/engineering-abl.elf").write_bytes(b"another")
        found = by_name(checks.settings_checks(FakeHost(), s, self.root))
        self.assertEqual(found["EDL backup"].status, doctor.MISSING)
        self.assertIn("sha256 differs", found["engineering ABL"].detail)

    def test_platform_tools_and_the_windows_link(self):
        s = self.settings(ncm_python="py")
        host = FakeHost(
            platform="win32",
            runs={
                "adb": (0, "Android Debug Bridge version 1.0.41"),
                "py": (0, '{"status": "ready", "dependencies": {"pyusb": "1.3.1"}}'),
            },
        )
        tools = by_name(checks.tool_checks(host, s))
        self.assertEqual((tools["adb"].status, tools["fastboot"].status), (doctor.OK, doctor.MISSING))
        self.assertEqual(checks.ncm_checks(host, s)[0].status, doctor.OK)
        self.assertEqual(checks.ncm_checks(FakeHost(platform="win32"), s)[0].status, doctor.MISSING)
        self.assertIn("UsbDk", checks.ncm_checks(host, self.settings())[0].fix)
        self.assertEqual(checks.ncm_checks(FakeHost(platform="linux"), s), [])

    def test_the_builds_linux_side_is_the_build_containers_alone(self):
        # The image holds every Linux tool by construction: the PICO 4 checks none of them itself.
        self.assertFalse(hasattr(checks, "build_checks"))
        host = FakeHost()
        for probe in checks.probes():
            probe(host)
        self.assertEqual(host.linux_cache, {})

    def test_no_extracted_assets_are_needed(self):
        # PICO OS's files come from the headset's backup at the install (tooling/stockfiles.py).
        self.assertFalse(hasattr(checks, "asset_checks"))

    def test_the_pico_4_registers_its_checks(self):
        self.assertEqual(
            HEADSET.dependencies(),
            (checks.settings_checks, checks.tool_checks, checks.ncm_checks, checks.usb_checks),
        )

    def test_linux_checks_usb_access_to_its_three_modes(self):
        self.assertEqual(checks.usb_checks(FakeHost("win32")), [])  # only Linux needs a rule
        self.assertEqual(
            [m.name for m in HEADSET.usb_modes()], ["PICO OS with USB debugging", "fastboot mode", "EDL mode"]
        )


if __name__ == "__main__":
    unittest.main()
