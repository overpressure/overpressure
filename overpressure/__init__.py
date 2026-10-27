"""overpressure: Linux and a SteamOS-compatible userspace on standalone VR headsets.

The host side, pure Python (3.9+) with the standard library only, so it runs on Windows, Linux
and macOS: the formats every Android-based headset shares (boot images, AVB footers, sparse
images, OTA payloads, cpio, Alpine packages), the registry of headsets in devices/, and
the command line and GUI that drive each headset's own workflow (devices/<codename>/tooling).
"""
from pathlib import Path

#: The repository root: .env and the private, ignored folders (out/, cache/, assets/, backup/).
REPO = Path(__file__).resolve().parent.parent
