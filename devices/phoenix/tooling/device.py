# SPDX-License-Identifier: Unlicense
"""Independent host-side implementation of the documented PICO unlock workflow.

No upstream code or binaries are bundled. EDL is a separately installed bkerler/edl
or edl-ng CLI; partition names must come from the device's GPT, never a guessed slot.
The generic parts (adb/fastboot, EDL, verified backups) are overpressure's; this module adds
the PICO 4's identity, its engineering ABL's unlock token and the backup/stage/unlock/restore
workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from overpressure import android
from overpressure.android import run
from overpressure.backup import digest, partition_names, save_new, verified_backup
from overpressure.qualcomm import (
    backup_commands,
    capture_backup,
    chip_id,
    edl_command,
    write_verified,
)

from . import localconfig, unlockfiles

#: The SoC ID is a 32-bit number.
CHIP_ID_MAX = 0xFFFFFFFF


def select_device(tool: str, serial: str | None) -> str:
    """Return @p serial when it is attached and usable, else the only attached device's."""
    return android.select_device(tool, serial, run)


def check_unlock(fastboot: str, serial: str, *, critical: bool = False) -> None:
    """Raise unless the bootloader reports itself unlocked (and critical-unlocked when asked)."""
    android.check_unlock(fastboot, serial, critical=critical, runner=run)


def unlock_token(number: int) -> str:
    """Return the engineering ABL's OEM unlock token for a SoC ID."""
    # Protocol facts: mask the SoC ID, then substitute each hexadecimal digit.
    # Only the first sixteen symbols in the documented alphabet are reachable.
    if not 0 <= number <= CHIP_ID_MAX:
        msg = "Chip ID must fit in 32 bits"
        raise ValueError(msg)
    return "pico" + format(number & 0xF7F3F37F, "x").translate(str.maketrans("0123456789abcdef", "0XD9J6FB3ATQIHNM"))


#: What the whole path has run on (docs/device-preparation.md): (PICO OS version, ro.oem.state).
VERIFIED_FIRMWARES = {("5.13.7", "true")}
#: Panels the kernel's driver and tree know, as the bootloader names them to the stock kernel.
VERIFIED_PANELS = ("sharp_ls026b3sa",)


def check_firmware(version: str, oem_state: str) -> None:
    """Refuse a PICO OS build the whole path has not run on."""
    if (version, str(oem_state).lower()) not in VERIFIED_FIRMWARES:
        verified = ", ".join(f"{v} (ro.oem.state {s})" for v, s in sorted(VERIFIED_FIRMWARES))
        msg = (
            f"PICO OS {version} (ro.oem.state {oem_state}) is not verified, only {verified}: "
            "nothing was changed (docs/device-preparation.md)"
        )
        raise ValueError(msg)


def check_panel(cmdline: str) -> str:
    """Return the panel the bootloader named on the stock kernel's command line, if verified.

    Another panel stays dark on Valve's kernel: the PICO 4 has been sold with a second one, an
    Innolux NT57900.
    """
    found = re.search(r"\bmsm_drm\.dsi_display0=qcom,mdss_dsi_([a-z0-9_]+?)_\d+_(?:video|cmd)\b", cmdline)
    if not found:
        msg = "The kernel command line names no panel: refusing to install (docs/device-preparation.md)"
        raise ValueError(msg)
    if found.group(1) not in VERIFIED_PANELS:
        msg = (
            f"Panel {found.group(1)} is not verified, only {', '.join(VERIFIED_PANELS)}: "
            "refusing to install (docs/device-preparation.md)"
        )
        raise ValueError(msg)
    return found.group(1)


def read_identity(path: Path) -> dict:
    """Read the saved identity; refuse another headset or an unverified firmware."""
    identity = json.loads(path.read_text(encoding="utf-8"))
    if identity.get("schema") != 1 or not identity.get("serial"):
        msg = "Invalid device identity"
        raise ValueError(msg)
    fp = identity.get("fingerprint", "")
    if not re.fullmatch(r"Pico/[Pp]hoenix(?:_ovs)?/PICOA8110:10/[^/]+/[^\s]+", fp):
        msg = "This workflow supports original PICO 4 A8110/phoenix only"
        raise ValueError(msg)
    version = fp.split("/")[3]
    check_firmware(version, identity.get("oem_state", ""))
    chip_id(str(identity["chip_id"]))
    return identity


