# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# Opt-in machine output on fd 3. The recovery caller owns and drains the pipe;
# normal build stdout and stderr retain their existing format.
build_event() {
	[ "${OVERPRESSURE_BUILD_PROGRESS:-0}" = 1 ] || return 0
	printf '%s\n' "$*" >&3
}

# The tool still opens its ordinary input file (zstd uses its size for the
# frame header). Report offsets only for descriptors actually on that file.
build_watch_input() {
	local watch_pid=$1 watch_file=$2 watch_phase=$3 watch_size=$4
	local watch_fd watch_done watch_seen=0
	while kill -0 "$watch_pid" 2>/dev/null; do
		for watch_fd in /proc/"$watch_pid"/fd/*; do
			[ "$watch_fd" -ef "$watch_file" ] || continue
			watch_done=$(awk '/^pos:/ {print $2; exit}' \
				"/proc/$watch_pid/fdinfo/${watch_fd##*/}" 2>/dev/null)
			if [ "${watch_done:-0}" -gt "$watch_seen" ] && [ "$watch_done" -le "$watch_size" ]; then
				build_event "$watch_phase" running "$watch_done" "$watch_size" bytes local-processed-bytes
				watch_seen=$watch_done
			fi
			break
		done
		sleep 0.5
	done
}

build_meter_input() {
	local meter_phase=$1 meter_file=$2 meter_size meter_worker meter_watcher meter_rc=0
	shift 2
	if [ "${OVERPRESSURE_BUILD_PROGRESS:-0}" != 1 ]; then
		"$@"
		return
	fi
	meter_size=$(stat -c %s "$meter_file") || return
	build_event "$meter_phase" running 0 "$meter_size" bytes local-processed-bytes
	"$@" &
	meter_worker=$!
	build_watch_input "$meter_worker" "$meter_file" "$meter_phase" "$meter_size" >/dev/null &
	meter_watcher=$!
	wait "$meter_worker" || meter_rc=$?
	wait "$meter_watcher" || true
	return "$meter_rc"
}
