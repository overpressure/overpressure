# SPDX-License-Identifier: Unlicense
"""overpressure.doctor: the host's checks, Linux probes, the report, the command and the panel."""

from __future__ import annotations

import contextlib
import io
import os
import time
import unittest
from typing import TYPE_CHECKING, NoReturn
from unittest import mock

from overpressure import __main__ as cli
from overpressure import doctor
from overpressure.headset import Command, Headset, Info, Registry

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeHost(doctor.Host):
    def __init__(
        self,
        platform: str = "linux",
        tools: Sequence[str] = ("ssh", "ssh-keygen", "sudo", "wsl"),
        linux: dict[str, str] | None = None,
        runs: dict[str, tuple[int, str]] | None = None,
    ):
        super().__init__(platform=platform, run=self.fake_run, which=lambda t: f"/usr/bin/{t}" if t in tools else None)
        self.linux_output, self.runs, self.calls = linux or {}, runs or {}, []

    def fake_run(self, argv: Sequence[str], _timeout: float) -> tuple[int, str]:
        self.calls.append(list(argv))
        if argv[0] in ("wsl", "bash"):
            script = argv[-1]
            out = "\n".join(f"{k}={v}" for k, v in self.linux_output.items() if f"{k}=" in script)
            return 0, out
        return self.runs.get(argv[0], (127, f"{argv[0]}: not found"))


class Probe(Headset):
    info = Info("probe", "Probe headset")

    def commands(self):
        return {"x": Command("x", "x")}

    def dependencies(self):
        def ok(_host: doctor.Host) -> list[doctor.Check]:
            return [doctor.Check("tool", "building", doctor.OK, "1.0")]

        def missing(_host: doctor.Host) -> list[doctor.Check]:
            return [doctor.Check("thing", "building", doctor.MISSING, "not there", "install thing")]

        def broken(_host: doctor.Host) -> NoReturn:
            msg = "probe broke"
            raise RuntimeError(msg)

        return (ok, missing, broken)


class DoctorTests(unittest.TestCase):
    def test_linux_probe_runs_once_per_script_through_wsl_exec(self):
        host = FakeHost(platform="win32", linux={"user": "0", "distro": "Ubuntu"})
        self.assertEqual(host.linux("echo user=$(id -u)"), {"user": "0"})
        host.linux("echo user=$(id -u)")
        self.assertEqual(len(host.calls), 1)
        self.assertEqual(host.calls[0][:5], ["wsl", "-u", "root", "--exec", "bash"])
        self.assertEqual(FakeHost(platform="win32", tools=()).linux("echo a=1"), {})

    def test_host_checks_name_what_is_missing_and_how_to_fix_it(self):
        checks = {c.name: c for c in doctor.host_checks(FakeHost(tools=("sudo",)))}
        self.assertEqual(checks["ssh"].status, doctor.MISSING)
        self.assertIn("OpenSSH", checks["ssh"].fix)
        self.assertEqual(checks["Python"].status, doctor.OK)
        windows = {c.name: c for c in doctor.host_checks(FakeHost(platform="win32", linux={"user": "1000"}))}
        self.assertEqual(windows["WSL (as root)"].status, doctor.MISSING)
        self.assertIn("wsl --install", windows["WSL (as root)"].fix)

    def test_the_container_backend_checks_the_engine_and_the_image(self):
        host = FakeHost(platform="darwin", tools=("docker",), runs={"docker": (0, "28.4.0")})
        self.assertEqual(host.backend, "docker")
        found = {c.name: c for c in doctor.host_checks(host)}
        self.assertEqual(found["docker"].detail, "engine 28.4.0")
        self.assertEqual(found["build container image"].status, doctor.OK)  # inspect answered 0
        host = FakeHost(platform="darwin", tools=("docker",), runs={"docker": (1, "Cannot connect to the daemon")})
        found = {c.name: c for c in doctor.host_checks(host)}
        self.assertEqual(found["docker"].status, doctor.MISSING)
        self.assertEqual(host.linux("echo a=1"), {})  # no image, no probes

    def test_version_and_linux_command_checks(self):
        host = FakeHost(runs={"adb": (0, "Android Debug Bridge version 1.0.41\nmore")}, linux={"curl": "/usr/bin/curl"})
        self.assertEqual(
            doctor.version_check(host, "adb", ["adb", "version"], "headset", "fix").detail,
            "Android Debug Bridge version 1.0.41",
        )
        self.assertEqual(doctor.version_check(host, "fb", ["fastboot"], "headset", "get it").status, doctor.MISSING)
        checks = doctor.linux_commands(host, "build", {"curl": "downloads", "mkfs.ext4": "images"}, "apt install x")
        self.assertEqual([c.status for c in checks], [doctor.OK, doctor.MISSING])
        self.assertEqual(checks[1].fix, "apt install x")

    def test_headset_probes_add_to_the_host_checks_and_a_broken_one_is_reported(self):
        checks = doctor.run(Probe(), FakeHost())
        names = [c.name for c in checks]
        self.assertIn("tool", names)
        self.assertEqual([c.name for c in doctor.failed(checks)], ["thing"])
        self.assertTrue(any(c.status == doctor.WARNING and "probe broke" in c.detail for c in checks))
        text = doctor.report(checks)
        self.assertIn("MISSING", text)
        self.assertIn("-> install thing", text)
        self.assertIn("1 missing: thing", text)

    def test_the_command_fails_while_something_is_missing(self):
        out = io.StringIO()
        registry = Registry({"probe": Probe()})
        for checks, expected in (
            ([doctor.Check("a", "b", doctor.MISSING)], 1),
            ([doctor.Check("a", "b", doctor.OK)], 0),
        ):
            with (
                contextlib.redirect_stdout(out),
                mock.patch.dict(os.environ, {"OVERPRESSURE_DEVICE": ""}),
                mock.patch.object(cli, "discover", return_value=registry),
                mock.patch.object(doctor, "run", return_value=checks),
            ):
                self.assertEqual(cli.main(["doctor"]), expected)

    def test_the_panel_lists_the_checks_and_shows_the_fix(self):
        try:
            import tkinter as tk  # noqa: PLC0415  # optional: Python without Tk skips
        except ImportError as exc:
            self.skipTest(f"no Tk: {exc}")
        try:
            root = tk.Tk()
        except tk.TclError as exc:  # no display
            self.skipTest(f"no Tk display: {exc}")
        from overpressure import gui  # noqa: PLC0415  # needs Tk, which this test checks first

        checks = [
            doctor.Check("thing", "building", doctor.MISSING, "not there", "install thing"),
            doctor.Check("tool", "building", doctor.OK, "1.0"),
        ]
        try:
            with mock.patch.object(doctor, "run", return_value=checks):
                app = gui.App(root, Registry({"probe": Probe()}), None)
                app.tree.selection_set(gui.DOCTOR)
                for _ in range(100):
                    root.update()
                    if app.checks:
                        break
                    time.sleep(0.02)  # what Tk's after(20) without a callback does
                app._drain()  # noqa: SLF001  # what Tk's idle loop runs, at once
                root.update()
            self.assertEqual(app.tree.item(gui.DOCTOR, "text"), "Check dependencies (1 missing)")
            self.assertEqual(len(app.doctor_table.get_children()), 2)
            self.assertEqual(app.doctor_fix.get("1.0", "end").strip(), "install thing")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
