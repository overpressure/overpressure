#!/system/bin/sh
# SPDX-License-Identifier: Unlicense
# PICO 4 Linux preflight - runs ON the headset under PICO OS, as the
# unprivileged adb shell user. Pushed and started by python -m overpressure hwtest preflight.
#
# It runs stage 1's Alpine userspace binaries on the real PICO kernel (through
# the musl loader, so nothing has to be installed) and records the facts the
# POC relies on. No root, no unlock needed; it only touches
# /data/local/tmp/pico4 and removes the unpacked files again.

D=${PICO4_PREFLIGHT_DIR:-/data/local/tmp/pico4}
R=$D/rootfs
LD=$R/lib/ld-musl-aarch64.so.1
pass=0
fail=0

cd "$D" || exit 1
rm -rf "$R" && mkdir -p "$R"
if ! tar -xf "$D/rootfs.tar" -C "$R" 2>"$D/tar.err"; then
	echo "note: tar reported problems (usually harmless): $(head -c 300 "$D/tar.err")"
fi

# Run an Alpine binary with its own libraries.
alp() {
	"$LD" --library-path "$R/lib:$R/usr/lib" "$@"
}

t() {
	name=$1
	shift
	if out=$("$@" 2>&1); then
		echo "ok   $name: $(echo "$out" | head -n 1 | cut -c1-100)"
		pass=$((pass + 1))
	else
		echo "FAIL $name (exit $?): $(echo "$out" | head -n 3 | tr '\n' ' ' | cut -c1-300)"
		fail=$((fail + 1))
	fi
}

echo "== headset"
for p in ro.build.fingerprint ro.oem.state ro.product.model ro.boot.verifiedbootstate \
	ro.boot.flash.locked ro.boot.vbmeta.device_state sys.usb.controller sys.usb.config; do
	echo "$p=$(getprop "$p")"
done
echo "kernel=$(uname -r) $(uname -m)"

echo
echo "== POC userspace on this kernel"
t "musl loader + busybox" alp "$R/bin/busybox" true
t "busybox sh, arithmetic" alp "$R/bin/busybox" sh -c 'echo $((6*7))'
t "busybox fractional sleep" alp "$R/bin/busybox" sleep 0.2
t "busybox md5sum" alp "$R/bin/busybox" md5sum "$R/bin/busybox"
t "busybox awk" alp "$R/bin/busybox" awk 'BEGIN { print 21 * 2 }'
t "busybox-extras udhcpd" alp "$R/bin/busybox-extras" udhcpd --help
t "busybox-extras telnetd" alp "$R/bin/busybox-extras" telnetd --help
t "busybox-extras httpd" alp "$R/bin/busybox-extras" httpd --help
t "dropbear" alp "$R/usr/sbin/dropbear" -V
rm -f "$D/test_hostkey" "$D/test_hostkey.pub"
t "dropbearkey (getrandom + crypto)" alp "$R/usr/bin/dropbearkey" -t ed25519 -f "$D/test_hostkey"
rm -f "$D/disk.img"
t "dd 8 MiB image" alp "$R/bin/busybox" dd if=/dev/zero of="$D/disk.img" bs=1048576 count=8
t "mtools mformat" env MTOOLS_SKIP_CHECK=1 "$LD" --library-path "$R/lib:$R/usr/lib" "$R/usr/bin/mformat" -i "$D/disk.img" -v PREFLIGHT ::
t "mtools mcopy" env MTOOLS_SKIP_CHECK=1 "$LD" --library-path "$R/lib:$R/usr/lib" "$R/usr/bin/mcopy" -i "$D/disk.img" "$R/etc/hostname" ::/test.txt
t "mtools mdir" env MTOOLS_SKIP_CHECK=1 "$LD" --library-path "$R/lib:$R/usr/lib" "$R/usr/bin/mdir" -i "$D/disk.img" ::
t "kmod" alp "$R/bin/kmod" --version
t "lsblk" alp "$R/bin/lsblk" --version
t "dtc" alp "$R/usr/bin/dtc" --version
t "htop" alp "$R/usr/bin/htop" --version
t "strace" alp "$R/usr/bin/strace" -V
t "pico4-reboot (static, --help only)" "$R/usr/sbin/pico4-reboot" --help

echo
echo "== facts stage 1 relies on (some may be hidden from the shell user)"
echo "udc: $(ls /sys/class/udc 2>&1 | tr '\n' ' ')"
echo "leds: $(ls /sys/class/leds 2>&1 | tr '\n' ' ')"
echo "drm: $(ls /sys/class/drm 2>&1 | tr '\n' ' ')"
echo "backlight: $(ls /sys/class/backlight 2>&1 | tr '\n' ' ')"
echo "lpm sleep_disabled: $(cat /sys/module/lpm_levels/parameters/sleep_disabled 2>&1)"
echo "cpufreq governors: $(cat /sys/devices/system/cpu/cpufreq/policy*/scaling_governor 2>&1 | tr '\n' ' ')"
echo "pstore: $(ls /sys/fs/pstore 2>&1 | tr '\n' ' ')"
if zcat /proc/config.gz >/dev/null 2>&1; then
	echo "kernel config: $(zcat /proc/config.gz | grep -E '^(# )?CONFIG_(DEVTMPFS|USB_CONFIGFS_NCM|USB_CONFIGFS_MASS_STORAGE|RD_GZIP)[ =]' | tr '\n' ' ')"
else
	echo "kernel config: /proc/config.gz not readable as shell"
fi

rm -rf "$R" "$D/disk.img" "$D/test_hostkey" "$D/test_hostkey.pub" "$D/tar.err"
echo
echo "PREFLIGHT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
