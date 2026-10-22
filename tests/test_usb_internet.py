import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4-poc/usb-internet"
_loader = importlib.machinery.SourceFileLoader("usb_internet", str(PATH))
_spec = importlib.util.spec_from_loader("usb_internet", _loader)
usb_internet = importlib.util.module_from_spec(_spec)
_loader.exec_module(usb_internet)


class UsbInternetTests(unittest.TestCase):
    def test_default_and_configured_pc_address(self):
        self.assertEqual(usb_internet.host_ip("console=ttyMSM0 quiet"), "172.16.42.2")
        self.assertEqual(usb_internet.host_ip("pico4poc.ip=10.9.8.1 pico4poc.host_ip=10.9.8.7"), "10.9.8.7")
        with self.assertRaises(ValueError):
            usb_internet.host_ip("pico4poc.host_ip=not-an-address")

    def test_writes_route_and_dns_behind_other_links(self):
        with tempfile.TemporaryDirectory() as temp:
            cmdline, out = Path(temp) / "cmdline", Path(temp) / "run/50-pico4-usb.network.d/60.conf"
            cmdline.write_text("root=/dev/ram0 pico4poc.host_ip=172.16.42.2\n")
            self.assertEqual(usb_internet.main([str(cmdline), str(out)]), 0)
            text = out.read_text()
            self.assertIn("[Network]\nDNS=172.16.42.2\n", text)
            self.assertIn("[Route]\nGateway=172.16.42.2\nMetric=2048\n", text)
            self.assertGreater(usb_internet.METRIC, 600)  # NetworkManager's Wi-Fi default
            cmdline.write_text("pico4poc.host_ip=300.1.1.1")
            self.assertEqual(usb_internet.main([str(cmdline), str(out)]), 1)

    def test_unit_runs_before_networkd(self):
        unit = (ROOT / "userspace/rootfs/usr/lib/systemd/system/pico4-usb-internet.service").read_text()
        self.assertIn("Before=systemd-networkd.service", unit)
        self.assertIn("ExecStart=/usr/libexec/pico4-poc/usb-internet", unit)
        self.assertNotIn("CPUWeight", unit)
        self.assertNotIn("CPUQuota", unit)


if __name__ == "__main__":
    unittest.main()
