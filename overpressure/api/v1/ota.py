# SPDX-License-Identifier: Unlicense
"""Headset interface 1: a vendor's OTA: boot images and partitions, pinned members (overpressure.ota)."""

import sys

from overpressure import ota as _same
from overpressure.ota import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
