# SPDX-License-Identifier: Unlicense
"""Bounded, serial-bound PICO 4 tests: the preflight on PICO OS, and flash-run with the stock boot restored."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import hashlib
import io
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable, TypeVar
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from overpressure import REPO, android, bootimg, compress, cpio, qualcomm, windows_ncm
from overpressure import backup as backups
from overpressure.android import fastboot_value

from . import device, userspace

if TYPE_CHECKING:
    import os
    from collections.abc import Sequence
    from email.message import Message

ROOT = Path(__file__).resolve().parent.parent  # devices/phoenix
T = TypeVar("T")
#: A row of the storage inventory: BLOCK, the device's name and its read-only flag.
BLOCK_FIELDS = 3
#: A row of /proc/mounts: device, mount point, type, options, dump and pass.
MOUNT_FIELDS = 6
MOUNT_POINT = 1
#: The boot image header versions whose fields the stock and test images are compared by.
MAX_HEADER_VERSION = 2
#: What PICO OS shows to identify the headset, one line each.
IDENTITY_COMMANDS = (
    "getprop ro.build.fingerprint",
    "getprop ro.oem.state",
    "cat /sys/devices/soc0/serial_number",
    "getprop sys.boot_completed",
)
#: What stage 1 shows to prove it is this test run on this headset: the run ID and the SoC serial.
STAGE1_PROOF = ("/etc/pico4/test-run", "/sys/devices/soc0/serial_number")


class NoRedirect(HTTPRedirectHandler):
    """Follow no redirect: the headset's address is the only one the runner talks to."""

    def redirect_request(  # noqa: PLR0913, PLR0917  # urllib's signature
        self,
        req: Request,  # noqa: ARG002  # urllib's signature
        fp: IO[bytes],  # noqa: ARG002  # urllib's signature
        code: int,  # noqa: ARG002  # urllib's signature
        msg: str,  # noqa: ARG002  # urllib's signature
        headers: Message,  # noqa: ARG002  # urllib's signature
        newurl: str,  # noqa: ARG002  # urllib's signature
    ) -> None:
        """Refuse every redirect."""
        return


def utc() -> str:
    """Return the current time in UTC, as the result records it."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require(condition: object, message: str) -> None:
    """Raise ValueError with @p message unless @p condition holds."""
    if not condition:
        raise ValueError(message)


def validate_preflight(output: str) -> None:
    """Refuse a preflight output without exactly one summary of only passed checks."""
    summaries = re.findall(r"^PREFLIGHT: (\d+) passed, (\d+) failed\s*$", output, re.MULTILINE)
    require(
        len(summaries) == 1 and int(summaries[0][0]) > 0 and int(summaries[0][1]) == 0,
        "Preflight failed or did not produce a complete result",
    )


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


def data_device(mounts: str) -> str:
    """Return the block device (sda16) mounted at /data, or empty."""
    for row in mounts.splitlines():
        fields = row.split()
        if len(fields) > MOUNT_POINT and fields[MOUNT_POINT] == "/data" and fields[0].startswith("/dev/"):
            return fields[0][len("/dev/") :]
    return ""


def validate_mounts(output: str, *, installed: bool = False) -> None:
    """Refuse a writable device-backed mount: stage 1 runs from RAM, the installed system read-only."""
    rows = [row.split() for row in output.splitlines()]
    require(rows and all(len(r) == MOUNT_FIELDS for r in rows), "Invalid mount inventory")
    roots = [r for r in rows if r[1] == "/"]
    writable = [r for r in rows if r[0].startswith("/dev/") and "rw" in r[3].split(",")]
    if installed:
        # The installed system: its image read-only; writable only the data partition, the device
        # at /data, wherever it is bound (/var, /home, SteamOS's offload binds of /srv, /var/log...).
        require(
            roots and all(r[0].startswith("/dev/") and "ro" in r[3].split(",") for r in roots),
            "The installed system's root is not mounted read-only",
        )
        data = {r[0] for r in writable if r[1] == "/data"}
        require(
            len(data) == 1 and {r[0] for r in writable} == data,
            "A device-backed filesystem other than the data partition is mounted writable",
        )
        return
    require(roots and all(r[2] in ("rootfs", "tmpfs", "ramfs") for r in roots), "Root filesystem is not RAM-backed")
    require(not writable, "A device-backed filesystem is mounted writable")


def validate_report(data: bytes, run_id: str) -> None:
    """Refuse a hardware report that lacks a member, or comes from another boot."""
    # Inspect members without extracting device-controlled archive paths.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in ("report/summary.txt", "report/system.txt", "report/dmesg.txt"):
            member = archive.getmember(name)
            require(member.isfile() and 0 < member.size <= 16 * 1024 * 1024, f"Invalid report member: {name}")
            reader = archive.extractfile(member)
            if reader is None:
                msg = f"Invalid report member: {name}"
                raise ValueError(msg)
            payload = reader.read()
            if name == "report/system.txt":
                require(run_id.encode() in payload, "HTTP report is from a different boot")


def prepare_image(  # noqa: PLR0913  # the image's inputs; its deadlines and mode keyword-only
    source: Path,
    folder: Path,
    run_id: str,
    public_key: bytes,
    *,
    connect_timeout: int = 210,
    test_timeout: int = 900,
    userspace: bool = False,
) -> dict:
    """Write the test boot image: the stage-1 image with this run's key, ID and device deadlines."""
    original = source.read_bytes()
    image = bootimg.parse(original)
    require(image.id == image.compute_id(), "Source boot image checksum mismatch")
    words = image.full_cmdline.split()
    require(
        not any(
            w in ("pico4.rw", "pico4.selftest")
            or (w.startswith(("pico4.rw=", "pico4.selftest=")) and not w.endswith("=0"))
            for w in words
        ),
        "Hardware tests require read-only mode and QEMU selftest disabled",
    )
    tree = cpio.Tree(nodes=dict(cpio.read_cpio(compress.decompress(image.ramdisk)[0])))
    require("etc/pico4/build-info" in tree.nodes, "Image is not a PICO 4 POC build")
    require(
        "usr/libexec/pico4/recovery-watch" in tree.nodes
        and "etc/inittab" in tree.nodes
        and b"svc recovery-watch" in tree.nodes["etc/inittab"].data,
        "Rebuild stage 1 image: independent recovery watchdog is missing",
    )
    if userspace:
        require(
            "usr/bin/pico4-userspace" in tree.nodes
            and "usr/libexec/pico4/pico4-init" in tree.nodes
            and b"::restart:/usr/libexec/pico4/switch-userspace" in tree.nodes["etc/inittab"].data,
            "Rebuild stage 1 image: the userspace handoff is missing",
        )
    info = json.loads(tree.nodes["etc/pico4/build-info"].data)
    require(0 < connect_timeout < test_timeout, "Invalid device recovery deadlines")
    # Preflight runs the exact source userspace, excluding device nodes.
    with (folder / "rootfs.tar").open("wb") as stream:
        tree.to_tar(stream)
    tree.mkdir("root/.ssh", 0o700)
    tree.add_file("root/.ssh/authorized_keys", public_key.rstrip() + b"\n", 0o600)
    tree.add_file("etc/pico4/test-run", run_id.encode() + b"\n")
    tree.add_file("etc/pico4/test-connect-timeout", f"{connect_timeout}\n".encode())
    tree.add_file("etc/pico4/test-timeout", f"{test_timeout}\n".encode())
    words = [w for w in words if not w.startswith("pico4.test_run=")]
    image = image.with_cmdline(" ".join([*words, f"pico4.test_run={run_id}"]))
    image.ramdisk = gzip.compress(tree.to_cpio(), mtime=0)
    image.boot_signature = b""
    data = image.pack()
    check = bootimg.parse(data)
    require(
        check.kernel == image.kernel and check.dtb == image.dtb and check.id == check.compute_id(),
        "Instrumented boot image failed validation",
    )
    (folder / "test-boot.img").write_bytes(data)
    return {
        "source_image": str(source.resolve()),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "boot_sha256": hashlib.sha256(data).hexdigest(),
        "build_info": info,
        "device_recovery": {"connect_timeout": connect_timeout, "test_timeout": test_timeout},
    }


