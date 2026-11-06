# SPDX-License-Identifier: Unlicense
"""overpressure's RAM boot (fastboot boot) against a fake fastboot and a fake headset."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from overpressure import bootimg, fastboot


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeTarget(fastboot.RamBootTarget):
    refused_hint = "Boot it from a partition instead."
    up_timeout = 30

    def __init__(  # noqa: PLR0913  # a fake headset: one keyword per behaviour a test sets
        self,
        image: Path,
        *,
        in_fastboot: bool = True,
        devices: int = 1,
        boot_error: str | None = None,
        boot_output: str = "OKAY",
        up_after: int = 2,
        locked: bool = False,
    ):
        self.image_path = image
        self.in_fastboot, self.devices = in_fastboot, devices
        self.boot_error, self.boot_output = boot_error, boot_output
        self.up_after, self.locked = up_after, locked
        self.calls, self.entered, self.polls = [], False, 0

    def run(self, argv: list[str], _timeout: float) -> str:
        self.calls.append(argv[1:])
        if argv[1:] == ["devices"]:
            rows = [f"OTHER{i}\tfastboot" for i in range(self.devices - 1)]
            return "\n".join(rows + (["SERIAL\tfastboot"] if self.in_fastboot else []))
        if argv[3:4] == ["boot"]:
            if self.boot_error:
                raise RuntimeError(self.boot_error)
            return self.boot_output
        return "OKAY"

    @property
    def fastboot(self):  # the property shadows the module in an annotation
        return fastboot.Fastboot("fastboot", "SERIAL", self.run)

    def image(self) -> Path:
        return self.image_path

    def enter(self) -> None:
        self.entered = True
        self.in_fastboot = True

    def check_unlocked(self) -> None:
        if self.locked:
            msg = "locked"
            raise RuntimeError(msg)

    def reached(self) -> None:
        self.polls += 1
        if self.polls < self.up_after:
            msg = "no answer yet"
            raise RuntimeError(msg)


class RamBootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.image = Path(temp.name) / "boot.img"
        self.image.write_bytes(bootimg.BootImage(2, 4096, b"kernel", b"ramdisk", dtb=b"dtb").pack())
        self.clock = FakeClock()

    def boot(self, target: FakeTarget, image: Path | None = None) -> None:
        return fastboot.ram_boot(target, image, log=lambda _line: None, clock=self.clock)

    def test_boots_from_ram_and_waits_until_the_image_answers(self):
        target = FakeTarget(self.image, up_after=3)
        self.boot(target)
        self.assertEqual(target.polls, 3)
        self.assertIn(["-s", "SERIAL", "boot", str(self.image)], target.calls)
        # Every command but the device list names the headset, and nothing is ever written.
        for call in target.calls:
            self.assertNotIn("flash", call)
            if call != ["devices"]:
                self.assertEqual(call[:2], ["-s", "SERIAL"])

    def test_enters_fastboot_when_the_headset_is_not_there(self):
        target = FakeTarget(self.image, in_fastboot=False)
        self.boot(target)
        self.assertTrue(target.entered)

    def test_a_bootloader_without_fastboot_boot_is_refused_not_failed(self):
        target = FakeTarget(self.image, boot_error="fastboot boot failed: FAILED (remote: 'unknown command')")
        with self.assertRaises(fastboot.Refused) as raised:
            self.boot(target)
        self.assertIn("nothing was written", str(raised.exception))
        self.assertIn("Boot it from a partition instead.", str(raised.exception))
        self.assertEqual(target.polls, 0)
        target = FakeTarget(self.image, boot_output="(bootloader) boot is disabled")
        with self.assertRaises(fastboot.Refused):
            self.boot(target)

    def test_the_refusal_quotes_the_bootloaders_answer(self):
        # fastboot's progress lines come first; the answer is its FAILED line, quoted whole.
        error = (
            "fastboot failed (1): Sending 'boot.img' (47616 KB)" + " " * 30 + "OKAY [  1.129s]\n"
            "Booting" + " " * 40 + "FAILED (remote: 'unknown command')\n"
            "fastboot: error: Command failed"
        )
        with self.assertRaises(fastboot.Refused) as raised:
            self.boot(FakeTarget(self.image, boot_error=error))
        self.assertIn("(Booting" + " " * 40 + "FAILED (remote: 'unknown command'))", str(raised.exception))
        self.assertNotIn("Sending", str(raised.exception))

    def test_other_boot_failures_are_errors(self):
        target = FakeTarget(self.image, boot_error="fastboot boot failed: FAILED (remote: 'image too large')")
        with self.assertRaises(RuntimeError) as raised:
            self.boot(target)
        self.assertNotIsInstance(raised.exception, fastboot.Refused)

    def test_checks_come_before_the_transfer(self):
        bad = self.image.with_name("bad.img")
        bad.write_bytes(b"not a boot image")
        target = FakeTarget(self.image)
        with self.assertRaises(ValueError):
            self.boot(target, bad)
        self.assertEqual(target.calls, [])
        for target in (FakeTarget(self.image, locked=True), FakeTarget(self.image, devices=2)):
            with self.assertRaises(RuntimeError):
                self.boot(target)
            self.assertFalse(any(call[2:3] == ["boot"] for call in target.calls))

    def test_an_image_that_never_answers_times_out(self):
        target = FakeTarget(self.image, up_after=10**6)
        with self.assertRaisesRegex(RuntimeError, "timed out waiting for the RAM-booted image"):
            self.boot(target)
        self.assertGreaterEqual(self.clock.now, target.up_timeout)

    def test_fastboot_needs_the_serial(self):
        with self.assertRaises(ValueError):
            fastboot.Fastboot("fastboot", "", lambda *_: "")("boot", "x")


if __name__ == "__main__":
    unittest.main()
