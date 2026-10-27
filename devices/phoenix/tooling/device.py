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
from overpressure.backup import digest, save_new, verified_backup
from overpressure.qualcomm import backup_commands, chip_id, edl_command, validate_native_dump


def select_device(tool: str, serial: str | None) -> str:
    return android.select_device(tool, serial, run)


def check_unlock(fastboot: str, serial: str, critical: bool = False) -> None:
    android.check_unlock(fastboot, serial, critical, run)


def unlock_token(number: int) -> str:
    # Protocol facts: mask the SoC ID, then substitute each hexadecimal digit.
    # Only the first sixteen symbols in the documented alphabet are reachable.
    if not 0 <= number <= 0xFFFFFFFF:
        raise ValueError("Chip ID must fit in 32 bits")
    return "pico" + format(number & 0xF7F3F37F, "x").translate(
        str.maketrans("0123456789abcdef", "0XD9J6FB3ATQIHNM"))


def read_identity(path: Path) -> dict:
    identity = json.loads(path.read_text(encoding="utf-8"))
    if identity.get("schema") != 1 or not identity.get("serial"):
        raise ValueError("Invalid device identity")
    fp = identity.get("fingerprint", "")
    if not re.fullmatch(r"Pico/[Pp]hoenix(?:_ovs)?/PICOA8110:10/[^/]+/[^\s]+", fp):
        raise ValueError("This workflow supports original PICO 4 A8110/phoenix only")
    version = fp.split("/")[3]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or tuple(map(int, version.split("."))) > (5, 13, 8):
        raise ValueError("Firmware is outside the upstream reported range (through 5.13.8)")
    chip_id(str(identity["chip_id"]))
    return identity


def confirm(args, identity: dict) -> None:
    if args.execute and args.confirm_device != identity["serial"]:
        raise ValueError("Execution requires --confirm-device matching the saved ADB serial")


