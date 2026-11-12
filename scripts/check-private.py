#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Nothing private in what the repository publishes (docs/releasing.md).

    python scripts/check-private.py            the tracked files and new, not ignored ones
    python scripts/check-private.py --history  every version of every file, and every commit message
    python scripts/check-private.py --repo DIR  another clone

It looks for what never belongs in the repository: MAC addresses, Steam IDs, private network addresses,
personal paths, private keys, register tables of a vendor's parts and binary files. With a local .env
and identity file it also searches for this headset's own serials and IDs, and for the words .env's
OVERPRESSURE_PRIVATE_WORDS lists (comma-separated: private host names, names); those values are never
printed. Fake values in tests are listed below.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

REPO = Path(__file__).resolve().parent.parent

PATTERNS = {
    "MAC address": re.compile(rb"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b"),
    "Steam ID": re.compile(rb"\b7656119\d{10}\b"),
    "private address": re.compile(
        rb"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ),
    "personal path": re.compile(
        rb"(?:[A-Za-z]:[\\/]+|/mnt/[a-z]/)[Uu]sers[\\/]+(?!x\b|name\b|USER\b|YOU\b)[A-Za-z0-9_.-]+"
    ),
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----(?!\\n)"),
}
#: A table of many {register, value} pairs is a vendor part's settings, not code (a camera sensor's mode).
REGISTER_PAIR = re.compile(rb"\{\s*0x[0-9a-fA-F]{4},\s*0x[0-9a-fA-F]{2}\s*\}")
REGISTER_TABLE = 16
#: Shorter .env values would match by chance.
MIN_SECRET = 4
#: The .env key whose comma-separated words never belong in the repository (private host names, names).
PRIVATE_WORDS = "OVERPRESSURE_PRIVATE_WORDS"

#: What may appear: the USB link, Windows' shared internet, the documentation's addresses, and fakes.
ALLOWED = {
    "MAC address": re.compile(rb"^AA:BB:CC:DD:EE:FF$"),  # the format's placeholder in a document
    "private address": re.compile(
        rb"^(?:172\.16\.42\.\d+|192\.168\.137\.1|192\.168\.178\.23|10\.9\.8\.\d+|192\.168\.1\.1)$"
    ),
}
#: Fake identifiers of the tests: this repository's, and those beside overpressure/packages' recipes
#: (LAYER/RECIPE/test_*).
TEST_PATHS = re.compile(r"^(?:tests/|devices/[^/]+/tests/|scripts/check-private\.py$|[a-z0-9]+/[^/]+/test_[^/]+$)")
TEST_ONLY = {"MAC address", "Steam ID", "private key"}
#: This repository's own binaries: built from src/ here, so stage 1 builds without a compiler.
BINARIES = re.compile(r"^devices/[^/]+/prebuilt/aarch64/pico4-(?:reboot|splash)$")
BINARY_SUFFIXES = {".png", ".jpg", ".svg", ".ico"}


def findings(path: str, data: bytes, secrets: Iterable[bytes] = ()) -> list[str]:
    """Return what is wrong with this file's @p data; the local @p secrets are named, never shown."""
    if b"\0" in data[:8192] and not BINARIES.match(path) and Path(path).suffix not in BINARY_SUFFIXES:
        return [f"{path}: binary file"]
    found = [f"{path}: a value from this PC's .env or identity file" for secret in secrets if secret and secret in data]
    if BINARIES.match(path):
        return found
    for name, pattern in PATTERNS.items():
        if name in TEST_ONLY and TEST_PATHS.match(path):
            continue
        for match in pattern.finditer(data):
            allowed = ALLOWED.get(name)
            if allowed and allowed.match(match.group(0)):
                continue
            found.append(f"{path}: {name}: {match.group(0).decode('ascii', 'replace')}")
    if not TEST_PATHS.match(path) and len(REGISTER_PAIR.findall(data)) >= REGISTER_TABLE:
        found.append(f"{path}: a table of {len(REGISTER_PAIR.findall(data))} register pairs")
    return found


def local_secrets(root: Path = REPO) -> list[bytes]:
    """Return this headset's own identifiers and the private words, when this PC has them."""
    values = []
    env = root / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key.endswith("_SERIAL") and len(value) >= MIN_SECRET:
                values.append(value.encode())
            elif key == PRIVATE_WORDS:
                values += [word.strip().encode() for word in value.split(",") if len(word.strip()) >= MIN_SECRET]
    identity = root / "backup" / "device.json"
    if identity.exists():
        record = json.loads(identity.read_text(encoding="utf-8"))
        values += [str(record[k]).encode() for k in ("serial", "chip_id") if record.get(k)]
    return values


def git(repo: Path, *words: str) -> bytes:
    """Return the output of a git command in REPO."""
    argv = ["git", "-C", str(repo), *words]
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE).stdout  # noqa: S603  # git from PATH, no shell


def tracked(repo: Path = REPO) -> Iterator[tuple[str, bytes]]:
    """Yield the tracked files and the new ones a commit would add (not ignored)."""
    for name in git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0"):
        path = repo / name
        if name and path.is_file():
            yield name, path.read_bytes()


def history(repo: Path = REPO) -> Iterator[tuple[str, bytes]]:
    """Yield every blob reachable from HEAD under one of its names, and every commit message."""
    names: dict[str, str] = {}
    for line in git(repo, "rev-list", "--objects", "HEAD").decode("utf-8", "replace").splitlines():
        sha, _, name = line.partition(" ")
        if name:
            names.setdefault(sha, name)
    argv = ["git", "-C", str(repo), "cat-file", "--batch"]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)  # noqa: S603  # git from PATH, no shell
    if process.stdin is None or process.stdout is None:
        msg = "git cat-file has no pipes"
        raise RuntimeError(msg)
    try:
        for sha, name in names.items():
            process.stdin.write(sha.encode() + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            size = int(header[2])
            data = process.stdout.read(size + 1)[:-1]
            if header[1] == b"blob":
                yield f"{name}@{sha[:10]}", data
    finally:
        process.stdin.close()
        process.wait()
    for entry in git(repo, "log", "--format=%H%x00%an <%ae>%n%B%x01", "HEAD").split(b"\x01"):
        sha, _, message = entry.strip().partition(b"\0")
        if sha:
            yield f"commit {sha[:10].decode()}", message


def main(argv: list[str]) -> int:
    """Scan the files, or with --history every version, and print each finding."""
    secrets = local_secrets()
    repo = Path(argv[argv.index("--repo") + 1]).resolve() if "--repo" in argv else REPO
    found: list[str] = []
    for name, data in history(repo) if "--history" in argv else tracked(repo):
        path = name.split("@")[0]
        found += (
            findings(path, data, secrets)
            if path == name
            else [f"{name}: {line.partition(': ')[2]}" for line in findings(path, data, secrets)]
        )
    for line in found:
        print(line)
    print(f"{len(found)} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