def confirm(args: argparse.Namespace, identity: dict) -> None:
    """Refuse to execute unless --confirm-device names the identity's serial."""
    if args.execute and args.confirm_device != identity["serial"]:
        msg = "Execution requires --confirm-device matching the saved ADB serial"
        raise ValueError(msg)


def dispatch(args: argparse.Namespace) -> None:  # noqa: C901, PLR0911, PLR0912, PLR0915  # the unlock workflow's commands, kept as verified on hardware
    """Run one device command; previews unless --execute."""
    if args.command == "token":
        print("fastboot oem " + unlock_token(chip_id(args.chip_id)) + " unlock")
        return
    if args.command == "fetch-unlock-files":
        unlockfiles.fetch(localconfig.load())
        return
    if args.command == "probe":
        serial = select_device(args.adb, args.serial)
        base = [args.adb, "-s", serial, "shell"]
        identity = {
            "schema": 1,
            "serial": serial,
            "fingerprint": run([*base, "getprop", "ro.build.fingerprint"]),
            "oem_state": run([*base, "getprop", "ro.oem.state"]),
            "chip_id": chip_id(run([*base, "cat", "/sys/devices/soc0/serial_number"])),
        }
        save_new(args.output, identity)
        print(f"Saved {args.output}; use 'python -m overpressure match' with fingerprint and oem_state")
        return
    if args.command == "check-unlocked":
        serial = select_device(args.fastboot, args.serial)
        check_unlock(args.fastboot, serial, critical=args.critical)
        return
    if args.command == "verify-backup":
        manifest = verified_backup(args.backup)
        print(f"Verified {len(manifest['files'])} files for {manifest['identity']['serial']}")
        return

    identity = read_identity(args.identity)
    confirm(args, identity)
    if args.command == "backup":
        commands = backup_commands(args)
        if not args.execute:
            for command in commands:
                print(subprocess.list2cmdline(command))
            return
        capture_backup(
            args.backup,
            commands,
            identity,
            backend=args.edl_backend,
            luns=args.edl_luns,
            include_userdata=args.include_userdata,
            runner=run,
        )
        print("Backup captured. Run verify-backup and retain an independent copy before modifying the device.")
        return

    manifest = verified_backup(args.backup)
    if manifest["identity"] != identity:
        msg = "Backup belongs to a different identity record"
        raise ValueError(msg)
    if args.command in ("stage", "restore-abl"):
        allowed = r"abl(?:_[ab])?" if args.command == "restore-abl" else r"(?:abl|devinfo)(?:_[ab])?"
        if not re.fullmatch(allowed, args.partition):
            msg = "Only explicit ABL/devinfo partition names are accepted"
            raise ValueError(msg)
        matches = partition_names(manifest, args.partition)
        if len(matches) != 1:
            msg = "Partition must have exactly one matching verified backup image"
            raise ValueError(msg)
        original = args.backup / matches[0]
        image = args.image if args.command == "stage" else original
        image = image.resolve()
        info = digest(image)
        if info["size"] > original.stat().st_size:
            msg = "Image exceeds backed-up partition size"
            raise ValueError(msg)
        if args.command == "stage" and info["sha256"] != args.sha256.lower():
            msg = "Engineering image SHA-256 does not match supplied value"
            raise ValueError(msg)
        command = edl_command(args, "w", args.partition, str(image))
        if not args.execute:
            print(subprocess.list2cmdline(command))
            return
        write_verified(
            command,
            lambda path: edl_command(args, "r", args.partition, path),
            image,
            readback=args.readback,
            backup=args.backup,
            runner=run,
        )
        print("Image bytes verified by EDL readback; no reboot performed.")
        return
    if args.command == "unlock":
        # Engineering ABL needs permissive mode before writing the unlock flags.
        # Otherwise 00000050 can leave apparently unlocked, nonpersistent state.
        commands = [
            ["oem", unlock_token(identity["chip_id"]), "unlock"],
            ["oem", "setenforce", "0"],
            ["flashing", "unlock"],
            ["flashing", "unlock_critical"],
            ["reboot", "bootloader"],
        ]
        if not args.execute:
            for command in commands:
                print(subprocess.list2cmdline([args.fastboot, "-s", args.serial or identity["serial"], *command]))
            return
        if not args.accept_data_loss:
            msg = "Unlock wipes user data; execution requires --accept-data-loss"
            raise ValueError(msg)
        serial = select_device(args.fastboot, args.serial or identity["serial"])
        for command in commands:
            run([args.fastboot, "-s", serial, *command], timeout=120)
        # fastboot itself waits for reconnection; subprocess timeout bounds that wait.
        check_unlock(args.fastboot, serial, critical=True)
        print(
            "Normal and critical unlock confirmed after bootloader reboot. "
            "See docs/device-preparation.md for reset, RAM testing and stock ABL restoration."
        )


