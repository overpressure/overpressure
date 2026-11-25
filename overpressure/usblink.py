# SPDX-License-Identifier: Unlicense
"""A headset's USB network link on a Linux computer: a link that is up without the headset's address, and its fix.

A headset's own Linux systems are a USB gadget whose network function hands this computer one DHCP lease
(Gadget.host_ip). NetworkManager can put a profile of its own on that interface instead: a wired profile with a
fixed address that is bound to no network port takes every new one. The link is then up, but this computer has no
address in the headset's network, and SSH finds no route to it. The fix is a profile for the headset's interface
alone, which takes the lease and leaves this computer's own routes and DNS alone; overpressure changes nothing on
the computer itself, it names the commands (as usbaccess does for udev).
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SYS_NET = Path("/sys/class/net")
#: Runs a program and returns its exit status and output; tests pass a fake.
Run = Callable[[Sequence[str]], "tuple[int, str]"]


@dataclass(frozen=True)
class Gadget:
    """A headset's USB network gadget: its IDs and product string, its network, the lease, the fix's profile."""

    vendor: str  #: hex, as sysfs writes it ("1d6b")
    product: str
    label: str  #: the USB product string, which narrows generic IDs to the headset
    network: str  #: the headset's network ("172.16.42.0/24")
    host_ip: str  #: the one lease it hands this computer
    profile: str  #: NetworkManager's profile the fix adds ("overpressure-CODENAME")

    def in_network(self, address: str) -> bool:
        """Tell whether @p address (with its prefix) lies in the headset's network."""
        return ipaddress.ip_interface(address).ip in ipaddress.ip_network(self.network)


def run_quietly(argv: Sequence[str]) -> tuple[int, str]:
    """Run a query (ip, nmcli) for at most 10 s; return its exit status and output, (127, "") when it cannot run."""
    if not shutil.which(argv[0]):
        return 127, ""
    try:
        done = subprocess.run(  # noqa: S603  # fixed queries, no shell
            list(argv), check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return done.returncode, done.stdout


@dataclass(frozen=True)
class Link:
    """The headset's network interface on this computer: its name, MAC, IPv4 addresses, NetworkManager's profile."""

    name: str
    mac: str
    addresses: tuple[str, ...]
    profile: str  #: the profile NetworkManager put on it; empty when none, or without NetworkManager


def usb_device(interface: Path) -> Path:
    """Return the USB device folder of the network interface folder @p interface (its device is a USB interface)."""
    return (interface / "device").resolve().parent


def interfaces(gadget: Gadget, net: Path = SYS_NET, device: Callable[[Path], Path] = usb_device) -> list[str]:
    """Return the network interfaces of @p gadget, by name (Linux; none elsewhere)."""
    found = []
    try:
        names = sorted(p.name for p in net.iterdir())
    except OSError:
        return []
    for name in names:
        try:
            usb = device(net / name)  # the gadget's USB interface (3-4:1.0) is its device; the IDs are its parent's
            ids = tuple((usb / f).read_text(encoding="utf-8").strip() for f in ("idVendor", "idProduct", "product"))
        except OSError:
            continue
        if ids == (gadget.vendor, gadget.product, gadget.label):
            found.append(name)
    return found


def addresses(name: str, run: Run) -> tuple[str, ...]:
    """Return @p name's IPv4 addresses with their prefix (ip -j), empty when there are none or ip cannot tell."""
    status, output = run(["ip", "-j", "-4", "address", "show", "dev", name])
    if status:
        return ()
    try:
        rows = json.loads(output or "[]")
        return tuple(f"{a['local']}/{a['prefixlen']}" for row in rows for a in row.get("addr_info", ()))
    except (ValueError, KeyError, TypeError, AttributeError):
        return ()


def profile(name: str, run: Run) -> str:
    """Return the NetworkManager profile active on @p name, empty when none or without NetworkManager."""
    status, output = run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", name])
    return output.strip() if not status else ""


def links(
    gadget: Gadget, net: Path = SYS_NET, run: Run = run_quietly, device: Callable[[Path], Path] = usb_device
) -> list[Link]:
    """Return @p gadget's network interfaces on this computer, each with its addresses and profile."""
    found = []
    for name in interfaces(gadget, net, device):
        try:
            mac = (net / name / "address").read_text(encoding="utf-8").strip()
        except OSError:
            mac = ""
        found.append(Link(name, mac, addresses(name, run), profile(name, run)))
    return found


def fix(gadget: Gadget, link: Link, *, nmcli: bool) -> str:
    """Return the commands that give @p link the headset's lease, for a terminal."""
    if not nmcli:
        prefix = ipaddress.ip_network(gadget.network).prefixlen
        return f"sudo ip address flush dev {link.name} && sudo ip address add {gadget.host_ip}/{prefix} dev {link.name}"
    match = f"ethernet.mac-address {link.mac}" if link.mac else f"connection.interface-name {link.name}"
    return (
        f"nmcli connection add type ethernet con-name {gadget.profile} {match} connection.autoconnect-priority 100 "
        "ipv4.method auto ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled\n"
        f"nmcli connection up {gadget.profile}"
    )


def problem(
    gadget: Gadget, found: Sequence[Link] | None = None, *, platform: str = sys.platform, nmcli: bool | None = None
) -> str:
    """Return why the headset's USB link is up without its address, and the fix, in the owner's words; else empty.

    Empty off Linux, without the headset's interface (the headset is not there yet) and once it has the lease.
    The fix's commands follow the explanation after an empty line.
    """
    if platform != "linux":
        return ""
    for link in links(gadget) if found is None else found:
        if any(gadget.in_network(a) for a in link.addresses):
            continue
        has = ", ".join(link.addresses) or "no address"
        why = (
            f'NetworkManager put its profile "{link.profile}" on it'
            if link.profile and link.profile != gadget.profile
            else "it did not take the headset's lease"
        )
        use_nmcli = (shutil.which("nmcli") is not None) if nmcli is None else nmcli
        own = (
            "Give the headset's link a profile of its own, which takes the headset's address and leaves this "
            "computer's routes and DNS alone"
            if use_nmcli
            else "Give the headset's link its address (until the headset is unplugged)"
        )
        return (
            f"The headset's USB link is up on this computer ({link.name}), but has {has} instead of "
            f"{gadget.host_ip}, which the headset hands out: {why}, so this computer has no route to the headset. "
            f"{own}, in a terminal:\n\n{fix(gadget, link, nmcli=use_nmcli)}"
        )
    return ""
