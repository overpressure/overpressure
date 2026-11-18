# SPDX-License-Identifier: Unlicense
"""A published release closes the issues whose Stage is Awaiting release, with a comment naming it."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release", REPO / "scripts" / "release.py")
assert spec is not None
assert spec.loader is not None
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def stage(name: str) -> dict:
    """Return the issue-field-values entry of a Stage value."""
    return {"issue_field_name": "Stage", "single_select_option": {"name": name}}


class FakeGitHub:
    """The REST calls close_released makes, answered from @p issues (number -> Stage or None)."""

    def __init__(self, issues: dict[int, str | None], *, pull: int | None = None) -> None:
        """Hold the open issues, and @p pull as an open pull request."""
        self.issues = issues
        self.pull = pull
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, body: dict | None) -> Any:  # noqa: ANN401  # GitHub's JSON
        """Answer one call and record it."""
        self.calls.append((method, path, body))
        if method == "GET" and "/issues?" in path:
            listed: list[dict] = [{"number": n} for n in self.issues]
            if self.pull is not None:
                listed.append({"number": self.pull, "pull_request": {}})
            return listed if "page=1" in path else []
        if method == "GET" and path.endswith("/issue-field-values"):
            number = int(path.split("/")[-2])
            value = self.issues[number]
            other = {"issue_field_name": "Priority", "single_select_option": {"name": "High"}}
            return [other] + ([stage(value)] if value else [])
        return None

    def writes(self) -> list[tuple[str, str, dict | None]]:
        """Return the calls that change something."""
        return [c for c in self.calls if c[0] != "GET"]


class CloseReleasedTests(unittest.TestCase):
    """Only Awaiting release issues are closed, each with the release's link."""

    def run_close(self, fake: FakeGitHub, *, dry_run: bool = False) -> list[int]:
        """Run close_released for v1.0-beta.4 of overpressure/phoenix, quietly."""
        with contextlib.redirect_stdout(io.StringIO()):
            return release.close_released("v1.0-beta.4", "overpressure/phoenix", fake, dry_run=dry_run)

    def test_awaiting_release_issues_get_the_release_and_close(self):
        fake = FakeGitHub({29: "Awaiting release", 63: "In progress", 31: None, 43: "Awaiting release"}, pull=70)
        self.assertEqual(self.run_close(fake), [29, 43])
        url = "https://github.com/overpressure/phoenix/releases/tag/v1.0-beta.4"
        self.assertEqual(
            fake.writes(),
            [
                (
                    "POST",
                    "repos/overpressure/phoenix/issues/29/comments",
                    {"body": f"Released in [v1.0-beta.4]({url})."},
                ),
                ("PATCH", "repos/overpressure/phoenix/issues/29", {"state": "closed", "state_reason": "completed"}),
                (
                    "POST",
                    "repos/overpressure/phoenix/issues/43/comments",
                    {"body": f"Released in [v1.0-beta.4]({url})."},
                ),
                ("PATCH", "repos/overpressure/phoenix/issues/43", {"state": "closed", "state_reason": "completed"}),
            ],
        )
        # The pull request's fields are never read.
        self.assertFalse(any("/issues/70/" in path for _m, path, _b in fake.calls))

    def test_a_dry_run_changes_nothing(self):
        fake = FakeGitHub({29: "Awaiting release"})
        self.assertEqual(self.run_close(fake, dry_run=True), [29])
        self.assertEqual(fake.writes(), [])

    def test_nothing_awaits_nothing_changes(self):
        fake = FakeGitHub({31: "Backlog", 33: "In review"})
        self.assertEqual(self.run_close(fake), [])
        self.assertEqual(fake.writes(), [])

    def test_without_a_token_it_refuses(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(release.main(["close", "v1.0-beta.4", "--repo", "overpressure/phoenix"]), 1)


if __name__ == "__main__":
    unittest.main()
