# SPDX-License-Identifier: Unlicense
"""overpressure.appupdate: the newest release on the app's channel that carries the app."""

from __future__ import annotations

import json
import unittest

from overpressure import appupdate


def release(tag: str, *, prerelease: bool = False, app: bool = True, draft: bool = False) -> dict:
    """Return one release as GitHub's API lists it."""
    assets = [{"name": f"overpressure-{tag}.zip", "browser_download_url": f"https://example.invalid/{tag}.zip"}]
    if app:
        assets.append({"name": appupdate.MANIFEST, "browser_download_url": f"https://example.invalid/{tag}.json"})
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "assets": assets,
        "html_url": f"https://github.com/overpressure/overpressure/releases/tag/{tag}",
        "body": f"Notes of {tag}",
    }


RELEASES = [
    release("2026.12.0b2", prerelease=True),
    release("2026.12.0b1", prerelease=True),
    release("2026.11.1", app=False),  # from before the app, or its build failed
    release("2026.11.0"),
    release("2026.11.0b1", prerelease=True),
]


def listing(found: list[dict]):
    """Return a fetch that answers the releases' listing with FOUND."""
    return lambda _url: json.dumps(found).encode()


class NewestTests(unittest.TestCase):
    def test_a_beta_takes_the_newest_beta(self):
        update = appupdate.newest("2026.11.0b1", listing(RELEASES))
        assert update is not None
        self.assertEqual((update.version, update.words), ("2026.12.0b2", "2026.12.0 beta 2"))
        self.assertTrue(update.manifest.endswith("2026.12.0b2.json"))
        self.assertEqual(update.notes, "Notes of 2026.12.0b2")

    def test_stable_takes_stable_releases_only(self):
        self.assertIsNone(appupdate.newest("2026.11.0", listing(RELEASES)))  # 2026.11.1 carries no app
        stable = [release("2026.12.0"), *RELEASES]
        update = appupdate.newest("2026.11.0", listing(stable))
        assert update is not None
        self.assertEqual(update.version, "2026.12.0")

    def test_a_beta_takes_the_stable_release_once_it_is_the_newest(self):
        update = appupdate.newest("2026.12.0b2", listing([release("2026.12.0"), *RELEASES]))
        assert update is not None
        self.assertEqual(update.version, "2026.12.0")

    def test_a_preview_takes_betas(self):
        update = appupdate.newest("2026.12.0.dev202612010000", listing(RELEASES))
        assert update is not None
        self.assertEqual(update.version, "2026.12.0b2")

    def test_nothing_newer_drafts_and_releases_without_the_app_are_not_offered(self):
        self.assertIsNone(appupdate.newest("2026.12.0b2", listing(RELEASES)))
        self.assertIsNone(appupdate.newest("2026.12.0b2", listing([release("2026.12.0", draft=True)])))
        self.assertIsNone(appupdate.newest("not a version", listing(RELEASES)))

    def test_long_notes_are_cut_at_a_line(self):
        long = release("2026.12.0b3", prerelease=True)
        long["body"] = "\n".join(f"- change {n}" for n in range(400))
        update = appupdate.newest("2026.12.0b2", listing([long]))
        assert update is not None
        self.assertLessEqual(len(update.notes), appupdate.NOTES + 2)
        self.assertTrue(update.notes.endswith("\n…"))
        self.assertIn("- change 0\n", update.notes)


class SemverTests(unittest.TestCase):
    def test_the_bundle_spells_versions_as_semver(self):
        self.assertEqual(appupdate.semver("2026.12.0"), "2026.12.0")
        self.assertEqual(appupdate.semver("2026.12.0b3"), "2026.12.0-beta.3")
        self.assertEqual(appupdate.semver("2026.12.0.dev5"), "2026.12.0-dev.5")
        self.assertEqual(appupdate.semver("1.0b4"), "1.0.0-beta.4")
        with self.assertRaises(ValueError):
            appupdate.semver("main")


if __name__ == "__main__":
    unittest.main()
