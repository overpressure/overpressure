# SPDX-License-Identifier: Unlicense
"""Android games through Valve's Lepton the Frame's way on a mainline kernel: rootless podman."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    """Return a repository file's text."""
    return (ROOT / path).read_text(encoding="utf-8")


class LeptonFrameTests(unittest.TestCase):
    def test_the_session_user_gets_subordinate_ids(self):
        # Rootless podman maps Android's users into them; sysusers gives none.
        build = text("scripts/build-userspace.sh")
        self.assertIn('usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$user"', build)

    def test_adb_finds_protobuf_25_and_bionic_files_are_exempt(self):
        build = text("scripts/build-userspace.sh")
        self.assertIn("-Sw -dd --noconfirm", build)
        self.assertIn("'usr/lib/libprotobuf.so.25*'", build)
        self.assertIn("optional+='|^deckard-(mesa|vulkan-layers)-android-aarch64 '", build)


if __name__ == "__main__":
    unittest.main()
