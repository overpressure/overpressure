# SPDX-License-Identifier: Unlicense
"""Headset interface 1: a headset's downloads before an install (overpressure.prefetch), since 1.7."""

import sys

from overpressure import prefetch as _same
from overpressure.prefetch import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
