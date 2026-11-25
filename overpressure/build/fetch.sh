# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# fetch URL FILE: download URL into FILE (through FILE.part, so FILE is whole or absent) and ride out a short
# outage of the server: an answer of 408, 429 or 5xx and a connection that fails or drops are tried again,
# 5 s later at first and then up to 30 s apart, for FETCH_SECONDS (180) in all. Any other answer fails at
# once. Each failure says the URL and what went wrong. Sourced by build-on-headset.sh and build-userspace.sh:
# POSIX sh, as the maintenance stage 1 has no bash; curl takes ALL_PROXY from the environment.
#
# fetch_pacman CMD...: run CMD, a pacman that downloads (possibly through a chroot), and run it again on the
# same schedule when it failed on a download: a package or database that pacman's only server cut short or
# answered with an error ("failed retrieving file", "failed to synchronize all databases"). pacman downloads
# every package before it changes anything and keeps the whole ones in its cache, so another pass changes
# nothing twice and fetches only what is still missing. Any other failure fails at once, with pacman's status.

fetch() {
	_fetch_url=$1 _fetch_file=$2
	_fetch_end=$(($(date +%s) + ${FETCH_SECONDS:-180}))
	_fetch_delay=${FETCH_DELAY:-5}
	while :; do
		# Without -f, curl exits 0 on an HTTP error too: the answer's code says what it was.
		_fetch_status=0
		_fetch_code=$(curl -sSL -o "$_fetch_file.part" -w '%{http_code}' "$_fetch_url") || _fetch_status=$?
		case $_fetch_status:$_fetch_code in
		0:[123]??)
			mv "$_fetch_file.part" "$_fetch_file"
			return 0
			;;
		0:408 | 0:429 | 0:5??) _fetch_why="HTTP $_fetch_code" ;;
		0:*)
			rm -f "$_fetch_file.part"
			echo "fetch: $_fetch_url: HTTP $_fetch_code" >&2
			return 1
			;;
		# curl's failures of the connection: name resolution, connecting, TLS, an answer cut short or
		# dropped, a timeout, a reset HTTP/2 stream.
		6:* | 7:* | 18:* | 28:* | 35:* | 52:* | 55:* | 56:* | 92:*) _fetch_why="curl error $_fetch_status" ;;
		*)
			rm -f "$_fetch_file.part"
			echo "fetch: $_fetch_url: curl error $_fetch_status" >&2
			return 1
			;;
		esac
		rm -f "$_fetch_file.part"
		if [ $(($(date +%s) + _fetch_delay)) -gt "$_fetch_end" ]; then
			echo "fetch: $_fetch_url: $_fetch_why, given up after ${FETCH_SECONDS:-180} s" >&2
			return 1
		fi
		echo "fetch: $_fetch_url: $_fetch_why, again in $_fetch_delay s" >&2
		sleep "$_fetch_delay"
		_fetch_delay=$((_fetch_delay * 2))
		[ "$_fetch_delay" -le 30 ] || _fetch_delay=30
	done
}

fetch_pacman() {
	_fetch_end=$(($(date +%s) + ${FETCH_SECONDS:-180}))
	_fetch_delay=${FETCH_DELAY:-5}
	_fetch_log=$(mktemp)
	while :; do
		# pacman's errors go to standard error: kept to tell a failed download, then shown as they came.
		_fetch_status=0
		"$@" 2> "$_fetch_log" || _fetch_status=$?
		cat "$_fetch_log" >&2
		if [ "$_fetch_status" = 0 ]; then
			rm -f "$_fetch_log"
			return 0
		fi
		if ! grep -qE 'failed retrieving file|failed to retrieve some files|failed to synchronize all databases' \
			"$_fetch_log"; then
			rm -f "$_fetch_log"
			return "$_fetch_status"
		fi
		if [ $(($(date +%s) + _fetch_delay)) -gt "$_fetch_end" ]; then
			echo "fetch: pacman: a download failed, given up after ${FETCH_SECONDS:-180} s" >&2
			rm -f "$_fetch_log"
			return "$_fetch_status"
		fi
		echo "fetch: pacman: a download failed, again in $_fetch_delay s" >&2
		sleep "$_fetch_delay"
		_fetch_delay=$((_fetch_delay * 2))
		[ "$_fetch_delay" -le 30 ] || _fetch_delay=30
	done
}
