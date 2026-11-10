# SPDX-License-Identifier: Unlicense
"""Development loop on a headset in development mode.

The headset stays in development mode between tasks: the engineering ABL, the dev image as the
maintenance stage 1 in recovery, and the installed system on Valve's kernel in boot, super and
userdata. Each attempt goes into the maintenance stage 1, installs and runs the acceptance
runner's stage-2 checks on the installed system; nothing is restored when a task ends. The
device-side deadlines return the maintenance stage 1 to fastboot, where "dev reboot" picks it
up. "dev restore", then "python -m overpressure cycle --restore-only", return it to stock.
Settings come from .env (devices/phoenix/tooling/localconfig.py); the SSH key, images and check
results live in out/dev/.

  python -m overpressure dev stage-abl         engineering ABL through EDL, then PICO OS
  python -m overpressure dev image             the dev image and the install boot image in out/dev/
  python -m overpressure dev flash             write the dev image to recovery (fastboot) and start it
  python -m overpressure dev connect           Windows NCM initialization and stage-1 login
  python -m overpressure dev recovery          into the maintenance stage 1, until dev normal
  python -m overpressure dev install [SYSTEM]  boot, super and data from it; starts the installed system
  python -m overpressure dev normal            from the maintenance stage 1 into the installed system
  python -m overpressure dev installed         connect to the running installed system
  python -m overpressure dev check             the acceptance checks on the installed system
  python -m overpressure dev ssh COMMAND...    run a command on the headset
  python -m overpressure dev reboot            what misc and boot select, also from fastboot
  python -m overpressure dev restore           leave development mode: PICO OS from the backup
  python -m overpressure dev restore-partition PARTITION   one partition from the backup
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable

from devices.phoenix.kernel import recovery_image
from overpressure import (
    REPO,
    android,
    avb,
    bootimg,
    ota,
    progress,
    qualcomm,
    ssh,
    windows_ncm,
)
from overpressure import (
    backup as backups,
)
from overpressure.fastboot import NOT_IN_FASTBOOT
from overpressure.fastboot import flash as flash_image

from . import device, localconfig
from . import hardware_test as hw
from .cycle import Headset, run

if TYPE_CHECKING:
    from overpressure.process import Timing

ROOT = REPO
WORK = ROOT / "out" / "dev"
HOST = "172.16.42.1"
RUN_ID = "dev-loop"
CONTACT = "/run/pico4/host-contact"  # uptime of the last command (recovery-watch)
ARCHIVE = ROOT / "out" / "userspace" / "pico4-userspace.tar.zst"
#: Boot images the dev loop writes through fastboot: the dev image to recovery, stock boot back.
BOOT_PARTITIONS = ("boot", "recovery")
SYSTEM = ROOT / "out" / "userspace" / "pico4-system.img"
#: Written by "dev install": PICO OS's system and data are gone, "dev restore" brings them back.
INSTALLED = "installed.json"
#: The installed system's identity file (on the data partition, pico4-install keys), as test-run is
#: the dev image's.
INSTALL_ID = "/etc/pico4/install-id"
#: Partitions "dev restore-partition" writes back from the backup through fastboot.
RESTORABLE = ("boot", "recovery", "misc")
#: Command-line word naming the partition a dev image was written for (pico4.part=recovery).
PART_MARK = "pico4.part="
#: This boot's random ID: a reboot is over once another one answers.
BOOT_ID = "/proc/sys/kernel/random/boot_id"
#: What the installed system shows: PID 1's name, its install ID and the SoC serial.
INSTALLED_PROOF = ("/proc/1/comm", INSTALL_ID, "/sys/devices/soc0/serial_number")
#: The start of an image that tells whether a partition may hold it (write_partition).
PREFIX_BYTES = 1 << 20
#: The share of an install's progress its partition writes stand for; the short steps after them have the rest.
INSTALL_WRITES = 0.9
#: Seconds without an answer after a reboot before the Windows NCM initialization (the maintenance
#: stage 1 needs it; it disturbs a working link).
NCM_RETRY_SECONDS = 60
#: The battery as the maintenance stage 1's stock kernel shows it.
BATTERY = "/sys/class/power_supply/battery"
#: Below this charge an install or a restore starts only on a charging battery: a headset that
#: switches off halfway is recoverable (it starts the maintenance stage 1 again), the write is lost.
MIN_WRITE_PERCENT = 20


#: A release boots to the graphical target within this (docs/release-checklist.md; 10 to 11 s measured).
MAX_BOOT_SECONDS = 13.0
#: pico4-suspend-check's result on the headset; SSH over USB drops at the wake, so the check runs detached.
SUSPEND_RESULT = "/run/pico4-suspend-check.json"
#: The check takes about a minute (two tones, a 20 s suspend); the link comes back within seconds.
SUSPEND_DEADLINE_SECONDS = 240
#: The still gate runs on a session that has been up this long (docs/release-checklist.md): the
#: acceptance check restarts SteamVR, and a tracker just started still settles.
SETTLED_SECONDS = 120
#: Seconds since SteamVR became active, on the headset.
SESSION_AGE = (
    "echo $(( $(cut -d. -f1 /proc/uptime) - $(systemctl --user -M steamos@.host show steamvr "
    "-p ActiveEnterTimestampMonotonic --value) / 1000000 ))"
)
_SPAN = re.compile(r"(?:(\d+)min )?(\d+(?:\.\d+)?)(ms|s)\b")


def _span_seconds(text: str) -> float:
    """Return systemd-analyze's span ("1min 2.5s", "823ms", "9.026s") in seconds."""
    match = _SPAN.fullmatch(text.strip())
    if not match:
        msg = f"not a systemd time span: {text!r}"
        raise ValueError(msg)
    minutes, value, unit = match.groups()
    return int(minutes or 0) * 60 + float(value) / (1000 if unit == "ms" else 1)


