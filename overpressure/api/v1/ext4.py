# SPDX-License-Identifier: Unlicense
"""Headset interface 1: ext4 images, read without mounting (overpressure.ext4)."""

import sys

from overpressure import ext4 as _same
from overpressure.ext4 import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
