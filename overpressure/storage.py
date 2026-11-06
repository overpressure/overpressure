# SPDX-License-Identifier: Unlicense
"""The read-only storage policy a headset's Linux stages enforce, checked from what they report.

A stage 1 runs from RAM with every physical block device switched read-only, so a test or a crash
cannot change the headset's partitions. An installed system keeps its image read-only too, and
writes only to its data partition. A run reads the block devices' read-only flags (lines
`BLOCK NAME 0|1`) and /proc/mounts from the headset and refuses anything else.
"""

from __future__ import annotations

import re

from .evidence import require

#: A row of the storage inventory: BLOCK, the device's name and its read-only flag.
BLOCK_FIELDS = 3
#: A row of /proc/mounts: device, mount point, type, options, dump and pass.
MOUNT_FIELDS = 6
MOUNT_POINT = 1
#: Where an installed system mounts its data partition.
DATA = "/data"


def validate_storage(output: str, data: str = "") -> None:
    """Require every physical block device read-only, except the installed system's data partition.

    On the installed system its data partition (@p data, e.g. sda16) is the one exception and
    must be writable: read-only under the mounted file system, its writeback fails and the data
    is lost. A mainline kernel counts a disk's read-only flag for its partitions too, so there the
    data partition's disk is writable as well; its other partitions keep their own flags.
    """
    rows = [line.split() for line in output.splitlines()]
    require(
        rows and all(len(r) == BLOCK_FIELDS and r[0] == "BLOCK" and r[2] in ("0", "1") for r in rows),
        "Storage protection failed: missing or invalid block-device inventory",
    )
    physical = [r for r in rows if not r[1].startswith(("loop", "ram", "zram", "nbd"))]
    require(physical, "No physical storage detected")
    data_disk = re.sub(r"\d+$", "", data) if data else ""
    require(
        all(r[2] == "1" for r in physical if r[1] not in (data, data_disk)),
        "Storage protection failed: writable disk or partition",
    )
    if data:
        require(
            [r[2] for r in physical if r[1] == data] == ["0"], "The installed system's data partition is not writable"
        )


def data_device(mounts: str, mount_point: str = DATA) -> str:
    """Return the block device (sda16) mounted at @p mount_point, or empty."""
    for row in mounts.splitlines():
        fields = row.split()
        if len(fields) > MOUNT_POINT and fields[MOUNT_POINT] == mount_point and fields[0].startswith("/dev/"):
            return fields[0][len("/dev/") :]
    return ""


def validate_mounts(output: str, *, installed: bool = False, data: str = DATA) -> None:
    """Refuse a writable device-backed mount: stage 1 runs from RAM, the installed system read-only.

    The installed system's one writable device is the one at @p data, wherever it is bound.
    """
    rows = [row.split() for row in output.splitlines()]
    require(rows and all(len(r) == MOUNT_FIELDS for r in rows), "Invalid mount inventory")
    roots = [r for r in rows if r[1] == "/"]
    writable = [r for r in rows if r[0].startswith("/dev/") and "rw" in r[3].split(",")]
    if installed:
        # The installed system: its image read-only; writable only the data partition, the device
        # at the data mount point, wherever it is bound (/var, /home, SteamOS's offload binds...).
        require(
            roots and all(r[0].startswith("/dev/") and "ro" in r[3].split(",") for r in roots),
            "The installed system's root is not mounted read-only",
        )
        devices = {r[0] for r in writable if r[1] == data}
        require(
            len(devices) == 1 and {r[0] for r in writable} == devices,
            "A device-backed filesystem other than the data partition is mounted writable",
        )
        return
    require(roots and all(r[2] in ("rootfs", "tmpfs", "ramfs") for r in roots), "Root filesystem is not RAM-backed")
    require(not writable, "A device-backed filesystem is mounted writable")
