# SPDX-License-Identifier: Unlicense
"""overpressure/build's scripts: what every headset's image gets from them, read from their text."""

import unittest
from pathlib import Path

BUILD = (Path(__file__).resolve().parents[1] / "overpressure/build/build-userspace.sh").read_text(encoding="utf-8")
IMAGE = (Path(__file__).resolve().parents[1] / "overpressure/build/build-system-image.sh").read_text(encoding="utf-8")


class UserspaceTests(unittest.TestCase):
    def test_steamos_readonly_is_the_headsets_and_the_image_can_be_written(self):
        # Valve's handles btrfs only: not extracted, and the headset's base package's in its place. The image has a
        # journal for the time steamos-readonly disable makes it writable, and stays reproducible (zeroed at mkfs).
        build = BUILD
        self.assertIn('ln -s "../libexec/$PREFIX/steamos-readonly" "$T/usr/bin/steamos-readonly"', build)
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

    def test_adb_finds_protobuf_25_and_bionic_files_are_exempt(self):
        build = BUILD
        self.assertIn("-Sw -dd --noconfirm", build)
        self.assertIn("'usr/lib/libprotobuf.so.25*'", build)
        self.assertIn("optional+='|^deckard-(mesa|vulkan-layers)-android-aarch64 '", build)


if __name__ == "__main__":
    unittest.main()
