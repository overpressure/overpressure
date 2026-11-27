# SPDX-License-Identifier: Unlicense
"""overpressure.payload's HTTP ranges: a connection the server cuts in the middle loses nothing that came."""

import http.client
import unittest
from types import SimpleNamespace
from unittest import mock

from overpressure import payload

DATA = bytes(range(256)) * 8192  # 2 MiB: reads this large skip the block cache


class Cutting:
    """A server that ends every connection after @p cut bytes (PICO's CDN does, after a few megabytes)."""

    def __init__(self, cut: int, empty: int = 0) -> None:
        self.cut, self.empty, self.asked = cut, empty, []

    def __call__(self, start: int, length: int) -> bytes:  # a class attribute that is no function: not bound
        self.asked.append(start)
        if self.empty:
            self.empty -= 1
            nothing = b""
            raise http.client.IncompleteRead(nothing, length)
        if length > self.cut:
            raise http.client.IncompleteRead(DATA[start : start + self.cut], length - self.cut)
        return DATA[start : start + length]


def head(_source: payload.HttpSource, _start: int, _end: int) -> SimpleNamespace:
    """Answer the first request as a server with ranges does."""
    return SimpleNamespace(status=206, headers={"Content-Range": f"bytes 0-0/{len(DATA)}"}, read=lambda: b"")


class RangeTests(unittest.TestCase):
    def read(self, fetch: Cutting, offset: int, length: int) -> bytes:
        with (
            mock.patch.object(payload.HttpSource, "_request", head),
            mock.patch.object(payload.HttpSource, "_fetch_once", fetch),
            mock.patch.object(payload.time, "sleep") as self.sleep,
        ):
            return payload.HttpSource("https://example.invalid/ota.zip", lambda _m: None).read_at(offset, length)

    def test_a_range_cut_again_and_again_arrives_whole_from_where_it_stopped(self):
        fetch = Cutting(cut=700_000)
        self.assertEqual(self.read(fetch, 100, 2_000_000), DATA[100:2_000_100])
        self.assertEqual(fetch.asked, [100, 700_100, 1_400_100])
        self.sleep.assert_not_called()  # each try brought something: no wait

    def test_tries_that_bring_nothing_give_up_after_the_attempts(self):
        with self.assertRaises(http.client.IncompleteRead):
            self.read(Cutting(cut=700_000, empty=payload.ATTEMPTS), 0, 2_000_000)
        self.assertEqual(self.read(Cutting(cut=700_000, empty=payload.ATTEMPTS - 1), 0, 2_000_000), DATA[:2_000_000])


if __name__ == "__main__":
    unittest.main()
