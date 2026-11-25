# SPDX-License-Identifier: Unlicense
"""A headset's USB link on a Linux computer: a link up without the headset's address is named with its fix."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from overpressure import doctor, usblink

if TYPE_CHECKING:
    from collections.abc import Sequence

MAC = "06:aa:bb:cc:dd:ee"
GADGET = usblink.Gadget("1d6b", "0104", "Example VR Linux", "172.16.42.0/24", "172.16.42.2", "overpressure-example")


class Sysfs:
    """A fake /sys/class/net with USB devices behind some interfaces."""

    def __init__(self, root: Path) -> None:
        self.net = root / "net"
        self.net.mkdir()
        self.usb: dict[str, Path] = {}

    def add(self, name: str, ids: tuple[str, str, str] | None, mac: str = MAC) -> None:
        folder = self.net / name
        folder.mkdir()
        (folder / "address").write_text(mac + "\n", encoding="utf-8")
        if ids:
            usb = self.net.parent / f"usb-{name}"
            usb.mkdir()
            for file, value in zip(("idVendor", "idProduct", "product"), ids):
                (usb / file).write_text(value + "\n", encoding="utf-8")
            self.usb[name] = usb

    def device(self, interface: Path) -> Path:
        if interface.name not in self.usb:
            raise FileNotFoundError(interface)
        return self.usb[interface.name]


def runner(addresses: dict[str, list[tuple[str, int]]], profiles: dict[str, str]) -> usblink.Run:
    """Answer ip -j and nmcli as they do on a computer with @p addresses and NetworkManager's @p profiles."""

    def run(argv: Sequence[str]) -> tuple[int, str]:
        name = argv[-1]
        if argv[0] == "ip":
            rows = [{"ifname": name, "addr_info": [{"local": a, "prefixlen": n} for a, n in addresses.get(name, [])]}]
            return 0, json.dumps(rows)
        if argv[0] == "nmcli":
            return (0, profiles[name] + "\n") if name in profiles else (10, "")
        return 127, ""

    return run


class UsbLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sysfs = Sysfs(Path(self.tmp.name))
        self.sysfs.add("enp3s0", None)  # the computer's own port: no USB device
        self.sysfs.add("enp6s0f3u4", (GADGET.vendor, GADGET.product, GADGET.label))
        self.sysfs.add("usb1", (GADGET.vendor, GADGET.product, "another gadget"))

    def links(self, addresses: dict, profiles: dict) -> list[usblink.Link]:
        return usblink.links(GADGET, self.sysfs.net, runner(addresses, profiles), self.sysfs.device)

    def test_only_the_headsets_gadget_is_its_link(self):
        found = self.links({}, {})
        self.assertEqual([link.name for link in found], ["enp6s0f3u4"])
        self.assertEqual(found[0].mac, MAC)

    def test_a_link_with_the_headsets_lease_is_no_problem(self):
        found = self.links({"enp6s0f3u4": [("172.16.42.2", 24)]}, {"enp6s0f3u4": "Wired connection 2"})
        self.assertEqual(usblink.problem(GADGET, found, platform="linux", nmcli=True), "")

    def test_a_profile_with_a_fixed_address_is_named_with_a_profile_of_the_headsets_own(self):
        # A wired profile with a fixed address on the LAN's network, bound to no port, took the headset's interface.
        found = self.links({"enp6s0f3u4": [("192.0.2.2", 24)]}, {"enp6s0f3u4": "Wired connection 1"})
        problem = usblink.problem(GADGET, found, platform="linux", nmcli=True)
        self.assertIn("(enp6s0f3u4), but has 192.0.2.2/24 instead of 172.16.42.2", problem)
        self.assertIn('NetworkManager put its profile "Wired connection 1" on it', problem)
        commands = problem.split("\n\n")[1].splitlines()
        self.assertEqual(
            commands,
            [
                (
                    f"nmcli connection add type ethernet con-name overpressure-example ethernet.mac-address {MAC} "
                    "connection.autoconnect-priority 100 ipv4.method auto ipv4.never-default yes "
                    "ipv4.ignore-auto-dns yes ipv6.method disabled"
                ),
                "nmcli connection up overpressure-example",
            ],
        )

    def test_without_networkmanager_the_address_is_given_by_hand(self):
        problem = usblink.problem(GADGET, self.links({}, {}), platform="linux", nmcli=False)
        self.assertIn("has no address instead of 172.16.42.2", problem)
        self.assertIn("it did not take the headset's lease", problem)
        self.assertTrue(
            problem.endswith(
                "sudo ip address flush dev enp6s0f3u4 && sudo ip address add 172.16.42.2/24 dev enp6s0f3u4"
            )
        )

    def test_no_problem_without_the_headset_or_off_linux(self):
        self.assertEqual(usblink.problem(GADGET, [], platform="linux", nmcli=True), "")
        self.assertEqual(usblink.problem(GADGET, self.links({}, {}), platform="win32", nmcli=True), "")

    def test_an_unreadable_ip_answer_is_no_address(self):
        self.assertEqual(usblink.addresses("x", lambda _argv: (0, "not json")), ())
        self.assertEqual(usblink.addresses("x", lambda _argv: (1, "")), ())


class DoctorTests(unittest.TestCase):
    def link(self, *addresses: str) -> usblink.Link:
        return usblink.Link("enp6s0f3u4", MAC, addresses, "Wired connection 1")

    def test_the_doctor_names_the_fix(self):
        host = doctor.Host(platform="linux", which=lambda name: f"/usr/bin/{name}")
        (check,) = doctor.link_check(host, GADGET, found=[self.link("192.0.2.2/24")])
        self.assertEqual(check.status, doctor.MISSING)
        self.assertIn('"Wired connection 1"', check.detail)
        self.assertTrue(check.fix.startswith("nmcli connection add type ethernet con-name overpressure-example"))
        self.assertIn(" && nmcli connection up overpressure-example", check.fix)

    def test_the_doctor_passes_a_link_with_the_lease_and_skips_without_one(self):
        host = doctor.Host(platform="linux", which=lambda name: f"/usr/bin/{name}")
        (check,) = doctor.link_check(host, GADGET, found=[self.link("172.16.42.2/24")])
        self.assertEqual(check.status, doctor.OK)
        self.assertEqual(doctor.link_check(host, GADGET, found=[]), [])
        self.assertEqual(doctor.link_check(doctor.Host(platform="win32"), GADGET, found=[self.link()]), [])


if __name__ == "__main__":
    unittest.main()
