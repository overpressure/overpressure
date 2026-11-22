# SPDX-License-Identifier: Unlicense
"""overpressure.version: calendar versions, their order, channel and two spellings, and this one's commit."""

from __future__ import annotations

import datetime as dt
import unittest

from overpressure import version

WHEN = dt.datetime(2026, 12, 3, 12, 54, tzinfo=dt.timezone(dt.timedelta(hours=1)))


def order(text: str) -> version.Key:
    """Return a version's sorting key, for versions known to parse."""
    found = version.key(text)
    assert found is not None
    return found


class VersionTests(unittest.TestCase):
    def test_every_spelling_of_both_schemes_parses(self):
        for text, parsed in (
            ("2026.12.0", version.Version((2026, 12, 0))),
            ("2026.12.0b3", version.Version((2026, 12, 0), 3)),
            ("2026.12.0b4.dev0", version.Version((2026, 12, 0), 4, 0)),
            ("2026.12.0.dev202612031154", version.Version((2026, 12, 0), None, 202612031154)),
            ("2026.12.0 beta 3", version.Version((2026, 12, 0), 3)),
            ("1.0b5.dev0", version.Version((1, 0), 5, 0)),
            ("v1.0-beta", version.Version((1, 0), 1)),
            ("v1.0-beta.2", version.Version((1, 0), 2)),
            ("1.0 beta 5", version.Version((1, 0), 5)),
            ("v1.0", version.Version((1, 0))),
        ):
            with self.subTest(text=text):
                self.assertEqual(version.parse(text), parsed)
        for text in ("main", "", "phoenix/7.2-6", "1.0-rc1", "2026.12.0-beta"):
            with self.subTest(text=text):
                self.assertIsNone(version.parse(text))

    def test_the_order_as_python_sorts_and_the_old_scheme_first(self):
        texts = [
            "2026.12.0", "2026.12.0b1", "2026.11.3", "2026.12.0.dev202612031154", "2026.12.0b1.dev202612050101",
            "v1.0-beta.2", "2026.12.0b0", "1.0b5.dev0", "v1.0-beta",
        ]  # fmt: skip
        self.assertEqual(
            sorted(texts, key=order),
            [
                "v1.0-beta", "v1.0-beta.2", "1.0b5.dev0", "2026.11.3", "2026.12.0.dev202612031154", "2026.12.0b0",
                "2026.12.0b1.dev202612050101", "2026.12.0b1", "2026.12.0",
            ],
        )  # fmt: skip

    def test_the_channel_and_the_two_spellings(self):
        self.assertEqual(
            [version.channel(t) for t in ("2026.12.0", "2026.12.0b3", "2026.12.0.dev0", "main")],
            [version.STABLE, version.BETA, version.PREVIEW, version.PREVIEW],
        )
        self.assertEqual(version.machine("2026.12.0 beta 3"), "2026.12.0b3")
        self.assertEqual(version.display("2026.12.0b3"), "2026.12.0 beta 3")
        self.assertEqual(version.display("2026.12.0"), "2026.12.0")
        self.assertEqual(version.display("v1.0-beta"), "1.0 beta")  # the old first beta
        self.assertEqual(version.display("1.0b5"), "1.0 beta 5")
        self.assertEqual(version.display("main"), "main")
        self.assertEqual(version.tag_spelling("2026.12.0b3"), "2026.12.0b3")
        self.assertEqual(version.tag_spelling("1.0b2"), "1.0-beta.2")

    def test_a_preview_is_named_by_its_commits_time(self):
        self.assertEqual(version.preview_of("2026.12.0.dev0", WHEN), "2026.12.0.dev202612031154")  # in UTC
        self.assertEqual(version.preview_of("2026.12.0b4.dev0", WHEN), "2026.12.0b4.dev202612031154")
        self.assertEqual(version.preview_of("2026.12.0b3", WHEN), "2026.12.0b3")  # a release stays as it is
        self.assertEqual(
            version.preview_words("2026.12.0.dev0", WHEN, "4d8f9770123"),
            "2026.12.0 preview from 3 December 2026 (4d8f977)",
        )
        self.assertEqual(version.committed_at("1764762840"), dt.datetime(2025, 12, 3, 11, 54, tzinfo=dt.timezone.utc))
        self.assertEqual(version.committed_at("2026-12-03T12:54:00+01:00"), WHEN)
        self.assertIsNone(version.committed_at("$Format:%ct$"))

    def test_describe_names_both_spellings_and_the_commit(self):
        words = version.describe()
        self.assertTrue(words.startswith("overpressure "), words)
        self.assertRegex(words, r"\(\d{4}\.\d+\.\d+(b\d+)?(\.dev\d+)?(, commit [0-9a-f]{12})?\)$")


if __name__ == "__main__":
    unittest.main()
