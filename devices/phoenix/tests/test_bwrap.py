import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BubblewrapTests(unittest.TestCase):
    def test_bwrap_is_setuid_in_the_image(self):
        # an issue: without user namespaces Steam Linux Runtime's pressure-vessel needs a setuid
        # bubblewrap, which it finds at /usr/bin/bwrap once its own fails.
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        self.assertIn('chmod 4755 "$T/usr/bin/bwrap"', build)
        self.assertIn('[[ -x "$T/usr/bin/bwrap" ]] || {', build)


if __name__ == "__main__":
    unittest.main()
