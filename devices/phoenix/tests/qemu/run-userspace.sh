#!/bin/bash
# Boot the stage-1 POC initramfs on a 4.19 kernel configured like the stock
# PICO 4 kernel, hand over to the step-4 userspace and run its self-test.
#
#   tests/qemu/run-userspace.sh [initramfs.cpio.gz] [pico4-userspace.tar.zst]
#
# EXTRA_APPEND adds kernel arguments. QEMU_SMP sets the vCPU count, default 2:
# with 4 TCG vCPUs, RCU stalls intermittently made systemd's generator fork time
# out. Needs qemu-system-aarch64, sfdisk, and cache/qemu/Image-4.19 from
#   tests/qemu/build-test-kernel.sh 4.19.325
# The archive is stored on a GPT partition named pico4_payload; stage 1 reads
# it with the same "pico4-userspace receive" used over SSH on the headset.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=$(cd "$HERE/../../../.." && pwd)  # the repository: cache/, out/
KERNEL=${KERNEL:-$PROJECT/cache/qemu/Image-4.19}
INITRD=${1:-$(ls -t "$PROJECT"/out/*-initramfs.cpio.gz | head -n 1)}
ROOTFS=${2:-$PROJECT/out/userspace/pico4-userspace.tar.zst}
[ -f "$KERNEL" ] || { echo "missing $KERNEL - run tests/qemu/build-test-kernel.sh 4.19.325" >&2; exit 1; }
[ -f "$ROOTFS" ] || { echo "missing $ROOTFS - run scripts/build-userspace.sh" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
bytes=$(stat -c %s "$ROOTFS")
sha=$(sha256sum "$ROOTFS" | cut -d' ' -f1)
sectors=$(( (bytes + 511) / 512 ))
truncate -s $(( (sectors + 4096 + 8192 + 2048) * 512 )) "$WORK/disk.img"
printf 'label: gpt\nstart=2048, size=8192, name="poc_test"\nstart=10240, size=%s, name="pico4_payload"\n' "$sectors" |
	sfdisk -q "$WORK/disk.img"
dd if="$ROOTFS" of="$WORK/disk.img" bs=512 seek=10240 conv=notrunc status=none

# Closest QEMU model of the XR2's Cortex-A77-class cores that 4.19 handles.
cpu=cortex-a57
qemu-system-aarch64 -M virt -cpu help 2>/dev/null | grep -qw cortex-a76 && cpu=cortex-a76

echo ">> kernel : $KERNEL"
echo ">> initrd : $INITRD"
echo ">> rootfs : $ROOTFS ($bytes bytes, sha256 $sha)"
LOG=${CONSOLE_LOG:-$PROJECT/out/qemu-userspace-console.log}
set +e
timeout 2400 qemu-system-aarch64 -M virt -cpu "$cpu" -smp "${QEMU_SMP:-2}" -m 4096 -nographic -no-reboot \
	-kernel "$KERNEL" -initrd "$INITRD" \
	-drive file="$WORK/disk.img",if=virtio,format=raw \
	-append "console=ttyAMA0 printk.devkmsg=on pico4poc.autoreboot=0 pico4poc.probe=0 pico4poc.userspace_selftest=1 pico4poc.userspace_payload=pico4_payload:$bytes:$sha ${EXTRA_APPEND:-}" \
	> "$LOG" 2>&1
rc=$?
set -e
grep -E "pico4-poc: (userspace|PICO4-USERSPACE)" "$LOG" | sed 's/^\[[^]]*\] //'
RESULT=$PROJECT/out/qemu-userspace-check.json
sed -n 's/.*pico4-userspace-check: \({.*\)$/\1/p' "$LOG" | tr -d '\r' > "$RESULT"
if grep -q "PICO4-USERSPACE-SELFTEST: PASS" "$LOG"; then
	echo ">> PASS (console log: $LOG, check: $RESULT)"
	exit 0
fi
echo ">> FAIL (qemu exit $rc, console log: $LOG)"
grep -E "pico4-journal:|Failed|FAILED|panic" "$LOG" | tail -n 40
exit 1
