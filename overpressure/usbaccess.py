# SPDX-License-Identifier: Unlicense
"""Whether this Linux user may use the headset's USB devices, and what the owner installs when not.

adb, fastboot and edl-ng open a USB device's file under /dev/bus/usb, which Linux gives only root
unless a udev rule grants it. The Android udev rules that distributions package (android-udev on
Arch, android-udev-rules on Debian) grant the logged-in user every device they list
(TAG+="uaccess"). overpressure runs nothing as root: it reads the installed rules, says which
package to install (or which rule to add where no package covers a mode), and checks a connected
device before a tool needs it. Other systems need no rule: Windows has its drivers, macOS lets
every user open USB devices.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .process import Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .process import Timing

#: Where udev reads rules; a file in an earlier folder hides one of the same name in a later one.
RULE_DIRS = (
    Path("/etc/udev/rules.d"),
    Path("/run/udev/rules.d"),
    Path("/usr/lib/udev/rules.d"),
    Path("/lib/udev/rules.d"),
)
#: uaccess takes effect only when tagged before this file, which turns the tag into the user's access.
SEAT_LATE = "73-seat-late.rules"
SYSFS = Path("/sys/bus/usb/devices")
DEV = Path("/dev/bus/usb")
OS_RELEASE = Path("/etc/os-release")
#: What adb and fastboot print for a device they may not open.
NO_PERMISSIONS = "no permissions"
#: Applies the rules to devices that are already connected.
TRIGGER = "sudo udevadm trigger"
#: Seconds a device may stay without access after it appears: udev grants it a moment later.
GRACE = 6.0
#: The Android udev rules by distribution (os-release ID or ID_LIKE): the package and the file it installs.
PACKAGES = {
    "arch": ("sudo pacman -S android-udev", "51-android.rules"),
    "debian": ("sudo apt install android-udev-rules", "51-android.rules"),
    "ubuntu": ("sudo apt install android-sdk-platform-tools-common", "51-android.rules"),
}
#: Where the fix's own rule goes when no package covers a mode.
OWN_RULES = "/etc/udev/rules.d/70-overpressure.rules"


@dataclass(frozen=True)
class UsbMode:
    """One way a headset appears on USB: a name for the owner, its vendor and product IDs (hex)."""

    name: str
    vendor: str
    product: str

    def rule(self) -> str:
        """Return the udev rule that grants the logged-in user this mode's device."""
        return (
            f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{self.vendor}", ATTR{{idProduct}}=="{self.product}", TAG+="uaccess"'
        )


class NoAccessError(PermissionError, Final):
    """The user may not use a connected device of the headset; the message says what to install."""


# -- the installed rules ---------------------------------------------------------------------------
_PAIR = re.compile(r'\s*([A-Za-z_]+(?:\{[^}]*\})?)\s*(==|!=|\+=|-=|:=|=)\s*"((?:[^"\\]|\\.)*)"\s*(?:,|$)')


def rule_files(dirs: Sequence[Path] = RULE_DIRS) -> list[Path]:
    """Return the rule files udev reads, in its order: by name, an earlier folder's file hiding a later one's."""
    found: dict[str, Path] = {}
    for folder in dirs:
        try:
            names = sorted(p for p in folder.iterdir() if p.suffix == ".rules")
        except OSError:
            continue
        for path in names:
            found.setdefault(path.name, path)
    return [found[name] for name in sorted(found)]


