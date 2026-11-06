# SPDX-License-Identifier: Unlicense
"""pico4-station: the station's SPI frames, the controller queries, mainline spidev transfers and the device lock."""

from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# The tool uses fcntl for the power ioctl and the device lock; Windows has no fcntl.
try:
    import fcntl  # noqa: F401  # only whether it exists
except ImportError:
    sys.modules.setdefault("fcntl", types.ModuleType("fcntl"))

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/bin/pico4-station"
_loader = importlib.machinery.SourceFileLoader("pico4_station_tool", str(PATH))
_spec = importlib.util.spec_from_loader("pico4_station_tool", _loader)
assert _spec is not None
station = importlib.util.module_from_spec(_spec)
_loader.exec_module(station)


def reply(target: int, command: int, body: bytes) -> bytes:
    """Build a frame the station sends: header, target, command, body, CRC."""
    buf = bytearray(40)
    buf[0], buf[1], buf[2] = 0xBB, target, command
    buf[3 : 3 + len(body)] = body
    buf[39] = station.crc8(bytes(buf[:39]))
    return bytes(buf)


class FakeStation(station.Station):
    """Answers the controller queries from a script instead of /dev/spidev0.0."""

    def __init__(self, answers: dict[tuple[int, int], list[bytes]]):
        self.answers = answers  # (command, payload) -> list of replies, one per request
        self.sent, self.pending, self.log, self.seq, self.announced = [], [], [], 0, False

    def send(self, data: bytes) -> None:
        self.sent.append(data)
        if data[2] != station.CMD_ACK:
            queue = self.answers.get((data[2], data[3]), [])
            if queue:
                self.pending.append(queue.pop(0))

    def read(self, timeout: float) -> bytes | None:  # noqa: ARG002  # Station.read's signature
        if not self.pending:
            return None
        buf = self.pending.pop(0)
        self.send(station.frame(station.CMD_ACK, buf[37:39]))
        return buf


