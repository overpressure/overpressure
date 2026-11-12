# SPDX-License-Identifier: Unlicense
"""devices.phoenix.tooling.kernel: the pinned release of overpressure/linux, its download and the contracts."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devices.phoenix.tooling import kernel
from overpressure import REPO


def pin_of(files: dict[str, bytes]) -> kernel.Release:
    """Return a release whose files are @p files."""
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    return kernel.Release("overpressure/linux", "phoenix/7.2-9", "a" * 40, "builder@sha256:" + "b" * 64, digests)


class Opener:
    """urlopen for a fixed set of URLs; it records what was asked."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: float = 0) -> io.BytesIO:  # noqa: ARG002  # urlopen's signature
        self.asked.append(url)
        return io.BytesIO(self.pages[url])


class PinTests(unittest.TestCase):
    def test_the_repositorys_pin_names_a_tag_its_commit_the_image_and_the_files(self):
        pin = kernel.release()
        self.assertEqual(pin.repository, "overpressure/linux")
        self.assertRegex(pin.tag, r"^phoenix/\d+\.\d+-\d+$")
        self.assertRegex(pin.commit, r"^[0-9a-f]{40}$")
        self.assertRegex(pin.image, r"^ghcr\.io/overpressure/builder:[\d.]+@sha256:[0-9a-f]{64}$")
        self.assertEqual(set(pin.files), {"Image.gz", "sm8250-pico4-phoenix.dtb", "config", "version"})
        for digest in pin.files.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_urls_quote_the_tags_slash(self):
        pin = pin_of({})
        self.assertEqual(
            pin.url("Image.gz"), "https://github.com/overpressure/linux/releases/download/phoenix%2F7.2-9/Image.gz"
        )
        self.assertEqual(
            pin.source_url("drivers/x.c"),
            f"https://raw.githubusercontent.com/overpressure/linux/{'a' * 40}/drivers/x.c",
        )


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "kernel"
        self.files = {"Image.gz": b"kernel", "version": b"7.2.0-pico4\n"}
        self.pin = pin_of(self.files)

    def test_each_file_is_downloaded_checked_and_kept(self):
        opener = Opener({self.pin.url(n): d for n, d in self.files.items()})
        self.assertEqual(kernel.fetch(self.out, self.pin, log=lambda _l: None, opener=opener), ["Image.gz", "version"])
        self.assertEqual((self.out / "Image.gz").read_bytes(), b"kernel")
        self.assertTrue(kernel.current(self.out, self.pin))
        # A second run downloads nothing: the files are there with their hashes.
        opener.asked.clear()
        self.assertEqual(kernel.fetch(self.out, self.pin, log=lambda _l: None, opener=opener), [])
        self.assertEqual(opener.asked, [])

    def test_a_file_with_another_hash_is_refused_and_not_written(self):
        opener = Opener({self.pin.url("Image.gz"): b"tampered", self.pin.url("version"): self.files["version"]})
        with self.assertRaisesRegex(ValueError, "is not the pinned"):
            kernel.fetch(self.out, self.pin, log=lambda _l: None, opener=opener)
        self.assertFalse((self.out / "Image.gz").exists())
        self.assertEqual(kernel.mismatches(self.out, self.pin), ["Image.gz: missing", "version: missing"])

    def test_check_names_what_differs(self):
        self.out.mkdir()
        (self.out / "Image.gz").write_bytes(b"other")
        (self.out / "version").write_bytes(self.files["version"])
        found = kernel.mismatches(self.out, self.pin)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("Image.gz: sha256 "))
        self.assertFalse(kernel.current(self.out, self.pin))


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/install.sh").write_text("install a.bin /usr/lib/firmware/pico4/a.bin\n")
        self.contract = kernel.Contract(
            "the file",
            (("drivers/x.c", r'#define FW "([^"]+)"'),),
            (("scripts", r"usr/lib/firmware/(\S+)"),),
        )

    def broken(self, kernel_source: str) -> list[str]:
        return kernel.check((self.contract,), read=lambda _path: kernel_source, root=self.root)

    def test_a_name_both_sides_use_holds(self):
        self.assertEqual(self.broken('#define FW "pico4/a.bin"\n'), [])

    def test_a_renamed_side_names_both(self):
        broken = self.broken('#define FW "pico4/b.bin"\n')
        self.assertEqual(broken, ["the file: this repository uses pico4/a.bin; the kernel has pico4/b.bin"])

    def test_a_side_that_finds_nothing_fails(self):
        self.assertIn("the kernel's side found nothing", self.broken("")[0])
        (self.root / "scripts/install.sh").write_text("")
        self.assertIn("this repository's side found nothing", self.broken('#define FW "pico4/a.bin"\n')[0])

    def test_every_contract_finds_this_repositorys_side(self):
        # The kernel's side needs the pinned commit's files (CI fetches them); this side is here.
        for contract in kernel.CONTRACTS:
            for path, pattern in contract.here:
                values = kernel._values(kernel._here_text(path, REPO), pattern)  # noqa: SLF001  # the checker's own reader
                self.assertTrue(values, f"{contract.name}: nothing in {path}")

    def test_the_kernels_files_are_read_once_per_commit(self):
        pin = pin_of({})
        opener = Opener({pin.source_url("drivers/x.c"): b"source"})
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                text = kernel.kernel_file("drivers/x.c", pin, cache=Path(tmp), opener=opener)
            self.assertEqual(text, "source")
            self.assertEqual(len(opener.asked), 1)
            self.assertTrue((Path(tmp) / pin.commit / "drivers/x.c").is_file())


class CommandTests(unittest.TestCase):
    def test_build_runs_the_step_in_the_releases_build_image(self):
        calls = []

        def call(argv: list[str], *, env: dict[str, str], cwd: Path) -> int:
            calls.append((argv, env, cwd))
            return 0

        pin = pin_of({})
        self.assertEqual(kernel.build(kernel.WORK, pin, call), 0)
        argv, env, cwd = calls[0]
        self.assertEqual(
            argv[1:],
            [
                "-m",
                "overpressure",
                "container",
                "run",
                "--",
                "bash",
                "devices/phoenix/scripts/build-kernel-step.sh",
                kernel.WORK,
            ],
        )
        self.assertEqual(env["OVERPRESSURE_BUILDER"], pin.image)
        self.assertEqual(cwd, REPO)

    def test_check_reports_a_folder_against_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(kernel.main(["check", tmp]), 1)
        self.assertEqual(len(re.findall(r"^error: .*: missing$", err.getvalue(), re.MULTILINE)), 4)

    def test_the_pin_is_json_with_lf(self):
        raw = kernel.RELEASE.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(json.loads(raw)["tag"], kernel.release().tag)


if __name__ == "__main__":
    unittest.main()
