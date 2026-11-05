# SPDX-License-Identifier: Unlicense
"""The repository's own checks: the private-data scan, the link check and the changelog."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


private = load("check-private")
docs = load("check-docs")
changelog = load("changelog")


class PrivateDataTests(unittest.TestCase):
    def kinds(self, path, data, secrets=()):
        return [line.split(": ")[1] for line in private.findings(path, data, secrets)]

    def test_what_it_finds(self):
        cases = {
            b"mac aa:bb:cc:dd:ee:ff": "MAC address",
            b"steam 76561198000000001": "Steam ID",
            # Split, so that the scan of this file finds nothing.
            b"host 192.168" b".2.40": "private address",
            b"host 10" b".1.2.3": "private address",
            b"C:\\Users\\" b"someone\\x": "personal path",
            b"/mnt/c/Users/" b"someone/x": "personal path",
            b"-----BEGIN OPENSSH PRIVATE KEY-----": "private key",
        }
        for data, kind in cases.items():
            self.assertEqual(self.kinds("devices/phoenix/docs/a.md", data), [kind], data)

    def test_the_projects_own_addresses_pass(self):
        for data in (b"172.16.42.1", b"192.168.137.1 is Windows' shared internet", b"version 1.0.30.0", b"C:/Users/x"):
            self.assertEqual(self.kinds("docs/a.md", data), [])

    def test_fake_identifiers_in_tests_pass(self):
        self.assertEqual(self.kinds("devices/phoenix/tests/test_a.py", b"aa:bb:cc:dd:ee:ff 76561198000000001"), [])

    def test_a_local_value_is_found_and_not_shown(self):
        (line,) = private.findings("docs/a.md", b"serial HEADSET-ONE", [b"HEADSET-ONE"])
        self.assertNotIn("HEADSET-ONE", line)

    def test_a_vendor_register_table_and_a_binary_are_found(self):
        table = b"{ 0x3c7e, 0x02 }, " * private.REGISTER_TABLE
        self.assertIn("register pairs", private.findings("devices/phoenix/kernel/src/a.c", table)[0])
        self.assertEqual(private.findings("devices/phoenix/kernel/src/a.c", b"{ 0x3c7e, 0x02 }, " * 3), [])
        self.assertIn("binary file", private.findings("devices/phoenix/a.bin", b"\0\1\2")[0])

    def test_its_own_binaries_pass(self):
        self.assertEqual(private.findings("devices/phoenix/prebuilt/aarch64/pico4-reboot", b"\x7fELF\0\0"), [])

    def test_the_tree_is_clean(self):
        found = [line for name, data in private.tracked() for line in private.findings(name, data)]
        self.assertEqual(found, [])


class LinkTests(unittest.TestCase):
    def test_broken_links_are_found(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "b.md").write_text("# A Title: here\n", encoding="utf-8")
            text = ("[good](b.md) [good](b.md#a-title-here) [web](https://example.com) `[code](none.md)`\n"
                    "```\n[fenced](none.md)\n```\n[gone](c.md) [nohead](b.md#other)\n")
            self.assertEqual(docs.broken(root / "a.md", text), ["c.md: no such file", "b.md#other: no such heading"])


class ChangelogTests(unittest.TestCase):
    def test_features_and_fixes_for_users_the_rest_as_a_count(self):
        text = changelog.notes(["fix: PipeWire survives a suspend", "docs: remove what is obsolete",
                                "feat(steam): 72 Hz", "feat!: a new layout", "not conventional"], "1.0-beta")
        self.assertIn("## 1.0-beta", text)
        self.assertIn("### Features\n\n- 72 Hz\n- a new layout (breaking)\n", text)
        self.assertIn("### Fixes\n\n- PipeWire survives a suspend\n", text)
        self.assertIn("2 other commit(s)", text)


if __name__ == "__main__":
    unittest.main()
