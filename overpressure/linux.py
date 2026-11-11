# SPDX-License-Identifier: Unlicense
"""Where a step that needs Linux as root runs: overpressure's build container, on every host.

    python -m overpressure container build       the image, overpressure-builder:HASH
    python -m overpressure container run -- CMD   CMD in the container, from the repository root
    python -m overpressure container shell        an interactive shell there
    python -m overpressure container binfmt       the aarch64 handler, registered when the engine has none

The container (overpressure/container) holds every tool of the builds (the userspace, the system
image, Valve's kernel), so a host needs only Docker, else Podman (OVERPRESSURE_CONTAINER_ENGINE
chooses). It runs privileged (the builds mount and chroot); the repository is mounted at /work and
/var/tmp (build roots) is the named volume overpressure-var.

One build at a time: `container run` names its container overpressure-build, with labels for its
client (host, process and parent process) and its command. A second run finds it and refuses with
one line; a leftover one, whose client is gone (its process was killed), is removed first. A
stopped step's container ends with it: stop_build() removes the container of the process being
stopped before it is ended, since on Windows that process may be killed outright, which leaves
the engine's client and the container running.

On a Linux host (a rootful engine), what a run creates under /work is root's; the entrypoint gives
back to the host's user what root created or changed there during the run. A build registers the
aarch64 handler the userspace build's chroot needs when the engine has none (binfmt.sh).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, TYPE_CHECKING, Callable, NamedTuple

from . import REPO

if TYPE_CHECKING:
    from collections.abc import Sequence

CONTAINER = Path(__file__).resolve().parent / "container"
VOLUME = "overpressure-var"  # /var/tmp in the container: build roots, on a Linux disk
NAME = "overpressure-build"  #: the one build's container
OWNER = "overpressure.owner"  #: label: host:pid:ppid of the client that started it
COMMAND = "overpressure.command"  #: label: its command line
CHECKOUT = "overpressure.checkout"  #: label: the repository it runs from
BINFMT = "overpressure-binfmt"  #: the image's binfmt.sh: the aarch64 handler when the engine has none
#: The prefix of a build's command line (runner.process): this package's container run.
RUN = ("-m", "overpressure", "container", "run")
#: Seconds the engine gets for a query or a removal.
QUERY_SECONDS = 60
#: The engines the builds take, in this order when both are there and answer.
ENGINES = ("docker", "podman")
ANSWER_SECONDS = 20
#: Windows: OpenProcess's access right, GetExitCodeProcess's "still running", and access denied.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


def engine(which: Callable[[str], str | None] = shutil.which) -> str:
    """Return the container engine: docker or podman, whichever is there (OVERPRESSURE_CONTAINER_ENGINE chooses).

    With both, the first that answers: an installed engine that is not running must not hide a
    running one.
    """
    chosen = os.environ.get("OVERPRESSURE_CONTAINER_ENGINE")
    if chosen:
        return chosen
    found = [tool for tool in ENGINES if which(tool)]
    if len(found) > 1:
        return next((tool for tool in found if answers(tool)), found[0])
    return found[0] if found else ENGINES[0]


def answers(tool: str) -> bool:
    """Tell whether the container engine @p tool answers (`info`), within ANSWER_SECONDS."""
    try:
        return (
            subprocess.run(  # noqa: S603  # the container engine, by name, no shell
                [tool, "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=ANSWER_SECONDS,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def image() -> str:
    """Return the image's tag: its recipe's hash, so a changed recipe is a new image."""
    h = hashlib.sha256()
    for name in sorted(p.name for p in CONTAINER.iterdir() if p.is_file()):
        h.update(name.encode() + b"\0" + (CONTAINER / name).read_bytes().replace(b"\r\n", b"\n"))
    return f"overpressure-builder:{h.hexdigest()[:12]}"


def host_ids() -> tuple[int, int] | None:
    """Return the user and group whose files a run's should be on this host; None without them, or as root.

    Whatever the engine: the entrypoint gives files back only where the container's root is the
    host's (a rootful engine), and where the engine maps ownership itself that changes nothing.
    """
    getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return None
    uid = getuid()
    return None if uid == 0 else (uid, getgid())


def owner() -> str:
    """Return this client's label value: host, process and parent process."""
    return f"{socket.gethostname()}:{os.getpid()}:{os.getppid()}"


def run_argv(  # noqa: PLR0913  # the command, and how its container runs keyword-only
    command: Sequence[str],
    *,
    interactive: bool = False,
    tool: str | None = None,
    name: str | None = None,
    labels: dict[str, str] | None = None,
    binfmt: bool = False,
    ids: tuple[int, int] | None = None,
) -> list[str]:
    """Return the container command line that runs @p command from the repository root.

    @p name and @p labels name the container (a build: NAME); @p binfmt registers the aarch64
    handler first when the engine has none; @p ids hands what the run creates under /work back to
    that user and group. A probe passes none of them.
    """
    options = ["--name", name] if name else []
    for key, value in (labels or {}).items():
        options += ["--label", f"{key}={value}"]
    env = {"PYTHONUNBUFFERED": "1"}
    if binfmt:
        env["OVERPRESSURE_BINFMT"] = "1"
    if ids is not None:
        env["OVERPRESSURE_UID"], env["OVERPRESSURE_GID"] = map(str, ids)
    git_mounts, git_env = worktree_git()
    env.update(git_env)
    for key, value in env.items():
        options += ["-e", f"{key}={value}"]
    return [
        tool or engine(),
        "run",
        "--rm",
        "--privileged",
        *(["-it"] if interactive else ["-i"]),
        *options,
        "-v",
        f"{REPO}:/work",
        *linked_mounts(),
        *git_mounts,
        "-w",
        "/work",
        "-v",
        f"{VOLUME}:/var/tmp",
        image(),
        *command,
    ]


