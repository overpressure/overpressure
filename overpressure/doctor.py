# SPDX-License-Identifier: Unlicense
"""What this host has and lacks for a headset's workflow: python -m overpressure doctor.

Every check names what it is needed for (the window, the builds, working on the headset, ...),
what was found, and how to fix it when it is missing. The checks only look: they run version
queries, read files and settings, and install, build or register nothing. The generic ones cover
the host (Python, Tk and CustomTkinter for the window, OpenSSH) and the build container, where
every Linux-root step runs (its engine installed and running, its image, the aarch64 handler; the
image holds every Linux tool of the builds by construction); a headset adds its own through
Headset.dependencies(), built from the checks here that every Qualcomm Android headset shares (its
DeviceSettings, the EDL backup and tools, adb and fastboot, the Windows USB link) and its own.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from . import REPO, linux, windows_ncm

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .headset import Headset
    from .localconfig import DeviceSettings

OK, MISSING, OPTIONAL, WARNING = "ok", "missing", "optional", "warning"
MARK = {OK: "ok", MISSING: "MISSING", OPTIONAL: "optional", WARNING: "warning"}
#: What the checks of a headset's settings and tools are needed for, unless the headset names it.
HEADSET = "working on the headset"
EDL = "EDL"
#: The fixes' common beginning: the pinned host tools.
FETCH = "python -m overpressure tools"
#: What the build container's checks are needed for.
BUILDS = "the builds (build container)"


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


def has_module(name: str) -> bool:
    """Tell whether this Python can import the top-level module @p name, without importing it."""
    return importlib.util.find_spec(name) is not None


@dataclass
class Host:
    """What the checks may ask of this host; tests pass fakes."""

    platform: str = sys.platform
    run: Callable[[Sequence[str], float], tuple[int, str]] = run_tool
    which: Callable[[str], str | None] = shutil.which
    linux_cache: dict[str, dict[str, str]] = field(default_factory=dict)
    has_module: Callable[[str], bool] = has_module

    @property
    def engine(self) -> str:
        """Return the container engine's program (overpressure/linux.py)."""
        return linux.engine(self.which)

    def container_ready(self) -> bool:
        """Tell whether the build container's image is built."""
        return self.run([self.engine, "image", "inspect", linux.image()], 30)[0] == 0

    def linux(self, script: str, timeout: float = 90) -> dict[str, str]:
        """Return the KEY=VALUE lines that @p script prints in the build container (a probe: unnamed).

        {} when the image is not built (the checks name it); cached per script.
        """
        if script not in self.linux_cache:
            if not self.container_ready():
                self.linux_cache[script] = {}
                return {}
            code, out = self.run(linux.run_argv(["bash", "-c", script], tool=self.engine), timeout)
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
    fix = PACKAGE_FIX + " (pyusb, libusb-package), and UsbDk: winget install daynix.UsbDk (admin)"
    python = settings.ncm()
    if python is None:
        detail = f"this Python lacks pyusb or libusb-package, and no {settings.key('ncm_python')}"
        return [Check("NCM Python", needed_for, MISSING, detail, fix)]
    code, out = host.run(windows_ncm.command(python), 30)
    try:
        state = json.loads(out.splitlines()[-1]) if code == 0 and out else {}
    except ValueError:
        state = {}
    if state.get("status") == "ready":
        versions = ", ".join(f"{k} {v}" for k, v in (state.get("dependencies") or {}).items())
        return [Check("NCM Python and UsbDk", needed_for, OK, versions)]
    return [Check("NCM Python and UsbDk", needed_for, MISSING, (out.splitlines() or ["no answer"])[-1][:120], fix)]


def window_check() -> Check:
    """Check CustomTkinter, which the window needs beside Tk (the gui extra)."""
    try:
        version = importlib.metadata.version("customtkinter")
    except importlib.metadata.PackageNotFoundError:
        return Check(
            "CustomTkinter",
            "the window",
            OPTIONAL,
            "not installed",
            "the start scripts (Install.cmd, Install.command, install.sh) bring it; in a development environment, "
            "uv sync --extra gui; the command line works without it",
        )
    return Check("CustomTkinter", "the window", OK, version)


