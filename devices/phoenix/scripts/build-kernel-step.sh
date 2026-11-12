#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# Valve's kernel for the PICO 4, built here from the commit kernel/release.json pins: the window's
# "build the kernel yourself" step, and how anyone checks a release.
#
#   build-kernel-step.sh [WORKDIR]
#
# Fetches the pinned commit of overpressure/linux (its tree without history, about 280 MB) into
# WORKDIR/tree on a Linux file system, builds it with kernel/build-tree.sh, checks every file against
# the pin's SHA-256 (python -m overpressure --device phoenix kernel check) and only then puts them in
# out/kernel. A file that differs fails the step: the build image or the tree is not the release's.
# Runs in the build container (python -m overpressure container run).
set -euo pipefail
[ $# -le 1 ] || { sed -n '3,12p' "$0" >&2; exit 2; }
work=${1:-/var/tmp/pico4-kernel-build}
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
read -r repository commit < <(python3 -c \
	'import json, sys; d = json.load(open(sys.argv[1])); print(d["repository"], d["commit"])' \
	"$here/../kernel/release.json")
tree=$work/tree
if [ "$(git -C "$tree" rev-parse HEAD 2>/dev/null)" != "$commit" ]; then
	echo "fetching $repository at $commit" >&2
	rm -rf "$tree"
	mkdir -p "$tree"
	git -C "$tree" init -q
	git -C "$tree" fetch -q --depth 1 "https://github.com/$repository.git" "$commit"
	git -C "$tree" checkout -q --detach FETCH_HEAD
fi
bash "$here/../kernel/build-tree.sh" "$tree" "$work" "$work/out"
(cd "$repo" && python3 -m overpressure --device phoenix kernel check "$work/out")
mkdir -p "$repo/out/kernel"
for f in Image.gz sm8250-pico4-phoenix.dtb version config build.json; do
	install -m 0644 "$work/out/$f" "$repo/out/kernel/$f"
done
echo "out/kernel: the pinned release's files, built here" >&2
