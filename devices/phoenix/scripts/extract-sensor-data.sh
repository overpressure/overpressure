#!/bin/bash
# Extract the head-IMU sensor stack and the lens and controller data from the stock firmware
# (docs/xr.md). The headset's own factory data (registry, calibration) is not extracted: the image
# links it from persist on the headset (docs/userspace.md#the-headsets-own-data).
#
# Usage (Linux/WSL, root):
#   scripts/extract-sensor-data.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (system.img, vendor.img, modem.bin from PICO's
# public OTA). OUTPUT_DIR receives:
#   firmware/slpi.mdt, slpi.bNN, slpi*.jsn   sensor DSP image (modem)
#   sensors/config/*.json                     sensor driver config (vendor)
#   sensors/sns_reg.conf                      registry layout (vendor)
#   calibration/pxrconfig/SDK_Lens_ConfigFile.txt  stock lens configuration
#                                             (chromatic aberration terms; system)
#   calibration/controllers/led-models.json   nominal LED rings of both controllers
#                                             (controller tracking library; system)
#   calibration/controllers/aim-to-grip.json  grip pose of each controller in its aim
#                                             frame (tracking service; system)
#   calibration/controllers/lever-arm.json    lever arm PICO OS adds to each aim pose
#                                             (controller tracking client; system)
#   rendermodels/pico4_controller_*/          SteamVR render models of both controllers
#                                             (PICO OS's controller models; system)
#   manifest.json                             sources and sha256 of every file
# The result is proprietary: keep it private (assets/ is ignored by Git). Nothing touches the
# headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
. "$project/../../scripts/stock-lib.sh"  # overpressure's
repo=$(cd "$project/../.." && pwd)
stock_dir "${1:-}" system.img vendor.img modem.bin
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$output"/{firmware,sensors,calibration}
output=$(realpath "$output")

echo ">> vendor: sensor config"
dump "$stock/vendor.img" /etc/sensors/config "$work"
mv "$work/config" "$output/sensors/config"
dump "$stock/vendor.img" /etc/sensors/sns_reg_config "$output/sensors/sns_reg.conf"

echo ">> system: lens configuration, controller LED models, aim-to-grip transforms, lever arms, controller models"
system=$stock/system.img
mkdir -p "$output/calibration/pxrconfig"
dump "$system" /system/etc/pxrconfig/config/SDK_Lens_ConfigFile.txt \
    "$output/calibration/pxrconfig/SDK_Lens_ConfigFile.txt"
dump "$system" /system/lib64/libpico_controller_tracking.pxr.so "$work/libpico_controller_tracking.pxr.so"
(cd "$repo" && python3 -m devices.phoenix.tooling.ledmodel "$work/libpico_controller_tracking.pxr.so" "$output/calibration/controllers/led-models.json")
dump "$system" /system/lib64/libpvrtrackingservice.so "$work/libpvrtrackingservice.so"
(cd "$repo" && python3 -m devices.phoenix.tooling.aimgrip "$work/libpvrtrackingservice.so" "$output/calibration/controllers/aim-to-grip.json")
dump "$system" /system/lib64/libpico_controller_tracking.client.so "$work/libpico_controller_tracking.client.so"
(cd "$repo" && python3 -m devices.phoenix.tooling.leverarm "$work/libpico_controller_tracking.client.so" \
    "$output/calibration/controllers/lever-arm.json")
dump "$system" /system/pre_resource/data/misc/user/controller/Neo3Phx "$work/models"
controllers=$output/calibration/controllers
(cd "$repo" && python3 -m devices.phoenix.tooling.controllermodel "$work/models/Neo3Phx" "$output/rendermodels" \
    --aim-to-grip "$controllers/aim-to-grip.json" --check "$controllers/led-models.json" "$controllers/lever-arm.json")

echo ">> modem: sensor DSP firmware"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$stock/modem.bin" '::/image/slpi*' "$output/firmware/"
[[ -s "$output/firmware/slpi.mdt" ]] || { echo 'no slpi.mdt in the modem partition' >&2; exit 1; }

chmod -R u+rwX,go+rX "$output"
write_manifest "$output" '{"firmware/": "modem /image", "sensors/": "vendor /etc/sensors",
    "calibration/": "system", "rendermodels/": "system /system/pre_resource"}'
