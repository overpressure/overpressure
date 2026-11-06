#!/bin/sh
# SPDX-License-Identifier: Unlicense
# Start the overpressure installer on macOS: double-click this file (the first time: right-click,
# Open). Terminal runs install.sh from this folder, which fetches the pinned uv and opens the window.
set -eu
cd "$(dirname "$0")"
exec /bin/sh ./install.sh
