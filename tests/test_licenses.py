# SPDX-License-Identifier: Unlicense
"""SPDX headers: every tracked source file names its license at its top, the one its path expects."""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Path prefix (a folder or one file) -> the SPDX identifiers allowed there; the longest prefix decides.
LICENSES = {
    "": {"Unlicense"},
    "devices/phoenix/src/monado/": {"BSL-1.0"},
    "devices/phoenix/src/splash/": {"BSL-1.0"},
    "devices/phoenix/src/vrlink/": {"BSL-1.0"},
    "devices/phoenix/src/xr/": {"BSL-1.0"},
}

#: Out of the rule: a recipe's files are pinned by its checksums.
EXEMPT = ("devices/phoenix/userspace/pkgbuilds/",)

SOURCE_SUFFIXES = {".c", ".cpp", ".dts", ".frag", ".h", ".hpp", ".ps1", ".py", ".sh", ".vert"}

#: A shebang, a copyright line and the identifier: nothing further down counts as a header.
HEADER_LINES = 3

_SPDX = re.compile(rb"SPDX-License-Identifier:\s*(\S+)")


def spdx_id(data: bytes) -> str | None:
    """Return the identifier in @p data's header, or None."""
    for line in data.splitlines()[:HEADER_LINES]:
        match = _SPDX.search(line)
        if match:
            return match.group(1).decode()
    return None


def allowed(path: str) -> set[str]:
    """Return the identifiers allowed for the repository path @p path."""
    return LICENSES[max((prefix for prefix in LICENSES if path.startswith(prefix)), key=len)]


def is_source(path: str, data: bytes) -> bool:
    """Whether the rule covers @p path. Scripts in the images often have no suffix, only a shebang."""
    if path.startswith(EXEMPT):
        return False
    return Path(path).suffix in SOURCE_SUFFIXES or data.startswith(b"#!")


def tracked() -> list[str]:
    """Return the tracked files; skip the test where git or the repository's history is not there."""
    git = shutil.which("git")
    if git is None:
        msg = "no git"
        raise unittest.SkipTest(msg)
    result = subprocess.run(  # noqa: S603  # git from PATH with fixed arguments
        [git, "ls-files", "-z"], cwd=REPO, capture_output=True, timeout=60, check=False
    )
    if result.returncode != 0:
        msg = "not a git repository"
        raise unittest.SkipTest(msg)
    return [name for name in result.stdout.decode().split("\0") if name and (REPO / name).is_file()]


class RuleTests(unittest.TestCase):
    def test_the_identifier_after_a_shebang_or_a_copyright_line(self):
        self.assertEqual(spdx_id(b"#!/bin/sh\n# SPDX-License-Identifier: Unlicense\n"), "Unlicense")
        self.assertEqual(spdx_id(b"// Copyright\n// SPDX-License-Identifier: BSL-1.0\n"), "BSL-1.0")
        self.assertEqual(spdx_id(b"/* SPDX-License-Identifier: GPL-2.0 */\n"), "GPL-2.0")

    def test_a_mention_further_down_is_no_header(self):
        self.assertIsNone(spdx_id(b"a\nb\nc\n# SPDX-License-Identifier: Unlicense\n"))
        self.assertIsNone(spdx_id(b""))

    def test_the_longest_prefix_decides(self):
        self.assertEqual(allowed("overpressure/tools.py"), {"Unlicense"})
        self.assertEqual(allowed("devices/phoenix/src/stockslam/vst.c"), {"Unlicense"})
        self.assertEqual(allowed("devices/phoenix/src/stockslam/build.sh"), {"Unlicense"})

    def test_what_the_rule_covers(self):
        self.assertTrue(is_source("overpressure/tools.py", b""))
        self.assertTrue(is_source("devices/phoenix/initramfs/init", b"#!/bin/sh\n"))
        self.assertFalse(is_source("README.md", b"# overpressure\n"))
        self.assertFalse(is_source("devices/phoenix/prebuilt/aarch64/pico4-reboot", b"\x7fELF"))
        self.assertFalse(is_source("devices/phoenix/userspace/pkgbuilds/pico4-monado/a.patch", b"From: a\n"))
        self.assertFalse(is_source("devices/phoenix/userspace/pkgbuilds/pico4-sensors/PKGBUILD", b"#!/bin/bash\n"))


class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {}
        for path in tracked():
            data = (REPO / path).read_bytes()
            if is_source(path, data):
                cls.sources[path] = data

    def test_every_source_file_carries_the_expected_header(self):
        wrong = {path: spdx_id(data) for path, data in self.sources.items() if spdx_id(data) not in allowed(path)}
        self.assertEqual(wrong, {})

    def test_every_prefix_of_the_table_still_matches_a_file(self):
        stale = [prefix for prefix in LICENSES if not any(path.startswith(prefix) for path in self.sources)]
        self.assertEqual(stale, [])

    def test_a_shebang_stays_on_the_first_line(self):
        wrong = [path for path, data in self.sources.items() if b"#!" in {line[:2] for line in data.splitlines()[1:2]}]
        self.assertEqual(wrong, [])


#: What THIRD-PARTY.md pins, read from where the build pins it: (file, pattern of the pin).
PINS = (
    ("devices/phoenix/kernel/release.json", r'^ "tag": "([^"]+)"'),
    ("devices/phoenix/userspace/pkgbuilds/pico4-basalt/PKGBUILD", r"commit=([0-9a-f]{12})"),
    ("devices/phoenix/userspace/pkgbuilds/pico4-gamescope/PKGBUILD", r"commit=([0-9a-f]{12})"),
    ("devices/phoenix/userspace/pkgbuilds/pico4-monado/PKGBUILD", r"^_commit=([0-9a-f]{12})"),
    ("devices/phoenix/userspace/pkgbuilds/pico4-sensors/PKGBUILD", r"^_hexagonrpc=([0-9a-f]{12})"),
    ("devices/phoenix/userspace/pkgbuilds/pico4-spa-alsa/PKGBUILD", r"^pkgver=(\S+)"),
)


class ThirdPartyTests(unittest.TestCase):
    def test_the_list_names_what_the_build_pins(self):
        listed = (REPO / "THIRD-PARTY.md").read_text(encoding="utf-8")
        for path, pattern in PINS:
            match = re.search(pattern, (REPO / path).read_text(encoding="utf-8"), re.MULTILINE)
            self.assertIsNotNone(match, path)
            assert match is not None
            self.assertIn(match.group(1), listed, path)


if __name__ == "__main__":
    unittest.main()
