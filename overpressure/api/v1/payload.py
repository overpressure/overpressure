# SPDX-License-Identifier: Unlicense
"""Headset interface 1: Android A/B OTA payloads (overpressure.payload)."""

import sys

from overpressure import payload as _same
from overpressure.payload import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
