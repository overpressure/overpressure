# SPDX-License-Identifier: Unlicense
"""overpressure.ext4: files, directories and links out of synthetic ext4 images."""

from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from overpressure import REPO
from overpressure.ext4 import Ext4, Ext4Error
from tests.images import Ext4Image


def sample(block_size: int = 1024, *, wide: bool = False, inode_size: int = 256) -> tuple[Ext4Image, bytes]:
    """Return an image with every layout the reader handles, and its bytes."""
    img = Ext4Image(block_size, wide=wide, inode_size=inode_size)
    bs = block_size
    img.mkdir("/etc", 0o750, uid=1000, gid=2000)
    img.file("/etc/small.txt", b"hello\n", 0o640, uid=1000, gid=1001, mtime=1_600_000_000, mtime_ns=123456789)
    img.file("/etc/tree.bin", bytes(range(256)) * (3 * bs // 256), layout="tree")
    sparse = bytearray(b"A" * bs + bytes(2 * bs) + b"B" * bs + bytes(bs // 2))
    img.file("/etc/sparse.bin", bytes(sparse), holes=frozenset({1, 2, 4}))
    img.file("/etc/uninit.bin", b"C" * bs + bytes(bs), uninit=frozenset({1}))
    # 12 direct blocks, one single indirect (bs/4 blocks) and part of a double indirect.
    mapped = b"".join(struct.pack("<I", i) * (bs // 4) for i in range(12 + bs // 4 + 3))
    img.file("/bin/mapped", mapped, 0o755, layout="map")
    img.file("/bin/holey", b"D" * bs + bytes(bs) + b"E" * 10, layout="map", holes=frozenset({1}))
    img.symlink("/bin/fast", "mapped")
    img.symlink("/bin/slow", "/" + "long-target/" * 8)
    for i in range(6):  # a directory big enough for an index block and two leaf blocks
        img.file(f"/many/file-{i:02d}-{'x' * 100}", str(i).encode())
    img.file("/many/empty", b"")
    return img, img.build()


class Ext4Tests(unittest.TestCase):
    def check(self, fs: Ext4) -> None:
        bs = fs.block_size
        self.assertEqual(fs.read("/etc/small.txt"), b"hello\n")
        small = fs.lookup("/etc/small.txt")
        self.assertEqual((small.permissions, small.uid, small.gid, small.size), (0o640, 1000, 1001, 6))
        self.assertTrue(small.is_file)
        etc = fs.lookup("etc")
        self.assertTrue(etc.is_dir)
        self.assertEqual((etc.permissions, etc.uid, etc.gid), (0o750, 1000, 2000))
        self.assertEqual(fs.read("/etc/tree.bin"), bytes(range(256)) * (3 * bs // 256))
        self.assertEqual(fs.read("/etc/sparse.bin"), b"A" * bs + bytes(2 * bs) + b"B" * bs + bytes(bs // 2))
        self.assertEqual(fs.read("/etc/uninit.bin"), b"C" * bs + bytes(bs))
        mapped = b"".join(struct.pack("<I", i) * (bs // 4) for i in range(12 + bs // 4 + 3))
        self.assertEqual(fs.read("/bin/mapped"), mapped)
        self.assertEqual(fs.read("/bin/holey"), b"D" * bs + bytes(bs) + b"E" * 10)
        self.assertEqual(fs.readlink("/bin/fast"), "mapped")
        self.assertEqual(fs.readlink("/bin/slow"), "/" + "long-target/" * 8)
        self.assertTrue(fs.lookup("/bin/fast").is_symlink)
        self.assertEqual(fs.listdir("/"), ["etc", "bin", "many"])
        self.assertEqual(len(fs.listdir("/many")), 7)
        self.assertEqual(fs.read("/many/empty"), b"")
        self.assertEqual(fs.read("/many/file-05-" + "x" * 100), b"5")

    def test_every_layout_with_1k_and_4k_blocks(self):
        for bs in (1024, 4096):
            with self.subTest(block_size=bs):
                self.check(Ext4(io.BytesIO(sample(bs)[1])))

    def test_64bit_descriptors_and_old_inodes(self):
        self.check(Ext4(io.BytesIO(sample(wide=True)[1])))
        fs = Ext4(io.BytesIO(sample(inode_size=128)[1]))
        self.check(fs)
        self.assertEqual(fs.lookup("/etc/small.txt").mtime_ns, 0)  # no extra fields

    def test_times(self):
        fs = Ext4(io.BytesIO(sample()[1]))
        small = fs.lookup("/etc/small.txt")
        self.assertEqual((small.mtime, small.mtime_ns), (1_600_000_000, 123456789))

    def test_inodes_in_the_second_group(self):
        img, data = sample()
        fs = Ext4(io.BytesIO(data))
        numbers = [inode.number for _path, inode in fs.walk("/")]
        self.assertGreater(max(numbers), img.inodes_per_group)  # the walk crossed into group 1

    def test_walk_lists_a_directory_before_its_contents(self):
        fs = Ext4(io.BytesIO(sample()[1]))
        paths = [path for path, _inode in fs.walk("/etc")]
        self.assertEqual(paths, ["/etc", "/etc/small.txt", "/etc/tree.bin", "/etc/sparse.bin", "/etc/uninit.bin"])
        self.assertIn("/bin/fast", [p for p, _ in fs.walk()])

    def test_chunks_are_bounded(self):
        fs = Ext4(io.BytesIO(sample()[1]))
        inode = fs.lookup("/bin/mapped")
        chunks = list(fs.chunks(inode, 1000))
        self.assertLessEqual(max(map(len, chunks)), 1000)
        self.assertEqual(b"".join(chunks), fs.read("/bin/mapped"))

    def test_from_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fs.img"
            path.write_bytes(sample()[1])
            with Ext4.open(path) as fs:
                self.assertEqual(fs.read("/etc/small.txt"), b"hello\n")

    def test_the_command_line_writes_a_regular_file_to_stdout(self):
        bs = 1024
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            image.write_bytes(sample(bs)[1])

            def cat(*words: str) -> subprocess.CompletedProcess[bytes]:
                # As the Linux build scripts run it (scripts/stock-lib.sh): the package from the repository.
                env = dict(os.environ, PYTHONPATH=str(REPO))
                return subprocess.run(  # noqa: S603  # this Python and fixed words
                    [sys.executable, "-m", "overpressure.ext4", *words], capture_output=True, env=env, check=False
                )

            done = cat("cat", str(image), "/etc/sparse.bin")
            self.assertEqual(
                (done.returncode, done.stdout), (0, b"A" * bs + bytes(2 * bs) + b"B" * bs + bytes(bs // 2))
            )
            for path in ("/bin/fast", "/etc"):  # a link is not followed, as debugfs's dump does not
                done = cat("cat", str(image), path)
                self.assertEqual((done.returncode, done.stdout), (1, b""))
                self.assertIn(b"not a regular file", done.stderr)
            self.assertIn(b"no /etc/none", cat("cat", str(image), "/etc/none").stderr)
            self.assertEqual(cat("ls", str(image)).returncode, 2)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash") and shutil.which("python3"), "Linux")
    def test_stock_lib_dumps_through_this_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "fs.img"
            image.write_bytes(sample()[1])
            script = (
                f'. "{REPO}/scripts/stock-lib.sh"; dump "$1" /etc/small.txt "$2/out"; ! dump "$1" /bin/fast "$2/link"'
            )
            subprocess.run(["bash", "-c", script, "x", str(image), tmp], check=True, capture_output=True)  # noqa: S603, S607  # bash, fixed words
            self.assertEqual((Path(tmp) / "out").read_bytes(), b"hello\n")
            self.assertFalse((Path(tmp) / "link").exists() or (Path(tmp) / "link.part").exists())

    def test_missing_names_and_wrong_kinds(self):
        fs = Ext4(io.BytesIO(sample()[1]))
        self.assertFalse(fs.exists("/etc/none"))
        self.assertTrue(fs.exists("/etc"))
        with self.assertRaisesRegex(Ext4Error, "no /etc/none"):
            fs.lookup("/etc/none")
        with self.assertRaisesRegex(Ext4Error, "not a directory"):
            fs.lookup("/etc/small.txt/x")
        with self.assertRaisesRegex(Ext4Error, "not a regular file"):
            fs.read("/etc")
        with self.assertRaisesRegex(Ext4Error, "not a symbolic link"):
            fs.readlink("/etc/small.txt")

    def test_not_ext4_and_refused_features(self):
        with self.assertRaisesRegex(Ext4Error, "not an ext2/3/4 image"):
            Ext4(io.BytesIO(bytes(4096)))
        data = bytearray(sample()[1])
        incompat = struct.unpack_from("<I", data, 1024 + 0x60)[0]
        struct.pack_into("<I", data, 1024 + 0x60, incompat | 0x8000)
        with self.assertRaisesRegex(Ext4Error, "inline_data"):
            Ext4(io.BytesIO(bytes(data)))


if __name__ == "__main__":
    unittest.main()
