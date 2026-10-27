#!/bin/bash
# The container's entry: the host kernel's binfmt handlers visible in /proc (a privileged
# container sees an empty binfmt_misc until it is mounted), then the command.
set -euo pipefail
if [[ ! -e /proc/sys/fs/binfmt_misc/status ]]; then
    mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc 2>/dev/null || true
fi
exec "$@"
