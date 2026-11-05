"""What this host has and lacks for a headset's workflow: python -m overpressure doctor.

Every check names what it is needed for (the window, building an image, the userspace build,
working on the headset, ...), what was found, and how to fix it when it is missing. The checks
only look: they run version queries and `command -v`, read files and settings, and install
nothing. The generic ones cover the host (Python, Tk, OpenSSH, Linux as root); a headset adds
its own through Headset.dependencies().
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import REPO, linux

OK, MISSING, OPTIONAL, WARNING = "ok", "missing", "optional", "warning"
MARK = {OK: "ok", MISSING: "MISSING", OPTIONAL: "optional", WARNING: "warning"}


@dataclass(frozen=True)
class Check:
    name: str
    needed_for: str            #: the part of the workflow that needs it
    status: str                #: ok, missing (needed), optional (missing, not needed), warning
    detail: str = ""           #: what was found, or why it is missing
    fix: str = ""              #: how to get it, when it is not ok


def run_tool(argv: Sequence[str], timeout: float = 30) -> Tuple[int, str]:
    """A program's exit status and combined output; (127, reason) when it cannot start."""
    try:
        process = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, timeout=timeout, cwd=REPO)
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
    run: Callable[[Sequence[str], float], Tuple[int, str]] = run_tool
    which: Callable[[str], Optional[str]] = shutil.which
    linux_cache: Dict[str, Dict[str, str]] = field(default_factory=dict)
    backend: str = ""  #: the Linux backend (overpressure/linux.py); empty: this host's

    def __post_init__(self):
        if not self.backend:
            self.backend = linux.backend(self.platform, self.which)

    def container_ready(self) -> bool:
        """The build container's image is built (the docker backend)."""
        return self.run([linux.engine(self.which), "image", "inspect", linux.image()], 30)[0] == 0

    def linux(self, script: str, timeout: float = 90) -> Dict[str, str]:
        """KEY=VALUE lines that @p script prints where the Linux-root steps run (WSL as root, this
        Linux, or the build container); {} when there is no such Linux. Cached per script."""
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
    """A program that answers a version query: its first output line, or how to get it."""
    code, out = host.run(argv, 30)
    if code == 0:
        return Check(name, needed_for, OK, (out.splitlines() or [""])[0][:120])
    return Check(name, needed_for, MISSING, (out.splitlines() or [""])[-1][:120], fix)


def linux_commands(host: Host, needed_for: str, commands: Dict[str, str], fix: str) -> List[Check]:
    """Programs the Linux side needs: each found with `command -v`, or the fix (an apt line)."""
    script = "; ".join(f'echo {k}=$(command -v {c} || true)' for k, c in
                       ((name.replace("-", "_").replace(".", "_"), name) for name in commands))
    found = host.linux(script)
    checks = []
    for name, purpose in commands.items():
        path = found.get(name.replace("-", "_").replace(".", "_"), "")
        checks.append(Check(name, needed_for, OK if path else MISSING, path or f"not in Linux ({purpose})",
                            "" if path else fix))
    return checks


def host_checks(host: Host) -> List[Check]:
    """The checks every headset's workflow shares."""
    checks = []
    version = sys.version_info
    checks.append(Check("Python", "everything", OK if version >= (3, 9) else MISSING,
                        f"{version.major}.{version.minor}.{version.micro}", "Python 3.9 or newer"))
    try:
        import tkinter  # noqa: F401  (only whether it is there)
        checks.append(Check("Tk", "the window", OK, f"Tk {tkinter.TkVersion}"))
    except ImportError:
        checks.append(Check("Tk", "the window", OPTIONAL, "this Python has no tkinter",
                            "a Python with Tk (python.org's installers have it; Debian/Ubuntu: apt install "
                            "python3-tk); the command line works without it"))
    for tool in ("ssh", "ssh-keygen"):
        path = host.which(tool)
        checks.append(Check(tool, "working on the headset", OK if path else MISSING, path or "not on PATH",
                            "" if path else "OpenSSH (Windows 10/11: optional feature \"OpenSSH Client\"; "
                                            "Linux: apt install openssh-client)"))
    if host.backend == "docker":
        checks.extend(container_checks(host))
    elif host.platform == "win32":
        values = host.linux("echo user=$(id -u); echo distro=$(. /etc/os-release && echo $PRETTY_NAME)")
        if values.get("user") == "0":
            checks.append(Check("WSL (as root)", "Linux-root steps", OK, values.get("distro", "")))
        else:
            checks.append(Check("WSL (as root)", "Linux-root steps", MISSING,
                                "no WSL distribution answers as root" if host.which("wsl") else "wsl not found",
                                "wsl --install -d Ubuntu-24.04 (then reboot and open it once)"))
    elif host.platform.startswith("linux"):
        sudo = host.which("sudo")
        checks.append(Check("sudo", "Linux-root steps", OK if sudo else WARNING, sudo or "not on PATH",
                            "" if sudo else "run the Linux-root steps as root, or install sudo"))
    return checks


def container_checks(host: Host) -> List[Check]:
    """The docker backend: the engine answers, and the build container's image is built."""
    needed_for = "Linux-root steps (build container)"
    tool = linux.engine(host.which)
    code, out = host.run([tool, "version", "--format", "{{.Server.Version}}"], 30)
    if code != 0:
        return [Check(tool, needed_for, MISSING, (out.splitlines() or ["no answer"])[-1][:120],
                      "Docker Desktop (started) or Docker/Podman on Linux; OVERPRESSURE_LINUX=wsl or sudo "
                      "uses WSL or this Linux instead")]
    checks = [Check(tool, needed_for, OK, f"engine {out.splitlines()[-1]}")]
    ready = host.container_ready()
    checks.append(Check("build container image", needed_for, OK if ready else MISSING, linux.image(),
                        "" if ready else "python -m overpressure container build"))
    return checks


def run(headset=None, host: Optional[Host] = None) -> List[Check]:
    """Every check: the host's, then the headset's."""
    host = host or Host()
    checks = host_checks(host)
    if headset is not None:
        for probe in headset.dependencies():
            try:
                checks.extend(probe(host))
            except Exception as exc:  # a broken probe must not hide the others
                checks.append(Check(getattr(probe, "__name__", "check"), "?", WARNING, f"the check failed: {exc}"))
    return checks


def failed(checks: Iterable[Check]) -> List[Check]:
    return [c for c in checks if c.status == MISSING]


def report(checks: Sequence[Check]) -> str:
    """The checks grouped by what they are needed for, the fixes below each missing one."""
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
    lines.append(f"{len(missing)} missing" + (": " + ", ".join(c.name for c in missing) if missing else
                                              ": everything needed is there"))
    return "\n".join(lines)
