# SPDX-License-Identifier: Unlicense
"""Headset interface 1: a headset's assets out of its stock folder (overpressure.assets)."""

import sys

from overpressure import assets as _same
from overpressure.assets import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
