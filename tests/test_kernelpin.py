# SPDX-License-Identifier: Unlicense
"""overpressure.kernelpin: a headset's pinned release of overpressure/linux, its download, its build, the contracts."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from overpressure import REPO, kernelpin
from overpressure.linux import IMAGE_OVERRIDE


def pin_of(files: dict[str, bytes]) -> kernelpin.Release:
    """Return a release whose files are @p files."""
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    return kernelpin.Release("overpressure/linux", "example/7.2-9", "a" * 40, "builder@sha256:" + "b" * 64, digests)


class Opener:
    """urlopen for a fixed set of URLs; it records what was asked."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str, timeout: float = 0) -> io.BytesIO:  # noqa: ARG002  # urlopen's signature
        self.asked.append(url)
        return io.BytesIO(self.pages[url])


def quiet(_line: str) -> None:
    """Take a log line and drop it."""


class PinTests(unittest.TestCase):
    def test_urls_quote_the_tags_slash(self):
        pin = pin_of({})
        self.assertEqual(
            pin.url("Image.gz"), "https://github.com/overpressure/linux/releases/download/example%2F7.2-9/Image.gz"
        )
        self.assertEqual(
            pin.source_url("drivers/x.c"),
            f"https://raw.githubusercontent.com/overpressure/linux/{'a' * 40}/drivers/x.c",
        )

    def test_a_kernel_pin_and_a_packages_pin_read_alike(self):
        with tempfile.TemporaryDirectory() as tmp:
            kernel, packages = Path(tmp) / "release.json", Path(tmp) / "packages.json"
            kernel.write_text(
                json.dumps({"repository": "r", "tag": "t", "commit": "c", "image": "i", "files": {"a": "d"}})
            )
            packages.write_text(json.dumps({"repository": "p", "tag": "2026.11.27", "commit": "e"}))
            self.assertEqual(kernelpin.release(kernel), kernelpin.Release("r", "t", "c", "i", {"a": "d"}))
            self.assertEqual(kernelpin.release(packages), kernelpin.Release("p", "2026.11.27", "e", "", {}))


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "kernel"
        self.files = {"Image.gz": b"kernel", "version": b"7.2.0-example\n"}
        self.pin = pin_of(self.files)

    def test_each_file_is_downloaded_checked_and_kept(self):
        opener = Opener({self.pin.url(n): d for n, d in self.files.items()})
        self.assertEqual(kernelpin.fetch(self.out, self.pin, log=quiet, opener=opener), ["Image.gz", "version"])
        self.assertEqual((self.out / "Image.gz").read_bytes(), b"kernel")
        self.assertTrue(kernelpin.current(self.out, self.pin))
        # A second run downloads nothing: the files are there with their hashes.
        opener.asked.clear()
        self.assertEqual(kernelpin.fetch(self.out, self.pin, log=quiet, opener=opener), [])
        self.assertEqual(opener.asked, [])

    def test_a_file_with_another_hash_is_refused_and_not_written(self):
        opener = Opener({self.pin.url("Image.gz"): b"tampered", self.pin.url("version"): self.files["version"]})
        with self.assertRaisesRegex(ValueError, "is not the pinned"):
            kernelpin.fetch(self.out, self.pin, log=quiet, opener=opener)
        self.assertFalse((self.out / "Image.gz").exists())
        self.assertEqual(kernelpin.mismatches(self.out, self.pin), ["Image.gz: missing", "version: missing"])

    def test_check_names_what_differs(self):
        self.out.mkdir()
        (self.out / "Image.gz").write_bytes(b"other")
        (self.out / "version").write_bytes(self.files["version"])
        found = kernelpin.mismatches(self.out, self.pin)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("Image.gz: sha256 "))
        self.assertFalse(kernelpin.current(self.out, self.pin))

    def test_a_build_of_the_pinned_commit_differs_only_in_its_image(self):
        # Each build signs modules with its own key, which Image.gz carries: the other files match the pin.
        self.out.mkdir()
        (self.out / "Image.gz").write_bytes(b"its own signing key")
        (self.out / "version").write_bytes(self.files["version"])
        self.assertEqual(kernelpin.mismatches(self.out, self.pin, built=True), [])
        (self.out / "version").write_bytes(b"another release")
        self.assertTrue(kernelpin.mismatches(self.out, self.pin, built=True)[0].startswith("version: sha256 "))


class BuildTests(unittest.TestCase):
    def test_the_step_runs_in_the_releases_build_image(self):
        calls = []

        def call(argv: list[str], *, env: dict[str, str], cwd: Path) -> int:
            calls.append((argv, env, cwd))
            return 0

        pin = pin_of({})
        self.assertEqual(kernelpin.build("devices/x/scripts/build-kernel-step.sh", "/build/kernel", pin, call), 0)
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
                "devices/x/scripts/build-kernel-step.sh",
                "/build/kernel",
            ],
        )
        self.assertEqual((env[IMAGE_OVERRIDE], cwd), (pin.image, REPO))


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/install.sh").write_text("install a.bin /usr/lib/firmware/example/a.bin\n")
        self.contract = kernelpin.Contract(
            "the file", (("drivers/x.c", r'#define FW "([^"]+)"'),), (("scripts", r"usr/lib/firmware/(\S+)"),)
        )

    def broken(self, kernel_source: str, packages: str = "") -> list[str]:
        return kernelpin.check((self.contract,), lambda _path: kernel_source, self.root, lambda _path: packages)

    def test_a_name_both_sides_use_holds(self):
        self.assertEqual(self.broken('#define FW "example/a.bin"\n'), [])

    def test_a_renamed_side_names_both(self):
        broken = self.broken('#define FW "example/b.bin"\n')
        self.assertEqual(broken, ["the file: this repository uses example/a.bin; the kernel has example/b.bin"])

    def test_a_side_that_finds_nothing_fails(self):
        self.assertIn("the kernel's side found nothing", self.broken("")[0])
        (self.root / "scripts/install.sh").write_text("")
        self.assertIn("this repository's side found nothing", self.broken('#define FW "example/a.bin"\n')[0])

    def test_a_side_in_overpressure_packages_is_read_from_its_pin(self):
        contract = kernelpin.Contract(
            "the control",
            (("drivers/x.c", r'"([\w%]+)"'),),
            ((f"{kernelpin.IN_PACKAGES}example/cam.c", r"ctrl (\w+)"),),
            here_value=kernelpin.numbered,
        )
        read: list[str] = []

        def packages(path: str) -> str:
            read.append(path)
            return "ctrl ring0\n"

        self.assertEqual(kernelpin.check((contract,), lambda _p: '"ring%d"', self.root, packages), [])
        self.assertEqual(read, ["example/cam.c"])

    def test_the_kernels_files_are_read_once_per_commit(self):
        pin = pin_of({})
        opener = Opener({pin.source_url("drivers/x.c"): b"source"})
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                text = kernelpin.source_file("drivers/x.c", pin, cache=Path(tmp), opener=opener)
            self.assertEqual(text, "source")
            self.assertEqual(len(opener.asked), 1)
            self.assertTrue((Path(tmp) / pin.commit / "drivers/x.c").is_file())


if __name__ == "__main__":
    unittest.main()
