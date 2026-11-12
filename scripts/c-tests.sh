#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# The C and C++ tests that build without a Monado checkout, with the host's compiler. The rest run in
# the pico4-monado package's build. Needs a C and a C++ compiler and Eigen 3.
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

cd monado
test_c test_pico4_ssc -std=c11 -D_GNU_SOURCE test_pico4_ssc.c pico4_ssc.c
test_c test_pico4_tracking -std=c11 -D_GNU_SOURCE test_pico4_tracking.c pico4_station.c pico4_tcam.c \
	pico4_blemcu.c pico4_passthrough.c -lm -lpthread

cd ../xr
test_c test_pico4_lens_panel -std=gnu11 test_pico4_lens_panel.c -lm
test_c test_pico4_onboarding -std=gnu11 test_pico4_onboarding.c
test_c test_pico4_setup -std=gnu11 test_pico4_setup.c

# The boot indication's picture (pico4-splash's drawing, without the display).
cd ../splash
test_c test_pico4_splash -std=gnu11 test_pico4_splash.c

cd ../xr

# The controller tracker.
cd ../monado
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -c -o "$work/tcam.o" pico4_tcam.c
objects=("$work/tcam.o")
for module in model solve init blob imu engine runner calib; do
	"${CXX:-c++}" -std=c++17 -O2 -ffp-contract=off -Wall -Wextra -Werror -isystem /usr/include/eigen3 \
		-c -o "$work/ctrack_$module.o" "pico4_ctrack_$module.cpp"
	objects+=("$work/ctrack_$module.o")
done
for name in solve init blob imu engine runner; do
	echo ">> test_pico4_ctrack_$name"
	"${CXX:-c++}" -std=c++17 -O1 -Wall -Wextra -Werror -isystem /usr/include/eigen3 -o "$work/test_$name" \
		"test_pico4_ctrack_$name.cpp" "${objects[@]}" -lpthread
	"$work/test_$name"
done
echo "c tests: passed"
