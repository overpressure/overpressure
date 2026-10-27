import importlib.machinery
import importlib.util
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/bin/pico4-wlfw-client"
_loader = importlib.machinery.SourceFileLoader("pico4_wlfw_client", str(PATH))
_spec = importlib.util.spec_from_loader("pico4_wlfw_client", _loader)
wlfw = importlib.util.module_from_spec(_spec)
_loader.exec_module(wlfw)

CTRL = wlfw.QRTR_PORT_CTRL


class FakeSocket:
    def __init__(self):
        self.sent = []

    def getsockname(self):
        return (1, 0x4000)

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def new_server(node=7, port=1, instance=0x0101):
    return struct.pack("<IIIII", wlfw.QRTR_TYPE_NEW_SERVER, wlfw.WLFW_SERVICE, instance, node, port)


class WlfwClientTests(unittest.TestCase):
    def test_golden_requests(self):
        # QMI header: type, txn, message id, length; then type/length/value TLVs.
        self.assertEqual(
            wlfw.ind_register(1).hex(),
            "00" "0100" "2000" "1700"
            "100100" "01" "110100" "01" "120100" "01" "150400" "4e4f4d44" "170100" "01")
        self.assertEqual(wlfw.cap_request(2).hex(), "00" "0200" "2400" "0000")
        self.assertEqual(wlfw.cal_report(3).hex(), "00" "0300" "2600" "0800" "010100" "00" "110100" "01")
        self.assertEqual(wlfw.lookup_packet(), struct.pack("<IIIII", 10, 0x45, 0, 0, 0))

    def test_parses_responses_and_indications(self):
        packet = wlfw.qmi_message(wlfw.QMI_RESPONSE, 5, 0x20, wlfw.qmi_tlv(0x02, struct.pack("<HH", 1, 0x4A)))
        msg_type, txn, msg_id, tlvs = wlfw.qmi_parse(packet)
        self.assertEqual((msg_type, txn, msg_id), (wlfw.QMI_RESPONSE, 5, 0x20))
        self.assertEqual(wlfw.result(tlvs), (1, 0x4A))
        with self.assertRaises(ValueError):
            wlfw.qmi_parse(b"\x00\x01")

    def test_sequence(self):
        sock, log = FakeSocket(), []
        client = wlfw.Client(sock, log.append)
        client.start()
        self.assertEqual(sock.sent[-1], (wlfw.lookup_packet(), (1, CTRL)))
        # Another service and the end-of-list marker are ignored.
        client.handle(struct.pack("<IIIII", 4, 0x190, 1, 9, 9), (1, CTRL))
        client.handle(struct.pack("<IIIII", 4, 0x45, 0, 0, 0), (1, CTRL))
        self.assertEqual(len(sock.sent), 1)
        # The firmware's service appears: register as cnss-daemon does.
        client.handle(new_server(), (1, CTRL))
        data, addr = sock.sent[-1]
        self.assertEqual(addr, (7, 1))
        self.assertEqual(wlfw.qmi_parse(data)[2], wlfw.MSG_IND_REGISTER)
        # FW memory ready: capability request, then the calibration report.
        client.handle(wlfw.qmi_message(wlfw.QMI_INDICATION, 0, wlfw.IND_FW_MEM_READY, b""), (7, 1))
        self.assertEqual([wlfw.qmi_parse(d)[2] for d, _ in sock.sent[-2:]], [wlfw.MSG_CAP, wlfw.MSG_CAL_REPORT])
        # Packets from anyone else are ignored; a firmware restart registers again.
        client.handle(wlfw.qmi_message(wlfw.QMI_INDICATION, 0, wlfw.IND_FW_MEM_READY, b""), (8, 2))
        self.assertEqual(len(sock.sent), 4)
        client.handle(struct.pack("<IIIII", wlfw.QRTR_TYPE_DEL_SERVER, 0x45, 0x0101, 7, 1), (1, CTRL))
        self.assertIsNone(client.server)
        client.handle(new_server(port=2), (1, CTRL))
        self.assertEqual(sock.sent[-1][1], (7, 2))
        self.assertTrue(any("FW memory ready" in line for line in log))


if __name__ == "__main__":
    unittest.main()
