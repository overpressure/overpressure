# SPDX-License-Identifier: Unlicense
"""The workflows' pins agree: CI runs the uv owners get, and every copy of a tool's pin is the same."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: overpressure's own workflows and the headsets' shared ones (headset-ci.yml, headset-release.yml).
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def found(pattern: str) -> dict[str, set[str]]:
    """Return what @p pattern's group finds, in each workflow that has it."""
    hits = {}
    for workflow in WORKFLOWS:
        values = set(re.findall(pattern, workflow.read_text(encoding="utf-8"), re.MULTILINE))
        if values:
            hits[workflow.name] = values
    return hits


def start_scripts_uv() -> set[str]:
    """Return the uv versions install.sh and Install.cmd fetch for owners."""
    sh = re.findall(r"^UV_VERSION=([0-9.]+)$", (ROOT / "install.sh").read_text(encoding="utf-8"), re.MULTILINE)
    cmd = re.findall(r'^set "UV_VERSION=([0-9.]+)"', (ROOT / "Install.cmd").read_text(encoding="utf-8"), re.MULTILINE)
    return set(sh) | set(cmd)


class WorkflowPinTests(unittest.TestCase):
    def test_ci_runs_the_uv_the_start_scripts_fetch(self):
        owners = start_scripts_uv()
        self.assertEqual(len(owners), 1, "install.sh and Install.cmd fetch different uv versions")
        # Each setup-uv step names its version on the line after its with:.
        versions = found(r'astral-sh/setup-uv@[0-9a-f]{40}[^\n]*\n\s+with:\n\s+version: "([0-9.]+)"')
        steps = found(r"(astral-sh/setup-uv@[0-9a-f]{40})")
        self.assertEqual(set(versions), set(steps), "a setup-uv step without its version")
        for name, values in versions.items():
            with self.subTest(name):
                self.assertEqual(values, owners)

    def test_every_workflow_pins_the_same_setup_uv_and_actionlint(self):
        for pattern in (r"astral-sh/setup-uv@([0-9a-f]{40})", r"^\s+SHA256: ([0-9a-f]{64})$"):
            pins = set().union(*found(pattern).values())
            with self.subTest(pattern):
                self.assertEqual(len(pins), 1, f"different pins: {sorted(pins)}")


if __name__ == "__main__":
    unittest.main()
