# SPDX-License-Identifier: Unlicense
"""The PICO 4's installer: five stages from a new computer to the installed system.

    python -m overpressure install [--json] [--plan] [--restart STAGE]

It strings together what the window's steps and the device and dev commands already do; the
builds and the dev commands run as the steps' command lines (runner.process), so nothing here
is a second copy of a command line. What it adds is what an owner otherwise does by hand: the
serials (found and written to .env), the identity and panel checks before anything is written,
the EDL backup's LUNs, the unlock's fastboot serial and the Wi-Fi profile.

1. computer: the host tools, the unlock files, then the doctor's checks (what fails becomes one
   message with its fixes).
2. build: stock firmware and assets, stage-1 image, userspace archive, system image, dev image,
   Valve's kernel, boot image; each skips when its output is there.
3. connect: developer mode and USB debugging (an action ask), exactly one adb device, its
   identity (device probe), firmware and panel; an unverified one is refused here.
4. prepare (writes): one data-loss consent, the EDL backup (printgpt for the LUNs and the EDL
   serial, device backup, verify-backup), the engineering ABL (dev stage-abl), the unlock
   (device unlock), the maintenance stage 1 (dev flash, dev connect).
5. install: Wi-Fi (a form, optional), dev install, dev installed, the Wi-Fi profile over SSH
   (the password on stdin only), dev check, then "unplug it and put it on".
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import uuid
from typing import TYPE_CHECKING, Callable

from overpressure import doctor, qualcomm, runner, tools
from overpressure.install import Ask, Context, Failed, Field, Stage, Task
from overpressure.process import until

from . import device, devloop, localconfig, unlockfiles
from . import doctor as checks

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from overpressure.doctor import Check
    from overpressure.headset import Headset, Step

ROOT = localconfig.ROOT
Settings = localconfig.Settings
KEY = Settings.key
#: What the installer finds out and writes itself, so the doctor's check of it does not count.
PROVIDED = {
    ".env",
    KEY("adb_serial"),
    KEY("fastboot_serial"),
    KEY("edl_serial"),
    "identity",
    "EDL backup",
}
#: The connection file the installer writes on the headset (one, replaced by the next install).
WIFI_FILE = "/etc/NetworkManager/system-connections/overpressure-wifi.nmconnection"
WIFI_COMMAND = f"install -D -m 600 /dev/stdin {WIFI_FILE} && nmcli connection reload"
#: An SSID's length in bytes, and a WPA passphrase's in characters (64: the hexadecimal key).
SSID_BYTES = 32
PSK_LENGTHS = (8, 63)
PSK_HEX = 64
#: How long the owner has to plug in and accept the prompt, and the headset to reach fastboot.
PLUG_IN_SECONDS = 600
FASTBOOT_SECONDS = 180

NOTHING_CHANGED = "Nothing was changed on your headset."
AGAIN = "Start the installer again to go on where it stopped."
WSL = (
    "Windows' Linux (WSL) is missing. Installing it needs an administrator and a restart, which the "
    "installer cannot do for you: open PowerShell as administrator, run wsl --install -d Ubuntu-24.04, "
    "restart the computer, open Ubuntu once from the Start menu, then start the installer again."
)
PLUG_IN = Ask(
    "plug-in",
    "action",
    "Connect your headset",
    "In PICO OS, turn on developer mode and USB debugging (Settings, General, About: tap the software "
    "version seven times; then Developer, USB debugging). Plug the headset into this computer with a USB-C "
    'data cable, put it on and accept the USB debugging prompt ("Always allow from this computer").',
)
CONSENT = Ask(
    "consent",
    "confirm",
    "Erase your headset?",
    "Preparing erases your headset: unlocking its bootloader wipes PICO OS's apps, games and data, and the "
    "install then replaces PICO OS. What is left of the warranty ends.\n\n"
    "First the installer copies the headset's storage, except your apps and data, to this computer. That "
    "backup is your way back to PICO OS, and every later write to the headset is checked against it: keep "
    "it, and a copy of it somewhere safe.\n\n"
    "This has run on one headset so far. If a step fails, the installer says what state the headset is in "
    "and what to do; keep the cable in until it says so.",
    accept="Erase and continue",
)
FACTORY_RESET = Ask(
    "factory-reset",
    "action",
    "Reset PICO OS",
    "PICO OS needs a factory reset after the unlock. Unplug the cable and hold the power button until the "
    "headset restarts; in PICO OS's recovery, Volume +/- (on the top edge of the right strap) moves and a "
    "short power press selects: select the factory reset, confirm it, then Reboot system now. Set PICO OS "
    "up, turn on developer mode and USB debugging again, plug the headset in and accept the prompt.",
)
WIFI = Ask(
    "wifi",
    "form",
    "Wi-Fi",
    "The headset can join your Wi-Fi network as soon as it starts. The network name and password go to "
    "the headset only; this computer does not keep them. Leave both empty to skip: the headset's settings "
    "join a network later too.",
    (Field("ssid", "Network name", optional=True), Field("password", "Password", secret=True, optional=True)),
)
READY = (
    "Unplug the headset and put it on. Sign in to Steam, pair the controllers and set up your room in the "
    "headset. If you skipped Wi-Fi, join a network in its settings first. The installer can update the "
    "system later, take the headset back to PICO OS, or collect the logs for a bug report."
)


def device_rows(output: str, states: Sequence[str] = ("device", "unauthorized", "offline")) -> dict[str, str]:
    """Return the serial and state of each device that adb or fastboot devices lists in @p states."""
    rows = {}
    for line in output.splitlines():
        words = line.split()
        if len(words) >= 2 and words[1] in states:  # noqa: PLR2004  # a serial and a state
            rows[words[0]] = words[1]
    return rows


def edl_serial(output: str) -> str:
    """Return the serial number edl-ng reports when it connects, or empty."""
    found = re.search(r"Serial Number:\s*(\S+)", output)
    return found.group(1) if found else ""


def edl_luns(output: str) -> list[int]:
    """Return the LUNs edl-ng's printgpt shows, the firmware LUNs first and LUN 0 (userdata's) last."""
    luns = sorted({int(n) for n in re.findall(r"---\s*(?:GPT Header|Partitions)\s+LUN\s+(\d+)\s*---", output)})
    return [n for n in luns if n] + [n for n in luns if not n]


def dump_progress(line: str) -> tuple[float | None, str] | None:
    """Turn edl-ng's "Dumping NAME: P% ..." into a progress text (the share of the whole is unknown)."""
    found = re.match(r"Dumping (\S+?):", line.strip())
    return (None, f"Reading {found.group(1)}") if found else None


