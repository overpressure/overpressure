import struct
import unittest

from pico4 import avb


def stock(total=64 * 4096, original=20000, vbmeta=b"AVB0" + b"\x11" * 252):
    """A made-up partition: image, vbmeta right behind it, footer at the end."""
    data = bytearray(total)
    data[:original] = b"\x22" * original
    data[original:original + len(vbmeta)] = vbmeta
    data[-64:] = avb.FOOTER.pack(avb.FOOTER_MAGIC, 1, 0, original, original, len(vbmeta))
    return bytes(data)


class AvbTests(unittest.TestCase):
    def test_vbmeta_moves_behind_a_larger_image_unchanged(self):
        part = stock()
        image = b"\x33" * 100001
        out = avb.with_stock_vbmeta(part, image)
        self.assertEqual(len(out), len(part))
        self.assertEqual(out[:len(image)], image)
        original, offset, size = avb.footer(out)
        self.assertEqual((original, offset, size), (len(image), 102400, 256))
        self.assertEqual(out[offset:offset + size], part[20000:20256])
        # Version and reserved bytes as stock; nothing else between image, vbmeta and footer.
        self.assertEqual(out[-64:-52], part[-64:-52])
        self.assertEqual(out[len(image):offset].count(0), offset - len(image))

    def test_no_room_or_no_footer_is_refused(self):
        with self.assertRaises(ValueError):
            avb.with_stock_vbmeta(stock(), b"x" * (64 * 4096 - 200))
        broken = bytearray(stock())
        broken[-64:-60] = b"XXXX"
        with self.assertRaises(ValueError):
            avb.footer(bytes(broken))
        self.assertEqual(struct.calcsize(">4sIIQQQ28x"), 64)


if __name__ == "__main__":
    unittest.main()