def parser() -> argparse.ArgumentParser:
    """Return the device command line's parser."""
    ap = argparse.ArgumentParser(prog="python -m overpressure device", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    token = sub.add_parser("token", help="derive an OEM command offline; execute nothing")
    token.add_argument("chip_id")
    sub.add_parser("fetch-unlock-files", help="the engineering ABL and Firehose loader from PICO's public downloads")
    probe = sub.add_parser("probe", help="read stock headset identity via ADB")
    probe.add_argument("--adb", default="adb")
    probe.add_argument("--serial")
    probe.add_argument("--output", type=Path, default=Path("backup/device.json"))
    check = sub.add_parser("check-unlocked", help="read fastboot unlock state; fail closed")
    check.add_argument("--fastboot", default="fastboot")
    check.add_argument("--serial")
    check.add_argument("--critical", action="store_true")
    verify = sub.add_parser("verify-backup")
    verify.add_argument("--backup", type=Path, required=True)
    for name in ("backup", "stage", "unlock", "restore-abl"):
        p = sub.add_parser(name, help="preview by default; --execute operates on the headset")
        p.add_argument("--identity", type=Path, required=True)
        p.add_argument("--backup", type=Path, required=True)
        p.add_argument("--execute", action="store_true")
        p.add_argument("--confirm-device", help="saved ADB serial; EDL physical identity must be checked manually")
        if name == "unlock":
            p.add_argument("--fastboot", default="fastboot")
            p.add_argument("--serial", help="fastboot serial, if different from ADB")
            p.add_argument("--accept-data-loss", action="store_true")
        else:
            p.add_argument("--edl", default="edl", help="path to the separately installed EDL executable")
            p.add_argument("--edl-backend", choices=("bkerler", "edl-ng"), default="bkerler")
            p.add_argument("--edl-script", help="alternative: path to bkerler/edl's top-level edl.py")
            p.add_argument("--edl-python", default=sys.executable, help="Python environment for --edl-script")
            p.add_argument("--loader", required=True, help="user-supplied compatible signed Firehose ELF")
        if name == "backup":
            p.add_argument(
                "--include-userdata", action="store_true", help="full userdata partition, potentially hundreds of GB"
            )
            p.add_argument(
                "--edl-luns",
                nargs="+",
                type=int,
                choices=range(8),
                help="edl-ng only: all LUNs found by the device's printgpt scan",
            )
        if name in ("stage", "restore-abl"):
            p.add_argument("--partition", required=True, help="exact partition name from the captured GPT")
            p.add_argument("--readback", type=Path, required=True)
        if name == "stage":
            p.add_argument("--image", type=Path, required=True)
            p.add_argument("--sha256", required=True, help="expected engineering image hash from your trusted source")
    return ap


def main(argv: list[str] | None = None) -> int:
    """Run the device command line; errors end in a message and exit code 1."""
    args = parser().parse_args(argv)
    try:
        dispatch(args)
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
