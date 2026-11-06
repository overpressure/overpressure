# SPDX-License-Identifier: Unlicense
"""Qualcomm devices: the SoC ID, Emergency Download mode (EDL) and its partition maps.

EDL runs through a separately installed client, edl-ng or bkerler/edl, with the device's own
signed Firehose loader; no upstream code or binaries are bundled. Partition names come from the
device's GPT (its rawprogram maps), never a guessed slot.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

from .android import executable
from .backup import digest
from .process import run

if TYPE_CHECKING:
    import argparse

EDL_USB = ("05c6", "9008")  # Qualcomm HS-USB QDLoader 9008
U32_MAX = 0xFFFFFFFF


def chip_id(value: str) -> int:
    """Return the SoC ID (/sys/devices/soc0/serial_number), decimal or 0x-hexadecimal, as a 32-bit number."""
    value = value.strip()
    if not re.fullmatch(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)", value):
        msg = "Chip ID must be decimal or 0x-prefixed hexadecimal"
        raise ValueError(msg)
    number = int(value, 16 if value.lower().startswith("0x") else 10)
    if not 0 <= number <= U32_MAX:
        msg = "Chip ID must fit in 32 bits"
        raise ValueError(msg)
    return number


def edl_devices() -> int:
    """Return the number of attached Qualcomm EDL (9008) devices."""
    if sys.platform == "win32":
        pattern = "USB\\VID_{}&PID_{}*".format(*EDL_USB).upper()
        output = run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"@(Get-PnpDevice -PresentOnly | Where-Object {{ $_.InstanceId -like '{pattern}' }}).Count",
            ],
            30,
        )
        return int(output.split()[-1]) if output.split() else 0
    count = 0
    for folder in Path("/sys/bus/usb/devices").glob("*"):
        try:
            ids = ((folder / "idVendor").read_text().strip(), (folder / "idProduct").read_text().strip())
        except OSError:
            continue
        count += ids == EDL_USB
    return count


def edl_command(args: argparse.Namespace, *words: str, memory: str = "ufs") -> list[str]:
    """Return an EDL command line for edl-ng or bkerler/edl, with the device's storage type.

    ARGS has edl_backend, edl, edl_script, edl_python and loader.
    """
    loader = Path(args.loader).resolve()
    digest(loader)
    if args.edl_backend == "edl-ng":
        if args.edl_script:
            msg = "edl-ng requires its native executable via --edl"
            raise ValueError(msg)
        operation = {"r": "read-part", "w": "write-part", "dump-rawprogram": "dump-rawprogram"}[words[0]]
        return [executable(args.edl), "--memory", memory.capitalize(), "--loader", str(loader), operation, *words[1:]]
    # Windows Git checkouts may materialize edlclient's entrypoint symlink as
    # plain text. Calling its top-level script avoids that broken entrypoint.
    tool = [executable(args.edl)]
    if args.edl_script:
        tool = [executable(args.edl_python), str(Path(args.edl_script).resolve())]
    return [*tool, *words, f"--memory={memory.lower()}", f"--loader={loader}"]


def backup_commands(args: argparse.Namespace) -> list[list[str]]:
    """Return the EDL command lines of a full backup (ARGS as edl_command's, with the backup's)."""
    if args.edl_backend == "edl-ng":
        if not args.edl_luns or len(set(args.edl_luns)) != len(args.edl_luns):
            msg = "edl-ng backup requires distinct --edl-luns from the device's GPT scan"
            raise ValueError(msg)
        commands = [
            edl_command(args, "dump-rawprogram", str((args.backup / f"lun{lun}").resolve()), "--lun", str(lun))
            for lun in args.edl_luns
        ]
        if not args.include_userdata:
            for command in commands:
                command.extend(["--skip", "userdata"])
        return commands
    if args.edl_luns:
        msg = "--edl-luns applies only to the edl-ng backend"
        raise ValueError(msg)
    command = edl_command(args, "rl", str(args.backup.resolve()), "--genxml")
    if not args.include_userdata:
        command.append("--skip=userdata")
    return [command]


def validate_native_dump(folder: Path, luns: list[int], *, include_userdata: bool) -> None:
    """Reject missing or truncated files against each native client's rawprogram map."""
    for lun in luns:
        directory = (folder / f"lun{lun}").resolve()
        rows = ET.parse(directory / f"rawprogram{lun}.xml").getroot().findall("program")  # noqa: S314  # the EDL client's own map
        if not rows:
            msg = f"Empty rawprogram map for LUN {lun}"
            raise ValueError(msg)
        for row in rows:
            if not include_userdata and row.get("label") == "userdata":
                continue
            name = row.attrib["filename"]
            path = (directory / name).resolve()
            size = int(row.attrib["num_partition_sectors"]) * int(row.attrib["SECTOR_SIZE_IN_BYTES"])
            if (
                directory not in path.parents
                or size <= 0
                or int(row.attrib["physical_partition_number"]) != lun
                or not path.is_file()
                or path.stat().st_size != size
            ):
                msg = f"Incomplete dump for LUN {lun}: {name}"
                raise ValueError(msg)


def partition_size(backup: Path, label: str) -> int:
    """Return a partition's size from the backup's rawprogram maps.

    Also for userdata, which a backup need not contain: exactly one partition of that name across
    all LUNs.
    """
    sizes = [
        int(row.attrib["num_partition_sectors"]) * int(row.attrib["SECTOR_SIZE_IN_BYTES"])
        for xml in sorted(backup.glob("lun*/rawprogram*.xml"))
        for row in ET.parse(xml).getroot().findall("program")  # noqa: S314  # the EDL client's own maps
        if row.get("label") == label
    ]
    if len(sizes) != 1 or sizes[0] <= 0:
        msg = f"the backup's partition maps hold {len(sizes)} partitions named {label}"
        raise ValueError(msg)
    return sizes[0]
