"""Independent host-side implementation of the documented PICO unlock workflow.

No upstream code or binaries are bundled. EDL is a separately installed bkerler/edl
or edl-ng CLI; partition names must come from the device's GPT, never a guessed slot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def chip_id(value: str) -> int:
    value = value.strip()
    if not re.fullmatch(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)", value):
        raise ValueError("Chip ID must be decimal or 0x-prefixed hexadecimal")
    number = int(value, 16 if value.lower().startswith("0x") else 10)
    if not 0 <= number <= 0xFFFFFFFF:
        raise ValueError("Chip ID must fit in 32 bits")
    return number


def unlock_token(number: int) -> str:
    # Protocol facts: mask the SoC ID, then substitute each hexadecimal digit.
    # Only the first sixteen symbols in the documented alphabet are reachable.
    if not 0 <= number <= 0xFFFFFFFF:
        raise ValueError("Chip ID must fit in 32 bits")
    return "pico" + format(number & 0xF7F3F37F, "x").translate(
        str.maketrans("0123456789abcdef", "0XD9J6FB3ATQIHNM"))


def run(command: list[str], timeout: int = 60) -> str:
    print(">> " + subprocess.list2cmdline(command), file=sys.stderr)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    output = result.stdout + "\n" + result.stderr
    if result.returncode or re.search(r"\b(?:FAILED|ERROR)\b", output, re.I):
        raise ValueError(f"Command failed ({result.returncode}): {output.strip()}")
    return output.strip()


def select_device(tool: str, serial: str | None) -> str:
    rows = [line.split() for line in run([tool, "devices"]).splitlines()]
    devices = {row[0]: row[1] for row in rows if len(row) >= 2
               and row[1] in ("device", "fastboot", "unauthorized", "offline")}
    if serial is None:
        if len(devices) != 1:
            raise ValueError("Connect exactly one headset or specify --serial")
        serial = next(iter(devices))
    if devices.get(serial) not in ("device", "fastboot"):
        raise ValueError("Selected headset is missing, offline, or unauthorized")
    return serial


def unlocked(output: str, critical: bool = False) -> bool:
    label = "Device critical unlocked" if critical else "(?:Device unlocked|unlocked)"
    values = re.findall(r"(?:^|\n)\s*(?:\(bootloader\)\s*)?" + label
                        + r"\s*:\s*(yes|no|true|false)\s*(?=\n|$)", output, re.I)
    return bool(values) and all(v.lower() in ("yes", "true") for v in values)


def check_unlock(fastboot: str, serial: str, critical: bool = False) -> None:
    try:
        output = run([fastboot, "-s", serial, "oem", "device-info"])
    except ValueError:
        if critical:
            raise
        output = run([fastboot, "-s", serial, "getvar", "unlocked"])
    if not unlocked(output) or (critical and not unlocked(output, True)):
        raise ValueError("Unlock state is locked or unknown; refusing to continue")
    print(output)


def digest(path: Path) -> dict:
    size = path.stat().st_size
    if not size:
        raise ValueError(f"Empty backup/image: {path}")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return {"size": size, "sha256": sha.hexdigest()}


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


def save_new(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def verified_backup(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not manifest.get("files"):
        raise ValueError("Incomplete backup manifest")
    for name, expected in manifest["files"].items():
        path = (folder / name).resolve()
        if folder.resolve() not in path.parents or digest(path) != expected:
            raise ValueError(f"Backup integrity check failed: {name}")
    return manifest


def executable(name: str) -> str:
    """Absolute path for a relative tool path: Windows CreateProcess rejects
    relative paths written with forward slashes. Bare names stay PATH lookups."""
    if "/" not in name and "\\" not in name:
        return name
    return str(Path(name).resolve())


def edl_command(args, *words: str) -> list[str]:
    loader = Path(args.loader).resolve()
    digest(loader)
    if args.edl_backend == "edl-ng":
        if args.edl_script:
            raise ValueError("edl-ng requires its native executable via --edl")
        operation = {"r": "read-part", "w": "write-part",
                     "dump-rawprogram": "dump-rawprogram"}[words[0]]
        return [executable(args.edl), "--memory", "Ufs", "--loader", str(loader), operation, *words[1:]]
    # Windows Git checkouts may materialize edlclient's entrypoint symlink as
    # plain text. Calling its top-level script avoids that broken entrypoint.
    tool = [executable(args.edl)]
    if args.edl_script:
        tool = [executable(args.edl_python), str(Path(args.edl_script).resolve())]
    return [*tool, *words, "--memory=ufs", f"--loader={loader}"]


def backup_commands(args) -> list[list[str]]:
    if args.edl_backend == "edl-ng":
        if not args.edl_luns or len(set(args.edl_luns)) != len(args.edl_luns):
            raise ValueError("edl-ng backup requires distinct --edl-luns from the device's GPT scan")
        commands = [edl_command(args, "dump-rawprogram", str((args.backup / f"lun{lun}").resolve()),
                                "--lun", str(lun)) for lun in args.edl_luns]
        if not args.include_userdata:
            for command in commands:
                command.extend(["--skip", "userdata"])
        return commands
    if args.edl_luns:
        raise ValueError("--edl-luns applies only to the edl-ng backend")
    command = edl_command(args, "rl", str(args.backup.resolve()), "--genxml")
    if not args.include_userdata:
        command.append("--skip=userdata")
    return [command]


def validate_native_dump(folder: Path, luns: list[int], include_userdata: bool) -> None:
    """Reject missing or truncated files against each native client's rawprogram map."""
    for lun in luns:
        directory = (folder / f"lun{lun}").resolve()
        rows = ET.parse(directory / f"rawprogram{lun}.xml").getroot().findall("program")
        if not rows:
            raise ValueError(f"Empty rawprogram map for LUN {lun}")
        for row in rows:
            if not include_userdata and row.get("label") == "userdata":
                continue
            name = row.attrib["filename"]
            path = (directory / name).resolve()
            size = int(row.attrib["num_partition_sectors"]) * int(row.attrib["SECTOR_SIZE_IN_BYTES"])
            if (directory not in path.parents or size <= 0
                    or int(row.attrib["physical_partition_number"]) != lun
                    or not path.is_file() or path.stat().st_size != size):
                raise ValueError(f"Incomplete dump for LUN {lun}: {name}")


def partition_size(backup: Path, label: str) -> int:
    """A partition's size from the backup's rawprogram maps (also for userdata, which a backup
    need not contain): exactly one partition of that name across all LUNs."""
    sizes = [int(row.attrib["num_partition_sectors"]) * int(row.attrib["SECTOR_SIZE_IN_BYTES"])
             for xml in sorted(backup.glob("lun*/rawprogram*.xml"))
             for row in ET.parse(xml).getroot().findall("program") if row.get("label") == label]
    if len(sizes) != 1 or sizes[0] <= 0:
        raise ValueError(f"the backup's partition maps hold {len(sizes)} partitions named {label}")
    return sizes[0]


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
