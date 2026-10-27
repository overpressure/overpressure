import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "userspace/rootfs/usr/bin/pico4-valve-session-check"


class SessionCheckDropinTests(unittest.TestCase):
    def test_user_manager_reads_the_dropin(self):
        # The user manager outlives a session restart, so a drop-in written without a reload
        # never reaches steamvr.service: the layer then writes no statistics.
        text = CHECK.read_text(encoding="utf-8")
        write = text.index('> "$dropin"')
        restart = text.index("systemctl restart pico4-session.service", write)
        self.assertRegex(text[write:restart], r"user_systemctl daemon-reload")
        removal = text.index('rm -f "$dropin"', restart)
        back = text.index("systemctl restart pico4-session.service", removal)
        self.assertRegex(text[removal:back], r"user_systemctl daemon-reload")

    def test_reload_is_not_fatal(self):
        # Without a running user manager the next one reads the drop-in at its start.
        for m in re.finditer(r"(?m)^user_systemctl daemon-reload.*$", CHECK.read_text(encoding="utf-8")):
            self.assertIn("|| true", m.group(0))


if __name__ == "__main__":
    unittest.main()
