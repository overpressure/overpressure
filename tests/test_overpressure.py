# SPDX-License-Identifier: Unlicense
"""The headset interface, the registry, the step runner and the command line."""

from __future__ import annotations

import contextlib
import io
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from overpressure import REPO, disclaimer, fastboot, linux, runner
from overpressure import __main__ as cli
from overpressure.headset import Command, Headset, Info, Param, Registry, Step, discover

#: The command line's own words: a step may name them instead of a command of the headset.
GENERIC = ("devices", "gui", "steps", "run", "ram-boot", "doctor", "tools", "container")


class Fake(Headset):
    info = Info("fake", "Fake headset")

    def commands(self):
        return {"build": Command("image", "builds", keep=True)}

    def steps(self):
        return (
            Step(
                "img",
                "Image",
                "Image",
                ("build", "--firmware", "{firmware}", "{fast}"),
                params=(Param("firmware", "Firmware", "a"), Param("fast", "Fast", "0", "flag", option="--fast")),
            ),
            Step(
                "us",
                "Userspace",
                "Image",
                ("scripts/build.sh", "{root}", "assets/fw-{version}", "{out}"),
                params=(
                    Param("root", "Root", "/var/tmp/b"),  # noqa: S108  # a default word, nothing is written
                    Param("version", "Version", "1.2"),
                    Param("out", "Out", ""),
                ),
                script=True,
                linux_root=True,
            ),
        )


class StepTests(unittest.TestCase):
    def test_parameters_flags_and_empty_words(self):
        img, us = Fake().steps()
        self.assertEqual(img.command({}), ["build", "--firmware", "a"])
        self.assertEqual(img.command({"firmware": "b", "fast": "1"}), ["build", "--firmware", "b", "--fast"])
        # Braces inside a word take the value too; an empty parameter word is left out.
        self.assertEqual(us.command({}), ["scripts/build.sh", "/var/tmp/b", "assets/fw-1.2"])  # noqa: S108  # an expected word, nothing is written


class RunnerTests(unittest.TestCase):
    def test_linux_root_steps_run_in_the_build_container_on_every_host(self):
        h = Fake()
        img, us = h.steps()
        argv = runner.process(h, us, {}, python="py")
        self.assertEqual(argv[:6], ["py", "-m", "overpressure", "container", "run", "--"])
        self.assertEqual(argv[6:8], ["bash", "devices/fake/scripts/build.sh"])
        self.assertTrue(linux.is_build(argv))
        self.assertEqual(runner.process(h, img, {}, python="py")[0], "py")  # no Linux needed
        self.assertFalse(linux.is_build(runner.process(h, img, {}, python="py")))

    def test_commands_go_through_this_command_line(self):
        h = Fake()
        img, _ = h.steps()
        self.assertEqual(
            runner.process(h, img, {}, python="py"),
            ["py", "-m", "overpressure", "--device", "fake", "build", "--firmware", "a"],
        )

    def test_stopping_a_build_removes_its_container_first(self):
        proc = mock.Mock(pid=4242)
        calls: list[str] = []
        proc.terminate.side_effect = lambda: calls.append("terminate")
        with mock.patch.object(linux, "stop_build", side_effect=lambda pid: calls.append(f"rm {pid}")):
            runner.stop(proc, ["py", "-m", "overpressure", "container", "run", "--", "bash", "x.sh"])
            self.assertEqual(calls, ["rm 4242", "terminate"])
            calls.clear()
            runner.stop(proc, ["py", "-m", "overpressure", "--device", "fake", "build"])
            self.assertEqual(calls, ["terminate"])  # no container behind it


class FakeEngine:
    """A fake container engine: the build container's state, and every command it was given."""

    def __init__(self, owner: str = "", *, running: bool = True) -> None:
        self.owner, self.running, self.calls = owner, running, []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv[1:])
        if argv[1:3] == ["container", "inspect"]:
            if not self.owner:
                return 1, "Error: No such container: overpressure-build"
            return 0, f"{self.owner}\tbash devices/x/build.sh\t{linux.REPO}\t{str(self.running).lower()}"
        if argv[1:3] == ["rm", "-f"]:
            self.owner = ""
            return 0, "overpressure-build"
        return 1, "unexpected"


