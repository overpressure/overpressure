# SPDX-License-Identifier: Unlicense
"""The first-run setup's helpers and texts (docs/session.md#first-run-setup)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
LANGUAGE = ROOTFS / "usr/libexec/pico4/steam-language"
TIMEZONE = ROOTFS / "usr/bin/steamos-polkit-helpers/steamos-set-timezone"
STRINGS = ROOT / "src/xr/pico4-onboarding.strings"
SETUP_H = ROOT / "src/xr/pico4_setup.h"
ONBOARDING = ROOT / "src/xr/pico4-onboarding.cpp"

_loader = importlib.machinery.SourceFileLoader("steam_language", str(LANGUAGE))
_spec = importlib.util.spec_from_loader("steam_language", _loader)
assert _spec is not None
tool = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = tool
_loader.exec_module(tool)

#: A registry as the client writes it at its first start (made-up values).
REGISTRY = """"Registry"
{
\t"HKLM"
\t{
\t\t"Software"
\t\t{
\t\t\t"Valve"
\t\t\t{
\t\t\t\t"Steam"
\t\t\t\t{
\t\t\t\t\t"SteamPID"\t\t"1234"
\t\t\t\t}
\t\t\t}
\t\t}
\t}
\t"HKCU"
\t{
\t\t"Software"
\t\t{
\t\t\t"Valve"
\t\t\t{
\t\t\t\t"Steamsteamglobal"
\t\t\t\t{
\t\t\t\t\t"language"\t\t"english"
\t\t\t\t}
\t\t\t\t"Steam"
\t\t\t\t{
\t\t\t\t\t"language"\t\t"english"
\t\t\t\t\t"AutoLoginUser"\t\t""
\t\t\t\t\t"path"\t\t"C:\\\\a \\"b\\""
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""


def sections(text: str) -> dict[str, dict[str, str]]:
    """Read the strings file as {language: {key: value}}."""
    out: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[([a-z]+)\]", line)
        if m:
            current = out.setdefault(m.group(1), {})
            continue
        key, sep, value = line.partition(" = ")
        assert current is not None, line
        assert sep, line
        assert key not in current, f"{key} twice"
        current[key] = value
    return out


class LanguageTests(unittest.TestCase):
    def test_both_places_change_and_nothing_else(self):
        new = tool.set_language(REGISTRY, "german")
        self.assertEqual(new.count('"language"\t\t"german"'), 2)
        self.assertNotIn('"english"', new)
        # Everything else as it was, in its order, quotes and backslashes included.
        self.assertEqual(new.replace('"german"', '"english"'), REGISTRY)

    def test_missing_places_are_made(self):
        new = tool.set_language('"Registry"\n{\n\t"HKCU"\n\t{\n\t}\n}\n', "koreana")
        root = tool.parse(new)
        for path in tool.PATHS:
            node = tool.child(root, "Registry")
            for key in path:
                node = tool.child(node, key)
            self.assertIn(("language", "koreana"), node)
        self.assertEqual(tool.parse(tool.set_language("", "thai"))[0][0], "Registry")

    def test_keys_ignore_case(self):
        new = tool.set_language(REGISTRY.replace('"Steam"\n', '"steam"\n'), "french")
        self.assertEqual(new.count('"language"\t\t"french"'), 2)

    def test_a_broken_registry_is_not_written(self):
        for text in ('"Registry"\n{\n', '"Registry"\n}\n', '"Registry"\n{\n"key"\n}\n'):
            with self.assertRaises(ValueError):
                tool.set_language(text, "german")

    def test_the_client_restarts_around_the_change(self):
        calls: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / "registry.vdf"
            registry.write_text(REGISTRY, encoding="utf-8")
            with (
                mock.patch.object(tool, "systemctl", side_effect=lambda *a: calls.append(a)),
                mock.patch.object(tool, "REGISTRY", registry),
                mock.patch.object(tool, "client_running", return_value=True),
            ):
                self.assertEqual(tool.main(["steam-language", "german"]), 0)
            self.assertIn('"language"\t\t"german"', registry.read_text(encoding="utf-8"))
        self.assertEqual(calls, [("stop", "steam.service"), ("start", "steam.service")])

    def test_a_client_still_to_start_only_gets_the_file(self):
        # During the first-run setup the client waits to start: it starts in the language.
        with tempfile.TemporaryDirectory() as temp:
            registry = Path(temp) / ".steam/registry.vdf"
            with (
                mock.patch.object(tool, "systemctl") as systemctl,
                mock.patch.object(tool, "REGISTRY", registry),
                mock.patch.object(tool, "client_running", return_value=False),
            ):
                self.assertEqual(tool.main(["steam-language", "japanese"]), 0)
            self.assertIn('"language"\t\t"japanese"', registry.read_text(encoding="utf-8"))
        systemctl.assert_not_called()

    def test_only_the_clients_languages(self):
        with mock.patch.object(tool, "systemctl") as systemctl:
            self.assertEqual(tool.main(["steam-language", "klingon"]), 2)
            self.assertEqual(tool.main(["steam-language"]), 2)
        systemctl.assert_not_called()

    def test_the_same_languages_as_the_panel(self):
        header = SETUP_H.read_text(encoding="utf-8")
        table = re.findall(r'\{"([a-z]+)", "[^"]+"\},', header[header.index("pico4_setup_languages[") :])
        self.assertEqual(sorted(table[: len(tool.LANGUAGES)]), sorted(tool.LANGUAGES))
        self.assertEqual(len(tool.LANGUAGES), 31)


class WaitTests(unittest.TestCase):
    SCRIPT = ROOTFS / "usr/libexec/pico4/wait-first-run"

    def test_the_client_waits_for_the_setup(self):
        conf = (ROOTFS / "usr/lib/systemd/user/steam.service.d/55-pico4-first-run.conf").read_text(encoding="utf-8")
        self.assertIn("ExecStartPre=/usr/libexec/pico4/wait-first-run\n", conf)
        self.assertIn("TimeoutStartSec=infinity\n", conf)
        # Its drop-in sorts after the other one, its step after the client's own unpacking.
        self.assertTrue((ROOTFS / "usr/lib/systemd/user/steam.service.d/50-pico4.conf").exists())
        script = self.SCRIPT.read_text(encoding="utf-8")
        for released_by in ("setup-done", '"CompletedOOBE"', "is-failed --quiet pico4-onboarding.service"):
            self.assertIn(released_by, script)

    def run_script(self, home: str) -> int:
        return subprocess.run(  # noqa: S603  # bash from PATH runs the script under test
            ["bash", str(self.SCRIPT)],  # noqa: S607  # bash from PATH
            env={"HOME": home, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            timeout=10,
            check=False,
        ).returncode

    def test_the_mark_and_a_sign_in_release_it(self):
        if shutil.which("bash") is None or sys.platform == "win32":
            self.skipTest("needs bash")
        with tempfile.TemporaryDirectory() as home:
            mark = Path(home) / ".local/share/pico4/setup-done"
            mark.parent.mkdir(parents=True)
            mark.write_text("1\n", encoding="utf-8")
            self.assertEqual(self.run_script(home), 0)
            mark.unlink()
            registry = Path(home) / ".steam/registry.vdf"
            registry.parent.mkdir()
            registry.write_text(REGISTRY.replace('"AutoLoginUser"\t\t""', '"CompletedOOBE"\t\t"1"'), encoding="utf-8")
            self.assertEqual(self.run_script(home), 0)


class TimezoneTests(unittest.TestCase):
    def test_the_helper_takes_only_zone_names(self):
        text = TIMEZONE.read_text(encoding="utf-8")
        self.assertIn("exec timedatectl set-timezone", text)
        if shutil.which("bash") is None or sys.platform == "win32":
            self.skipTest("needs bash")
        for zone in ("", "/etc/passwd", "../../etc/passwd", "Europe/Paris;reboot", "Mars/Olympus"):
            # The helper under test, with made-up zones.
            result = subprocess.run(  # noqa: S603  # bash from PATH runs the helper
                ["bash", str(TIMEZONE), zone],  # noqa: S607  # bash from PATH
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2, zone)

    def test_polkit_allows_only_setting_the_zone(self):
        rule = (ROOTFS / "usr/share/polkit-1/rules.d/50-pico4-timezone.rules").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'action\.id === "([^"]+)"', rule), ["org.freedesktop.timedate1.set-timezone"])
        self.assertIn('subject.user === "steamos"', rule)

    def test_the_panel_calls_steams_helper(self):
        # Steam's own time-zone setting runs the same path (steamui.so, on SteamOS).
        self.assertIn('"/usr/bin/steamos-polkit-helpers/steamos-set-timezone"', ONBOARDING.read_text(encoding="utf-8"))

    def test_every_zone_is_a_name_the_helper_takes(self):
        zones = re.findall(r'\{-?\d+, "([^"]+)", "', SETUP_H.read_text(encoding="utf-8"))
        self.assertEqual(len(zones), 62)
        for zone in zones:
            self.assertRegex(zone, r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)+$")


class StringsTests(unittest.TestCase):
    def setUp(self):
        self.text = STRINGS.read_text(encoding="utf-8")
        self.languages = sections(self.text)

    def test_every_key_the_panel_uses_is_in_english(self):
        source = ONBOARDING.read_text(encoding="utf-8")
        english = self.languages["english"]
        asked = set(re.findall(r'our\("([a-z_]+)"\)', source))
        self.assertFalse(asked - set(english), "keys without English text")
        # Each text is named in the source, also where a condition picks one of two.
        self.assertFalse({key for key in english if f'"{key}"' not in source}, "texts nothing uses")

    def test_every_language_has_every_text_with_its_placeholders(self):
        english = self.languages["english"]
        self.assertEqual(sorted(self.languages), sorted(tool.LANGUAGES))
        for language, texts in self.languages.items():
            self.assertEqual(sorted(texts), sorted(english), language)
            for key, value in texts.items():
                self.assertEqual(value.count("%s"), english[key].count("%s"), f"{language} {key}")
                self.assertTrue(value.strip(), f"{language} {key}")

    def test_the_pico_4_not_the_frame(self):
        self.assertNotIn("Frame", self.text)

    def test_lf_and_utf8(self):
        data = STRINGS.read_bytes()
        self.assertNotIn(b"\r", data)
        data.decode("utf-8")


if __name__ == "__main__":
    unittest.main()
