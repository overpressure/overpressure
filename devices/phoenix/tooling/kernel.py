# SPDX-License-Identifier: Unlicense
"""Valve's kernel for the PICO 4: the release of overpressure/linux this repository pins.

    python -m overpressure --device phoenix kernel [fetch]       the pinned release's files into out/kernel
    python -m overpressure --device phoenix kernel build [WORK]  the pinned commit built here instead, checked
    python -m overpressure --device phoenix kernel check DIR     DIR's files against the pin
    python -m overpressure --device phoenix kernel contracts     the names and paths the kernel and this
                                                                 repository share, against the pinned commit

The kernel's source is overpressure/linux (phoenix/7.2 and its layers); kernel/release.json pins one
of its tags: the commit, the build image it was built with and every file's SHA-256. The installer
downloads those files from the tag's release and checks them. `kernel build` builds the same commit
here instead (scripts/build-kernel-step.sh, in the build image the release was built with) and checks
the result against the same hashes: how an owner verifies a release. Only overpressure/linux's CI
builds; this repository's CI downloads the pinned release and checks it.

The contracts are what no build checks: a name or a path one side uses and the other provides (a
firmware file, a V4L2 control, a sysfs name). Each reads the pinned commit's files from GitHub, without
its history, and this repository's own; a renamed side fails before anything is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from overpressure import REPO, tools

Log = Callable[[str], None]
Opener = Callable[..., object]

#: The pin: overpressure/linux's tag, its commit, the build image and its files' hashes.
RELEASE = Path(__file__).resolve().parents[1] / "kernel" / "release.json"
#: Where the files go, for the boot image (dev boot-image).
OUT = REPO / "out" / "kernel"
#: The pinned commit's files the contracts read, kept per commit.
CACHE = REPO / "cache" / "kernel"
#: The self-build's folder, on the Linux side (the build container's volume).
WORK = "/var/tmp/pico4-kernel-build"  # noqa: S108  # a root-owned build folder, not a temporary file


@dataclass(frozen=True)
class Release:
    """The pinned release of overpressure/linux."""

    repository: str
    tag: str
    commit: str
    image: str
    files: dict[str, str]  #: name -> SHA-256

    def url(self, name: str) -> str:
        """Return the download URL of the release's file @p name."""
        tag = urllib.parse.quote(self.tag, safe="")
        return f"https://github.com/{self.repository}/releases/download/{tag}/{name}"

    def source_url(self, path: str) -> str:
        """Return the URL of the pinned commit's file @p path (the tree as it is, no history)."""
        return f"https://raw.githubusercontent.com/{self.repository}/{self.commit}/{path}"


def release(path: Path = RELEASE) -> Release:
    """Return the pin in @p path."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return Release(data["repository"], data["tag"], data["commit"], data["image"], dict(data["files"]))


def sha256(path: Path) -> str:
    """Return @p path's SHA-256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mismatches(folder: Path, pin: Release) -> list[str]:
    """Return the files in @p folder that are missing or differ from the pin, as messages."""
    found = []
    for name, digest in pin.files.items():
        path = folder / name
        if not path.is_file():
            found.append(f"{name}: missing")
        elif sha256(path) != digest:
            found.append(f"{name}: sha256 {sha256(path)}, pinned {digest}")
    return found


def current(folder: Path = OUT, pin: Release | None = None) -> bool:
    """Tell whether @p folder holds the pinned kernel's files."""
    return not mismatches(folder, pin or release())


def fetch(
    folder: Path = OUT, pin: Release | None = None, *, log: Log = print, opener: Opener = urllib.request.urlopen
) -> list[str]:
    """Download the pinned release's files into @p folder, each checked by hash; return their names.

    A file that is there with its pinned hash is kept; nothing is written that fails its hash.
    """
    pin = pin or release()
    folder.mkdir(parents=True, exist_ok=True)
    log(f">> overpressure/linux {pin.tag} ({pin.commit[:12]})")
    fetched = []
    for name, digest in pin.files.items():
        path = folder / name
        if path.is_file() and sha256(path) == digest:
            log(f"   {name}: there")
            continue
        data = tools.download(pin.url(name), digest, log, opener)
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        partial.replace(path)
        fetched.append(name)
    return fetched