def wifi_problem(ssid: str, password: str) -> str:
    """Return what is wrong with a Wi-Fi answer, in plain words; empty when it can be used."""
    if not ssid:
        return "Enter the network name too, or leave both empty to skip Wi-Fi." if password else ""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in ssid + password):  # noqa: PLR2004  # control characters
        return "The network name and password cannot hold control characters."
    if len(ssid.encode()) > SSID_BYTES:
        return f"A network name has at most {SSID_BYTES} bytes."
    hex_key = len(password) == PSK_HEX and all(c in "0123456789abcdefABCDEF" for c in password)
    if password and not hex_key and not PSK_LENGTHS[0] <= len(password) <= PSK_LENGTHS[1]:
        return f"A Wi-Fi password has {PSK_LENGTHS[0]} to {PSK_LENGTHS[1]} characters."
    return ""


def _keyfile_value(value: str) -> str:
    """Return @p value escaped for a key file (GLib's rules: backslashes, a leading space)."""
    value = value.replace("\\", "\\\\")
    return "\\s" + value[1:] if value.startswith(" ") else value


def keyfile(ssid: str, password: str, connection: str) -> bytes:
    """Return a NetworkManager keyfile for @p ssid: WPA-PSK with @p password, an open network without one."""
    lines = [
        "[connection]",
        f"id={_keyfile_value(ssid)}",
        f"uuid={connection}",
        "type=wifi",
        "autoconnect=true",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={_keyfile_value(ssid)}",
        "",
    ]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={_keyfile_value(password)}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines).encode()


