# SPDX-License-Identifier: Unlicense
"""The repository's own checks: the private-data scan, the link check, the release tag and the changelog's setup."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from overpressure import version

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

    def test_a_finding_outside_the_consoles_code_page_is_printed_and_fails(self):
        console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        self.addCleanup(console.close)
        mangled = "packages/a" + chr(0xF03A) + "b.bin"
        with (
            mock.patch.object(private, "local_secrets", list),
            mock.patch.object(private, "tracked", return_value=[(mangled, b"x")]),
            mock.patch.object(private, "findings", lambda path, _data, _secrets: [f"{path}: binary"]),
            mock.patch.object(sys, "stdout", console),
        ):
            self.assertEqual(private.main([]), 1)
            console.flush()
        self.assertIn(b"packages/a" + bytes([92]) + b"uf03ab.bin: binary", console.buffer.getvalue())

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
            self.assertEqual(
                docs.broken(root / "a.md", text, root), ["c.md: no such file", "b.md#other: no such heading"]
            )
            (root / "inner").mkdir()
            self.assertEqual(
                docs.broken(root / "inner" / "a.md", "[up](../b.md)", root / "inner"),
                ["../b.md: outside the repository"],
            )


class HeadsetReadmeTests(unittest.TestCase):
    ISSUE = "([#7](https://github.com/overpressure/example/issues/7))"

    def setUp(self):
        self.template = docs.TEMPLATE.read_text(encoding="utf-8")
        # Filled in as a headset would: every status works, every other placeholder some text.
        status = re.sub(r"^(\| [^|]+ \| )\{\{\}\}", r"\1✅", self.template, flags=re.MULTILINE)
        self.readme = docs.PLACEHOLDER.sub("x", docs.COMMENT.sub("", status))

    def problems(self, readme: str) -> list[str]:
        return docs.readme_problems(readme, self.template)

    def test_a_readme_filled_in_from_the_template_passes(self):
        self.assertEqual(self.problems(self.readme), [])

    def test_the_headsets_own_rows_come_after_the_templates(self):
        own = self.readme.replace("| Cooling | ✅ | x |\n", "| Cooling | ✅ | x |\n| Fan noise | ✅ | x |\n")
        self.assertEqual(self.problems(own), [])
        early = self.readme.replace("| Both displays |", "| Fan noise | ✅ | x |\n| Both displays |")
        self.assertEqual(
            self.problems(early), ["Display and comfort: row 2 is not 'Both displays' (the template's rows come first)"]
        )

    def test_a_missing_or_moved_section_is_found(self):
        gone = self.readme.replace("### Sound\n", "")
        self.assertIn("its sections are", self.problems(gone)[0])
        moved = self.readme.replace("## What you need", "## Needed")
        self.assertIn("the template's are", self.problems(moved)[0])

    def test_a_row_needs_one_status_and_a_broken_one_its_issue(self):
        expected = [
            f"Controllers: Vibration: the status is 'yes', not one of {' '.join(docs.STATUS)}",
            "Sound: Microphone: doesn't work, and links no issue",
        ]
        broken = self.readme.replace("| Vibration | ✅ | x |", "| Vibration | yes | x |")
        broken = broken.replace("| Microphone | ✅ | x |", "| Microphone | ❌ | x |")
        self.assertEqual(self.problems(broken), expected)
        linked = self.readme.replace("| Microphone | ✅ | x |", f"| Microphone | ❌ | Not yet {self.ISSUE} |")
        self.assertEqual(self.problems(linked), [])

    def test_a_left_placeholder_is_found(self):
        left = self.readme.replace("| Wi-Fi | ✅ | x |", "| Wi-Fi | ✅ | {{notes}} |")
        self.assertEqual(self.problems(left), ["a placeholder is left: {{notes}}"])


class ReleaseTests(unittest.TestCase):
    PYPROJECT = (
        '[build-system]\nrequires = []\n\n[project]\nname = "x"\nversion = "2026.12.0b0"\n\n[tool.x]\nversion = "9"\n'
    )
    HEADSET = '{\n "codename": "x",\n "version": "2026.12.0b0",\n "api": "1.5",\n "commit": "$Format:%H$"\n}\n'
    TODAY = dt.date(2026, 12, 3)

    def test_the_tag_must_be_a_calendar_release_and_the_projects_version(self):
        self.assertEqual(release.check("2026.12.0b0", self.PYPROJECT), "prerelease")
        self.assertEqual(release.check("2026.12.0", self.PYPROJECT.replace("2026.12.0b0", "2026.12.0")), "release")
        for tag in ("2026.12.0", "2026.12.0b1", "v1.0-beta", "2026.12.0.dev0", "2026.12.0-beta", "2026.012.0", "main"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.check(tag, self.PYPROJECT)
        with self.assertRaises(ValueError):
            release.project_version('[tool.x]\nversion = "1.0"\n')

    def test_a_headsets_tag_names_headset_json(self):
        self.assertEqual(release.check("2026.12.0b0", self.HEADSET, headset=True), "prerelease")
        with self.assertRaises(ValueError):
            release.check("2026.12.0b1", self.HEADSET, headset=True)

    def test_releases_in_version_order_a_beta_before_its_stable_the_old_scheme_first(self):
        tags = ["2026.12.0", "v1.0-beta.2", "2026.12.0b0", "v1.0-beta", "phoenix/7.2-1", "2026.11.0", "2026.12.0.dev0"]
        self.assertEqual(
            sorted((t for t in tags if release.release_version(t)), key=release.key),
            ["v1.0-beta", "v1.0-beta.2", "2026.11.0", "2026.12.0b0", "2026.12.0"],
        )

    def test_bump_names_the_next_version(self):
        tags = ["v1.0-beta", "v1.0-beta.5", "2026.11.0b0", "2026.11.0", "2026.12.0b0"]
        for kind, have, today, new in (
            ("beta", "1.0b6.dev0", self.TODAY, "2026.12.1b0"),  # 2026.12.0 is taken by a beta series already
            ("beta", "2026.12.0b1.dev0", self.TODAY, "2026.12.0b1"),  # a running series: its next beta
            ("stable", "2026.12.0b1.dev0", dt.date(2027, 1, 2), "2026.12.0"),  # keeps its name past the month
            ("beta", "2026.11.1.dev0", dt.date(2027, 1, 2), "2027.1.0b0"),  # a new series: the month's first patch
            ("stable", "2026.11.1.dev0", dt.date(2026, 11, 30), "2026.11.1"),
            ("next", "2026.12.0b0", self.TODAY, "2026.12.0b1.dev0"),
            ("next", "2026.12.0", self.TODAY, "2026.12.1.dev0"),
        ):
            with self.subTest(kind=kind, have=have):
                self.assertEqual(release.bump(kind, have, tags, today), new)
        for kind, have in (("next", "2026.12.0.dev0"), ("next", "1.0b5"), ("rc", "2026.12.0"), ("beta", "main")):
            with self.subTest(kind=kind, have=have), self.assertRaises(ValueError):
                release.bump(kind, have, tags, self.TODAY)

    def test_a_headset_release_names_the_oldest_overpressure_that_runs_its_interface(self):
        self.assertEqual(release.supported("x = 1\nSUPPORTED: dict[int, int] = {1: 5, 2: 0}\n"), {1: 5, 2: 0})
        self.assertEqual(release.supported("no table"), {})
        headset_py = {
            "v1.0-beta.4": "SUPPORTED: dict[int, int] = {1: 4}",
            "2026.12.0b0": "SUPPORTED: dict[int, int] = {1: 5}",
            "2026.12.0": "SUPPORTED: dict[int, int] = {1: 5}",
        }
        tags = [*headset_py, "phoenix/7.2-6"]
        self.assertEqual(release.needs("1.5", tags, headset_py.__getitem__), "2026.12.0b0")
        self.assertEqual(release.needs("1.2", tags, headset_py.__getitem__), "v1.0-beta.4")
        self.assertEqual(release.needs("1.6", tags, headset_py.__getitem__), "")
        with self.assertRaises(ValueError):
            release.needs("one", tags, headset_py.__getitem__)

    def test_bump_changes_only_the_version_in_its_file(self):
        self.assertEqual(
            release.write_version(self.PYPROJECT, "2026.12.0"), self.PYPROJECT.replace("2026.12.0b0", "2026.12.0")
        )
        self.assertEqual(
            release.write_version(self.HEADSET, "2026.12.0b1.dev0", headset=True),
            self.HEADSET.replace("2026.12.0b0", "2026.12.0b1.dev0"),
        )

    def test_the_changelog_starts_at_the_first_release(self):
        tags = ["v1.0-beta", "v1.0-beta.2", "2026.12.0b0", "2026.12.0b1", "2026.12.0", "2027.1.0b0", "other"]
        self.assertEqual(release.ranges("v1.0-beta", tags), (None, "v1.0-beta"))  # its own words only
        self.assertEqual(release.ranges("v1.0-beta.2", tags), ("v1.0-beta..v1.0-beta.2", "v1.0-beta"))
        self.assertEqual(release.ranges("2026.12.0b0", tags), ("v1.0-beta.2..2026.12.0b0", "v1.0-beta"))
        # A stable release: everything since the stable one before it, here none: the first release.
        self.assertEqual(release.ranges("2026.12.0", tags), ("v1.0-beta..2026.12.0", "v1.0-beta"))
        self.assertEqual(release.ranges("2027.1.0b0", tags), ("2026.12.0..2027.1.0b0", "v1.0-beta"))
        self.assertRegex("2026.12.0b3", release.BETA_TAGS)
        self.assertRegex("v1.0-beta.2", release.BETA_TAGS)
        self.assertNotRegex("2026.12.0", release.BETA_TAGS)

    def test_the_repositorys_version_is_a_calendar_development_version(self):
        found = version.parse(release.project_version((REPO / "pyproject.toml").read_text(encoding="utf-8")))
        self.assertIsNotNone(found)
        assert found is not None  # for the type checker
        self.assertTrue(found.calendar)
        self.assertEqual(found.channel, version.PREVIEW)  # main between releases: the next one's development version

    def test_the_release_workflow_checks_the_tag_and_attaches_no_image(self):
        workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/release.py check", workflow)
        self.assertIn("scripts/release.py notes", workflow)
        self.assertIn("check-private.py --files", workflow)
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
