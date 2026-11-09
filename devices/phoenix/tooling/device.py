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


#: The PICO OS builds the install accepts (docs/device-preparation.md#other-pico-os-builds), as (version,
#: region, ro.oem.state): the one the whole path has run on, and the ones that run the same code before
#: the kernel by `python -m overpressure bootchain` (the boot chain re-signed, the board overlay the same)
#: and sign vbmeta and recovery with the same key at the same rollback index. Everything else the system
#: runs comes from the verified build's public package, whatever the headset runs.
VERIFIED_FIRMWARES = {("5.13.7", "global", "true")}
SAME_CODE_FIRMWARES = {("5.13.3", "global", "true"), ("5.13.8", "global", "true")}
#: Panels the kernel's driver and tree know, as the bootloader names them to the stock kernel.
VERIFIED_PANELS = ("sharp_ls026b3sa",)
#: The PICO 4 among the headsets PICO OS's one package serves, as PICO OS tells them apart: the model from
#: the serial number (vendor pxr.vendorhw.check.sh: "PICO 4", "PICO 4 Pro", "PICO 4 Enterprise") and the
#: memory from the bootloader's report (vendor init.customize.config.sh: "DDR4" or "DDR5"). The Pro and
#: Enterprise share the PICO 4's fingerprint; their LPDDR5 needs another Firehose loader and memory setup.
MODEL_PROPERTIES = {"model": "pxr.vendorhw.product.model", "ddr_type": "ro.vendor.ddr_type"}
VERIFIED_MODEL = {"model": "PICO 4", "ddr_type": "DDR4"}


def firmware_region(fingerprint: str) -> str:
    """Return a PICO 4 fingerprint's region: global for its product Phoenix_ovs, cn for Phoenix."""
    product = fingerprint.split("/")[1] if fingerprint.count("/") >= 1 else ""
    return "global" if product.lower().endswith("_ovs") else "cn"


def firmware_support(identity: dict) -> str:
    """Return how the install supports a saved identity's PICO OS build; refuse one it does not."""
    fingerprint = identity.get("fingerprint", "")
    return check_firmware(fingerprint.split("/")[3], identity.get("oem_state", ""), firmware_region(fingerprint))


def check_firmware(version: str, oem_state: str, region: str = "global") -> str:
    """Return whether a PICO OS build is verified or the same code before the kernel; refuse any other."""
    state = str(oem_state).lower()
    build = (version, region, state)
    if build in VERIFIED_FIRMWARES:
        return "verified"
    if build in SAME_CODE_FIRMWARES:
        return "the verified build's code before the kernel"
    if region != "global":
        why = "China builds run another XBL and AOP than the verified build"
    elif state != "true":
        why = (
            "builds with an empty ro.oem.state (sek) sign vbmeta and recovery with another key than the seko "
            "build whose recovery the install writes"
        )
    else:
        why = "this build has not been compared with the verified one (python -m overpressure bootchain)"
    accepted = ", ".join(v for v, _r, _s in sorted(VERIFIED_FIRMWARES | SAME_CODE_FIRMWARES))
    msg = (
        f"PICO OS {version} ({region}, ro.oem.state {oem_state}) is not supported: {why}. Supported: the global "
        f"seko builds {accepted}. Nothing was changed (docs/device-preparation.md)"
    )
    raise ValueError(msg)


def check_model(identity: dict) -> None:
    """Refuse a PICO 4 Pro or Enterprise, which PICO OS's fingerprint does not tell from the PICO 4.

    An identity saved before the probe read these has neither field and stays as it was; a probe now always
    records both, and an empty value (PICO OS did not say) is refused like a wrong one.
    """
    for key, verified in VERIFIED_MODEL.items():
        if key in identity and identity[key] != verified:
            said = identity[key] or "nothing"
            msg = (
                f"PICO OS reports {MODEL_PROPERTIES[key]} as {said}, not the original PICO 4's {verified}: the "
                "PICO 4 Pro and Enterprise are not verified (their LPDDR5 memory needs another loader and setup); "
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


#: What PICO OS's adb shell may read about the panel, one command: SELinux keeps it from the kernel's
#: command line, not from the display service's log at boot (Dpps: "panel_name <panel>_90hz_video_mode_dsi_panel")
#: or SurfaceFlinger's display name (the panel's name with spaces, cut to 13 characters, "sharp ls026b3").
PANEL_PROBE = (
    "cat /proc/cmdline 2>&1; logcat -d -s Dpps 2>&1 | grep -m1 panel_name; dumpsys SurfaceFlinger --display-id 2>&1"
)


def check_panel_in_pico_os(output: str) -> str:
    """Return the verified panel from PANEL_PROBE's output on PICO OS, or refuse.

    The kernel's command line where the shell may read it (check_panel), else the display service's full
    name while the log still holds the boot, else SurfaceFlinger's short one, which tells the PICO 4's two
    panels apart. The maintenance stage 1 checks the command line again before the system is written.
    """
    if "msm_drm.dsi_display0=" in output:
        return check_panel(output)
    found = re.search(r"\bpanel_name ([a-z0-9_]+?)_\d+hz_(?:video|cmd)_mode_dsi_panel\b", output)
    if found:
        name = found.group(1)
    else:
        shown = re.search(r'\(HWC display 0\).*?displayName="([^"]*)"', output)
        if not shown or not shown.group(1):
            msg = "PICO OS names no display panel: refusing to install (docs/device-preparation.md)"
            raise ValueError(msg)
        short = {panel.replace("_", " ")[:13]: panel for panel in VERIFIED_PANELS}
        name = short.get(shown.group(1), shown.group(1).replace(" ", "_"))
    if name not in VERIFIED_PANELS:
        msg = (
            f"Panel {name} is not verified, only {', '.join(VERIFIED_PANELS)}: "
            "refusing to install (docs/device-preparation.md)"
        )
        raise ValueError(msg)
    return name


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
    firmware_support(identity)
    check_model(identity)
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
            **{key: run([*base, "getprop", prop]) for key, prop in MODEL_PROPERTIES.items()},
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
            unlock_step(args.fastboot, serial, command)
        # fastboot itself waits for reconnection; subprocess timeout bounds that wait.
        check_unlock(args.fastboot, serial, critical=True)
        print(
            "Normal and critical unlock confirmed after bootloader reboot. "
            "See docs/device-preparation.md for reset, RAM testing and stock ABL restoration."
        )


def unlock_step(fastboot: str, serial: str, command: list[str]) -> None:
    """Run one fastboot command of the unlock; a flashing command refused as done already counts.

    A headset unlocked before (back from PICO OS for a new install) refuses the unlock with "Device
    already : unlocked!". The check after the bootloader's reboot still decides.
    """
    try:
        run([fastboot, "-s", serial, *command], timeout=120)
    except ValueError as exc:
        if command[0] != "flashing" or not re.search(r"already\s*:?\s*unlocked", str(exc), re.IGNORECASE):
            raise
        print(f"{' '.join(command)}: the headset is unlocked already", file=sys.stderr)


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
