"""SSH to a headset with the project's key and a known-hosts file of its own.

A headset's Linux stages make new host keys on every boot, so a run keeps its known hosts apart
from the user's and accepts a new key on first contact; the key file authenticates the host.
"""
from __future__ import annotations

from pathlib import Path
from typing import List


def argv(ssh: str, key: Path, known_hosts: Path, target: str, command: str, connect_timeout: int = 5) -> List:
    """The ssh command line that runs @p command on @p target (user@host), batch mode, key only."""
    known = Path(known_hosts).as_posix()
    return [ssh, "-i", key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", f"ConnectTimeout={connect_timeout}", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR", "-o", f'UserKnownHostsFile="{known}"', "-o", f'GlobalKnownHostsFile="{known}"',
            target, command]