#: The pinned packages (pyproject.toml) and what works without them.
PACKAGE_FIX = (
    "the start scripts (Install.cmd, Install.command, install.sh) bring it; in a development environment, uv sync"
)


def package_checks(host: Host) -> list[Check]:
    """Check the pinned Python packages: brotli for the stock OTA, pyusb with libusb for EDL devices.

    Without brotli the brotli command decompresses (missing only when neither is there); without
    pyusb this system's own listing counts EDL devices.
    """
    checks = []
    if host.has_module("brotli"):
        checks.append(Check("brotli (Python)", "the stock firmware", OK, "the package decompresses the OTA"))
    else:
        command = host.which("brotli")
        checks.append(
            Check(
                "brotli (Python)",
                "the stock firmware",
                OPTIONAL if command else MISSING,
                f"not installed; the brotli command at {command} decompresses instead"
                if command
                else "not installed, and no brotli command",
                PACKAGE_FIX,
            )
        )
    usb = host.has_module("usb") and host.has_module("libusb_package")
    checks.append(
        Check(
            "pyusb and libusb-package",
            EDL,
            OK if usb else OPTIONAL,
            "count EDL devices" if usb else "not installed; EDL devices are counted by this system's own listing",
            "" if usb else PACKAGE_FIX,
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
    checks.append(window_check())
    checks.extend(package_checks(host))
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
    checks.extend(container_checks(host))
    return checks


#: The one fix for the builds' engine, whichever the owner picks and however it runs its containers.
ENGINE = "Docker or Podman"
ENGINE_FIX = "install Docker (docs.docker.com/get-docker) or Podman (podman.io) and have it running"
BINFMT_FIX = "none needed: each build registers it; if a build fails on it: python -m overpressure container binfmt"
#: The aarch64 handler by its ELF magic, whatever it is named, with its flags; the engine's architecture.
BINFMT_PROBE = (
    "echo arch=$(uname -m); for f in /proc/sys/fs/binfmt_misc/*; do grep -qsx enabled $f && "
    "grep -qsx 'magic 7f454c460201010000000000000000000200b700' $f && "
    "echo binfmt=${f##*/} $(sed -n 's/^flags: //p' $f) && break; done; true"
)


def container_checks(host: Host) -> list[Check]:
    """Check the build container: Docker or Podman answers, its image is built, the aarch64 handler.

    Only whether an engine answers counts: how it runs its containers (a VM, WSL, a service,
    rootless) is the owner's choice and none of the builds' business.
    """
    tool = host.engine
    if host.which(tool) is None:
        return [Check(ENGINE, BUILDS, MISSING, "neither docker nor podman is on PATH", ENGINE_FIX)]
    code, out = host.run([tool, "info"], 30)
    if code != 0:
        last = (out.splitlines() or ["no answer"])[-1][:120]
        return [Check(ENGINE, BUILDS, MISSING, f"{Path(tool).name} does not answer: {last}", ENGINE_FIX)]
    checks = [Check(ENGINE, BUILDS, OK, Path(tool).name)]
    if not host.container_ready():
        fix = "python -m overpressure container build (the installer builds it itself)"
        checks.append(Check("build container image", BUILDS, MISSING, f"{linux.image()} not built", fix))
        return checks
    checks.append(Check("build container image", BUILDS, OK, linux.image()))
    state = host.linux(BINFMT_PROBE)
    arch, found = state.get("arch", ""), state.get("binfmt", "")
    name, _, flags = found.partition(" ")
    if arch and arch != "x86_64":
        checks.append(Check("aarch64 handler", BUILDS, OK, f"the engine is {arch}: it runs arm64 programs itself"))
    elif "F" in flags:
        checks.append(Check("aarch64 handler", BUILDS, OK, f"{name}, flags {flags}"))
    else:
        detail = f"{name} without the F flag" if name else "none yet"
        checks.append(Check("aarch64 handler", BUILDS, OPTIONAL, detail + ": the build registers its own", BINFMT_FIX))
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
