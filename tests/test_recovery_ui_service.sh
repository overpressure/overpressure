#!/bin/sh
# SPDX-License-Identifier: Unlicense
# Exercise the service's real startup decision using a simulated late DRM node.
set -eu
repo=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export P4_TEST_WORK="$work"

cat > "$work/lib.sh" <<'EOF'
OP_RUN=$P4_TEST_WORK
OP_RECOVERY_UI=$P4_TEST_WORK/ui
sleep() { test "$1" != 2147483647 || exit 8; }
log() { printf '%s\n' "$*" >> "$OP_RUN/trace"; }
wait_for() {
	test "$1" = /dev/dri/card0 && test "$2" = 60 || exit 7
	if [ ! -f "$OP_RUN/waited" ]; then
		touch "$OP_RUN/waited"
		return 1
	fi
	return 0
}
EOF
cat > "$work/ui" <<'EOF'
#!/bin/sh
echo renderer >> "$P4_TEST_WORK/trace"
EOF
chmod +x "$work/ui"
sed "s@^\\. /usr/libexec/overpressure/lib\\.sh\$@. '$work/lib.sh'@" \
	"$repo/overpressure/runtime/usr/libexec/overpressure/svc" > "$work/svc"
# Bound the command in case a future retry never advances.
timeout 3 sh "$work/svc" recovery-ui
cat > "$work/expected" <<'EOF'
Recovery UI: waiting for DRM
Recovery UI: DRM still unavailable; continuing to wait
Recovery UI: DRM node available; starting renderer
renderer
EOF
diff -u "$work/expected" "$work/trace"
echo "RECOVERY_UI_LATE_DRM_OK"
