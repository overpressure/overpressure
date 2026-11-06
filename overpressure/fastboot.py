# SPDX-License-Identifier: Unlicense
"""Booting an image from RAM with `fastboot boot`, for any headset whose bootloader allows it.

A RAM boot writes nothing: the next normal boot starts whatever the headset's partitions hold.
That makes it the first contact with a new headset, before its flashing path is trusted. The
sequence is the same everywhere; what differs per headset is a RamBootTarget: how it reaches
fastboot, how its unlock state reads, which image it boots and how to tell that the image is up.

The sequence never falls back to flashing. A bootloader without `fastboot boot` answers after
the transfer (the PICO 4's engineering ABL: "unknown command"); that is Refused, a result of its
own, and the headset stays in fastboot.

flash() is the write: an image goes sparse above a size, as bootloaders crash on large raw
downloads, and raw below.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import bootimg, sparse
from .process import Clock, Timing, until

#: How bootloaders say that they do not boot from RAM.
REFUSED = re.compile(
    r"unknown command|not allowed|not supported|unsupported|command not found|"
    r"boot is disabled|not implemented",
    re.IGNORECASE,
)


class Refused(RuntimeError):  # noqa: N818  # a result of its own, not a failure: the documented name
    """The bootloader has no `fastboot boot` (or refuses it): nothing was written."""


def run(argv: list[str], timeout: float) -> str:
    """Return a program's combined output; a nonzero exit or a FAILED line raises RuntimeError with it."""
    process = subprocess.run(  # noqa: S603  # fastboot with the sequence's fixed words, no shell
        [str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False
    )
    output = process.stdout.decode("utf-8", "replace").strip()
    if process.returncode or re.search(r"\bFAILED\b", output):
        msg = f"{Path(str(argv[0])).name} {' '.join(map(str, argv[1:3]))} failed: {output[-800:]}"
        raise RuntimeError(msg)
    return output


#: The fields of a `fastboot devices` line that matter: the serial and the state.
DEVICE_ROW_FIELDS = 2


@dataclass
class Fastboot:
    """fastboot for one headset: every command names its serial."""

    tool: str
    serial: str
    runner: Callable[[list[str], float], str] = run

    def __call__(self, *words: str, timeout: float = 60) -> str:
        """Run fastboot -s SERIAL WORDS...; return its output."""
        if not self.serial:
            msg = "no fastboot serial for this headset (see .env.example)"
            raise ValueError(msg)
        return self.runner([self.tool, "-s", self.serial, *words], timeout)

    def present(self) -> bool:
        """Tell whether the headset is in fastboot: listed with its serial, and nothing else is."""
        rows = [line.split() for line in self.runner([self.tool, "devices"], 30).splitlines() if line.strip()]
        serials = [row[0] for row in rows if len(row) >= DEVICE_ROW_FIELDS and row[1] == "fastboot"]
        if len(serials) > 1:
            msg = f"{len(serials)} devices in fastboot; connect only this headset"
            raise RuntimeError(msg)
        return serials == [self.serial]


#: Images above this size go to fastboot as sparse images: ABLs crash on large raw downloads (an
#: engineering ABL on a 100 MiB recovery image).
SPARSE_ABOVE = 64 << 20


def flash(
    fastboot: Callable[..., str],
    partition: str,
    image: Path,
    *,
    above: int = SPARSE_ABOVE,
    sparse_path: Path | None = None,
) -> None:
    """Write @p image to @p partition: as a sparse image above @p above bytes, else raw.

    @p fastboot runs fastboot on the headset (its words, a timeout keyword). The sparse image is
    written to @p sparse_path (else next to the image, .simg) and removed once it is sent.
    """
    if image.stat().st_size > above:
        # Sparse, the bootloader gets the data blocks in chunks it takes.
        simg = sparse_path or image.with_suffix(".simg")
        simg.write_bytes(sparse.encode(image.read_bytes()))
        fastboot("flash", partition, simg, timeout=600)
        simg.unlink()
    else:
        fastboot("flash", partition, image, timeout=180)


class RamBootTarget:
    """What a headset supplies for a RAM boot (Headset.ram_boot)."""

    #: Seconds for the transfer and the boot command, and for the image to come up.
    boot_timeout: float = 180
    up_timeout: float = 180
    #: Said when the bootloader refuses: where this headset boots from instead.
    refused_hint: str = ""

    @property
    def fastboot(self) -> Fastboot:
        """Return fastboot for this headset."""
        raise NotImplementedError

    def image(self) -> Path:
        """Return the image to boot when none is given."""
        raise NotImplementedError

    def enter(self) -> None:
        """Bring the headset into fastboot (from Android, say); called when it is not there."""
        msg = "the headset is not in fastboot"
        raise RuntimeError(msg)

    def check_unlocked(self) -> None:
        """Raise unless the bootloader reports a state that may boot an unsigned image."""

    def reached(self) -> None:
        """Raise until the booted image answers; return once it does."""
        raise NotImplementedError


def ram_boot(
    target: RamBootTarget,
    image: Path | None = None,
    log: Callable[[str], None] = print,
    clock: Timing | None = None,
) -> None:
    """Boot @p image (else the target's) from RAM and wait until it answers."""
    clock = clock or Clock()
    image = Path(image) if image is not None else target.image()
    if not image.is_file():
        msg = f"no image {image}"
        raise ValueError(msg)
    bootimg.parse(image.read_bytes())  # an Android boot image, or ValueError
    fb = target.fastboot
    if not fb.present():
        log(">> into fastboot")
        target.enter()

        def present() -> None:
            if not fb.present():
                msg = "not in fastboot yet"
                raise RuntimeError(msg)

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


def _refused(target: RamBootTarget, why: object) -> str:
    # The bootloader's own answer (fastboot's FAILED line), not the transfer's progress around it.
    lines = [line.strip() for line in str(why).splitlines() if line.strip()]
    answer = next((line for line in lines if REFUSED.search(line)), lines[-1] if lines else "")
    text = f"the bootloader refuses fastboot boot ({answer[-200:]}); nothing was written"
    return text + (f". {target.refused_hint}" if target.refused_hint else "")