def graphical_seconds(analyze: str) -> float:
    """Return the seconds from the kernel's start to the graphical target, from systemd-analyze time."""
    kernel = re.search(r"in (.+?) \(kernel\)", analyze)
    graphical = re.search(r"graphical\.target reached after (.+?) in userspace", analyze)
    if not kernel or not graphical:
        msg = "systemd-analyze names no kernel time or no graphical target (still starting?)"
        raise RuntimeError(msg)
    return _span_seconds(kernel.group(1)) + _span_seconds(graphical.group(1))


def still_gate_lost(output: str) -> bool:
    """Tell whether pico4-still-gate's line shows a lost pose (or names no poses at all)."""
    found = re.search(r"(\d+) poses, (\d+) lost", output)
    return found is None or int(found.group(2)) > 0


def battery_refusal(reading: str) -> str:
    """Return why partition writes may not start on @p reading (capacity, status), or "" if they may.

    A reading that is not a percentage refuses nothing: the caller warns instead.
    """
    words = reading.split()
    if not words or not words[0].isdigit():
        return ""
    percent, status = int(words[0]), (words[1] if len(words) > 1 else "")
    if percent >= MIN_WRITE_PERCENT or status in ("Charging", "Full"):
        return ""
    return (
        f"the battery is at {percent} % ({status or 'status unknown'}): charge the headset to {MIN_WRITE_PERCENT} % "
        "first, or connect it to a port that charges it"
    )


