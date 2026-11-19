# SPDX-License-Identifier: Unlicense
"""Headset interface 1: HTTPS downloads (overpressure.web), since 1.5."""

import sys

from overpressure import web as _same
from overpressure.web import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