#: The private folders at the repository root; a git worktree may link them to another checkout's.
PRIVATE = ("assets", "backup", "cache", "out")


def linked_mounts(root: Path = REPO) -> list[str]:
    """Return mounts for the private folders that are links (a worktree's junctions or symlinks).

    The engine binds the repository as it is, and a link to a folder outside it leads nowhere in
    the container: each such folder is bound at its place under /work. A worktree's own private
    folder may hold such links one level down (cache/stock into the main checkout's cache): those
    are bound too.
    """
    mounts = []
    for name in PRIVATE:
        path = root / name
        target = Path(os.path.realpath(path))
        if target != path.absolute():
            if target.is_dir():
                mounts += ["-v", f"{target}:/work/{name}"]
        elif path.is_dir():
            for child in sorted(path.iterdir()):
                child_target = Path(os.path.realpath(child))
                if child_target != child.absolute() and child_target.is_dir():
                    mounts += ["-v", f"{child_target}:/work/{name}/{child.name}"]
    return mounts


#: Where a worktree's repository (its common git folder) is bound in the container.
GIT_MOUNT = "/git"


def worktree_git(root: Path = REPO) -> tuple[list[str], dict[str, str]]:
    """Return the mount and the environment that let git in the container read a worktree.

    A worktree's .git is a file naming its folder in the main checkout's .git by a host path,
    which leads nowhere in the container. The common folder is bound at GIT_MOUNT, and
    OVERPRESSURE_GIT_DIR names the worktree's folder in it for the scripts that ask git (the
    image's commit, the shell scripts to check); GIT_DIR itself would also reach the git of the
    builds' package recipes. A plain checkout needs neither.
    """
    dot = root / ".git"
    if not dot.is_file():
        return [], {}
    text = dot.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return [], {}
    gitdir = Path(text.split(":", 1)[1].strip())
    gitdir = gitdir if gitdir.is_absolute() else root / gitdir
    common_file = gitdir / "commondir"
    common = gitdir / common_file.read_text(encoding="utf-8").strip() if common_file.is_file() else gitdir
    common, gitdir = Path(os.path.realpath(common)), Path(os.path.realpath(gitdir))
    try:
        inside = gitdir.relative_to(common).as_posix()
    except ValueError:
        return [], {}
    folder = GIT_MOUNT if inside == "." else f"{GIT_MOUNT}/{inside}"
    return ["-v", f"{common}:{GIT_MOUNT}"], {"OVERPRESSURE_GIT_DIR": folder}


