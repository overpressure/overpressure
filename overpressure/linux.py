# SPDX-License-Identifier: Unlicense
"""Where a step that needs Linux as root runs: WSL, sudo, or overpressure's build container.

    OVERPRESSURE_LINUX=wsl|sudo|docker     (environment or .env; the default fits the host)

- wsl: Windows' Linux, as root (the default on Windows when WSL is there).
- sudo: this Linux host (the default on Linux; no sudo when already root).
- docker: the build container (overpressure/container), on any host with Docker or Podman (the
  default on macOS, and on Windows without WSL). It needs nothing else on the host: its image
  holds the tools of the userspace, kernel, system image and extraction builds, the repository is
  mounted at /work and /var/tmp (build roots, stock folders) is a named volume.

    python -m overpressure container build      the image, overpressure-builder:HASH
    python -m overpressure container run -- CMD  CMD in the container, from the repository root
    python -m overpressure container shell      an interactive shell there
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import REPO, localconfig

BACKENDS = ("wsl", "sudo", "docker")
CONTAINER = Path(__file__).resolve().parent / "container"
VOLUME = "overpressure-var"  # /var/tmp in the container: build roots and stock folders, on a Linux disk


def backend(platform: str = sys.platform, which=shutil.which, environ=None) -> str:
    """The configured backend (OVERPRESSURE_LINUX), else this host's default."""
    environ = os.environ if environ is None else environ
    choice = environ.get("OVERPRESSURE_LINUX")
    if choice is None:
        env = localconfig.ROOT / ".env"
        choice = localconfig.parse(env.read_text(encoding="utf-8")).get("OVERPRESSURE_LINUX") if env.exists() else None
    if choice:
        if choice not in BACKENDS:
            raise ValueError(f"OVERPRESSURE_LINUX={choice!r}: one of {', '.join(BACKENDS)}")
        return choice
    if platform == "win32":
        return "wsl" if which("wsl") else "docker"
    if platform == "darwin":
        return "docker"
    return "sudo"


def engine(which=shutil.which) -> str:
    """docker, else podman (OVERPRESSURE_CONTAINER_ENGINE chooses)."""
    chosen = os.environ.get("OVERPRESSURE_CONTAINER_ENGINE")
    if chosen:
        return chosen
    return "docker" if which("docker") or not which("podman") else "podman"


def image() -> str:
    """The image's tag: its recipe's hash, so a changed recipe is a new image."""
    h = hashlib.sha256()
    for name in sorted(p.name for p in CONTAINER.iterdir() if p.is_file()):
        h.update(name.encode() + b"\0" + (CONTAINER / name).read_bytes().replace(b"\r\n", b"\n"))
    return f"overpressure-builder:{h.hexdigest()[:12]}"


def run_argv(command: List[str], interactive: bool = False, tool: Optional[str] = None) -> List[str]:
    """The container command line that runs @p command from the repository root."""
    return [tool or engine(), "run", "--rm", "--privileged", *(["-it"] if interactive else ["-i"]),
            "-v", f"{REPO}:/work", "-w", "/work", "-v", f"{VOLUME}:/var/tmp", "-e", "PYTHONUNBUFFERED=1",
            image(), *command]


def image_present() -> bool:
    return subprocess.run([engine(), "image", "inspect", image()], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def build() -> int:
    print(f">> {image()} from {CONTAINER}", flush=True)
    return subprocess.call([engine(), "build", "-t", image(), str(CONTAINER)])


def main(argv: List[str]) -> int:
    word = argv[0] if argv else ""
    if word == "build":
        return build()
    if word in ("run", "shell"):
        command = argv[2:] if argv[1:2] == ["--"] else argv[1:]
        if word == "shell":
            command = command or ["bash"]
        if not command:
            print("usage: python -m overpressure container run -- COMMAND...", file=sys.stderr)
            return 2
        if not image_present():
            code = build()
            if code:
                return code
        return subprocess.call(run_argv(command, interactive=word == "shell"))
    print(__doc__.strip())
    return 0 if word in ("-h", "--help") else 2