def build_argv(work: str = WORK) -> list[str]:
    """Return the command line that builds the pinned commit here (in the build container)."""
    step = "devices/phoenix/scripts/build-kernel-step.sh"
    return [sys.executable, "-m", "overpressure", "container", "run", "--", "bash", step, work]


def build(work: str = WORK, pin: Release | None = None, call: Callable[..., int] = subprocess.call) -> int:
    """Build the pinned commit in the image the release was built with; return the build's exit status."""
    pin = pin or release()
    env = dict(os.environ, OVERPRESSURE_BUILDER=pin.image)
    return call(build_argv(work), env=env, cwd=REPO)


# -- the contracts ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Contract:
    """A name or path this repository uses and the kernel must provide.

    Each side is a list of (file, pattern); every match's first group is a value. The contract holds
    when this repository's values (after @p here_value) are all among the kernel's (after
    @p kernel_value), and both sides found some.
    """

    name: str
    kernel: tuple[tuple[str, str], ...]
    here: tuple[tuple[str, str], ...]
    kernel_value: Callable[[str], str] = field(default=lambda value: value)
    here_value: Callable[[str], str] = field(default=lambda value: value)


def _numbered(name: str) -> str:
    """Return a workqueue's name as the kernel formats it: ring0 -> ring%d."""
    return re.sub(r"\d+$", "%d", name)