def _query(argv: list[str], timeout: float = QUERY_SECONDS) -> tuple[int, str]:
    """Run an engine query; (exit status, its output), 127 when the engine cannot start."""
    try:
        done = subprocess.run(  # noqa: S603  # the container engine, by name, no shell
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


def image_present(run: Callable[[list[str]], tuple[int, str]] = _query) -> bool:
    """Tell whether the container engine has the image."""
    return run([engine(), "image", "inspect", image()])[0] == 0


def build() -> int:
    """Build the image; return the engine's exit status."""
    print(f">> {image()} from {CONTAINER}", flush=True)
    return subprocess.call([engine(), "build", "-t", image(), str(CONTAINER)])  # noqa: S603  # the container engine, no shell


class Build(NamedTuple):
    """The build container as the engine reports it."""

    owner: str  #: host:pid:ppid of its client
    command: str
    checkout: str
    running: bool


def inspect_build(run: Callable[[list[str]], tuple[int, str]] = _query) -> Build | None:
    """Return the build container, or None when there is none."""
    fields = "\t".join(f'{{{{index .Config.Labels "{key}"}}}}' for key in (OWNER, COMMAND, CHECKOUT))
    code, out = run([engine(), "container", "inspect", "--format", fields + "\t{{.State.Running}}", NAME])
    if code != 0:
        return None
    words = (out.splitlines() or [""])[0].split("\t")
    if len(words) != 4:  # noqa: PLR2004  # the four fields asked for
        return None
    return Build(words[0], words[1], words[2], words[3] == "true")


def pid_alive(pid: int) -> bool:
    """Tell whether process @p pid runs on this host."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415  # Windows only

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # noqa: FBT003  # the API's bInheritHandle
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED  # there, but someone else's
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # there, but someone else's
    return True


def _owner_parts(value: str) -> tuple[str, int, int] | None:
    host, _, rest = value.partition(":")
    pid, _, ppid = rest.partition(":")
    try:
        return host, int(pid), int(ppid or 0)
    except ValueError:
        return None


def client_alive(value: str, alive: Callable[[int], bool] = pid_alive) -> bool:
    """Tell whether the client an owner label names still runs; one on another host counts as running."""
    parts = _owner_parts(value)
    if parts is None:
        return False  # no client of ours: a leftover
    host, pid, _ = parts
    return host != socket.gethostname() or alive(pid)


def remove(run: Callable[[list[str]], tuple[int, str]] = _query) -> bool:
    """Remove the build container, running or not; True when the engine did."""
    return run([engine(), "rm", "-f", NAME])[0] == 0


def busy_message(found: Build) -> str:
    """Return the refusal: another build runs, what it is, and what to do."""
    where = f" (from {found.checkout})" if found.checkout and found.checkout != str(REPO) else ""
    return (
        f"another build is running: {found.command or NAME}{where}; let it finish or stop it, then start again "
        f"(if none runs: {engine()} rm -f {NAME})"
    )


def claim(run: Callable[[list[str]], tuple[int, str]] = _query, alive: Callable[[int], bool] = pid_alive) -> str | None:
    """Make room for this client's build: None when it may start, else why not (another build runs).

    A build container whose client is gone is a leftover: it is removed.
    """
    found = inspect_build(run)
    if found is None:
        return None
    if found.running and client_alive(found.owner, alive):
        return busy_message(found)
    print(f">> removing a leftover {NAME} whose client is gone", file=sys.stderr, flush=True)
    if not remove(run) and inspect_build(run) is not None:
        return f"cannot remove the leftover container {NAME}: {engine()} rm -f {NAME}"
    return None


def is_build(argv: Sequence[str]) -> bool:
    """Tell whether @p argv is a build: this package's `container run` (runner.process's Linux-root steps)."""
    return tuple(argv[1:5]) == RUN


def stop_build(pid: int, run: Callable[[list[str]], tuple[int, str]] = _query) -> bool:
    """Remove the build container that the client @p pid (or its child) started; True when there was one.

    It ends the container's command and the engine's client with it; the caller ends @p pid itself.
    """
    found = inspect_build(run)
    parts = _owner_parts(found.owner) if found is not None else None
    if parts is None or parts[0] != socket.gethostname() or pid not in parts[1:]:
        return False
    return remove(run)


def _rewrite(stream: IO[bytes], out: IO[bytes]) -> None:
    """Copy the engine client's stderr, its refusal of a second container of the name as one plain line."""
    refusal = f"error: another build is running (container {NAME}); let it finish or stop it\n".encode()
    for raw in iter(stream.readline, b""):
        text = raw.decode("utf-8", "replace")
        out.write(refusal if "already in use" in text and NAME in text else raw)
        out.flush()


class _Ended(Exception):  # noqa: N818  # a signal, not an error
    """SIGTERM reached the client."""


def _ended(_signum: int, _frame: object) -> None:
    raise _Ended


def _wait(proc: subprocess.Popen) -> int:
    """Wait for @p proc in slices, so that Ctrl-C reaches this process on Windows too."""
    while True:
        with contextlib.suppress(subprocess.TimeoutExpired):
            return proc.wait(0.5)


def run_build(command: list[str]) -> int:
    """Run @p command as the build: refused while another runs, removed when this client is stopped."""
    refusal = claim()
    if refusal:
        print(f"error: {refusal}", file=sys.stderr, flush=True)
        return 1
    labels = {OWNER: owner(), COMMAND: " ".join(command)[:300], CHECKOUT: str(REPO)}
    argv = run_argv(command, name=NAME, labels=labels, binfmt=True, ids=host_ids())
    if hasattr(signal, "SIGTERM") and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _ended)
    proc = subprocess.Popen(argv, stderr=subprocess.PIPE)  # noqa: S603  # the user's command in the container
    copier = threading.Thread(target=_rewrite, args=(proc.stderr, sys.stderr.buffer), daemon=True)
    copier.start()
    try:
        code = _wait(proc)
    except (KeyboardInterrupt, _Ended):
        # The engine keeps a container running when its client ends: remove it, then let the client end.
        stop_build(os.getpid())
        try:
            proc.wait(QUERY_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
        code = 130
    copier.join(5)
    return code


def main(argv: list[str]) -> int:
    """Run the container command line: build, run, shell or binfmt."""
    word = argv[0] if argv else ""
    if word == "build":
        return build()
    if word not in ("run", "shell", "binfmt"):
        print(__doc__.strip())
        return 0 if word in ("-h", "--help") else 2
    command = argv[2:] if argv[1:2] == ["--"] else argv[1:]
    if word == "run" and not command:
        print("usage: python -m overpressure container run -- COMMAND...", file=sys.stderr)
        return 2
    code = 0 if image_present() else build()
    if code:
        return code
    if word == "run":
        return run_build(command)
    if word == "binfmt":
        argv = run_argv([BINFMT, "--report"])
    else:
        argv = run_argv(command or ["bash"], interactive=True, ids=host_ids())
    return subprocess.call(argv)  # noqa: S603  # the container engine, no shell
