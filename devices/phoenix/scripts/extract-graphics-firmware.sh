#!/bin/bash
# Extract the GPU firmware the stock kernel's KGSL loads (docs/graphics.md) from the stock firmware.
#
# Usage (Linux/WSL, root):
#   scripts/extract-graphics-firmware.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (vendor.img from PICO's public OTA). OUTPUT_DIR
# receives a650_gmu.bin, a650_sqe.fw, a650_zap.mdt and a650_zap.b00-b02 (vendor /firmware) and
# manifest.json. The result is proprietary: keep it private (assets/ is ignored by Git).
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
. "$project/scripts/stock-lib.sh"
stock_dir "${1:-}" vendor.img
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
command -v debugfs >/dev/null || { echo 'debugfs (e2fsprogs) is required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }
mkdir -p "$output"
output=$(realpath "$output")

echo ">> vendor: GPU firmware"
for f in a650_gmu.bin a650_sqe.fw a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
    dump "$stock/vendor.img" "/firmware/$f" "$output/$f"
done
chmod -R u+rwX,go+rX "$output"
write_manifest "$output" '{"": "vendor /firmware"}'
