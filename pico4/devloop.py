"""Development loop on a headset whose boot partition holds the dev image.

The acceptance cycle (python -m pico4 cycle) restores stock boot and ABL after every run.
In between, the dev image stays flashed: each attempt reboots into stage 1, streams a
userspace archive, switches to it and runs the acceptance runner's checks. The device-side
deadlines still return the headset to fastboot. Settings come from .env
(pico4/localconfig.py); the SSH key, images and check results live in out/dev/.

  python -m pico4 dev stage-abl         engineering ABL through EDL, then PICO OS
  python -m pico4 dev image             out/dev/dev-boot.img from the stage-1 image
  python -m pico4 dev flash             write it to boot (fastboot) and start stage 1
  python -m pico4 dev connect           Windows NCM initialization and stage-1 login
  python -m pico4 dev boot [ARCHIVE]    stream the userspace, switch, wait for stage 2
  python -m pico4 dev check             the acceptance checks on the running stage 2
  python -m pico4 dev ssh COMMAND...    run a command on the headset
  python -m pico4 dev reboot            restart into stage 1 for the next attempt
  python -m pico4 dev restore           stock boot image back, then PICO OS (ABL stays)
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import device, hardware_test as hw, localconfig
from .cycle import Headset, run

ROOT = localconfig.ROOT
WORK = ROOT / "out" / "dev"
HOST = "172.16.42.1"
RUN_ID = "dev-loop"
ARCHIVE = ROOT / "out" / "userspace" / "pico4-userspace.tar.zst"


class Dev(Headset):
    def __init__(self, settings, work: Path = WORK, runner=run, clock=time, edl_count=None):
        work.mkdir(parents=True, exist_ok=True)
        super().__init__(settings, work, runner, clock, *([edl_count] if edl_count else []))
        self.work = work

    def identity(self) -> dict:
        return device.read_identity(self.s.path("identity"))

    def ssh_argv(self, command: str) -> list:
        known = (self.work / "known_hosts").as_posix()
        return [self.s.ssh, "-i", self.work / "id_ed25519", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f'UserKnownHostsFile="{known}"', "-o", f'GlobalKnownHostsFile="{known}"',
                f"root@{HOST}", command]

    def ssh(self, command: str, timeout=30, stdin=None, check=True) -> str:
        return self.run(self.ssh_argv(command), timeout, stdin=stdin, check=check)

    def fastboot(self, *words, timeout=60) -> str:
        return self.run([self.s.tool("fastboot"), "-s", self.s.fastboot_serial, *words], timeout)

    def image(self):
        source = ROOT / "out" / f"pico4-poc-{self.s.firmware}.img"
        if not source.exists():
            raise ValueError(f"missing {source}: run python -m pico4 build --firmware {self.s.firmware}")
        key = self.work / "id_ed25519"
        if not key.exists():
            self.run([self.s.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", key])
        # Long deadlines: the dev image waits for the next attempt instead of rebooting.
        info = hw.prepare_image(source, self.work, RUN_ID, key.with_name("id_ed25519.pub").read_bytes(),
                                connect_timeout=600, test_timeout=3600, userspace=True)
        (self.work / "test-boot.img").replace(self.work / "dev-boot.img")
        (self.work / "dev-image.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        print(f"out/dev/dev-boot.img {info['boot_sha256']}")

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

    def check_boot_partition(self, image: Path):
        """The acceptance runner's checks before it writes boot: an unlocked bootloader and
        exactly one non-slotted boot partition of the backed-up size."""
        try:
            state = self.fastboot("oem", "device-info")
        except RuntimeError:
            state = self.fastboot("getvar", "unlocked")
        if not device.unlocked(state):
            raise RuntimeError("the bootloader is locked or its state is unknown")
        inventory = self.fastboot("getvar", "all")
        names = re.findall(r"^[ \t]*(?:\(bootloader\)[ \t]*)?partition-size:(boot(?:_[ab])?):", inventory, re.M)
        if names != ["boot"]:
            raise RuntimeError("only a single, non-slotted boot partition is supported")
        _, expected = self.stock_boot()
        if int(hw.fastboot_value(inventory, "partition-size:boot"), 16) != expected["size"] \
                or image.stat().st_size > expected["size"]:
            raise RuntimeError("the boot partition or the image does not fit the backup's boot size")

    def flash_boot(self, image: Path):
        self.to_fastboot()
        self.check_boot_partition(image)
        # The engineering ABL's per-session authorization, as the acceptance runner sends it.
        self.fastboot("oem", device.unlock_token(self.identity()["chip_id"]), "unlock")
        self.fastboot("flash", "boot", image, timeout=180)
        self.fastboot("reboot")

    def flash(self):
        image = self.work / "dev-boot.img"
        if not image.exists():
            raise ValueError("run python -m pico4 dev image first")
        self.s.require("adb_serial", "fastboot_serial")
        self.flash_boot(image)
        print("dev image flashed; stage 1 is starting")

    def authenticate(self):
        lines = self.ssh("cat /etc/pico4-poc/test-run; cat /sys/devices/soc0/serial_number", 10).splitlines()
        if len(lines) != 2 or lines[0] != RUN_ID or device.chip_id(lines[1]) != self.identity()["chip_id"]:
            raise RuntimeError("the SSH endpoint is not this headset's dev image")
        self.ssh("touch /run/pico4-poc/test-authenticated", 10)

    def connect(self):
        # Stage 1 makes new host keys on every boot.
        (self.work / "known_hosts").unlink(missing_ok=True)
        if self.s.ncm_python:
            self.until("NCM gadget", 120, lambda: self.run(
                [self.s.path("ncm_python"), ROOT / "pico4/windows_ncm.py", "--serial", self.s.fastboot_serial], 30))
        self.until("stage-1 SSH", 120, self.authenticate)
        # The flash is verified by reading boot back from the running stage 1.
        image = self.work / "dev-boot.img"
        if (self.work / "dev-image.json").exists() and image.exists():
            expected = json.loads((self.work / "dev-image.json").read_text(encoding="utf-8"))["boot_sha256"]
            output = self.ssh(f"head -c {image.stat().st_size} /dev/block/by-name/boot | sha256sum", 60).split()
            if output[:1] != [expected]:
                raise RuntimeError("boot does not hold out/dev/dev-boot.img (readback differs)")
        print("stage 1 authenticated, boot holds the dev image")

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

    def check(self) -> int:
        state = json.loads((self.work / "state.json").read_text(encoding="utf-8"))
        image = json.loads((self.work / "dev-image.json").read_text(encoding="utf-8"))
        folder = self.work / ("check-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        checks = DevChecks(self, folder, image["build_info"], self.identity())
        try:
            checks.step("stage-2 checks", lambda: checks.userspace_checks(dict(state)))
            checks.result["status"] = "passed"
        except (Exception, KeyboardInterrupt) as exc:
            checks.result.update(status="failed", error=str(exc) or "Interrupted")
        checks.save()
        print(f"{checks.result['status'].upper()}: {folder / 'result.json'}")
        return 0 if checks.result["status"] == "passed" else 1

    def reboot(self):
        (self.work / "state.json").unlink(missing_ok=True)
        self.ssh("systemctl reboot 2>/dev/null || pico4-reboot", 15, check=False)
        self.clock.sleep(20)
        self.connect()

    def restore(self):
        """Leave the loop: the backup's stock boot image, then PICO OS. ABL stays engineering."""
        self.s.require("adb_serial", "fastboot_serial")
        stock, expected = self.stock_boot()
        if device.digest(stock) != expected:
            raise RuntimeError(f"{stock} differs from the backup manifest")
        self.flash_boot(stock)
        (self.work / "state.json").unlink(missing_ok=True)
        print(f"PICO OS: {self.wait_pico_os()}")


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
    ap = argparse.ArgumentParser(prog="python -m pico4 dev", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("stage-abl", "image", "flash", "connect", "check", "reboot", "restore"):
        sub.add_parser(name)
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
