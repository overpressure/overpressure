"""The headset interface, the registry, the step runner, the command line and the GUI."""
import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from overpressure import REPO, __main__ as cli, runner
from overpressure.headset import Command, Headset, Info, Param, Registry, Step, discover


class Fake(Headset):
    info = Info("fake", "Fake headset")

    def commands(self):
        return {"build": Command("image", "builds", True)}

    def steps(self):
        return (Step("img", "Image", "Image", ("build", "--firmware", "{firmware}", "{fast}"),
                     params=(Param("firmware", "Firmware", "a"), Param("fast", "Fast", "0", "flag", option="--fast"))),
                Step("us", "Userspace", "Image", ("scripts/build.sh", "{root}", "assets/fw-{version}", "{out}"),
                     params=(Param("root", "Root", "/var/tmp/b"), Param("version", "Version", "1.2"),
                             Param("out", "Out", "")),
                     script=True, linux_root=True))


class StepTests(unittest.TestCase):
    def test_parameters_flags_and_empty_words(self):
        img, us = Fake().steps()
        self.assertEqual(img.command({}), ["build", "--firmware", "a"])
        self.assertEqual(img.command({"firmware": "b", "fast": "1"}), ["build", "--firmware", "b", "--fast"])
        # Braces inside a word take the value too; an empty parameter word is left out.
        self.assertEqual(us.command({}), ["scripts/build.sh", "/var/tmp/b", "assets/fw-1.2"])


class RunnerTests(unittest.TestCase):
    def test_windows_runs_linux_root_steps_in_wsl(self):
        h = Fake()
        _, us = h.steps()
        argv = runner.process(h, us, {}, platform="win32")
        self.assertEqual(argv[:6], ["wsl", "-u", "root", "--cd", str(REPO), "--"])
        self.assertEqual(argv[6], "bash")
        self.assertTrue(argv[7].startswith("/mnt/") and argv[7].endswith("devices/fake/scripts/build.sh"))

    def test_linux_asks_sudo_unless_root(self):
        h = Fake()
        _, us = h.steps()
        self.assertEqual(runner.process(h, us, {}, platform="linux", is_root=False)[:3],
                         ["sudo", "bash", "devices/fake/scripts/build.sh"])
        self.assertEqual(runner.process(h, us, {}, platform="linux", is_root=True)[0], "bash")

    def test_commands_go_through_this_command_line(self):
        h = Fake()
        img, _ = h.steps()
        self.assertEqual(runner.process(h, img, {}, platform="linux", python="py"),
                         ["py", "-m", "overpressure", "--device", "fake", "build", "--firmware", "a"])

    def test_wsl_path(self):
        self.assertEqual(runner.wsl_path(Path("C:/Users/x/repo")), "/mnt/c/Users/x/repo")


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
                self.assertIn(step.group, ("Image", "Headset"))
                words = step.command({})
                if step.script:
                    self.assertTrue((headset.root / words[0]).exists(), (codename, step.key))
                else:
                    self.assertIn(words[0], headset.commands(), (codename, step.key))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.dict(os.environ, {"OVERPRESSURE_DEVICE": ""}):
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
        with mock.patch.object(Headset, "main", return_value=7) as main, \
                mock.patch.object(cli, "discover", return_value=Registry({"fake": h})):
            code, _ = self.run_cli("build", "--firmware", "x")
        self.assertEqual(code, 7)
        main.assert_called_once_with(["build", "--firmware", "x"])


class GuiTests(unittest.TestCase):
    def test_window_shows_every_step_with_its_command_line(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as exc:  # no Tk or no display
            self.skipTest(f"no Tk display: {exc}")
        from overpressure import gui
        try:
            app = gui.App(root, Registry({"fake": Fake()}), None)
            root.update()
            app.tree.selection_set("img")
            root.update()
            app.vars["fast"].set("1")
            root.update()
            self.assertIn("build --firmware a --fast", app.command.get("1.0", "end"))
            app.tree.selection_set("us")
            root.update()
            self.assertEqual(sorted(app.vars), ["out", "root", "version"])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
