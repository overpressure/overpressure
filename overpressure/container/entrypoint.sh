#!/bin/bash
# SPDX-License-Identifier: Unlicense
# The build container's entry (overpressure/linux.py):
# 1. the host kernel's binfmt handlers visible in /proc (a privileged container sees an empty
#    binfmt_misc until it is mounted);
# 2. with OVERPRESSURE_BINFMT=1 (a build), the aarch64 handler registered when the engine has
#    none (overpressure-binfmt);
# 3. the command. With OVERPRESSURE_UID and OVERPRESSURE_GID (a Linux host's user) on a rootful
#    engine, what root created or changed under /work during the command goes back to that user
#    (overpressure-hand-back), whatever the command's exit status, which stays the container's.
#    A rootless engine's root is the host's user already: nothing to give back.
set -euo pipefail
if [[ ! -e /proc/sys/fs/binfmt_misc/status ]]; then
    mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc 2>/dev/null || true
fi
if [[ ${OVERPRESSURE_BINFMT:-} == 1 ]]; then
    overpressure-binfmt
fi
uid=${OVERPRESSURE_UID:-} gid=${OVERPRESSURE_GID:-}
rootful=0
read -r inside outside count < /proc/self/uid_map && [[ $inside == 0 && $outside == 0 && $count == 4294967295 ]] && rootful=1
if [[ -z $uid || -z $gid || $uid == 0 || $rootful == 0 || ! -d /work ]]; then
    exec "$@"
fi
marker=$(mktemp)
status=0
"$@" || status=$?
overpressure-hand-back /work "$marker" "$uid" "$gid" || echo "warning: could not give /work's new files back to $uid:$gid" >&2
rm -f "$marker"
exit "$status"
