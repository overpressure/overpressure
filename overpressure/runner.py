"""Running a headset's steps: the process each one is, on this host.

A command step runs this package's command line for the headset; a script step runs a program
from the headset's folder. Steps that need Linux as root (the userspace build mounts and
chroots) go through sudo on Linux and through WSL as root on Windows, from the repository root,
so relative paths in their parameters mean the same everywhere.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Callable, Dict, List, Optional

from . import REPO
from .headset import Headset, Step


def wsl_path(path: Path) -> str:
    """C:\\a\\b -> /mnt/c/a/b, as WSL mounts Windows drives."""
    p = PureWindowsPath(path)
    return "/mnt/" + p.drive.rstrip(":").lower() + "/" + "/".join(p.parts[1:])


def process(headset: Headset, step: Step, values: Dict[str, str], platform: str = sys.platform,
            python: str = sys.executable, is_root: Optional[bool] = None) -> List[str]:
    """The argv that runs @p step with @p values; its working directory is the repository."""
    words = step.command(values)
    windows = platform == "win32"
    if step.script:
        program = headset.root / words[0]
        if step.linux_root and windows:
            return ["wsl", "-u", "root", "--cd", str(REPO), "--", "bash", wsl_path(program), *words[1:]]
        argv = ["bash", str(program.relative_to(REPO)).replace("\\", "/"), *words[1:]]
    else:
        cli = ["-m", "overpressure", "--device", headset.info.codename, *words]
        if step.linux_root and windows:
            return ["wsl", "-u", "root", "--cd", str(REPO), "--", "python3", *cli]
        argv = [python if not step.linux_root else "python3", *cli]
    if step.linux_root:
        root = (os.geteuid() == 0) if is_root is None and hasattr(os, "geteuid") else bool(is_root)
        if not root:
            argv = ["sudo", *argv]
    return argv


def display(argv: List[str], platform: str = sys.platform) -> str:
    """The command line as one would type it in this host's terminal."""
    argv = ["python" if word == sys.executable else word for word in argv]
    if platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def run(argv: List[str], output: Callable[[str], None], started: Callable[[subprocess.Popen], None] = None) -> int:
    """Runs @p argv from the repository root, handing each line of its output to @p output."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    if sys.platform == "win32":
        env.setdefault("MSYS_NO_PATHCONV", "1")
    proc = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, bufsize=1, text=True,
                            encoding="utf-8", errors="replace")
    if started is not None:
        started(proc)
    assert proc.stdout is not None
    for line in proc.stdout:
        output(line.rstrip("\n"))
    return proc.wait()
