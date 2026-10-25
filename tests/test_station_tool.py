import importlib.machinery
import importlib.util
import struct
import sys
import types
import unittest
from pathlib import Path

# The tool uses fcntl for the power ioctl and the device lock; Windows has no fcntl.
try:
    import fcntl  # noqa: F401
except ImportError:
    sys.modules.setdefault("fcntl", types.ModuleType("fcntl"))

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/bin/pico4-station"
_loader = importlib.machinery.SourceFileLoader("pico4_station_tool", str(PATH))
_spec = importlib.util.spec_from_loader("pico4_station_tool", _loader)
station = importlib.util.module_from_spec(_spec)
_loader.exec_module(station)


def reply(target: int, command: int, body: bytes) -> bytes:
    buf = bytearray(40)
    buf[0], buf[1], buf[2] = 0xBB, target, command
    buf[3:3 + len(body)] = body
    buf[39] = station.crc8(bytes(buf[:39]))
    return bytes(buf)


class FakeStation(station.Station):
    """Answers the controller queries from a script instead of /dev/spidev0.0."""

    def __init__(self, answers):
        self.answers = answers  # (command, payload) -> list of replies, one per request
        self.sent, self.pending, self.log, self.seq, self.announced = [], [], [], 0, False

    def send(self, data: bytes):
        self.sent.append(data)
        if data[2] != station.CMD_ACK:
            queue = self.answers.get((data[2], data[3]), [])
            if queue:
                self.pending.append(queue.pop(0))

    def read(self, timeout: float):
        if not self.pending:
            return None
        buf = self.pending.pop(0)
        self.send(station.frame(station.CMD_ACK, buf[37:39]))
        return buf


class StationToolTests(unittest.TestCase):
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
        self.assertEqual(requests, [1, 2, 2] + list(range(3, 13)))

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

        def send(data):
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


class StationLockTests(unittest.TestCase):
    """One owner of the station: a second runtime must not reset it under the first."""

    @unittest.skipUnless(hasattr(station.fcntl, "flock"), "needs POSIX flock")
    def test_second_owner_is_refused(self):
        import tempfile
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
        self.assertIn("flock(st->fd, LOCK_EX | LOCK_NB)", source[opened:source.index("\n}\n", opened)])
        hmd = (ROOT / "src/monado/pico4_hmd.c").read_text(encoding="utf-8")
        setup = hmd.index("setup_station(struct pico4_hmd *h)")
        self.assertLess(hmd.index("pico4_station_open(&h->station, NULL)", setup),
                        hmd.index("pico4_station_reset(h->station)", setup))


if __name__ == "__main__":
    unittest.main()
