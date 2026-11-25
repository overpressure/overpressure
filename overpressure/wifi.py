# SPDX-License-Identifier: Unlicense
"""The owner's Wi-Fi for a headset's installed system: asked once, kept in memory only, written as a keyfile.

The installer asks for the network early, so the owner can leave during the install (ask, then remember a skip),
and writes it to the installed system at the end as a NetworkManager keyfile (WPA-PSK, or an open network) over
SSH's standard input (COMMAND): neither the name nor the password is in a command line, a log or .env. After a
restart the password is gone, never written down: the install asks again (held).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .install import Ask, Field

if TYPE_CHECKING:
    from .install import Context

#: Where the keyfile goes on the installed system, and the command that puts it there from SSH's stdin.
FILE = "/etc/NetworkManager/system-connections/overpressure-wifi.nmconnection"
COMMAND = f"install -D -m 600 /dev/stdin {FILE} && nmcli connection reload"
SSID_BYTES = 32
PSK_LENGTHS = (8, 63)
PSK_HEX = 64
#: What the Wi-Fi ask's answer was, in the state (the network and its password stay in memory).
JOIN, SKIP = "join", "skip"
ASK = Ask(
    "wifi",
    "form",
    "Wi-Fi",
    "The headset can join your Wi-Fi network as soon as it starts. The network name and password go to "
    "the headset only; this computer does not keep them. Leave both empty to skip: the headset's settings "
    "join a network later too.",
    (Field("ssid", "Network name", optional=True), Field("password", "Password", secret=True, optional=True)),
)


def problem(ssid: str, password: str) -> str:
    """Return what is wrong with a Wi-Fi answer, in plain words; empty when it can be used."""
    if not ssid:
        return "Enter the network name too, or leave both empty to skip Wi-Fi." if password else ""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in ssid + password):  # noqa: PLR2004  # control characters
        return "The network name and password cannot hold control characters."
    if len(ssid.encode()) > SSID_BYTES:
        return f"A network name has at most {SSID_BYTES} bytes."
    hex_key = len(password) == PSK_HEX and all(c in "0123456789abcdefABCDEF" for c in password)
    if password and not hex_key and not PSK_LENGTHS[0] <= len(password) <= PSK_LENGTHS[1]:
        return f"A Wi-Fi password has {PSK_LENGTHS[0]} to {PSK_LENGTHS[1]} characters."
    return ""


def _keyfile_value(value: str) -> str:
    """Return @p value escaped for a key file (GLib's rules: backslashes, a leading space)."""
    value = value.replace("\\", "\\\\")
    return "\\s" + value[1:] if value.startswith(" ") else value


def keyfile(ssid: str, password: str, connection: str) -> bytes:
    """Return a NetworkManager keyfile for @p ssid: WPA-PSK with @p password, an open network without one."""
    lines = [
        "[connection]",
        f"id={_keyfile_value(ssid)}",
        f"uuid={connection}",
        "type=wifi",
        "autoconnect=true",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={_keyfile_value(ssid)}",
        "",
    ]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={_keyfile_value(password)}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines).encode()


def ask(ctx: Context) -> tuple[str, str]:
    """Ask for the Wi-Fi network until the answer can be used; empty: no Wi-Fi."""
    while True:
        values = ctx.ask(ASK)
        ssid, password = values["ssid"].strip(), values["password"]
        wrong = problem(ssid, password)
        if not wrong:
            return ssid, password
        ctx.note(wrong, "warning")


def remember(ctx: Context) -> None:
    """Ask for the Wi-Fi now, so the owner can leave during the install: the answer in memory, a skip in the state."""
    answer = ask(ctx)
    ctx.memory["wifi"] = answer
    ctx.remember(JOIN if answer[0] else SKIP)


def held(ctx: Context, task: str = "wifi") -> tuple[str, str]:
    """Return the answer remember() kept; a skip in an earlier run is not asked twice, a lost password is asked again.

    @p task is the key of the task that called remember().
    """
    answer = ctx.memory.get("wifi")
    if answer is None and ctx.recall(task) == SKIP:
        return "", ""
    return answer or ask(ctx)
