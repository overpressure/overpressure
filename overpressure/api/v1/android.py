# SPDX-License-Identifier: Unlicense
"""Headset interface 1: adb and fastboot, fail-closed, each command naming the serial (overpressure.android)."""

import sys

from overpressure import android as _same
from overpressure.android import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
