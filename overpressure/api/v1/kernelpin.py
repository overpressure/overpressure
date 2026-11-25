# SPDX-License-Identifier: Unlicense
"""Headset interface 1: a headset's pin of overpressure/linux and its contracts (overpressure.kernelpin), since 1.7."""

import sys

from overpressure import kernelpin as _same
from overpressure.kernelpin import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