def dispatch(args) -> None:
    if args.command == "token":
        print("fastboot oem " + unlock_token(chip_id(args.chip_id)) + " unlock")
        return
    if args.command == "probe":
        serial = select_device(args.adb, args.serial)
        base = [args.adb, "-s", serial, "shell"]
        identity = {"schema": 1, "serial": serial,
                    "fingerprint": run(base + ["getprop", "ro.build.fingerprint"]),
                    "oem_state": run(base + ["getprop", "ro.oem.state"]),
                    "chip_id": chip_id(run(base + ["cat", "/sys/devices/soc0/serial_number"]))}
        save_new(args.output, identity)
        print(f"Saved {args.output}; use 'python -m overpressure match' with fingerprint and oem_state")
        return
    if args.command == "check-unlocked":
        serial = select_device(args.fastboot, args.serial)
        check_unlock(args.fastboot, serial, args.critical)
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
        if args.backup.exists():
            raise ValueError("Use a new backup directory; existing or partial backups are never overwritten")
        args.backup.mkdir(parents=True)
        with (args.backup / "edl.log").open("w", encoding="utf-8") as log:
            for command in commands:
                output = run(command, timeout=86400)
                log.write(output + "\n")
                log.flush()
        if args.edl_backend == "edl-ng":
            validate_native_dump(args.backup, args.edl_luns, args.include_userdata)
        files = {p.relative_to(args.backup).as_posix(): digest(p)
                 for p in args.backup.rglob("*") if p.is_file() and p.suffix in (".bin", ".img", ".xml")}
        stems = {Path(name).stem for name in files}
        for required in ("abl", "boot", "devinfo"):
            if not stems.intersection({required, required + "_a", required + "_b"}):
                raise ValueError(f"EDL did not produce a {required} backup; no manifest written")
        if not any(s.startswith("gpt_main") for s in stems) or not any(
                Path(n).name.startswith("rawprogram") and n.endswith(".xml") for n in files):
            raise ValueError("Missing GPT/rawprogram metadata; no manifest written")
        if args.include_userdata and "userdata" not in stems:
            raise ValueError("Requested userdata backup is missing; no manifest written")
        save_new(args.backup / "manifest.json", {"schema": 1, "identity": identity,
                 "includes_userdata": args.include_userdata, "edl_backend": args.edl_backend,
                 "luns": args.edl_luns, "files": files})
        print("Backup captured. Run verify-backup and retain an independent copy before modifying the device.")
        return

    manifest = verified_backup(args.backup)
    if manifest["identity"] != identity:
        raise ValueError("Backup belongs to a different identity record")
    if args.command in ("stage", "restore-abl"):
        allowed = r"abl(?:_[ab])?" if args.command == "restore-abl" else r"(?:abl|devinfo)(?:_[ab])?"
        if not re.fullmatch(allowed, args.partition):
            raise ValueError("Only explicit ABL/devinfo partition names are accepted")
        matches = [name for name in manifest["files"]
                   if Path(name).stem == args.partition and Path(name).suffix in (".bin", ".img")]
        if len(matches) != 1:
            raise ValueError("Partition must have exactly one matching verified backup image")
        original = args.backup / matches[0]
        image = args.image if args.command == "stage" else original
        image = image.resolve()
        info = digest(image)
        if info["size"] > original.stat().st_size:
            raise ValueError("Image exceeds backed-up partition size")
        if args.command == "stage" and info["sha256"] != args.sha256.lower():
            raise ValueError("Engineering image SHA-256 does not match supplied value")
        command = edl_command(args, "w", args.partition, str(image))
        if not args.execute:
            print(subprocess.list2cmdline(command))
            return
        # Keep readback outside the immutable backup set, and never overwrite it.
        readback = args.readback.resolve()
        if readback.exists() or args.backup.resolve() in readback.parents:
            raise ValueError("--readback must be a new path outside the backup directory")
        readback.parent.mkdir(parents=True, exist_ok=True)
        run(command, timeout=600)
        run(edl_command(args, "r", args.partition, str(readback)), timeout=600)
        with image.open("rb") as source, readback.open("rb") as target:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                if target.read(len(block)) != block:
                    raise ValueError("Flash readback mismatch; keep the device in EDL and recover from backup")
        print("Image bytes verified by EDL readback; no reboot performed.")
        return
    if args.command == "unlock":
        # Engineering ABL needs permissive mode before writing the unlock flags.
        # Otherwise 00000050 can leave apparently unlocked, nonpersistent state.
        commands = [["oem", unlock_token(identity["chip_id"]), "unlock"],
                    ["oem", "setenforce", "0"], ["flashing", "unlock"],
                    ["flashing", "unlock_critical"], ["reboot", "bootloader"]]
        if not args.execute:
            for command in commands:
                print(subprocess.list2cmdline([args.fastboot, "-s", args.serial or identity["serial"], *command]))
            return
        if not args.accept_data_loss:
            raise ValueError("Unlock wipes user data; execution requires --accept-data-loss")
        serial = select_device(args.fastboot, args.serial or identity["serial"])
        for command in commands:
            run([args.fastboot, "-s", serial, *command], timeout=120)
        # fastboot itself waits for reconnection; subprocess timeout bounds that wait.
        check_unlock(args.fastboot, serial, critical=True)
        print("Normal and critical unlock confirmed after bootloader reboot. "
              "See docs/device-preparation.md for reset, RAM testing and stock ABL restoration.")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m overpressure device", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    token = sub.add_parser("token", help="derive an OEM command offline; execute nothing")
    token.add_argument("chip_id")
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
            p.add_argument("--include-userdata", action="store_true", help="full userdata partition, potentially hundreds of GB")
            p.add_argument("--edl-luns", nargs="+", type=int, choices=range(8),
                           help="edl-ng only: all LUNs found by the device's printgpt scan")
        if name in ("stage", "restore-abl"):
            p.add_argument("--partition", required=True, help="exact partition name from the captured GPT")
            p.add_argument("--readback", type=Path, required=True)
        if name == "stage":
            p.add_argument("--image", type=Path, required=True)
            p.add_argument("--sha256", required=True, help="expected engineering image hash from your trusted source")
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        dispatch(args)
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
