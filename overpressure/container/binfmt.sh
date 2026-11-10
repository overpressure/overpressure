#!/bin/bash
# SPDX-License-Identifier: Unlicense
# binfmt.sh [--report]: the aarch64 handler the userspace build's chroot runs arm64 programs
# through, a binfmt_misc entry of the host kernel for aarch64 ELF executables with the F
# (fix-binary) flag. Some engines register one, others do not; an arm64 engine runs arm64
# programs itself. When an enabled handler with F is there this
# does nothing (--report says which); else it registers this image's qemu-aarch64-static as
# overpressure-aarch64 and says so, as Ubuntu's qemu-user-static registers it: through its
# aarch64-binfmt-P name (QEMU then knows that argv[0] is preserved) with the flags POF. F opens
# the interpreter at registration, so the handler serves every later container and chroot and the
# host until the host restarts.
set -euo pipefail
dir=${BINFMT_MISC:-/proc/sys/fs/binfmt_misc}  # the variables: for tests
name=overpressure-aarch64
magic=7f454c460201010000000000000000000200b700
qemu=${BINFMT_QEMU:-/usr/libexec/qemu-binfmt/aarch64-binfmt-P}
report=0
[[ ${1:-} == --report ]] && report=1
say() { [[ $report == 0 ]] || echo "binfmt: $*"; }

arch=$(uname -m)
if [[ $arch == aarch64 ]]; then
    say "the engine is aarch64: it runs arm64 programs itself"
    exit 0
fi
if [[ ! -e $dir/register ]]; then
    mount -t binfmt_misc binfmt_misc "$dir" 2>/dev/null || true
fi
[[ -e $dir/register ]] || { echo "binfmt: no binfmt_misc in $dir (the container must run privileged)" >&2; exit 1; }

# found: the first enabled entry for aarch64 executables with the F flag, whatever its name.
found() {
    local f
    for f in "$dir"/*; do
        [[ -f $f && ${f##*/} != register && ${f##*/} != status ]] || continue
        grep -qx enabled "$f" && grep -qx "magic $magic" "$f" && grep -qE '^flags: [A-Z]*F' "$f" &&
            { echo "${f##*/}"; return 0; }
    done
    return 1
}

if entry=$(found); then
    say "aarch64 handler $entry ($(sed -n 's/^interpreter //p' "$dir/$entry"), flags $(sed -n 's/^flags: //p' "$dir/$entry"))"
    exit 0
fi
[[ ! -e $dir/$name ]] || { echo "binfmt: $dir/$name is there but disabled or without F; remove it: echo -1 > $dir/$name" >&2; exit 1; }
[[ -x $qemu ]] || { echo "binfmt: no $qemu in the image" >&2; exit 1; }
# The kernel decodes the \x escapes itself: the line goes to register as it is written here.
printf '%s' ":$name:M::"'\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00'":"'\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff'":$qemu:POF" > "$dir/register"
entry=$(found) || { echo "binfmt: registering $name did not give an enabled aarch64 handler with F" >&2; exit 1; }
echo "binfmt: registered the aarch64 handler $entry ($qemu, flags $(sed -n 's/^flags: //p' "$dir/$entry")) for this host until it restarts"
