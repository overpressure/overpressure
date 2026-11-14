# SPDX-License-Identifier: Unlicense
"""Headset interface 1: the installer: stages, tasks, asks and the context a task runs in (overpressure.install)."""

import sys

from overpressure import install as _same
from overpressure.install import *  # noqa: F403  # interface 1 is the module as it is: its names, for type checkers

sys.modules[__name__] = _same  # at run time the module itself: one module, so a patch reaches every caller
