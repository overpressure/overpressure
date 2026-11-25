#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# A headset's kernel built here from the commit its kernel/release.json pins: how anyone checks a release
# (overpressure.kernelpin). A headset's scripts/build-kernel-step.sh calls it with its folder:
#
#   overpressure/build/build-kernel-step.sh HEADSET [WORKDIR]
#
# Fetches the pinned commit of overpressure/linux (its tree without history) into WORKDIR/tree on a Linux file
# system, builds it with the headset's kernel/build-tree.sh, checks the files against the pin's SHA-256 (python -m
# overpressure --device CODENAME kernel check --built) and only then puts them, with build.json, in out/kernel. A
# file that differs fails the step: the build image or the tree is not the release's. Image.gz differs from the
# release's by its module-signing key, which Valve's configuration generates in each build.
# Runs in the build container (python -m overpressure container run).
set -euo pipefail
[ $# -ge 1 ] && [ $# -le 2 ] || { sed -n '3,12p' "$0" >&2; exit 2; }
headset=$(cd "$1" && pwd)
read -r codename < <(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["codename"])' "$headset/headset.json")
work=${2:-/var/tmp/$codename-kernel-build}
repo=$(cd "$(dirname "$0")/../.." && pwd)
read -r repository commit < <(python3 -c \
	'import json, sys; d = json.load(open(sys.argv[1])); print(d["repository"], d["commit"])' \
	"$headset/kernel/release.json")
mapfile -t files < <(python3 -c 'import json, sys; print("\n".join(json.load(open(sys.argv[1]))["files"]))' \
	"$headset/kernel/release.json")
tree=$work/tree
if [ "$(git -C "$tree" rev-parse HEAD 2>/dev/null)" != "$commit" ]; then
	echo "fetching $repository at $commit" >&2
	rm -rf "$tree"
	mkdir -p "$tree"
	git -C "$tree" init -q
	git -C "$tree" fetch -q --depth 1 "https://github.com/$repository.git" "$commit"
	git -C "$tree" checkout -q --detach FETCH_HEAD
fi
bash "$headset/kernel/build-tree.sh" "$tree" "$work" "$work/out"
(cd "$repo" && python3 -m overpressure --device "$codename" kernel check --built "$work/out")
mkdir -p "$repo/out/kernel"
for f in "${files[@]}" build.json; do
	install -m 0644 "$work/out/$f" "$repo/out/kernel/$f"
done
echo "out/kernel: the pinned release's files, built here" >&2
