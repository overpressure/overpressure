# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# Every login shell of the maintenance system (ssh, telnet, serial): someone reached the shell, so the USB link
# works and the recovery deadline stops (recovery-watch).
touch /run/overpressure/no-autoreboot 2>/dev/null
