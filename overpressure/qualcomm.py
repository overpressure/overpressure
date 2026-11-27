# SPDX-License-Identifier: Unlicense
"""Qualcomm devices: the SoC ID, Emergency Download mode (EDL) and its partition maps.

EDL runs through a separately installed client, edl-ng or bkerler/edl, with the device's own
signed Firehose loader; no upstream code or binaries are bundled. Partition names come from the
device's GPT (its rawprogram maps), never a guessed slot. A full backup gets a manifest only
when it is complete, and a write through EDL is read back and compared; the headset reaches EDL
from Android, and edl-ng's serial says it is the configured one.
"""

from __future__ import annotations

import contextlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from . import holes, progress, usbaccess
from .android import executable
from .backup import digest, save_new
from .process import run, until
from .usbaccess import UsbMode

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

    from .process import Timing

EDL_USB = ("05c6", "9008")  # Qualcomm HS-USB QDLoader 9008
#: EDL as a USB mode: the device edl-ng opens, which Linux grants through a udev rule.
EDL_MODE = UsbMode("EDL mode", *EDL_USB)
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


def usb_count(vendor: int, product: int) -> int | None:
    """Return how many USB devices have @p vendor and @p product, None when pyusb or libusb is missing.

    Enumeration only, through pyusb and libusb-package's libusb (pinned in pyproject.toml): the
    device descriptors libusb lists, no device opened or claimed, nothing sent.
    """
    try:
        import libusb_package  # noqa: PLC0415  # optional: the start scripts and uv sync install it
        import usb.core  # noqa: PLC0415  # optional: the start scripts and uv sync install it

        backend = libusb_package.get_libusb1_backend()
        if backend is None:
            return None
        return sum(1 for _ in usb.core.find(find_all=True, idVendor=vendor, idProduct=product, backend=backend))
    except (ImportError, OSError, ValueError, RuntimeError):  # no package, no library, or libusb's own error
        return None
    except Exception as exc:  # usb.core.USBError and NoBackendError: pyusb's own, imported lazily
        if type(exc).__module__.startswith("usb."):
            return None
        raise


def ioreg_count(output: str, vendor: int, product: int) -> int:
    """Count the devices in macOS's `ioreg -p IOUSB -l` output with @p vendor and @p product."""
    count, ids = 0, {}
    for line in [*output.splitlines(), "+-o end"]:
        if "+-o " in line:  # the next device's node
            count += ids.get("idVendor") == vendor and ids.get("idProduct") == product
            ids = {}
            continue
        match = re.search(r'"(idVendor|idProduct)" = (\d+)', line)
        if match:
            ids[match.group(1)] = int(match.group(2))
    return count


def sysfs_count(root: Path, vendor: int, product: int) -> int:
    """Count the devices under Linux's /sys/bus/usb/devices with @p vendor and @p product."""
    count = 0
    for folder in root.glob("*"):
        try:
            ids = (int((folder / "idVendor").read_text(), 16), int((folder / "idProduct").read_text(), 16))
        except (OSError, ValueError):
            continue
        count += ids == (vendor, product)
    return count


def usb_ids(
    *,
    platform: str = sys.platform,
    command: Callable[[list[str], float], str] = run,
    sysfs: Path = Path("/sys/bus/usb/devices"),
) -> list[tuple[int, int]]:
    """Return the vendor and product of each USB device in this system's own listing (since interface 1.12).

    For a look without pyusb: PowerShell's Get-PnpDevice on Windows (devices, not their interfaces), ioreg on
    macOS, sysfs on Linux, in one call each. Enumeration only: no device is opened.
    """
    if platform == "win32":
        output = command(["powershell", "-NoProfile", "-Command", "Get-PnpDevice -PresentOnly | % InstanceId"], 30)
        found = re.findall(r"(?im)^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})\\", output)
        return [(int(v, 16), int(p, 16)) for v, p in found]
    if platform == "darwin":
        ids, devices = {}, []
        for line in [*command(["ioreg", "-p", "IOUSB", "-l"], 30).splitlines(), "+-o end"]:
            if "+-o " in line:
                if {"idVendor", "idProduct"} <= set(ids):
                    devices.append((ids["idVendor"], ids["idProduct"]))
                ids = {}
                continue
            match = re.search(r'"(idVendor|idProduct)" = (\d+)', line)
            if match:
                ids[match.group(1)] = int(match.group(2))
        return devices
    devices = []
    for folder in sysfs.glob("*"):
        try:
            ids = (int((folder / "idVendor").read_text(), 16), int((folder / "idProduct").read_text(), 16))
        except (OSError, ValueError):
            continue
        devices.append(ids)
    return devices


