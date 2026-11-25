# SPDX-License-Identifier: Unlicense
"""The organisation's own actions: CI runs the uv owners get, and no workflow pins its own copy of a tool."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB = ROOT / ".github"


def pinned(path: Path, pattern: str) -> str:
    """Return the one version @p pattern finds in @p path."""
    (found,) = re.findall(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    return found


class WorkflowTests(unittest.TestCase):
    def test_ci_runs_the_uv_the_start_scripts_fetch(self):
        action = pinned(GITHUB / "actions/uv/action.yml", r'^\s+version: "([0-9.]+)"')
        self.assertEqual(pinned(ROOT / "install.sh", r"^UV_VERSION=([0-9.]+)$"), action)
        self.assertEqual(pinned(ROOT / "Install.cmd", r'^set "UV_VERSION=([0-9.]+)"'), action)

    def test_the_workflows_take_uv_and_actionlint_from_the_actions(self):
        for workflow in sorted((GITHUB / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow.name):
                self.assertNotIn("astral-sh/setup-uv@", text, "use .github/actions/uv")
                self.assertNotIn("rhysd/actionlint/releases", text, "use .github/actions/actionlint")


if __name__ == "__main__":
    unittest.main()