class ContainerTests(unittest.TestCase):
    HOST = socket.gethostname()

    def test_a_build_is_named_labelled_and_registers_the_handler(self):
        argv = linux.run_argv(
            ["bash", "x.sh"],
            tool="docker",
            name=linux.NAME,
            labels={linux.OWNER: "h:1:2"},
            binfmt=True,
            ids=(1000, 1000),
        )
        self.assertEqual(argv[:4], ["docker", "run", "--rm", "--privileged"])
        self.assertEqual(argv[argv.index("--name") + 1], "overpressure-build")
        self.assertIn(f"{linux.OWNER}=h:1:2", argv)
        for env in ("OVERPRESSURE_BINFMT=1", "OVERPRESSURE_UID=1000", "OVERPRESSURE_GID=1000"):
            self.assertIn(env, argv)
        self.assertIn(f"{REPO}:/work", argv)
        self.assertIn(f"{linux.VOLUME}:/var/tmp", argv)
        self.assertEqual(argv[-3:], [linux.image(), "bash", "x.sh"])
        self.assertTrue(linux.image().startswith("overpressure-builder:"))
        probe = linux.run_argv(["true"], tool="podman")
        self.assertNotIn("--name", probe)
        self.assertFalse(any(w.startswith(("OVERPRESSURE_BINFMT", "OVERPRESSURE_UID")) for w in probe))

    def test_ownership_goes_back_to_the_hosts_user_whatever_the_engine(self):
        with (
            mock.patch.object(os, "getuid", create=True, return_value=1000),
            mock.patch.object(os, "getgid", create=True, return_value=100),
        ):
            self.assertEqual(linux.host_ids(), (1000, 100))
        with (
            mock.patch.object(os, "getuid", create=True, return_value=0),
            mock.patch.object(os, "getgid", create=True, return_value=0),
        ):
            self.assertIsNone(linux.host_ids())  # root needs nothing back
        with mock.patch.object(os, "getuid", None, create=True):
            self.assertIsNone(linux.host_ids())  # Windows: no Unix owners

    def test_the_engine_is_docker_or_podman_whichever_answers(self):
        def which(*tools: str) -> Callable[[str], str | None]:
            return lambda tool: tool if tool in tools else None

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OVERPRESSURE_CONTAINER_ENGINE", None)
            self.assertEqual(linux.engine(which("podman")), "podman")
            self.assertEqual(linux.engine(which("docker")), "docker")
            self.assertEqual(linux.engine(which()), "docker")  # the checks then say neither is there
            with mock.patch.object(linux, "answers", side_effect=lambda tool: tool == "podman"):
                self.assertEqual(linux.engine(which("docker", "podman")), "podman")
            with mock.patch.object(linux, "answers", return_value=False):
                self.assertEqual(linux.engine(which("docker", "podman")), "docker")
            os.environ["OVERPRESSURE_CONTAINER_ENGINE"] = "podman"
            self.assertEqual(linux.engine(which("docker")), "podman")

    def test_a_second_build_is_refused_in_one_line(self):
        engine = FakeEngine(f"{self.HOST}:{os.getpid()}:1")
        refusal = linux.claim(engine, alive=lambda _pid: True)
        self.assertIsNotNone(refusal)
        self.assertTrue(str(refusal).startswith("another build is running: bash devices/x/build.sh;"))
        self.assertNotIn("\n", str(refusal))
        self.assertNotIn(["rm", "-f", linux.NAME], engine.calls)
        # One started on another host (a shared engine) counts as running too.
        self.assertIsNotNone(linux.claim(FakeEngine("elsewhere:1:1"), alive=lambda _pid: False))

    def test_a_leftover_whose_client_is_gone_is_removed(self):
        for engine in (
            FakeEngine(f"{self.HOST}:999999:1"),
            FakeEngine(f"{self.HOST}:5:1", running=False),
            FakeEngine("junk"),
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(linux.claim(engine, alive=lambda _pid: False))
            self.assertIn(["rm", "-f", linux.NAME], engine.calls)
        self.assertIsNone(linux.claim(FakeEngine(), alive=lambda _pid: True))  # none there

    def test_stop_removes_only_the_container_of_the_client_being_stopped(self):
        engine = FakeEngine(f"{self.HOST}:200:100")
        self.assertFalse(linux.stop_build(300, engine))  # someone else's build
        self.assertNotIn(["rm", "-f", linux.NAME], engine.calls)
        self.assertTrue(linux.stop_build(100, engine))  # the client's launcher: its child started it
        self.assertIn(["rm", "-f", linux.NAME], engine.calls)
        self.assertTrue(linux.stop_build(200, FakeEngine(f"{self.HOST}:200:100")))
        self.assertFalse(linux.stop_build(200, FakeEngine("elsewhere:200:100")))
        self.assertFalse(linux.stop_build(200, FakeEngine()))

    def test_the_engines_refusal_of_the_name_becomes_one_plain_line(self):
        out = io.BytesIO()
        linux._rewrite(  # noqa: SLF001  # the client's stderr copier
            io.BytesIO(
                b'docker: Error response from daemon: Conflict. The container name "/overpressure-build" is already '
                b'in use by container "abc".\nother line\n'
            ),
            out,
        )
        self.assertEqual(
            out.getvalue().decode().splitlines(),
            ["error: another build is running (container overpressure-build); let it finish or stop it", "other line"],
        )

    def test_this_process_is_alive_and_a_finished_one_is_not(self):
        self.assertTrue(linux.pid_alive(os.getpid()))
        self.assertFalse(linux.pid_alive(0))
        done = subprocess.run(
            [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True
        )  # this Python
        self.assertFalse(linux.pid_alive(int(done.stdout)))


class RegistryTests(unittest.TestCase):
    def test_the_pico_4_is_registered(self):
        registry = discover()
        self.assertEqual(registry.get("phoenix").info.name, "PICO 4")
        with self.assertRaises(KeyError):
            registry.get("nonexistent")

    def test_steps_only_name_commands_and_scripts_that_exist(self):
        for codename, headset in discover().headsets.items():
            self.assertEqual(headset.root, REPO / "devices" / codename)
            for word, command in headset.commands().items():
                self.assertTrue((headset.root / "tooling" / f"{command.module}.py").exists(), (codename, word))
            keys = [s.key for s in headset.steps()]
            self.assertEqual(len(keys), len(set(keys)), codename)
            for step in headset.steps():
                self.assertIn(step.group, ("Setup", "Image", "Headset"))
                words = step.command({})
                if step.script:
                    self.assertTrue((headset.root / words[0]).exists(), (codename, step.key))
                elif words[0] == "ram-boot":
                    self.assertIsNotNone(headset.ram_boot(), codename)
                elif words[0] in GENERIC:
                    pass
                else:
                    self.assertIn(words[0], headset.commands(), (codename, step.key))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(out),
            mock.patch.dict(os.environ, {"OVERPRESSURE_DEVICE": ""}),
        ):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_devices_and_steps(self):
        code, out = self.run_cli("devices")
        self.assertEqual(code, 0)
        self.assertIn("phoenix", out)
        code, out = self.run_cli("--device", "phoenix", "steps")
        self.assertEqual(code, 0)
        self.assertIn("-m overpressure --device phoenix build --firmware", out)

    def test_unknown_headset(self):
        code, out = self.run_cli("--device", "nonexistent", "steps")
        self.assertEqual(code, 2)
        self.assertIn("no headset 'nonexistent'", out)

    def test_headset_commands_are_listed(self):
        code, out = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("Commands of the PICO 4 (phoenix)", out)

    def test_a_command_reaches_the_headset_module(self):
        h = Fake()
        with (
            mock.patch.object(Headset, "main", return_value=7) as main,
            mock.patch.object(cli, "discover", return_value=Registry({"fake": h})),
        ):
            code, _ = self.run_cli("build", "--firmware", "x")
        self.assertEqual(code, 7)
        main.assert_called_once_with(["build", "--firmware", "x"])

    def test_ram_boot_needs_a_target_and_reports_refusal_apart(self):
        registry = Registry({"fake": Fake()})
        with mock.patch.object(cli, "discover", return_value=registry):
            code, out = self.run_cli("ram-boot")
        self.assertEqual(code, 2)
        self.assertIn("has no RAM boot", out)
        target = mock.Mock(spec=fastboot.RamBootTarget)
        for effect, expected in ((fastboot.Refused("no fastboot boot"), 3), (RuntimeError("no answer"), 1), (None, 0)):
            with (
                mock.patch.object(cli, "discover", return_value=registry),
                mock.patch.object(Fake, "ram_boot", return_value=target, create=True),
                mock.patch.object(fastboot, "ram_boot", side_effect=effect) as boot,
                mock.patch.dict(os.environ, {disclaimer.ENV: "1"}),  # accepted, as an unattended run
                mock.patch.object(disclaimer.power, "battery_problem", return_value=""),
            ):
                code, _ = self.run_cli("ram-boot", "out/x.img")
            self.assertEqual(code, expected)
            self.assertEqual(boot.call_args[0][1], Path("out/x.img"))


if __name__ == "__main__":
    unittest.main()
