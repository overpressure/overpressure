"""Development loop on a headset whose boot partition holds the dev image.

The headset stays in development mode between tasks: the engineering ABL, and the dev image
in boot, so every boot starts stage 1. Each attempt reboots into stage 1, streams a userspace
archive, switches to it and runs the acceptance runner's checks; nothing is restored when a
task ends. The device-side deadlines still return the headset to fastboot, where "dev reboot"
picks it up. "dev restore", then "python -m overpressure cycle --restore-only", return it to stock.
Settings come from .env (devices/phoenix/tooling/localconfig.py); the SSH key, images and check results live in
out/dev/.

  python -m overpressure dev stage-abl         engineering ABL through EDL, then PICO OS
  python -m overpressure dev image             out/dev/dev-boot.img from the stage-1 image
  python -m overpressure dev flash             write it to boot (fastboot) and start stage 1
  python -m overpressure dev connect           Windows NCM initialization and stage-1 login
  python -m overpressure dev boot [ARCHIVE]    stream the userspace, switch, wait for stage 2
  python -m overpressure dev check             the acceptance checks on the running stage 2
  python -m overpressure dev ssh COMMAND...    run a command on the headset
  python -m overpressure dev reboot            restart into stage 1, also from fastboot
  python -m overpressure dev restore           leave development mode: stock boot, then PICO OS
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from overpressure import android, avb, backup as backups, bootimg, compress, cpio, ota, qualcomm, sparse, ssh, windows_ncm

from . import device, hardware_test as hw, localconfig
from .cycle import Headset, run

ROOT = localconfig.ROOT
WORK = ROOT / "out" / "dev"
HOST = "172.16.42.1"
RUN_ID = "dev-loop"
CONTACT = "/run/pico4-poc/host-contact"  # uptime of the last command (recovery-watch)
ARCHIVE = ROOT / "out" / "userspace" / "pico4-userspace.tar.zst"
#: Boot images the dev loop may write: stage 1 in boot, or in recovery for maintenance.
BOOT_PARTITIONS = ("boot", "recovery")
SYSTEM = ROOT / "out" / "userspace" / "pico4-system.img"
#: Written by "dev install": PICO OS's system and data are gone, "dev restore" brings them back.
INSTALLED = "installed.json"
#: The installed system's identity file (install boot image), as test-run is the dev image's.
INSTALL_ID = "/etc/pico4-poc/install-id"
#: Images above this size go to fastboot as sparse images.
SPARSE_ABOVE = 64 << 20
#: Partitions "dev restore-partition" writes back from the backup through fastboot.
RESTORABLE = ("boot", "recovery", "misc")
#: Command-line word naming the partition a dev image was written for (pico4poc.part=recovery).
PART_MARK = "pico4poc.part="


class Dev(Headset):
    def __init__(self, settings, work: Path = WORK, runner=run, clock=time, edl_count=None):
        work.mkdir(parents=True, exist_ok=True)
        super().__init__(settings, work, runner, clock, *([edl_count] if edl_count else []))
        self.work = work

    def identity(self) -> dict:
        return device.read_identity(self.s.path("identity"))

    def ssh_argv(self, command: str) -> list:
        """Every command renews the host contact: the dev image's maximum runtime counts from
        the last one (recovery-watch), so it ends abandoned sessions, not ones in use."""
        command = f"cut -d. -f1 /proc/uptime > {CONTACT} 2>/dev/null; {command}"
        return ssh.argv(self.s.ssh, self.work / "id_ed25519", self.work / "known_hosts", f"root@{HOST}", command)

    def ssh(self, command: str, timeout=30, stdin=None, check=True) -> str:
        return self.run(self.ssh_argv(command), timeout, stdin=stdin, check=check)

    def fastboot(self, *words, timeout=60) -> str:
        return self.run([self.s.tool("fastboot"), "-s", self.s.fastboot_serial, *words], timeout)

    def stock_location(self) -> str:
        """The headset's firmware in the OTA catalog (PICO4_FIRMWARE in .env, as for stage 1)."""
        builds = ota.load_catalog(str(Path(__file__).with_name("firmware.json")))
        if self.s.firmware not in builds:
            raise ValueError(f"unknown firmware {self.s.firmware!r} (pico4/firmware.json)")
        return builds[self.s.firmware]["url"]

    def image(self):
        source = ROOT / "out" / f"pico4-poc-{self.s.firmware}.img"
        if not source.exists():
            raise ValueError(f"missing {source}: run python -m overpressure build --firmware {self.s.firmware}")
        key = self.work / "id_ed25519"
        if not key.exists():
            self.run([self.s.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", key])
        # Long deadlines: the dev image waits for the next attempt instead of rebooting.
        info = hw.prepare_image(source, self.work, RUN_ID, key.with_name("id_ed25519.pub").read_bytes(),
                                connect_timeout=600, test_timeout=3600, userspace=True)
        (self.work / "test-boot.img").replace(self.work / "dev-boot.img")
        # The same image for recovery, marked so stage 1 can tell which partition started it.
        # Booting recovery, the ABL loads the device-tree overlay from the image itself
        # (recovery_dtbo); without one it falls back to fastboot. It comes from the stock recovery
        # of the headset's firmware (its public OTA, as stage 1's kernel), the whole partition with
        # its AVB footer, so it stays private like the rest of out/dev.
        stock = ota.fetch_boot_image(self.stock_location(), str(ROOT / "cache"), print, "recovery")
        dtbo = bootimg.parse(stock).recovery_dtbo
        if not dtbo:
            raise RuntimeError("the stock recovery image has no recovery_dtbo")
        boot = bootimg.parse((self.work / "dev-boot.img").read_bytes())
        recovery = boot.with_cmdline(f"{boot.full_cmdline} {PART_MARK}recovery")
        recovery.recovery_dtbo = dtbo
        recovery = recovery.pack()
        if bootimg.parse(recovery).recovery_dtbo != dtbo:
            raise RuntimeError("the recovery image failed validation")
        # recovery is a chained AVB partition: the whole partition, with stock's vbmeta behind it.
        recovery = avb.with_stock_vbmeta(stock, recovery)
        (self.work / "dev-recovery.img").write_bytes(recovery)
        info["recovery_sha256"] = hashlib.sha256(recovery).hexdigest()
        install = self.install_image(source, key.with_name("id_ed25519.pub").read_bytes())
        (self.work / "install-boot.img").write_bytes(install)
        info["install_sha256"] = hashlib.sha256(install).hexdigest()
        (self.work / "dev-image.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        print(f"out/dev/dev-boot.img {info['boot_sha256']}")
        print(f"out/dev/dev-recovery.img {info['recovery_sha256']}")
        print(f"out/dev/install-boot.img {info['install_sha256']}")

    @staticmethod
    def install_image(source: Path, public_key: bytes) -> bytes:
        """The installed system's boot image: stage 1 in installed mode, without deadlines (it
        mounts the system and switches on its own), with the dev loop's SSH key and identity."""
        image = bootimg.parse(source.read_bytes())
        tree = cpio.Tree(nodes=dict(cpio.read_cpio(compress.decompress(image.ramdisk)[0])))
        if "usr/bin/pico4-install" not in tree.nodes or b"pico4-userspace installed" not in tree.nodes["init"].data:
            raise ValueError("rebuild the stage-1 image: the installed mode is missing")
        tree.mkdir("root/.ssh", 0o700)
        tree.add_file("root/.ssh/authorized_keys", public_key.rstrip() + b"\n", 0o600)
        tree.add_file(INSTALL_ID.lstrip("/"), RUN_ID.encode() + b"\n")
        words = [w for w in image.full_cmdline.split() if not w.startswith(("pico4poc.root=", "pico4poc.autoreboot="))]
        image = image.with_cmdline(" ".join(words + ["pico4poc.root=installed", "pico4poc.autoreboot=0"]))
        image.ramdisk = gzip.compress(tree.to_cpio(), mtime=0)
        image.boot_signature = b""
        data = image.pack()
        if bootimg.parse(data).kernel != image.kernel:
            raise RuntimeError("the install boot image failed validation")
        return data

    def to_fastboot(self):
        """From PICO OS (ADB), stage 1 or stage 2 (SSH) into the bootloader."""
        fastboot = self.s.tool("fastboot")
        if self.s.fastboot_serial in self.run([fastboot, "devices"], 30, check=False):
            return
        if f"{self.s.adb_serial}\tdevice" in self.run([self.s.tool("adb"), "devices"], 30, check=False):
            self.run([self.s.tool("adb"), "-s", self.s.adb_serial, "reboot", "bootloader"], 30)
        else:
            self.ssh("systemctl reboot --reboot-argument=bootloader 2>/dev/null || pico4-reboot bootloader",
                     15, check=False)

        def ready():
            if self.s.fastboot_serial not in self.run([fastboot, "devices"], 30, check=False):
                raise RuntimeError("not in fastboot yet")
        self.until("fastboot", 180, ready)

    def check_boot_partition(self, image: Path, name: str = "boot"):
        """The acceptance runner's checks before it writes a boot image: an unlocked bootloader
        and exactly one non-slotted partition of that name, of the backed-up size."""
        if name not in BOOT_PARTITIONS:
            raise ValueError(f"{name} is not a boot image partition")
        try:
            state = self.fastboot("oem", "device-info")
        except RuntimeError:
            state = self.fastboot("getvar", "unlocked")
        if not android.unlocked(state):
            raise RuntimeError("the bootloader is locked or its state is unknown")
        inventory = self.fastboot("getvar", "all")
        names = re.findall(rf"^[ \t]*(?:\(bootloader\)[ \t]*)?partition-size:({name}(?:_[ab])?):", inventory, re.M)
        if names != [name]:
            raise RuntimeError(f"only a single, non-slotted {name} partition is supported")
        _, expected = self.stock_partition(name)
        if int(hw.fastboot_value(inventory, f"partition-size:{name}"), 16) != expected["size"] \
                or image.stat().st_size > expected["size"]:
            raise RuntimeError(f"the {name} partition or the image does not fit the backup's {name} size")

    def flash_boot(self, image: Path, name: str = "boot"):
        """Write a boot image to boot or recovery, then start that partition's image."""
        self.to_fastboot()
        self.check_boot_partition(image, name)
        # The engineering ABL's per-session authorization, as the acceptance runner sends it.
        self.fastboot("oem", device.unlock_token(self.identity()["chip_id"]), "unlock")
        if image.stat().st_size > SPARSE_ABOVE:
            # A whole-partition image (recovery's): sparse, as the ABL crashed on a large raw one.
            simg = image.with_suffix(".simg")
            simg.write_bytes(sparse.encode(image.read_bytes()))
            self.fastboot("flash", name, simg, timeout=600)
            simg.unlink()
        else:
            self.fastboot("flash", name, image, timeout=180)
        self.fastboot("reboot", *([] if name == "boot" else [name]))

    def restore_partition(self, name: str, reboot: bool = True):
        """Write a partition back from the backup through fastboot: the image must match the
        backup manifest and the partition its backed-up size."""
        if name not in RESTORABLE:
            raise ValueError(f"{name} is not restored through fastboot")
        stock, expected = self.stock_partition(name)
        if backups.digest(stock) != expected:
            raise RuntimeError(f"{stock} differs from the backup manifest")
        self.to_fastboot()
        inventory = self.fastboot("getvar", "all")
        names = re.findall(rf"^[ \t]*(?:\(bootloader\)[ \t]*)?partition-size:({name}(?:_[ab])?):", inventory, re.M)
        if names != [name] or int(hw.fastboot_value(inventory, f"partition-size:{name}"), 16) != expected["size"]:
            raise RuntimeError(f"the {name} partition does not match the backup")
        self.fastboot("oem", device.unlock_token(self.identity()["chip_id"]), "unlock")
        # The ABL crashed receiving the 100 MiB recovery raw; sparse, it gets the data blocks.
        image = self.work / f"restore-{name}.simg"
        image.write_bytes(sparse.encode(stock.read_bytes()))
        self.fastboot("flash", name, image, timeout=600)
        image.unlink()
        if reboot:
            self.fastboot("reboot")
        print(f"{name} restored from the backup")

    def flash(self, name: str = "boot"):
        image = self.work / f"dev-{name}.img"
        if not image.exists():
            raise ValueError("run python -m overpressure dev image first")
        self.s.require("adb_serial", "fastboot_serial")
        self.flash_boot(image, name)
        print(f"dev image flashed to {name}; stage 1 is starting")

    def authenticate(self):
        lines = self.ssh("cat /etc/pico4-poc/test-run; cat /sys/devices/soc0/serial_number", 10).splitlines()
        if len(lines) != 2 or lines[0] != RUN_ID or qualcomm.chip_id(lines[1]) != self.identity()["chip_id"]:
            raise RuntimeError("the SSH endpoint is not this headset's dev image")
        self.ssh("touch /run/pico4-poc/test-authenticated", 10)

    def connect(self):
        # Stage 1 makes new host keys on every boot.
        (self.work / "known_hosts").unlink(missing_ok=True)
        if self.s.ncm():
            self.until("NCM gadget", 120, lambda: self.run(
                windows_ncm.command(self.s.ncm(), self.s.fastboot_serial), 30))
        self.until("stage-1 SSH", 120, self.authenticate)
        # The partition the running stage 1 came from, by its command line.
        name = "recovery" if f"{PART_MARK}recovery" in self.ssh("cat /proc/cmdline", 10).split() else "boot"
        # The flash is verified by reading that partition back from the running stage 1.
        image = self.work / f"dev-{name}.img"
        if (self.work / "dev-image.json").exists() and image.exists():
            expected = json.loads((self.work / "dev-image.json").read_text(encoding="utf-8")).get(f"{name}_sha256")
            output = self.ssh(f"head -c {image.stat().st_size} /dev/block/by-name/{name} | sha256sum", 60).split()
            if output[:1] != [expected]:
                raise RuntimeError(f"{name} does not hold out/dev/dev-{name}.img (readback differs)")
        print(f"stage 1 authenticated, started from {name}, which holds the dev image")
        return name

    def write_partition(self, name: str, image: Path, timeout: int):
        """Stream an image to boot or super through the maintenance stage 1 (pico4-install),
        against the backup's partition size; it checks the stream and reads it back."""
        size = qualcomm.partition_size(self.s.path("backup"), name)
        digest = backups.digest(image)
        # An update rewrites only what changed: the partition may hold the image already.
        held = self.ssh(f"head -c {digest['size']} /dev/block/by-name/{name} | sha256sum", timeout).split()
        if held[:1] == [digest["sha256"]]:
            print(f"{name} holds {image.name} already", flush=True)
            return
        print(f"writing {image.name} ({digest['size'] >> 20} MiB) to {name}", flush=True)
        with image.open("rb") as stream:
            output = self.ssh(f"pico4-install write {name} --size {size} --bytes {digest['size']} "
                              f"--sha256 {digest['sha256']}", timeout, stdin=stream)
        if output.splitlines()[-1:] != [f"written {name} {digest['sha256']}"]:
            raise RuntimeError(f"the headset did not confirm {name}")

    def install(self, system: Path = SYSTEM, format_data: bool = False, boot: Path | None = None):
        """Install from the maintenance stage 1: the install boot image in boot, the system
        image in super, userdata formatted if it holds no data yet (or when asked), then the
        installed system starts. @p boot replaces the install boot image, such as one with
        another kernel (kernel/recovery_image.py --installed out/dev/install-boot.img --boot)."""
        boot = boot or self.work / "install-boot.img"
        for path, hint in ((boot, "python -m overpressure dev image"), (system, "scripts/build-system-image.sh")):
            if not path.exists():
                raise ValueError(f"missing {path}: run {hint}")
        if self.connect() != "recovery":
            raise RuntimeError("install runs from the maintenance stage 1 in recovery (python -m overpressure dev recovery)")
        # From here on PICO OS's system is gone until "dev restore" writes it back.
        (self.work / INSTALLED).write_text(json.dumps({"system": str(system), "since": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds")}) + "\n", encoding="utf-8")
        self.write_partition("boot", boot, 120)
        self.write_partition("super", system, 1800)
        status = self.ssh("pico4-install status", 30)
        if format_data or "data: pico4-data" not in status.splitlines():
            size = qualcomm.partition_size(self.s.path("backup"), "userdata")
            print("formatting userdata", flush=True)
            if self.ssh(f"pico4-install format-data --size {size}", 900).splitlines()[-1:] != ["formatted userdata"]:
                raise RuntimeError("formatting userdata failed")
        self.normal()

    def normal(self):
        """From the maintenance stage 1 into the installed system: clear the recovery request."""
        if self.ssh("pico4-install bootmode normal", 30).splitlines()[-1:] != ["bootmode normal"]:
            raise RuntimeError("could not clear the recovery request in misc")
        (self.work / "state.json").unlink(missing_ok=True)
        self.ssh("pico4-reboot", 15, check=False)
        self.clock.sleep(20)
        self.wait_installed()

    def installed(self):
        """Connect to the running installed system (after a reboot, or a power-on away from the PC)."""
        self.wait_installed()

    def recovery(self):
        """Into the maintenance stage 1, which then stays until "dev normal": from fastboot
        with the ABL's reboot-recovery (it keeps the request in misc), from Linux through it."""
        (self.work / "state.json").unlink(missing_ok=True)
        self.to_fastboot()
        self.fastboot("reboot", "recovery")
        self.clock.sleep(20)
        if self.connect() != "recovery":
            raise RuntimeError("the maintenance stage 1 did not start from recovery")

    def wait_installed(self):
        """The installed system's systemd, reached over the USB link (it renews stage 1's)."""
        def ncm():
            if self.s.ncm():
                self.run(windows_ncm.command(self.s.ncm(), self.s.fastboot_serial), 30)
        self.until("NCM gadget", 180, ncm)
        initialized = [self.clock.monotonic()]

        def installed():
            # Stage 1 of the installed boot answers for a few seconds with its own host key; the
            # installed system keeps another one. Neither may be pinned against the other.
            (self.work / "known_hosts").unlink(missing_ok=True)
            try:
                lines = self.ssh(f"cat /proc/1/comm {INSTALL_ID} /sys/devices/soc0/serial_number", 10).splitlines()
            except RuntimeError:
                # A link initialized before a reboot finished is gone; initializing a working one
                # again disturbs it, so at most once a minute.
                if self.clock.monotonic() - initialized[0] > 60:
                    ncm()
                    initialized[0] = self.clock.monotonic()
                raise
            if len(lines) != 3 or lines[1] != RUN_ID or qualcomm.chip_id(lines[2]) != self.identity()["chip_id"]:
                raise RuntimeError("the SSH endpoint is not this headset's installed system")
            if lines[0] != "systemd":
                raise RuntimeError("stage 1 is still running")
        self.until("installed system", 240, installed)
        # dev check's state: the archive the system image was built from, if it is a local one
        # (out/userspace, or another build's output folder such as a kernel branch's).
        archive = self.ssh("cat /usr/share/pico4-poc/userspace-archive.sha256", 10).strip()
        local = [ARCHIVE] + sorted(p for p in (ROOT / "out").glob("*/pico4-userspace.tar.zst") if p != ARCHIVE)
        found = next((p for p in local if p.exists() and backups.digest(p)["sha256"] == archive), None)
        if found is not None:
            state = hw.userspace_state(found)
            state["installed"] = True
            state["booted"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
            (self.work / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print(f"installed system running (from archive {archive[:12]})")

    def boot(self, archive: Path, pushes=()) -> dict:
        state = hw.userspace_state(archive)
        print(f"sending {state['bytes'] >> 20} MiB", flush=True)
        started = self.clock.monotonic()
        with archive.open("rb") as stream:
            output = self.ssh(f"pico4-userspace receive --sha256 {state['sha256']}", 900, stdin=stream)
        if output.splitlines()[-1:] != [f"received {state['sha256']}"]:
            raise RuntimeError("the headset did not confirm the archive")
        seconds = max(self.clock.monotonic() - started, 0.1)
        state["receive_seconds"] = round(seconds, 1)
        print(f"received in {seconds:.0f} s ({state['bytes'] / seconds / 1e6:.1f} MB/s)", flush=True)
        for local, remote in pushes:
            with open(local, "rb") as stream:
                self.ssh(f"cat > /newroot{remote}", 60, stdin=stream)
            print(f"pushed {local} -> {remote}")
        # A stage 2 that never answers returns to fastboot 180 s after the switch (this
        # timeout plus 120 s, recovery-watch), not 720 s as the dev image's 600 s would give.
        self.ssh("echo 60 > /etc/pico4-poc/test-connect-timeout")
        self.ssh("pico4-userspace switch", 30)
        switched = self.clock.monotonic()
        self.clock.sleep(5)

        def stage2():
            if self.ssh("cat /proc/1/comm", 10) != "systemd":
                raise RuntimeError("stage 1 is still running")
            self.ssh("touch /run/pico4-poc/userspace-authenticated", 10)
        self.until("stage 2", 170, stage2)
        state["ssh_seconds"] = round(self.clock.monotonic() - switched, 1)
        state["booted"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        (self.work / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print(f"stage 2 answered {state['ssh_seconds']:.0f} s after the switch")
        return state

    def known_build_info(self, dev_image: dict) -> dict:
        """The build information of the running stage 1: the dev image's, or a kernel test image's
        (recovery_image.py records it in out/kernel/*.img.json) when that one runs."""
        try:
            running = json.loads(self.ssh("cat /etc/pico4-poc/build-info", 10))
        except (RuntimeError, ValueError):
            return dev_image
        if running != dev_image:
            for record in sorted((self.work.parent / "kernel").glob("*.img.json")):
                info = json.loads(record.read_text(encoding="utf-8")).get("build_info")
                if info == running:
                    print(f"stage 1 of the kernel test image {record.name[:-5]}")
                    return info
        return dev_image

    def check(self) -> int:
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        image = json.loads((self.work / "dev-image.json").read_text(encoding="utf-8"))
        folder = self.work / ("check-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        checks = DevChecks(self, folder, self.known_build_info(image["build_info"]), self.identity())
        try:
            checks.step("stage-2 checks", lambda: checks.userspace_checks(dict(state)))
            checks.result["status"] = "passed"
        except (Exception, KeyboardInterrupt) as exc:
            checks.result.update(status="failed", error=str(exc) or "Interrupted")
        checks.save()
        print(f"{checks.result['status'].upper()}: {folder / 'result.json'}")
        return 0 if checks.result["status"] == "passed" else 1

    def reboot(self):
        """Into stage 1 for the next attempt. A device deadline leaves the headset in fastboot;
        boot still holds the dev image there, so a plain reboot is enough, without a flash. With
        the system installed, boot holds the installed system: the maintenance stage 1 starts
        only while misc holds its request (dev recovery), else the installed system comes back."""
        (self.work / "state.json").unlink(missing_ok=True)
        fastboot = self.s.tool("fastboot")
        if self.s.fastboot_serial and self.s.fastboot_serial in self.run([fastboot, "devices"], 30, check=False):
            self.fastboot("reboot")
        else:
            self.ssh("systemctl reboot 2>/dev/null || pico4-reboot", 15, check=False)
        self.clock.sleep(20)
        if (self.work / INSTALLED).exists() and self.started() == "installed":
            self.wait_installed()
            return
        self.connect()

    def started(self) -> str:
        """After a reboot of an installed headset: "maintenance" (stage 1 from recovery) or
        "installed" (the installed system's systemd), by PID 1 and the kernel command line."""
        if self.s.ncm():
            self.until("NCM gadget", 120, lambda: self.run(
                windows_ncm.command(self.s.ncm(), self.s.fastboot_serial), 30))

        def which():
            # Each stage 1 makes new host keys, and the installed system keeps its own.
            (self.work / "known_hosts").unlink(missing_ok=True)
            comm, _, cmdline = self.ssh("cat /proc/1/comm /proc/cmdline", 10).partition("\n")
            if comm.strip() == "systemd":
                return "installed"
            if f"{PART_MARK}recovery" in cmdline.split():
                return "maintenance"
            raise RuntimeError("the installed boot's stage 1 is still running")
        return self.until("stage 1 or the installed system", 240, which)

    def restore(self):
        """Leave the loop: the backup's stock boot image, then PICO OS. ABL stays engineering.
        With the system installed (dev install), PICO OS's system, recovery and data come back too."""
        self.s.require("adb_serial", "fastboot_serial")
        if (self.work / INSTALLED).exists():
            return self.restore_installed()
        stock, expected = self.stock_boot()
        if backups.digest(stock) != expected:
            raise RuntimeError(f"{stock} differs from the backup manifest")
        self.flash_boot(stock)
        (self.work / "state.json").unlink(missing_ok=True)
        print(f"PICO OS: {self.wait_pico_os()}")

    def restore_installed(self):
        """PICO OS as the backup has it, from the maintenance stage 1: super and boot written back
        (checked against the manifest, read back), userdata and metadata wiped (PICO OS's own
        data was not in the backup; it formats both and starts its first-time setup), misc
        cleared; then stock recovery through fastboot, and PICO OS starts."""
        images = {}
        for name in ("super", "boot", "recovery"):
            path, expected = self.stock_partition(name)
            if backups.digest(path) != expected:
                raise RuntimeError(f"{path} differs from the backup manifest")
            images[name] = path
        if self.connect() != "recovery":
            self.recovery()
        self.write_partition("super", images["super"], 3600)
        self.write_partition("boot", images["boot"], 300)
        backup = self.s.path("backup")
        for name in ("userdata", "metadata"):
            size = qualcomm.partition_size(backup, name)
            if self.ssh(f"pico4-install wipe {name} --size {size}", 300).splitlines()[-1:] != [f"wiped {name}"]:
                raise RuntimeError(f"wiping {name} failed")
        if self.ssh("pico4-install bootmode normal", 30).splitlines()[-1:] != ["bootmode normal"]:
            raise RuntimeError("could not clear the recovery request in misc")
        self.restore_partition("recovery")
        (self.work / INSTALLED).unlink()
        (self.work / "state.json").unlink(missing_ok=True)
        print("PICO OS is starting with fresh data: its first-time setup follows on the headset "
              "(ADB stays off until developer mode is enabled again)")


class DevChecks(hw.Runner):
    """The acceptance runner's stage-2 checks, through the dev loop's SSH key."""

    def __init__(self, dev: Dev, folder: Path, build_info: dict, identity: dict):
        super().__init__(argparse.Namespace(command="dev-check", out=folder, serial=dev.s.adb_serial,
                                            host=HOST, userspace=None))
        self.dev = dev
        self.run_id = RUN_ID
        self.baseline = {"chip_id": identity["chip_id"]}
        self.result["build_info"] = build_info

    def ssh(self, command, timeout=30, stdin=None):
        return self.command(self.dev.ssh_argv(command), timeout, **({"stdin": stdin} if stdin is not None else {}))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m overpressure dev", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("stage-abl", "image", "connect", "check", "reboot", "restore", "normal", "recovery", "installed"):
        sub.add_parser(name)
    inst = sub.add_parser("install")
    inst.add_argument("system", nargs="?", type=Path, default=SYSTEM)
    inst.add_argument("--format-data", action="store_true", help="format userdata even if it holds data")
    inst.add_argument("--boot", type=Path, help="the boot image to install instead of out/dev/install-boot.img")
    restore_part = sub.add_parser("restore-partition")
    restore_part.add_argument("partition", choices=RESTORABLE)
    restore_part.add_argument("--no-reboot", action="store_true")
    flash = sub.add_parser("flash")
    flash.add_argument("partition", nargs="?", choices=BOOT_PARTITIONS, default="boot",
                       help="boot (default) or recovery, then start it")
    boot = sub.add_parser("boot")
    boot.add_argument("archive", nargs="?", type=Path, default=ARCHIVE)
    boot.add_argument("--push", action="append", default=[], metavar="LOCAL=REMOTE",
                      help="replace a file of the received root before the switch (repeatable)")
    shell = sub.add_parser("ssh")
    shell.add_argument("words", nargs="+")
    args = ap.parse_args(argv)
    dev = Dev(localconfig.load())
    try:
        if args.command == "stage-abl":
            folder = WORK / ("stage-abl-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            folder.mkdir(parents=True)
            Headset(dev.s, folder).stage_abl()
        elif args.command == "boot":
            dev.boot(args.archive, [item.split("=", 1) for item in args.push])
        elif args.command == "check":
            return dev.check()
        elif args.command == "flash":
            dev.flash(args.partition)
        elif args.command == "install":
            dev.install(args.system, args.format_data, args.boot)
        elif args.command == "restore-partition":
            dev.restore_partition(args.partition, not args.no_reboot)
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
