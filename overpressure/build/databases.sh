# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# The package databases a new base root starts with (build-userspace.sh): without --refresh, the last base root's,
# so that the image keeps what the last build resolved against; downloaded again when there are none to keep.
# Sourced by build-userspace.sh; POSIX sh, as its test runs in dash.
#
# keep_databases BASE KEY DBS: copy BASE's databases (BASE/var/lib/pacman/sync) to DBS when the base root
# finished, which its key file KEY says (the build writes it last). A base root whose first download stopped
# holds only what it got so far: its databases are never kept, and DBS stays as an earlier finished one left it.
#
# databases_complete CONF DBS: whether DBS holds a database of every repository pacman.conf CONF names, the
# project's own (overpressure-*, which the build copies in each time) aside. A missing one means: download them.

keep_databases() {
	[ -f "$2" ] && [ -d "$1/var/lib/pacman/sync" ] || return 0
	rm -rf "$3"
	cp -a "$1/var/lib/pacman/sync" "$3"
}

databases_complete() {
	[ -d "$2" ] || return 1
	# Repository names hold no spaces: one word each.
	_databases_repos=$(sed -n 's/^\[\([^]]*\)\][[:space:]]*$/\1/p' "$1")
	for _databases_repo in $_databases_repos; do
		case $_databases_repo in
		options | overpressure-*) continue ;;
		esac
		[ -s "$2/$_databases_repo.db" ] || return 1
	done
	return 0
}
