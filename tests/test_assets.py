# SPDX-License-Identifier: Unlicense
"""overpressure.assets: a checked stock folder, files out of its images, an assets manifest."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from overpressure import assets
from overpressure.ext4 import Ext4
from overpressure.fat import Fat
from tests.images import Ext4Image, FatImage, stock_folder

POSIX = os.name == "posix"


def ext4() -> Ext4:
    """Return an image with a file, a tree with its modes and times, and links."""
    img = Ext4Image()
    img.file("/firmware/a.fw", b"A" * 3000, 0o600, mtime=1_500_000_000)
    img.mkdir("/etc/config", 0o750, mtime=1_400_000_000)
    img.file("/etc/config/one.json", b"{}", 0o640, mtime=1_300_000_000, mtime_ns=1200)
    img.mkdir("/etc/config/sub", 0o700)
    img.file("/etc/config/sub/two.json", b"[]", 0o755)
    img.symlink("/etc/links/link", "../config/one.json")
    img.symlink("/firmware/b.fw", "a.fw")
    return Ext4(io.BytesIO(img.build()))


class StockFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_files_are_checked_against_the_manifest_before_use(self):
        img = FatImage(12)
        img.file("/image/x.bin", b"x")
        folder = stock_folder(self.root / "stock", {"modem.bin": img.build(), "other.bin": b"o"})
        with assets.StockFolder(folder) as stock:
            self.assertEqual(stock.firmware, "1.0-test")
            self.assertEqual(stock.fat("modem.bin").read("/image/x.bin"), b"x")
            (folder / "other.bin").write_bytes(b"changed")
            with self.assertRaisesRegex(assets.AssetError, "other.bin: sha256 does not match the stock manifest"):
                stock.check("other.bin")
            with self.assertRaisesRegex(assets.AssetError, "missing"):
                stock.check("absent.img")

    def test_a_folder_without_a_manifest(self):
        with self.assertRaisesRegex(assets.AssetError, "no stock manifest.json"):
            assets.StockFolder(self.root)

    def test_dump_a_file_and_a_tree_as_debugfs_did(self):
        fs = ext4()
        out = self.root / "out"
        out.mkdir()
        assets.dump(fs, "/firmware/a.fw", out / "renamed.fw")
        self.assertEqual((out / "renamed.fw").read_bytes(), b"A" * 3000)
        assets.dump(fs, "/etc/config", out / "tree")  # rdump: the tree lands as tree/config
        tree = out / "tree" / "config"
        self.assertEqual((tree / "one.json").read_bytes(), b"{}")
        self.assertEqual((tree / "sub" / "two.json").read_bytes(), b"[]")
        self.assertEqual((tree / "one.json").stat().st_mtime_ns, 1_300_000_000 * 10**9 + 1200)  # NTFS keeps 100 ns
        if POSIX:
            self.assertEqual(stat.S_IMODE((tree / "one.json").stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(tree.stat().st_mode), 0o750)
        try:
            assets.dump(fs, "/etc/links", out)
            self.assertEqual((out / "links" / "link").readlink().as_posix(), "../config/one.json")
        except assets.AssetError as exc:  # Windows without the right to create links
            self.assertFalse(POSIX)
            self.assertIn("symbolic link", str(exc))
        with self.assertRaisesRegex(assets.AssetError, "is a link; name its target"):
            assets.dump(fs, "/firmware/b.fw", out / "b.fw")
        with self.assertRaisesRegex(assets.AssetError, "missing /firmware/none"):
            assets.dump(fs, "/firmware/none", out / "none")

    def test_copy_matching_as_mcopy_did(self):
        img = FatImage(16)
        for name in ("venus.mdt", "venus.b00", "adspr.jsn"):
            img.file(f"/image/{name}", name.encode())
        fs = Fat(io.BytesIO(img.build()))
        out = self.root / "fw"
        out.mkdir()
        self.assertEqual(
            assets.copy_matching(fs, "/image", ["venus.*", "adspr.jsn"], out), ["venus.mdt", "venus.b00", "adspr.jsn"]
        )
        self.assertEqual((out / "venus.b00").read_bytes(), b"venus.b00")
        with self.assertRaisesRegex(assets.AssetError, "no /image/cdsp.\\* in the image"):
            assets.copy_matching(fs, "/image", ["cdsp.*"], out)
        with self.assertRaisesRegex(assets.AssetError, "no /none"):
            assets.copy_matching(fs, "/none", ["x"], out)

    def test_manifest_in_path_order_without_manifests(self):
        out = self.root / "kind"
        (out / "config").mkdir(parents=True)
        (out / "config" / "x.json").write_bytes(b"x")
        (out / "config.json").write_bytes(b"c")  # "config/" sorts before "config.json" by parts
        (out / "config" / "manifest.json").write_bytes(b"left out, as before")
        count = assets.write_manifest(out, "1.0-test", {"config/": "vendor /etc"})
        raw = (out / "manifest.json").read_bytes()
        manifest = json.loads(raw)
        self.assertEqual(count, 2)
        self.assertEqual(list(manifest["files"]), ["config/x.json", "config.json"])
        self.assertEqual(manifest["files"]["config.json"], hashlib.sha256(b"c").hexdigest())
        self.assertEqual(
            (manifest["schema"], manifest["firmware"], manifest["sources"]), (2, "1.0-test", {"config/": "vendor /etc"})
        )
        self.assertTrue(raw.endswith(b"}\n"))
        self.assertNotIn(b"\r", raw)
        self.assertIn(b'\n "schema": 2,', raw)  # indent=1, the shell helper's format

    @unittest.skipUnless(POSIX, "Windows files carry no POSIX modes")
    def test_widen_modes(self):
        out = self.root / "w"
        (out / "d").mkdir(parents=True)
        (out / "d" / "f").write_bytes(b"")
        (out / "d" / "x").write_bytes(b"")
        (out / "d" / "f").chmod(0o600)
        (out / "d" / "x").chmod(0o700)
        (out / "d").chmod(0o700)
        assets.widen_modes(out)
        self.assertEqual(stat.S_IMODE((out / "d" / "f").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((out / "d" / "x").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((out / "d").stat().st_mode), 0o755)


if __name__ == "__main__":
    unittest.main()