class StationToolTests(unittest.TestCase):
    def test_slow_advertising_frame_as_the_stock_sleep(self):
        buf = station.frame(station.CMD_SLOW_ADV, bytes([0, 0]))
        self.assertEqual(buf[:5], bytes([0xAA, 0x01, 0x0D, 0, 0]))
        for payload in (b"", bytes([4, 0]), bytes([0, 1]), bytes([0, 0, 0])):
            with self.assertRaises(ValueError):
                station.frame(station.CMD_SLOW_ADV, payload)

    def test_slow_advertising_reads_the_status_and_tries_twice(self):
        confirmed = reply(1, station.CMD_SLOW_ADV, bytes([0, 1]))
        self.assertTrue(FakeStation({(station.CMD_SLOW_ADV, 0): [confirmed]}).controllers_slow_adv())
        refused = reply(1, station.CMD_SLOW_ADV, bytes([0, 0]))
        self.assertFalse(FakeStation({(station.CMD_SLOW_ADV, 0): [refused]}).controllers_slow_adv())
        silent = FakeStation({})
        self.assertFalse(silent.controllers_slow_adv())
        self.assertEqual(sum(1 for s in silent.sent if s[2] == station.CMD_SLOW_ADV), 2)

    def test_controller_queries_exactly_as_pico_os(self):
        info = station.controller_query_frame("left", station.CMD_CONTROLLER_INFO)
        self.assertEqual(info[:4], bytes([0xAA, 0x02, 0x03, 0x00]))
        self.assertEqual(info[3:39], bytes(36))
        self.assertEqual(info[39], station.crc8(info[:39]))
        calib = station.controller_query_frame("right", station.CMD_CONTROLLER_CALIBRATION, bytes([12]))
        self.assertEqual(calib[:4], bytes([0xAA, 0x03, 0x02, 12]))
        self.assertEqual(calib[4:39], bytes(35))

    def test_anything_else_to_a_controller_is_refused(self):
        refused = [
            ("left", station.CMD_CONTROLLER_INFO, b"\x01"),
            ("left", station.CMD_CONTROLLER_CALIBRATION, b""),
            ("left", station.CMD_CONTROLLER_CALIBRATION, b"\x00"),
            ("left", station.CMD_CONTROLLER_CALIBRATION, b"\x0d"),
            ("left", station.CMD_CONTROLLER_CALIBRATION, b"\x01\x00"),
            ("right", 0x04, b""),
            ("station", station.CMD_CONTROLLER_INFO, b""),
        ]
        for hand, command, payload in refused:
            with self.assertRaises(ValueError, msg=(hand, command, payload)):
                station.controller_query_frame(hand, command, payload)

    def test_info_reply_fields(self):
        body = bytearray(36)
        body[0:6] = b"3.26.0"
        body[6] = 0x21  # byte 9: connect type 2, status 1
        body[7:9] = bytes([0x01, 0x02])
        body[10:27] = b"TESTSERIAL0000001"
        body[27:33] = bytes([1, 2, 3, 4, 5, 6])
        fake = FakeStation({(station.CMD_CONTROLLER_INFO, 0): [reply(0x03, 0x03, bytes(body))]})
        info = fake.controller_info("right")
        self.assertEqual(info["status"], 1)
        self.assertEqual(info["connect_type"], 2)
        self.assertEqual(info["software"], "3.26.0")
        self.assertEqual(info["hardware"], "0102")
        self.assertEqual(info["serial"], "TESTSERIAL0000001")
        self.assertEqual(info["address"], "01:02:03:04:05:06")
        self.assertEqual(fake.sent[0][:3], bytes([0xAA, 0x03, 0x03]))
        self.assertEqual(fake.sent[1][2], station.CMD_ACK)  # replies are acknowledged

    def test_calibration_retries_a_chunk_that_is_not_ready(self):
        answers = {}
        for index in range(1, 13):
            data = struct.pack("<8f", *[index + k / 10 for k in range(8)])
            answers[(station.CMD_CONTROLLER_CALIBRATION, index)] = [reply(0x02, 0x02, bytes([index]) + data)]
        answers[(station.CMD_CONTROLLER_CALIBRATION, 2)].insert(0, reply(0x02, 0x02, bytes([0xFF])))
        fake = FakeStation(answers)
        chunks = fake.controller_calibration("left")
        self.assertEqual(sorted(chunks), list(range(1, 13)))
        record = station.calibration_floats(chunks)
        self.assertEqual(len(record["imu"]), 24)
        self.assertEqual(len(record["leds"]), 64)
        self.assertEqual(len(record["imu_ir"]), 8)
        self.assertAlmostEqual(record["imu"][8], 2.0, places=5)
        requests = [f[3] for f in fake.sent if f[2] == station.CMD_CONTROLLER_CALIBRATION]
        self.assertEqual(requests, [1, 2, 2, *list(range(3, 13))])

    def test_late_replies_for_an_earlier_chunk_are_skipped(self):
        answers = {}
        for index in range(1, 13):
            data = bytes([index]) * 32
            answers[(station.CMD_CONTROLLER_CALIBRATION, index)] = [reply(0x02, 0x02, bytes([index]) + data)]
        # The request for chunk 4 is answered first by a late reply for chunk 3.
        late = reply(0x02, 0x02, bytes([3]) + bytes([3]) * 32)
        answers[(station.CMD_CONTROLLER_CALIBRATION, 4)].insert(0, late)
        fake = FakeStation(answers)
        real_send = fake.send

        def send(data: bytes) -> None:
            real_send(data)
            if data[2] == station.CMD_CONTROLLER_CALIBRATION and data[3] == 4:
                fake.pending.extend(answers[(station.CMD_CONTROLLER_CALIBRATION, 4)])

        fake.send = send
        chunks = fake.controller_calibration("left")
        self.assertEqual(chunks[4], bytes([4]) * 32)
        requests = [f[3] for f in fake.sent if f[2] == station.CMD_CONTROLLER_CALIBRATION]
        self.assertEqual(requests, list(range(1, 13)))  # waited instead of asking again

    def test_calibration_error_stops(self):
        fake = FakeStation({(station.CMD_CONTROLLER_CALIBRATION, 1): [reply(0x02, 0x02, bytes([0xEE]))]})
        with self.assertRaises(OSError):
            fake.controller_calibration("left")


