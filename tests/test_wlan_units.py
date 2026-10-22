import os
import socket
import tempfile
import unittest
from pathlib import Path

from tests.test_wlfw_client import wlfw

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "userspace/rootfs/usr/lib/systemd/system"


def unit(name: str) -> str:
    return (UNITS / name).read_text(encoding="utf-8")


class WlanUnitTests(unittest.TestCase):
    def test_bring_up_waits_for_the_client_and_the_name_service(self):
        client, wlan = unit("pico4-wlfw-client.service"), unit("pico4-wlan.service")
        self.assertIn("Type=notify", client)
        self.assertIn("After=pico4-qrtr-ns.service", client)
        self.assertIn("Requires=pico4-wlfw-client.service", wlan)
        self.assertIn("After=pico4-wlfw-client.service", wlan)
        for text in (client, wlan):
            self.assertIn("ConditionPathExists=/usr/lib/pico4-wlan/qca_cld3_qca6390.ko", text)
            self.assertNotIn("CPUWeight", text)
            self.assertNotIn("CPUQuota", text)

    def test_script_sets_the_restart_level_before_starting_the_chip(self):
        script = (ROOT / "userspace/rootfs/usr/libexec/pico4-poc/wlan").read_text(encoding="utf-8")
        start = script[script.index("start)"):script.index("stop)")]
        self.assertLess(start.index("RELATED"), start.index("fs_ready"))
        self.assertLess(start.index("fs_ready"), start.index("insmod"))
        self.assertNotIn("rmmod", script)

    def test_interfaces_keep_their_names(self):
        link = (ROOT / "userspace/rootfs/usr/lib/systemd/network/10-pico4-wlan.link").read_text(encoding="utf-8")
        self.assertIn("OriginalName=wlan0 p2p0", link)
        self.assertIn("NamePolicy=\n", link)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "needs Unix sockets")
    def test_notify_ready(self):
        wlfw.notify_ready({})  # no NOTIFY_SOCKET: nothing to do
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "notify")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
                server.bind(path)
                wlfw.notify_ready({"NOTIFY_SOCKET": path})
                self.assertEqual(server.recv(64), b"READY=1")


if __name__ == "__main__":
    unittest.main()
