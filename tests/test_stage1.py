"""overpressure.stage1: the kernel arguments, the repacked stock image and the overlay."""
import gzip
import tempfile
import unittest
from pathlib import Path

from overpressure import bootimg, cpio, stage1


def stock_image(cmdline=b"console=ttyMSM0 quiet"):
    return bootimg.parse(bootimg.BootImage(2, 4096, b"kernel", gzip.compress(b"old"), dtb=b"dtb",
                                           cmdline=cmdline).pack())


class Stage1Tests(unittest.TestCase):
    def test_arguments_are_appended_once(self):
        stock = stock_image()
        self.assertEqual(stage1.kernel_cmdline(stock, ["quiet", "a=1", "a=1"]), ["console=ttyMSM0", "quiet", "a=1"])

    def test_the_stock_image_keeps_everything_but_the_ramdisk(self):
        stock = stock_image()
        tree = cpio.Tree()
        tree.add_file("init", b"#!/bin/sh\n", 0o755)
        image = bootimg.parse(stage1.assemble(stock, gzip.compress(tree.to_cpio()), ["a=1"]))
        self.assertEqual((image.kernel, image.dtb), (stock.kernel, stock.dtb))
        self.assertIn("a=1", image.full_cmdline.split())
        with self.assertRaises(AssertionError):  # a ramdisk without /init never ships
            stage1.assemble(stock, gzip.compress(cpio.Tree().to_cpio()), [])

    def test_overlay_modes_and_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/tool").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
            (root / "etc").mkdir()
            (root / "etc/conf").write_bytes(b"a=1\r\n")
            (root / ".gitkeep").write_bytes(b"")
            tree = cpio.Tree()
            stage1.add_overlay(tree, root)
            self.assertEqual(tree.get("usr/bin/tool").data, b"#!/bin/sh\necho hi\n")
            self.assertEqual(tree.get("usr/bin/tool").mode & 0o777, 0o755)
            self.assertEqual(tree.get("etc/conf").mode & 0o777, 0o644)
            self.assertIsNone(tree.get(".gitkeep"))


if __name__ == "__main__":
    unittest.main()
