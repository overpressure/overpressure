# SPDX-License-Identifier: Unlicense
"""overpressure.doctor: the host's and a headset's shared checks, Linux probes, the report, the command."""

from __future__ import annotations

import contextlib
import importlib.metadata
import io
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from unittest import mock

from overpressure import __main__ as cli
from overpressure import doctor, localconfig, tools
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
        return self.runs.get(str(argv[0]), (127, f"{argv[0]}: not found"))


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

    def test_the_window_needs_customtkinter_and_the_command_line_does_not(self):
        with mock.patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            missing = doctor.window_check()
        self.assertEqual((missing.status, missing.needed_for), (doctor.OPTIONAL, "the window"))
        self.assertIn("uv sync --extra gui", missing.fix)
        with mock.patch("importlib.metadata.version", return_value="6.0.0"):
            self.assertEqual(doctor.window_check(), doctor.Check("CustomTkinter", "the window", doctor.OK, "6.0.0"))
        self.assertIn("CustomTkinter", [c.name for c in doctor.host_checks(FakeHost())])

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


@dataclass
class Example(localconfig.DeviceSettings):
    PREFIX = "EXAMPLE_"


class DeviceChecksTests(unittest.TestCase):
    """The checks every Qualcomm Android headset's doctor is built from."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(tools, "CACHE", self.root / "tools-cache")  # no fetched tools
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings(self, **values: str) -> Example:
        s = Example(**values)
        s.path = lambda name: self.root / getattr(s, name)
        return s

    def test_env_serials_loader_and_edl_ng(self):
        s = self.settings(adb_serial="A1")
        self.assertEqual(doctor.env_check(self.root).status, doctor.MISSING)
        (self.root / ".env").write_text("EXAMPLE_ADB_SERIAL=A1\n")
        self.assertEqual(doctor.env_check(self.root, "work").needed_for, "work")
        serials = {c.name: c for c in doctor.serial_checks(s, "work", "edl")}
        self.assertEqual(list(serials), ["EXAMPLE_ADB_SERIAL", "EXAMPLE_FASTBOOT_SERIAL", "EXAMPLE_EDL_SERIAL"])
        self.assertEqual([c.status for c in serials.values()], [doctor.OK, doctor.MISSING, doctor.MISSING])
        self.assertEqual(serials["EXAMPLE_EDL_SERIAL"].needed_for, "edl")
        self.assertIn("edl-ng's Serial Number", serials["EXAMPLE_EDL_SERIAL"].fix)
        loader = doctor.loader_check(s, fix="the vendor's loader")
        self.assertEqual((loader.status, loader.fix), (doctor.MISSING, "the vendor's loader"))
        (self.root / "assets").mkdir()
        (self.root / "assets/firehose.elf").write_bytes(b"elf")
        self.assertEqual(doctor.loader_check(s).status, doctor.OK)
        self.assertEqual(doctor.edl_tool_check(FakeHost(tools=("edl-ng.exe", "edl-ng")), s).status, doctor.OK)
        missing = doctor.edl_tool_check(FakeHost(tools=()), s)
        self.assertEqual((missing.status, missing.fix), (doctor.MISSING, doctor.FETCH))

    def test_the_backup_by_its_manifest(self):
        folder = self.root / "backup"
        self.assertEqual(doctor.backup_check(folder, fix="take one").fix, "take one")
        folder.mkdir()
        (folder / "boot.bin").write_bytes(b"boot")
        (folder / "manifest.json").write_text(json.dumps({"files": {"boot.bin": {"size": 4, "sha256": "x"}}}))
        self.assertEqual(doctor.backup_check(folder).status, doctor.OK)
        (folder / "boot.bin").write_bytes(b"truncated, no: longer")
        found = doctor.backup_check(folder)
        self.assertEqual(
            (found.status, found.detail), (doctor.MISSING, "1 of 1 files missing or of another size: boot.bin")
        )

    def test_platform_tools_and_the_windows_link(self):
        s = self.settings(ncm_python="py")
        host = FakeHost(
            platform="win32",
            runs={
                str(s.tool("adb")): (0, "Android Debug Bridge version 1.0.41"),
                str(self.root / "py"): (0, '{"status": "ready", "dependencies": {"pyusb": "1.3.1"}}'),
            },
        )
        found = {c.name: c for c in doctor.tool_checks(host, s)}
        self.assertEqual((found["adb"].status, found["fastboot"].status), (doctor.OK, doctor.MISSING))
        self.assertIn("EXAMPLE_PLATFORM_TOOLS", found["fastboot"].fix)
        self.assertEqual(doctor.ncm_checks(host, s)[0].detail, "pyusb 1.3.1")
        self.assertEqual(doctor.ncm_checks(FakeHost(platform="win32"), s)[0].status, doctor.MISSING)
        self.assertEqual(doctor.ncm_checks(FakeHost(platform="linux"), s), [])
        with mock.patch.object(localconfig.tools, "ncm_python", return_value=None):
            self.assertIn(
                "EXAMPLE_NCM_PYTHON", doctor.ncm_checks(FakeHost(platform="win32"), self.settings())[0].detail
            )

    def test_an_x86_64_build_host_needs_binfmt_with_the_f_flag_and_prefers_native_llvm(self):
        found = {
            c.name: c for c in doctor.arm64_build_checks(FakeHost(linux={"arch": "x86_64", "binfmt": "PO"}), "build")
        }
        self.assertEqual(found["aarch64 binfmt (F flag)"].status, doctor.MISSING)
        self.assertEqual(found["clang and lld"].status, doctor.WARNING)
        host = FakeHost(linux={"arch": "x86_64", "binfmt": "POCF", "llvm": "/usr/lib/llvm-20"})
        self.assertEqual(doctor.failed(doctor.arm64_build_checks(host, "build")), [])
        self.assertEqual(doctor.arm64_build_checks(FakeHost(linux={"arch": "aarch64"}), "build"), [])


if __name__ == "__main__":
    unittest.main()
