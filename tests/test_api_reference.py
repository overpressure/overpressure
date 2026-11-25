# SPDX-License-Identifier: Unlicense
"""docs/api.md, the headset interface's reference, against the code it is written from (scripts/api-reference.py)."""

import importlib.util
import unittest
from pathlib import Path

from overpressure import headset

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("api_reference", REPO / "scripts/api-reference.py")
assert spec is not None
assert spec.loader is not None
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class ApiReferenceTests(unittest.TestCase):
    text: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = reference.reference()

    def test_the_reference_is_current(self):
        # A change to a module of the interface rewrites it: python scripts/api-reference.py.
        self.assertEqual(
            (REPO / "docs/api.md").read_text(encoding="utf-8"),
            self.text,
            "docs/api.md is out of date: python scripts/api-reference.py",
        )

    def test_every_module_of_the_interface_has_a_section(self):
        text = self.text
        for module in sorted((REPO / "overpressure/api/v1").glob("*.py")):
            if module.stem != "__init__":
                self.assertIn(f"\n## {module.stem}\n", text)
                self.assertIn(f"`overpressure.api.v1.{module.stem}`:", text)

    def test_it_names_the_newest_interface(self):
        self.assertIn(f"headset interface {headset.API}, ", self.text)

    def test_a_signature_keeps_its_words_without_its_comments(self):
        text = self.text
        self.assertIn("- `takes_runtime(spec: Spec) -> bool`: ", text)
        self.assertNotIn("noqa", text)
        self.assertIn("- `RUNTIME_SINCE = (1, 8)`: ", text)
        self.assertIn("  - `runtime: bool | None = None`: ", text)


if __name__ == "__main__":
    unittest.main()
