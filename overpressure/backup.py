"""Verified files: SHA-256 digests, manifests of backups, and records that are never overwritten.

A backup folder holds manifest.json ({"schema": 1, "files": {NAME: {"size", "sha256"}}}); every
write to a device is checked against it first, so a changed or truncated backup stops it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def digest(path: Path) -> dict:
    size = path.stat().st_size
    if not size:
        raise ValueError(f"Empty backup/image: {path}")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return {"size": size, "sha256": sha.hexdigest()}


def save_new(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def verified_backup(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not manifest.get("files"):
        raise ValueError("Incomplete backup manifest")
    for name, expected in manifest["files"].items():
        path = (folder / name).resolve()
        if folder.resolve() not in path.parents or digest(path) != expected:
            raise ValueError(f"Backup integrity check failed: {name}")
    return manifest
