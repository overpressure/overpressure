#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
. initramfs/usr/libexec/pico4-poc/recovery-policy.sh

# USB enumeration/carrier alone supplies neither reachability nor authentication.
[ -z "$(recovery_reason 299 300 900 0 0 0)" ]
[ -n "$(recovery_reason 300 300 900 0 0 0)" ]
[ -z "$(recovery_reason 300 300 900 0 1 0)" ]
[ -z "$(recovery_reason 1000 0 900 0 0 0)" ]
# Even DHCP traffic cannot cancel a hardware run's authentication deadline.
[ -n "$(recovery_reason 210 210 660 1 1 0)" ]
[ -z "$(recovery_reason 210 210 660 1 1 1)" ]
[ -z "$(recovery_reason 659 210 660 1 1 1)" ]
# A connected, authenticated but abandoned test eventually returns to fastboot.
[ -n "$(recovery_reason 660 210 660 1 1 1)" ]
echo 'Recovery policy: 8 checks passed'
