# SPDX-License-Identifier: Unlicense
"""The wall clock from the RTC and PICO OS's offset in persist (pico4-clock)."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
SCRIPT = ROOTFS / "usr/libexec/pico4/clock"


@unittest.skipIf(shutil.which("sh") is None or shutil.which("od") is None, "needs sh and od")
class ClockTests(unittest.TestCase):
    def run_clock(self, offset_ms: int, rtc: int, now: int):
        with tempfile.TemporaryDirectory() as tmp:
            ats, rtc_file, log = Path(tmp, "ats_2"), Path(tmp, "since_epoch"), Path(tmp, "date.log")
            ats.write_bytes(struct.pack("<q", offset_ms))
            rtc_file.write_text(f"{rtc}\n")
            bin_dir = Path(tmp, "bin")
            bin_dir.mkdir()
            # date: +%s prints the current clock; -s records the clock it was asked to set.
            Path(bin_dir, "date").write_bytes(
                f'#!/bin/sh\n[ "$1" = +%s ] && {{ echo {now}; exit 0; }}\necho "$@" >> {log.as_posix()}\n'.encode()
            )
            (bin_dir / "date").chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}")
            sh = shutil.which("sh")
            assert sh is not None  # the class is skipped without sh
            r = subprocess.run(  # noqa: S603  # the repository's script under sh, no shell string
                [sh, SCRIPT.as_posix(), ats.as_posix(), rtc_file.as_posix()],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout, (log.read_text().split() if log.exists() else [])

    def test_clock_is_the_rtc_plus_pico_os_offset(self):
        # The headset's own numbers: RTC 47194257 s, offset 1746150285037 ms.
        out, calls = self.run_clock(1746150285037, 47194257, now=1771903351)
        self.assertEqual(calls, ["-u", "-s", "@1793344542"])
        self.assertIn("set from the RTC", out)

    def test_a_later_clock_stays(self):
        out, calls = self.run_clock(1746150285037, 47194257, now=1793344600)
        self.assertEqual(calls, [])
        self.assertIn("kept", out)

    def test_runs_before_timesyncd_with_persist(self):
        unit = (ROOTFS / "usr/lib/systemd/system/pico4-clock.service").read_text(encoding="utf-8")
        self.assertIn("After=pico4-persist.service", unit)
        self.assertIn("Before=time-set.target systemd-timesyncd.service", unit)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn("pico4-clock.service", build[build.index("in_t systemctl enable") :])


if __name__ == "__main__":
    unittest.main()
