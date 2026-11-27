#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Put a standalone Python with overpressure into the app's engine folder, which tauri build ships.

    python scripts/bundle-app.py [--out app/src-tauri/engine]

uv, pinned as the start scripts pin it (its version and each system's SHA-256 in Install.cmd and install.sh),
installs its managed Python 3.12 (a python-build-standalone build, checked against the hashes that uv version
carries) into OUT/python. uv.lock's packages (no development tools, no extras), exported with their hashes, go
into that Python with every hash required. The files git tracks that the engine reads at run time
(overpressure/, devices/__init__.py, pyproject.toml for the version, .env.example, the licences) go to
OUT/root, compiled to bytecode with the bundled Python, and marked as the app's engine (.bundled), whose data goes to
the owner's own folder (overpressure.data_root). The Python is relocatable: the app runs it from wherever it is
installed (app/src-tauri/src/lib.rs). OUT/bundle.json says what went in.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from overpressure import web  # noqa: E402  # every download goes through it

#: The Python the app carries: the start scripts' (Install.cmd, install.sh: --python 3.12).
PYTHON = "3.12"
#: What the engine reads from its root at run time, beside overpressure/ itself.
ROOT_FILES = ("devices/__init__.py", "pyproject.toml", ".env.example", "LICENSE", "THIRD-PARTY.md")
#: The standard library's parts the engine never imports: its own tests, the Tk window's toolkit, IDLE.
TRIMMED = ("Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "tcl")
RELEASES = "https://github.com/astral-sh/uv/releases/download"


def uv_pin(system: str = sys.platform, machine: str = platform.machine()) -> tuple[str, str, str]:
    """Return the start scripts' uv for this system: its version, its asset and the asset's SHA-256.

    Read from Install.cmd on Windows, from install.sh elsewhere, so that the app and the start scripts run the
    same uv (tests/test_workflows.py keeps the two scripts' versions the same).
    """
    cpus = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    cpu = cpus.get(machine.lower(), "")
    arm = cpu == "aarch64"
    if system == "win32" and cpu:
        text = (ROOT / "Install.cmd").read_text(encoding="utf-8")
        version = re.search(r'^set "UV_VERSION=([0-9.]+)"', text, re.MULTILINE)
        prefix = re.escape('if /i "%ARCH%"=="ARM64" set ' if arm else "set ")
        asset = re.search(rf'^{prefix}"UV_ASSET=([^"]+)"', text, re.MULTILINE)
        digest = re.search(rf'^{prefix}"UV_SHA256=([0-9a-f]{{64}})"', text, re.MULTILINE)
        if version and asset and digest:
            return version.group(1), asset.group(1), digest.group(1)
    elif cpu:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        version = re.search(r"^UV_VERSION=([0-9.]+)$", text, re.MULTILINE)
        kernel = "Darwin" if system == "darwin" else "Linux"
        cases = re.finditer(r'^\s*([^)\n]+)\)\s*\n\s*asset="([^"]+)"\s*\n\s*sum=([0-9a-f]{64})', text, re.MULTILINE)
        for case in cases:
            if version and case.group(1).startswith(f"{kernel}-") and case.group(2).startswith(f"uv-{cpu}-"):
                return version.group(1), case.group(2) + ".tar.gz", case.group(3)
    msg = f"the start scripts pin no uv for {system} {machine}"
    raise SystemExit(msg)


def fetch_uv(cache: Path) -> Path:
    """Return the pinned uv, downloaded into @p cache (checked by its SHA-256) unless it is there."""
    version, asset, digest = uv_pin()
    folder = cache / f"{version}-{asset}"
    program = folder / ("uv.exe" if sys.platform == "win32" else "uv")
    if program.is_file():
        return program
    data = web.read(f"{RELEASES}/{version}/{asset}", timeout=120)
    if hashlib.sha256(data).hexdigest() != digest:
        msg = f"the downloaded {asset} does not match its pinned SHA-256: nothing was unpacked"
        raise SystemExit(msg)
    folder.mkdir(parents=True, exist_ok=True)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(folder)  # noqa: S202  # the pinned uv's own archive, its SHA-256 checked
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():  # the archive's one folder: its programs go flat into ours
                if member.isfile():
                    member.name = Path(member.name).name
                    archive.extract(member, folder)
    if sys.platform != "win32":
        program.chmod(0o755)
    return program


def run(argv: list[str | Path], env: dict[str, str] | None = None) -> str:
    """Run @p argv from the repository, in @p env (this process's by default); return its output, or stop with it."""
    words = [str(a) for a in argv]
    done = subprocess.run(words, cwd=ROOT, capture_output=True, text=True, check=False, env=env)  # noqa: S603  # our own pinned tools
    if done.returncode:
        msg = f"{Path(str(argv[0])).name} {' '.join(map(str, argv[1:3]))} failed:\n{done.stdout}{done.stderr}"
        raise SystemExit(msg)
    return done.stdout


def python_of(folder: Path) -> Path:
    """Return the interpreter of a python-build-standalone folder."""
    return folder / "python.exe" if sys.platform == "win32" else folder / "bin" / "python3"


def bundle(out: Path) -> dict:
    """Build @p out/python and @p out/root anew; return what went in (also @p out/bundle.json)."""
    uv = fetch_uv(ROOT / "cache" / "uv")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = {**os.environ, "UV_CACHE_DIR": str(ROOT / "cache" / "uv-cache"), "UV_NO_CONFIG": "1"}
    with tempfile.TemporaryDirectory() as temp:
        installs = Path(temp) / "pythons"
        run([uv, "python", "install", PYTHON, "--install-dir", installs, "--no-bin"], env=env)
        # The folder of the full version; uv links the minor version's name to it too.
        (found,) = [p for p in installs.iterdir() if re.match(r"cpython-\d+\.\d+\.\d+-", p.name)]
        shutil.move(str(found), str(out / "python"))
        requirements = Path(temp) / "requirements.txt"
        export = ["export", "--locked", "--no-dev", "--no-emit-project", "--format", "requirements-txt"]
        run([uv, *export, "--output-file", requirements], env=env)
        python = python_of(out / "python")
        install = ["pip", "install", "--python", python, "--break-system-packages", "--require-hashes"]
        run([uv, *install, "-r", requirements], env=env)
    for part in TRIMMED:
        shutil.rmtree(out / "python" / part, ignore_errors=True)
    tracked = run(["git", "ls-files", "-z", "overpressure", *ROOT_FILES]).split("\0")
    for name in filter(None, tracked):
        target = out / "root" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    # The mark that this root is the app's engine: its data goes to the owner's folder (overpressure.data_root).
    (out / "root" / ".bundled").write_text("overpressure's engine inside the app\n", encoding="utf-8")
    run([python, "-m", "compileall", "-q", out / "root"])
    version = run([python, "-c", "import sys; print(sys.version.split()[0])"]).strip()
    packages = run([uv, "pip", "list", "--python", python, "--format", "json"], env=env)
    manifest = {
        "schema": 1,
        "python": version,
        "uv": uv_pin()[0],
        "packages": {p["name"]: p["version"] for p in json.loads(packages)},
        "overpressure": run(["git", "rev-parse", "HEAD"]).strip(),
        "files": sum(1 for p in out.rglob("*") if p.is_file()),
        "bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file()),
    }
    (out / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    """Build the bundle and say what went in."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "app" / "src-tauri" / "engine")
    manifest = bundle(ap.parse_args().out.resolve())
    print(
        f"Python {manifest['python']}, {', '.join(manifest['packages'])}; {manifest['files']} files, "
        f"{manifest['bytes'] / 1e6:.0f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
