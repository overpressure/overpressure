#!/bin/sh
# SPDX-License-Identifier: Unlicense
# overpressure/build/databases.sh as build-userspace.sh runs it for a new base root: a base root whose first download stopped
# keeps no databases and the next run downloads them; a finished one's are kept; a set that lacks a repository's
# database is downloaded again.
set -eu
cd "$(dirname "$0")/.."
. overpressure/build/databases.sh
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0
check() { # check WHAT EXPECTED ACTUAL
	if [ "$2" = "$3" ]; then
		echo "ok: $1"
	else
		echo "FAIL: $1: expected $2, got $3"
		failures=$((failures + 1))
	fi
}

cat > "$work/pacman.conf" << 'EOF'
[options]
Architecture = aarch64

[overpressure-phoenix]
Server = file:///var/cache/overpressure/phoenix

[core]
Server = https://example.invalid/$repo/os/$arch

[extra]
Server = https://example.invalid/$repo/os/$arch

[deckard-arch-hotfixes-release-0.3]
Server = https://example.invalid/hotfixes
EOF

# new_base BUILD_ROOT: what build-userspace.sh does for a new base root without --refresh; prints -Sy or -S.
new_base() {
	keep_databases "$1/base" "$1/base.key" "$1/base-databases"
	rm -f "$1/base.key"
	if databases_complete "$work/pacman.conf" "$1/base-databases"; then echo -S; else echo -Sy; fi
}
# base ROOT DB...: a base root with these databases.
base() {
	_root=$1
	shift
	mkdir -p "$_root/base/var/lib/pacman/sync"
	for _db in "$@"; do echo "$_db" > "$_root/base/var/lib/pacman/sync/$_db.db"; done
}

# The report: the first build's download stopped after core; the base root has no key. The next run downloads.
root=$work/stopped
base "$root" core
check "a base root whose download stopped: its databases are not kept" absent \
	"$([ -e "$root/base-databases" ] && echo kept || echo absent)"
check "after a stopped download: the next run downloads the databases" -Sy "$(new_base "$root")"
# That run finished: its base root holds them all, with its key.
base "$root" core extra deckard-arch-hotfixes-release-0.3
echo key > "$root/base.key"
check "a finished base root: its databases are kept and the next run downloads none" -S "$(new_base "$root")"
check "the kept databases are the finished base root's" "core extra deckard-arch-hotfixes-release-0.3" \
	"$(cat "$root/base-databases/core.db" "$root/base-databases/extra.db" \
		"$root/base-databases/deckard-arch-hotfixes-release-0.3.db" | tr '\n' ' ' | sed 's/ $//')"

# A later base root's download stopped again: the earlier finished set stays, and is enough.
base "$root" core
rm -f "$root/base/var/lib/pacman/sync/extra.db" "$root/base/var/lib/pacman/sync/deckard-arch-hotfixes-release-0.3.db"
check "a stopped download after a finished one: the finished databases are used" -S "$(new_base "$root")"

# A kept set without one of pacman.conf's repositories (a repository added since) is downloaded again.
root=$work/missing
base "$root" core extra
echo key > "$root/base.key"
check "a repository without its database: the databases are downloaded" -Sy "$(new_base "$root")"

# An empty database file is a missing one.
root=$work/empty
base "$root" core extra deckard-arch-hotfixes-release-0.3
: > "$root/base/var/lib/pacman/sync/extra.db"
echo key > "$root/base.key"
check "an empty database: the databases are downloaded" -Sy "$(new_base "$root")"

# No base root before: nothing to keep, a download.
check "the first base root: the databases are downloaded" -Sy "$(new_base "$work/first")"

[ "$failures" = 0 ] || { echo "$failures failed"; exit 1; }
echo "all passed"