def edl_devices(
    *,
    platform: str = sys.platform,
    usb: Callable[[int, int], int | None] = usb_count,
    command: Callable[[list[str], float], str] = run,
    sysfs: Path = Path("/sys/bus/usb/devices"),
) -> int:
    """Return the number of attached Qualcomm EDL (9008) devices.

    Through pyusb on every system; where it is missing, this system's own listing: PowerShell's
    Get-PnpDevice on Windows, ioreg on macOS, sysfs on Linux.
    """
    vendor, product = (int(i, 16) for i in EDL_USB)
    count = usb(vendor, product)
    if count is not None:
        return count
    if platform == "win32":
        pattern = "USB\\VID_{}&PID_{}*".format(*EDL_USB).upper()
        output = command(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"@(Get-PnpDevice -PresentOnly | Where-Object {{ $_.InstanceId -like '{pattern}' }}).Count",
            ],
            30,
        )
        return int(output.split()[-1]) if output.split() else 0
    if platform == "darwin":
        return ioreg_count(command(["ioreg", "-p", "IOUSB", "-l"], 30), vendor, product)
    return sysfs_count(sysfs, vendor, product)


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


#: Partitions a backup leaves out besides userdata: Android's cache and the crash-dump areas. No install or
#: restore writes them back, and they hold nothing of the unit's own (2.7 GiB of a PICO 4's 12.5, 0.1 of it data).
NOT_KEPT = ("cache", "rawdump", "logdump")


def skipped(*, include_userdata: bool) -> list[str]:
    """Return the partitions a backup leaves out: NOT_KEPT, and userdata unless it is asked for."""
    return [*NOT_KEPT, *(() if include_userdata else ("userdata",))]


def backup_commands(args: argparse.Namespace, done: dict[int, list[str]] | None = None) -> list[list[str]]:
    """Return the EDL command lines of a full backup (ARGS as edl_command's, with the backup's).

    @p done (since interface 1.5) names, per LUN, the partitions a stopped edl-ng backup already read whole
    (dumped()): they are skipped as well, so that the backup goes on from where it stopped.
    """
    skip = skipped(include_userdata=args.include_userdata)
    if args.edl_backend == "edl-ng":
        if not args.edl_luns or len(set(args.edl_luns)) != len(args.edl_luns):
            msg = "edl-ng backup requires distinct --edl-luns from the device's GPT scan"
            raise ValueError(msg)
        # The skipped partitions stay in the rawprogram maps (their sizes), without an image.
        return [
            edl_command(
                args,
                "dump-rawprogram",
                str((args.backup / f"lun{lun}").resolve()),
                "--lun",
                str(lun),
                "--skip",
                ",".join([*skip, *(done or {}).get(lun, [])]),
            )
            for lun in args.edl_luns
        ]
    if args.edl_luns:
        msg = "--edl-luns applies only to the edl-ng backend"
        raise ValueError(msg)
    return [edl_command(args, "rl", str(args.backup.resolve()), "--genxml", f"--skip={','.join(skip)}")]


#: In a backup folder while its capture is unfinished: whose backup it is (the identity), so that a later try
#: goes on with it. The manifest takes its place once the backup is complete.
UNFINISHED = "unfinished.json"


