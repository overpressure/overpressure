# SPDX-License-Identifier: Unlicense
"""The headsets: one package per codename, each with its tooling (docs/adding-a-headset.md).

The headsets lie in the data root's devices/ (overpressure.REPO), which in a checkout is this folder; the app's
engine keeps this package beside its code and the downloaded headsets in the owner's data folder: both are its path.
"""

from pathlib import Path

from overpressure import REPO

_HEADSETS = REPO / "devices"
if _HEADSETS.resolve() != Path(__file__).resolve().parent and str(_HEADSETS) not in __path__:
    __path__.insert(0, str(_HEADSETS))  # the data root's first: a downloaded headset before a clone's
