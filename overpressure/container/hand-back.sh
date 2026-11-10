#!/bin/bash
# SPDX-License-Identifier: Unlicense
# hand-back.sh DIR MARKER UID GID: give UID:GID what under DIR root owns and created or changed
# after MARKER was made (its inode changed since: -cnewer, which copies and archives that keep
# old modification times cannot hide). Only DIR's own file system, links themselves (-h), never
# what other users own or what the run left alone.
set -euo pipefail
[[ $# -eq 4 && -d $1 && -e $2 && $3 =~ ^[0-9]+$ && $4 =~ ^[0-9]+$ ]] || { sed -n '3,6p' "$0" >&2; exit 2; }
find "$1" -xdev -uid 0 -cnewer "$2" -print0 | xargs -0 -r chown -h "$3:$4"