#: Where this repository uses the kernel's names: the image's files, its build, Monado's cameras.
_ROOTFS = "devices/phoenix/userspace/rootfs"
_LIBEXEC = f"{_ROOTFS}/usr/libexec/pico4"
_BUILD = "devices/phoenix/scripts/build-userspace.sh"
_CAM = "devices/phoenix/src/monado/pico4_cam.c"
CONTRACTS = (
    Contract(
        "the RGB camera's register table: where the driver loads it, where the image installs it",
        (("drivers/media/i2c/pico4-imx471.c", r'#define IMX471_MODE_FIRMWARE\s+"([^"]+)"'),),
        ((_BUILD, r"usr/lib/firmware/(qcom/sm8250/pico4/imx471-mode\.bin)"),),
    ),
    Contract(
        "the audio calibration: where the driver loads it, where the image installs it",
        (("sound/soc/qcom/qdsp6/q6calibration-pico4.c", r'#define CAL_FIRMWARE\s+"([^"]+)"'),),
        ((_BUILD, r"usr/lib/firmware/(qcom/sm8250/pico4/audio-calibration\.bin)"),),
    ),
    Contract(
        "the audio calibration file's magic: what pico4-audiocal writes, what the driver takes",
        (("sound/soc/qcom/qdsp6/q6calibration-pico4.c", r"#define CAL_MAGIC\s+(0x[0-9a-fA-F]+)"),),
        ((f"{_ROOTFS}/usr/bin/pico4-audiocal", r'^MAINLINE_MAGIC = b"(\w+)"'),),
        kernel_value=lambda value: int(value, 16).to_bytes(4, "little").decode("ascii"),
    ),
    Contract(
        "the audio calibration file's version",
        (("sound/soc/qcom/qdsp6/q6calibration-pico4.c", r"#define CAL_VERSION\s+(\d+)"),),
        ((f"{_ROOTFS}/usr/bin/pico4-audiocal", r"^MAINLINE_VERSION = (\d+)"),),
    ),
    Contract(
        "the RGB camera's width: the mode imx471_mode.py takes and Monado's frames, the size the driver gives",
        (("drivers/media/i2c/pico4-imx471.c", r"#define IMX471_WIDTH\s+(\d+)"),),
        (
            ("devices/phoenix/kernel/imx471_mode.py", r"^WIDTH, HEIGHT, \w+, \w+, \w+, \w+ = (\d+),"),
            ("devices/phoenix/src/monado/pico4_cam.h", r"#define PICO4_CAM_RGB_WIDTH (\d+)"),
        ),
    ),
    Contract(
        "the RGB camera's height",
        (("drivers/media/i2c/pico4-imx471.c", r"#define IMX471_HEIGHT\s+(\d+)"),),
        (
            ("devices/phoenix/kernel/imx471_mode.py", r"^WIDTH, HEIGHT, \w+, \w+, \w+, \w+ = \d+, (\d+),"),
            ("devices/phoenix/src/monado/pico4_cam.h", r"#define PICO4_CAM_RGB_HEIGHT (\d+)"),
        ),
    ),
    Contract(
        "the RGB camera's longest exposure: the mode's frame length less 10 lines",
        (("drivers/media/i2c/pico4-imx471.c", r"#define IMX471_MAX_LINES\s+(\d+)"),),
        (
            (
                "devices/phoenix/kernel/imx471_mode.py",
                r"^WIDTH, HEIGHT, \w+, \w+, \w+, \w+ = \d+, \d+, \d+, \d+, (\d+),",
            ),
        ),
        here_value=lambda value: str(int(value) - 10),
    ),
    Contract(
        "the panels' backlight Monado switches for standby",
        (("drivers/gpu/drm/panel/panel-pico4-ls026b3sa.c", r'devm_backlight_device_register\(dev, "([^"]+)"'),),
        (("devices/phoenix/src/monado/pico4_hmd.c", r"/sys/class/backlight/([\w-]+)/"),),
    ),
    Contract(
        "the RGB camera's paused control: its id in the driver and in Monado",
        (
            (
                "drivers/media/i2c/pico4-imx471.c",
                r"#define V4L2_CID_PICO4_PAUSED\s+\(V4L2_CID_USER_BASE \| (0x[0-9a-fA-F]+)\)",
            ),
        ),
        ((_CAM, r"#define V4L_CID_RGB_PAUSED \(V4L2_CID_USER_BASE \| (0x[0-9a-fA-F]+)\)"),),
        kernel_value=str.lower,
        here_value=str.lower,
    ),
    Contract(
        "the CAMSS entities Monado links the camera pipelines by",
        (
            ("drivers/media/platform/qcom/camss/camss-csiphy.c", r'#define MSM_CSIPHY_NAME "([^"]+)"'),
            ("drivers/media/platform/qcom/camss/camss-csid.c", r'#define MSM_CSID_NAME "([^"]+)"'),
            ("drivers/media/platform/qcom/camss/camss-vfe.c", r'#define MSM_VFE_NAME "([^"]+)"'),
        ),
        ((_CAM, r'snprintf\(\w+, sizeof\(\w+\), "(msm_[a-z]+?)%d'),),
    ),
    Contract(
        "the GPU's workqueues kthread-priorities raises, with their sysfs attributes",
        (
            ("drivers/gpu/drm/msm/msm_kms.h", r'alloc_ordered_workqueue\("([^"]+)", WQ_SYSFS'),
            ("drivers/gpu/drm/msm/dsi/dsi_host.c", r'alloc_ordered_workqueue\("([^"]+)", WQ_SYSFS'),
            ("drivers/gpu/drm/msm/msm_ringbuffer.c", r'msm_gem_object_set_name\(ring->bo, "([^"]+)"'),
        ),
        ((f"{_LIBEXEC}/kthread-priorities", r"^for wq in ([^;]+);"),),
        here_value=_numbered,
    ),
    Contract(
        "the power supplies the scripts read",
        (("drivers/power/supply/pico4-pm8150b-battery.c", r'^\t\.name = "([^"]+)"'),),
        ((_ROOTFS, r"power_supply/(\w+)/"),),
    ),
    Contract(
        "the power supplies' attributes the scripts read",
        (("drivers/power/supply/pico4-pm8150b-battery.c", r"^\tPOWER_SUPPLY_PROP_(\w+),$"),),
        ((_ROOTFS, r"power_supply/\w+/(\w+)"),),
        kernel_value=str.lower,
    ),
    Contract(
        "the USB gadget functions the system's gadget creates",
        (("arch/arm64/configs/pico4.config", r"^CONFIG_USB_CONFIGFS_(\w+)=y$"),),
        ((f"{_LIBEXEC}/usb-gadget", r"functions/(\w+)\."),),
        kernel_value=str.lower,
    ),
    Contract(
        "the video codec's firmware: the name the device tree gives, the file the image's probe waits for",
        (("arch/arm64/boot/dts/qcom/sm8250-pico4-phoenix.dts", r'firmware-name = "(venus\.mdt)";'),),
        ((f"{_ROOTFS}/usr/lib/systemd/system/pico4-venus-probe.service", r"/usr/lib/firmware/(venus\.mdt)$"),),
    ),
    Contract(
        "the device tree the boot image takes",
        (("arch/arm64/boot/dts/qcom/Makefile", r"\+= (sm8250-pico4-[\w-]+\.dtb)$"),),
        (("devices/phoenix/kernel/recovery_image.py", r'^DTB = "([^"]+)"'),),
    ),
)


