# SPDX-License-Identifier: Unlicense
"""The two files the unlock needs, from PICO's own public downloads for the PICO Neo 3.

They stay out of the repository (docs/distribution.md): each owner fetches them. Only the two members
are read from the archives over HTTP ranges, a few MB out of about 5 GB, and checked by hash.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

from overpressure import ota, payload

#: The hashes these downloads were verified with; PICO4_ENGINEERING_ABL_SHA256 defaults to this one.
ENGINEERING_ABL_SHA256 = "446eb09a169eb8e8a25f3ecb9865c08b7ac8cd7636d366a8c450d59dfdbe118a"


@dataclass(frozen=True)
class UnlockFile:
    setting: str     #: the setting naming where it lives (assets/ by default)
    url: str
    member: str
    size: int
    sha256: str


FILES = (
    UnlockFile("engineering_abl",
               "https://zstatic.us-pui.picovr.com/syspackage_web/update_PicoNeo3_4.6.3-202203312043-RELEASE-user-neo3-"
               "b678-55ecdee5d1_SEKSA-B678.zip",
               "firmware-update/abl.elf", 151552, ENGINEERING_ABL_SHA256),
    UnlockFile("loader",
               "https://static.us-pui.picovr.com/SEKSA-pico_rls_neo3-mol-tob-pui-4.8.0-20220622-falconcv3-user-20221017"
               "-225305-32g-b1981.zip",
               "SEKSA-pico_rls_neo3-mol-tob-pui-4.8.0-20220622-falconcv3-user-20221017-225305-32g-b1981/"
               "prog_firehose_ddr.elf", 675320, "d05711c81d07e426ab44a25dd9f4a391c0ddcbe251a935babb2532ca84ebdce1"),
)


def fetch(settings, log=print, read: Callable[[UnlockFile], bytes] = None) -> List[Path]:
    """Each file not yet there, into the place its setting names; a hash mismatch writes nothing."""
    def from_archive(item: UnlockFile) -> bytes:
        return ota.read_member(payload.open_source(item.url, log), item.member, log)

    read = read or from_archive
    done = []
    for item in FILES:
        path = settings.path(item.setting)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256:
            log(f"{path}: already there")
            done.append(path)
            continue
        data = read(item)
        found = hashlib.sha256(data).hexdigest()
        if found != item.sha256 or len(data) != item.size:
            raise ValueError(f"{item.member} from {item.url} is not the known file (SHA-256 {found}): nothing written")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log(f"{path}: {item.size} bytes, SHA-256 checked")
        done.append(path)
    return done
