# SPDX-License-Identifier: Unlicense
"""The two files the unlock needs, from PICO's own public downloads for the PICO Neo 3.

They stay out of the repository (docs/distribution.md): each owner fetches them. Only the two members
are read from the archives over HTTP ranges, a few MB out of about 5 GB, and checked by hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

from overpressure import ota

if TYPE_CHECKING:
    from pathlib import Path

#: The hashes these downloads were verified with; PICO4_ENGINEERING_ABL_SHA256 defaults to this one.
ENGINEERING_ABL_SHA256 = "446eb09a169eb8e8a25f3ecb9865c08b7ac8cd7636d366a8c450d59dfdbe118a"


class Locations(Protocol):
    """Where the settings put each file: localconfig.Settings, or a test's fake."""

    def path(self, name: str) -> Path:
        """Return the path the setting @p name names."""


@dataclass(frozen=True)
class UnlockFile(ota.PinnedMember):
    """One file the unlock needs: the archive member it comes from, and where it lives."""

    setting: str  #: the setting naming where it lives (assets/ by default)


FILES = (
    UnlockFile(
        setting="engineering_abl",
        url="https://zstatic.us-pui.picovr.com/syspackage_web/update_PicoNeo3_4.6.3-202203312043-RELEASE-user-neo3-"
        "b678-55ecdee5d1_SEKSA-B678.zip",
        member="firmware-update/abl.elf",
        size=151552,
        sha256=ENGINEERING_ABL_SHA256,
    ),
    UnlockFile(
        setting="loader",
        url="https://static.us-pui.picovr.com/SEKSA-pico_rls_neo3-mol-tob-pui-4.8.0-20220622-falconcv3-user-20221017"
        "-225305-32g-b1981.zip",
        member="SEKSA-pico_rls_neo3-mol-tob-pui-4.8.0-20220622-falconcv3-user-20221017-225305-32g-b1981/prog_firehose_ddr.elf",
        size=675320,
        sha256="d05711c81d07e426ab44a25dd9f4a391c0ddcbe251a935babb2532ca84ebdce1",
    ),
)


def fetch(
    settings: Locations, log: Callable[[str], None] = print, read: Callable[[UnlockFile], bytes] | None = None
) -> list[Path]:
    """Each file not yet there, into the place its setting names; a hash mismatch writes nothing."""
    return [ota.fetch_pinned(item, settings.path(item.setting), log, read) for item in FILES]
