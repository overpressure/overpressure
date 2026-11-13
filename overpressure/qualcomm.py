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
import re
import sys
import xml.etree.ElementTree as ET
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


def backup_commands(args: argparse.Namespace) -> list[list[str]]:
    """Return the EDL command lines of a full backup (ARGS as edl_command's, with the backup's)."""
    skip = ",".join(skipped(include_userdata=args.include_userdata))
    if args.edl_backend == "edl-ng":
        if not args.edl_luns or len(set(args.edl_luns)) != len(args.edl_luns):
            msg = "edl-ng backup requires distinct --edl-luns from the device's GPT scan"
            raise ValueError(msg)
        # The skipped partitions stay in the rawprogram maps (their sizes), without an image.
        return [
            edl_command(
                args, "dump-rawprogram", str((args.backup / f"lun{lun}").resolve()), "--lun", str(lun), "--skip", skip
            )
            for lun in args.edl_luns
        ]
    if args.edl_luns:
        msg = "--edl-luns applies only to the edl-ng backend"
        raise ValueError(msg)
    return [edl_command(args, "rl", str(args.backup.resolve()), "--genxml", f"--skip={skip}")]


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
) -> dict:
    """Take a full EDL backup into the new folder @p folder and write its manifest; return the manifest.

    @p commands are backup_commands' (with @p backend, @p luns and @p include_userdata as they
    were made); @p runner runs each, its output going to edl.log. No manifest is written unless
    the dump is complete: edl-ng's files against its rawprogram maps, an image of every @p required
    partition, the GPT and rawprogram metadata, and userdata when asked for.
    """
    if folder.exists():
        msg = "Use a new backup directory; existing or partial backups are never overwritten"
        raise ValueError(msg)
    folder.mkdir(parents=True)
    with (folder / "edl.log").open("w", encoding="utf-8") as log:
        for command in commands:
            output = runner(command, timeout=86400)
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


#: edl-ng's words when the device in EDL mode gives it no answer it can place (its mode stays Unknown or Error).
_UNANSWERED = re.compile(r"Device mode is \w+ or could not be determined")
#: What that means, in plain words: a device in EDL mode opens the conversation once, right after it appears.
NO_ANSWER = (
    "The headset is in EDL mode but does not answer: it opens the conversation only right after it enters "
    "EDL mode, and that moment has passed (it waited too long, or an earlier attempt failed)."
)


def unanswered(output: str) -> bool:
    """Tell whether edl-ng's @p output says the device in EDL mode did not answer (a stale EDL session)."""
    return _UNANSWERED.search(output) is not None


def serial_shown(output: str) -> bool:
    """Tell whether edl-ng's @p output names the device's serial.

    It does when its session starts with the device's Sahara handshake. A device still running the
    Firehose loader of an earlier session (on Linux the loader keeps running between sessions;
    Windows' driver starts each afresh) names none: a reset into EDL (fresh_edl_argv) brings the
    handshake back.
    """
    return re.search(r"Serial Number:\s*\S+", output) is not None


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
        try:
            import libusb_package  # noqa: PLC0415  # pinned in pyproject.toml; imported where used
            import usb.core  # noqa: PLC0415  # pinned in pyproject.toml; imported where used
            import usb.util  # noqa: PLC0415  # pinned in pyproject.toml; imported where used
        except ImportError:
            return False
        backend = libusb_package.get_libusb1_backend()
        device = usb.core.find(idVendor=self.vendor, idProduct=self.product, backend=backend)
        if device is None:
            return True
        where = (int(device.bus or 0), int(device.address or 0))
        if self.seen is None:
            self.seen = where
        elif where != self.seen:
            usb.util.dispose_resources(device)
            return True  # the restarted device: left alone
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
                return False
            usb.util.claim_interface(device, 0)
            for _ in range(64):  # what is queued, a packet at a time, until a read times out
                device.read(endpoint.bEndpointAddress, 16384, timeout=250)
        except usb.core.USBError:  # a timeout (nothing more queued), or the device left while reading
            pass
        finally:
            usb.util.dispose_resources(device)
        return False


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
    if not re.search(rf"Serial Number:\s*{re.escape(serial)}\b", output, re.IGNORECASE):
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
