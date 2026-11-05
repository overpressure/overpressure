#!/usr/bin/env python3
# SPDX-License-Identifier: Unlicense
"""Nothing private in what the repository publishes (docs/releasing.md).

    python scripts/check-private.py            the tracked files and new, not ignored ones
    python scripts/check-private.py --history  every version of every file, and every commit message
    python scripts/check-private.py --repo DIR  another clone

It looks for what never belongs in the repository: MAC addresses, Steam IDs, private network addresses,
personal paths, private keys, the private remote's name, register tables of a vendor's parts and
binary files. With a local .env and identity file it also searches for this headset's own serials and
IDs; those values are never printed. Fake values in tests are listed below.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple

REPO = Path(__file__).resolve().parent.parent

PATTERNS = {
    "MAC address": re.compile(rb"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b"),
    "Steam ID": re.compile(rb"\b7656119\d{10}\b"),
    "private address": re.compile(rb"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    "personal path": re.compile(rb"(?:[A-Za-z]:[\\/]+|/mnt/[a-z]/)[Uu]sers[\\/]+(?!x\b|name\b|USER\b|YOU\b)[A-Za-z0-9_.-]+"),
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----(?!\\n)"),
    "private remote": re.compile(rb"git\.example\.net"),
}
#: A table of many {register, value} pairs is a vendor part's settings, not code (kernel/panel_sequence.py).
REGISTER_PAIR = re.compile(rb"\{\s*0x[0-9a-fA-F]{4},\s*0x[0-9a-fA-F]{2}\s*\}")
REGISTER_TABLE = 16

#: What may appear: the USB link, Windows' shared internet, the documentation's addresses, and fakes.
ALLOWED = {
    "MAC address": re.compile(rb"^AA:BB:CC:DD:EE:FF$"),  # the format's placeholder in a document
    "private address": re.compile(rb"^(?:172\.16\.42\.\d+|192\.168\.137\.1|192\.168\.178\.23|10\.9\.8\.\d+|192\.168\.1\.1)$"),
}
#: Fake identifiers of the tests.
TEST_PATHS = re.compile(r"^(?:tests/|devices/[^/]+/tests/|scripts/check-private\.py$)")
TEST_ONLY = {"MAC address", "Steam ID", "private key"}
#: This repository's own binaries: built from src/ here, so stage 1 builds without a compiler.
BINARIES = re.compile(r"^devices/[^/]+/prebuilt/aarch64/pico4-(?:reboot|splash)$")
BINARY_SUFFIXES = {".png", ".jpg", ".svg"}


def findings(path: str, data: bytes, secrets: Iterable[bytes] = ()) -> List[str]:
    """What is wrong with this file's @p data; the local @p secrets are named, never shown."""
    found = []
    if b"\0" in data[:8192] and not BINARIES.match(path) and Path(path).suffix not in BINARY_SUFFIXES:
        return [f"{path}: binary file"]
    for secret in secrets:
        if secret and secret in data:
            found.append(f"{path}: a value from this PC's .env or identity file")
    if BINARIES.match(path):
        return found
    for name, pattern in PATTERNS.items():
        if name in TEST_ONLY and TEST_PATHS.match(path):
            continue
        if path.endswith("check-private.py") and name == "private remote":
            continue
        for match in pattern.finditer(data):
            allowed = ALLOWED.get(name)
            if allowed and allowed.match(match.group(0)):
                continue
            found.append(f"{path}: {name}: {match.group(0).decode('ascii', 'replace')}")
    if not TEST_PATHS.match(path) and len(REGISTER_PAIR.findall(data)) >= REGISTER_TABLE:
        found.append(f"{path}: a table of {len(REGISTER_PAIR.findall(data))} register pairs")
    return found


def local_secrets(root: Path = REPO) -> List[bytes]:
    """This headset's own identifiers, when this PC has them."""
    values = []
    env = root / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip().endswith("_SERIAL") and len(value.strip()) >= 4:
                values.append(value.strip().strip("'\"").encode())
    identity = root / "backup" / "device.json"
    if identity.exists():
        record = json.loads(identity.read_text(encoding="utf-8"))
        values += [str(record[k]).encode() for k in ("serial", "chip_id") if record.get(k)]
    return values


def git(*words: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *words], check=True, stdout=subprocess.PIPE).stdout


def tracked() -> Iterator[Tuple[str, bytes]]:
    """The tracked files and the new ones a commit would add (not ignored)."""
    for name in git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0"):
        path = REPO / name
        if name and path.is_file():
            yield name, path.read_bytes()


def history() -> Iterator[Tuple[str, bytes]]:
    """Every blob reachable from HEAD under one of its names, and every commit message."""
    names: Dict[str, str] = {}
    for line in git("rev-list", "--objects", "HEAD").decode("utf-8", "replace").splitlines():
        sha, _, name = line.partition(" ")
        if name:
            names.setdefault(sha, name)
    process = subprocess.Popen(["git", "-C", str(REPO), "cat-file", "--batch"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)
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
    for entry in git("log", "--format=%H%x00%an <%ae>%n%B%x01", "HEAD").split(b"\x01"):
        sha, _, message = entry.strip().partition(b"\0")
        if sha:
            yield f"commit {sha[:10].decode()}", message


def main(argv: List[str]) -> int:
    global REPO
    secrets = local_secrets()
    if "--repo" in argv:
        REPO = Path(argv[argv.index("--repo") + 1]).resolve()
    found: List[str] = []
    for name, data in (history() if "--history" in argv else tracked()):
        path = name.split("@")[0]
        found += findings(path, data, secrets) if path == name else [f"{name}: {line.partition(': ')[2]}"
                                                                     for line in findings(path, data, secrets)]
    for line in found:
        print(line)
    print(f"{len(found)} finding(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
