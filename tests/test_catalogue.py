# SPDX-License-Identifier: Unlicense
"""overpressure.catalogue and the registry's interface check: a headset's releases, downloaded into devices/."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from overpressure import catalogue, headset
from overpressure.headset import API, refusal

REPO = "https://api.github.com/repos/overpressure/phoenix/releases?per_page=100"


def archive(version: str, api: str = API, codename: str = "phoenix") -> bytes:
    """Return a release zip as git archive makes it: one top folder, headset.json, a program."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as out:
        manifest = {"codename": codename, "name": "PICO 4", "version": version, "api": api}
        out.writestr(f"phoenix-{version}/headset.json", json.dumps(manifest))
        program = zipfile.ZipInfo(f"phoenix-{version}/scripts/run.sh")
        program.external_attr = 0o100755 << 16
        out.writestr(program, "#!/bin/sh\n")
    return data.getvalue()


class Published:
    """GitHub as far as the catalogue asks it: the release list, each release's zip and checksum, main."""

    def __init__(self, *releases: tuple[str, bool, bytes]) -> None:
        self.files: dict[str, bytes] = {}
        listing = []
        for tag, prerelease, data in releases:
            zip_url, sha_url = f"https://x/{tag}.zip", f"https://x/{tag}.zip.sha256"
            self.files[zip_url] = data
            self.files[sha_url] = f"{hashlib.sha256(data).hexdigest()}  phoenix.zip\n".encode()
            assets = [{"name": "p.zip", "browser_download_url": zip_url}]
            assets.append({"name": "p.zip.sha256", "browser_download_url": sha_url})
            listing.append({"tag_name": tag, "prerelease": prerelease, "draft": False, "assets": assets})
            with zipfile.ZipFile(
                io.BytesIO(data)
            ) as out:  # the tag's headset.json, as raw.githubusercontent.com has it
                manifest = next(n for n in out.namelist() if n.endswith("/headset.json"))
                self.files[f"{catalogue.RAW}/overpressure/phoenix/{tag}/headset.json"] = out.read(manifest)
        self.files[REPO] = json.dumps(listing).encode()
        self.files["https://codeload.github.com/overpressure/phoenix/zip/refs/heads/main"] = archive("1.2-dev")

    def get(self, url: str) -> bytes:
        return self.files[url]


class CatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.github = Published(
            ("v1.1-beta", True, archive("1.1-beta")), ("v1.0", False, archive("1.0")), ("v0.9", False, archive("0.9"))
        )
        self.lines: list[str] = []

    def add(self, **options: str) -> catalogue.Release:
        return catalogue.add("phoenix", root=self.root, get=self.github.get, log=self.lines.append, **options)

    def version(self) -> str:
        return json.loads((self.root / "devices/phoenix/headset.json").read_text(encoding="utf-8"))["version"]

    def test_each_channel_and_a_version_take_their_release(self):
        for options, version in (
            ({}, "1.0"),
            ({"channel": catalogue.BETA}, "1.1-beta"),
            ({"version": "0.9"}, "0.9"),
            ({"channel": catalogue.PREVIEW}, "1.2-dev"),
        ):
            self.add(**options)
            self.assertEqual(self.version(), version, options)
        self.assertEqual(catalogue.installed("phoenix", self.root), "preview (main)")
        self.add(version="v1.0")
        self.assertEqual(catalogue.installed("phoenix", self.root), "release 1.0")
        self.assertTrue((self.root / "devices/phoenix/scripts/run.sh").is_file())
        self.assertEqual(sorted(p.name for p in (self.root / "devices").iterdir()), ["phoenix"])  # nothing left over

    def test_an_unknown_version_names_the_published_ones(self):
        with self.assertRaisesRegex(LookupError, r"no release 2\.0 \(published: 1\.1-beta, 1\.0, 0\.9\)"):
            self.add(version="2.0")

    def test_a_wrong_checksum_or_interface_keeps_what_was_there(self):
        self.add()
        self.github.files["https://x/v0.9.zip.sha256"] = b"0" * 64
        with self.assertRaisesRegex(RuntimeError, "is not the published"):
            self.add(version="0.9")
        main = "https://codeload.github.com/overpressure/phoenix/zip/refs/heads/main"
        self.github.files[main] = archive("2.0-dev", api="2.0")  # its zip says more than the tag's file did
        with self.assertRaisesRegex(RuntimeError, "needs headset interface 2.0"):
            self.add(channel=catalogue.PREVIEW)
        self.assertEqual(self.version(), "1.0")
        self.assertEqual(sorted(p.name for p in (self.root / "devices").iterdir()), ["phoenix"])

    def test_releases_this_overpressure_cannot_run_are_marked_and_never_picked(self):
        self.github = Published(
            ("v2.0", False, archive("2.0", api="2.0")), ("v1.1", False, archive("1.1")), ("v0.5", False, archive("0.5"))
        )
        found = catalogue.releases(catalogue.CATALOGUE["phoenix"], self.github.get)
        self.assertEqual([bool(r.problem) for r in found], [True, False, False])
        self.assertEqual(self.add().version, "1.1")  # the newest that runs here
        with self.assertRaisesRegex(LookupError, r"release 2\.0: it needs headset interface 2\.0"):
            self.add(version="2.0")
        self.assertEqual(getattr(catalogue.oldest_supported(found), "tag", None), "v0.5")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            catalogue.main(["releases", "phoenix"], self.root, self.github.get)
            catalogue.main(["releases", "phoenix", "--oldest-supported"], self.root, self.github.get)
        lines = out.getvalue().splitlines()
        self.assertIn("update overpressure", lines[0])
        self.assertEqual((lines[1].split()[-2:], lines[-1]), (["runs", "here"], "v0.5"))

    def test_a_development_checkout_is_never_replaced(self):
        (self.root / "devices/phoenix/.git").mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "development checkout"):
            self.add()
        self.assertEqual(catalogue.installed("phoenix", self.root), "development checkout")

    def test_stable_takes_the_newest_beta_until_there_is_a_stable_release(self):
        self.github = Published(("v1.0-beta.2", True, archive("1.0-beta.2")), ("v1.0-beta", True, archive("1.0-beta")))
        self.assertEqual(self.add().version, "1.0-beta.2")
        self.assertEqual(catalogue.record_of("phoenix", self.root)["channel"], "stable")

    def test_an_update_follows_the_recorded_channel_and_a_pinned_release_stays(self):
        self.add(version="0.9")  # pinned: asked for by version
        self.assertTrue(catalogue.record_of("phoenix", self.root)["pinned"])
        self.assertIsNone(catalogue.update("phoenix", self.root, self.github.get))
        for channel in (catalogue.STABLE, catalogue.BETA):
            self.add(channel=channel)
            self.assertIsNone(catalogue.update("phoenix", self.root, self.github.get), channel)  # it has the newest
            self.github = Published(
                ("v1.2", False, archive("1.2")),
                ("v1.1-beta", True, archive("1.1-beta")),
                ("v1.0", False, archive("1.0")),
            )
            found = catalogue.update("phoenix", self.root, self.github.get)
            self.assertEqual(getattr(found, "version", None), "1.2", channel)
            self.setUp()
        self.add(channel=catalogue.PREVIEW)  # main's commit as its headset.json carries it
        commits = f"{catalogue.GITHUB}/repos/overpressure/phoenix/commits/main"
        self.github.files[commits] = json.dumps({"sha": "abc"}).encode()
        self.assertEqual(getattr(catalogue.update("phoenix", self.root, self.github.get), "tag", None), "main")

    def test_versions_sort_betas_before_their_release(self):
        order = ["0.9", "1.0-beta", "1.0-beta.2", "1.0", "1.1-beta", "1.1", "2.0"]
        keys = [catalogue.version_key(v) for v in order]
        self.assertEqual(keys, sorted(k for k in keys if k is not None))  # each later than the one before
        self.assertEqual(len(set(keys)), len(order))
        self.assertIsNone(catalogue.version_key("main"))

    def test_the_command_line_lists_and_adds(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(catalogue.main(["add", "phoenix", "--channel", "beta"], self.root, self.github.get), 0)
            self.assertEqual(catalogue.main(["add", "phoenix", "--version", "3.0"], self.root, self.github.get), 1)
            self.assertEqual(catalogue.main(["list"], self.root, self.github.get), 0)
        self.assertEqual(self.version(), "1.1-beta")
        self.assertIn("phoenix      PICO 4       release 1.1-beta", out.getvalue())


class RefusalTests(unittest.TestCase):
    def test_a_folder_runs_only_with_its_own_manifest_and_this_interface(self):
        self.assertEqual(refusal("phoenix", {"codename": "phoenix", "version": "1.0", "api": API}), "")
        self.assertIn("no headset.json", refusal("phoenix", {}))
        self.assertIn("names 'other'", refusal("phoenix", {"codename": "other", "api": API}))
        self.assertIn("update overpressure", refusal("phoenix", {"codename": "phoenix", "api": "2.0"}))

    def test_a_minor_adds_and_a_major_breaks(self):
        with mock.patch.dict(headset.SUPPORTED, {1: 3, 2: 1}, clear=True), mock.patch.dict(headset.RETIRED, {0: "0.9"}):
            for api in (1, "1.0", "1.3", "2.1"):  # a bare 1 is 1.0
                self.assertEqual(headset.api_problem(api), "", api)
            self.assertIn("runs 1.0 to 1.3, 2.0 to 2.1: update overpressure", headset.api_problem("1.4"))
            self.assertIn("update overpressure", headset.api_problem("3.0"))
            self.assertIn("overpressure 0.9 was the last to run: use that one", headset.api_problem("0.2"))
            self.assertIn("is no MAJOR.MINOR", headset.api_problem("one"))


if __name__ == "__main__":
    unittest.main()
