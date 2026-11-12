# SPDX-License-Identifier: Unlicense
"""The on-headset listener check and support bundle, with fake output and fake identifiers."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> ModuleType:
    """Load one of the headset's programs, which have no suffix, as a module."""
    path = ROOT / "userspace/rootfs/usr/bin" / name
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def member_text(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    """Return a regular member's text; extractfile gives None only for other kinds."""
    f = archive.extractfile(member)
    assert f is not None
    return f.read().decode()


listeners = load("pico4-listeners")
gate = load("pico4-still-gate")
support = load("pico4-support-bundle")

SS = """\
tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=512,fd=3))
tcp LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=512,fd=4))
tcp LISTEN 0 128 127.0.0.1:27060 0.0.0.0:* users:(("steam",pid=900,fd=8))
tcp LISTEN 0 128 [::1]:631 [::]:* users:(("cupsd",pid=33,fd=7))
udp UNCONN 0 0 0.0.0.0:68 0.0.0.0:* users:(("NetworkManager",pid=7,fd=20))
udp UNCONN 0 0 0.0.0.0:27036 0.0.0.0:* users:(("steam",pid=900,fd=9))
"""


class ListenerTests(unittest.TestCase):
    def test_loopback_does_not_count(self):
        found = listeners.parse(SS)
        self.assertEqual(
            [(i["process"], i["port"]) for i in found],
            [("sshd", 22), ("sshd", 22), ("NetworkManager", 68), ("steam", 27036)],
        )

    def test_the_expected_set_passes(self):
        self.assertEqual(listeners.unexpected(listeners.parse(SS)), [])

    def test_a_new_listener_is_caught(self):
        for line in (
            'tcp LISTEN 0 5 0.0.0.0:80 0.0.0.0:* users:(("httpd",pid=1,fd=1))',  # an unknown program
            'tcp LISTEN 0 5 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=1,fd=1))',  # an unknown port
            "tcp LISTEN 0 5 0.0.0.0:23 0.0.0.0:*",
        ):  # no process known
            (bad,) = listeners.unexpected(listeners.parse(line))
            self.assertIn(str(bad["port"]), listeners.describe(bad))


class StillGateTests(unittest.TestCase):
    def memory(self, poses: list[tuple[int, float, int]]) -> bytearray:
        data = bytearray(gate.POSES + gate.POSE_SIZE * gate.POSE_COUNT)
        for index, (stamp, x, confidence) in enumerate(poses):
            at = gate.POSES + gate.POSE_SIZE * index
            struct.pack_into("<q", data, at, stamp)
            struct.pack_into("<3d", data, at + 8, x, 0.0, 0.0)
            struct.pack_into("<4d", data, at + 32, 0.0, 0.0, 0.0, 1.0)
            struct.pack_into("<I", data, at + 112, confidence)
        return data

    def test_movement_gap_and_loss(self):
        data = self.memory([(0, 0.0, 1), (5_000_000, 0.002, 1), (25_000_000, 0.001, 0)])
        times = iter([0.0, 1.0, 2.0, 9.0])

        def sleep(_: float) -> None:
            struct.pack_into("<Q", data, 32, 3)

        count, lost, furthest, angle, longest = gate.measure(data, 5, clock=lambda: next(times), sleep=sleep)
        self.assertEqual((count, lost), (3, 1))
        self.assertAlmostEqual(furthest, 0.002)
        self.assertAlmostEqual(angle, 0.0)
        self.assertEqual(longest, 20_000_000)


class PackageLockTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("lock", ROOT / "scripts/check-package-lock.py")
        assert spec is not None
        assert spec.loader is not None
        self.lock = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.lock)

    def test_only_the_tested_packages_pass(self):
        lock = self.lock.read("aa  ./a-1.pkg\nbb  b-1.pkg\n")
        self.assertEqual(self.lock.differences(self.lock.read("aa  ./a-1.pkg\ncc  ./pico4-x-1.pkg\n"), lock), [])
        self.assertEqual(
            self.lock.differences(self.lock.read("ff  ./a-1.pkg\n"), lock), ["a-1.pkg: differs from the lock"]
        )
        self.assertEqual(self.lock.differences(self.lock.read("aa  ./a-2.pkg\n"), lock), ["a-2.pkg: not in the lock"])


