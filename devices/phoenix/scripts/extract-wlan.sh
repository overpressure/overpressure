#!/bin/bash
# Extract the Wi-Fi (QCA6390, ath11k) inputs from the stock firmware. The headset's own MAC
# addresses are not extracted: pico4-wlan-address reads wlan_mac.bin from persist on the headset
# (docs/userspace.md#the-headsets-own-data).
#
# Usage (Linux/WSL):
#   scripts/extract-wlan.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (modem.bin from PICO's public OTA).
# OUTPUT_DIR receives:
#   firmware/qca6390/amss20.bin, m3.bin       chip firmware (modem)
#   firmware/qca6390/bdwlanq.elf              the headset's board data, ath11k's board.bin (modem)
#   manifest.json                             sources and sha256 of every file
# The result is proprietary: keep it private (assets/ is ignored by Git). Nothing touches the
# headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
. "$project/../../scripts/stock-lib.sh"  # overpressure's
stock_dir "${1:-}" modem.bin
output=${2:?specify an output directory}
command -v mcopy >/dev/null || { echo 'mcopy (mtools) is required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

mkdir -p "$output/firmware/qca6390"
output=$(realpath "$output")

echo ">> modem: chip firmware and board data"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$stock/modem.bin" '::/image/qca6390/amss20.bin' '::/image/qca6390/m3.bin' \
    '::/image/qca6390/bdwlanq.elf' "$output/firmware/qca6390/"
for f in amss20.bin m3.bin bdwlanq.elf; do
    [[ -s "$output/firmware/qca6390/$f" ]] || { echo "no qca6390/$f in the modem partition" >&2; exit 1; }
done

chmod -R u+rwX,go+rX "$output"
write_manifest "$output" '{"firmware/qca6390/": "modem /image/qca6390"}'
