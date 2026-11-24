# SPDX-License-Identifier: Unlicense
"""A release's notes and changelog name who brought each feature and fix, and each release's contributors."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release", REPO / "scripts" / "release.py")
assert spec is not None
assert spec.loader is not None
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class FakeGitHub:
    """The REST calls contributors makes: each commit's pull requests, and the commit itself."""

    def __init__(self, pulls: dict[str, list[dict]], authors: dict[str, str | None]) -> None:
        """Hold each commit's pull requests and, for a commit without one, its GitHub author."""
        self.pulls = pulls
        self.authors = authors
        self.calls: list[str] = []

    def __call__(self, method: str, path: str, body: dict | None) -> Any:  # noqa: ANN401  # GitHub's JSON
        """Answer one GET and record it."""
        assert method == "GET"
        assert body is None
        self.calls.append(path)
        sha = path.split("/")[4]
        if path.endswith("/pulls"):
            return self.pulls.get(sha, [])
        login = self.authors.get(sha)
        return {"author": {"login": login} if login else None}


def pull(number: int, login: str, *, merged: bool = True) -> dict:
    """Return a pull request as GitHub lists it for a commit."""
    return {"number": number, "user": {"login": login}, "merged_at": "2026-11-24T00:00:00Z" if merged else None}


def context(*releases: list[str]) -> list[dict]:
    """Return git-cliff's context of releases with these commits."""
    return [
        {"version": None, "commits": [{"id": sha, "extra": None} for sha in shas], "extra": None} for shas in releases
    ]


class ContributorsTests(unittest.TestCase):
    """The pull request's author names a commit, whatever name and address the commit itself carries."""

    def test_the_pull_requests_author_names_the_commit(self):
        fake = FakeGitHub({"a1": [pull(61, "osxdoc")]}, {})
        named = release.contributors(context(["a1"]), "overpressure/overpressure", fake)
        self.assertEqual(named[0]["commits"][0]["extra"], {"author": "osxdoc", "pr": 61})
        self.assertEqual(named[0]["extra"], {"contributors": ["osxdoc"]})

    def test_a_merged_pull_request_comes_before_one_that_was_closed(self):
        fake = FakeGitHub({"a1": [pull(60, "someone", merged=False), pull(61, "osxdoc")]}, {})
        named = release.contributors(context(["a1"]), "o/r", fake)
        self.assertEqual(named[0]["commits"][0]["extra"], {"author": "osxdoc", "pr": 61})

    def test_a_commit_without_pull_request_takes_its_github_author(self):
        fake = FakeGitHub({}, {"b2": "Xenolphthalein"})
        named = release.contributors(context(["b2"]), "o/r", fake)
        self.assertEqual(named[0]["commits"][0]["extra"], {"author": "Xenolphthalein", "pr": None})

    def test_a_commit_nobody_on_github_made_stays_unnamed(self):
        fake = FakeGitHub({}, {"c3": None})
        named = release.contributors(context(["c3"]), "o/r", fake)
        self.assertIsNone(named[0]["commits"][0]["extra"])
        self.assertEqual(named[0]["extra"], {"contributors": []})

    def test_each_release_lists_its_contributors_once_sorted(self):
        fake = FakeGitHub(
            {"a1": [pull(1, "zeta")], "a2": [pull(2, "Alpha")], "a3": [pull(3, "zeta")], "b1": [pull(4, "beta")]}, {}
        )
        named = release.contributors(context(["a1", "a2", "a3"], ["b1"]), "o/r", fake)
        self.assertEqual(named[0]["extra"]["contributors"], ["Alpha", "zeta"])
        self.assertEqual(named[1]["extra"]["contributors"], ["beta"])

    def test_each_commit_is_looked_up_once(self):
        fake = FakeGitHub({"a1": [pull(1, "zeta")]}, {})
        release.contributors(context(["a1"], ["a1"]), "o/r", fake)
        self.assertEqual(fake.calls, ["repos/o/r/commits/a1/pulls"])

    def test_the_templates_print_the_names(self):
        cliff = (REPO / "cliff.toml").read_text(encoding="utf-8")
        for name in ("commit.extra.author", "commit.extra.pr", "extra.contributors"):
            self.assertIn(name, cliff)

    def test_the_release_gives_the_notes_a_token(self):
        workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        step = workflow.split("- name: Notes and changelog", 1)[1].split("- name:", 1)[0]
        self.assertIn("GH_TOKEN: ${{ github.token }}", step)


if __name__ == "__main__":
    unittest.main()
