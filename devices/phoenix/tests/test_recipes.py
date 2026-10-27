"""The package recipes under userspace/pkgbuilds: what makepkg checks before a build."""
import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKGBUILDS = ROOT / "userspace/pkgbuilds"


class ChecksumTests(unittest.TestCase):
    def test_checksummed_recipe_files_match(self):
        # Patches and project sources are SKIP; the other files next to a PKGBUILD carry their
        # sha256, and a stale one fails the build only once makepkg validates the sources.
        for recipe in sorted(p for p in PKGBUILDS.iterdir() if (p / "PKGBUILD").is_file()):
            pkgbuild = (recipe / "PKGBUILD").read_text(encoding="utf-8")
            source = re.search(r"^source=\((.*?)\)", pkgbuild, re.S | re.M).group(1)
            for path in sorted(recipe.iterdir()):
                if path.name == "PKGBUILD" or path.suffix == ".patch" or not path.is_file():
                    continue
                if not re.search(r"(^|[\s(/\"])" + re.escape(path.name) + r"([\s)\"]|$)", source):
                    continue
                with self.subTest(recipe=recipe.name, file=path.name):
                    self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), pkgbuild)


if __name__ == "__main__":
    unittest.main()
