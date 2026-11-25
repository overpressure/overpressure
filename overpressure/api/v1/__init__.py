# SPDX-License-Identifier: Unlicense
"""Headset interface 1 (overpressure.headset.SUPPORTED names its newest minor): what a headset may use.

    from overpressure.api.v1 import REPO, qualcomm
    from overpressure.api.v1.headset import Headset, Step

Its modules and every name in them, with their signatures: docs/api.md (scripts/api-reference.py writes it).

Each module is today's own module (the same object at run time, so a patch in a test reaches every caller).
Once interface 2 changes one, its module here becomes an adapter that keeps interface 1's behaviour.
"""

from overpressure import REPO

__all__ = ["REPO"]
