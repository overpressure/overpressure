#!/bin/sh
# SPDX-License-Identifier: Unlicense
# overpressure/build/fetch.sh against a fake server: server errors and cut answers are tried again, a refusal of the
# file fails at once, a server that stays down is given up on after FETCH_SECONDS. fetch_pacman against a fake
# pacman: a failed download is run again, any other failure fails at once.
set -eu
cd "$(dirname "$0")/.."
. overpressure/build/fetch.sh
work=$(mktemp -d)
server=""
trap 'rm -rf "$work"; [ -z "$server" ] || kill "$server" 2>/dev/null || true' EXIT
FETCH_DELAY=1
export FETCH_DELAY

# /flaky-N answers 500 to its first N requests, /cut-N cuts its first N answers short, /down always
# answers 503, /missing 404; then each answers "the file".
cat > "$work/server.py" << 'EOF'
import collections
import http.server
import os
import sys

seen = collections.Counter()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        seen[self.path] += 1
        kind, _, count = self.path[1:].partition("-")
        failing = seen[self.path] <= int(count or 0)
        body = b"the file"
        if kind == "missing":
            self.send_error(404)
        elif kind == "down" or (kind == "flaky" and failing):
            self.send_error(503 if kind == "down" else 500)
        elif kind == "cut" and failing:
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
with open(sys.argv[1] + ".new", "w") as port:
    port.write(str(httpd.server_port))
os.rename(sys.argv[1] + ".new", sys.argv[1])
httpd.serve_forever()
EOF
python3 "$work/server.py" "$work/port" &
server=$!
tries=0
until [ -s "$work/port" ]; do
	tries=$((tries + 1))
	[ "$tries" -le 50 ] || { echo "the fake server did not start" >&2; exit 1; }
	sleep 0.1
done
url=http://127.0.0.1:$(cat "$work/port")
checks=0
ok() { checks=$((checks + 1)); }

# Two server errors, then the file.
fetch "$url/flaky-2" "$work/a" 2> "$work/log"
[ "$(cat "$work/a")" = "the file" ] && [ ! -e "$work/a.part" ] && ok
[ "$(grep -c 'HTTP 500, again in' "$work/log")" = 2 ] && ok
# An answer cut short, then the whole file.
fetch "$url/cut-1" "$work/b" 2> "$work/log"
[ "$(cat "$work/b")" = "the file" ] && grep -q 'curl error 18, again in 1 s' "$work/log" && ok
# A refusal fails at once and leaves nothing behind.
if fetch "$url/missing" "$work/c" 2> "$work/log"; then exit 1; fi
[ ! -e "$work/c" ] && [ ! -e "$work/c.part" ] && ok
[ "$(cat "$work/log")" = "fetch: $url/missing: HTTP 404" ] && ok
# A server that stays down, and one that refuses the connection, are given up on.
start=$(date +%s)
if FETCH_SECONDS=3 fetch "$url/down" "$work/d" 2> "$work/log"; then exit 1; fi
[ ! -e "$work/d" ] && grep -q "fetch: $url/down: HTTP 503, given up after 3 s" "$work/log" && ok
[ $(($(date +%s) - start)) -le 5 ] && ok
kill "$server"
wait "$server" 2>/dev/null || true
server=""
if FETCH_SECONDS=2 fetch "$url/flaky-0" "$work/e" 2> "$work/log"; then exit 1; fi
grep -q "fetch: $url/flaky-0: curl error 7, given up after 2 s" "$work/log" && ok

# fetch_pacman against a fake pacman: its first N passes fail as pacman does on a cut download, then it
# installs; "conflict" fails as pacman does on anything else.
pacman_fake() {
	_passes=$(($(cat "$work/passes" 2> /dev/null || echo 0) + 1))
	echo "$_passes" > "$work/passes"
	case $1 in
	conflict)
		echo "error: failed to prepare transaction (conflicting dependencies)" >&2
		return 1
		;;
	esac
	if [ "$_passes" -le "$1" ]; then
		echo "error: failed retrieving file 'serd-0.32.6-1-aarch64.pkg.tar.zst' from holo-packages.steamos.cloud : end of response with 58720 bytes missing" >&2
		echo "warning: failed to retrieve some files" >&2
		echo "error: failed to commit transaction (download library error)" >&2
		return 1
	fi
	echo installed
}
# Two cut downloads, then the transaction.
rm -f "$work/passes"
[ "$(fetch_pacman pacman_fake 2 2> "$work/log")" = installed ] && [ "$(cat "$work/passes")" = 3 ] && ok
[ "$(grep -c 'fetch: pacman: a download failed, again in' "$work/log")" = 2 ] && grep -q 'failed retrieving file' "$work/log" && ok
# Any other failure fails at once, with pacman's own message and status.
rm -f "$work/passes"
if fetch_pacman pacman_fake conflict 2> "$work/log"; then exit 1; fi
[ "$(cat "$work/passes")" = 1 ] && grep -q 'conflicting dependencies' "$work/log" && ! grep -q 'again in' "$work/log" && ok
# A download that keeps failing is given up on after FETCH_SECONDS.
rm -f "$work/passes"
if FETCH_SECONDS=2 fetch_pacman pacman_fake 99 > /dev/null 2> "$work/log"; then exit 1; fi
grep -q 'fetch: pacman: a download failed, given up after 2 s' "$work/log" && ok
[ "$checks" = 12 ] || { echo "fetch: $checks of 12 checks passed" >&2; exit 1; }
echo 'fetch: 12 checks passed'
