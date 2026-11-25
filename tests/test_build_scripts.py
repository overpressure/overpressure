# SPDX-License-Identifier: Unlicense
"""overpressure/build's scripts: what every headset's image gets from them, read from their text."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from overpressure import headset

BUILD = (Path(__file__).resolve().parents[1] / "overpressure/build/build-userspace.sh").read_text(encoding="utf-8")
IMAGE = (Path(__file__).resolve().parents[1] / "overpressure/build/build-system-image.sh").read_text(encoding="utf-8")


class UserspaceTests(unittest.TestCase):
    def test_steamos_readonly_is_the_headsets_and_the_image_can_be_written(self):
        # Valve's handles btrfs only: not extracted, and the headset's base package's in its place. The image has a
        # journal for the time steamos-readonly disable makes it writable, and stays reproducible (zeroed at mkfs).
        build = BUILD
        self.assertIn('ln -s "../libexec/$readonly_owner/steamos-readonly" "$T/usr/bin/steamos-readonly"', build)
        self.assertIn("[[ $RUNTIME != 1 ]] || readonly_owner=overpressure", build)
        image = IMAGE
        self.assertNotIn("^has_journal", image)
        self.assertIn("-J size=$JOURNAL_MIB", image)
        self.assertIn("lazy_journal_init=0", image)
        self.assertIn("E2FSPROGS_FAKE_TIME=$epoch mkfs.ext4", image)

    def test_pacmans_database_is_part_of_the_image(self):
        # It describes the image's packages: moved into it once the build's own pacman runs are done, named by
        # the image's pacman.conf, and no copy left under /var, which is the data partition's on the headset.
        build = BUILD
        move = build.index('mv "$T/var/lib/pacman" "$T/usr/lib/holo/pacmandb"')
        self.assertLess(build.index('in_t pacman -Q > "$build_root/packages.txt"'), move)
        self.assertLess(build.index('"$T"/var/lib/pacman/local/*/desc'), move)
        self.assertLess(move, build.index("archive=$output/$PREFIX-userspace.tar.zst"))
        self.assertIn("sed -i '/^\\[options\\]$/a DBPath = /usr/lib/holo/pacmandb/' \"$T/etc/pacman.conf\"", build)

    def test_no_account_has_a_usable_password_until_its_owner_sets_one(self):
        build = BUILD
        self.assertNotIn("passwd -d", build)
        self.assertIn("in_t usermod -p '*' root", build)
        # The session user's empty password only lets its owner's first passwd skip the current one:
        # authentication takes no empty password, and the build stops when it would.
        self.assertIn("in_t usermod -p '' \"$user\"", build)
        self.assertIn(
            "sed -i '/^auth[[:space:]].*pam_unix\\.so/s/[[:space:]]nullok\\b//' \"$T/etc/pam.d/system-auth\"", build
        )
        self.assertIn("system-auth's auth still takes an empty password", build)
        self.assertIn("'%wheel ALL=(ALL:ALL) ALL'", build)

    def test_the_session_user_gets_subordinate_ids(self):
        # Rootless podman maps Android's users into them; sysusers gives none.
        build = BUILD
        self.assertIn('usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$user"', build)

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"), "Linux")
    def test_every_layers_presets_are_applied(self):
        # Each layer's packages carry its prefix; an image that applied only the headset's presets started without
        # sshd, networkd or the USB link. Run the script's own block against presets of three layers.
        start = BUILD.index("preset_names=(")
        end = BUILD.index("\n", BUILD.index('in_t systemctl preset "${preset_units[@]}"'))
        with tempfile.TemporaryDirectory() as root:
            presets = Path(root, "usr/lib/systemd/system-preset")
            presets.mkdir(parents=True)
            files = {
                "50-overpressure-base.preset": "enable sshd.service\nenable overpressure-usb-link.service\n",
                "50-sm8250-base.preset": "# the DSPs\nenable sm8250-persist.service\ndisable foo.service\n",
                "50-pico4-base.preset": "enable  bluetooth.service extra\nenable sshd.service\n",
                "50-other-base.preset": "enable not-ours.service\n",
                "90-overpressure-base.preset": "enable not-50.service\n",
            }
            for name, text in files.items():
                (presets / name).write_bytes(text.encode())  # Python 3.9's write_text takes no newline
            script = 'in_t() { printf "%s\\n" "$@"; }\nlayers=(common sm8250 phoenix)\n' + BUILD[start:end]
            result = subprocess.run(  # noqa: S603  # bash, the script's own words
                ["bash", "-c", script],  # noqa: S607  # bash from PATH, as the build container has it
                env={**os.environ, "T": Path(root).as_posix(), "PREFIX": "pico4"},
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(
            result.stdout.split(),
            [
                "systemctl",
                "preset",
                "bluetooth.service",
                "overpressure-usb-link.service",
                "sm8250-persist.service",
                "sshd.service",
            ],
        )

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bash"), "Linux")
    def test_the_runtime_follows_the_headsets_interface_unless_it_decides(self):
        # As stage 1 (stage1.takes_runtime): on from 1.8 (headset.RUNTIME_SINCE), RUNTIME in build.sh decides.
        start = BUILD.index("if [[ -z $RUNTIME ]]; then")
        end = BUILD.index("\n", BUILD.index("[[ $RUNTIME == [01] ]]"))
        self.assertEqual(headset.RUNTIME_SINCE, (1, 8))
        cases: tuple[tuple[object, str, str], ...] = (
            ("1.8", "", "1"),
            ("1.10", "", "1"),
            ("1.7", "", "0"),
            (1, "", "0"),
            ("1.8", "0", "0"),
            ("1.7", "1", "1"),
        )
        for api, preset, expected in cases:
            with self.subTest(api=api, preset=preset), tempfile.TemporaryDirectory() as root:
                Path(root, "headset.json").write_text(json.dumps({"api": api}), encoding="utf-8")
                script = f'RUNTIME="{preset}"\n' + BUILD[start:end] + '\necho "$RUNTIME"'
                result = subprocess.run(  # noqa: S603  # bash, the script's own words
                    ["bash", "-c", script],  # noqa: S607  # bash from PATH, as the build container has it
                    env={**os.environ, "source_dir": Path(root).as_posix()},
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(result.stdout.strip(), expected)

    def test_adb_finds_protobuf_25_and_bionic_files_are_exempt(self):
        build = BUILD
        self.assertIn("-Sw -dd --noconfirm", build)
        self.assertIn("'usr/lib/libprotobuf.so.25*'", build)
        self.assertIn("optional+='|^deckard-(mesa|vulkan-layers)-android-aarch64 '", build)


if __name__ == "__main__":
    unittest.main()
