# SPDX-License-Identifier: Unlicense
"""What this host has and lacks for a headset's workflow: python -m overpressure doctor.

Every check names what it is needed for (the window, building an image, the userspace build,
working on the headset, ...), what was found, and how to fix it when it is missing. The checks
only look: they run version queries and `command -v`, read files and settings, and install
nothing. The generic ones cover the host (Python, Tk, OpenSSH, Linux as root); a headset adds
its own through Headset.dependencies(), built from the checks here that every Qualcomm Android
headset shares (its DeviceSettings, the EDL backup and tools, adb and fastboot, the Windows USB
link, an arm64 build host) and its own.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from . import REPO, linux, windows_ncm

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from .headset import Headset
    from .localconfig import DeviceSettings

OK, MISSING, OPTIONAL, WARNING = "ok", "missing", "optional", "warning"
MARK = {OK: "ok", MISSING: "MISSING", OPTIONAL: "optional", WARNING: "warning"}
#: What the checks of a headset's settings and tools are needed for, unless the headset names it.
HEADSET = "working on the headset"
EDL = "EDL"
#: The fixes' common beginnings: a package on the Linux side, the pinned host tools.
APT = "in WSL/Linux as root: apt install "
FETCH = "python -m overpressure tools"


@dataclass(frozen=True)
class Check:
    """One thing the workflow needs, and whether this host has it."""

    name: str
    needed_for: str  #: the part of the workflow that needs it
    status: str  #: ok, missing (needed), optional (missing, not needed), warning
    detail: str = ""  #: what was found, or why it is missing
    fix: str = ""  #: how to get it, when it is not ok


def run_tool(argv: Sequence[str], timeout: float = 30) -> tuple[int, str]:
    """Return a program's exit status and combined output; (127, reason) when it cannot start."""
    try:
        process = subprocess.run(  # noqa: S603  # the checks' fixed version queries, no shell
            [str(a) for a in argv],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=REPO,
        )
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except OSError as exc:
        return 127, f"{argv[0]}: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: no answer within {timeout:.0f} s"
    return process.returncode, process.stdout.decode("utf-8", "replace").strip()


@dataclass
class Host:
    """What the checks may ask of this host; tests pass fakes."""

    platform: str = sys.platform
    run: Callable[[Sequence[str], float], tuple[int, str]] = run_tool
    which: Callable[[str], str | None] = shutil.which
    linux_cache: dict[str, dict[str, str]] = field(default_factory=dict)
    backend: str = ""  #: the Linux backend (overpressure/linux.py); empty: this host's

    def __post_init__(self) -> None:
        """Pick this host's Linux backend unless one is given."""
        if not self.backend:
            self.backend = linux.backend(self.platform, self.which)

    def container_ready(self) -> bool:
        """Tell whether the build container's image is built (the docker backend)."""
        return self.run([linux.engine(self.which), "image", "inspect", linux.image()], 30)[0] == 0

    def linux(self, script: str, timeout: float = 90) -> dict[str, str]:
        """Return the KEY=VALUE lines that @p script prints where the Linux-root steps run.

        That is WSL as root, this Linux, or the build container; {} when there is no such Linux.
        Cached per script.
        """
        if script not in self.linux_cache:
            if self.backend == "docker":
                if not self.container_ready():
                    self.linux_cache[script] = {}
                    return {}
                argv = linux.run_argv(["bash", "-c", script], tool=linux.engine(self.which))
            elif self.platform == "win32":
                if self.which("wsl") is None:
                    self.linux_cache[script] = {}
                    return {}
                # --exec: the script goes to bash as it is, not through WSL's default shell.
                argv = ["wsl", "-u", "root", "--exec", "bash", "-c", script]
            else:
                argv = ["bash", "-c", script]
            code, out = self.run(argv, timeout)
            values = {}
            for line in out.splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip().isidentifier():
                    values[key.strip()] = value.strip()
            self.linux_cache[script] = values if code == 0 else {}
        return self.linux_cache[script]


