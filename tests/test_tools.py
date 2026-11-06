# SPDX-License-Identifier: Unlicense
"""overpressure.tools: pinned downloads checked by SHA-256, unpacked into cache/tools, found by name."""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from overpressure import tools


def zip_bytes(files: dict[str, bytes]) -> bytes:
    """Build a zip archive holding these files."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


class Opener:
    def __init__(self, data: bytes):
        self.data, self.urls = data, []

    def __call__(self, url: str, timeout: float | None = None) -> io.BytesIO:  # noqa: ARG002  # urlopen's keyword
        self.urls.append(url)
        return io.BytesIO(self.data)


class ToolsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cache = Path(temp.name)
        patcher = mock.patch.object(tools, "CACHE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tool(self, data: bytes, host: str = "linux-x86_64") -> tools.Tool:
        archive = tools.Archive("https://example.invalid/t.zip", hashlib.sha256(data).hexdigest())
        return tools.Tool("thing", "1.0", {host: archive}, {"thing": "bin/thing"})

    def test_fetch_checks_the_hash_unpacks_and_is_found_by_name(self):
        data = zip_bytes({"bin/thing": b"#!/bin/sh\n", "README": b"x"})
        tool = self.tool(data)
        with (
            mock.patch.object(tools, "host_key", return_value="linux-x86_64"),
            mock.patch.dict(tools.ARCHIVE_TOOLS, {"thing": tool}),
        ):
            opener = Opener(data)
            folder = tools.fetch(tool, log=lambda _l: None, opener=opener)
            self.assertTrue((folder / "bin/thing").is_file())
            self.assertEqual(tools.program("thing"), folder / "bin/thing")
            tools.fetch(tool, log=lambda _l: None, opener=opener)  # already there: no second download
            self.assertEqual(len(opener.urls), 1)

    def test_a_wrong_hash_or_an_escaping_member_keeps_nothing(self):
        data = zip_bytes({"bin/thing": b"x"})
        tool = self.tool(data)
        with mock.patch.object(tools, "host_key", return_value="linux-x86_64"):
            with self.assertRaisesRegex(ValueError, "is not the pinned"):
                tools.fetch(tool, log=lambda _l: None, opener=Opener(data + b"tampered"))
            self.assertFalse(tool.folder.exists())
            evil = zip_bytes({"bin/thing": b"x", "../escape": b"x"})
            with self.assertRaisesRegex(ValueError, "leaves the folder"):
                tools.fetch(self.tool(evil), log=lambda _l: None, opener=Opener(evil))
            self.assertIsNone(tools.program("thing"))

    def test_no_archive_for_this_host(self):
        tool = self.tool(b"x", host="darwin-arm64")
        with mock.patch.object(tools, "host_key", return_value="linux-x86_64"):
            with self.assertRaisesRegex(ValueError, "no thing 1.0 for linux-x86_64"):
                tools.fetch(tool, log=lambda _l: None, opener=Opener(b""))

    def test_archives_fall_back_to_the_system(self):
        self.assertIs(tools.PLATFORM_TOOLS.archive("windows-arm64"), tools.PLATFORM_TOOLS.archives["windows"])
        self.assertIsNone(tools.PLATFORM_TOOLS.archive("linux-aarch64"))  # Google builds none
        self.assertIsNotNone(tools.EDL_NG.archive("linux-aarch64"))

    def test_the_ncm_venv_installs_only_hash_pinned_packages(self):
        venv = self.cache / "ncm-venv-test"
        calls = []

        def run(argv: list[str], *, check: bool) -> None:  # noqa: ARG001  # subprocess.run's keyword
            calls.append(argv)
            python = venv / ("Scripts/python.exe" if tools.sys.platform == "win32" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")

        with mock.patch.object(tools, "NCM_VENV", venv):
            tools.fetch_ncm_venv(log=lambda _l: None, python="py", run=run)
            self.assertIsNotNone(tools.ncm_python())
        self.assertEqual(calls[0], ["py", "-m", "venv", str(venv)])
        self.assertIn("--require-hashes", calls[1])
        requirements = (venv / "requirements.txt").read_text()
        for line in requirements.splitlines():
            if "==" in line:  # every requirement carries its hashes
                self.assertTrue(line.rstrip().endswith("\\"), line)


if __name__ == "__main__":
    unittest.main()
