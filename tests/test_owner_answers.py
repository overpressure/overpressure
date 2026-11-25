# SPDX-License-Identifier: Unlicense
"""What an installer asks the owner: the Wi-Fi network (kept in memory only) and the owner's own SSH key."""

import unittest
from unittest import mock

from overpressure import ssh, wifi


def asking(*answers: dict) -> mock.Mock:
    """Return a task context whose asks answer @p answers in turn, with an empty memory and no recorded choice."""
    ctx = mock.Mock()
    ctx.ask.side_effect = list(answers)
    ctx.memory = {}
    ctx.recall.return_value = ""
    return ctx


class WifiTests(unittest.TestCase):
    def test_answers(self):
        self.assertEqual(wifi.problem("", ""), "")
        self.assertEqual(wifi.problem("net", ""), "")  # an open network
        self.assertEqual(wifi.problem("net", "12345678"), "")
        self.assertEqual(wifi.problem("net", "ab" * 32), "")  # the hexadecimal key
        self.assertIn("network name too", wifi.problem("", "12345678"))
        self.assertIn("8 to 63", wifi.problem("net", "short"))
        self.assertIn("32 bytes", wifi.problem("n" * 33, ""))
        self.assertIn("control", wifi.problem("net\n", "12345678"))

    def test_keyfile(self):
        text = wifi.keyfile(" back\\slash", "pass word!", "uuid-1").decode()
        self.assertIn("id=\\sback\\\\slash\n", text)
        self.assertIn("[wifi-security]\nkey-mgmt=wpa-psk\npsk=pass word!\n", text)
        self.assertIn("uuid=uuid-1\n", text)
        self.assertNotIn("wifi-security", wifi.keyfile("open", "", "u").decode())

    def test_a_wrong_answer_is_asked_again(self):
        ctx = asking({"ssid": "net", "password": "short"}, {"ssid": " net ", "password": "12345678"})
        self.assertEqual(wifi.ask(ctx), ("net", "12345678"))
        ctx.note.assert_called_once()

    def test_the_answer_stays_in_memory_and_a_skip_is_recorded(self):
        ctx = asking({"ssid": "net", "password": "12345678"})
        wifi.remember(ctx)
        self.assertEqual(ctx.memory["wifi"], ("net", "12345678"))
        ctx.remember.assert_called_once_with(wifi.JOIN)
        self.assertEqual(wifi.held(ctx), ("net", "12345678"))
        skipped = asking({"ssid": "", "password": ""})
        wifi.remember(skipped)
        skipped.remember.assert_called_once_with(wifi.SKIP)

    def test_a_later_run_asks_for_a_lost_password_but_not_for_a_skip(self):
        later = asking({"ssid": "net", "password": "12345678"})
        later.recall.return_value = wifi.JOIN
        self.assertEqual(wifi.held(later), ("net", "12345678"))  # the password was never written down
        later.ask.assert_called_once()
        skipped = asking()
        skipped.recall.return_value = wifi.SKIP
        self.assertEqual(wifi.held(skipped), ("", ""))
        skipped.ask.assert_not_called()


class PublicKeyTests(unittest.TestCase):
    def test_only_a_public_key_line_is_taken(self):
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample owner@pc"
        ctx = asking({"key": "-----BEGIN OPENSSH PRIVATE KEY-----"}, {"key": "ssh-ed25519"}, {"key": f" {key} "})
        self.assertEqual(ssh.ask_public_key(ctx), key)
        self.assertEqual(ctx.note.call_count, 2)

    def test_an_empty_answer_is_no_key(self):
        self.assertEqual(ssh.ask_public_key(asking({"key": "  "})), "")


if __name__ == "__main__":
    unittest.main()
