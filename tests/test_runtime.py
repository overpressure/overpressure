# SPDX-License-Identifier: Unlicense
"""The headset runtime (docs/runtime.md): its names, its library's defaults, the PC's side and stage 1's use of it.

tests/test_recovery_policy.sh checks the recovery policy's decisions; overpressure/packages' common/overpressure-base
tests the installed system's share in its build.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from overpressure import contract, headset, stage1

RUNTIME = stage1.RUNTIME
LIB = RUNTIME / "usr/libexec/overpressure"


def text(path: Path) -> str:
    """Return a runtime file's text."""
    return path.read_text(encoding="utf-8")


def files() -> list[Path]:
    """Return every file of the runtime."""
    return sorted(p for p in RUNTIME.rglob("*") if p.is_file())


def defaults() -> dict[str, str]:
    """Return the library's OP_ defaults, as it assigns them."""
    found = {}
    for line in text(LIB / "lib.sh").splitlines():
        match = re.match(r"^(OP_[A-Z_]+)=(.*)$", line)
        if match:
            found[match.group(1)] = match.group(2).strip('"')
    return found


class NameTests(unittest.TestCase):
    def test_no_headset_is_named(self) -> None:
        # A headset's names belong to its overlay and its packages.
        for path in files():
            for word in ("pico4", "PICO", "P4_", "phoenix"):
                self.assertNotIn(word, text(path), f"{path.relative_to(RUNTIME)}: {word}")

    def test_the_words_carry_the_prefix(self) -> None:
        # stage1_arg reads overpressure.NAME, and nothing else reads the command line's own words.
        self.assertIn(f'local word key="{contract.WORD}$1="', text(LIB / "lib.sh"))

    def test_the_paths_are_the_contracts(self) -> None:
        values = defaults()
        self.assertEqual(values["OP_RUN"], contract.RUN)
        self.assertEqual(values["OP_LIB"], contract.LIB)
        self.assertIn(f"[ ! -r {contract.HEADSET} ] || . {contract.HEADSET}", text(LIB / "lib.sh"))

    def test_the_recovery_deadline_reads_the_hosts_files(self) -> None:
        watch = text(LIB / "recovery-watch")
        self.assertIn(f"[ -s {contract.TEST_RUN} ]", watch)
        self.assertIn(f"cat {contract.TEST_CONNECT_TIMEOUT}", watch)
        self.assertIn(f"cat {contract.TEST_TIMEOUT}", watch)
        self.assertIn('"$OP_RUN/host-contact"', watch)
        self.assertEqual(contract.CONTACT, f"{contract.RUN}/host-contact")
        self.assertIn('"$OP_RUN/test-authenticated"', watch)
        self.assertEqual(contract.TEST_AUTHENTICATED, f"{contract.RUN}/test-authenticated")
        # A login stops the deadline: the runtime's own profile marks it.
        self.assertIn('"$OP_RUN/no-autoreboot"', watch)
        self.assertIn(f"touch {contract.RUN}/no-autoreboot", text(RUNTIME / "etc/profile.d/overpressure.sh"))

    def test_word(self) -> None:
        self.assertEqual(contract.word("rw"), "overpressure.rw")
        self.assertEqual(contract.word("autoreboot", 0), "overpressure.autoreboot=0")


class LibraryTests(unittest.TestCase):
    def test_every_value_a_headset_gives_has_a_default(self) -> None:
        values = defaults()
        for name in ("OP_NAME", "OP_STOCK", "OP_IP", "OP_HOST_IP", "OP_PREFIX", "OP_USB_MANUFACTURER"):
            self.assertIn(name, values)
        for name in ("OP_USB_PRODUCT", "OP_REBOOT", "OP_PROBE", "OP_DATA_PART", "OP_DATA_LABEL", "OP_DATA_MARKER"):
            self.assertIn(name, values)
        self.assertEqual(values["OP_REBOOT"], "reboot")
        self.assertEqual(values["OP_PROBE"], "true")

    def test_the_headsets_values_come_after_the_defaults(self) -> None:
        lib = text(LIB / "lib.sh")
        self.assertLess(lib.index("OP_DATA_MARKER="), lib.index(f". {contract.HEADSET}"))

    def test_the_maintenance_system_arms_the_hardware_watchdog(self) -> None:
        # A hang that stops every process stops recovery-watch too: the SoC's watchdog ends it, as in the
        # installed system (RuntimeWatchdogSec=30s).
        self.assertIn("::respawn:/usr/libexec/overpressure/svc watchdog\n", text(RUNTIME / "etc/inittab"))
        svc = text(LIB / "svc")
        self.assertIn("exec watchdog -F -T 30 -t 10 /dev/watchdog", svc)
        self.assertLess(svc.index("[ -c /dev/watchdog ] || idle"), svc.index("exec watchdog"))

    def test_the_programs_use_the_headsets_reboot_and_probe(self) -> None:
        self.assertIn('"$OP_REBOOT" bootloader', text(LIB / "recovery-watch"))
        self.assertIn('"$OP_PROBE" --boot', text(RUNTIME / "init"))

    def test_the_gadget_takes_the_headsets_strings(self) -> None:
        gadget = text(LIB / "usb-gadget")
        self.assertIn('write "$OP_USB_MANUFACTURER" "$G/strings/0x409/manufacturer"', gadget)
        self.assertIn('write "$OP_USB_PRODUCT" "$G/strings/0x409/product"', gadget)


class Stage1Tests(unittest.TestCase):
    def headset(self, root: Path, api: object) -> Path:
        """Return the initramfs/ overlay of a headset folder in ROOT made for interface API."""
        (root / "headset.json").write_text(json.dumps({"codename": "fake", "api": api}), encoding="utf-8")
        (root / "initramfs").mkdir()
        return root / "initramfs"

    def test_a_headset_made_for_1_8_or_newer_takes_the_runtime(self) -> None:
        # Interface 1.8 adds it; a headset made for an older one (phoenix's releases) brings its own init.
        for api, expected in (("1.8", True), ("1.10", True), ("2.0", True), ("1.7", False), (1, False), ("x", False)):
            with self.subTest(api=api), tempfile.TemporaryDirectory() as tmp:
                overlay = self.headset(Path(tmp), api)
                self.assertIsNone(stage1.Spec(overlay=overlay).runtime)
                self.assertEqual(stage1.takes_runtime(stage1.Spec(overlay=overlay)), expected)
                self.assertEqual(headset.runs_on_runtime(overlay / "deeper"), expected)

    def test_a_headset_may_decide_either_way(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overlay = self.headset(Path(tmp), "1.8")
            self.assertFalse(stage1.takes_runtime(stage1.Spec(overlay=overlay, runtime=False)))
        with tempfile.TemporaryDirectory() as tmp:
            overlay = self.headset(Path(tmp), "1.7")
            self.assertTrue(stage1.takes_runtime(stage1.Spec(overlay=overlay, runtime=True)))

    def test_without_a_headset_json_there_is_no_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(stage1.takes_runtime(stage1.Spec(overlay=Path(tmp))))

    def test_the_runtime_has_its_init_and_inittab(self) -> None:
        for name in ("init", "etc/inittab", f"{contract.LIB[1:]}/overpressure-init", f"{contract.LIB[1:]}/svc"):
            self.assertTrue((RUNTIME / name).is_file(), name)
        for line in text(RUNTIME / "etc/inittab").splitlines():
            if line.startswith("::respawn:"):
                self.assertTrue(line.startswith(f"::respawn:{contract.LIB}/svc "), line)


if __name__ == "__main__":
    unittest.main()
