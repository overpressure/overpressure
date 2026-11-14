# SPDX-License-Identifier: Unlicense
"""Headset interface 1: Android Verified Boot footers and vbmeta (overpressure.avb)."""

import sys

from overpressure import avb as _same
from overpressure.avb import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
