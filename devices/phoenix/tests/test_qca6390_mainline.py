# SPDX-License-Identifier: Unlicense
"""The QCA6390 on a mainline kernel: ath11k (Wi-Fi) and hci_qca with PICO OS's firmware, board data, addresses."""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
UNITS = ROOTFS / "usr/lib/systemd/system"
_loader = importlib.machinery.SourceFileLoader("bt_address", str(ROOTFS / "usr/libexec/pico4/bt-address"))
_spec = importlib.util.spec_from_loader("bt_address", _loader)
assert _spec is not None
bt_address = importlib.util.module_from_spec(_spec)
_loader.exec_module(bt_address)


def text(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


class WifiTests(unittest.TestCase):
    def test_ath11k_finds_the_stock_files_under_its_names(self):
        build = text(ROOT / "scripts/build-userspace.sh")
        for link in (
            'ln -sfn ../../../qca6390/amss20.bin "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0/amss.bin"',
            'ln -sfn ../../../qca6390/m3.bin "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0/m3.bin"',
            'ln -sfn ../../../qca6390/bdwlanq.elf "$T/usr/lib/firmware/ath11k/QCA6390/hw2.0/board.bin"',
        ):
            self.assertIn(link, build)

    def test_ath11k_gets_the_address_pico_assigned(self):
        unit = text(UNITS / "pico4-wlan-address.service")
        self.assertIn("Before=NetworkManager.service", unit)
        conf = text(ROOTFS / "etc/NetworkManager/conf.d/50-pico4-wifi-address.conf")
        self.assertIn("wifi.cloned-mac-address=preserve", conf)

    @unittest.skipIf(shutil.which("sh") is None, "needs sh")
    def test_address_script_reads_the_first_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "wlan_mac.bin").write_bytes(b"Intf0MacAddress=00AA11BB22CC\nIntf1MacAddress=00AA11BB22CD\n")
            bin_dir = Path(tmp, "bin")
            bin_dir.mkdir()
            log = Path(tmp, "ip.log")
            Path(bin_dir, "ip").write_bytes(f'#!/bin/sh\necho "$@" >> {log.as_posix()}\n'.encode())
            (bin_dir / "ip").chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}")
            script = ROOTFS / "usr/libexec/pico4/wlan-address"
            sh = shutil.which("sh")
            assert sh is not None  # the test is skipped without sh
            r = subprocess.run(  # noqa: S603  # the repository's script under sh, no shell string
                [sh, script.as_posix(), Path(tmp, "wlan_mac.bin").as_posix()],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(
                log.read_text().splitlines(),
                ["link set dev wlan0 down", "link set dev wlan0 address 00:aa:11:bb:22:cc"],
            )

    def test_profiles_lose_the_chips_changing_address(self):
        # The chip's own address differs at each boot; a profile naming it never matches again.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "wlan_mac.bin").write_text("Intf0MacAddress=00AA11BB22CC\n")
            bin_dir = Path(tmp, "bin")
            bin_dir.mkdir()
            Path(bin_dir, "ip").write_bytes(b"#!/bin/sh\nexit 0\n")
            (bin_dir / "ip").chmod(0o755)
            profiles = Path(tmp, "profiles")
            profiles.mkdir()
            profile = profiles / "home.nmconnection"
            profile.write_bytes(
                b"[wifi]\nband=no6\nmac-address=00:03:7F:12:C5:E0\nssid=home\n[wifi-security]\npsk=secret\n"
            )
            env = dict(os.environ, PATH=f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}")
            script = ROOTFS / "usr/libexec/pico4/wlan-address"
            sh = shutil.which("sh")
            assert sh is not None  # the test is skipped without sh
            r = subprocess.run(  # noqa: S603  # the repository's script under sh, no shell string
                [
                    sh,
                    script.as_posix(),
                    Path(tmp, "wlan_mac.bin").as_posix(),
                    "wlan0",
                    profiles.as_posix(),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(profile.read_bytes(), b"[wifi]\nband=no6\nssid=home\n[wifi-security]\npsk=secret\n")

    def test_network_manager_retries_a_failed_connection(self):
        # With iwd's autoconnect, one DHCP timeout leaves the headset offline for good.
        conf = text(ROOTFS / "etc/NetworkManager/conf.d/50-pico4-wifi-autoconnect.conf")
        self.assertRegex(conf, r"(?m)^\[device\]\nwifi\.iwd\.autoconnect=false$")

    def test_kernel_builds_ath11k_in(self):
        config = text(ROOT / "kernel/pico4.config")
        for option in ("CONFIG_ATH11K=y", "CONFIG_ATH11K_PCI=y", "CONFIG_PCI_PWRCTRL_PWRSEQ=y"):
            self.assertIn(option + "\n", config)

    def test_tree_powers_the_chip_from_the_stock_supplies(self):
        tree = text(ROOT / "kernel/sm8250-pico4-phoenix.dts")
        pmu = tree[tree.index("qca6390-pmu {") : tree.index("regulators {", tree.index("qca6390-pmu {"))]
        for supply in (
            "vddaon-supply = <&vreg_s6a_0p95>",
            "vddrfa1p3-supply = <&vreg_s8c_1p3>",
            "vddrfa1p9-supply = <&vreg_s5a_1p9>",
            "vddio-supply = <&vreg_s4a_1p8>",
            "wlan-enable-gpios = <&tlmm 20 GPIO_ACTIVE_HIGH>",
            "bt-enable-gpios = <&tlmm 21 GPIO_ACTIVE_HIGH>",
        ):
            self.assertIn(supply, pmu)
        self.assertIn('compatible = "pci17cb,1101"', tree)
        self.assertIn('compatible = "qcom,qca6390-bt"', tree)


class BluetoothTests(unittest.TestCase):
    def test_firmware_is_in_the_image_and_the_controller_probes_again(self):
        # The image carries the files from PICO's public package; the headset's partition is not read.
        build = text(ROOT / "scripts/build-userspace.sh")
        self.assertIn('install -m 0644 "$wlan/firmware/qca/$f" "$T/usr/lib/firmware/qca/"', build)
        self.assertIn("for f in htbtfw20.tlv htnv20.bin; do", build)
        self.assertNotIn("bt_firmware", build)
        script = text(ROOTFS / "usr/libexec/pico4/bt-firmware")
        self.assertNotIn("mount -", script)
        self.assertNotIn("by-name/bluetooth", script)
        # hci_qca set the controller up before the root was mounted: it probes again, once the files are there.
        self.assertLess(script.index("/usr/lib/firmware/qca/"), script.index('echo "$name" > "$driver/unbind"'))
        self.assertLess(script.index('"$driver/unbind"'), script.index('echo "$name" > "$driver/bind"'))
        unit = text(UNITS / "pico4-bt-firmware.service")
        self.assertIn("Before=bluetooth.service pico4-bt-address.service", unit)
        self.assertIn("ConditionPathExists=/usr/lib/firmware/qca/htbtfw20.tlv", unit)

    def test_address_is_set_before_bluetoothd(self):
        unit = text(UNITS / "pico4-bt-address.service")
        self.assertIn("Before=bluetooth.service", unit)
        self.assertIn("After=pico4-persist.service", unit)
        self.assertIn("ExecStart=/usr/libexec/pico4/bt-address /mnt/vendor/persist/pico_bt_mac 0", unit)
        self.assertIn("TimeoutStartSec=", unit)

    def test_address_command_is_the_management_interfaces(self):
        # Set Public Address (0x0039) for index 0, six address bytes least significant first.
        self.assertEqual(
            bt_address.command(0, bt_address.address("00:AA:11:BB:22:CC\n")),
            bytes.fromhex("3900 0000 0600 cc22bb11aa00"),
        )
        with self.assertRaises(ValueError):
            bt_address.address("00:AA:11")

    def test_address_reply_is_matched_by_index_and_opcode(self):
        complete = bytes.fromhex("0100 0000 0300 3900 00")
        self.assertEqual(bt_address.reply_status(complete, 0), 0)
        self.assertEqual(bt_address.reply_status(bytes.fromhex("0200 0000 0300 3900 0b"), 0), 11)
        self.assertIsNone(bt_address.reply_status(complete, 1))
        self.assertIsNone(bt_address.reply_status(bytes.fromhex("0100 0000 0300 0500 00"), 0))
        self.assertIsNone(bt_address.reply_status(bytes.fromhex("0400 0000 0000"), 0))

    def test_address_waits_until_the_controller_is_offered(self):
        # Right after the device appears its setup still runs: Invalid Index, then success.
        replies = [bytes.fromhex("0200 0000 0300 3900 11"), bytes.fromhex("0100 0000 0300 3900 00")]
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            mac = Path(tmp, "pico_bt_mac")
            mac.write_text("00:AA:11:BB:22:CC\n")
            with (
                mock.patch.object(bt_address, "control_socket", return_value=7),
                mock.patch.object(bt_address.os, "write", side_effect=lambda _fd, b: sent.append(b)),
                mock.patch.object(bt_address.os, "read", side_effect=lambda *_: replies.pop(0)),
                mock.patch.object(bt_address.select, "select", return_value=([7], [], [])),
                mock.patch.object(bt_address.time, "sleep"),
            ):
                self.assertEqual(bt_address.main(["bt-address", str(mac), "0"]), 0)
        self.assertEqual(len(sent), 2)

    def test_bluez_is_the_frames(self):
        packages = text(ROOT / "userspace/packages.txt").split()
        self.assertIn("deckard-arch-hotfixes-release-0.3/bluez", packages)
        self.assertIn("deckard-arch-hotfixes-release-0.3/bluez-utils", packages)
        build = text(ROOT / "scripts/build-userspace.sh")
        enabled = build[build.index("in_t systemctl enable") :]
        for name in ("pico4-bt-firmware.service", "pico4-bt-address.service", "bluetooth.service"):
            self.assertIn(name, enabled)

    def test_kernel_waits_for_the_wcn7850_firmware_only(self):
        patch = text(ROOT / "kernel/patches/0006-bluetooth-hci_qca-wait-for-the-wcn7850-firmware-only.patch")
        self.assertIn("+	if (qcadev->btsoc_type == QCA_WCN7850) {", patch)


if __name__ == "__main__":
    unittest.main()