def version_check(host: Host, name: str, argv: Sequence[str], needed_for: str, fix: str) -> Check:
    """Check a program that answers a version query: its first output line, or how to get it."""
    code, out = host.run(argv, 30)
    if code == 0:
        return Check(name, needed_for, OK, (out.splitlines() or [""])[0][:120])
    return Check(name, needed_for, MISSING, (out.splitlines() or [""])[-1][:120], fix)


def linux_commands(host: Host, needed_for: str, commands: dict[str, str], fix: str) -> list[Check]:
    """Programs the Linux side needs: each found with `command -v`, or the fix (an apt line)."""
    script = "; ".join(
        f"echo {k}=$(command -v {c} || true)"
        for k, c in ((name.replace("-", "_").replace(".", "_"), name) for name in commands)
    )
    found = host.linux(script)
    checks = []
    for name, purpose in commands.items():
        path = found.get(name.replace("-", "_").replace(".", "_"), "")
        checks.append(
            Check(name, needed_for, OK if path else MISSING, path or f"not in Linux ({purpose})", "" if path else fix)
        )
    return checks


def env_check(root: Path = REPO, needed_for: str = HEADSET) -> Check:
    """Check that .env, which names the headset's serials and private files, is there."""
    env = root / ".env"
    return Check(
        ".env",
        needed_for,
        OK if env.exists() else MISSING,
        str(env) if env.exists() else "no .env",
        "" if env.exists() else "copy .env.example to .env and fill in the headset's serials and files",
    )


def serial_checks(settings: DeviceSettings, needed_for: str = HEADSET, edl_needed_for: str = EDL) -> list[Check]:
    """Check that the headset's adb, fastboot and EDL serials are set."""
    checks = []
    for name, what in (
        ("adb_serial", "adb devices"),
        ("fastboot_serial", "fastboot devices"),
        ("edl_serial", "edl-ng's Serial Number"),
    ):
        value = getattr(settings, name)
        checks.append(
            Check(
                settings.key(name),
                needed_for if name != "edl_serial" else edl_needed_for,
                OK if value else MISSING,
                "set" if value else "empty",
                "" if value else f"set it in .env ({what})",
            )
        )
    return checks


def backup_check(folder: Path, needed_for: str = HEADSET, fix: str = "python -m overpressure device backup") -> Check:
    """Check the EDL backup: its manifest, and every file in it present at its recorded size.

    The images are not hashed here (gigabytes); every write checks them against the manifest.
    """
    manifest = folder / "manifest.json"
    try:
        files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError) as exc:
        return Check("EDL backup", needed_for, MISSING, f"{manifest}: {exc}"[:120], fix)
    short = [
        name
        for name, entry in files.items()
        if not (folder / name).is_file() or (folder / name).stat().st_size != entry.get("size")
    ]
    if short:
        return Check(
            "EDL backup",
            needed_for,
            MISSING,
            f"{len(short)} of {len(files)} files missing or of another size: " + ", ".join(short[:3]),
            fix,
        )
    return Check("EDL backup", needed_for, OK, f"{len(files)} files (sizes; every write checks their hashes)")


def loader_check(
    settings: DeviceSettings, needed_for: str = EDL, fix: str = "the headset's signed Firehose loader"
) -> Check:
    """Check that the Firehose loader the loader setting names is there."""
    loader = settings.path("loader")
    return Check(
        "Firehose loader",
        needed_for,
        OK if loader.is_file() else MISSING,
        str(loader) if loader.is_file() else f"no {loader}",
        "" if loader.is_file() else fix,
    )


def edl_tool_check(host: Host, settings: DeviceSettings, needed_for: str = EDL) -> Check:
    """Check that edl-ng is there: the edl setting's, the fetched one or one on PATH."""
    edl = settings.edl_program()
    found = edl.is_file() or (not edl.is_absolute() and host.which(str(edl)))
    return Check(
        "edl-ng", needed_for, OK if found else MISSING, str(edl) if found else f"no {edl}", "" if found else FETCH
    )


