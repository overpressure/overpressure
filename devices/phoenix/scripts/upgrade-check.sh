#!/bin/bash
# SPDX-License-Identifier: Unlicense
# The upgrade check (docs/development.md#upgrade-check): would the image still build on Valve's newest
# packages? It needs none of PICO OS's files, so it runs anywhere the build container does
# (weekly in CI, .github/workflows/upgrade.yml):
#
#   python -m overpressure container run -- bash devices/phoenix/scripts/upgrade-check.sh WORKDIR OUTPUT_DIR
#
# 1. The userspace build with the newest package databases and a new lock (build-userspace.sh
#    --refresh --update-lock) in WORKDIR/userspace: our packages build against Valve's newest, our
#    patches apply, nothing is left unresolved. What moved since the pinned packages.lock goes into the
#    summary.
# Valve's kernel is followed in overpressure/linux: Valve's new commits move up its layers by pull
# request, where its CI builds them.
#
# OUTPUT_DIR/upgrade-check.md is the summary. The exit status is 1 when a build failed. That the
# session still runs on the result is the headset's check, before a release (docs/releasing.md).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)    # devices/phoenix/scripts
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
	# The lock of the overpressure/packages commit the build took (userspace/packages.json).
	python3 "$here/upstream.py" lock-diff "$work/userspace/project/packages/headsets/phoenix/packages.lock" \
		"$out/userspace/packages.lock" >> "$summary"
fi

cat "$summary"
exit "$failed"
