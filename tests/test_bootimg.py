# SPDX-License-Identifier: Unlicense
"""overpressure.bootimg's ramdisk edits: a boot image's files, and a new image with them changed."""

import gzip
import unittest
from unittest.mock import patch

from overpressure import bootimg, cpio


def image(version: int = 2) -> bootimg.BootImage:
    """Return a boot image whose ramdisk holds /init, with a command line to edit."""
    tree = cpio.Tree()
    tree.add_file("init", b"#!/bin/sh\n", 0o755)
    ramdisk = gzip.compress(tree.to_cpio(), mtime=0)
    if version >= bootimg.HEADER_V3:
        source = bootimg.BootImage(version, 4096, b"kernel", ramdisk, boot_signature=b"signature")
    else:
        source = bootimg.BootImage(version, 4096, b"kernel", ramdisk, dtb=b"dtb")
    return bootimg.parse(source.with_cmdline("console=ttyMSM0 x.run=old x.mode=1 quiet").pack())


class RamdiskTests(unittest.TestCase):
    def test_the_files_and_a_changed_command_line(self):
        source = image()
        tree = bootimg.ramdisk_tree(source)
        self.assertEqual(tree.nodes["init"].data, b"#!/bin/sh\n")
        tree.add_file("etc/run-id", b"new\n")
        data = bootimg.with_ramdisk(source, tree, drop=("x.run=", "x.mode="), add=("x.run=new",))
        result = bootimg.parse(data)
        self.assertEqual(result.full_cmdline, "console=ttyMSM0 quiet x.run=new")
        self.assertEqual((result.kernel, result.dtb), (source.kernel, source.dtb))
        self.assertEqual(result.id, result.compute_id())
        self.assertEqual(bootimg.ramdisk_tree(result).nodes["etc/run-id"].data, b"new\n")
        self.assertEqual(gzip.decompress(result.ramdisk), tree.to_cpio())
        # Reproducible on any host: mtime 0 and the OS byte pinned to Unix (gzip.compress writes the host's).
        self.assertEqual(result.ramdisk[4:8], bytes(4))
        self.assertEqual(result.ramdisk[9], 3)
        self.assertEqual(bootimg.with_ramdisk(source, tree, drop=("x.run=", "x.mode="), add=("x.run=new",)), data)
        unchanged = bootimg.parse(bootimg.with_ramdisk(source, bootimg.ramdisk_tree(source)))
        self.assertEqual(unchanged.full_cmdline, source.full_cmdline)

    def test_a_signed_v4_image_loses_its_signature(self):
        source = image(4)
        self.assertEqual(source.boot_signature, b"signature")
        result = bootimg.parse(bootimg.with_ramdisk(source, bootimg.ramdisk_tree(source), add=("x.extra=1",)))
        self.assertEqual(result.boot_signature, b"")
        self.assertTrue(result.full_cmdline.endswith(" x.extra=1"))

    def test_a_pack_that_changes_the_kernel_fails_validation(self):
        source = image()
        original = bootimg.parse

        def parse(data: bytes) -> bootimg.BootImage:
            parsed = original(data)
            parsed.kernel = b"other"
            return parsed

        with (
            patch.object(bootimg, "parse", side_effect=parse),
            self.assertRaisesRegex(ValueError, "^the new image is broken$"),
        ):
            bootimg.with_ramdisk(source, bootimg.ramdisk_tree(source), failed="the new image is broken")


if __name__ == "__main__":
    unittest.main()
