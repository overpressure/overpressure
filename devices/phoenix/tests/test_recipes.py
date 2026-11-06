# SPDX-License-Identifier: Unlicense
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
            match = re.search(r"^source=\((.*?)\)", pkgbuild, re.DOTALL | re.MULTILINE)
            assert match is not None
            source = match.group(1)
            for path in sorted(recipe.iterdir()):
                if path.name == "PKGBUILD" or path.suffix == ".patch" or not path.is_file():
                    continue
                if not re.search(r"(^|[\s(/\"])" + re.escape(path.name) + r"([\s)\"]|$)", source):
                    continue
                with self.subTest(recipe=recipe.name, file=path.name):
                    self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), pkgbuild)


class RegistryWritesTests(unittest.TestCase):
    """The sensors process's registry writes: taken and dropped, so that its IMU comes without 37 s of retries."""

    def test_only_the_registry_is_taken_and_nothing_reaches_a_file(self):
        patch = (
            Path(__file__).resolve().parents[1] / "userspace/pkgbuilds/pico4-sensors/hexagonrpc-registry-writes.patch"
        ).read_text(encoding="utf-8")
        self.assertIn('+	return strstr(path, "/sensors/registry/") != NULL;', patch)
        self.assertIn("+		if (is_registry_path(inbufs[3].p)) {", patch)
        self.assertIn('fprintf(stderr, "Tried to open %s for writing', patch)
        added = "\n".join(line for line in patch.splitlines() if line.startswith("+"))
        for call in ("fopen(", "fwrite(", "open(", "rename(", "write("):
            self.assertNotRegex(added, r"(?<![_\w])" + re.escape(call))

    def test_large_inputs_come_through_the_listener(self):
        patch = (
            Path(__file__).resolve().parents[1] / "userspace/pkgbuilds/pico4-sensors/hexagonrpc-registry-writes.patch"
        ).read_text(encoding="utf-8")
        self.assertIn("-		fprintf(stderr, \"Large (>256B) input buffers aren't implemented", patch)
        self.assertIn("+		ret = adsp_listener_get_in_bufs2(fd, *rctx, offset, &got, want, more);", patch)


if __name__ == "__main__":
    unittest.main()
