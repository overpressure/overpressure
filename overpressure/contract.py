# SPDX-License-Identifier: Unlicense
"""The headset runtime's contract on the PC's side: the paths, files and kernel words the host reads or writes.

overpressure's runtime (overpressure/runtime/, a headset's maintenance system; the installed system's share in
overpressure/packages' common/overpressure-base) keeps its state in RUN, its programs in LIB and its settings in ETC,
and reads its kernel command line words as overpressure.NAME (word). The host renews the maintenance system's
deadline through CONTACT, proves a test run's endpoint by TEST_RUN and marks it in TEST_AUTHENTICATED, and reads
the maintenance system's build in BUILD_INFO (docs/runtime.md). Since interface 1.8.
"""

from __future__ import annotations

#: The runtime's state (tmpfs), its programs and its settings, on the headset.
RUN = "/run/overpressure"
LIB = "/usr/libexec/overpressure"
ETC = "/etc/overpressure"
#: The uptime of the host's last command: the maintenance system's deadline counts from it (recovery-watch).
CONTACT = f"{RUN}/host-contact"
#: A test run's identity in its image, and the mark that its endpoint was proven (the deadline then stops).
TEST_RUN = f"{ETC}/test-run"
TEST_AUTHENTICATED = f"{RUN}/test-authenticated"
#: How long a test run waits for its first contact and runs at most (seconds, its image's files).
TEST_CONNECT_TIMEOUT = f"{ETC}/test-connect-timeout"
TEST_TIMEOUT = f"{ETC}/test-timeout"
#: The maintenance system's build (JSON): its Alpine, its packages, its kernel.
BUILD_INFO = f"{ETC}/build-info"
#: The headset's values the runtime sources (its name, its USB link, its programs).
HEADSET = f"{ETC}/headset"
#: The word prefix the runtime reads on the kernel command line.
WORD = "overpressure."


def word(name: str, value: object = None) -> str:
    """Return the kernel command line word overpressure.NAME, with =VALUE when one is given."""
    return f"{WORD}{name}" if value is None else f"{WORD}{name}={value}"
