"""Booting an image from RAM with `fastboot boot`, for any headset whose bootloader allows it.

A RAM boot writes nothing: the next normal boot starts whatever the headset's partitions hold.
That makes it the first contact with a new headset, before its flashing path is trusted. The
sequence is the same everywhere; what differs per headset is a RamBootTarget: how it reaches
fastboot, how its unlock state reads, which image it boots and how to tell that the image is up.

The sequence never falls back to flashing. A bootloader without `fastboot boot` answers after
the transfer (the PICO 4's engineering ABL: "unknown command"); that is Refused, a result of its
own, and the headset stays in fastboot.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import bootimg
from .process import Clock, until

#: How bootloaders say that they do not boot from RAM.
REFUSED = re.compile(r"unknown command|not allowed|not supported|unsupported|command not found|"
                     r"boot is disabled|not implemented", re.I)


class Refused(RuntimeError):
    """The bootloader has no `fastboot boot` (or refuses it): nothing was written."""


def run(argv: List[str], timeout: float) -> str:
    """A program's combined output; a nonzero exit or a FAILED line raises RuntimeError with it."""
    process = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout)
    output = process.stdout.decode("utf-8", "replace").strip()
    if process.returncode or re.search(r"\bFAILED\b", output):
        raise RuntimeError(f"{Path(str(argv[0])).name} {' '.join(map(str, argv[1:3]))} failed: {output[-800:]}")
    return output


@dataclass
class Fastboot:
    """fastboot for one headset: every command names its serial."""

    tool: str
    serial: str
    runner: Callable[[List[str], float], str] = run

    def __call__(self, *words: str, timeout: float = 60) -> str:
        if not self.serial:
            raise ValueError("no fastboot serial for this headset (see .env.example)")
        return self.runner([self.tool, "-s", self.serial, *words], timeout)

    def present(self) -> bool:
        """The headset is in fastboot: listed with its serial, and nothing else is."""
        rows = [line.split() for line in self.runner([self.tool, "devices"], 30).splitlines() if line.strip()]
        serials = [row[0] for row in rows if len(row) >= 2 and row[1] == "fastboot"]
        if len(serials) > 1:
            raise RuntimeError(f"{len(serials)} devices in fastboot; connect only this headset")
        return serials == [self.serial]


class RamBootTarget:
    """What a headset supplies for a RAM boot (Headset.ram_boot)."""

    #: Seconds for the transfer and the boot command, and for the image to come up.
    boot_timeout: float = 180
    up_timeout: float = 180
    #: Said when the bootloader refuses: where this headset boots from instead.
    refused_hint: str = ""

    @property
    def fastboot(self) -> Fastboot:
        raise NotImplementedError

    def image(self) -> Path:
        """The image to boot when none is given."""
        raise NotImplementedError

    def enter(self) -> None:
        """Brings the headset into fastboot (from Android, say); called when it is not there."""
        raise RuntimeError("the headset is not in fastboot")

    def check_unlocked(self) -> None:
        """Raises unless the bootloader reports a state that may boot an unsigned image."""

    def reached(self) -> None:
        """Raises until the booted image answers; returns once it does."""
        raise NotImplementedError


@dataclass
class Result:
    image: Path
    seconds_to_up: float


def ram_boot(target: RamBootTarget, image: Optional[Path] = None, log: Callable[[str], None] = print,
             clock: Optional[Clock] = None) -> Result:
    """Boots @p image (else the target's) from RAM and waits until it answers."""
    clock = clock or Clock()
    image = Path(image) if image is not None else target.image()
    if not image.is_file():
        raise ValueError(f"no image {image}")
    bootimg.parse(image.read_bytes())  # an Android boot image, or ValueError
    fb = target.fastboot
    if not fb.present():
        log(">> into fastboot")
        target.enter()

        def present():
            if not fb.present():
                raise RuntimeError("not in fastboot yet")
        until("fastboot", 60, present, clock)
    log(">> unlock state")
    target.check_unlocked()
    log(f">> fastboot boot {image} (RAM only, nothing is written)")
    try:
        output = fb("boot", str(image), timeout=target.boot_timeout)
    except RuntimeError as exc:
        if REFUSED.search(str(exc)):
            raise Refused(_refused(target, exc)) from exc
        raise
    if REFUSED.search(output):
        raise Refused(_refused(target, output))
    started = clock.monotonic()
    log(">> waiting for the image to answer")
    until("the RAM-booted image", target.up_timeout, target.reached, clock)
    took = clock.monotonic() - started
    log(f">> up after {took:.0f} s; the next normal boot starts what the partitions hold")
    return Result(image, took)


def _refused(target: RamBootTarget, why) -> str:
    # The bootloader's own answer (fastboot's FAILED line), not the transfer's progress around it.
    lines = [line.strip() for line in str(why).splitlines() if line.strip()]
    answer = next((line for line in lines if REFUSED.search(line)), lines[-1] if lines else "")
    text = f"the bootloader refuses fastboot boot ({answer[-200:]}); nothing was written"
    return text + (f". {target.refused_hint}" if target.refused_hint else "")