def _rules(text: str) -> list[list[tuple[str, str, str]]]:
    """Return a rules file's rules, each a list of (key, operator, value); continued lines joined."""
    rules = []
    for line in text.replace("\\\n", "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pairs, rest = [], stripped
        while rest:
            found = _PAIR.match(rest)
            if found is None:
                pairs = []  # a line this reader cannot parse counts as no rule
                break
            pairs.append(found.groups())
            rest = rest[found.end() :]
        if pairs:
            rules.append(pairs)
    return rules


def _matches(value: str, pattern: str) -> bool:
    """Tell whether @p value matches a udev pattern: shell globs, alternatives separated by |."""
    return any(fnmatch.fnmatchcase(value, alternative) for alternative in pattern.split("|"))


@dataclass
class _Device:
    """What udev knows of a USB device when it is added, and what the rules have set so far."""

    attrs: dict[str, str]
    env: dict[str, str] = field(default_factory=dict)
    uaccess: bool = False
    mode: str = ""
    group: str = ""

    def known(self, key: str) -> str | None:
        """Return the value a match key sees, or None for a key this reader does not evaluate."""
        if key == "ACTION":
            return "add"
        if key in ("SUBSYSTEM", "SUBSYSTEMS"):
            return "usb"
        found = re.fullmatch(r"(ATTRS?|ENV)\{([^}]*)\}", key)
        if found is None:
            return None
        if found.group(1) == "ENV":
            return self.env.get(found.group(2), "")
        return self.attrs.get(found.group(2))


def _matched(rule: list[tuple[str, str, str]], device: _Device) -> bool:
    """Tell whether every match of @p rule holds for @p device.

    A match this reader cannot evaluate counts as not holding: it never grants by guessing.
    """
    for key, op, value in rule:
        if op not in ("==", "!="):
            continue
        seen = device.known(key)
        if seen is None or _matches(seen, value) != (op == "=="):
            return False
    return True


def _assign(rule: list[tuple[str, str, str]], device: _Device, *, before_seat: bool) -> str | None:
    """Apply @p rule's ENV, TAG, MODE and GROUP assignments to @p device; return its GOTO label, if any."""
    target = None
    for key, op, value in rule:
        if key.startswith("ENV{") and op in ("=", ":="):
            device.env[key[4:-1]] = value
        elif key == "TAG" and op in ("+=", "=", ":=") and "uaccess" in value.split():
            device.uaccess = device.uaccess or before_seat
        elif key == "MODE" and op in ("=", ":="):
            device.mode = value
        elif key == "GROUP" and op in ("=", ":="):
            device.group = value
        elif key == "GOTO":
            target = value
    return target


def _apply(rules: list[list[tuple[str, str, str]]], device: _Device, *, before_seat: bool) -> None:
    """Run one file's rules on @p device, following its GOTOs to their labels (a missing label ends the file)."""
    # A label may share its line with assignments (LABEL="x", ENV{y}="1", GOTO="z"): GOTO runs that line.
    labels = {value: n for n, rule in enumerate(rules) for key, _, value in rule if key == "LABEL"}
    n = 0
    while n < len(rules):
        rule, n = rules[n], n + 1
        if not _matched(rule, device):
            continue
        target = _assign(rule, device, before_seat=before_seat)
        if target is not None:
            n = labels.get(target, len(rules))


def granted(mode: UsbMode, files: Iterable[Path], groups: Iterable[str] = ()) -> bool:
    """Tell whether the rules in @p files let this user open @p mode's device.

    The user may when a rule tags it uaccess (before 73-seat-late.rules), or its mode lets every
    user write, or its group is one of @p groups and may write.
    """
    attrs = {"idVendor": mode.vendor, "idProduct": mode.product}
    device = _Device(attrs, {"DEVTYPE": "usb_device", "ID_VENDOR_ID": mode.vendor, "ID_MODEL_ID": mode.product})
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _apply(_rules(text), device, before_seat=path.name < SEAT_LATE)
    if device.uaccess:
        return True
    bits = int(device.mode, 8) if re.fullmatch(r"0?[0-7]{3,4}", device.mode) else 0
    return bool(bits & 0o002) or (bool(bits & 0o020) and device.group in set(groups))


def user_groups() -> list[str]:
    """Return the names of this process's groups; none where there are no POSIX groups (Windows)."""
    try:
        import grp  # noqa: PLC0415  # POSIX only
    except ImportError:
        return []
    known = {entry.gr_gid: entry.gr_name for entry in grp.getgrall()}
    return [known[gid] for gid in os.getgroups() if gid in known]


# -- the fix ---------------------------------------------------------------------------------------
def distribution(path: Path = OS_RELEASE) -> list[str]:
    """Return this system's distribution and the ones it is like (os-release ID, then ID_LIKE)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    values = dict(re.findall(r'^([A-Z_]+)="?([^"\n]*)"?$', text, re.MULTILINE))
    return [values.get("ID", ""), *values.get("ID_LIKE", "").split()]


def fix(missing: Sequence[UsbMode], *, release: Path = OS_RELEASE, files: Sequence[Path] = ()) -> str:
    """Return what the owner runs so that @p missing modes are granted.

    The distribution's Android rules package when it is not installed yet; otherwise (installed but
    not covering a mode, or no package known) the rules to add.
    """
    installed = {p.name for p in files}
    for name in distribution(release):
        package = PACKAGES.get(name)
        if package is not None and package[1] not in installed:
            return f"{package[0]}, then {TRIGGER} (or plug the headset in again)"
    lines = " ".join(f"'{mode.rule()}'" for mode in missing)
    return (
        f"printf '%s\\n' {lines} | sudo tee -a {OWN_RULES} >/dev/null && sudo udevadm control --reload-rules "
        f"&& {TRIGGER}"
    )


def missing_modes(
    modes: Sequence[UsbMode], *, dirs: Sequence[Path] = RULE_DIRS, groups: Iterable[str] | None = None
) -> tuple[list[UsbMode], list[Path]]:
    """Return the modes no installed rule grants this user, and the rule files read."""
    files = rule_files(dirs)
    names = list(groups) if groups is not None else user_groups()
    return [mode for mode in modes if not granted(mode, files, names)], files


# -- a connected device ----------------------------------------------------------------------------
def device_files(vendor: str, product: str, sysfs: Path = SYSFS, dev: Path = DEV) -> list[Path]:
    """Return the device files of the connected USB devices with @p vendor and @p product (Linux)."""
    found = []
    try:
        folders = sorted(sysfs.iterdir())
    except OSError:
        return []
    for folder in folders:
        try:
            ids = ((folder / "idVendor").read_text().strip(), (folder / "idProduct").read_text().strip())
            if ids != (vendor, product):
                continue
            bus, number = int((folder / "busnum").read_text()), int((folder / "devnum").read_text())
        except (OSError, ValueError):
            continue
        found.append(dev / f"{bus:03d}" / f"{number:03d}")
    return found


def denied(
    mode: UsbMode, *, sysfs: Path = SYSFS, dev: Path = DEV, access: Callable[[Path], bool] | None = None
) -> list[Path]:
    """Return the connected devices of @p mode this user may not write (always none off Linux)."""
    if sys.platform != "linux" and access is None:
        return []
    writable = access or (lambda path: os.access(path, os.W_OK))
    return [path for path in device_files(mode.vendor, mode.product, sysfs, dev) if not writable(path)]


def listing_denied(output: str) -> bool:
    """Tell whether adb's or fastboot's device list shows a device it may not open."""
    return NO_PERMISSIONS in output


def message(
    mode: UsbMode, modes: Sequence[UsbMode], *, dirs: Sequence[Path] = RULE_DIRS, release: Path = OS_RELEASE
) -> str:
    """Return why a connected device of @p mode cannot be used, and the fix, in plain words.

    Rules that grant it but have not reached the connected device yet need only the trigger.
    """
    missing, files = missing_modes(modes, dirs=dirs)
    if mode not in missing:
        how = f"{TRIGGER} (or unplug the headset and plug it in again)"
    else:
        how = fix([m for m in modes if m in missing], release=release, files=files)
    return f"This computer may not use the headset in {mode.name} over USB: Linux needs a udev rule for it. Run: {how}"


def guard(
    runner: Callable[..., str],
    modes: Sequence[UsbMode],
    tools: dict[str, UsbMode],
    clock: Timing,
    grace: float = GRACE,
) -> Callable[..., str]:
    """Return @p runner, with adb's and fastboot's "no permissions" turned into NoAccessError.

    @p tools maps a program's name (adb, fastboot) to the mode it talks to. A command that meets a
    device it may not open did nothing to it: it runs again for @p grace s, the time udev may take
    to grant a device that has just appeared, then NoAccessError names the fix from @p modes.
    """

    def run(argv: Sequence[object], *args: object, **kwargs: object) -> str:
        mode = tools.get(Path(str(argv[0])).stem)
        if mode is None:
            return runner(argv, *args, **kwargs)
        end = clock.monotonic() + grace
        while True:
            try:
                output = runner(argv, *args, **kwargs)
            except (RuntimeError, ValueError) as exc:
                if not listing_denied(str(exc)) or clock.monotonic() >= end:
                    if listing_denied(str(exc)):
                        raise NoAccessError(message(mode, modes)) from exc
                    raise
            else:
                if not listing_denied(output):
                    return output
                if clock.monotonic() >= end:
                    raise NoAccessError(message(mode, modes))
            clock.sleep(0.5)

    return run


@dataclass
class Denial:
    """A device without access, said only once it has stayed so for GRACE s (udev grants a moment late)."""

    clock: Timing
    grace: float = GRACE
    since: float | None = None

    def see(self, is_denied: bool, text: Callable[[], str]) -> None:  # noqa: FBT001  # the state just seen
        """Record whether the device is denied now; raise NoAccessError with @p text once it stayed so."""
        if not is_denied:
            self.since = None
            return
        now = self.clock.monotonic()
        if self.since is None:
            self.since = now
        if now - self.since >= self.grace:
            raise NoAccessError(text())


def require(
    mode: UsbMode,
    modes: Sequence[UsbMode],
    clock: Timing,
    *,
    grace: float = GRACE,
    denied_now: Callable[[], bool] | None = None,
) -> None:
    """Return once a connected device of @p mode may be used; raise NoAccessError if it stays denied for @p grace s."""
    check = denied_now or (lambda: bool(denied(mode)))
    end = clock.monotonic() + grace
    while check():
        if clock.monotonic() >= end:
            raise NoAccessError(message(mode, modes))
        clock.sleep(0.5)
