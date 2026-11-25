# SPDX-License-Identifier: Unlicense
"""Headset interface 1: a boot image with another kernel (overpressure.kernelimage), since 1.7."""

import sys

from overpressure import kernelimage as _same
from overpressure.kernelimage import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
