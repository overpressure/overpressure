# SPDX-License-Identifier: Unlicense
"""The installed system's log drive: a journal larger than the drive keeps its newest whole lines."""

import subprocess
import unittest
from pathlib import Path

from devices.phoenix.tests.userland import needs_gnu_userland

ROOTFS = Path(__file__).resolve().parents[1] / "userspace/rootfs"
SCRIPT = ROOTFS / "usr/libexec/pico4/userspace-logdisk"
NOTE = "-- the journal's older lines do not fit on the drive"


def tail(text: str, room: int) -> str:
    """Run the drive's journal step on TEXT with ROOM bytes."""
    return subprocess.run(  # noqa: S603  # bash from PATH runs the script under test
        ["bash", str(SCRIPT), "--tail", str(room)],  # noqa: S607  # bash from PATH
        input=text,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout


@needs_gnu_userland
class LogdiskTests(unittest.TestCase):
    def test_a_journal_that_fits_stays_whole(self) -> None:
        journal = "".join(f"[{i:6d}.000000] line {i}\n" for i in range(100))
        self.assertEqual(tail(journal, len(journal)), journal)

    def test_a_larger_journal_keeps_its_newest_whole_lines(self) -> None:
        journal = "".join(f"[{i:6d}.000000] line {i}\n" for i in range(10000))
        room = 4000
        out = tail(journal, room)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith(NOTE))
        self.assertTrue(journal.endswith("\n".join(lines[1:]) + "\n"))
        # Whole lines only, and no more than the room and the note.
        self.assertRegex(lines[1], r"^\[ *\d+\.000000\] line \d+$")
        self.assertLessEqual(len(out), room + len(lines[0]) + 1)

    def test_the_drive_keeps_room_for_its_other_files(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('room=$(( mb * 1048576 - $(du -sb "$tmp" | cut -f1) - LOGDISK_SLACK ))', script)
        self.assertLess(script.index('logdisk_tail "$room"'), script.index("mformat -i"))
        self.assertIn("journalctl -b -n ", script)


if __name__ == "__main__":
    unittest.main()
