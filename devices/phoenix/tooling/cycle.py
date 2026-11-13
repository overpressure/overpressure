# SPDX-License-Identifier: Unlicense
"""Unattended acceptance cycle that ends on stock boot and stock ABL (docs/hardware-testing.md).

  python -m overpressure cycle [--skip-stage] [--keep-abl | --restore-only]

1. Engineering ABL: ADB to EDL, write and read back (python -m overpressure device stage), EDL
   reset, PICO OS. --skip-stage when it is already staged (after the dev loop).
2. python -m overpressure hwtest flash-run with the stage-1 image for PICO4_FIRMWARE. The
   runner restores stock boot itself; if it could not, the device deadline returns the headset
   to fastboot and the runner's recorded commands flash the pinned stock image.
3. ADB to EDL: read boot back and compare it with the backup, then restore stock ABL
   (python -m overpressure device restore-abl, read back). --keep-abl stops before this.
4. EDL reset; PICO OS must finish booting.

--restore-only does steps 3 and 4 (after "python -m overpressure dev restore"). The cycle stops
at the first failure and says where the headset is. Evidence goes to out/cycle-<time>/.
Settings come from .env (devices/phoenix/tooling/localconfig.py).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar

from overpressure import REPO, android, qualcomm, usbaccess
from overpressure import backup as backups
from overpressure.fastboot import NOT_IN_FASTBOOT
from overpressure.process import run, until
from overpressure.qualcomm import edl_devices

from . import ADB_MODE, EDL_RESTART, FASTBOOT_MODE, USB_MODES, device, localconfig

if TYPE_CHECKING:
    from overpressure.process import Timing

ROOT = REPO
T = TypeVar("T")


class Headset:
    """Waits and ADB/EDL steps shared by the cycle and the dev loop."""

    def __init__(
        self,
        settings: localconfig.Settings,
        folder: Path,
        runner: Callable[..., str] = run,
        clock: Timing = time,
        edl_count: Callable[[], int] = edl_devices,
    ) -> None:
        """Work on the headset @p settings name, with evidence in @p folder; tests pass fakes."""
        self.s = settings
        self.folder = folder
        # adb and fastboot meeting a device Linux does not let this user open: NoAccessError with the fix.
        self.run = usbaccess.guard(runner, USB_MODES, {"adb": ADB_MODE, "fastboot": FASTBOOT_MODE}, clock)
        self.clock = clock
        self.edl_count = edl_count
        self.usb_reader: Callable[[], bool] | None = None  #: tests: what reads the EDL device's pipe

    def until(self, name: str, seconds: float, operation: Callable[[], T]) -> T:
        """Retry @p operation until it succeeds, for at most @p seconds."""
        return until(name, seconds, operation, self.clock)

    def wait_pico_os(self, seconds: float = 300) -> str:
        """Wait until PICO OS has finished booting; return its build fingerprint."""
        return android.wait_booted(
            self.s.tool("adb"), self.s.adb_serial, self.run, self.clock, seconds=seconds, system="PICO OS"
        )

    def enter_edl(self) -> None:
        """Bring the headset from PICO OS to EDL, unless it is the one EDL device already.

        PICO OS is left only once it is through with the lens and fan MCU (device.wait_mcu).
        """
        if self.edl_count() != 1:
            device.wait_mcu(self.s.tool("adb"), self.s.adb_serial, self.run, self.clock)
        qualcomm.enter_edl(
            self.s.tool("adb"), self.s.adb_serial, self.run, self.clock, edl_count=self.edl_count, modes=USB_MODES
        )

    def edl(self, *words: str | Path, log: str, timeout: float = 600) -> str:
        """Run edl-ng with the headset's loader; its output goes to @p log.log in the evidence folder.

        A device that does not answer (a stale EDL session) fails in plain words, with the way out.
        """
        path = self.folder / f"{log}.log"
        try:
            return self.run(qualcomm.edl_ng(self.s.edl_program(), self.s.path("loader"), *words), timeout, log=path)
        except RuntimeError as exc:
            try:
                output = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            if qualcomm.unanswered(output + str(exc)):
                msg = f"{qualcomm.NO_ANSWER} Nothing was written to it. {EDL_RESTART}"
                raise RuntimeError(msg) from exc
            raise

    def reset_to_pico_os(self, log: str) -> str:
        """Reset from EDL (edl-ng's plain reset mode), then wait for PICO OS.

        Once the headset stayed off after the final reset, with nothing on USB, and only a hard
        reset by the user brought PICO OS back: the cycle then stops and says so instead of
        retrying anything.
        """
        self.edl("reset", "--mode", "Reset", log=log)
        self.drain()  # Linux: the loader resets only once its last message is read
        try:
            return self.wait_pico_os()
        except RuntimeError as exc:
            where = "still in EDL" if self.edl_count() else "no ADB or EDL device"
            msg = (
                f"PICO OS did not come up after the EDL reset ({where}): hard reset needed; "
                "hold the power button until the headset restarts, nothing is retried"
            )
            raise RuntimeError(msg) from exc

    def release_adb(self) -> None:
        """Stop the adb server (not on Windows), so that fastboot reaches the headset.

        It starts with the first adb command and keeps running; on Linux it can keep hold of the
        headset when it comes back in fastboot mode, and fastboot then does not list it (root's
        fastboot does). The next adb command starts it again; Windows' drivers keep the two apart.
        """
        android.release_usb(self.s.tool("adb"), lambda argv: self.run(argv, 15, check=False))

    def drain(self) -> None:
        """After a power command, read the headset's USB pipe until it leaves (qualcomm.drain_until_gone)."""
        qualcomm.drain_until_gone(self.clock, reader=self.usb_reader)

    def check_edl_serial(self, output: str) -> None:
        """edl-ng reports the device serial when it connects: only the configured headset may be written."""
        qualcomm.check_edl_serial(output, self.s.edl_serial)

    def identify(self) -> None:
        """Identify the EDL device in a read-only session before anything is written: only the configured headset.

        A headset that still runs the Firehose loader of an earlier session names no serial: it is
        restarted into a fresh EDL session (no storage written) and identified again. The sessions
        that follow in the same step need no check: on Linux they would name no serial either.
        """
        output = self.edl("printgpt", log="edl-identify", timeout=120)
        if not qualcomm.serial_shown(output) and not qualcomm.unanswered(output):
            print(
                "   the EDL device runs an earlier session's loader: restarting it into a fresh EDL session", flush=True
            )
            self.run(
                qualcomm.fresh_edl_argv(self.s.edl_program(), self.s.path("loader")),
                60,
                log=self.folder / "edl-fresh.log",
            )
            self.drain()
            qualcomm.wait_fresh_edl(self.edl_count, self.clock)
            usbaccess.require(qualcomm.EDL_MODE, USB_MODES, self.clock)
            output = self.edl("printgpt", log="edl-identify-fresh", timeout=120)
        self.check_edl_serial(output)

    def device_args(self) -> list[str | Path]:
        """Return the device command's arguments for the ABL with this headset's backup and loader."""
        return [
            "--identity",
            self.s.path("identity"),
            "--backup",
            self.s.path("backup"),
            "--loader",
            self.s.path("loader"),
            "--edl-backend",
            "edl-ng",
            "--edl",
            self.s.edl_program(),
            "--partition",
            "abl",
        ]

    def stage_abl(self) -> None:
        """Engineering ABL through EDL (hash and readback checked), then PICO OS."""
        self.s.require("adb_serial", "edl_serial", "engineering_abl_sha256")
        print(">> engineering ABL", flush=True)
        self.enter_edl()
        # A read-only first session identifies the device before anything is written.
        self.identify()
        self.run(
            [
                sys.executable,
                "-m",
                "overpressure",
                "--device",
                "phoenix",
                "device",
                "stage",
                *self.device_args(),
                "--image",
                self.s.path("engineering_abl"),
                "--sha256",
                self.s.engineering_abl_sha256,
                "--readback",
                self.folder / "engineering-abl-readback.bin",
                "--execute",
                "--confirm-device",
                self.s.adb_serial,
            ],
            900,
            log=self.folder / "stage-abl.log",
        )
        print(f"   PICO OS with engineering ABL: {self.reset_to_pico_os('reset-stage')}", flush=True)

    def stock_partition(self, name: str) -> tuple[Path, dict]:
        """Return a partition's image in the backup and its manifest entry."""
        return backups.partition_image(self.s.path("backup"), name)

    def stock_boot(self) -> tuple[Path, dict]:
        """Return the stock boot image of the backup and its manifest entry."""
        return self.stock_partition("boot")


class Cycle(Headset):
    """The acceptance cycle's steps: stage the ABL, the runner's flash-run, verify and restore."""

    def flash_run(self) -> int:
        """Run the acceptance runner's flash-run; restore stock boot if it could not; wait for PICO OS."""
        print(">> hardware acceptance (flash-run)", flush=True)
        image = ROOT / "out" / f"pico4-stage1-{self.s.firmware}.img"
        if not image.exists():
            msg = f"missing {image}: run python -m overpressure build --firmware {self.s.firmware}"
            raise ValueError(msg)
        argv = [
            sys.executable,
            "-m",
            "overpressure",
            "--device",
            "phoenix",
            "hwtest",
            "flash-run",
            "--serial",
            self.s.adb_serial,
            "--confirm-device",
            self.s.adb_serial,
            "--fastboot-serial",
            self.s.fastboot_serial,
            "--backup",
            self.s.path("backup"),
            "--image",
            image,
            "--adb",
            self.s.tool("adb"),
            "--fastboot",
            self.s.tool("fastboot"),
            "--ssh",
            self.s.ssh,
            "--ssh-keygen",
            self.s.ssh_keygen,
            "--out",
            self.folder / "run",
        ]
        ncm = self.s.ncm()
        if ncm:
            argv += ["--windows-ncm-python", ncm]
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

    def restore_boot(self, result: dict) -> None:
        """Flash stock boot with the runner's recorded commands once its deadline brings fastboot."""
        print("   boot restore pending: waiting for fastboot", flush=True)

        def in_fastboot() -> None:
            self.release_adb()
            if self.s.fastboot_serial not in self.run([self.s.tool("fastboot"), "devices"], 30, check=False):
                raise RuntimeError(NOT_IN_FASTBOOT)

        self.until("fastboot", 45 * 60, in_fastboot)
        saved = result["stock_boot"]
        if backups.digest(Path(saved["path"])) != {"size": saved["size"], "sha256": saved["sha256"]}:
            msg = "the pinned stock boot image changed; headset left in fastboot"
            raise RuntimeError(msg)
        with (self.folder / "manual-restore.log").open("w", encoding="utf-8") as log:
            log.writelines(self.run(command, 300) + "\n" for command in result["manual_restore_commands"])
        print("   stock boot flashed by the cycle", flush=True)

    def restore_abl(self) -> None:
        """Stock boot verified by a full EDL readback, then stock ABL, then PICO OS."""
        self.s.require("adb_serial", "edl_serial")
        print(">> verify stock boot, restore stock ABL", flush=True)
        stock, expected = self.stock_boot()
        self.enter_edl()
        self.identify()
        self.edl("read-part", "boot", self.folder / "boot-readback.bin", log="boot-readback")
        if backups.digest(self.folder / "boot-readback.bin")["sha256"] != expected["sha256"]:
            msg = f"boot differs from {stock.name} in the backup (headset in EDL)"
            raise RuntimeError(msg)
        self.run(
            [
                sys.executable,
                "-m",
                "overpressure",
                "--device",
                "phoenix",
                "device",
                "restore-abl",
                *self.device_args(),
                "--readback",
                self.folder / "stock-abl-readback.bin",
                "--execute",
                "--confirm-device",
                self.s.adb_serial,
            ],
            900,
            log=self.folder / "restore-abl.log",
        )
        print(f"   final PICO OS: {self.reset_to_pico_os('reset-final')}", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Run the acceptance cycle; it stops at the first failure and says where the headset is."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure cycle", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--skip-stage", action="store_true", help="the engineering ABL is already staged")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--keep-abl", action="store_true", help="stop before restoring the stock ABL")
    mode.add_argument("--restore-only", action="store_true", help="only verify stock boot and restore stock ABL")
    args = ap.parse_args(argv)
    settings = localconfig.load()
    folder = ROOT / "out" / ("cycle-" + dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True)
    cycle = Cycle(settings, folder)
    code = 0
    try:
        settings.require("adb_serial", "fastboot_serial")
        if not args.restore_only:
            if not args.skip_stage:
                cycle.stage_abl()
            code = cycle.flash_run()
        if not args.keep_abl:
            cycle.restore_abl()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"STOPPED: {exc}\nevidence: {folder}", file=sys.stderr)
        return 1
    print(f"{'PASSED' if code == 0 else 'FAILED'}: evidence in {folder}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
