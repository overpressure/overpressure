# SPDX-License-Identifier: Unlicense
"""The repository's own checks: the private-data scan, the link check, the release tag and the changelog's setup."""

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
release = load("release")


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


class ReleaseTests(unittest.TestCase):
    PYPROJECT = '[build-system]\nrequires = []\n\n[project]\nname = "x"\nversion = "1.0b1"\n\n[tool.x]\nversion = "9"\n'

    def test_tags_in_pythons_spelling(self):
        self.assertEqual(release.python_version("v1.0-beta"), "1.0b1")
        self.assertEqual(release.python_version("v1.0-beta.2"), "1.0b2")
        self.assertEqual(release.python_version("v1.0"), "1.0")
        self.assertEqual(release.python_version("v2.13"), "2.13")
        for tag in ("1.0", "v1", "v1.0.1", "v1.0-rc1", "v1.0-beta.0", "v1.0-beta2", "v1.0-beta."):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.python_version(tag)

    def test_the_tag_must_name_the_projects_version(self):
        self.assertEqual(release.check("v1.0-beta", self.PYPROJECT), "prerelease")
        self.assertEqual(release.check("v1.0", self.PYPROJECT.replace("1.0b1", "1.0")), "release")
        with self.assertRaises(ValueError):
            release.check("v1.0", self.PYPROJECT)
        with self.assertRaises(ValueError):
            release.check("v1.0-beta.2", self.PYPROJECT)
        with self.assertRaises(ValueError):
            release.project_version('[tool.x]\nversion = "1.0"\n')

    def test_the_repositorys_version_has_a_tag_spelling(self):
        version = release.project_version((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        base, _, beta = version.partition("b")
        tag = f"v{base}" + (f"-beta.{beta}" if beta and beta != "1" else "-beta" if beta else "")
        self.assertEqual(release.python_version(tag), version)

    def test_the_release_workflow_checks_the_tag_and_attaches_no_image(self):
        workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/release.py check", workflow)
        self.assertIn("git-cliff@", workflow)
        self.assertIn("git archive", workflow)
        for private_suffix in (".img", ".tar.zst", ".bin", ".elf", ".mbn"):
            self.assertNotIn(private_suffix, workflow)


class LintScopeTests(unittest.TestCase):
    """Python files and the programs without a suffix: executable exactly when they have a shebang."""

    @staticmethod
    def python_program(path: Path) -> bool:
        if not path.is_file() or path.suffix:
            return False
        with path.open("rb") as file:
            first = file.readline(128)
        return first.startswith(b"#!") and b"python" in first

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


class ScriptModeTests(unittest.TestCase):
    #: Where the image builds set each file's mode themselves.
    IMAGE_TREES = ("/initramfs/",)

    def test_every_script_is_executable_in_git(self):
        # A Windows checkout, and Docker's view of it, show every file as executable; Linux and macOS
        # (a clone, or the release zip from git archive) keep git's mode: a script started by its
        # path must be 100755 there.
        git = shutil.which("git")
        if git is None or not (REPO / ".git").exists():
            self.skipTest("not a git checkout")
        listed = subprocess.run(  # noqa: S603  # git with fixed arguments
            [git, "ls-files", "-s"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout
        missing = []
        for row in listed.splitlines():
            mode, _, path = row.partition("\t")
            if not mode.startswith("100644") or any(tree in "/" + path for tree in self.IMAGE_TREES):
                continue
            with (REPO / path).open("rb") as stream:
                if stream.read(2) == b"#!":
                    missing.append(path)
        self.assertEqual(missing, [], "git update-index --chmod=+x these scripts")
