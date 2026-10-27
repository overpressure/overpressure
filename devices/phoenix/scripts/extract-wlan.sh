#!/bin/bash
# Extract the Wi-Fi (QCA6390, qcacld-3.0) inputs from the stock firmware. The headset's own MAC
# addresses are not extracted: the image links wlan_mac.bin from persist on the headset
# (docs/userspace.md#the-headsets-own-data).
#
# Usage (Linux/WSL, root):
#   scripts/extract-wlan.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (vendor.img, modem.bin from PICO's public OTA).
# OUTPUT_DIR receives, laid out as they are requested through the kernel's firmware loader
# (install firmware/ under /usr/lib/firmware):
#   modules/qca_cld3_qca6390.ko               WLAN host driver (module "wlan"), signed by the
#                                             stock kernel's build key; never strip it (vendor)
#   firmware/qca6390/amss20.bin, m3.bin       chip firmware that cnss2 loads over MHI (modem)
#   firmware/qca6390/bdwlan*                  board data files, all variants; cnss2 picks one
#                                             by board ID and RF front end (modem)
#   firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini  driver configuration (vendor)
#   manifest.json                             sources and sha256 of every file
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

mkdir -p "$output"/{modules,firmware/qca6390,firmware/wlan/qca_cld/qca6390}
output=$(realpath "$output")
cld=$output/firmware/wlan/qca_cld/qca6390

echo ">> vendor: host driver and its configuration"
dump "$stock/vendor.img" /lib/modules/qca_cld3_qca6390.ko "$output/modules/qca_cld3_qca6390.ko"
# PICO OS's /vendor/firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini links here.
dump "$stock/vendor.img" /etc/wifi/qca6390/WCNSS_qcom_cfg.ini "$cld/WCNSS_qcom_cfg.ini"
# The kernel enforces module signatures (MODULE_SIG_FORCE) and modversions: the module
# must be the stock build's, unmodified.
python3 - "$output/modules/qca_cld3_qca6390.ko" <<'EOF'
import sys
data = open(sys.argv[1], "rb").read()
if not data.endswith(b"~Module signature appended~\n"):
    sys.exit("qca_cld3_qca6390.ko: no appended module signature")
if b"vermagic=4.19.81-perf+ SMP preempt mod_unload modversions aarch64\0" not in data:
    sys.exit("qca_cld3_qca6390.ko: vermagic is not the stock kernel's")
if b"name=wlan\0" not in data or b"wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini" not in data:
    sys.exit("qca_cld3_qca6390.ko: not the QCA6390 qcacld-3.0 driver")
EOF

echo ">> modem: chip firmware and board data"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$stock/modem.bin" '::/image/qca6390/amss20.bin' '::/image/qca6390/m3.bin' \
    "$output/firmware/qca6390/"
mcopy -n -i "$stock/modem.bin" '::/image/qca6390/bdwlan*' "$output/firmware/qca6390/"
for f in amss20.bin m3.bin bdwlan.elf; do
    [[ -s "$output/firmware/qca6390/$f" ]] || { echo "no qca6390/$f in the modem partition" >&2; exit 1; }
done

chmod -R u+rwX,go+rX "$output"
write_manifest "$output" '{"modules/": "vendor /lib/modules", "firmware/qca6390/": "modem /image/qca6390",
    "firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini": "vendor /etc/wifi/qca6390"}'
