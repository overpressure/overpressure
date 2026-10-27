#!/bin/bash
# Boot the POC initramfs in an arm64 QEMU VM and run its self-test.
#
#   tests/qemu/run-selftest.sh [initramfs.cpio.gz] [extra kernel args...]
#
# Needs qemu-system-aarch64, sfdisk and the kernel from build-test-kernel.sh.
# The VM kernel mirrors the stock PICO kernel where it matters (no devtmpfs,
# configfs gadget) and adds dummy_hcd so the VM can also act as the USB host.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../../../.." && pwd)  # the repository: cache/, out/
KERNEL=${KERNEL:-$PROJECT/cache/qemu/Image}
INITRD=${1:-$(ls -t "$PROJECT"/out/*-initramfs.cpio.gz | head -n 1)}
shift || true
EXTRA="$*"

[ -f "$KERNEL" ] || { echo "missing $KERNEL - run tests/qemu/build-test-kernel.sh" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# A GPT disk with Android-style partition names: poc_test checks the read-only
# enforcement and /dev/block/by-name; poc_export receives the probe report.
truncate -s 32M "$WORK/disk.img"
printf 'label: gpt\nstart=2048, size=8192, name="poc_test"\nstart=10240, size=32768, name="poc_export"\n' |
	sfdisk -q "$WORK/disk.img"

echo ">> kernel : $KERNEL"
echo ">> initrd : $INITRD"
echo ">> extra  : ${EXTRA:-none}"
set +e
timeout 900 qemu-system-aarch64 -M virt -cpu max -smp 4 -m 1024 -nographic -no-reboot \
	-kernel "$KERNEL" -initrd "$INITRD" \
	-drive file="$WORK/disk.img",if=virtio,format=raw \
	-append "console=ttyAMA0 printk.devkmsg=on pico4poc.selftest=1 pico4poc.autoreboot=0 pico4poc.probe_delay=1 $EXTRA" \
	> "$WORK/console.log" 2>&1
rc=$?
set -e
LOG=${CONSOLE_LOG:-$PROJECT/out/qemu-console.log}
cp "$WORK/console.log" "$LOG"
REPORT=$PROJECT/out/qemu-report
rm -rf "$REPORT" && mkdir -p "$REPORT"
dd if="$WORK/disk.img" bs=512 skip=10240 count=32768 status=none |
	tar -xz -C "$REPORT" 2>/dev/null && echo ">> probe report from the VM: $REPORT/report"
grep -E "pico4-poc: (SELFTEST|PICO4POC)" "$LOG" | sed 's/^\[[^]]*\] //'
if grep -q "PICO4POC-SELFTEST: PASS" "$LOG"; then
	echo ">> PASS (console log: $LOG)"
	exit 0
fi
echo ">> FAIL (qemu exit $rc, console log: $LOG)"
exit 1