def computer_problems(found: Sequence[Check], platform: str, *, wsl: bool) -> str:
    """Return the doctor's failed checks as one message with their fixes; WSL's explained first."""
    lines = ["This computer needs a few things first:"]
    if platform == "win32" and (not wsl or any(c.name == "WSL (as root)" for c in found)):
        lines.append(WSL)
    for check in found:
        lines.append(f"- {check.name} ({check.needed_for}): {check.detail}")
        if check.fix:
            lines.append(f"  How: {check.fix}")
    return "\n".join(lines)


class Plan:
    """The PICO 4's installer plan; tests pass the folders and the host's hooks."""

    def __init__(  # noqa: PLR0913  # the headset; where things live and the host's hooks keyword-only for tests
        self,
        headset: Headset,
        *,
        root: Path = ROOT,
        work: Path = devloop.WORK,
        computer: Callable[[], list[Check]] | None = None,
        edl_count: Callable[[], int] = qualcomm.edl_devices,
        factory_reset: bool = False,
    ) -> None:
        """Install through @p headset's steps; @p factory_reset adds the owner's reset after the unlock."""
        self.headset = headset
        self.root = root
        self.work = work
        self.computer = computer or (lambda: doctor.run(headset))
        self.edl_count = edl_count
        self.factory_reset = factory_reset
        self.steps: dict[str, Step] = {step.key: step for step in headset.steps()}

    # -- helpers ------------------------------------------------------------------------------
    def argv(self, ctx: Context, key: str) -> list[str]:
        """Return the window's step @p key as this host runs it, with the firmware of .env."""
        step = self.steps[key]
        values = {"firmware": ctx.settings(Settings).firmware} if any(p.name == "firmware" for p in step.params) else {}
        return runner.process(self.headset, step, values)

    def command(self, *words: str | Path) -> list[str]:
        """Return a command of the headset's command line (one that is no step of the window)."""
        return [sys.executable, "-m", "overpressure", "--device", self.headset.info.codename, *map(str, words)]

    def step_task(
        self, key: str, title: str, estimate: str, *, done: Callable[[Context], bool] | None = None, where: str = ""
    ) -> Task:
        """Return a task that runs the window's step @p key (it writes to the headset when the step does)."""
        return Task(key, title, lambda ctx: self.run_step(ctx, key), estimate, self.steps[key].writes, done, where)

    def run_step(self, ctx: Context, key: str) -> None:
        """Run the window's step @p key."""
        ctx.run(self.argv(ctx, key))

    def out(self, *parts: str) -> Path:
        """Return a path under the repository's out/."""
        return self.root.joinpath("out", *parts)

    def stage1(self, ctx: Context) -> Path:
        """Return the stage-1 image of the firmware in .env."""
        return self.out(f"pico4-stage1-{ctx.settings(Settings).firmware}.img")

    @staticmethod
    def newer(output: Path, *inputs: Path) -> bool:
        """Tell whether @p output is there and not older than any of @p inputs that are there."""
        if not output.is_file():
            return False
        return all(output.stat().st_mtime >= p.stat().st_mtime for p in inputs if p.exists())

    # -- 1. this computer -------------------------------------------------------------------
    @staticmethod
    def tools_there(_ctx: Context) -> bool:
        """Tell whether this host's pinned tools are fetched."""
        fetched = all(tools.installed(tools.ARCHIVE_TOOLS[n]) for n in tools.wanted() if n in tools.ARCHIVE_TOOLS)
        return fetched and ("ncm-venv" not in tools.wanted() or tools.ncm_python() is not None)

    @staticmethod
    def unlock_files_there(ctx: Context) -> bool:
        """Tell whether the unlock files are where .env names them, with their known hashes."""
        settings = ctx.settings(Settings)
        return all(_sha256(settings.path(f.setting)) == f.sha256 for f in unlockfiles.FILES)

    def check_computer(self, ctx: Context) -> None:
        """Run the doctor's checks; what the installer does not provide itself and fails stops the run."""
        found = [c for c in self.computer() if c.name not in PROVIDED and c.needed_for != checks.ASSETS]
        for check in found:
            ctx.log(f"{check.status}: {check.name} ({check.needed_for}): {check.detail}")
            if check.status == doctor.WARNING:
                ctx.note(f"{check.name}: {check.detail}" + (f" ({check.fix})" if check.fix else ""), "warning")
        problems = doctor.failed(found)
        if problems:
            wsl = any(c.name == "WSL (as root)" and c.status == doctor.OK for c in found) or sys.platform != "win32"
            raise Failed(computer_problems(problems, sys.platform, wsl=wsl), NOTHING_CHANGED + " " + AGAIN)

    # -- 2. the build ---------------------------------------------------------------------------
    def assets_there(self, _ctx: Context) -> bool:
        """Tell whether every extraction's assets are there."""
        version = checks.FIRMWARE_VERSION
        return all((self.root / "assets" / f"{k}-{version}" / "manifest.json").is_file() for k in checks.ASSET_KINDS)

    def build_done(self, key: str) -> Callable[[Context], bool]:
        """Return the done check of the build step @p key: its outputs are there, not older than their inputs."""
        archive = self.out("userspace", "pico4-userspace.tar.zst")
        system = self.out("userspace", "pico4-system.img")
        kernel = self.out("kernel", "Image.gz")
        dev = self.work

        outputs: dict[str, Callable[[Context], bool]] = {
            "assets": self.assets_there,
            "stage1": lambda ctx: self.stage1(ctx).is_file(),
            "userspace": lambda _ctx: archive.is_file(),
            "system": lambda _ctx: self.newer(system, archive),
            "dev-image": lambda ctx: (
                self.newer(dev / "dev-image.json", self.stage1(ctx))
                and all((dev / name).is_file() for name in ("dev-recovery.img", "install-boot.img"))
            ),
            "kernel": lambda _ctx: kernel.is_file() and self.out("kernel", "sm8250-pico4-phoenix.dtb").is_file(),
            "boot-image": lambda _ctx: self.newer(
                self.out("kernel", "valve-boot.img"), kernel, dev / "install-boot.img"
            ),
        }
        return outputs[key]

    # -- 3. the headset over USB ------------------------------------------------------------------
    def find(self, ctx: Context) -> None:
        """Ask the owner to plug in; wait for exactly one adb device and write its serial to .env."""
        ctx.ask(PLUG_IN)
        adb = ctx.settings(Settings).tool("adb")
        told: set[str] = set()

        def one() -> str:
            rows = device_rows(ctx.output([adb, "devices"], 30, check=False))
            ready = [serial for serial, state in rows.items() if state == "device"]
            if len(rows) > 1:
                problem = "More than one Android device is connected: unplug the others."
            elif rows and not ready:
                problem = "Put the headset on and accept the USB debugging prompt."
            elif not ready:
                problem = "No headset on USB yet: check the cable and USB debugging."
            else:
                return ready[0]
            if problem not in told:
                told.add(problem)
                ctx.note(problem, "warning")
            raise RuntimeError(problem)

        try:
            serial = until("the headset on USB", PLUG_IN_SECONDS, one, ctx.clock)
        except RuntimeError as exc:
            raise Failed(str(exc).split(": ", 1)[-1], NOTHING_CHANGED + " " + AGAIN) from exc
        ctx.set_setting(KEY("adb_serial"), serial)

    def identity_known(self, ctx: Context) -> bool:
        """Tell whether the saved identity is this headset's (by its adb serial) and verified."""
        settings = ctx.settings(Settings)
        try:
            return bool(settings.adb_serial) and device.read_identity(settings.path("identity"))["serial"] == (
                settings.adb_serial
            )
        except (OSError, ValueError, KeyError):
            return False

    def identity(self, ctx: Context) -> None:
        """Read the headset's identity (device probe) unless the saved one is this headset; refuse an unverified one."""
        settings = ctx.settings(Settings)
        path = settings.path("identity")
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8")).get("serial")
            except (OSError, ValueError, AttributeError):
                saved = None
            if saved != settings.adb_serial:
                msg = (
                    f"{path} describes another headset, and a record is never overwritten: move it away to "
                    "install this one."
                )
                raise Failed(msg, NOTHING_CHANGED)
        else:
            ctx.run(
                self.command(
                    "device", "probe", "--adb", settings.tool("adb"), "--serial", settings.adb_serial, "--output", path
                )
            )
        try:
            record = device.read_identity(path)
        except (OSError, ValueError, KeyError) as exc:
            raise Failed(str(exc), NOTHING_CHANGED + " PICO OS keeps running as it is.") from exc
        ctx.log(f"identity: {record['fingerprint'].split('/')[3]}, verified")

    def panel(self, ctx: Context) -> None:
        """Refuse a display panel Valve's kernel does not drive, from PICO OS's kernel command line."""
        settings = ctx.settings(Settings)
        argv = [settings.tool("adb"), "-s", settings.adb_serial, "shell", "cat /proc/cmdline"]
        cmdline = ctx.output(argv, 30, check=False)
        try:
            name = device.check_panel(cmdline)
        except ValueError as exc:
            raise Failed(str(exc), NOTHING_CHANGED + " PICO OS keeps running as it is.") from exc
        ctx.log(f"panel {name}, verified")

    # -- 4. preparing the headset ---------------------------------------------------------------
    def consented(self, ctx: Context, later: Sequence[str]) -> bool:
        """Tell whether the consent is needed no more: every write it covers is done."""
        return all(ctx.completed(task) for task in later)

    def enter_edl(self, ctx: Context, settings: localconfig.Settings) -> None:
        """Bring the headset from PICO OS to EDL, unless it is the one EDL device already."""
        qualcomm.enter_edl(
            settings.tool("adb"),
            settings.adb_serial,
            lambda argv, timeout=60: ctx.output(argv, timeout),
            ctx.clock,
            edl_count=self.edl_count,
        )

    @staticmethod
    def backup_there(ctx: Context) -> bool:
        """Tell whether .env names a complete backup of this headset (by sizes: each write checks the hashes)."""
        settings = ctx.settings(Settings)
        folder = settings.path("backup")
        if not settings.edl_serial or checks.backup_check(folder).status != doctor.OK:
            return False
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            return manifest.get("identity") == device.read_identity(settings.path("identity"))
        except (OSError, ValueError, KeyError):
            return False

    def backup(self, ctx: Context) -> None:
        """EDL, its partition table for the LUNs and the serial, then a full backup without userdata, verified."""
        settings = ctx.settings(Settings)
        if settings.path("backup").exists() and not self.backup_there(ctx):
            folder = "backup/stock-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
            ctx.note(
                f"{settings.path('backup')} holds another or an unfinished backup: it stays, this one goes to {folder}."
            )
            ctx.set_setting(KEY("backup"), folder)
            settings = ctx.settings(Settings)
        ctx.progress(None, "Starting EDL mode")
        self.enter_edl(ctx, settings)
        ctx.progress(None, "Reading the partition table")
        table = ctx.output(qualcomm.edl_ng(settings.edl_program(), settings.path("loader"), "printgpt"), 180)
        serial, luns = edl_serial(table), edl_luns(table)
        if not serial or not luns:
            msg = "EDL did not show the headset's serial number and partition table."
            raise Failed(msg)
        if settings.edl_serial and settings.edl_serial != serial:
            msg = f"The headset in EDL mode is not the one {KEY('edl_serial')} in .env names."
            raise Failed(msg)
        ctx.set_setting(KEY("edl_serial"), serial)
        folder = settings.path("backup")
        ctx.run(
            self.command(
                "device",
                "backup",
                "--identity",
                settings.path("identity"),
                "--backup",
                folder,
                "--loader",
                settings.path("loader"),
                "--edl-backend",
                "edl-ng",
                "--edl",
                settings.edl_program(),
                "--edl-luns",
                *map(str, luns),
                "--execute",
                "--confirm-device",
                settings.adb_serial,
            ),
            progress=dump_progress,
        )
        ctx.progress(None, "Checking the backup")
        ctx.run(self.command("device", "verify-backup", "--backup", folder))
        ctx.note(f"Your backup is in {folder}: keep it, and a copy somewhere safe. It is your way back to PICO OS.")

    def unlock(self, ctx: Context) -> None:
        """Into fastboot, its serial to .env, then the unlock (it wipes PICO OS's data)."""
        settings = ctx.settings(Settings)
        fastboot = settings.tool("fastboot")

        def listed() -> dict[str, str]:
            return device_rows(ctx.output([fastboot, "devices"], 30, check=False), ("fastboot",))

        if not listed():
            ctx.output([settings.tool("adb"), "-s", settings.adb_serial, "reboot", "bootloader"], 30)
        ctx.note("The headset restarts into its bootloader. Keep the cable in.")

        def one() -> str:
            rows = listed()
            if len(rows) != 1:
                msg = "expected exactly one device in fastboot"
                raise RuntimeError(msg)
            return next(iter(rows))

        serial = until("the headset in fastboot", FASTBOOT_SECONDS, one, ctx.clock)
        ctx.set_setting(KEY("fastboot_serial"), serial)
        ctx.run(
            self.command(
                "device",
                "unlock",
                "--identity",
                settings.path("identity"),
                "--backup",
                settings.path("backup"),
                "--fastboot",
                fastboot,
                "--serial",
                serial,
                "--execute",
                "--confirm-device",
                settings.adb_serial,
                "--accept-data-loss",
            )
        )

    @staticmethod
    def consent(ctx: Context) -> None:
        """Ask for the one data-loss consent of the run."""
        ctx.ask(CONSENT)

    @staticmethod
    def reset(ctx: Context) -> None:
        """Ask the owner for PICO OS's factory reset."""
        ctx.ask(FACTORY_RESET)

    # -- 5. the install ------------------------------------------------------------------------
    @staticmethod
    def ask_wifi(ctx: Context) -> tuple[str, str]:
        """Ask for the Wi-Fi network until the answer can be used; empty: no Wi-Fi."""
        while True:
            values = ctx.ask(WIFI)
            ssid, password = values["ssid"].strip(), values["password"]
            problem = wifi_problem(ssid, password)
            if not problem:
                return ssid, password
            ctx.note(problem, "warning")

    def wifi(self, ctx: Context) -> None:
        """Ask for Wi-Fi now, so the owner can leave during the install; the answer stays in memory."""
        ctx.memory["wifi"] = self.ask_wifi(ctx)

    def wifi_profile(self, ctx: Context) -> None:
        """Write the Wi-Fi profile to the installed system over SSH: the keyfile on stdin, never in an argv."""
        # After a restart the answer is gone (it is never written down): ask again.
        ssid, password = ctx.memory.get("wifi") or self.ask_wifi(ctx)
        if not ssid:
            ctx.note("No Wi-Fi set up: join a network in the headset's settings.")
            return
        dev = devloop.Dev(ctx.settings(Settings), self.work)
        ctx.run(dev.ssh_argv(WIFI_COMMAND), stdin=keyfile(ssid, password, str(uuid.uuid4())))
        ctx.note("The headset joins your Wi-Fi network when it is in range.")

    # -- the plan ---------------------------------------------------------------------------------
    def stages(self) -> tuple[Stage, ...]:
        """Return the five stages."""
        prepare_writes = ("stage-abl", "unlock", *(("reset",) if self.factory_reset else ()), "flash", "maintenance")
        prepare = [
            Task(
                "consent",
                "Your consent",
                self.consent,
                done=lambda ctx: self.consented(ctx, ("backup", *prepare_writes)),
                where=NOTHING_CHANGED,
            ),
            Task(
                "backup",
                "Back up the headset",
                self.backup,
                "about 30 min",
                done=self.backup_there,
                where="Your headset may be in EDL mode (its lenses stay dark); nothing was written to it. Keep "
                "the cable in and start the installer again: it goes on from there. To return to PICO OS "
                "instead, hold the power button for about 10 s.",
            ),
            self.step_task(
                "stage-abl",
                "Engineering bootloader",
                "about 5 min",
                where="Your headset may be in EDL mode with the engineering bootloader not or only partly "
                "written. Keep the cable in and start the installer again; if it fails again, get help: your "
                "backup holds the stock bootloader.",
            ),
            Task(
                "unlock",
                "Unlock the bootloader",
                self.unlock,
                "about 2 min",
                writes=True,
                where="Your headset is in its bootloader (fastboot) or in PICO OS. Keep the cable in and start "
                "the installer again: the unlock can be repeated.",
            ),
        ]
        if self.factory_reset:
            prepare.append(
                Task("reset", "Reset PICO OS", self.reset, "about 10 min", where=NOTHING_CHANGED + " " + AGAIN)
            )
        maintenance_where = (
            "Your headset is in its bootloader (fastboot) or starting the maintenance system. Keep the cable "
            "in and start the installer again."
        )
        prepare += [
            self.step_task("flash", "Maintenance system", "about 3 min", where=maintenance_where),
            Task(
                "maintenance",
                "Connect to the maintenance system",
                lambda ctx: self.run_step(ctx, "connect"),
                "about 1 min",
                where=maintenance_where,
            ),
        ]
        installed_where = "The system is installed. Keep the cable in and start the installer again."
        built_where = NOTHING_CHANGED + " " + AGAIN
        build = [
            self.step_task(
                "assets", "Stock firmware and assets", "about 40 min", where=built_where, done=self.build_done("assets")
            ),
            self.step_task(
                "stage1", "Maintenance system image", "about 2 min", where=built_where, done=self.build_done("stage1")
            ),
            self.step_task(
                "userspace",
                "SteamOS-compatible system",
                "1 to 3 h",
                where=built_where,
                done=self.build_done("userspace"),
            ),
            self.step_task("system", "System image", "about 10 min", where=built_where, done=self.build_done("system")),
            self.step_task(
                "dev-image", "Boot images", "about 3 min", where=built_where, done=self.build_done("dev-image")
            ),
            self.step_task("kernel", "Valve's kernel", "about 1 h", where=built_where, done=self.build_done("kernel")),
            self.step_task(
                "boot-image",
                "Boot image with Valve's kernel",
                "about 1 min",
                where=built_where,
                done=self.build_done("boot-image"),
            ),
        ]
        return (
            Stage(
                "computer",
                "Check this computer",
                "What the install needs on this computer: the host tools and the unlock files are fetched, "
                "anything else is explained. The headset is not needed.",
                (
                    self.step_task("tools", "Host tools", "about 1 min", done=self.tools_there),
                    self.step_task("unlock-files", "Unlock files", "about 2 min", done=self.unlock_files_there),
                    Task("doctor", "Everything else this computer needs", self.check_computer, "under a minute"),
                ),
            ),
            Stage(
                "build",
                "Build the system",
                "The SteamOS-compatible system for your headset, built on this computer from public downloads "
                "(about 15 GB). It takes a few hours; the headset is not needed.",
                tuple(build),
            ),
            Stage(
                "connect",
                "Connect your headset",
                "Turn on developer mode and USB debugging, then plug the headset in. The installer finds it and "
                "checks that the install has run on a headset like it. Nothing is written to it.",
                (
                    Task("find", "Find the headset", self.find, "a few seconds", where=NOTHING_CHANGED),
                    Task(
                        "identity",
                        "Check the headset and its firmware",
                        self.identity,
                        "a few seconds",
                        done=self.identity_known,
                        where=NOTHING_CHANGED,
                    ),
                    Task("panel", "Check the display panel", self.panel, "a few seconds", where=NOTHING_CHANGED),
                ),
            ),
            Stage(
                "prepare",
                "Prepare your headset",
                "Erases the headset: a backup of its storage to this computer first, your way back to PICO OS; "
                "then the engineering bootloader, the unlock and the maintenance system.",
                tuple(prepare),
            ),
            Stage(
                "install",
                "Install",
                "Wi-Fi if you like, then the system goes onto the headset and is checked. About 15 minutes; keep "
                "the cable in.",
                (
                    Task("wifi", "Wi-Fi (optional)", self.wifi, "a minute"),
                    self.step_task(
                        "install",
                        "Install the system",
                        "about 10 min",
                        where="The headset is in the maintenance system; PICO OS's system may be gone already. "
                        "Keep the cable in and start the installer again: the install can be repeated.",
                    ),
                    self.step_task("installed", "Start the installed system", "about 2 min", where=installed_where),
                    Task(
                        "wifi-profile",
                        "Wi-Fi on the headset",
                        self.wifi_profile,
                        "a few seconds",
                        writes=True,
                        where=installed_where,
                    ),
                    self.step_task("check", "Acceptance checks", "about 3 min", where=installed_where),
                    Task("ready", "Unplug it and put it on", lambda ctx: ctx.note(READY), ""),
                ),
            ),
        )


def _sha256(path: Path) -> str:
    """Return a small file's SHA-256, empty when it is not there."""
    import hashlib  # noqa: PLC0415  # only here

    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