class Dev(Headset):
    """The development loop's steps on a headset in development mode."""

    def __init__(
        self,
        settings: localconfig.Settings,
        work: Path = WORK,
        runner: Callable[..., str] = run,
        clock: Timing = time,
        edl_count: Callable[[], int] | None = None,
    ) -> None:
        """Work on the headset @p settings name, with keys, images and results in @p work; tests pass fakes."""
        work.mkdir(parents=True, exist_ok=True)
        super().__init__(settings, work, runner, clock, *([edl_count] if edl_count else []))
        self.work = work

    def identity(self) -> dict:
        """Return the headset's saved identity, checked."""
        return device.read_identity(self.s.path("identity"))

    def ssh_argv(self, command: str) -> list:
        """Return the ssh command line that runs @p command on the headset.

        Every command renews the host contact: the dev image's maximum runtime counts from the
        last one (recovery-watch), so it ends abandoned sessions, not ones in use.
        """
        command = f"cut -d. -f1 /proc/uptime > {CONTACT} 2>/dev/null; {command}"
        return ssh.argv(self.s.ssh, self.work / "id_ed25519", self.work / "known_hosts", f"root@{HOST}", command)

    def ssh(
        self, command: str, timeout: float = 30, stdin: int | IO[bytes] | None = None, *, check: bool = True
    ) -> str:
        """Run @p command on the headset; return its output."""
        return self.run(self.ssh_argv(command), timeout, stdin=stdin, check=check)

    def fastboot(self, *words: str | Path, timeout: float = 60) -> str:
        """Run fastboot on the configured headset; return its output."""
        return self.run([self.s.tool("fastboot"), "-s", self.s.fastboot_serial, *words], timeout)

    def stock_location(self) -> str:
        """Return the URL of the headset's firmware in the OTA catalog (PICO4_FIRMWARE, as for stage 1)."""
        builds = ota.load_catalog(str(Path(__file__).with_name("firmware.json")))
        if self.s.firmware not in builds:
            msg = f"unknown firmware {self.s.firmware!r} (pico4/firmware.json)"
            raise ValueError(msg)
        return builds[self.s.firmware]["url"]

    def image(self) -> None:
        """Build the dev image for recovery (and boot) in out/dev."""
        source = ROOT / "out" / f"pico4-stage1-{self.s.firmware}.img"
        if not source.exists():
            msg = f"missing {source}: run python -m overpressure build --firmware {self.s.firmware}"
            raise ValueError(msg)
        key = self.work / "id_ed25519"
        if not key.exists():
            self.run([self.s.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", key])
        # Long deadlines: the dev image waits for the next attempt instead of rebooting.
        info = hw.prepare_image(
            source,
            self.work,
            RUN_ID,
            key.with_name("id_ed25519.pub").read_bytes(),
            connect_timeout=600,
            test_timeout=3600,
        )
        (self.work / "test-boot.img").replace(self.work / "dev-boot.img")
        # The same image for recovery, marked so stage 1 can tell which partition started it.
        # Booting recovery, the ABL loads the device-tree overlay from the image itself
        # (recovery_dtbo); without one it falls back to fastboot. It comes from the stock recovery
        # of the headset's firmware (its public OTA, as stage 1's kernel), the whole partition with
        # its AVB footer, so it stays private like the rest of out/dev.
        stock = ota.fetch_boot_image(self.stock_location(), str(ROOT / "cache"), print, "recovery")
        dtbo = bootimg.parse(stock).recovery_dtbo
        if not dtbo:
            msg = "the stock recovery image has no recovery_dtbo"
            raise RuntimeError(msg)
        boot = bootimg.parse((self.work / "dev-boot.img").read_bytes())
        recovery = boot.with_cmdline(f"{boot.full_cmdline} {PART_MARK}recovery")
        recovery.recovery_dtbo = dtbo
        recovery = recovery.pack()
        if bootimg.parse(recovery).recovery_dtbo != dtbo:
            msg = "the recovery image failed validation"
            raise RuntimeError(msg)
        # recovery is a chained AVB partition: the whole partition, with stock's vbmeta behind it.
        recovery = avb.with_stock_vbmeta(stock, recovery)
        (self.work / "dev-recovery.img").write_bytes(recovery)
        info["recovery_sha256"] = hashlib.sha256(recovery).hexdigest()
        (self.work / "dev-image.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        print(f"out/dev/dev-boot.img {info['boot_sha256']}")
        print(f"out/dev/dev-recovery.img {info['recovery_sha256']}")

    def enrolled_keys(self) -> bytes:
        """Return the installed system's authorized_keys.

        This PC's key, and the owner's own when .env names one. These are the only logins the
        system takes, on USB and on Wi-Fi (docs/security.md).
        """
        this_pc = (self.work / "id_ed25519.pub").read_bytes()
        own = self.s.path("ssh_public_key") if self.s.ssh_public_key else None
        keys = ssh.authorized_keys(this_pc, own, self.s.key("ssh_public_key"))
        print(f"SSH: keys only; enrolled {len(keys.splitlines())} key(s), this PC's is {self.work / 'id_ed25519'}")
        return keys

    def write_keys(self) -> None:
        """Write the enrolled keys and the install ID to the data partition (pico4-install keys).

        The installed system reads them there: /root and /etc's changes live on the data
        partition. They are never in the system image or a boot image (docs/security.md).
        """
        size = qualcomm.partition_size(self.s.path("backup"), "userdata")
        keys = self.enrolled_keys()
        with tempfile.TemporaryFile() as stream:
            stream.write(keys)
            stream.seek(0)
            output = self.ssh(f"pico4-install keys --size {size} --id {RUN_ID}", 60, stdin=stream)
        if output.splitlines()[-1:] != [f"keys {len(keys.splitlines())}"]:
            msg = "the headset did not confirm the keys"
            raise RuntimeError(msg)

    def to_fastboot(self) -> None:
        """From PICO OS (ADB), stage 1 or stage 2 (SSH) into the bootloader."""
        fastboot = self.s.tool("fastboot")
        if self.s.fastboot_serial in self.run([fastboot, "devices"], 30, check=False):
            return
        if f"{self.s.adb_serial}\tdevice" in self.run([self.s.tool("adb"), "devices"], 30, check=False):
            self.run([self.s.tool("adb"), "-s", self.s.adb_serial, "reboot", "bootloader"], 30)
        else:
            self.ssh(
                "systemctl reboot --reboot-argument=bootloader 2>/dev/null || pico4-reboot bootloader", 15, check=False
            )

        def ready() -> None:
            if self.s.fastboot_serial not in self.run([fastboot, "devices"], 30, check=False):
                raise RuntimeError(NOT_IN_FASTBOOT)

        self.until("fastboot", 180, ready)

    def check_boot_partition(self, image: Path, name: str = "boot") -> None:
        """Run the acceptance runner's checks before it writes a boot image.

        An unlocked bootloader and exactly one non-slotted partition of that name, of the
        backed-up size.
        """
        if name not in BOOT_PARTITIONS:
            msg = f"{name} is not a boot image partition"
            raise ValueError(msg)
        try:
            state = self.fastboot("oem", "device-info")
        except RuntimeError:
            state = self.fastboot("getvar", "unlocked")
        if not android.unlocked(state):
            msg = "the bootloader is locked or its state is unknown"
            raise RuntimeError(msg)
        size = android.single_partition_size(self.fastboot("getvar", "all"), name)
        if size is None:
            msg = f"only a single, non-slotted {name} partition is supported"
            raise RuntimeError(msg)
        _, expected = self.stock_partition(name)
        if size != expected["size"] or image.stat().st_size > expected["size"]:
            msg = f"the {name} partition or the image does not fit the backup's {name} size"
            raise RuntimeError(msg)

    def flash_boot(self, image: Path, name: str = "boot") -> None:
        """Write a boot image to boot or recovery, then start that partition's image."""
        self.to_fastboot()
        self.check_boot_partition(image, name)
        # The engineering ABL's per-session authorization, as the acceptance runner sends it.
        self.fastboot("oem", device.unlock_token(self.identity()["chip_id"]), "unlock")
        # A whole-partition image (recovery's) goes sparse, as the ABL crashed on a large raw one.
        flash_image(self.fastboot, name, image)
        self.fastboot("reboot", *([] if name == "boot" else [name]))

    def restore_partition(self, name: str, *, reboot: bool = True) -> None:
        """Write a partition back from the backup through fastboot.

        The image must match the backup manifest and the partition its backed-up size.
        """
        if name not in RESTORABLE:
            msg = f"{name} is not restored through fastboot"
            raise ValueError(msg)
        stock, expected = self.stock_partition(name)
        if backups.digest(stock) != expected:
            msg = f"{stock} differs from the backup manifest"
            raise RuntimeError(msg)
        self.to_fastboot()
        size = android.single_partition_size(self.fastboot("getvar", "all"), name)
        if size is None or size != expected["size"]:
            msg = f"the {name} partition does not match the backup"
            raise RuntimeError(msg)
        self.fastboot("oem", device.unlock_token(self.identity()["chip_id"]), "unlock")
        # The ABL crashed receiving the 100 MiB recovery raw; sparse, it gets the data blocks.
        flash_image(self.fastboot, name, stock, above=0, sparse_path=self.work / f"restore-{name}.simg")
        if reboot:
            self.fastboot("reboot")
        print(f"{name} restored from the backup")

    def flash(self) -> None:
        """Write the dev image to recovery, where it starts as the maintenance stage 1."""
        image = self.work / "dev-recovery.img"
        if not image.exists():
            msg = "run python -m overpressure dev image first"
            raise ValueError(msg)
        self.s.require("adb_serial", "fastboot_serial")
        self.flash_boot(image, "recovery")
        print("dev image flashed to recovery; the maintenance stage 1 is starting")

    def authenticate(self) -> None:
        """Refuse an SSH endpoint that is not this headset's dev image; mark the stage 1 authenticated."""
        lines = self.ssh("; ".join(f"cat {path}" for path in hw.STAGE1_PROOF), 10).splitlines()
        if (
            len(lines) != len(hw.STAGE1_PROOF)
            or lines[0] != RUN_ID
            or qualcomm.chip_id(lines[1]) != self.identity()["chip_id"]
        ):
            msg = "the SSH endpoint is not this headset's dev image"
            raise RuntimeError(msg)
        self.ssh("touch /run/pico4/test-authenticated", 10)

    def connect(self) -> str:
        """Log in to the running stage 1; return the partition it started from, checked by readback."""
        # Stage 1 makes new host keys on every boot.
        (self.work / "known_hosts").unlink(missing_ok=True)
        ncm = self.s.ncm()
        if ncm:
            self.until("NCM gadget", 120, lambda: self.run(windows_ncm.command(ncm, self.s.fastboot_serial), 30))
        self.until("stage-1 SSH", 120, self.authenticate)
        # The partition the running stage 1 came from, by its command line.
        name = "recovery" if f"{PART_MARK}recovery" in self.ssh("cat /proc/cmdline", 10).split() else "boot"
        # The flash is verified by reading that partition back from the running stage 1.
        image = self.work / f"dev-{name}.img"
        if (self.work / "dev-image.json").exists() and image.exists():
            expected = json.loads((self.work / "dev-image.json").read_text(encoding="utf-8")).get(f"{name}_sha256")
            output = self.ssh(f"pico4-install hash {name} {image.stat().st_size}", 60).split()
            if output[:1] != [expected]:
                msg = f"{name} does not hold out/dev/dev-{name}.img (readback differs)"
                raise RuntimeError(msg)
        print(f"stage 1 authenticated, started from {name}, which holds the dev image")
        return name

    def check_battery(self) -> None:
        """Refuse partition writes on an almost empty battery that does not charge (battery_refusal)."""
        reading = self.ssh(f"cat {BATTERY}/capacity {BATTERY}/status 2>/dev/null", 10, check=False)
        refusal = battery_refusal(reading)
        if refusal:
            raise RuntimeError(refusal)
        if not reading.split()[:1] or not reading.split()[0].isdigit():
            print("warning: the battery's charge is unknown; keep the headset connected", flush=True)

    @staticmethod
    def stream_size(image: Path) -> int:
        """Return the bytes write_partition sends for @p image: its compressed copy's, when the build made one."""
        compressed = image.with_name(image.name + ".zst")
        return (compressed if compressed.is_file() else image).stat().st_size

    def write_partition(self, name: str, image: Path, timeout: int, meter: progress.Meter | None = None) -> None:
        """Stream an image to boot or super through the maintenance stage 1 (pico4-install).

        The backup's partition size bounds it; pico4-install checks the stream and reads it back.
        @p meter counts the bytes sent (an image the partition holds already counts at once);
        without one, the image's own meter prints the progress lines.
        """
        size = qualcomm.partition_size(self.s.path("backup"), name)
        digest = backups.digest(image)
        # An update rewrites only what changed: the partition may hold the image already. Its first
        # MiB tells a different image at once (the file system's superblock is in it); only a
        # match is worth hashing the whole image on the headset.
        with image.open("rb") as head:
            prefix = head.read(PREFIX_BYTES)
        quick = self.ssh(f"pico4-install hash {name} {len(prefix)}", 30).split()
        if quick[:1] == [hashlib.sha256(prefix).hexdigest()]:
            held = self.ssh(f"pico4-install hash {name} {digest['size']}", timeout).split()
            if held[:1] == [digest["sha256"]]:
                print(f"{name} holds {image.name} already", flush=True)
                if meter is not None:
                    meter.add(self.stream_size(image))
                return
        # The system image goes compressed when its build made a copy of exactly this image.
        compressed = image.with_name(image.name + ".zst")
        record = image.with_name(image.name + ".zst.sha256")
        zstd = compressed.is_file() and record.is_file() and record.read_text().split()[:1] == [digest["sha256"]]
        source = compressed if zstd else image
        print(f"writing {source.name} ({source.stat().st_size >> 20} MiB) to {name}", flush=True)
        meter = meter or progress.Meter(
            source.stat().st_size, lambda done, total: f"writing {name}: {progress.mib(done, total)}"
        )
        with source.open("rb") as stream, progress.feed(stream, meter) as pipe:
            output = self.ssh(
                f"pico4-install write {name} --size {size} --bytes {digest['size']} --sha256 {digest['sha256']}"
                + (" --zstd" if zstd else ""),
                timeout,
                stdin=pipe,
            )
        if output.splitlines()[-1:] != [f"written {name} {digest['sha256']}"]:
            msg = f"the headset did not confirm {name}"
            raise RuntimeError(msg)

    def install(self, system: Path = SYSTEM, *, format_data: bool = False, boot: Path | None = None) -> None:
        """Install from the maintenance stage 1, then start the installed system.

        The boot image with Valve's kernel goes to boot, the system image to super, and userdata is
        formatted if it holds no data yet (or when asked); the enrolled keys and the install ID go
        onto it. @p boot replaces the boot image.
        """
        boot = boot or self.work.parent / "kernel/valve-boot.img"
        for path, hint in (
            (boot, "python -m overpressure dev boot-image"),
            (system, "scripts/build-system-image.sh"),
        ):
            if not path.exists():
                msg = f"missing {path}: run {hint}"
                raise ValueError(msg)
        if self.connect() != "recovery":
            msg = "install runs from the maintenance stage 1 in recovery (python -m overpressure dev recovery)"
            raise RuntimeError(msg)
        device.check_panel(self.ssh("cat /proc/cmdline", 10))
        self.check_battery()
        # From here on PICO OS's system is gone until "dev restore" writes it back.
        (self.work / INSTALLED).write_text(
            json.dumps(
                {
                    "system": str(system),
                    "since": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # The writes are most of the install: up to WRITES of its progress, the short steps after them the rest.
        sizes = {"boot": self.stream_size(boot), "super": self.stream_size(system)}
        current = {"name": "boot"}
        meter = progress.Meter(
            sum(sizes.values()),
            lambda done, total: f"writing {current['name']}: {progress.mib(done, total)}",
            end=INSTALL_WRITES,
        )
        for name, image, timeout in (("boot", boot, 120), ("super", system, 1800)):
            current["name"] = name
            self.write_partition(name, image, timeout, meter)
        status = self.ssh("pico4-install status", 30)
        if format_data or "data: pico4-data" not in status.splitlines():
            size = qualcomm.partition_size(self.s.path("backup"), "userdata")
            progress.say("formatting userdata", INSTALL_WRITES)
            if self.ssh(f"pico4-install format-data --size {size}", 900).splitlines()[-1:] != ["formatted userdata"]:
                msg = "formatting userdata failed"
                raise RuntimeError(msg)
        progress.say("adding this computer's key", INSTALL_WRITES + 0.03)
        self.write_keys()
        progress.say("starting the installed system", INSTALL_WRITES + 0.05)
        self.normal()

    def normal(self) -> None:
        """From the maintenance stage 1 into the installed system: clear the recovery request."""
        if self.ssh("pico4-install bootmode normal", 30).splitlines()[-1:] != ["bootmode normal"]:
            msg = "could not clear the recovery request in misc"
            raise RuntimeError(msg)
        (self.work / "state.json").unlink(missing_ok=True)
        self.ssh("pico4-reboot", 15, check=False)
        # Only the installed system's systemd passes wait_installed: no fixed wait for the reboot.
        self.clock.sleep(3)
        self.wait_installed()

    def installed(self) -> None:
        """Connect to the running installed system (after a reboot, or a power-on away from the PC)."""
        self.wait_installed()

    def recovery(self) -> None:
        """Start the maintenance stage 1, which then stays until "dev normal".

        From fastboot with the ABL's reboot-recovery (it keeps the request in misc), from Linux
        through it.
        """
        (self.work / "state.json").unlink(missing_ok=True)
        self.to_fastboot()
        self.fastboot("reboot", "recovery")
        # From fastboot nothing else answers: connect retries until stage 1 does.
        if self.connect() != "recovery":
            msg = "the maintenance stage 1 did not start from recovery"
            raise RuntimeError(msg)

    def wait_installed(self, previous_boot: str = "") -> None:
        """Wait for the installed system's systemd, reached over the USB link.

        Valve's kernel takes Windows' NCM driver as it is: no NCM initialization, which would
        disturb the working link (only the maintenance stage 1's stock kernel needs it). With
        @p previous_boot, the boot of that ID does not count: it is the one going down.
        """

        def installed() -> None:
            # The maintenance stage 1 makes new host keys on every boot; the installed system keeps
            # its own. Neither may be pinned against the other.
            (self.work / "known_hosts").unlink(missing_ok=True)
            lines = self.ssh(f"cat {' '.join(INSTALLED_PROOF)}", 10).splitlines()
            if (
                len(lines) != len(INSTALLED_PROOF)
                or lines[1] != RUN_ID
                or qualcomm.chip_id(lines[2]) != self.identity()["chip_id"]
            ):
                msg = "the SSH endpoint is not this headset's installed system"
                raise RuntimeError(msg)
            if lines[0] != "systemd":
                msg = "systemd is not running yet"
                raise RuntimeError(msg)
            if previous_boot and self.boot_id() == previous_boot:
                msg = "the boot before the reboot still answers"
                raise RuntimeError(msg)

        self.until("installed system", 240, installed)
        # dev check's state: the archive the system image was built from, if it is a local one
        # (out/userspace, or another build's output folder).
        archive = self.ssh("cat /usr/share/pico4/userspace-archive.sha256", 10).strip()
        local = [ARCHIVE, *sorted(p for p in (ROOT / "out").glob("*/pico4-userspace.tar.zst") if p != ARCHIVE)]
        found = next((p for p in local if p.exists() and backups.digest(p)["sha256"] == archive), None)
        if found is not None:
            state = hw.userspace_state(found)
            state["installed"] = True
            state["booted"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            (self.work / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print(f"installed system running (from archive {archive[:12]})")

    def boot_build_info(self) -> dict:
        """Return the build information of the running boot image: the record whose kernel runs.

        The installed system's boot image, or a one-shot test image (out/kernel/*.img.json, which
        recovery_image.py writes). Without a matching record, the running kernel's release alone.
        """
        release = self.ssh("uname -r", 10).strip()
        for record in sorted((self.work.parent / "kernel").glob("*.img.json")):
            info = json.loads(record.read_text(encoding="utf-8")).get("build_info") or {}
            if info.get("boot") == "direct" and info.get("kernel", "").startswith(f"Linux version {release} "):
                return info
        return {"kernel": f"Linux version {release} "}

    def boot_image(self) -> Path:
        """Build the installed system's boot image from what the steps before made (docs/install.md).

        Valve's kernel, its device tree and the command line that starts the system image in super:
        no stage 1, no keys and no firmware (the system image holds them).
        """
        kernel, out = self.work.parent / "kernel", self.work.parent / "kernel" / "valve-boot.img"
        needed = {
            kernel / "Image.gz": "the Valve's kernel step",
            self.work / "dev-recovery.img": "the dev image step",
        }
        for path, hint in needed.items():
            if not path.exists():
                msg = f"missing {path}: run {hint} first"
                raise ValueError(msg)
        stock = ROOT / "cache" / "stock" / (self.stock_location().rsplit("/", 1)[-1] + ".recovery.img")
        if not stock.exists():
            msg = f"no stock recovery at {stock}: run the dev image step first"
            raise ValueError(msg)
        code = recovery_image.main(
            ["--direct", "--boot", str(kernel), str(stock), str(self.work / "dev-recovery.img"), str(out)]
        )
        if code:
            msg = "the boot image failed"
            raise RuntimeError(msg)
        return out

    def release_check(self, *, attended: bool = False) -> int:
        """Run the automated checks of docs/release-checklist.md on the installed system with a session up.

        Each runs whatever the others found, and the result file is the release's record.
        """
        folder = self.work.parent / "release" / dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        checks = {"listeners": "pico4-listeners", "still gate": "pico4-still-gate 60"}
        results: dict = {"acceptance": "passed" if self.check() == 0 else "failed"}
        # The boot this runs on: the checklist starts from a fresh one.
        try:
            seconds = graphical_seconds(self.ssh("systemd-analyze time", 30))
            results["boot time"] = {
                "status": "passed" if seconds <= MAX_BOOT_SECONDS else "failed",
                "output": f"graphical target {seconds:.1f} s after the kernel's start (at most {MAX_BOOT_SECONDS:g} s)",
            }
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            results["boot time"] = {"status": "failed", "output": str(exc)[-2000:]}
        self.wait_settled()
        for name, command in checks.items():
            try:
                results[name] = {"status": "passed", "output": self.ssh(command, 600)}
            except (RuntimeError, subprocess.TimeoutExpired) as exc:  # noqa: PERF203  # each check fails on its own
                results[name] = {"status": "failed", "output": str(exc)[-2000:]}
        if attended:
            try:
                results["suspend with sound"] = self.suspend_check()
            except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
                results["suspend with sound"] = {"status": "failed", "output": str(exc)[-2000:]}
        # pico4-still-gate only measures; the checklist wants the headset tracked throughout.
        gate = results["still gate"]
        if gate["status"] == "passed" and still_gate_lost(gate["output"]):
            gate["status"] = "failed"
            gate["output"] += "\nthe tracker lost the headset (the checklist wants it tracked throughout)"
        results["version"] = self.ssh("cat /usr/share/pico4/version", 10, check=False)
        (folder / "result.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        failed = [k for k, v in results.items() if v == "failed" or (isinstance(v, dict) and v["status"] == "failed")]
        print(f"{folder / 'result.json'}: " + (f"FAILED {', '.join(failed)}" if failed else "automated checks passed"))
        print("The worn checks stay yours: docs/release-checklist.md")
        return 1 if failed else 0

    def wait_settled(self) -> None:
        """Wait until the session has been up SETTLED_SECONDS (at once when its age can't be read)."""
        try:
            age = int(self.ssh(SESSION_AGE, 30).strip())
        except (RuntimeError, subprocess.TimeoutExpired, ValueError):
            return
        if 0 <= age < SETTLED_SECONDS:
            print(f">> the session is {age} s old: waiting {SETTLED_SECONDS - age} s for it to settle")
            self.clock.sleep(SETTLED_SECONDS - age)

    def suspend_check(self) -> dict:
        """Run pico4-suspend-check detached and wait for its result file (docs/hardware-testing.md).

        SSH over USB drops at the wake, which would end a check run in the SSH session itself.
        """
        unit = f"pico4-suspend-check-{int(self.clock.monotonic())}"
        self.ssh(f"rm -f {SUSPEND_RESULT}; systemd-run --unit={unit} --collect --no-block pico4-suspend-check", 30)
        end = self.clock.monotonic() + SUSPEND_DEADLINE_SECONDS
        while self.clock.monotonic() < end:
            self.clock.sleep(5)
            try:
                out = self.ssh(f"cat {SUSPEND_RESULT} 2>/dev/null || true", 15).strip()
            except (RuntimeError, subprocess.TimeoutExpired):
                continue  # the link is down while it sleeps
            if out:
                result = json.loads(out)
                return {"status": "passed" if result.get("status") == "passed" else "failed", "output": out}
        msg = f"no {SUSPEND_RESULT} within {SUSPEND_DEADLINE_SECONDS} s: look at the headset"
        raise RuntimeError(msg)

    def support_bundle(self, output: Path | None = None) -> Path:
        """Run pico4-support-bundle on the headset and fetch its archive.

        Its member list is printed, so the owner sees what would be sent.
        """
        lines = self.ssh("pico4-support-bundle", 300).splitlines()
        if not lines or not lines[0].startswith("/"):
            msg = "pico4-support-bundle gave no archive"
            raise RuntimeError(msg)
        remote = lines[0]
        print("\n".join(lines[1:]), flush=True)
        output = output or self.work.parent / "support" / Path(remote).name
        output.parent.mkdir(parents=True, exist_ok=True)
        # Binary: not through the text-decoding runner.
        with output.open("wb") as target:
            subprocess.run(  # noqa: S603  # ssh with the dev loop's own key and host, no shell
                [str(a) for a in self.ssh_argv(f"cat {remote}; rm -f {remote}")], stdout=target, check=True, timeout=120
            )
        return output

    def check(self) -> int:
        """Run the acceptance runner's stage-2 checks on the installed system; 0 when they pass."""
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        folder = self.work / ("check-" + dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S"))
        checks = DevChecks(self, folder, self.boot_build_info(), self.identity())
        try:
            checks.step("stage-2 checks", lambda: checks.userspace_checks(dict(state)))
            checks.result["status"] = "passed"
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001  # whatever stops the checks is their result
            checks.result.update(status="failed", error=str(exc) or "Interrupted")
        checks.save()
        print(f"{checks.result['status'].upper()}: {folder / 'result.json'}")
        return 0 if checks.result["status"] == "passed" else 1

    def reboot(self) -> None:
        """Reboot the headset, also from fastboot, where a device deadline leaves it; no flash needed.

        It starts the maintenance stage 1 while misc holds its request (dev recovery), else what
        boot holds, the installed system.
        """
        (self.work / "state.json").unlink(missing_ok=True)
        fastboot = self.s.tool("fastboot")
        previous = ""
        if self.s.fastboot_serial and self.s.fastboot_serial in self.run([fastboot, "devices"], 30, check=False):
            self.fastboot("reboot")
        else:
            try:
                previous = self.boot_id()
            except RuntimeError:
                previous = ""
            self.ssh("systemctl reboot 2>/dev/null || pico4-reboot", 15, check=False)
        # The boot that answers next must be another one; no fixed wait for the reboot.
        self.clock.sleep(3)
        if (self.work / INSTALLED).exists() and self.started(previous) == "installed":
            self.wait_installed(previous)
            return
        self.connect()

    def boot_id(self) -> str:
        """Return the running boot's random ID."""
        lines = [line for line in self.ssh(f"cat {BOOT_ID}", 10).splitlines() if line.strip()]
        if not lines:
            msg = "no boot ID"
            raise RuntimeError(msg)
        return lines[-1].strip()

    def started(self, previous_boot: str = "") -> str:
        """Return what started after a reboot of an installed headset.

        "maintenance" (stage 1 from recovery) or "installed" (the installed system's systemd), by
        PID 1 and the kernel command line. The installed system answers without the Windows NCM
        initialization, which would disturb its link; the maintenance stage 1's stock kernel needs
        it, so it is sent once when nothing answered for a while.
        """
        ncm = self.s.ncm()
        started = self.clock.monotonic()
        initialized = [False]

        def which() -> str:
            # The maintenance stage 1 makes new host keys on every boot; the installed system keeps
            # its own.
            (self.work / "known_hosts").unlink(missing_ok=True)
            try:
                comm, _, cmdline = self.ssh("cat /proc/1/comm /proc/cmdline", 10).partition("\n")
            except RuntimeError:
                if ncm and not initialized[0] and self.clock.monotonic() - started > NCM_RETRY_SECONDS:
                    initialized[0] = True
                    self.run(windows_ncm.command(ncm, self.s.fastboot_serial), 30)
                raise
            if previous_boot and self.boot_id() == previous_boot:
                msg = "the boot before the reboot still answers"
                raise RuntimeError(msg)
            if comm.strip() == "systemd":
                return "installed"
            if f"{PART_MARK}recovery" in cmdline.split():
                return "maintenance"
            msg = "neither the installed system nor the maintenance stage 1 is running"
            raise RuntimeError(msg)

        return self.until("stage 1 or the installed system", 240, which)

    def restore(self, *, installed: bool = False) -> None:
        """Leave the loop: the backup's stock boot image, then PICO OS. ABL stays engineering.

        With the system installed (dev install, as this computer's record says or @p installed tells
        for a headset another computer installed), PICO OS's system, recovery and data come back too.
        """
        self.s.require("adb_serial", "fastboot_serial")
        if installed or (self.work / INSTALLED).exists():
            self.restore_installed()
            return
        stock, expected = self.stock_boot()
        if backups.digest(stock) != expected:
            msg = f"{stock} differs from the backup manifest"
            raise RuntimeError(msg)
        self.flash_boot(stock)
        (self.work / "state.json").unlink(missing_ok=True)
        print(f"PICO OS: {self.wait_pico_os()}")

    def restore_installed(self) -> None:
        """Bring back PICO OS as the backup has it, from the maintenance stage 1.

        Super and boot are written back (checked against the manifest, read back), userdata and
        metadata wiped (PICO OS's own data was not in the backup; it formats both and starts its
        first-time setup), misc cleared; then stock recovery goes back through fastboot, and PICO
        OS starts.
        """
        images = {}
        for name in ("super", "boot", "recovery"):
            path, expected = self.stock_partition(name)
            if backups.digest(path) != expected:
                msg = f"{path} differs from the backup manifest"
                raise RuntimeError(msg)
            images[name] = path
        # A fresh maintenance boot from wherever the headset is: the installed system, the
        # maintenance stage 1 or fastboot (connect alone waits for a stage 1 that may not run).
        self.recovery()
        self.check_battery()
        self.write_partition("super", images["super"], 3600)
        self.write_partition("boot", images["boot"], 300)
        backup = self.s.path("backup")
        for name in ("userdata", "metadata"):
            size = qualcomm.partition_size(backup, name)
            if self.ssh(f"pico4-install wipe {name} --size {size}", 300).splitlines()[-1:] != [f"wiped {name}"]:
                msg = f"wiping {name} failed"
                raise RuntimeError(msg)
        if self.ssh("pico4-install bootmode normal", 30).splitlines()[-1:] != ["bootmode normal"]:
            msg = "could not clear the recovery request in misc"
            raise RuntimeError(msg)
        self.restore_partition("recovery")
        (self.work / INSTALLED).unlink(missing_ok=True)
        (self.work / "state.json").unlink(missing_ok=True)
        print(
            "PICO OS is starting with fresh data: its first-time setup follows on the headset "
            "(ADB stays off until developer mode is enabled again)"
        )


class DevChecks(hw.Runner):
    """The acceptance runner's stage-2 checks, through the dev loop's SSH key."""

    def __init__(self, dev: Dev, folder: Path, build_info: dict, identity: dict) -> None:
        """Check the installed system of @p dev, with evidence in @p folder."""
        super().__init__(argparse.Namespace(command="dev-check", out=folder, serial=dev.s.adb_serial, host=HOST))
        self.dev = dev
        self.run_id = RUN_ID
        self.baseline = {"chip_id": identity["chip_id"]}
        self.result["build_info"] = build_info

    def ssh(self, command: str, timeout: float = 30) -> str:
        """Run @p command on the headset through the dev loop's key; return its output."""
        return self.command(self.dev.ssh_argv(command), timeout)


def main(argv: list[str] | None = None) -> int:  # noqa: C901  # one branch per command word
    """Run the development loop's command line."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure dev", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)
    for name in (
        "stage-abl",
        "image",
        "flash",
        "connect",
        "check",
        "reboot",
        "normal",
        "recovery",
        "installed",
    ):
        sub.add_parser(name)
    sub.add_parser("boot-image", help="out/kernel/valve-boot.img from out/kernel, out/dev and the assets")
    release = sub.add_parser("release-check", help="the automated part of docs/release-checklist.md")
    release.add_argument("--attended", action="store_true", help="also what needs someone near: tones, a suspend")
    bundle = sub.add_parser("support-bundle", help="logs for a bug report, identifiers masked, into out/support")
    bundle.add_argument("--output", type=Path)
    inst = sub.add_parser("install")
    inst.add_argument("system", nargs="?", type=Path, default=SYSTEM)
    inst.add_argument("--format-data", action="store_true", help="format userdata even if it holds data")
    inst.add_argument("--boot", type=Path, help="the boot image to install instead of out/kernel/valve-boot.img")
    restore = sub.add_parser("restore", help="back to PICO OS from the backup: system, boot, recovery, fresh data")
    restore.add_argument(
        "--installed", action="store_true", help="the system is installed, also where this computer did not record it"
    )
    restore_part = sub.add_parser("restore-partition")
    restore_part.add_argument("partition", choices=RESTORABLE)
    restore_part.add_argument("--no-reboot", action="store_true")
    shell = sub.add_parser("ssh")
    shell.add_argument("words", nargs="+")
    args = ap.parse_args(argv)
    dev = Dev(localconfig.load())
    try:
        if args.command == "stage-abl":
            folder = WORK / ("stage-abl-" + dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S"))
            folder.mkdir(parents=True)
            Headset(dev.s, folder).stage_abl()
        elif args.command == "check":
            return dev.check()
        elif args.command == "install":
            dev.install(args.system, format_data=args.format_data, boot=args.boot)
        elif args.command == "boot-image":
            print(dev.boot_image())
        elif args.command == "release-check":
            return dev.release_check(attended=args.attended)
        elif args.command == "support-bundle":
            print(dev.support_bundle(args.output))
        elif args.command == "restore":
            dev.restore(installed=args.installed)
        elif args.command == "restore-partition":
            dev.restore_partition(args.partition, reboot=not args.no_reboot)
        elif args.command == "ssh":
            print(dev.ssh(" ".join(args.words), 600, check=False))
        else:
            getattr(dev, args.command)()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
