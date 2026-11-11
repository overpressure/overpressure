# SPDX-License-Identifier: Unlicense
"""SSH to a headset with the project's key and a known-hosts file of its own.

A headset's Linux stages make new host keys on every boot, so a run keeps its known hosts apart
from the user's and accepts a new key on first contact; the key file authenticates the host.
An image enrolls this PC's key and, when the owner names one, the owner's own (authorized_keys).
"""

from __future__ import annotations

from pathlib import Path


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
            if b"PRIVATE KEY" in line or not line.startswith((b"ssh-", b"ecdsa-", b"sk-")):
                msg = f"{setting}: {extra} is no public key file"
                raise ValueError(msg)
        keys += own
    return b"\n".join(keys) + b"\n"