class PlainSpidevTests(unittest.TestCase):
    """A mainline kernel: full-duplex transfers instead of the vendor spidev's reads and writes."""

    def station(self, replies: list[bytes | None]):
        st = station.Station.__new__(station.Station)
        st.spidev, st.queue, st.sent, st.seq, st.log, st.announced = True, [], [], 0, [], False

        def transfer(data: bytes) -> None:
            # Each transfer brings the station's next frame back, or nothing (None).
            st.sent.append(data)
            if replies:
                frame = replies.pop(0)
                if frame is not None:
                    st.queue.append(frame)

        st._transfer = transfer  # noqa: SLF001  # the fake replaces the private transfer
        return st

    def test_reads_send_zeros_until_a_frame_comes_back(self):
        frame = reply(1, station.CMD_VERSION, bytes([2, 0x60]))
        st = self.station([None, None, frame])
        self.assertEqual(st._next(1.0), frame)  # noqa: SLF001  # tests the private read
        self.assertEqual(st.sent, [bytes(40)] * 3)

    def test_commands_go_out_as_transfers(self):
        st = self.station([])
        st.send(station.frame(station.CMD_VERSION, b"", 1))
        self.assertEqual(len(st.sent), 1)
        self.assertEqual(st.sent[0][0], station.HEADER_SEND)

    def test_a_repeated_frame_is_no_new_one(self):
        # The station returns its last frame on every transfer until it has another.
        st = station.Station.__new__(station.Station)
        st.fd, st.spidev, st.queue, st.last = 3, True, [], None
        st._tx, st._rx = ctypes.create_string_buffer(40), ctypes.create_string_buffer(40)  # noqa: SLF001  # the transfer's buffers
        frames = [
            reply(1, station.CMD_VERSION, bytes([1])),
            reply(1, station.CMD_VERSION, bytes([1])),
            reply(1, station.CMD_VERSION, bytes([2])),
        ]

        def ioctl(*_: object) -> None:
            ctypes.memmove(st._rx, frames.pop(0), 40)  # noqa: SLF001  # the transfer's receive buffer

        with mock.patch.object(station.fcntl, "ioctl", ioctl, create=True):
            for _ in range(3):
                st._transfer(bytes(40))  # noqa: SLF001  # tests the private transfer
        self.assertEqual([f[3] for f in st.queue], [1, 2])

    def test_nothing_within_the_timeout(self):
        st = self.station([])
        self.assertIsNone(st._next(0.01))  # noqa: SLF001  # tests the private read


class StationLockTests(unittest.TestCase):
    """One owner of the station: a second runtime must not reset it under the first."""

    @unittest.skipUnless(hasattr(station.fcntl, "flock"), "needs POSIX flock")
    def test_second_owner_is_refused(self):
        with tempfile.NamedTemporaryFile() as f:
            first = station.Station(f.name)
            try:
                with self.assertRaisesRegex(OSError, "in use"):
                    station.Station(f.name)
            finally:
                first.close()
            station.Station(f.name).close()  # free again once the owner closed it

    def test_driver_locks_before_any_reset(self):
        source = (ROOT / "src/monado/pico4_station.c").read_text(encoding="utf-8")
        opened = source.index("pico4_station_open(struct pico4_station **out")
        self.assertIn("flock(st->fd, LOCK_EX | LOCK_NB)", source[opened : source.index("\n}\n", opened)])
        hmd = (ROOT / "src/monado/pico4_hmd.c").read_text(encoding="utf-8")
        setup = hmd.index("setup_station(struct pico4_hmd *h)")
        self.assertLess(
            hmd.index("pico4_station_open(&h->station, NULL)", setup),
            hmd.index("pico4_station_reset(h->station)", setup),
        )


class StationRateTests(unittest.TestCase):
    """The cameras' rate: 50 Hz as stock, or PICO OS's 60 Hz for a faster room view."""

    def test_rate_reaches_the_announcement_and_the_exposure_commands(self):
        source = (ROOT / "src/monado/pico4_station.c").read_text(encoding="utf-8")
        self.assertIn("st->camera_hz = 50;", source)
        self.assertIn("{st->camera_hz == 60 ? FPS_60 : FPS_50, DEVICE_TYPE, 1}", source)
        para = source[source.index("\nsend_camera_para(") :]
        para = para[: para.index("\n}\n")]
        self.assertIn("p[13] = (uint8_t)(PICO4_STATION_PARA_SYNC_OTHER & 0xff);", para)
        header = (ROOT / "src/monado/pico4_station.h").read_text(encoding="utf-8")
        self.assertIn("#define PICO4_STATION_PARA_SYNC_OTHER 160", header)
        hmd = (ROOT / "src/monado/pico4_hmd.c").read_text(encoding="utf-8")
        setup = hmd.index("setup_station(struct pico4_hmd *h)")
        self.assertLess(
            hmd.index("pico4_station_set_camera_hz(h->station", setup),
            hmd.index("pico4_station_reset(h->station)", setup),
        )
        self.assertIn('DEBUG_GET_ONCE_NUM_OPTION(pico4_camera_hz, "PICO4_CAMERA_HZ", 50)', hmd)

    def test_auto_exposure_takes_the_measured_rate(self):
        slamd = (ROOT / "src/stockslam/pico4-slamd.c").read_text(encoding="utf-8")
        self.assertIn("ts - c->last_set_ts < 36700000 ? 60 : AE_FPS", slamd)
        self.assertIn("&unused, __atomic_load_n(&c->fps, __ATOMIC_RELAXED),", slamd)


if __name__ == "__main__":
    unittest.main()
