#!/bin/bash
# Extract the audio (ADSP, sound card) inputs from the stock firmware.
#
# Usage (Linux/WSL, root):
#   scripts/extract-audio.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (vendor.img, modem.bin from PICO's public OTA).
# OUTPUT_DIR receives (docs/userspace.md#audio):
#                                      the stock kernel's build key; never strip them (vendor)
#   firmware/adsp.mdt, adsp.bNN         the audio DSP's image, loaded by the kernel's PIL (modem)
#   firmware/adspr.jsn, adspua.jsn      its protection-domain maps, for pd-mapper (modem)
#   audiopd/acdb/adsp_avs_config.acdb   the audio process's AVS configuration (vendor acdbdata)
#   audiopd/dsp/adsp/                   the audio libraries PICO OS serves it (vendor lib/rfsa/adsp)
#   calibration/*.acdb                  the calibration database of the speakers and the handset
#                                      microphone, which pico4-audiocal loads (vendor acdbdata)
#   manifest.json                       sources and sha256 of every file
# The result is proprietary: keep it private (assets/ is ignored by Git). Nothing touches the
# headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
. "$project/../../scripts/stock-lib.sh"  # overpressure's
stock_dir "${1:-}" vendor.img modem.bin
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$output"/{firmware,audiopd/acdb,audiopd/dsp/adsp,calibration}
output=$(realpath "$output")
vendor=$stock/vendor.img

echo ">> vendor: AVS configuration and DSP libraries"
dump "$vendor" /etc/acdbdata/adsp_avs_config.acdb "$output/audiopd/acdb/adsp_avs_config.acdb"
# PICO OS's audio HAL loads its calibration from the WSA8810 MTP database: the global file and
# the files of the speaker and handset devices are what the system's two paths use.
for f in Global Speaker Handset; do
    dump "$vendor" "/etc/acdbdata/wsa8810/base/MTP/MTP_base_Wsa8810_${f}_cal.acdb" \
        "$output/calibration/MTP_base_Wsa8810_${f}_cal.acdb"
done
# The audio process's share of PICO OS's ADSP library path: its audio modules, the C++ runtime
# they link, its heap module and audio configuration. The rest of the directory (vision, neural
# network, SLAM and sensor libraries, about 72 MB) belongs to processes this system does not run.
debugfs -R "rdump /lib/rfsa/adsp $work" "$vendor" >/dev/null 2>&1
for f in "$work"/adsp/capi_v2_*.so "$work"/adsp/libc++.so.1 "$work"/adsp/libc++abi.so.1 \
    "$work"/adsp/libapps_mem_heap.so "$work"/adsp/sami_audio_config.json; do
    [[ -s "$f" ]] || { echo "missing ${f#$work/} in the vendor partition" >&2; exit 1; }
    cp "$f" "$output/audiopd/dsp/adsp/"
done

echo ">> modem: the audio DSP's image and protection-domain maps"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$stock/modem.bin" '::/image/adsp.*' '::/image/adspr.jsn' '::/image/adspua.jsn' \
    "$output/firmware/"
for f in adsp.mdt adsp.b00 adspr.jsn adspua.jsn; do
    [[ -s "$output/firmware/$f" ]] || { echo "no $f in the modem partition" >&2; exit 1; }
done

chmod -R u+rwX,go+rX "$output"
write_manifest "$output" '{"firmware/": "modem /image",
    "audiopd/acdb/": "vendor /etc/acdbdata", "audiopd/dsp/adsp/": "vendor /lib/rfsa/adsp",
    "calibration/": "vendor /etc/acdbdata/wsa8810/base/MTP"}'