def tool_checks(host: Host, settings: DeviceSettings, needed_for: str = HEADSET) -> list[Check]:
    """Check adb and fastboot by their version queries."""
    fix = FETCH + f" (or Android platform-tools of your own, in {settings.key('platform_tools')})"
    return [
        version_check(host, "adb", [settings.tool("adb"), "version"], needed_for, fix),
        version_check(host, "fastboot", [settings.tool("fastboot"), "--version"], needed_for, fix),
    ]


def ncm_checks(host: Host, settings: DeviceSettings) -> list[Check]:
    """Windows only: the Python with pyusb and libusb-package, and UsbDk behind it."""
    if host.platform != "win32":
        return []
    needed_for = "the USB network link on Windows"
    fix = FETCH + " (the NCM venv), and UsbDk: winget install daynix.UsbDk (admin)"
    python = settings.ncm()
    if python is None:
        return [Check("NCM Python", needed_for, MISSING, f"no NCM venv and no {settings.key('ncm_python')}", fix)]
    code, out = host.run(windows_ncm.command(python), 30)
    try:
        state = json.loads(out.splitlines()[-1]) if code == 0 and out else {}
    except ValueError:
        state = {}
    if state.get("status") == "ready":
        versions = ", ".join(f"{k} {v}" for k, v in (state.get("dependencies") or {}).items())
        return [Check("NCM Python and UsbDk", needed_for, OK, versions)]
    return [Check("NCM Python and UsbDk", needed_for, MISSING, (out.splitlines() or ["no answer"])[-1][:120], fix)]


def arm64_build_checks(host: Host, needed_for: str) -> list[Check]:
    """Check what building arm64 code in Linux needs on an x86-64 host; nothing on another one.

    The aarch64 binfmt handler with the F flag (a chroot runs arm64 programs through it) and a
    native clang and lld (cross compiling instead of compiling under QEMU).
    """
    # The aarch64 handler by its ELF magic: qemu-aarch64 (Debian) or aarch64 (Docker Desktop).
    state = host.linux(
        "echo arch=$(uname -m); echo binfmt=$(for f in /proc/sys/fs/binfmt_misc/*; do "
        "grep -qs '^magic 7f454c460201010000000000000000000200b700' $f && "
        "sed -n 's/^flags: //p' $f; done | head -n 1); "
        "for v in 21 20 19; do [ -x /usr/lib/llvm-$v/bin/clang ] && [ -x /usr/lib/llvm-$v/bin/ld.lld ] "
        "&& { echo llvm=/usr/lib/llvm-$v; break; }; done; true"
    )
    checks = []
    if state.get("arch") == "x86_64":
        flags = state.get("binfmt", "")
        checks.append(
            Check(
                "aarch64 binfmt (F flag)",
                needed_for,
                OK if "F" in flags else MISSING,
                f"flags {flags}" if flags else "no qemu-aarch64 handler",
                "" if "F" in flags else APT + "qemu-user-static binfmt-support",
            )
        )
        llvm = state.get("llvm", "")
        checks.append(
            Check(
                "clang and lld",
                needed_for,
                OK if llvm else WARNING,
                llvm or "none: packages compile under QEMU, about ten times slower",
                "" if llvm else APT + "clang-20 lld-20",
            )
        )
    return checks


