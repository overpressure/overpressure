# SPDX-License-Identifier: Unlicense
"""SSH to a headset with the project's key and a known-hosts file of its own.

A headset's Linux stages make new host keys on every boot, so a run keeps its known hosts apart
from the user's and accepts a new key on first contact; the key file authenticates the host.
An image enrolls this PC's key and, when the owner names one, the owner's own (authorized_keys).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .install import Ask, Field

if TYPE_CHECKING:
    from .install import Context


def argv(  # noqa: PLR0913  # the ssh setup, the target and the command; the timeout keyword-only
    ssh: str, key: Path, known_hosts: Path, target: str, command: str, *, connect_timeout: int = 5
) -> list:
    """Return the ssh command line that runs @p command on @p target (user@host), batch mode, key only."""
    known = Path(known_hosts).as_posix()
    return [
        ssh,
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        # A USB link that vanishes (the headset restarts) ends no TCP connection on Linux: the
        # keepalives notice it within seconds. A busy command still answers them.
        "-o",
        "ServerAliveInterval=3",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f'UserKnownHostsFile="{known}"',
        "-o",
        f'GlobalKnownHostsFile="{known}"',
        target,
        command,
    ]


def is_public_key(line: bytes) -> bool:
    """Tell whether LINE is one OpenSSH public key (not a private one): what authorized_keys takes."""
    return b"PRIVATE KEY" not in line and line.strip().startswith((b"ssh-", b"ecdsa-", b"sk-"))


def authorized_keys(this_pc: bytes, extra: Path | None, setting: str) -> bytes:
    """Return the authorized_keys a headset's image enrolls: this PC's key, and those of @p extra.

    @p extra is the owner's own public key file (the .env key @p setting names it), one key per
    line; a missing file, a private key or a line that is no public key raises ValueError.
    """
    keys = [this_pc.strip()]
    if extra is not None:
        if not extra.is_file():
            msg = f"{setting}: no file {extra}"
            raise ValueError(msg)
        own = [line.strip() for line in extra.read_bytes().splitlines() if line.strip()]
        for line in own:
            if not is_public_key(line):
                msg = f"{setting}: {extra} is no public key file"
                raise ValueError(msg)
        keys += own
    return b"\n".join(keys) + b"\n"


# -- the owner's own key, asked by an installer (since interface 1.6) -------------------------------
#: The ask for the owner's own public key, which a headset's install enrolls beside this computer's.
PUBLIC_KEY = Ask(
    "ssh-key",
    "form",
    "Your SSH key",
    "To log in to the headset from your own computer over SSH, paste your public key: the one line of your "
    "id_ed25519.pub (or another .pub file) in your .ssh folder, starting with ssh-ed25519, ssh-rsa or "
    "ecdsa-sha2. The headset takes it besides this computer's own key; it takes no password. Leave it empty "
    "to skip.",
    (Field("key", "Public key", optional=True),),
)
NO_PUBLIC_KEY = (
    "That is no public key: paste the one line of a .pub file, starting with ssh-ed25519, ssh-rsa or ecdsa-sha2. "
    "Never the private key."
)


def ask_public_key(ctx: Context) -> str:
    """Ask for the owner's own public key until the answer is one; return its line, empty for none."""
    while True:
        line = ctx.ask(PUBLIC_KEY)["key"].strip()
        if not line or (is_public_key(line.encode()) and len(line.split()) >= 2):  # noqa: PLR2004  # kind and key
            return line
        ctx.note(NO_PUBLIC_KEY, "warning")
