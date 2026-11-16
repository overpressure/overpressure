# SPDX-License-Identifier: Unlicense
"""overpressure.version: the version in the release tags' spelling, its channel and its commit."""

from __future__ import annotations

import unittest

from overpressure import version


class VersionTests(unittest.TestCase):
    def test_the_tags_spelling(self):
        self.assertEqual(version.tag_spelling("1.0b1"), "1.0-beta")
        self.assertEqual(version.tag_spelling("1.0b2"), "1.0-beta.2")
        self.assertEqual(version.tag_spelling("1.0"), "1.0")

    def test_the_channel(self):
        self.assertEqual(version.channel("1.0"), version.STABLE)
        self.assertEqual(version.channel("1.0b2"), version.BETA)
        self.assertEqual(version.channel("1.0b2.dev0"), version.PREVIEW)

    def test_describe_names_the_version_channel_and_commit(self):
        words = version.describe()
        self.assertTrue(words.startswith("overpressure "), words)
        self.assertRegex(words, r"\((stable|beta|preview)(, commit [0-9a-f]{12})?\)$")


if __name__ == "__main__":
    unittest.main()