def host_checks(host: Host) -> list[Check]:
    """Return the checks every headset's workflow shares."""
    checks = []
    version = sys.version_info
    checks.append(
        Check(
            "Python",
            "everything",
            OK if version >= (3, 9) else MISSING,
            f"{version.major}.{version.minor}.{version.micro}",
            "Python 3.9 or newer",
        )
    )
    try:
        import tkinter as tk  # noqa: PLC0415  # optional: whether this Python has Tk is the check

        checks.append(Check("Tk", "the window", OK, f"Tk {tk.TkVersion}"))
    except ImportError:
        checks.append(
            Check(
                "Tk",
                "the window",
                OPTIONAL,
                "this Python has no tkinter",
                "a Python with Tk (python.org's installers have it; Debian/Ubuntu: apt install "
                "python3-tk); the command line works without it",
            )
        )
    for tool in ("ssh", "ssh-keygen"):
        path = host.which(tool)
        checks.append(
            Check(
                tool,
                "working on the headset",
                OK if path else MISSING,
                path or "not on PATH",
                ""
                if path
                else 'OpenSSH (Windows 10/11: optional feature "OpenSSH Client"; Linux: apt install openssh-client)',
            )
        )
    if host.backend == "docker":
        checks.extend(container_checks(host))
    elif host.platform == "win32":
        values = host.linux("echo user=$(id -u); echo distro=$(. /etc/os-release && echo $PRETTY_NAME)")
        if values.get("user") == "0":
            checks.append(Check("WSL (as root)", "Linux-root steps", OK, values.get("distro", "")))
        else:
            checks.append(
                Check(
                    "WSL (as root)",
                    "Linux-root steps",
                    MISSING,
                    "no WSL distribution answers as root" if host.which("wsl") else "wsl not found",
                    "wsl --install -d Ubuntu-24.04 (then reboot and open it once)",
                )
            )
    elif host.platform.startswith("linux"):
        sudo = host.which("sudo")
        checks.append(
            Check(
                "sudo",
                "Linux-root steps",
                OK if sudo else WARNING,
                sudo or "not on PATH",
                "" if sudo else "run the Linux-root steps as root, or install sudo",
            )
        )
    return checks


def container_checks(host: Host) -> list[Check]:
    """Check the docker backend: the engine answers, and the build container's image is built."""
    needed_for = "Linux-root steps (build container)"
    tool = linux.engine(host.which)
    code, out = host.run([tool, "version", "--format", "{{.Server.Version}}"], 30)
    if code != 0:
        return [
            Check(
                tool,
                needed_for,
                MISSING,
                (out.splitlines() or ["no answer"])[-1][:120],
                "Docker Desktop (started) or Docker/Podman on Linux; OVERPRESSURE_LINUX=wsl or sudo "
                "uses WSL or this Linux instead",
            )
        ]
    checks = [Check(tool, needed_for, OK, f"engine {out.splitlines()[-1]}")]
    ready = host.container_ready()
    checks.append(
        Check(
            "build container image",
            needed_for,
            OK if ready else MISSING,
            linux.image(),
            "" if ready else "python -m overpressure container build",
        )
    )
    return checks


def run(headset: Headset | None = None, host: Host | None = None) -> list[Check]:
    """Run every check: the host's, then the headset's."""
    host = host or Host()
    checks = host_checks(host)
    if headset is not None:
        for probe in headset.dependencies():
            try:
                checks.extend(probe(host))
            except Exception as exc:  # noqa: BLE001, PERF203  # a broken probe must not hide the others
                checks.append(Check(getattr(probe, "__name__", "check"), "?", WARNING, f"the check failed: {exc}"))
    return checks


def failed(checks: Iterable[Check]) -> list[Check]:
    """Return the checks of what is needed and missing."""
    return [c for c in checks if c.status == MISSING]


def report(checks: Sequence[Check]) -> str:
    """Return the checks grouped by what they are needed for, the fixes below each missing one."""
    lines, groups = [], {}
    for check in checks:
        groups.setdefault(check.needed_for, []).append(check)
    width = max((len(c.name) for c in checks), default=10)
    for needed_for, group in groups.items():
        lines.append(f"{needed_for}:")
        for c in group:
            lines.append(f"  {MARK[c.status]:<8} {c.name:<{width}}  {c.detail}")
            if c.fix and c.status != OK:
                lines.append(f"  {'':<8} {'':<{width}}  -> {c.fix}")
    missing = failed(checks)
    lines.append("")
    lines.append(
        f"{len(missing)} missing"
        + (": " + ", ".join(c.name for c in missing) if missing else ": everything needed is there")
    )
    return "\n".join(lines)
