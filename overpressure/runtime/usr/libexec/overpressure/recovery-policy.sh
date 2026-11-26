# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# Return a recovery reason, or nothing while stage 1 can keep running.
# Arguments: uptime, connect deadline, test deadline, test mode, reachable, acknowledged,
# the uptime of the host's last contact (0 or empty: none), and an optional flag
# for an explicit UI request to disable the test deadline. The test deadline (the
# maximum runtime) counts from Linux start, or from that contact when the host renews it
# (the dev loop does with every command), so it ends abandoned runs, not sessions in use.
recovery_reason() {
	if [ "$4" = 1 ] && [ "${8:-0}" != 1 ] && [ "$(($1 - ${7:-0}))" -ge "$3" ]; then
		if [ "${7:-0}" -gt 0 ]; then
			echo "maximum runtime: no host contact for ${3}s"
		else
			echo "hardware test exceeded ${3}s"
		fi
	elif [ "$2" -gt 0 ] && [ "$1" -ge "$2" ]; then
		if [ "$4" = 1 ] && [ "$6" != 1 ]; then
			echo "hardware runner did not authenticate within ${2}s"
		elif [ "$4" != 1 ] && [ "$5" != 1 ]; then
			echo "no usable USB connection within ${2}s"
		fi
	fi
}

# Explicit Recovery UI settings can extend a deadline or disable it. The
# default and invalid settings retain the image's configured deadline.
recovery_limit() {
	case "$2" in
	0) echo 0 ;;
	600 | 900 | 1800 | 3600 | 7200 | 14400)
		if [ "$2" -gt "$1" ]; then echo "$2"; else echo "$1"; fi ;;
	*) echo "$1" ;;
	esac
}
