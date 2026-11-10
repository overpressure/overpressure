#!/bin/bash
# SPDX-License-Identifier: Unlicense
# The upgrade check (docs/development.md#upgrade-check): would the image still build on Valve's newest
# packages and kernel? It needs none of PICO OS's files, so it runs anywhere the build container does
# (weekly in CI, .github/workflows/upgrade.yml):
#
#   python -m overpressure container run -- bash devices/phoenix/scripts/upgrade-check.sh WORKDIR OUTPUT_DIR
#
# 1. The userspace build with the newest package databases and a new lock (build-userspace.sh
#    --refresh --update-lock) in WORKDIR/userspace: our packages build against Valve's newest, our
#    patches apply, nothing is left unresolved. What moved since userspace/packages.lock goes into the
#    summary.
# 2. Valve's current linux-72-deckard against the pinned one. A newer one whose source package is
#    published is built with our patches, configuration and drivers (build-kernel.sh with - for the
#    stock images: no camera table, no device tree).
#
# OUTPUT_DIR/upgrade-check.md is the summary. The exit status is 1 when a build failed. That the
# session still runs on the result is the headset's check, before a release (docs/releasing.md).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)    # devices/phoenix/scripts
phoenix=$(cd "$here/.." && pwd)
work=${1:?specify a work directory on a Linux file system}
out=${2:?specify an output directory}
mkdir -p "$work" "$out"
summary=$out/upgrade-check.md
failed=0

{
	echo "# Upgrade check"
	echo
	echo "## Userspace"
	echo
} > "$summary"
if bash "$here/build-userspace.sh" "$work/userspace" "$out/userspace" --refresh --update-lock > "$out/userspace.log" 2>&1; then
	echo "Builds with the newest packages." >> "$summary"
else
	failed=1
	{
		echo "**The build fails** with the newest packages; the log's end:"
		echo
		echo '```'
		tail -n 30 "$out/userspace.log"
		echo '```'
	} >> "$summary"
fi
echo >> "$summary"
if [ -f "$out/userspace/packages.lock" ]; then
	python3 "$here/upstream.py" lock-diff "$phoenix/userspace/packages.lock" "$out/userspace/packages.lock" >> "$summary"
fi

{
	echo
	echo "## Kernel"
	echo
} >> "$summary"
kernel_pinned="" kernel_current="" kernel_source=""
if versions=$(python3 "$here/upstream.py" kernel "$phoenix/userspace/pacman.conf" "$here/build-kernel-step.sh"); then
	eval "$versions"
fi
if [ -z "$kernel_current" ]; then
	failed=1
	echo "**Valve's repository could not be read.**" >> "$summary"
elif [ "$kernel_current" = "$kernel_pinned" ]; then
	echo "linux-72-deckard $kernel_pinned, as pinned." >> "$summary"
elif [ -z "$kernel_source" ]; then
	echo "linux-72-deckard $kernel_current is out (pinned: $kernel_pinned); Valve has not published its source package yet." >> "$summary"
else
	package=$work/kernel-source/$(basename "${kernel_source//%2B/+}")
	mkdir -p "$work/kernel-source"
	if { [ -f "$package" ] || curl -fsSL --retry 3 -o "$package" "$kernel_source"; } &&
		bash "$phoenix/kernel/build-kernel.sh" "$package" - - "$work/kernel" "$out/kernel" > "$out/kernel.log" 2>&1; then
		echo "linux-72-deckard $kernel_current (pinned: $kernel_pinned) builds with our patches, configuration and drivers." >> "$summary"
	else
		failed=1
		{
			echo "**linux-72-deckard $kernel_current (pinned: $kernel_pinned) does not build** with our patches; the log's end:"
			echo
			echo '```'
			tail -n 30 "$out/kernel.log" 2>/dev/null
			echo '```'
		} >> "$summary"
	fi
fi
cat "$summary"
exit "$failed"
