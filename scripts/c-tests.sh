#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# The C tests of this repository's own C sources, with the host's compiler. The packages' tests (Monado's
# PICO 4 driver, its tools, the controller tracker) run in their builds in overpressure/packages.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$repo/devices/phoenix/src"
strict=(-O1 -Wall -Wextra -Werror)

test_c() { # NAME FLAGS... : compile into the work folder and run
	local name=$1
	shift
	echo ">> $name"
	"${CC:-cc}" "${strict[@]}" -o "$work/$name" "$@"
	"$work/$name"
}

# The boot indication's picture (pico4-splash's drawing, without the display).
cd splash
test_c test_pico4_splash -std=gnu11 test_pico4_splash.c
echo "c tests: passed"
