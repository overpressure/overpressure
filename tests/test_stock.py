"""pico4.stock: partition images from a full OTA's block images, as AOSP's updater writes them."""
import io
import unittest

from pico4 import stock

B = stock.BLOCK


class TransferListTests(unittest.TestCase):
    def test_rangeset(self):
        self.assertEqual(stock.parse_rangeset("4,0,2,5,6"), [(0, 2), (5, 6)])
        with self.assertRaises(ValueError):
            stock.parse_rangeset("3,0,2,5")

    def test_new_blocks_in_order_zero_and_erase_left_empty(self):
        data = b"".join(bytes([i + 1]) * B for i in range(3))
        transfer = "4\n8\n0\n0\nerase 2,0,8\nnew 2,5,7\nzero 2,0,2\nnew 2,2,3\n"
        out = io.BytesIO()
        size = stock.apply_transfer_list(transfer, io.BytesIO(data), out)
        image = out.getvalue()
        self.assertEqual(size, 8 * B)
        self.assertEqual(len(image), 8 * B)
        self.assertEqual(image[5 * B:6 * B], b"\x01" * B)
        self.assertEqual(image[6 * B:7 * B], b"\x02" * B)
        self.assertEqual(image[2 * B:3 * B], b"\x03" * B)
        self.assertEqual(image[0:2 * B], bytes(2 * B))
        self.assertEqual(image[7 * B:], bytes(B))

    def test_short_data_and_unknown_commands_refused(self):
        with self.assertRaises(ValueError):
            stock.apply_transfer_list("4\n4\n0\n0\nnew 2,0,2\n", io.BytesIO(bytes(B)), io.BytesIO())
        with self.assertRaises(ValueError):
            stock.apply_transfer_list("4\n4\n0\n0\nmove 2,0,1\n", io.BytesIO(), io.BytesIO())

    def test_members_cover_every_output(self):
        members = set(stock.members_needed())
        self.assertIn("firmware-update/NON-HLOS.bin", members)
        self.assertIn("firmware-update/dspso.bin", members)
        self.assertIn("system.new.dat.br", members)
        self.assertIn("vendor.transfer.list", members)


if __name__ == "__main__":
    unittest.main()