def userspace_state(archive: Path) -> dict:
    """Return the archive's identity and the features its manifest says it contains."""
    manifest = json.loads(archive.with_name("pico4-userspace.manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    require(manifest.get("archive", {}).get("sha256") == digest, "Userspace archive does not match its manifest")
    packages = manifest.get("packages", {})
    return {
        "archive": str(archive.resolve()),
        "sha256": digest,
        "bytes": archive.stat().st_size,
        "packages": len(packages),
        "systemd": packages.get("systemd"),
        "gpu_firmware": bool(manifest.get("gpu_firmware")),
        "sensor_data": bool(manifest.get("sensor_data")),
        "steamvr": "deckard-steamvr-rel" in packages,
    }


def prepare_restore_image(backup: Path, folder: Path, serial: str, identity: dict, image: Path) -> dict:
    """Copy the verified stock boot image into the results, after checking the test image against it."""
    require(
        backup.resolve() not in (folder.resolve(), *folder.resolve().parents),
        "Test results must be outside the immutable backup directory",
    )
    manifest = backups.verified_backup(backup)
    saved = manifest.get("identity", {})
    require(
        saved.get("serial") == serial and all(saved.get(key) == value for key, value in identity.items()),
        "Backup identity does not match this headset and firmware",
    )
    names = [
        name
        for name in manifest["files"]
        if re.fullmatch(r"boot(?:_[ab])?", Path(name).stem) and Path(name).suffix in (".bin", ".img")
    ]
    require(
        len(names) == 1 and Path(names[0]).stem == "boot",
        "Flash tests require exactly one original, non-slotted boot partition",
    )
    original = backup / names[0]
    expected = manifest["files"][names[0]]
    require(image.stat().st_size <= expected["size"], "Test image exceeds the boot partition")
    stock, test = bootimg.parse(original.read_bytes()), bootimg.parse(image.read_bytes())
    require(
        stock.header_version <= MAX_HEADER_VERSION and stock.id == stock.compute_id(), "Stock boot checksum mismatch"
    )
    for field in (
        "header_version",
        "page_size",
        "kernel",
        "second",
        "recovery_dtbo",
        "dtb",
        "name",
        "os_version",
        "kernel_addr",
        "ramdisk_addr",
        "second_addr",
        "tags_addr",
        "dtb_addr",
    ):
        require(getattr(stock, field) == getattr(test, field), f"POC differs from backed-up stock {field}")
    target = folder / "stock-boot.img"
    with original.open("rb") as source, target.open("xb") as output:
        shutil.copyfileobj(source, output)
    require(backups.digest(target) == expected, "Restoration copy differs from the verified backup")
    return {"source": str(original.resolve()), "path": str(target.resolve()), **expected}


class Runner:
    """The bounded acceptance run on one headset: every step recorded, the headset always brought back."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Start a run with the command line's @p args; its results go to a new folder."""
        self.args = args
        self.folder = args.out.resolve()
        self.flash_mode = args.command == "flash-run"
        if self.flash_mode:
            require(
                args.backup.resolve() not in (self.folder, *self.folder.parents),
                "Test results must be outside the immutable backup directory",
            )
        self.folder.mkdir(parents=True, exist_ok=False)
        self.run_id = uuid.uuid4().hex
        self.result: dict[str, Any] = {
            "schema": 1,
            "run_id": self.run_id,
            "started": utc(),
            "status": "running",
            "serial": args.serial,
            "checks": [],
            "recovery": "not_needed",
        }
        self.count = 0
        self.moved = False
        self.boot_attempted = False
        self.authenticated = False
        self.ncm_initialized = False
        self.baseline: dict[str, Any] | None = None
        self.boot_dirty = False
        self.result.update(boot_restore="not_needed", boot_restore_required=False)
        self.save()

    def save(self) -> None:
        """Write the result file, replacing the last one whole."""
        temp = self.folder / "result.json.tmp"
        temp.write_text(json.dumps(self.result, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.folder / "result.json")

    def command(self, argv: Sequence[str | os.PathLike[str]], timeout: float = 30) -> str:
        """Run a host program, its command line and output kept as evidence; return its output."""
        self.count += 1
        prefix = self.folder / f"command-{self.count:04d}"
        Path(str(prefix) + ".json").write_text(
            json.dumps({"argv": list(map(str, argv)), "timeout": timeout, "started": utc()}, indent=2), encoding="utf-8"
        )
        try:
            process = subprocess.run(  # noqa: S603  # adb, fastboot, ssh and the NCM helper by name, no shell
                list(map(str, argv)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            Path(str(prefix) + ".log").write_bytes((exc.stdout or b"") + b"\nTIMEOUT\n")
            msg = f"Command timed out; see {prefix.name}.log"
            raise ValueError(msg) from exc
        output = process.stdout
        Path(str(prefix) + ".log").write_bytes(output)
        text = output.decode("utf-8", errors="replace").strip()
        require(
            process.returncode == 0 and not re.search(r"\bFAILED\b", text),
            f"Command failed ({process.returncode}); see {prefix.name}.log",
        )
        return text

    def adb(self, *words: str, timeout: float = 30) -> str:
        """Run adb on the selected headset; return its output."""
        return self.command([self.args.adb, "-s", self.args.serial, *words], timeout)

    def fastboot(self, *words: str, timeout: float = 30) -> str:
        """Run fastboot on the selected headset; return its output."""
        return self.command([self.args.fastboot, "-s", self.args.fastboot_serial or self.args.serial, *words], timeout)

    def ssh(self, command: str, timeout: float = 30) -> str:
        """Run @p command on stage 1 with this run's key, host keys pinned on first use; return its output."""
        return self.command(
            [
                self.args.ssh,
                "-F",
                self.folder / "ssh_config",
                "-i",
                self.folder / "id_ed25519",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                f'UserKnownHostsFile="{(self.folder / "known_hosts").as_posix()}"',
                "-o",
                f'GlobalKnownHostsFile="{(self.folder / "known_hosts").as_posix()}"',
                f"root@{self.args.host}",
                command,
            ],
            timeout,
        )

    def step(self, name: str, operation: Callable[[], T]) -> T:
        """Run one step of the run, recorded in the result whether it passes or fails."""
        print(f">> {name}", flush=True)
        record = {"name": name, "started": utc(), "status": "running"}
        self.result["checks"].append(record)
        self.save()
        try:
            value = operation()
        except (Exception, KeyboardInterrupt) as exc:
            record.update(status="failed", error=str(exc) or "Interrupted")
            raise
        else:
            record["status"] = "passed"
            return value
        finally:
            record["finished"] = utc()
            self.save()

    def wait(self, name: str, seconds: float, operation: Callable[[float], T]) -> T:
        """Retry @p operation, given the time left for each attempt, until it succeeds or @p seconds pass."""
        deadline = time.monotonic() + seconds
        last = "no response"
        while time.monotonic() < deadline:
            try:
                return operation(max(0.1, min(10, deadline - time.monotonic())))
            except (ValueError, OSError) as exc:  # noqa: PERF203  # each attempt may fail until the deadline
                last = str(exc)
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        msg = f"Timed out waiting for {name}: {last}"
        raise ValueError(msg)

    def identity(self, timeout: float = 30) -> dict[str, Any]:
        """Return the identity of the booted PICO OS: its fingerprint, OEM state and SoC ID."""
        output = self.adb("shell", "; ".join(IDENTITY_COMMANDS), timeout=timeout)
        lines = output.splitlines()
        require(len(lines) == len(IDENTITY_COMMANDS) and lines[3].strip() == "1", "PICO OS has not completed boot")
        require(
            re.fullmatch(r"Pico/[Pp]hoenix(?:_ovs)?/PICOA8110:10/\S+", lines[0]),
            "Selected device is not an original PICO 4",
        )
        return {"fingerprint": lines[0], "oem_state": lines[1], "chip_id": qualcomm.chip_id(lines[2])}

    def preflight(self, rootfs: Path) -> None:
        """Run the stage-1 root's programs on PICO OS from adb shell, without root; remove them after."""
        remote = f"/data/local/tmp/pico4-stage1-{self.run_id}"
        try:
            self.adb("shell", f"mkdir -p {remote}")
            self.adb("push", str(rootfs), remote + "/rootfs.tar", timeout=180)
            self.adb("push", str(ROOT / "scripts/preflight-device.sh"), remote + "/preflight.sh")
            output = self.adb("shell", f"PICO4_PREFLIGHT_DIR={remote} sh {remote}/preflight.sh", timeout=300)
            (self.folder / "preflight.txt").write_text(output + "\n", encoding="utf-8")
            validate_preflight(output)
        finally:
            self.adb("shell", f"rm -rf {remote}")

    def prepare(self) -> None:
        """Make this run's SSH key and configuration and its test boot image."""
        if getattr(self.args, "windows_ncm_python", None):
            readiness = json.loads(self.command(windows_ncm.command(self.args.windows_ncm_python), timeout=15))
            require(readiness.get("status") == "ready", "Windows NCM helper is not ready")
            self.result["windows_ncm"] = readiness
        self.command([self.args.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", self.folder / "id_ed25519"])
        (self.folder / "ssh_config").write_text(
            f"Host pico4\n  HostName {self.args.host}\n  User root\n"
            f'  IdentityFile "{(self.folder / "id_ed25519").as_posix()}"\n'
            "  IdentitiesOnly yes\n  BatchMode yes\n  ConnectTimeout 5\n  StrictHostKeyChecking yes\n"
            f'  UserKnownHostsFile "{(self.folder / "known_hosts").as_posix()}"\n'
            f'  GlobalKnownHostsFile "{(self.folder / "known_hosts").as_posix()}"\n',
            encoding="utf-8",
        )
        self.result.update(
            prepare_image(
                self.args.image,
                self.folder,
                self.run_id,
                (self.folder / "id_ed25519.pub").read_bytes(),
                connect_timeout=self.args.boot_timeout + 30,
                test_timeout=self.args.boot_timeout + self.args.report_timeout + 300,
            )
        )

    def stock_identity(self) -> dict[str, Any]:
        """Return the headset's identity from before the test, which every write is checked against."""
        if self.baseline is None:
            msg = "The stock headset has not been identified"
            raise ValueError(msg)
        return self.baseline

    def prepare_restore(self) -> None:
        """Pin the stock boot image and record the commands that flash it back by hand."""
        require(self.args.confirm_device == self.args.serial, "Flash tests require --confirm-device matching --serial")
        self.result["stock_boot"] = prepare_restore_image(
            self.args.backup, self.folder, self.args.serial, self.stock_identity(), self.folder / "test-boot.img"
        )
        base = [self.args.fastboot, "-s", self.args.fastboot_serial or self.args.serial]
        self.result["manual_restore_commands"] = [
            [*base, "oem", device.unlock_token(self.stock_identity()["chip_id"]), "unlock"],
            [*base, "flash", "boot", self.result["stock_boot"]["path"]],
            [*base, "reboot"],
        ]
        self.save()

    def check_restore_image(self) -> None:
        """Refuse to flash anything once the pinned stock boot image changed."""
        saved = self.result["stock_boot"]
        require(
            backups.digest(Path(saved["path"])) == {key: saved[key] for key in ("size", "sha256")},
            "Pinned stock restoration image changed; refusing to flash",
        )

    def check_fastboot_unlock(self) -> None:
        """Refuse a locked bootloader, or one whose state is unknown."""
        try:
            output = self.fastboot("oem", "device-info")
        except ValueError:
            output = self.fastboot("getvar", "unlocked")
        require(android.unlocked(output), "Bootloader is locked or unlock state is unknown")

    def authorize_flash(self) -> None:
        """Check the restore image, the unlock and the boot partition; then send the session's authorization."""
        self.check_restore_image()
        self.check_fastboot_unlock()
        # This engineering ABL lists every partition but lacks getvar has-slot.
        inventory = self.fastboot("getvar", "all")
        names = re.findall(r"^[ \t]*(?:\(bootloader\)[ \t]*)?partition-size:(boot(?:_[ab])?):", inventory, re.MULTILINE)
        require(names == ["boot"], "Flash tests only support a non-slotted boot partition")
        size = fastboot_value(inventory, "partition-size:boot")
        require(
            re.fullmatch(r"0x[0-9a-fA-F]+", size) and int(size, 16) == self.result["stock_boot"]["size"],
            "Live boot partition size differs from its original backup",
        )
        # Engineering ABL's per-session command authorization; no flashing unlock/reset.
        self.fastboot("oem", device.unlock_token(self.stock_identity()["chip_id"]), "unlock")

    def enter_fastboot(self) -> None:
        """Reboot PICO OS into the bootloader and wait for an unlocked fastboot."""
        self.moved = True  # Even a command timeout may have rebooted the device.
        self.adb("reboot", "bootloader")
        serial = self.args.fastboot_serial or self.args.serial

        def detect(timeout: float) -> None:
            output = self.command([self.args.fastboot, "devices"], timeout)
            require(
                any(line.split()[:2] == [serial, "fastboot"] for line in output.splitlines()),
                "Selected fastboot serial is missing",
            )

        self.wait("fastboot", self.args.boot_timeout, detect)
        self.check_fastboot_unlock()

    def boot(self) -> None:
        """Flash the test boot image, journalled first, and start it."""
        path = self.folder / "test-boot.img"
        require(backups.digest(path)["sha256"] == self.result["boot_sha256"], "Test image changed before boot")
        require(self.flash_mode, "Only flash-run boots the test image")
        self.authorize_flash()
        # A failed or timed-out write may still have changed storage. Journal first.
        self.boot_dirty = True
        self.result["boot_restore"] = "required"
        self.result["boot_restore_required"] = True
        self.save()
        self.fastboot("flash", "boot", str(path), timeout=self.args.boot_timeout)
        self.boot_attempted = True
        self.fastboot("reboot")

    def connect(self, timeout: float) -> None:
        """Initialize the Windows NCM link once, then authenticate stage 1 over SSH."""
        if getattr(self.args, "windows_ncm_python", None) and not self.ncm_initialized:
            serial = self.args.fastboot_serial or self.args.serial
            initialized = json.loads(self.command(windows_ncm.command(self.args.windows_ncm_python, serial), timeout))
            require(
                initialized.get("status") == "initialized" and initialized.get("serial") == serial,
                "Windows NCM initialization did not identify this headset",
            )
            self.ncm_initialized = True
            self.result["windows_ncm"] = initialized
            self.save()
            # Return through wait() so initialization and SSH each receive a
            # fresh timeout bounded by the same connection deadline.
            msg = "Windows NCM initialized; waiting for DHCP and SSH"
            raise ValueError(msg)
        self.authenticate(timeout)

    def authenticate(self, timeout: float) -> None:
        """Refuse an SSH endpoint that is not this run on this headset; cancel its connection deadline."""
        output = self.ssh("; ".join(f"cat {path}" for path in STAGE1_PROOF), timeout)
        lines = output.splitlines()
        require(
            len(lines) == len(STAGE1_PROOF)
            and lines[0] == self.run_id
            and qualcomm.chip_id(lines[1]) == self.stock_identity()["chip_id"],
            "SSH endpoint is not the expected test run and headset",
        )
        self.authenticated = True
        # Only a verified endpoint may cancel the connection deadline. The
        # independent maximum test duration still recovers a crashed runner.
        self.ssh("touch /run/pico4/test-authenticated", timeout)

    def assertions(self, suffix: str = "") -> None:
        """Check the running build, the root shell, read-only storage and, after a flash, the readback."""
        info = json.loads(self.ssh("cat /etc/pico4/build-info"))
        require(info == self.result["build_info"], "Running build metadata differs from supplied image")
        require(self.ssh("uname -m") == "aarch64", "Linux is not running ARM64")
        release = self.ssh("uname -r")
        require(info.get("kernel", "").startswith(f"Linux version {release} "), "Unexpected running kernel")
        require(self.ssh("id -u") == "0", "SSH did not provide stage 1 root shell")
        require(self.ssh("printf 'SHELL-%s' $((6*7))") == "SHELL-42", "SSH shell execution failed")
        storage = self.ssh(
            'for d in /sys/class/block/*; do [ -e "$d" ] || continue; '
            'printf \'BLOCK %s %s\\n\' "${d##*/}" "$(cat "$d/ro")"; done'
        )
        (self.folder / f"storage{suffix}.txt").write_text(storage + "\n", encoding="utf-8")
        mounts = self.ssh("cat /proc/mounts")
        (self.folder / f"mounts{suffix}.txt").write_text(mounts + "\n", encoding="utf-8")
        installed = getattr(self, "installed", False)
        validate_mounts(mounts, installed=installed)
        validate_storage(storage, data_device(mounts) if installed else "")
        if self.flash_mode:
            size = (self.folder / "test-boot.img").stat().st_size
            output = self.ssh(f"head -c {size} /dev/block/by-name/boot | sha256sum")
            (self.folder / f"boot-readback{suffix}.txt").write_text(output + "\n", encoding="utf-8")
            require(
                output.split() and output.split()[0] == self.result["boot_sha256"],
                "Flashed test image failed readback verification",
            )

    def http(self, endpoint: str, timeout: float = 15) -> bytes:
        """Fetch @p endpoint from stage 1's web server, bounded in time and size."""
        # The USB address must bypass desktop/environment HTTP proxies.
        url = f"http://{self.args.host}/{endpoint}"
        deadline = time.monotonic() + timeout
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=timeout) as response:
            require(response.status == HTTPStatus.OK and response.url == url, "Unexpected HTTP response/redirect")
            data = bytearray()
            while True:
                require(time.monotonic() < deadline, "HTTP transfer exceeded its deadline")
                block = response.read1(65536)
                if not block:
                    return bytes(data)
                data.extend(block)
                require(len(data) <= 32 * 1024 * 1024, "HTTP response exceeds test limit")

    def diagnostics(self) -> None:
        """Fetch stage 1's status page, boot log and hardware report over HTTP, and its kernel log."""
        status = self.http("cgi-bin/status")
        (self.folder / "http-status.txt").write_bytes(status)
        require(b"PICO 4 Linux" in status, "HTTP status page is not stage 1")
        log = self.http("cgi-bin/log?boot")
        (self.folder / "boot.log").write_bytes(log)
        require(self.run_id.encode() in log, "HTTP log does not identify this boot")
        # Wait for the init-triggered probe instead of starting a competing probe.
        self.wait(
            "hardware report",
            self.args.report_timeout,
            lambda timeout: require(
                self.ssh("test -s /run/pico4/report.tar.gz && echo READY", timeout) == "READY", "Report not ready"
            ),
        )
        report = self.http("cgi-bin/report")
        (self.folder / "report.tar.gz").write_bytes(report)
        validate_report(report, self.run_id)
        (self.folder / "dmesg.txt").write_text(self.ssh("dmesg"), encoding="utf-8")

    def evidence(self, filename: str, command: str) -> None:
        """Keep a command's output as evidence; best effort, the checks decide."""
        try:
            (self.folder / filename).write_text(self.ssh(command, 60), encoding="utf-8")
        except (ValueError, OSError) as exc:
            (self.folder / filename).write_text(f"unavailable: {exc}\n", encoding="utf-8")

    def userspace_checks(self, state: dict) -> None:
        """Everything checked in a running stage 2, in the order each feature builds on the last."""
        self.installed = bool(state.get("installed"))
        output = self.ssh("pico4-userspace-check --wait 240; echo EXIT=$?", 300)
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-check.json").write_text(report.strip() + "\n", encoding="utf-8")
        for name, command in (
            ("journal", "journalctl -b --no-pager -o short-monotonic"),
            ("units", "systemctl list-units --all --no-pager --plain"),
            ("analyze", "systemd-analyze; systemd-analyze blame --no-pager | head -n 40"),
            ("dmesg", "dmesg"),
        ):
            self.evidence(f"userspace-{name}.txt", command)
        value = json.loads(report)
        state["check"] = {
            k: value.get(k) for k in ("status", "errors", "systemd", "versions", "vulkan", "fan", "power")
        }
        state["warnings"] = userspace.power_warnings(value.get("power"))
        self.save()
        for warning in state["warnings"]:
            print(f"   warning: {warning}", flush=True)
        require(code.strip() == "0", "Userspace check failed")
        userspace.validate_check(value, require_vulkan=state["gpu_firmware"])
        # The storage and identity guarantees of stage 1 must hold in stage 2.
        self.assertions("-userspace")
        if state["sensor_data"]:
            self.head_imu(state)

    def head_imu(self, state: dict) -> None:
        """Roadmap step 5: stream the head IMU through the sensor DSP (docs/xr.md)."""
        print(">> head IMU: sensor DSP and SSC stream", flush=True)
        for name, command in (
            ("sensors-units", "systemctl status --no-pager -l pico4-slpi pico4-sensorspd"),
            ("sensors-list", "pico4-imu list accel gyro"),
        ):
            self.evidence(f"userspace-{name}.txt", command)
        output = self.ssh(
            f"pico4-imu stream --rate {userspace.IMU_RATE_HZ:g} --seconds 10 --check --orientation; echo EXIT=$?", 120
        )
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-imu.json").write_text(report.strip() + "\n", encoding="utf-8")
        value = json.loads(report)
        state["imu"] = {k: value.get(k) for k in ("status", "failures", "streams", "orientation")}
        self.save()
        require(code.strip() == "0", "Head IMU check failed")
        userspace.validate_imu(value)
        if state.get("steamvr"):
            paused = self.pause_session()
            try:
                self.valve_session(state)
            finally:
                if paused:
                    self.resume_session()

    def pause_session(self) -> bool:
        """Stop the session for Valve's session check; True if it ran.

        Valve's session holds the panels and the controller radio (SteamVR's
        driver_monado). The session's stop returns before the user manager has
        stopped SteamVR and the Steam client, so this waits for them (bounded):
        a check started earlier lost the controller radio and frames.
        """
        for name, command in (
            (
                "session",
                (
                    "loginctl list-sessions --no-pager; loginctl user-status steamos --no-pager -l; "
                    "runuser -u steamos -- env XDG_RUNTIME_DIR=/run/user/1000 "
                    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
                    "systemctl --user list-units --no-pager --plain"
                ),
            ),
        ):
            self.evidence(f"userspace-{name}.txt", command)
        output = self.ssh(
            "if systemctl is-active -q pico4-session.service; then systemctl stop pico4-session.service; "
            f"for i in $(seq {userspace.SESSION_STOP_SECONDS}); do "
            f"pgrep -u {userspace.SESSION_USER} -x '{userspace.SESSION_PROCESSES}' >/dev/null || break; "
            "sleep 1; done; echo SESSION-PAUSED; fi",
            userspace.SESSION_STOP_SECONDS + 60,
        )
        return "SESSION-PAUSED" in output

    def resume_session(self) -> None:
        """Start the session again, unless Valve's session check already did."""
        self.ssh(
            "systemctl is-active -q pico4-session.service || { systemctl reset-failed pico4-session.service; "
            "systemctl start pico4-session.service; }; true",
            90,
        )

    def valve_session(self, state: dict) -> None:
        """Roadmap step 6: Valve's VR session with gamescope and SteamVR on the panels (docs/session.md).

        The check runs gamescope-session.target briefly (it heats the headset
        quickly, docs/cooling.md), then returns to the configured session.
        driver_monado gives SteamVR the headset and controllers.
        """
        print(">> Valve's session: gamescope and SteamVR with driver_monado", flush=True)
        # The tests before it warmed the headset; let it cool first (bounded).
        started, hottest = time.monotonic(), None
        while True:
            try:
                hottest = float(
                    self.ssh(
                        "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | "
                        "awk '$1 < 200000 && $1 > m {m = $1} END {print m / 1000}'",
                        30,
                    )
                )
            except ValueError:
                break
            if (
                hottest < userspace.VALVE_SESSION_START_C
                or time.monotonic() - started > userspace.VALVE_SESSION_COOLDOWN_SECONDS
            ):
                break
            time.sleep(5)
        state["valve_session_cooldown"] = {"seconds": round(time.monotonic() - started), "hottest_c": hottest}
        self.save()
        # Its stderr (systemctl's notes among it) stays out of the JSON.
        output = self.ssh(
            f"pico4-valve-session-check --seconds {userspace.VALVE_SESSION_SECONDS} "
            "2>/run/pico4/valve-session.log; echo EXIT=$?",
            400,
        )
        report, _, code = output.rpartition("EXIT=")
        (self.folder / "userspace-valve-session.json").write_text(report.strip() + "\n", encoding="utf-8")
        logs = f"/home/{userspace.SESSION_USER}/.local/share/Steam/logs"
        for name, command in (
            ("valve-session.log", "cat /run/pico4/valve-session.log"),
            ("valve-vrcompositor.txt", f"cat {logs}/vrcompositor.txt"),
            ("valve-vrserver.txt", f"tail -n 400 {logs}/vrserver.txt"),
            ("valve-journal.txt", "journalctl -b --no-pager -o short-monotonic _UID=1000 | tail -n 2000"),
        ):
            self.evidence(f"userspace-{name}", command)
        value = json.loads(report)
        frames = value.get("frames") if isinstance(value.get("frames"), dict) else {}
        state["valve_session"] = {
            **{
                k: value.get(k)
                for k in (
                    "status",
                    "errors",
                    "startup_seconds",
                    "session",
                    "compositor",
                    "devices",
                    "max_temp_c",
                    "fan",
                )
            },
            "frames": {k: frames.get(k) for k in ("target", "commits", "commit_hz", "vblank_hz", "late")},
            "performance": value.get("performance"),
        }
        self.save()
        print(
            f"   integration: {value.get('status')}; {userspace.format_valve_performance(value.get('performance'))}",
            flush=True,
        )
        require(code.strip() == "0", "Valve session check failed")
        userspace.validate_valve_session(value)

    def recover(self) -> None:
        """Bring the headset back to the original PICO OS, restoring stock boot if the flash touched it."""
        self.result["recovery"] = "attempting"
        self.save()
        if self.boot_attempted and not self.authenticated:
            with contextlib.suppress(ValueError, OSError):
                self.authenticate(10)
        if self.authenticated:
            # Logs are best effort and must not prevent the recovery attempt.
            with contextlib.suppress(ValueError, OSError):
                (self.folder / "recovery-boot.log").write_text(
                    self.ssh("cat /run/pico4/boot.log", timeout=10), encoding="utf-8"
                )
            try:
                target = " bootloader" if self.boot_dirty else ""
                self.ssh(f"(sleep 2; pico4-reboot{target}) </dev/null >/dev/null 2>&1 &", timeout=10)
            except (ValueError, OSError):
                pass  # A dropped SSH connection can mean the reboot already began.
        if self.boot_dirty:
            self.restore_boot()
        rebooted_fastboot = False

        def stock(timeout: float) -> dict[str, Any]:
            nonlocal rebooted_fastboot
            try:
                identity = self.identity(timeout)
                require(identity == self.baseline, "Returned Android identity does not match original headset/build")
            except (ValueError, OSError):
                if not rebooted_fastboot:
                    serial = self.args.fastboot_serial or self.args.serial
                    output = self.command([self.args.fastboot, "devices"], timeout)
                    if any(line.split()[:2] == [serial, "fastboot"] for line in output.splitlines()):
                        self.fastboot("reboot", timeout=timeout)
                        rebooted_fastboot = True
                msg = "Waiting for original PICO OS to complete boot"
                raise ValueError(msg) from None
            return identity

        self.wait("stock PICO OS recovery", self.args.recovery_timeout, stock)
        self.result["recovery"] = "verified"
        if self.result["boot_restore"] == "flash_acknowledged":
            self.result["boot_restore"] = "android_boot_verified"

    def restore_boot(self) -> None:
        """Flash the pinned stock boot image back once the headset is in fastboot."""
        self.result["boot_restore"] = "attempting"
        self.save()
        serial = self.args.fastboot_serial or self.args.serial

        def ready(timeout: float) -> None:
            output = self.command([self.args.fastboot, "devices"], timeout)
            if any(line.split()[:2] == [serial, "fastboot"] for line in output.splitlines()):
                return
            # A partial/failed write could still have left a bootable stock image.
            identity = self.identity(timeout)
            require(identity == self.baseline, "Unexpected Android identity during restoration")
            self.adb("reboot", "bootloader", timeout=timeout)
            msg = "Waiting for bootloader to restore original boot image"
            raise ValueError(msg)

        self.wait("fastboot for stock boot restoration", self.args.recovery_timeout, ready)
        self.authorize_flash()
        self.fastboot("flash", "boot", self.result["stock_boot"]["path"], timeout=self.args.boot_timeout)
        self.boot_dirty = False
        self.result["boot_restore"] = "flash_acknowledged"
        self.result["boot_restore_required"] = False
        self.save()
        self.fastboot("reboot")

    def execute(self) -> int:
        """Run every step; whatever happens, bring the headset back and record the result. 0 when all passed."""
        failed = False
        try:
            if self.args.command != "preflight":
                self.step("prepare test boot image", self.prepare)
            self.baseline = self.step("identify stock headset", self.identity)
            self.result["identity"] = self.baseline
            if self.flash_mode:
                self.step("verify and pin stock boot restoration image", self.prepare_restore)
            rootfs = self.args.rootfs if self.args.command == "preflight" else self.folder / "rootfs.tar"
            self.step("userspace preflight", lambda: self.preflight(rootfs))
            if self.args.command != "preflight":
                self.step("enter unlocked fastboot", self.enter_fastboot)
                self.step("flash test boot image", self.boot)
                self.step("SSH identity", lambda: self.wait("SSH", self.args.boot_timeout, self.connect))
                self.step("Linux shell and read-only storage", self.assertions)
                self.step("HTTP and diagnostic report", self.diagnostics)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001  # whatever stops the run is its result
            failed = True
            self.result["error"] = str(exc) or "Interrupted"
        finally:
            if self.moved:
                try:
                    self.step("return to original PICO OS", self.recover)
                except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001  # recorded; the run still ends cleanly
                    failed = True
                    self.result["recovery"] = "failed"
                    self.result["recovery_error"] = str(exc) or "Interrupted"
            self.result["boot_restore_required"] = self.boot_dirty
            self.result.update(status="failed" if failed else "passed", finished=utc())
            self.save()
            # A per-run credential must not remain usable after the test.
            (self.folder / "id_ed25519").unlink(missing_ok=True)
        print(f"{self.result['status'].upper()}: {self.folder / 'result.json'}", flush=True)
        return 1 if failed else 0


def newest_rootfs(out: Path = REPO / "out") -> Path:
    """Return the newest stage-1 root file system tar the image build wrote."""
    tars = sorted(out.glob("pico4-stage1-*-rootfs.tar"), key=lambda p: p.stat().st_mtime)
    if not tars:
        msg = "no rootfs tar in out/: run python -m overpressure build first"
        raise SystemExit(msg)
    return tars[-1]


def main(argv: list[str] | None = None) -> int:  # noqa: C901  # every option checked before the headset is touched
    """Run the acceptance runner's command line: preflight or flash-run."""
    ap = argparse.ArgumentParser(prog="python -m overpressure hwtest", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("preflight", "flash-run"):
        p = sub.add_parser(name)
        p.add_argument(
            "--serial", required=name != "preflight", help="authorized ADB serial (preflight can select a sole device)"
        )
        p.add_argument("--adb", default="adb")
        p.add_argument(
            "--out",
            type=Path,
            default=REPO / "out" / ("hardware-" + uuid.uuid4().hex[:12]),
            help="new result directory (never overwritten)",
        )
        if name == "preflight":
            p.add_argument(
                "--rootfs",
                type=Path,
                help="the stage-1 root file system tar (default: the newest out/pico4-stage1-*-rootfs.tar)",
            )
        else:
            p.add_argument("--image", type=Path, required=True)
            p.add_argument("--fastboot", default="fastboot")
            p.add_argument("--fastboot-serial", help="explicit fastboot serial if different from ADB")
            p.add_argument("--ssh", default="ssh")
            p.add_argument("--ssh-keygen", default="ssh-keygen")
            p.add_argument(
                "--windows-ncm-python",
                help="Python with the windows-ncm extra (pyproject.toml); enable UsbDk NCM initialization",
            )
            p.add_argument("--host", default="172.16.42.1", help="headset USB IPv4 address")
            for flag, default in (("boot", 180), ("report", 180), ("recovery", 420)):
                p.add_argument(f"--{flag}-timeout", type=int, default=default)
            p.add_argument("--backup", type=Path, required=True, help="verified original device backup")
            p.add_argument("--confirm-device", required=True, help="repeat --serial to authorize boot writes")
    args = ap.parse_args(argv)
    if args.command == "preflight" and args.rootfs is None:
        args.rootfs = newest_rootfs()
    try:
        for tool in ("adb", "fastboot", "ssh", "ssh_keygen"):
            if hasattr(args, tool):
                require(shutil.which(str(getattr(args, tool))), f"Required executable missing: {getattr(args, tool)}")
        if args.command != "preflight":
            ipaddress.IPv4Address(args.host)
            if args.windows_ncm_python:
                require(sys.platform == "win32", "--windows-ncm-python requires Windows")
                found = shutil.which(str(args.windows_ncm_python))
                require(found, "Windows NCM Python executable is missing")
                # CreateProcess rejects relative paths written with forward slashes.
                args.windows_ncm_python = str(Path(str(found)).resolve())
            require(
                all(getattr(args, f"{n}_timeout") > 0 for n in ("boot", "report", "recovery")),
                "Timeouts must be positive",
            )
            require(args.confirm_device == args.serial, "--confirm-device must match --serial")
        elif args.serial is None:
            args.serial = android.select_device(args.adb, None)
        return Runner(args).execute()
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
