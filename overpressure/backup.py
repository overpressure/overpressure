# SPDX-License-Identifier: Unlicense
"""Verified files: SHA-256 digests, manifests of backups, and records that are never overwritten.

A backup folder holds manifest.json ({"schema": 1, "files": {NAME: {"size", "sha256"}}}); every
write to a device is checked against it first, so a changed or truncated backup stops it.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def digest(path: Path) -> dict:
    """Return a file's size and SHA-256, as a manifest records them; an empty file raises."""
    size = path.stat().st_size
    if not size:
        msg = f"Empty backup/image: {path}"
        raise ValueError(msg)
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return {"size": size, "sha256": sha.hexdigest()}


def save_new(path: Path, data: dict) -> None:
    """Write DATA as JSON to PATH, which must not exist: a record is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def verified_backup(folder: Path) -> dict:
    """Return a backup's manifest once every file in it matches; a mismatch raises."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or not manifest.get("files"):
        msg = "Incomplete backup manifest"
        raise ValueError(msg)
    for name, expected in manifest["files"].items():
        path = (folder / name).resolve()
        if folder.resolve() not in path.parents or digest(path) != expected:
            msg = f"Backup integrity check failed: {name}"
            raise ValueError(msg)
    return manifest
