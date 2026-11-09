# SPDX-License-Identifier: Unlicense
"""The repository's own checks: the private-data scan, the link check and the changelog."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType

REPO = Path(__file__).resolve().parents[1]


def load(name: str) -> ModuleType:
    """Load one of the repository's check scripts, whose names have dashes, as a module."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), REPO / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


private = load("check-private")
docs = load("check-docs")
changelog = load("changelog")


class PrivateDataTests(unittest.TestCase):
    def kinds(self, path: str, data: bytes, secrets: Iterable[bytes] = ()) -> list[str]:
        return [line.split(": ")[1] for line in private.findings(path, data, secrets)]

    def test_what_it_finds(self):
        cases = {
            b"mac aa:bb:cc:dd:ee:ff": "MAC address",
            b"steam 76561198000000001": "Steam ID",
            # Joined at run time, so that the scan of this file finds nothing.
            b"host 192.168." + b"2.40": "private address",
            b"host 10." + b"1.2.3": "private address",
            b"C:\\Users\\" + b"someone\\x": "personal path",
            b"/mnt/c/Users/" + b"someone/x": "personal path",
            b"-----BEGIN OPENSSH PRIVATE KEY-----": "private key",
        }
        for data, kind in cases.items():
            self.assertEqual(self.kinds("devices/phoenix/docs/a.md", data), [kind], data)

    def test_the_projects_own_addresses_pass(self):
        for data in (b"172.16.42.1", b"192.168.137.1 is Windows' shared internet", b"version 1.0.30.0", b"C:/Users/x"):
            self.assertEqual(self.kinds("docs/a.md", data), [])

    def test_fake_identifiers_in_tests_pass(self):
        self.assertEqual(self.kinds("devices/phoenix/tests/test_a.py", b"aa:bb:cc:dd:ee:ff 76561198000000001"), [])

    def test_a_local_value_is_found_and_not_shown(self):
        (line,) = private.findings("docs/a.md", b"serial HEADSET-ONE", [b"HEADSET-ONE"])
        self.assertNotIn("HEADSET-ONE", line)

    def test_the_private_words_come_from_env(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text(
                "PICO4_ADB_SERIAL='SERIAL-ONE'\nOVERPRESSURE_PRIVATE_WORDS=host.example, Someone ,ab\n",
                encoding="utf-8",
            )
            # Too short a word would match by chance and is left out.
            self.assertEqual(private.local_secrets(Path(folder)), [b"SERIAL-ONE", b"host.example", b"Someone"])

    def test_a_vendor_register_table_and_a_binary_are_found(self):
        table = b"{ 0x3c7e, 0x02 }, " * private.REGISTER_TABLE
        self.assertIn("register pairs", private.findings("devices/phoenix/kernel/src/a.c", table)[0])
        self.assertEqual(private.findings("devices/phoenix/kernel/src/a.c", b"{ 0x3c7e, 0x02 }, " * 3), [])
        self.assertIn("binary file", private.findings("devices/phoenix/a.bin", b"\0\1\2")[0])

    def test_its_own_binaries_pass(self):
        self.assertEqual(private.findings("devices/phoenix/prebuilt/aarch64/pico4-reboot", b"\x7fELF\0\0"), [])

    def test_the_tree_is_clean(self):
        found = [line for name, data in private.tracked() for line in private.findings(name, data)]
        self.assertEqual(found, [])


class LinkTests(unittest.TestCase):
    def test_broken_links_are_found(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "b.md").write_text("# A Title: here\n", encoding="utf-8")
            text = (
                "[good](b.md) [good](b.md#a-title-here) [web](https://example.com) `[code](none.md)`\n"
                "```\n[fenced](none.md)\n```\n[gone](c.md) [nohead](b.md#other)\n"
            )
            self.assertEqual(docs.broken(root / "a.md", text), ["c.md: no such file", "b.md#other: no such heading"])


class ChangelogTests(unittest.TestCase):
    def test_features_and_fixes_for_users_the_rest_as_a_count(self):
        text = changelog.notes(
            [
                "fix: PipeWire survives a suspend",
                "docs: remove what is obsolete",
                "feat(steam): 72 Hz",
                "feat!: a new layout",
                "not conventional",
            ],
            "1.0-beta",
        )
        self.assertIn("## 1.0-beta", text)
        self.assertIn("### Features\n\n- 72 Hz\n- a new layout (breaking)\n", text)
        self.assertIn("### Fixes\n\n- PipeWire survives a suspend\n", text)
        self.assertIn("2 other commit(s)", text)


class LintScopeTests(unittest.TestCase):
    """ruff and ty find Python files by their suffix: the headset's programs without one are listed by name."""

    @staticmethod
    def listed(text: str, table: str) -> set[str]:
        section = text[text.index(f"[{table}]") :]
        block = section[section.index(" = [") : section.index("\n]")]
        return {line.strip().strip('",') for line in block.splitlines()[1:] if line.strip().startswith('"devices/')}

    @staticmethod
    def python_program(path: Path) -> bool:
        if not path.is_file() or path.suffix:
            return False
        with path.open("rb") as file:
            first = file.readline(128)
        return first.startswith(b"#!") and b"python" in first

    def test_every_python_program_without_a_suffix_is_linted_and_type_checked(self):
        programs = {
            path.relative_to(REPO).as_posix()
            for path in REPO.glob("devices/*/userspace/rootfs/**/*")
            if self.python_program(path)
        }
        self.assertTrue(programs)
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(self.listed(text, "tool.ruff"), programs)
        self.assertEqual(self.listed(text, "tool.ty.src"), programs)

    def test_python_files_are_executable_exactly_when_they_have_a_shebang(self):
        # ruff's EXE001/EXE002 on Linux; Windows checkouts can't see the bit, so Git's index decides here.
        git = shutil.which("git")
        if git is None:
            self.skipTest("no git")
        out = subprocess.run(  # noqa: S603  # git from PATH with fixed arguments
            [git, "ls-files", "-s", "-z"], cwd=REPO, capture_output=True, check=True
        ).stdout
        wrong = []
        for entry in out.split(b"\0"):
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            name = path.decode()
            file = REPO / name
            if not (name.endswith(".py") or self.python_program(file)) or not file.is_file():
                continue
            with file.open("rb") as handle:
                shebang = handle.readline(128).startswith(b"#!")
            if shebang != meta.startswith(b"100755"):
                wrong.append(name)
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
