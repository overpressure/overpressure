# SPDX-License-Identifier: Unlicense
"""overpressure.ssh: the command line with the project's key, and the keys an image enrolls."""

import tempfile
import unittest
from pathlib import Path

from overpressure import ssh


class SshTests(unittest.TestCase):
    def test_batch_mode_key_only_and_its_own_known_hosts(self):
        argv = ssh.argv("ssh", Path("key"), Path("dir/known_hosts"), "root@headset", "uname -a")
        self.assertEqual(argv[-2:], ["root@headset", "uname -a"])
        for option in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=accept-new"):
            self.assertIn(option, argv)
        self.assertIn('UserKnownHostsFile="dir/known_hosts"', argv)

    def test_authorized_keys_are_this_pcs_and_the_owners(self):
        self.assertEqual(ssh.authorized_keys(b"ssh-ed25519 PC\n", None, "X_KEY"), b"ssh-ed25519 PC\n")
        with tempfile.TemporaryDirectory() as temp:
            own = Path(temp) / "own.pub"
            with self.assertRaisesRegex(ValueError, "X_KEY: no file"):
                ssh.authorized_keys(b"ssh-ed25519 PC", own, "X_KEY")
            own.write_bytes(b"ssh-ed25519 OWN me\r\n\n  ecdsa-sha2-nistp256 TWO you\n")
            self.assertEqual(
                ssh.authorized_keys(b"ssh-ed25519 PC\n", own, "X_KEY"),
                b"ssh-ed25519 PC\nssh-ed25519 OWN me\necdsa-sha2-nistp256 TWO you\n",
            )
            for bad in (b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n", b"ssh-ed25519 A\nnot a key\n"):
                own.write_bytes(bad)
                with self.assertRaisesRegex(ValueError, "X_KEY: .* is no public key file"):
                    ssh.authorized_keys(b"ssh-ed25519 PC", own, "X_KEY")


if __name__ == "__main__":
    unittest.main()
