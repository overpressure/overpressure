#!/bin/sh
# SPDX-License-Identifier: Unlicense
set -eu
cd "$(dirname "$0")/.."
. initramfs/usr/libexec/pico4/recovery-policy.sh

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
# The dev loop's host contact renews the maximum runtime; without it the hour counts from boot.
[ -z "$(recovery_reason 3700 600 3600 1 1 1 200)" ]
[ -n "$(recovery_reason 3800 600 3600 1 1 1 200)" ]
[ "$(recovery_reason 3800 600 3600 1 1 1 200)" = "maximum runtime: no host contact for 3600s" ]
[ "$(recovery_reason 3600 600 3600 1 1 1 0)" = "hardware test exceeded 3600s" ]
[ "$(recovery_reason 3600 600 3600 1 1 1)" = "hardware test exceeded 3600s" ]
# Contact never cancels the connection deadline of an unauthenticated run.
[ -n "$(recovery_reason 600 600 3600 1 1 0 590)" ]
echo 'Recovery policy: 14 checks passed'
