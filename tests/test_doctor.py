# SPDX-License-Identifier: Unlicense
"""overpressure.doctor: the host's and a headset's shared checks, the build container, the report, the command."""

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
    def __init__(  # noqa: PLR0913  # the fake's facts, each with a default
        self,
        platform: str = "linux",
        *,
        tools: Sequence[str] = ("ssh", "ssh-keygen", "docker"),
        linux: dict[str, str] | None = None,
        runs: dict[str, tuple[int, str]] | None = None,
        modules: Sequence[str] = ("brotli", "usb", "libusb_package"),
        engine: tuple[int, str] = (0, "28.4.0"),
        image: bool = True,
    ):
        super().__init__(
            platform=platform,
            run=self.fake_run,
            which=lambda t: f"/usr/bin/{t}" if t in tools else None,
            has_module=lambda name: name in modules,
        )
        self.linux_output, self.runs, self.calls = linux or {}, runs or {}, []
        self.engine_answer, self.image = engine, image

    def fake_run(self, argv: Sequence[str], _timeout: float) -> tuple[int, str]:
        words = [str(a) for a in argv]
        self.calls.append(words)
        if words[0] in ("docker", "podman"):
            if words[1:3] == ["image", "inspect"]:
                return (0, "[]") if self.image else (1, "Error: No such image")
            if words[1] in ("version", "info"):
                return self.engine_answer
            if words[1] == "run":
                script = words[-1]
                return 0, "\n".join(f"{k}={v}" for k, v in self.linux_output.items() if f"{k}=" in script)
        return self.runs.get(words[0], (127, f"{words[0]}: not found"))


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
    def test_a_probe_runs_once_per_script_in_an_unnamed_container(self):
        host = FakeHost(platform="win32", linux={"user": "0"})
        self.assertEqual(host.linux("echo user=$(id -u)"), {"user": "0"})
        host.linux("echo user=$(id -u)")
        runs = [c for c in host.calls if c[1] == "run"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][:4], ["docker", "run", "--rm", "--privileged"])
        self.assertEqual(runs[0][-3:-1], ["bash", "-c"])
        # A probe neither takes the build's name nor registers or hands back anything.
        self.assertNotIn("--name", runs[0])
        self.assertFalse(any(w.startswith(("OVERPRESSURE_BINFMT", "OVERPRESSURE_UID")) for w in runs[0]))
        unbuilt = FakeHost(image=False)
        self.assertEqual(unbuilt.linux("echo a=1"), {})
        self.assertFalse(any(c[1] == "run" for c in unbuilt.calls))

    def test_host_checks_name_what_is_missing_and_how_to_fix_it(self):
        checks = {c.name: c for c in doctor.host_checks(FakeHost(tools=("docker",)))}
        self.assertEqual(checks["ssh"].status, doctor.MISSING)
        self.assertIn("OpenSSH", checks["ssh"].fix)
        self.assertEqual(checks["Python"].status, doctor.OK)
        self.assertFalse(any("WSL" in c.name or c.name == "sudo" for c in checks.values()))

    def test_the_pinned_packages_and_their_fallbacks(self):
        def checks(**kw: Sequence[str]) -> dict[str, doctor.Check]:
            return {c.name: c for c in doctor.package_checks(FakeHost(**kw))}  # ty: ignore[invalid-argument-type]  # the fake's own fields

        found = checks()
        self.assertEqual(
            (found["brotli (Python)"].status, found["pyusb and libusb-package"].status), (doctor.OK, doctor.OK)
        )
        # Without the package the brotli command decompresses; without either, the stock firmware cannot be built.
        self.assertEqual(checks(modules=(), tools=("brotli",))["brotli (Python)"].status, doctor.OPTIONAL)
        missing = checks(modules=(), tools=())
        self.assertEqual(missing["brotli (Python)"].status, doctor.MISSING)
        self.assertIn("uv sync", missing["brotli (Python)"].fix)
        # Without pyusb the system's own listing counts EDL devices.
        self.assertEqual(missing["pyusb and libusb-package"].status, doctor.OPTIONAL)
        self.assertIn("brotli (Python)", [c.name for c in doctor.host_checks(FakeHost())])

    def test_the_window_needs_customtkinter_and_the_command_line_does_not(self):
        with mock.patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            missing = doctor.window_check()
        self.assertEqual((missing.status, missing.needed_for), (doctor.OPTIONAL, "the window"))
        self.assertIn("uv sync --extra gui", missing.fix)
        with mock.patch("importlib.metadata.version", return_value="6.0.0"):
            self.assertEqual(doctor.window_check(), doctor.Check("CustomTkinter", "the window", doctor.OK, "6.0.0"))
        self.assertIn("CustomTkinter", [c.name for c in doctor.host_checks(FakeHost())])

    def test_the_build_container_checks_the_engine_the_image_and_the_handler(self):
        def found(**kw: object) -> dict[str, doctor.Check]:
            return {c.name: c for c in doctor.container_checks(FakeHost(**kw))}  # ty: ignore[invalid-argument-type]  # the fake's own fields

        ready = found(linux={"arch": "x86_64", "binfmt": "aarch64 POCF"})
        self.assertEqual((ready[doctor.ENGINE].status, ready[doctor.ENGINE].detail), (doctor.OK, "docker"))
        self.assertEqual(ready["build container image"].status, doctor.OK)
        self.assertEqual(
            (ready["aarch64 handler"].status, ready["aarch64 handler"].detail), (doctor.OK, "aarch64, flags POCF")
        )
        self.assertTrue(all(c.needed_for == doctor.BUILDS for c in ready.values()))
        # No handler yet: the build registers its own, so it is not missing; a failure has its command.
        none = found(linux={"arch": "x86_64"})["aarch64 handler"]
        self.assertEqual(none.status, doctor.OPTIONAL)
        self.assertIn("python -m overpressure container binfmt", none.fix)
        self.assertEqual(
            found(linux={"arch": "x86_64", "binfmt": "qemu-aarch64 OC"})["aarch64 handler"].status, doctor.OPTIONAL
        )
        self.assertEqual(found(linux={"arch": "aarch64"})["aarch64 handler"].status, doctor.OK)
        # The image: the installer pulls it itself; the checks only name the command.
        unbuilt = found(image=False)
        self.assertEqual(unbuilt["build container image"].status, doctor.MISSING)
        self.assertIn("python -m overpressure container pull", unbuilt["build container image"].fix)
        self.assertNotIn("aarch64 handler", unbuilt)

    def test_a_missing_or_silent_engine_has_one_fix_on_every_host(self):
        for platform in ("win32", "darwin", "linux"):
            missing = doctor.container_checks(FakeHost(platform=platform, tools=()))
            self.assertEqual([(c.name, c.status) for c in missing], [(doctor.ENGINE, doctor.MISSING)])
            self.assertEqual(missing[0].fix, doctor.ENGINE_FIX)
            silent = doctor.container_checks(FakeHost(platform=platform, engine=(1, "failed to connect to the API")))
            self.assertEqual([(c.name, c.status) for c in silent], [(doctor.ENGINE, doctor.MISSING)])
            self.assertEqual(silent[0].fix, doctor.ENGINE_FIX)
            self.assertIn("failed to connect to the API", silent[0].detail)

    def test_docker_and_podman_are_asked_the_same_way(self):
        for tool in ("docker", "podman"):
            host = FakeHost(tools=(tool,), linux={"arch": "x86_64", "binfmt": "x F"})
            checks = doctor.container_checks(host)
            self.assertEqual((checks[0].name, checks[0].status, checks[0].detail), (doctor.ENGINE, doctor.OK, tool))
            self.assertIn([tool, "info"], host.calls)

    def test_an_engine_with_little_memory_is_a_warning_with_its_fix(self):
        # Docker Desktop's default VM (4 GiB) swapped the userspace build to a crawl.
        for info, memory in (
            ("Server:\n Total Memory: 3.877GiB\n", 3.877),
            ("host:\n  memTotal: 4163051520\n", 4163051520 / 2**30),
        ):
            host = FakeHost(engine=(0, info), linux={"arch": "x86_64", "binfmt": "x F"})
            checks = {c.name: c for c in doctor.container_checks(host)}
            low = checks["build container memory"]
            self.assertEqual(low.status, doctor.WARNING)
            self.assertIn(f"{memory:.1f} GiB", low.detail)
            self.assertIn("Memory limit", low.fix)
            self.assertEqual(checks[doctor.ENGINE].status, doctor.OK)
        enough = FakeHost(engine=(0, " Total Memory: 15.63GiB\n"), linux={"arch": "x86_64", "binfmt": "x F"})
        self.assertNotIn("build container memory", [c.name for c in doctor.container_checks(enough)])
        self.assertIsNone(doctor.engine_memory("28.4.0"))

    def test_version_checks(self):
        host = FakeHost(runs={"adb": (0, "Android Debug Bridge version 1.0.41\nmore")})
        self.assertEqual(
            doctor.version_check(host, "adb", ["adb", "version"], "headset", "fix").detail,
            "Android Debug Bridge version 1.0.41",
        )
        self.assertEqual(doctor.version_check(host, "fb", ["fastboot"], "headset", "get it").status, doctor.MISSING)

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
        with mock.patch.object(localconfig.importlib.util, "find_spec", return_value=None):
            self.assertIn(
                "EXAMPLE_NCM_PYTHON", doctor.ncm_checks(FakeHost(platform="win32"), self.settings())[0].detail
            )


if __name__ == "__main__":
    unittest.main()
