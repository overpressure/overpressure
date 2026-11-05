#!/bin/bash
# SPDX-License-Identifier: Unlicense
# Extract the GPU firmware the kernel loads (docs/graphics.md) and the video codec's
# (Venus; docs/session.md#steam-link) from the stock firmware.
#
# Usage (Linux/WSL, root):
#   scripts/extract-graphics-firmware.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (vendor.img and modem.bin from PICO's public
# OTA). OUTPUT_DIR receives a650_gmu.bin, a650_sqe.fw, a650_zap.mdt and a650_zap.b00-b02 (vendor
# /firmware), venus.mdt and venus.bNN (modem /image) and manifest.json. The result is
# proprietary: keep it private (assets/ is ignored by Git).
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
. "$project/../../scripts/stock-lib.sh"  # overpressure's
stock_dir "${1:-}" vendor.img modem.bin
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }
mkdir -p "$output"
output=$(realpath "$output")
export MTOOLS_SKIP_CHECK=1

echo ">> vendor: GPU firmware"
for f in a650_gmu.bin a650_sqe.fw a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
    dump "$stock/vendor.img" "/firmware/$f" "$output/$f"
done
echo ">> modem: the video codec's image (Venus)"
mcopy -n -i "$stock/modem.bin" '::/image/venus.*' "$output/"
[[ -s "$output/venus.mdt" ]] || { echo "no venus.mdt in the modem partition" >&2; exit 1; }
chmod -R u+rwX,go+rX "$output"
write_manifest "$output" '{"a650_": "vendor /firmware", "venus.": "modem /image"}'