def _values(text: str, pattern: str) -> list[str]:
    """Return the first group of every match of @p pattern in @p text, split where a match lists several."""
    found = []
    for match in re.finditer(pattern, text, re.MULTILINE):
        found += match.group(1).split()
    return found


def _here_text(path: str, root: Path) -> str:
    """Return a file of this repository, or every file of a folder, as text."""
    target = root / path
    files = sorted(p for p in target.rglob("*") if p.is_file()) if target.is_dir() else [target]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def kernel_file(
    path: str, pin: Release | None = None, *, cache: Path = CACHE, opener: Opener = urllib.request.urlopen
) -> str:
    """Return the pinned commit's file @p path, kept in @p cache per commit."""
    pin = pin or release()
    kept = cache / pin.commit / path
    if not kept.is_file():
        with opener(pin.source_url(path), timeout=60) as response:  # ty: ignore[invalid-context-manager]  # urlopen's response
            data = response.read()
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
    return kept.read_text(encoding="utf-8", errors="replace")


def check(
    contracts: tuple[Contract, ...] = CONTRACTS, read: Callable[[str], str] = kernel_file, root: Path = REPO
) -> list[str]:
    """Return the broken contracts, each as one line that names both sides."""
    broken = []
    for contract in contracts:
        kernel = {contract.kernel_value(v) for path, pattern in contract.kernel for v in _values(read(path), pattern)}
        here = {
            contract.here_value(v) for path, pattern in contract.here for v in _values(_here_text(path, root), pattern)
        }
        if not here:
            broken.append(f"{contract.name}: this repository's side found nothing ({contract.here[0][0]})")
        elif not kernel:
            broken.append(f"{contract.name}: the kernel's side found nothing ({contract.kernel[0][0]})")
        elif not here <= kernel:
            missing = ", ".join(sorted(here - kernel))
            broken.append(
                f"{contract.name}: this repository uses {missing}; the kernel has {', '.join(sorted(kernel))}"
            )
    return broken


def main(argv: list[str] | None = None) -> int:
    """Run the kernel command line."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure --device phoenix kernel", description=__doc__.split("\n\n")[0]
    )
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("fetch", help="the pinned release's files into out/kernel (the default)")
    built = sub.add_parser("build", help="the pinned commit built here, checked against the release")
    built.add_argument("work", nargs="?", default=WORK, help=f"the build folder in the container (default {WORK})")
    checked = sub.add_parser("check", help="a folder's kernel files against the pin")
    checked.add_argument("folder", type=Path)
    sub.add_parser("contracts", help="the names and paths the kernel and this repository share")
    args = ap.parse_args(argv)
    pin = release()
    if args.action == "build":
        return build(args.work, pin)
    if args.action == "check":
        found = mismatches(args.folder, pin)
        for line in found:
            print(f"error: {line}", file=sys.stderr)
        if not found:
            print(f"{args.folder}: the files of {pin.tag} ({pin.commit[:12]})")
        return 1 if found else 0
    if args.action == "contracts":
        broken = check(read=lambda path: kernel_file(path, pin))
        for contract in CONTRACTS:
            print(f"{'broken' if any(b.startswith(contract.name) for b in broken) else 'ok':6} {contract.name}")
        for line in broken:
            print(f"error: {line}", file=sys.stderr)
        return 1 if broken else 0
    try:
        fetch(OUT, pin)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{OUT}: the files of {pin.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
