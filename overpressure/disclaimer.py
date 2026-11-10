# SPDX-License-Identifier: Unlicense
"""The disclaimer an owner accepts before anything touches the headset, and the computer's power check.

The window shows the disclaimer on every start: accepted, or the window closes. On the command line
every command that touches the headset (Headset.touches; the builds do not) shows it and asks y or
n; --accept-disclaimer, or ENV=1 in the environment, accepts it without asking (unattended runs). The
window and the installer set ENV for the programs they start once it is accepted. The same commands
refuse to start on a computer that runs on its battery below MIN_BATTERY percent (overpressure.power).
"""

from __future__ import annotations

import os
import sys
from typing import IO, TYPE_CHECKING, Callable

from . import power

if TYPE_CHECKING:
    from collections.abc import MutableMapping

#: The environment variable that says the disclaimer was accepted; the flag sets it.
ENV = "OVERPRESSURE_ACCEPT_DISCLAIMER"
FLAG = "--accept-disclaimer"
TITLE = "Before you go on"
TEXT = (
    "Your warranty is now void, or will be the moment the bootloader unlocks.\n\n"
    "We are not responsible for bricked headsets, scratched lenses, controllers thrown through the TV, lost "
    "high scores, a room boundary that ends in a wall, motion sickness, thermonuclear war, or you missing your "
    "train because you forgot the time in VR. Read up on what this changes before you go on.\n\n"
    "YOU are choosing to make these changes to your headset. overpressure comes with no warranty of any kind, "
    "and if you point the finger at us when something breaks, we will laugh at you (kindly, and in VR)."
)
ACCEPT = "I accept"
DECLINE = "Close"
QUESTION = "Do you accept this and want to go on? [y/n] "
DECLINED = "Declined: nothing was done."
UNATTENDED = f"No answer: nothing was done. Accept the disclaimer with {FLAG} to run it without asking."


def accepted(environ: MutableMapping[str, str] | None = None) -> bool:
    """Tell whether the disclaimer was accepted for this program and the ones it starts."""
    return (os.environ if environ is None else environ).get(ENV) == "1"


def accept(environ: MutableMapping[str, str] | None = None) -> None:
    """Record the acceptance for this program and every program it starts from now on."""
    (os.environ if environ is None else environ)[ENV] = "1"


def ask(read: Callable[[str], str] = input, out: IO[str] | None = None) -> bool | None:
    """Show the disclaimer and ask until the answer is y or n; None for no answer (the end of the input)."""
    out = out or sys.stderr
    out.write(f"\n{TITLE}\n\n{TEXT}\n\n")
    out.flush()
    while True:
        try:
            answer = read(QUESTION).strip().lower()
        except EOFError:
            return None
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def gate(
    *,
    environ: MutableMapping[str, str] | None = None,
    read: Callable[[str], str] = input,
    out: IO[str] | None = None,
    battery: Callable[[], str] | None = None,
) -> int:
    """Return 0 when a command that touches the headset may run; else say why and return its exit status.

    The computer's battery first (1), then the disclaimer: accepted already, else asked (n: 1, no
    answer: 2). Whether stdin is a terminal does not decide: Git Bash's is a pipe, Windows' NUL a tty.
    """
    out = out or sys.stderr
    problem = (battery or power.battery_problem)()
    if problem:
        out.write(f"error: {problem}\n")
        return 1
    if accepted(environ):
        return 0
    answer = ask(read, out)
    if answer is None:
        out.write(f"\n{UNATTENDED}\n")
        return 2
    if not answer:
        out.write(DECLINED + "\n")
        return 1
    accept(environ)
    return 0
