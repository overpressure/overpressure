# SPDX-License-Identifier: Unlicense
"""The build container's own scripts: the aarch64 handler's registration and handing files back.

They run in the container (python -m overpressure container run -- make test); on another host
they need bash on Linux, and handing back needs root.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from overpressure import linux

LINUX_BASH = sys.platform.startswith("linux") and shutil.which("bash") is not None
#: What binfmt.sh writes to register: the kernel decodes the escapes itself, so they arrive as text.
REGISTER = (
    r":overpressure-aarch64:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00"
    r":\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:{qemu}:POF"
)
MAGIC = "7f454c460201010000000000000000000200b700"


def entry(*, enabled: bool = True, flags: str = "POCF", magic: str = MAGIC) -> str:
    """Return a binfmt_misc entry as the kernel shows it."""
    state = "enabled" if enabled else "disabled"
    return f"{state}\ninterpreter /usr/bin/qemu-aarch64\nflags: {flags}\noffset 0\nmagic {magic}\nmask ff\n"


@unittest.skipUnless(LINUX_BASH and platform.machine() == "x86_64", "bash on x86-64 Linux (the build container)")
class BinfmtTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "binfmt_misc"
        self.dir.mkdir()
        (self.dir / "register").write_text("")
        (self.dir / "status").write_text("enabled\n")
        self.qemu = Path(tmp.name) / "qemu-aarch64-static"
        self.qemu.write_text("")
        self.qemu.chmod(0o755)

    def run_script(self) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, BINFMT_MISC=str(self.dir), BINFMT_QEMU=str(self.qemu))
        return subprocess.run(  # noqa: S603  # bash and the repository's script
            ["bash", str(linux.CONTAINER / "binfmt.sh"), "--report"],  # noqa: S607  # bash from PATH
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_a_handler_with_f_is_left_alone(self):
        (self.dir / "aarch64").write_text(entry())
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "binfmt: aarch64 handler aarch64 (/usr/bin/qemu-aarch64, flags POCF)")
        self.assertEqual((self.dir / "register").read_text(), "")

    def test_without_one_this_images_qemu_is_registered_with_f(self):
        (self.dir / "qemu-aarch64").write_text(entry(flags="OC"))  # without F: a chroot cannot use it
        (self.dir / "arm").write_text(entry(magic="7f454c4601010100000000000000000002002800"))
        (self.dir / "disabled").write_text(entry(enabled=False))
        done = self.run_script()
        # The fake kernel makes no entry of it: the script says so, after writing the exact line.
        self.assertEqual(done.returncode, 1)
        self.assertIn("did not give an enabled aarch64 handler", done.stderr)
        self.assertEqual((self.dir / "register").read_text(), REGISTER.format(qemu=self.qemu))


@unittest.skipUnless(LINUX_BASH and hasattr(os, "geteuid") and os.geteuid() == 0, "root on Linux (the build container)")
class HandBackTests(unittest.TestCase):
    def test_only_roots_files_from_the_run_go_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old"
            old.write_text("before the run")
            changed = root / "changed"
            changed.write_text("before the run")
            time.sleep(0.05)
            marker = root.parent / f"{root.name}.marker"
            marker.write_text("")
            self.addCleanup(marker.unlink)
            time.sleep(0.05)
            (root / "out").mkdir()
            (root / "out" / "new").write_text("the run's")
            (root / "out" / "link").symlink_to("new")
            other = root / "someone-elses"
            other.write_text("")
            os.chown(other, 1234, 1234)
            with changed.open("a") as stream:  # an old file the run wrote to
                stream.write(", and during it")
            subprocess.run(  # noqa: S603  # bash and the repository's script
                ["bash", str(linux.CONTAINER / "hand-back.sh"), str(root), str(marker), "4321", "4322"],  # noqa: S607  # bash from PATH
                check=True,
            )
            owner = {p.name: (p.lstat().st_uid, p.lstat().st_gid) for p in root.rglob("*")}
            self.assertEqual(owner["out"], (4321, 4322))
            self.assertEqual(owner["new"], (4321, 4322))
            self.assertEqual(owner["link"], (4321, 4322))  # the link itself, not what it names
            self.assertEqual(owner["changed"], (4321, 4322))
            self.assertEqual(owner["old"], (0, 0))  # the run left it alone
            self.assertEqual(owner["someone-elses"], (1234, 1234))

    def test_wrong_arguments_change_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(  # noqa: S603  # bash and the repository's script
                ["bash", str(linux.CONTAINER / "hand-back.sh"), tmp, "/nonexistent", "x", "1"],  # noqa: S607  # bash from PATH
                capture_output=True,
                check=False,
            )
            self.assertEqual(done.returncode, 2)


class RecipeTests(unittest.TestCase):
    def test_the_image_has_every_script_the_entrypoint_runs(self):
        dockerfile = (linux.CONTAINER / "Dockerfile").read_text(encoding="utf-8")
        for script, program in (
            ("entrypoint.sh", "overpressure-entrypoint"),
            ("binfmt.sh", linux.BINFMT),
            ("hand-back.sh", "overpressure-hand-back"),
        ):
            self.assertIn(f"COPY {script} /usr/local/bin/{program}", dockerfile)
        entrypoint = (linux.CONTAINER / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("overpressure-binfmt", entrypoint)
        self.assertIn("overpressure-hand-back /work", entrypoint)
        # Pinned: the base by digest, the packages by one snapshot of the archive.
        self.assertRegex(dockerfile, r"FROM ubuntu:24\.04@sha256:[0-9a-f]{64}")
        self.assertRegex(dockerfile, r"ARG SNAPSHOT=\d{8}T\d{6}Z")
        for package in ("clang-20", "lld-20", "qemu-user-static", "e2fsprogs", "device-tree-compiler"):
            self.assertIn(package, dockerfile)


class LinkedFolderTests(unittest.TestCase):
    def test_a_worktrees_linked_private_folders_are_bound_where_they_are_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, other = Path(tmp, "worktree"), Path(tmp, "checkout")
            for folder in (root / "cache", other / "out", other / "assets"):
                folder.mkdir(parents=True)
            links = {str(root / "out"): str(other / "out"), str(root / "assets"): str(other / "assets")}
            real = os.path.realpath

            def realpath(path: os.PathLike[str] | str) -> str:
                return links.get(str(path), real(path))

            with mock.patch.object(linux.os.path, "realpath", realpath):
                mounts = linux.linked_mounts(root)
        # The worktree's own cache stays inside the repository's mount; a link to nothing is left.
        self.assertEqual(mounts, ["-v", f"{other / 'assets'}:/work/assets", "-v", f"{other / 'out'}:/work/out"])


if __name__ == "__main__":
    unittest.main()
