"""The PICO 4's RAM boot target for overpressure's `fastboot boot` (overpressure/fastboot.py).

It boots the dev image (out/dev/dev-boot.img, from `dev image`), whose SSH key and run
identifier let the host tell that the image is up: after the Windows NCM initialization, the
dev image's authenticated SSH login, as `dev connect` makes it (without its partition readback:
nothing was written). The tested engineering ABL has no `fastboot boot` and answers "unknown
command" after the transfer, so on the PICO 4 the RAM boot ends refused, with the headset in
fastboot, where `dev reboot` starts stage 1 from its partition.
"""
from __future__ import annotations

from pathlib import Path

from overpressure import android, windows_ncm
from overpressure.fastboot import Fastboot, RamBootTarget

from . import devloop, localconfig


class PicoRamBoot(RamBootTarget):
    boot_timeout = 180
    up_timeout = 180
    refused_hint = ("The PICO 4's engineering ABL has no fastboot boot: stage 1 boots from a partition "
                    "(python -m overpressure dev flash); dev reboot starts it from fastboot.")

    def __init__(self, settings=None, dev=None):
        self.s = settings or localconfig.load()
        self._dev = dev
        self.ncm_done = False

    @property
    def dev(self) -> devloop.Dev:
        if self._dev is None:
            self._dev = devloop.Dev(self.s)
        return self._dev

    @property
    def fastboot(self) -> Fastboot:
        return Fastboot(self.s.tool("fastboot"), self.s.fastboot_serial, self.dev.run)

    def image(self) -> Path:
        return devloop.WORK / "dev-boot.img"

    def enter(self) -> None:
        # From PICO OS or a running Linux stage: ADB, else stage 1's own reboot over SSH.
        self.s.require("adb_serial")
        adb = self.dev.run([self.s.tool("adb"), "devices"], 30)
        if f"{self.s.adb_serial}\tdevice" in adb:
            self.dev.run([self.s.tool("adb"), "-s", self.s.adb_serial, "reboot", "bootloader"], 30)
        else:
            self.dev.ssh("pico4-reboot bootloader", 15, check=False)

    def check_unlocked(self) -> None:
        # The form the engineering ABL reports; the stock ABL's DeviceUnlocked=true is refused,
        # as by the acceptance runner.
        self.s.require("fastboot_serial")
        try:
            output = self.fastboot("oem", "device-info", timeout=30)
        except RuntimeError:
            output = self.fastboot("getvar", "unlocked", timeout=30)
        if not android.unlocked(output):
            raise RuntimeError("the bootloader does not report an unlocked state the tools accept "
                               "(stage the engineering ABL: dev stage-abl)")

    def reached(self) -> None:
        # Stage 1 makes new host keys on every boot; the NCM link once, then the login.
        (self.dev.work / "known_hosts").unlink(missing_ok=True)
        if self.s.ncm_python and not self.ncm_done:
            self.dev.run(windows_ncm.command(self.s.path("ncm_python"), self.s.fastboot_serial), 30)
            self.ncm_done = True
        self.dev.authenticate()
