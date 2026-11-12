# SPDX-License-Identifier: Unlicense
"""micmute: the microphone follows the wear sensor in place of Valve's steamvr-proxmicmute."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "userspace/rootfs/usr/libexec/pico4/micmute"
_loader = importlib.machinery.SourceFileLoader("micmute", str(PATH))
_spec = importlib.util.spec_from_loader("micmute", _loader)
assert _spec is not None
tool = importlib.util.module_from_spec(_spec)
_loader.exec_module(tool)


class MuteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = Path(self.dir.name) / "pico4-worn"
        self.calls = []
        patches = [
            mock.patch.object(tool, "STATE", str(self.state)),
            mock.patch.object(tool.subprocess, "run", self.fake_run),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.dir.cleanup()

    def fake_run(self, argv: list[str], *, check: bool = False) -> mock.Mock:  # noqa: ARG002  # subprocess.run's keyword
        self.calls.append(argv)
        return mock.Mock(returncode=0)

    def once(self, value: str | None) -> list[str]:
        if value is not None:
            with self.state.open("w", encoding="ascii") as f:
                f.write(value)
        self.calls.clear()
        self.assertEqual(tool.main(["micmute", "--once"]), 0)
        return [c[-1] for c in self.calls]

    def test_off_the_head_mutes_and_worn_unmutes(self):
        self.assertEqual(self.once("0\n"), ["1"])
        self.assertEqual(self.once("1\n"), ["0"])
        self.assertEqual(self.calls[0][:3], ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@"])

    def test_unknown_state_leaves_the_microphone_on(self):
        self.assertEqual(self.once(None), ["0"])
        self.assertEqual(self.once("garbage"), ["0"])

    def test_stopping_unmutes(self):
        with self.state.open("w", encoding="ascii") as f:
            f.write("0\n")
        handlers = {}
        with (
            mock.patch.object(tool.signal, "signal", lambda sig, fn: handlers.update({sig: fn})),
            mock.patch.object(tool.time, "sleep", side_effect=lambda _s: handlers[tool.signal.SIGTERM](15, None)),
        ):
            self.assertEqual(tool.main(["micmute"]), 0)
        self.assertEqual([c[-1] for c in self.calls], ["1", "0"])


class WiringTests(unittest.TestCase):
    def test_steamvr_wants_it_and_valves_mute_stays_masked(self):
        dropin = ROOT / "userspace/rootfs/usr/lib/systemd/user/steamvr.service.d/51-pico4-micmute.conf"
        self.assertIn("Wants=pico4-micmute.service", dropin.read_text(encoding="utf-8"))
        unit = (ROOT / "userspace/rootfs/usr/lib/systemd/user/pico4-micmute.service").read_text(encoding="utf-8")
        self.assertIn("PartOf=steamvr.service", unit)
        self.assertIn("ExecStart=/usr/libexec/pico4/micmute\n", unit)
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        mask = next(line for line in build.splitlines() if "systemctl --global mask" in line)
        self.assertIn("steamvr-proxmicmute.service", mask)
        self.assertNotIn(b"\r", PATH.read_bytes())


if __name__ == "__main__":
    unittest.main()
