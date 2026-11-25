# SPDX-License-Identifier: Unlicense
"""SteamVR's English keyboard layout gets its underscore in a headset's image build (build-userspace.sh)."""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STEP = re.compile(r"layout_english_overlay\.json\" <<'EOF'\n(.*?)\nEOF\n", re.DOTALL)


def layout(third_row: list[Any]) -> dict[str, Any]:
    """Return SteamVR's English layout, reduced, with the symbols plane's fourth row given.

    The number row, one letter row and the symbols plane's rows with its tildes; the shift plane
    repeats the number row (Valve's layout_english_overlay.json).
    """
    return {
        "shiftPlanes": [
            {"name": "normal", "rows": [["1", "2", "-"], ["q"], ["", "a"], ["?", "z"], [" "]]},
            {"name": "shift", "rows": [["1", "2", "-"], ["Q"], ["", "A"], ['"', "Z"], [" "]]},
            {"name": "symbols", "rows": [["!", "@"], ["+", "~"], ["", "{"], third_row, [" "]]},
        ]
    }


TILDE_TWICE = ["/", "`", "~", "|"]
TILDE_ONCE = ["/", "`", "_", "|"]
TILDE_ONLY_ONCE = ["/", "`", "^", "|"]


class EnglishKeyboardLayoutTests(unittest.TestCase):
    def built(self, before: dict[str, Any]) -> dict[str, Any]:
        """Return the layout after the step the image build runs over Valve's."""
        build = (ROOT / "overpressure/build/build-userspace.sh").read_text(encoding="utf-8")
        step = STEP.search(build)
        assert step is not None, "the build no longer touches SteamVR's English keyboard layout"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "layout_english_overlay.json"
            path.write_text(json.dumps(before), encoding="utf-8")
            subprocess.run([sys.executable, "-", str(path)], input=step.group(1), text=True, check=True)  # noqa: S603
            return json.loads(path.read_text(encoding="utf-8"))

    def test_the_underscore_takes_the_second_tildes_place(self):
        before = layout(TILDE_TWICE)
        after = self.built(before)
        self.assertEqual(after["shiftPlanes"][2]["rows"][3], ["/", "`", "_", "|"])
        after["shiftPlanes"][2]["rows"][3] = TILDE_TWICE
        self.assertEqual(after, before)  # nothing else changes

    def test_a_layout_with_one_tilde_stays(self):
        before = layout(TILDE_ONLY_ONCE)  # no underscore, but no second tilde to give up either
        self.assertEqual(self.built(before), before)

    def test_a_layout_with_an_underscore_stays(self):
        before = layout(TILDE_ONCE)
        self.assertEqual(self.built(before), before)

    def test_only_the_english_layout_is_written(self):
        build = (ROOT / "overpressure/build/build-userspace.sh").read_text(encoding="utf-8")
        self.assertEqual(len(STEP.findall(build)), 1)
        self.assertNotIn("layout_german", build)


if __name__ == "__main__":
    unittest.main()
