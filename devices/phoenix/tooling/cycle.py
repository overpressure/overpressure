"""Unattended acceptance cycle that ends on stock boot and stock ABL (docs/hardware-testing.md).

  python -m overpressure cycle [--userspace ARCHIVE] [--skip-stage] [--keep-abl | --restore-only]

1. Engineering ABL: ADB to EDL, write and read back (python -m overpressure device stage), EDL
   reset, PICO OS. --skip-stage when it is already staged (after the dev loop).
2. python -m overpressure hwtest flash-run with the stage-1 image for PICO4_FIRMWARE and the
   userspace archive. The runner restores stock boot itself; if it could not, the device
   deadline returns the headset to fastboot and the runner's recorded commands flash the
   pinned stock image.
3. ADB to EDL: read boot back and compare it with the backup, then restore stock ABL
   (python -m overpressure device restore-abl, read back). --keep-abl stops before this.
4. EDL reset; PICO OS must finish booting.

--restore-only does steps 3 and 4 (after "python -m overpressure dev restore"). The cycle stops
at the first failure and says where the headset is. Evidence goes to out/cycle-<time>/.
Settings come from .env (devices/phoenix/tooling/localconfig.py).
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

from overpressure import backup as backups
from overpressure.process import run, until
from overpressure.qualcomm import EDL_USB, edl_devices

from . import localconfig

ROOT = localconfig.ROOT
EDL_USB = ("05c6", "9008")  # Qualcomm HS-USB QDLoader 9008


class Headset:
    """Waits and ADB/EDL steps shared by the cycle and the dev loop."""

    def __init__(self, settings, folder: Path, runner=run, clock=time, edl_count=edl_devices):
        self.s = settings
        self.folder = folder
        self.run = runner
        self.clock = clock
        self.edl_count = edl_count

    def until(self, name, seconds, operation):
        return until(name, seconds, operation, self.clock)

    def wait_pico_os(self, seconds=300) -> str:
        def ready():
            lines = self.run([self.s.tool("adb"), "-s", self.s.adb_serial, "shell",
                              "getprop sys.boot_completed; getprop ro.build.fingerprint"], 15).splitlines()
            if not lines or lines[0].strip() != "1":
                raise RuntimeError("PICO OS has not finished booting")
            return lines[1].strip() if len(lines) > 1 else ""
        return self.until("PICO OS", seconds, ready)

    def enter_edl(self):
        if self.edl_count() == 1:
            return
        self.run([self.s.tool("adb"), "-s", self.s.adb_serial, "reboot", "edl"], 30)

        def one():
            if self.edl_count() != 1:
                raise RuntimeError("expected exactly one EDL device")
        self.until("EDL", 30, one)

    def edl(self, *words, log: str, timeout=600) -> str:
        return self.run([self.s.path("edl"), "--loader", self.s.path("loader"), "--memory", "Ufs", *words],
                        timeout, log=self.folder / f"{log}.log")

    def reset_to_pico_os(self, log: str) -> str:
        """EDL reset (edl-ng's plain reset mode), then PICO OS. Once the headset stayed off after the
        final reset, with nothing on USB, and only a hard reset by the user brought PICO OS back: the
        cycle then stops and says so instead of retrying anything."""
        self.edl("reset", "--mode", "Reset", log=log)
        try:
            return self.wait_pico_os()
        except RuntimeError as exc:
            where = "still in EDL" if self.edl_count() else "no ADB or EDL device"
            raise RuntimeError(f"PICO OS did not come up after the EDL reset ({where}): hard reset needed; "
                               "hold the power button until the headset restarts, nothing is retried") from exc

    def check_edl_serial(self, output: str):
        """edl-ng reports the device serial when it connects: only the configured headset may be written."""
        if not re.search(rf"Serial Number:\s*{re.escape(self.s.edl_serial)}\b", output, re.I):
            raise RuntimeError("the EDL device is not the configured headset (headset in EDL)")

    def device_args(self) -> list:
        return ["--identity", self.s.path("identity"), "--backup", self.s.path("backup"),
                "--loader", self.s.path("loader"), "--edl-backend", "edl-ng", "--edl", self.s.path("edl"),
                "--partition", "abl"]

    def stage_abl(self):
        """Engineering ABL through EDL (hash and readback checked), then PICO OS."""
        self.s.require("adb_serial", "edl_serial", "engineering_abl_sha256")
        print(">> engineering ABL", flush=True)
        self.enter_edl()
        # A read-only first session identifies the device before anything is written.
        self.check_edl_serial(self.edl("printgpt", log="edl-identify", timeout=120))
        self.run([sys.executable, "-m", "overpressure", "--device", "phoenix", "device", "stage", *self.device_args(),
                  "--image", self.s.path("engineering_abl"), "--sha256", self.s.engineering_abl_sha256,
                  "--readback", self.folder / "engineering-abl-readback.bin", "--execute",
                  "--confirm-device", self.s.adb_serial], 900, log=self.folder / "stage-abl.log")
        print(f"   PICO OS with engineering ABL: {self.reset_to_pico_os('reset-stage')}", flush=True)

    def stock_partition(self, name: str) -> tuple:
        """A partition's image in the backup and its manifest entry."""
        backup = self.s.path("backup")
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        names = [n for n in manifest["files"] if Path(n).stem == name and Path(n).suffix in (".bin", ".img")]
        if len(names) != 1:
            raise ValueError(f"the backup must hold exactly one {name} partition image")
        return backup / names[0], manifest["files"][names[0]]

    def stock_boot(self) -> tuple:
        """The stock boot image of the backup and its manifest entry."""
        return self.stock_partition("boot")


class Cycle(Headset):
    def flash_run(self, archive: Path | None) -> int:
        print(">> hardware acceptance (flash-run)", flush=True)
        image = ROOT / "out" / f"pico4-poc-{self.s.firmware}.img"
        if not image.exists():
            raise ValueError(f"missing {image}: run python -m overpressure build --firmware {self.s.firmware}")
        argv = [sys.executable, "-m", "overpressure", "--device", "phoenix", "hwtest", "flash-run", "--serial", self.s.adb_serial,
                "--confirm-device", self.s.adb_serial, "--fastboot-serial", self.s.fastboot_serial,
                "--backup", self.s.path("backup"), "--image", image, "--adb", self.s.tool("adb"),
                "--fastboot", self.s.tool("fastboot"), "--ssh", self.s.ssh, "--ssh-keygen", self.s.ssh_keygen,
                "--out", self.folder / "run"]
        if archive:
            argv += ["--userspace", archive.resolve()]
        if self.s.ncm_python:
            argv += ["--windows-ncm-python", self.s.path("ncm_python")]
        try:
            self.run(argv, 4 * 3600, log=self.folder / "run.log")
            code = 0
        except RuntimeError:
            code = 1
        result = json.loads((self.folder / "run" / "result.json").read_text(encoding="utf-8"))
        print(f"   runner: {result.get('status')} {result.get('error', '')}".rstrip(), flush=True)
        if result.get("boot_restore_required"):
            self.restore_boot(result)
        print(f"   PICO OS after the run: {self.wait_pico_os()}", flush=True)
        return code

    def restore_boot(self, result: dict):
        """The runner could not restore boot: its device deadline brings the headset to fastboot."""
        print("   boot restore pending: waiting for fastboot", flush=True)

        def in_fastboot():
            if self.s.fastboot_serial not in self.run([self.s.tool("fastboot"), "devices"], 30, check=False):
                raise RuntimeError("not in fastboot yet")
        self.until("fastboot", 45 * 60, in_fastboot)
        saved = result["stock_boot"]
        if backups.digest(Path(saved["path"])) != {"size": saved["size"], "sha256": saved["sha256"]}:
            raise RuntimeError("the pinned stock boot image changed; headset left in fastboot")
        with open(self.folder / "manual-restore.log", "w", encoding="utf-8") as log:
            for command in result["manual_restore_commands"]:
                log.write(self.run(command, 300) + "\n")
        print("   stock boot flashed by the cycle", flush=True)

    def restore_abl(self):
        """Stock boot verified by a full EDL readback, then stock ABL, then PICO OS."""
        self.s.require("adb_serial", "edl_serial")
        print(">> verify stock boot, restore stock ABL", flush=True)
        stock, expected = self.stock_boot()
        self.enter_edl()
        self.check_edl_serial(self.edl("read-part", "boot", self.folder / "boot-readback.bin", log="boot-readback"))
        if backups.digest(self.folder / "boot-readback.bin")["sha256"] != expected["sha256"]:
            raise RuntimeError(f"boot differs from {stock.name} in the backup (headset in EDL)")
        self.run([sys.executable, "-m", "overpressure", "--device", "phoenix", "device", "restore-abl", *self.device_args(),
                  "--readback", self.folder / "stock-abl-readback.bin", "--execute",
                  "--confirm-device", self.s.adb_serial], 900, log=self.folder / "restore-abl.log")
        print(f"   final PICO OS: {self.reset_to_pico_os('reset-final')}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m overpressure cycle", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--userspace", type=Path, metavar="ARCHIVE",
                    help="pico4-userspace.tar.zst to hand over to and check (default: stage 1 only)")
    ap.add_argument("--skip-stage", action="store_true", help="the engineering ABL is already staged")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--keep-abl", action="store_true", help="stop before restoring the stock ABL")
    mode.add_argument("--restore-only", action="store_true", help="only verify stock boot and restore stock ABL")
    args = ap.parse_args(argv)
    settings = localconfig.load()
    folder = ROOT / "out" / ("cycle-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True)
    cycle = Cycle(settings, folder)
    code = 0
    try:
        settings.require("adb_serial", "fastboot_serial")
        if not args.restore_only:
            if not args.skip_stage:
                cycle.stage_abl()
            code = cycle.flash_run(args.userspace)
        if not args.keep_abl:
            cycle.restore_abl()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"STOPPED: {exc}\nevidence: {folder}", file=sys.stderr)
        return 1
    print(f"{'PASSED' if code == 0 else 'FAILED'}: evidence in {folder}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
