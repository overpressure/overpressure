# SPDX-License-Identifier: Unlicense
"""Headset interface 1: a headset's USB network link on a Linux computer (overpressure.usblink), since 1.6."""

import sys

from overpressure import usblink as _same
from overpressure.usblink import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
