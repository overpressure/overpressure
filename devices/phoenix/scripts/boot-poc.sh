#!/bin/sh
# Boot the PICO 4 Linux POC image from RAM ("fastboot boot"); nothing is flashed.
# Needs adb/fastboot (Android platform-tools) and an unlocked bootloader.
#   scripts/boot-poc.sh [image]
set -eu
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)  # the repository
IMAGE=${1:-$(ls -t "$ROOT"/out/pico4-poc-*.img 2>/dev/null | head -n 1)}
[ -n "$IMAGE" ] || { echo "no image in out/ - run: python3 -m overpressure build" >&2; exit 1; }
command -v fastboot >/dev/null || { echo "fastboot not found (Android platform-tools)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 required for the unlock-state check" >&2; exit 1; }

if command -v adb >/dev/null && [ "$(adb get-state 2>/dev/null)" = device ]; then
	echo ">> headset is running PICO OS, rebooting into fastboot"
	adb reboot bootloader
fi
echo ">> waiting for the headset in fastboot mode"
i=0
until [ -n "$(fastboot devices 2>/dev/null)" ]; do
	i=$((i + 1))
	[ "$i" -lt 60 ] || { echo "no fastboot device found" >&2; exit 1; }
	sleep 1
done
PYTHONPATH="$ROOT" python3 -m overpressure --device phoenix device check-unlocked
echo ">> fastboot boot $IMAGE   (RAM only, nothing is written to the headset)"
fastboot boot "$IMAGE" || {
	echo "fastboot boot failed. If the bootloader reports an unknown command, see" >&2
	echo "docs/stage1.md, \"If fastboot boot is not available\"." >&2
	exit 1
}

echo ">> waiting for the headset on 172.16.42.1 (up to 3 minutes)"
i=0
until ping -c 1 -W 1 172.16.42.1 >/dev/null 2>&1; do
	i=$((i + 1))
	[ "$i" -lt 90 ] || { echo; echo "no network yet - see docs/stage1.md > Troubleshooting"; exit 1; }
	printf .
	sleep 1
done
echo
echo "Linux is up:  ssh root@172.16.42.1  (empty password)   http://172.16.42.1/"
