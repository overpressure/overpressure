#!/bin/bash
# Extract the head-IMU sensor stack inputs from YOUR OWN stock backup (XR.md).
#
# Usage (Linux/WSL, root):
#   scripts/extract-sensor-data.sh BACKUP_DIR OUTPUT_DIR
#
# BACKUP_DIR is a full EDL backup as described in DEVICE.md (lun0/super.bin,
# lun0/persist.bin, lun4/modem.bin). OUTPUT_DIR receives:
#   firmware/slpi.mdt, slpi.bNN, slpi*.jsn   sensor DSP image (modem partition)
#   sensors/config/*.json                     sensor driver config (vendor)
#   sensors/sns_reg.conf                      registry layout (vendor)
#   sensors/registry/, sns_reg_version        this headset's registry with its
#                                             factory calibration (persist)
#   calibration/pvr/                          factory camera/IMU/lens calibration
#   calibration/pxrconfig/SDK_Lens_ConfigFile.txt  stock lens configuration
#                                             (chromatic aberration terms; system)
#   manifest.json                             sources and sha256 of every file
# The result is device-specific and partly proprietary: keep it private
# (assets/ is ignored by Git). Nothing touches the headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
backup=$(realpath "${1:?specify the backup directory}")
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
for f in lun0/super.bin lun0/persist.bin lun4/modem.bin manifest.json; do
    [[ -s "$backup/$f" ]] || { echo "missing $backup/$f" >&2; exit 1; }
done
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$output"/{firmware,sensors,calibration}
output=$(realpath "$output")

# Files must match the backup manifest before anything is read from them.
python3 - "$backup" <<'EOF'
import hashlib, json, sys, pathlib
root = pathlib.Path(sys.argv[1])
files = json.loads((root / "manifest.json").read_text())["files"]
for name in ("lun0/super.bin", "lun0/persist.bin", "lun4/modem.bin"):
    h = hashlib.sha256()
    with open(root / name, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    if h.hexdigest() != files[name]["sha256"]:
        sys.exit(f"{name}: sha256 does not match the backup manifest")
EOF

# dump IMAGE PATH DEST: copy one file or directory out of an ext4 image.
dump() {
    local kind
    kind=$(debugfs -R "stat $2" "$1" 2>/dev/null | sed -n 's/.*Type: \([a-z]*\).*/\1/p' | head -n 1)
    case "$kind" in
    directory) mkdir -p "$3" && debugfs -R "rdump $2 $3" "$1" >/dev/null 2>&1 ;;
    regular) debugfs -R "dump $2 $3" "$1" >/dev/null 2>&1 ;;
    *) echo "missing $2 in $1" >&2; return 1 ;;
    esac
}

echo ">> vendor: sensor config"
(cd "$project" && python3 -m tools.superimg extract "$backup/lun0/super.bin" vendor "$work/vendor.img") >/dev/null
dump "$work/vendor.img" /etc/sensors/config "$work"
mv "$work/config" "$output/sensors/config"
dump "$work/vendor.img" /etc/sensors/sns_reg_config "$output/sensors/sns_reg.conf"
rm -f "$work/vendor.img"

echo ">> system: lens configuration"
(cd "$project" && python3 -m tools.superimg extract "$backup/lun0/super.bin" system "$work/system.img") >/dev/null
mkdir -p "$output/calibration/pxrconfig"
dump "$work/system.img" /system/etc/pxrconfig/config/SDK_Lens_ConfigFile.txt \
    "$output/calibration/pxrconfig/SDK_Lens_ConfigFile.txt"
rm -f "$work/system.img"

echo ">> persist: registry and factory calibration"
dump "$backup/lun0/persist.bin" /sensors/registry/registry "$work"
mv "$work/registry" "$output/sensors/registry"
dump "$backup/lun0/persist.bin" /sensors/registry/sns_reg_version "$output/sensors/sns_reg_version"
dump "$backup/lun0/persist.bin" /pvr "$output/calibration"

echo ">> modem: sensor DSP firmware"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$backup/lun4/modem.bin" '::/image/slpi*' "$output/firmware/"
[[ -s "$output/firmware/slpi.mdt" ]] || { echo 'no slpi.mdt in the modem partition' >&2; exit 1; }

chmod -R u+rwX,go+rX "$output"
python3 - "$backup" "$output" > "$output/manifest.json" <<'EOF'
import hashlib, json, sys, pathlib
backup, out = map(pathlib.Path, sys.argv[1:3])
identity = json.loads((backup / "manifest.json").read_text())["identity"]
files = {}
for p in sorted(out.rglob("*")):
    if p.is_file() and p.name != "manifest.json":
        files[str(p.relative_to(out))] = hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({"schema": 1, "source_backup": str(backup),
                  "fingerprint": identity.get("fingerprint"), "files": files}, indent=1))
EOF
echo ">> $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' "$output/manifest.json") files in $output"
