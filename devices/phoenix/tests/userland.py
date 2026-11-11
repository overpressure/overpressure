# SPDX-License-Identifier: Unlicense
"""Where the tests that run the headset's own scripts can run.

The headset's scripts use its GNU userland (``stat -c``, ``sed -i``, ``ps --ppid``, ``tail -c``). A
test that runs one with the host's tools checks it only where those tools are GNU's too: on a
Linux host. macOS's BSD tools and Windows' Git Bash differ, and the headset has neither.
"""

import shutil
import sys
import unittest

#: The host's tools are GNU's like the headset's, and bash is there.
GNU_USERLAND = sys.platform.startswith("linux") and shutil.which("bash") is not None

#: Skip a test that runs a headset script with the host's tools anywhere but on Linux.
needs_gnu_userland = unittest.skipUnless(GNU_USERLAND, "the headset's GNU userland (a Linux host)")
