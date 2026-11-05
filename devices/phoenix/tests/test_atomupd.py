# SPDX-License-Identifier: Unlicense
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "userspace/rootfs/usr/bin/atomupd-manager"


def arms(script: str) -> dict[str, str]:
    """The script's case arms: each pattern alternative to its command."""
    result = {}
    for patterns, body in re.findall(r"(?m)^\t([a-z| -]+)\) (.+?) ;;$", script):
        for pattern in patterns.split("|"):
            result[pattern.strip()] = body
    return result


class AtomupdManagerTests(unittest.TestCase):
    """The queries Valve's steamos-select-branch and steamos-update make."""

    def setUp(self):
        self.script = MANAGER.read_text(encoding="utf-8")
        self.arms = arms(self.script)

    def test_queries_describe_an_up_to_date_stable_system(self):
        # steamos-select-branch -c maps "stable" to the client's "rel".
        self.assertEqual(self.arms["tracked-branch"], "echo stable")
        self.assertEqual(self.arms["list-branches"], "echo stable")
        # steamos-update exits 7 ("No update available") on exactly this line.
        self.assertEqual(self.arms["check"], 'echo "No update available"')
        # "successful" would make the client ask for a reboot.
        self.assertEqual(self.arms["get-update-status"], "echo idle")

    def test_changes_are_refused(self):
        for command in ("switch-branch", "update"):
            self.assertEqual(self.arms[command], "refuse", command)
        self.assertRegex(self.script, r"refuse\(\) \{\n\techo [^\n]*installing a new image[^\n]* >&2\n\texit 1")

    def test_the_updater_itself_is_not_installed(self):
        packages = (ROOT / "userspace/packages.txt").read_text(encoding="utf-8").split()
        self.assertNotIn("atomupd-daemon", packages)
        self.assertNotIn("steamos-atomupd-client", packages)


if __name__ == "__main__":
    unittest.main()
