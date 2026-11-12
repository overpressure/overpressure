# SPDX-License-Identifier: Unlicense
"""The weekly upgrade check's reading of Valve's repository and of the package locks (scripts/upstream.py)."""

from __future__ import annotations

import importlib.util
import io
import tarfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("upstream", ROOT / "scripts" / "upstream.py")
assert _spec is not None
assert _spec.loader is not None
upstream = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upstream)


def database(packages: dict[str, str]) -> bytes:
    """Return a pacman repository database (tar.xz) with these packages' desc files."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:xz") as tar:
        for name, version in packages.items():
            body = f"%FILENAME%\n{name}-{version}-aarch64.pkg.tar.zst\n\n%NAME%\n{name}\n\n%VERSION%\n{version}\n\n"
            data = body.encode()
            info = tarfile.TarInfo(f"{name}-{version}/desc")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def lock(*files: str) -> str:
    """Return a packages.lock with these package files."""
    return "".join(f"{'0' * 64}  {f}\n" for f in files)


class LockTests(unittest.TestCase):
    def test_versions_keep_epochs_and_any(self):
        versions = upstream.lock_versions(
            lock("pipewire-1:1.4.9-1-aarch64.pkg.tar.zst", "adwaita-fonts-49.0-2.2-any.pkg.tar.zst")
        )
        self.assertEqual(versions, {"pipewire": "1:1.4.9-1", "adwaita-fonts": "49.0-2.2"})

    def test_the_summary_puts_the_sessions_packages_first(self):
        old = lock(
            "aardvark-dns-1.16.0-1-aarch64.pkg.tar.zst",
            "deckard-steamvr-rel-r25358740+fefbdc82-1-aarch64.pkg.tar.zst",
            "gone-1-1-any.pkg.tar.zst",
        )
        new = lock(
            "aardvark-dns-1.17.0-1-aarch64.pkg.tar.zst",
            "deckard-steamvr-rel-r25400000+0000aaaa-1-aarch64.pkg.tar.zst",
            "fresh-2-1-any.pkg.tar.zst",
        )
        text = upstream.lock_diff(old, new)
        self.assertIn("2 changed, 1 added, 1 removed", text)
        self.assertLess(text.index("deckard-steamvr-rel"), text.index("aardvark-dns"))
        self.assertIn("- **deckard-steamvr-rel**: r25358740+fefbdc82-1 -> r25400000+0000aaaa-1", text)
        self.assertIn("- fresh: new, 2-1", text)
        self.assertIn("- gone: gone (was 1-1)", text)
        self.assertEqual(upstream.lock_diff(old, old), "No package moved since packages.lock.\n")


if __name__ == "__main__":
    unittest.main()