def unfinished(folder: Path) -> dict | None:
    """Return the identity whose unfinished backup @p folder holds, None for anything else (since 1.5)."""
    if (folder / "manifest.json").exists():
        return None
    try:
        record = json.loads((folder / UNFINISHED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    identity = record.get("identity") if isinstance(record, dict) else None
    return identity if isinstance(identity, dict) else None


def dumped(folder: Path, lun: int) -> list[str]:
    """Return the partitions of @p lun a stopped edl-ng backup in @p folder read whole (since 1.5).

    edl-ng writes a LUN's rawprogram map before it reads any partition, and deletes the file of a read that
    fails: a partition counts as read when its file has the map's size. Without the map, none does.
    """
    directory = (folder / f"lun{lun}").resolve()
    try:
        rows = ET.parse(directory / f"rawprogram{lun}.xml").getroot().findall("program")  # noqa: S314  # the EDL client's own map
    except (OSError, ET.ParseError):
        return []
    found = []
    for row in rows:
        label, name = row.get("label"), row.get("filename")
        if not label or not name or label in found:
            continue
        path = (directory / name).resolve()
        try:
            size = int(row.attrib["num_partition_sectors"]) * int(row.attrib["SECTOR_SIZE_IN_BYTES"])
        except (KeyError, ValueError):
            continue
        if directory in path.parents and size > 0 and path.is_file() and path.stat().st_size == size:
            found.append(label)
    return found


def validate_native_dump(folder: Path, luns: list[int], *, include_userdata: bool) -> None:
    """Reject missing or truncated files against each native client's rawprogram map."""
    for lun in luns:
        directory = (folder / f"lun{lun}").resolve()
        rows = ET.parse(directory / f"rawprogram{lun}.xml").getroot().findall("program")  # noqa: S314  # the EDL client's own map
        if not rows:
            msg = f"Empty rawprogram map for LUN {lun}"
            raise ValueError(msg)
        left_out = set(skipped(include_userdata=include_userdata))
        for row in rows:
            if row.get("label") in left_out:
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


#: Partitions a backup must hold an image of (a slot suffix allowed), or no manifest is written.
REQUIRED_PARTITIONS = ("abl", "boot", "devinfo")


class DumpProgress:
    """Turn edl-ng's "Dumping NAME: P% ..." display into progress lines: the partition and each tenth of it.

    How far the whole backup is stays unknown: a LUN's partitions are listed only once its dump begins.
    """

    def __init__(self, out: Callable[[str], None] = progress.say) -> None:
        """Print each progress line through @p out."""
        self.out = out
        self.shown: tuple[str, int] | None = None

    def __call__(self, line: str) -> None:
        """Take one line of edl-ng's output."""
        found = re.match(r"Dumping (\S+?):\s*(\d+(?:\.\d+)?)%", line.strip())
        if found is None:
            return
        name, tenth = found.group(1), int(float(found.group(2)) // 10)
        if self.shown != (name, tenth):
            self.shown = (name, tenth)
            self.out(f"reading {name}" + (f" ({tenth * 10}%)" if tenth else ""))


def _backup_folder(folder: Path, identity: dict, *, resume: bool) -> None:
    """Make @p folder a new backup's, or with @p resume take this identity's unfinished one; refuse the rest."""
    if (folder / "manifest.json").exists() or (folder.exists() and not resume):
        msg = "Use a new backup directory; existing or partial backups are never overwritten"
        raise ValueError(msg)
    if folder.exists():
        if unfinished(folder) != identity:
            msg = f"{folder} is not an unfinished backup of this headset; it is never written to"
            raise ValueError(msg)
        return
    folder.mkdir(parents=True)
    save_new(folder / UNFINISHED, {"identity": identity})


def capture_backup(  # noqa: PLR0913  # the backup's folder, commands and identity; how it was taken keyword-only
    folder: Path,
    commands: list[list[str]],
    identity: dict,
    *,
    backend: str,
    luns: list[int] | None,
    include_userdata: bool,
    runner: Callable[..., str],
    required: Sequence[str] = REQUIRED_PARTITIONS,
    resume: bool = False,
) -> dict:
    """Take a full EDL backup into the new folder @p folder and write its manifest; return the manifest.

    @p commands are backup_commands' (with @p backend, @p luns and @p include_userdata as they
    were made); @p runner runs each, its output going to edl.log. No manifest is written unless
    the dump is complete: edl-ng's files against its rawprogram maps, an image of every @p required
    partition, the GPT and rawprogram metadata, and userdata when asked for. With @p resume (since 1.5)
    the folder may hold this identity's unfinished backup (unfinished()), which goes on: the commands then skip
    what it holds (backup_commands' done). A complete backup, or another headset's, is never written to.
    """
    _backup_folder(folder, identity, resume=resume)
    with (folder / "edl.log").open("a", encoding="utf-8") as log:
        for command in commands:
            try:
                output = runner(command, timeout=86400)
            except Exception as exc:
                # The failure goes into the log whole: the error keeps only its end, the log shows where it began.
                log.write(f"{failed_output(exc)}\n{type(exc).__name__}: {exc}\n")
                log.flush()
                raise
            log.write(output + "\n")
            log.flush()
    if backend == "edl-ng":
        validate_native_dump(folder, luns or [], include_userdata=include_userdata)
    files = {
        p.relative_to(folder).as_posix(): digest(p)
        for p in folder.rglob("*")
        if p.is_file() and p.suffix in (".bin", ".img", ".xml")
    }
    stems = {Path(name).stem for name in files}
    for name in required:
        if not stems.intersection({name, name + "_a", name + "_b"}):
            msg = f"EDL did not produce a {name} backup; no manifest written"
            raise ValueError(msg)
    if not any(s.startswith("gpt_main") for s in stems) or not any(
        Path(n).name.startswith("rawprogram") and n.endswith(".xml") for n in files
    ):
        msg = "Missing GPT/rawprogram metadata; no manifest written"
        raise ValueError(msg)
    if include_userdata and "userdata" not in stems:
        msg = "Requested userdata backup is missing; no manifest written"
        raise ValueError(msg)
    manifest = {
        "schema": 1,
        "identity": identity,
        "includes_userdata": include_userdata,
        "skipped": skipped(include_userdata=include_userdata),
        "edl_backend": backend,
        "luns": luns,
        "files": files,
    }
    save_new(folder / "manifest.json", manifest)
    (folder / UNFINISHED).unlink(missing_ok=True)
    # The images' zero runs go back to the file system: the same bytes, about half the space.
    for name in files:
        holes.free_zeros(folder / name)
    return manifest


def write_verified(  # noqa: PLR0913  # the two commands, the image and both paths, and the runner
    write: list[str],
    read: Callable[[str], list[str]],
    image: Path,
    *,
    readback: Path,
    backup: Path,
    runner: Callable[..., str],
) -> None:
    """Write @p image to a partition through EDL, read it back and compare the bytes.

    @p write is the write command (edl_command's "w"), @p read returns the read command for a
    path; the readback goes to @p readback, a new path outside the immutable @p backup. A
    mismatch raises with the device left in EDL.
    """
    # Keep readback outside the immutable backup set, and never overwrite it.
    readback = readback.resolve()
    if readback.exists() or backup.resolve() in readback.parents:
        msg = "--readback must be a new path outside the backup directory"
        raise ValueError(msg)
    readback.parent.mkdir(parents=True, exist_ok=True)
    runner(write, timeout=600)
    runner(read(str(readback)), timeout=600)
    with image.open("rb") as source, readback.open("rb") as target:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if target.read(len(block)) != block:
                msg = "Flash readback mismatch; keep the device in EDL and recover from backup"
                raise ValueError(msg)


def enter_edl(  # noqa: PLR0913  # adb and the serial, the runner and the clock, the EDL counter and the bound
    adb: str,
    serial: str,
    runner: Callable[..., str],
    clock: Timing,
    *,
    edl_count: Callable[[], int] = edl_devices,
    seconds: float = 30,
    modes: Sequence[UsbMode] = (EDL_MODE,),
    denied: Callable[[], bool] | None = None,
) -> None:
    """Bring the headset @p serial from Android to EDL (adb reboot edl), unless it is the one EDL device already.

    Exactly one EDL device must appear within @p seconds, and this user must be allowed to open it
    (Linux: a udev rule; usbaccess.NoAccessError names the fix from the headset's @p modes). @p denied
    tells whether it is not, for tests.
    """
    if edl_count() != 1:
        runner([adb, "-s", serial, "reboot", "edl"], 30)

        def one() -> None:
            if edl_count() != 1:
                msg = "expected exactly one EDL device"
                raise RuntimeError(msg)

        until("EDL", seconds, one, clock)
    usbaccess.require(EDL_MODE, modes, clock, denied_now=denied)


def edl_ng(program: str | Path, loader: str | Path, *words: str | Path, memory: str = "Ufs") -> list[str | Path]:
    """Return an edl-ng command line with the device's Firehose @p loader and storage type."""
    return [program, "--loader", loader, "--memory", memory, *words]


#: edl-ng's words when the device in EDL mode gives it no answer it can place: its mode stays Unknown or Error,
#: or the device looks like Sahara but its handshake fails again after edl-ng's reset (1.6's last words in
#: UploadLoaderViaSaharaAsync; on macOS libusb fails it with a stalled pipe). The first failure, "Initial Sahara
#: handshake failed, attempting reset and retry...", is not one: the retry may still answer.
_UNANSWERED = re.compile(
    r"Device mode is \w+ or could not be determined"  # no mode found
    r"|Sahara handshake failed(?: even after reset)?\."  # the handshake failed again after the reset
)
#: What that means, in plain words: a device in EDL mode opens the conversation once, right after it appears.
NO_ANSWER = (
    "The headset is in EDL mode but does not answer: it opens the conversation only right after it enters "
    "EDL mode, and that moment has passed (it waited too long, or an earlier attempt failed)."
)


# Windows' serial driver: "QUD write to \\.\COM5 timed out" (the device command keeps only the error's end:
# "\.\COM5 timed out"); libusb: "LibUsb bulk write timed out". A garbled reply: the loader's XML arrives as zeros,
# which .NET's XML reader refuses ("'.', hexadecimal value 0x00, is an invalid character").
_LINK_LOST = re.compile(
    r"(?:write to|read from) \S+ timed out|COM\d+ timed out|LibUsb [\w ]+ timed out"
    r"|hexadecimal value 0x[0-9A-Fa-f]+, is an invalid character"
)
#: What a broken link in the middle of edl-ng's session means: its loader keeps running, but answers no new session.
LINK_LOST = (
    "The USB link to the headset in EDL mode broke in the middle of the backup (edl-ng's transfer timed out, or "
    "its answer arrived garbled). That is almost always the USB port or the cable. The headset has to restart "
    "before EDL mode works again."
)


def link_lost(output: str) -> bool:
    """Tell whether edl-ng's @p output says the link broke mid-session (since 1.5): a timeout or a garbled reply."""
    return _LINK_LOST.search(output) is not None


def failed_output(exc: BaseException) -> str:
    """Return the whole output of the command behind @p exc: an `output` on it or on what caused it, else its text.

    process.CommandFailed carries it, as subprocess.CalledProcessError does; a headset's runner may raise its own
    error from one (`raise ... from exc`) or carry `output` itself.
    """
    seen: BaseException | None = exc
    for _ in range(8):  # a chain is short; this bounds a cycle
        if seen is None:
            break
        output = getattr(seen, "output", None)
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        if isinstance(output, str) and output:
            return output
        seen = seen.__cause__ or seen.__context__
    return str(exc)


def unanswered(output: str) -> bool:
    """Tell whether edl-ng's @p output says the device in EDL mode did not answer (a stale EDL session)."""
    return _UNANSWERED.search(output) is not None


#: The serial edl-ng names from the device's Sahara handshake, not the loader's log of the storage's own serial
#: ("Device Serial Number: 0x..."), which edl-ng's debug level shows in every session (since interface 1.12).
HANDSHAKE_SERIAL = r"(?<!Device )Serial Number:\s*"


def serial_shown(output: str) -> bool:
    """Tell whether edl-ng's @p output names the device's serial.

    It does when its session starts with the device's Sahara handshake. A device still running the
    Firehose loader of an earlier session (on Linux the loader keeps running between sessions;
    Windows' driver starts each afresh) names none: a reset into EDL (fresh_edl_argv) brings the
    handshake back.
    """
    return re.search(HANDSHAKE_SERIAL + r"\S+", output) is not None


def fresh_edl_argv(program: str | Path, loader: str | Path) -> list[str | Path]:
    """Return the edl-ng command that restarts the device into a fresh EDL session (Firehose's reset_to_edl).

    It restarts the chip; no storage is written.
    """
    return edl_ng(program, loader, "reset", "--mode", "Edl")


def reboot_argv(program: str | Path, loader: str | Path) -> list[str | Path]:
    """Return the edl-ng command that leaves EDL with a normal restart (Firehose's reset): Android starts.

    The PICO 4's loader carries it out at once, also after a session that read the whole storage, where it
    left a restart into EDL (fresh_edl_argv) undone. No storage is written.
    """
    return edl_ng(program, loader, "reset", "--mode", "Reset")


#: Seconds a loader may take to carry out fresh_edl_argv's restart (the PICO 4's: about 12 s, whatever the delay asked).
RESTART_SECONDS = 60


class UsbReader:
    """Read what a device's loader still sends on its USB pipe: a computer's reading lets it carry out a reset.

    After acknowledging a power command, the loader sends a last message and waits until it is read.
    Windows' Qualcomm driver reads the pipe all the time; on Linux and macOS nothing does once edl-ng
    has exited, and the loader would wait forever. Only reads: nothing is sent to the device.
    """

    def __init__(self, vendor: int = int(EDL_USB[0], 16), product: int = int(EDL_USB[1], 16)) -> None:
        """Read from the device with @p vendor and @p product (EDL's by default)."""
        self.vendor = vendor
        self.product = product
        self.seen: tuple[int, int] | None = None  # the device's bus and address when first read

    def __call__(self) -> bool:
        """Read the device's pipe until it is empty; return True once the device has left USB.

        A device back at another address is the restarted one: it is never read, so that a fresh
        EDL session keeps its greeting for edl-ng.
        """
        return self.read() is None

    def queued(self) -> int:
        """Read what the device's loader still sends; return the bytes read (0: nothing, or the device is gone)."""
        return self.read() or 0

    def read(self) -> int | None:
        """Read the device's pipe until a read times out; return the bytes read, None once it has left USB.

        Without pyusb nothing is read (0). A device back at another address is the restarted one (None).
        """
        try:
            import libusb_package  # noqa: PLC0415  # pinned in pyproject.toml; imported where used
            import usb.core  # noqa: PLC0415  # pinned in pyproject.toml; imported where used
            import usb.util  # noqa: PLC0415  # pinned in pyproject.toml; imported where used
        except ImportError:
            return 0
        backend = libusb_package.get_libusb1_backend()
        device = usb.core.find(idVendor=self.vendor, idProduct=self.product, backend=backend)
        if device is None:
            return None
        where = (int(device.bus or 0), int(device.address or 0))
        if self.seen is None:
            self.seen = where
        elif where != self.seen:
            usb.util.dispose_resources(device)
            return None  # the restarted device: left alone
        total = 0
        try:
            with contextlib.suppress(NotImplementedError, usb.core.USBError):
                if device.is_kernel_driver_active(0):
                    device.detach_kernel_driver(0)
            interface = device.get_active_configuration()[(0, 0)]
            endpoint = usb.util.find_descriptor(
                interface,
                custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN,
            )
            if endpoint is None:
                return 0
            usb.util.claim_interface(device, 0)
            for _ in range(64):  # what is queued, a packet at a time, until a read times out
                total += len(device.read(endpoint.bEndpointAddress, 16384, timeout=250))
        except usb.core.USBError:  # a timeout (nothing more queued), or the device left while reading
            pass
        finally:
            usb.util.dispose_resources(device)
        return total


def drain_until_gone(
    clock: Timing,
    *,
    seconds: float = 60,
    reader: Callable[[], bool] | None = None,
    platform: str = sys.platform,
) -> bool:
    """After a power command, read the device's pipe until it leaves USB; return whether it left in @p seconds.

    Windows' driver does the reading itself: there it only returns True.
    """
    if platform == "win32" and reader is None:
        return True
    read = reader or UsbReader()
    end = clock.monotonic() + seconds
    while clock.monotonic() < end:
        if read():
            return True
        clock.sleep(0.2)
    return False


#: Seconds a broken session's loader may stay silent before what it still sends counts as read (drain_session).
QUIET_SECONDS = 3.0
#: The most a broken session's loader is read: a whole partition's readback is at most a few hundred MB.
DRAIN_SECONDS = 300.0


def com_port(output: str) -> str:
    """Return the COM port in Get-PnpDevice's FriendlyName output ("Qualcomm HS-USB QDLoader 9008 (COM8)"), or ""."""
    match = re.search(r"\((COM\d+)\)", output)
    return match.group(1) if match else ""


class ComReader:
    """Windows: read what an EDL device's loader still sends, through Qualcomm's serial driver (its COM port).

    The driver keeps the device's pipe moving, but what the loader sent after its session ended waits in the
    port's buffer, in front of the next session's answers; edl-ng then reads the old transfer's bytes as its
    answers and its writes time out. Reading the port empties it. Only reads: nothing is sent to the device.
    """

    def __init__(self, command: Callable[[list[str], float], str] = run) -> None:
        """Find the port with @p command (PowerShell's Get-PnpDevice) at the first read."""
        self.command = command
        self.port = ""

    def find(self) -> str:
        """Return the EDL device's COM port, or "" when Windows lists none."""
        pattern = "USB\\VID_{}&PID_{}*".format(*EDL_USB).upper()
        script = f"Get-PnpDevice -PresentOnly | Where-Object {{ $_.InstanceId -like '{pattern}' }} | % FriendlyName"
        return com_port(self.command(["powershell", "-NoProfile", "-Command", script], 30))

    def __call__(self) -> int:
        """Read the port until it gives nothing for half a second; return the bytes read (0: none waited)."""
        self.port = self.port or self.find()
        if not self.port or sys.platform != "win32":
            return 0
        import ctypes  # noqa: PLC0415  # Windows only
        from ctypes import wintypes  # noqa: PLC0415  # Windows only

        class Timeouts(ctypes.Structure):
            _fields_ = (  # ctypes reads the class attribute
                ("ReadIntervalTimeout", wintypes.DWORD),
                ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                ("ReadTotalTimeoutConstant", wintypes.DWORD),
                ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                ("WriteTotalTimeoutConstant", wintypes.DWORD),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )  # fmt: skip
        generic_read, generic_write, open_existing = 0x80000000, 0x40000000, 3
        handle = kernel32.CreateFileW(
            f"\\\\.\\{self.port}", generic_read | generic_write, 0, None, open_existing, 0, None
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            return 0  # in use, or gone
        total = 0
        try:
            kernel32.SetCommTimeouts(handle, ctypes.byref(Timeouts(50, 0, 500, 0, 1000)))
            buffer, got = ctypes.create_string_buffer(1 << 20), wintypes.DWORD()
            while kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(got), None) and got.value:
                total += got.value
        finally:
            kernel32.CloseHandle(handle)
        return total


def drain_session(
    read: Callable[[], int], clock: Timing, *, quiet: float = QUIET_SECONDS, seconds: float = DRAIN_SECONDS
) -> int:
    """Read what a broken session's loader still sends until it stays @p quiet seconds; return the bytes read.

    @p read reads what is queued and returns its size (ComReader on Windows, UsbReader.queued elsewhere).
    Raises RuntimeError when the loader still sends after @p seconds.
    """
    total, end, last = 0, clock.monotonic() + seconds, clock.monotonic()
    while clock.monotonic() < end:
        got = read()
        total += got
        if got:
            last = clock.monotonic()
        elif clock.monotonic() - last >= quiet:
            return total
        clock.sleep(0.2)
    msg = f"the headset in EDL mode still sends after {seconds:.0f} s"
    raise RuntimeError(msg)


def unstick(  # noqa: PLR0913  # edl-ng, its loader, the runner and the clock; the reader keyword-only
    program: str | Path,
    loader: str | Path,
    runner: Callable[[list[str | Path], float], str],
    clock: Timing,
    *,
    read: Callable[[], int] | None = None,
    platform: str = sys.platform,
) -> int:
    """Free a loader that a broken session left mid-transfer, and restart the device normally (reboot_argv).

    What the loader still sends is read first (drain_session): until then it takes no command, and every new
    session meets the old transfer's bytes, times out or finds no mode at all. Then it restarts normally: a
    restart into a fresh EDL session (fresh_edl_argv) is acknowledged but not carried out after such a session
    (the PICO 4's loader), so the caller enters EDL mode again from the system that starts. Nothing is written
    to the device's storage. Return the bytes read. Raises RuntimeError when the device takes no restart (no
    loader runs: an EDL session whose greeting has passed): then only a restart by hand frees it.
    """
    if read is None:
        read = ComReader() if platform == "win32" else UsbReader().queued
    total = drain_session(read, clock)
    runner(reboot_argv(program, loader), 60)
    return total


def wait_fresh_edl(edl_count: Callable[[], int], clock: Timing, *, seconds: float = RESTART_SECONDS) -> None:
    """Wait until the device restarted by fresh_edl_argv has left USB, then is back as the one EDL device.

    Its leaving must be seen: a device still there may still run the old loader, which a session
    would meet just before it disappears. Raises RuntimeError when it does not leave in @p seconds.
    """

    def gone() -> None:
        if edl_count():
            msg = "the EDL device is still there"
            raise RuntimeError(msg)

    def one() -> None:
        if edl_count() != 1:
            msg = "expected exactly one EDL device"
            raise RuntimeError(msg)

    try:
        until("the headset to restart", seconds, gone, clock, interval=0.5)
    except RuntimeError as exc:
        msg = f"the headset did not restart into a fresh EDL session within {seconds:.0f} s (still in EDL)"
        raise RuntimeError(msg) from exc
    until("a fresh EDL session", seconds, one, clock, interval=1)


def check_edl_serial(output: str, serial: str) -> None:
    """Raise unless edl-ng's @p output names @p serial: only the configured headset may be written.

    edl-ng reports the device's serial when it connects.
    """
    if not re.search(HANDSHAKE_SERIAL + rf"{re.escape(serial)}\b", output, re.IGNORECASE):
        msg = "the EDL device is not the configured headset (headset in EDL)"
        raise RuntimeError(msg)


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


# -- a whole backup written back over EDL ----------------------------------------------------------
#: The partitions a write-back zeroes at their start instead of writing them from the backup, as an Android
#: restore leaves them: the system makes its data anew. userdata is not in a backup; metadata belongs to the
#: data that is gone (since interface 1.10).
WIPED = ("userdata", "metadata")
#: How much of a WIPED partition's start is zeroed (all of a smaller one).
WIPE_BYTES = 64 << 20


@dataclass(frozen=True)
class Write:
    """One write of a backup's write-back: an image at a start sector of a LUN (since interface 1.10).

    @p image is a file of the backup, or None for @p sectors sectors of zeros (a WIPED partition's start).
    """

    lun: int
    label: str
    start: int
    sectors: int
    sector_size: int
    image: Path | None

    @property
    def size(self) -> int:
        """Return the write's size in bytes."""
        return self.sectors * self.sector_size


def _rows(folder: Path, lun: int) -> list[ET.Element]:
    """Return a backup LUN's rawprogram rows."""
    return ET.parse(folder / f"lun{lun}" / f"rawprogram{lun}.xml").getroot().findall("program")  # noqa: S314  # the EDL client's own map


def backup_lun_sectors(folder: Path, lun: int) -> int:
    """Return a backup LUN's size in sectors from its primary GPT: the backup header's position, plus one.

    The LUN the backup was read from had this size; a LUN of another size cannot take its backup GPT, nor
    the partitions as they lie (since interface 1.10).
    """
    primary = next((row for row in _rows(folder, lun) if row.get("label") == "PrimaryGPT"), None)
    if primary is None:
        msg = f"the backup's map of LUN {lun} has no primary GPT"
        raise ValueError(msg)
    with (folder / f"lun{lun}" / primary.attrib["filename"]).open("rb") as gpt:
        gpt.seek(int(primary.attrib["SECTOR_SIZE_IN_BYTES"]))
        header = gpt.read(92)
    if header[:8] != b"EFI PART":
        msg = f"the backup's primary GPT of LUN {lun} has no GPT header"
        raise ValueError(msg)
    return struct.unpack_from("<Q", header, 32)[0] + 1


def _start(value: str, sectors: int) -> int:
    """Return a rawprogram start sector: a number, or NUM_DISK_SECTORS-N. counted from the LUN's end."""
    found = re.fullmatch(r"NUM_DISK_SECTORS-(\d+)\.?", value.strip())
    return sectors - int(found.group(1)) if found else int(value.strip().rstrip("."))


def write_back_plan(folder: Path, manifest: dict) -> list[Write]:
    """Return a verified backup's write-back: LUN by LUN in the backup's order, each in its map's order.

    Every partition the backup holds goes back from its image, the GPTs and the boot chain included; the
    WIPED ones get zeros at their start instead, and the others a backup leaves out (NOT_KEPT) are not
    written. Each write lies inside its LUN, and each image has its partition's size (since interface 1.10).
    """
    files = manifest["files"]
    plan = []
    for lun in manifest["luns"]:
        total = backup_lun_sectors(folder, lun)
        for row in _rows(folder, lun):
            label = row.get("label", "")
            size = int(row.attrib["SECTOR_SIZE_IN_BYTES"])
            sectors = int(row.attrib["num_partition_sectors"])
            start = _start(row.attrib["start_sector"], total)
            if sectors <= 0 or start < 0 or start + sectors > total:
                msg = f"{label} lies outside LUN {lun} ({total} sectors)"
                raise ValueError(msg)
            name = f"lun{lun}/{row.attrib['filename']}"
            if label in WIPED:
                plan.append(Write(lun, label, start, min(sectors, WIPE_BYTES // size), size, None))
            elif name in files:
                if files[name]["size"] != sectors * size:
                    msg = f"{name} does not have its partition's size"
                    raise ValueError(msg)
                plan.append(Write(lun, label, start, sectors, size, folder / name))
            elif label not in NOT_KEPT:
                msg = f"the backup holds no image of {label} (LUN {lun})"
                raise ValueError(msg)
    return plan


def storage_sectors(output: str) -> dict[int, int]:
    """Return each LUN's size in sectors as the loader reports it (getstorageinfo) in edl-ng's debug output.

    These are the LUNs' own sizes, which do not depend on their partition tables (since interface 1.10).
    """
    found: dict[int, int] = {}
    total = None
    for line in output.splitlines():
        blocks = re.search(r'"total_blocks":\s*(\d+)', line)
        if blocks:
            total = int(blocks.group(1))
            continue
        lun = re.search(r"UFS Current LUN Number: = 0x([0-9a-fA-F]+)", line)
        if lun and total is not None:
            found.setdefault(int(lun.group(1), 16), total)
            total = None
    return found


def check_storage(backup: dict[int, int], device: dict[int, int]) -> None:
    """Refuse a device whose LUNs differ from the backup's in number or size (since interface 1.10)."""
    if set(backup) != set(device):
        msg = f"the headset has the LUNs {sorted(device)}, the backup {sorted(backup)}: nothing was written"
        raise ValueError(msg)
    other = [
        f"LUN {lun}: {device[lun]} sectors, the backup's {backup[lun]}" for lun in backup if backup[lun] != device[lun]
    ]
    if other:
        msg = "the headset's LUNs have other sizes than the backup's (" + "; ".join(other) + "): nothing was written"
        raise ValueError(msg)


def _same(a: Path, b: Path, size: int) -> bool:
    """Tell whether the first @p size bytes of @p a and @p b are equal, and @p b is that long."""
    with a.open("rb") as one, b.open("rb") as other:
        left = size
        while left:
            block = one.read(min(left, 1 << 20))
            if not block or other.read(len(block)) != block:
                return False
            left -= len(block)
    return b.stat().st_size >= size


def write_back(
    plan: Sequence[Write],
    program: str | Path,
    loader: str | Path,
    work: Path,
    runner: Callable[[list[str | Path], float], object],
) -> None:
    """Write @p plan through edl-ng (write-sector), each write read back into @p work and compared before the next.

    A write that reads back other bytes raises, the device left in EDL mode with everything before it
    written. Progress lines count the bytes (since interface 1.10).
    """
    work.mkdir(parents=True, exist_ok=True)
    readback, zeros = work / "readback.bin", work / "zeros.bin"
    total = sum(w.size for w in plan) or 1
    done = 0
    for write in plan:
        image = write.image
        if image is None:
            zeros.write_bytes(bytes(write.size))
            image = zeros
        progress.say(f"writing {write.label} (LUN {write.lun})", done / total)
        timeout = 120 + write.size / (10 << 20)
        runner(edl_ng(program, loader, "write-sector", str(write.start), image, "--lun", str(write.lun)), timeout)
        readback.unlink(missing_ok=True)
        read = ["read-sector", str(write.start), str(write.sectors), readback, "--lun", str(write.lun)]
        runner(edl_ng(program, loader, *read), timeout)
        if not _same(image, readback, write.size):
            msg = (
                f"{write.label} (LUN {write.lun}) reads back other bytes than written; the headset stays in EDL mode "
                "with the partitions before it written"
            )
            raise RuntimeError(msg)
        readback.unlink()
        done += write.size
    zeros.unlink(missing_ok=True)
    progress.say("written and read back", 1.0)
