# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the read-only storage policy (overpressure.storage)."""

import sys

from overpressure import storage as _same
from overpressure.storage import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