class SupportBundleTests(unittest.TestCase):
    SECRETS = ("FAKESERIAL123", "MyHomeNetwork", "fake_account", "Fake Persona", "fake-machine-id-0123")
    TEXT = (
        "serial FAKESERIAL123 joined MyHomeNetwork from 192.168.178.23 and fd00:1234:5678:9abc::1 "
        "fe80:1:2:3:4:5:6:7 mac aa:bb:cc:dd:ee:ff\n"
        "steam 76561198000000001 [U:1:39734273] fake_account Fake Persona fake-machine-id-0123\n"
        "kept: 127.0.0.1 172.16.42.1 0.0.0.0 version 300.12.5.999 pico4-steamos\n"
    )

    def bundle(self, globs: Sequence[str] = ()) -> tuple[list[str], dict[str, str]]:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "bundle.tar.gz"
            with patch.object(support, "run", lambda *_: self.TEXT):
                names = support.bundle(output, commands={"one.txt": "fake"}, globs=globs, names=list(self.SECRETS))
            with tarfile.open(output) as archive:
                return names, {m.name: member_text(archive, m) for m in archive.getmembers()}

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_identifiers_are_masked(self):
        _, members = self.bundle()
        text = members["support/one.txt"]
        for secret in (
            *self.SECRETS,
            "192.168.178.23",
            "aa:bb:cc:dd:ee:ff",
            "76561198000000001",
            "[U:1:39734273]",
            "fe80:1:2:3:4:5:6:7",
        ):
            self.assertNotIn(secret, text)
        for kept in ("127.0.0.1", "172.16.42.1", "0.0.0.0", "300.12.5.999", "pico4-steamos"):  # noqa: S104  # text, nothing binds
            self.assertIn(kept, text)

    def test_log_files_are_masked_and_cut_to_their_newest_part(self):
        log = self.folder / "big.log"
        log.write_bytes(b"old aa:bb:cc:dd:ee:ff\n" * 1000 + b"new FAKESERIAL123\n")
        with tempfile.TemporaryDirectory() as out:
            output = Path(out) / "b.tar.gz"
            support.bundle(output, commands={}, globs=[str(log)], names=list(self.SECRETS), limit=40)
            with tarfile.open(output) as archive:
                (member,) = archive.getmembers()
                text = member_text(archive, member)
        self.assertNotIn("FAKESERIAL123", text)
        self.assertTrue(text.endswith("<name>\n"))

    def test_a_missing_part_stays_out_without_failing(self):
        names, _ = self.bundle(globs=[str(self.folder / "absent/*")])
        self.assertEqual(names, ["one.txt"])

    def test_nothing_with_tokens_is_archived(self):
        for pattern in support.LOG_GLOBS:
            for forbidden in ("config.vdf", "loginusers", "system-connections", ".ssh", "shadow"):
                self.assertNotIn(forbidden, pattern)
        text = str(support.COMMANDS)
        for forbidden in ("config.vdf", "loginusers", "system-connections", ".ssh", "shadow"):
            self.assertNotIn(forbidden, text)

    def test_the_soc_serial_is_masked_in_hex_too(self):
        # FastRPC's test signature is named after it: testsig-0x<serial in hex>.so.
        serials = {"/sys/devices/soc0/serial_number": "3735928559"}
        with (
            patch.object(support, "read", lambda path: serials.get(str(path), "")),
            patch.object(support, "run", lambda *_: ""),
        ):
            names = support.identifiers("console=x", steam=self.folder)
        self.assertIn("deadbeef", names)
        mask = support.masker(names)
        self.assertEqual(
            mask("for testsig-0xDEADBEEF.so and testsig-0xdeadbeef.so"),
            "for testsig-0x<name>.so and testsig-0x<name>.so",
        )

    def test_account_and_network_names_are_found(self):
        with tempfile.TemporaryDirectory() as folder:
            steam = Path(folder)
            (steam / "config").mkdir()
            (steam / "config/loginusers.vdf").write_text(
                '"users"\n{\n"76561198000000001"\n{\n"AccountName"\t\t"fake_account"\n'
                '"PersonaName"\t\t"Fake Persona"\n}\n}\n',
                encoding="utf-8",
            )
            with patch.object(support, "run", lambda *_: "MyHomeNetwork\n"):
                names = support.identifiers("androidboot.serialno=FAKESERIAL123 console=x", steam=steam)
        for name in ("FAKESERIAL123", "fake_account", "Fake Persona", "MyHomeNetwork"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
