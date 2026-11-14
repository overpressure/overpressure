# SPDX-License-Identifier: Unlicense
"""Headset interface 1: cpio archives (ramdisks) (overpressure.cpio)."""

import sys

from overpressure import cpio as _same
from overpressure.cpio import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
