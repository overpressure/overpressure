# SPDX-License-Identifier: Unlicense
"""overpressure.kernelimage: a boot image's command line rewritten by key, archives appended to its ramdisk."""

import gzip
import unittest

from overpressure import cpio, kernelimage


class CommandLineTests(unittest.TestCase):
    def test_a_later_word_replaces_an_earlier_one_with_its_key(self):
        self.assertEqual(
            kernelimage.keyed_words(("panic=5", "ro"), ("panic=10",), ("quiet",)), ("ro", "panic=10", "quiet")
        )

    def test_the_stock_words_go_and_the_new_ones_come_last(self):
        line = kernelimage.command_line(
            "androidboot.hardware=qcom console=ttyMSM0 panic=1 keep=me",
            ("panic=5", "ro"),
            ("androidboot.", "console="),
            511,
        )
        self.assertEqual(line, "keep=me panic=5 ro")

    def test_a_line_longer_than_the_bootloader_passes_is_refused(self):
        with self.assertRaisesRegex(ValueError, "the bootloader passes only 10"):
            kernelimage.command_line("", ("x" * 11,), (), 10)

    def test_a_trace_names_one_kernel_function(self):
        self.assertIn("ftrace_graph_filter=qcom_smem_probe", kernelimage.trace_words("qcom_smem_probe"))
        with self.assertRaises(ValueError):
            kernelimage.trace_words("a b")


class RamdiskTests(unittest.TestCase):
    def test_archives_follow_the_ramdisk_in_order(self):
        tree = cpio.Tree()
        tree.add_file("etc/x/build-info", b"{}\n")
        data = kernelimage.appended(b"base", tree)
        self.assertTrue(data.startswith(b"base"))
        nodes = dict(cpio.read_cpio(gzip.decompress(data[4:])))
        self.assertEqual(nodes["etc/x/build-info"].data, b"{}\n")
        self.assertIn("etc/x", nodes)  # the tree made the parent directories


if __name__ == "__main__":
    unittest.main()
