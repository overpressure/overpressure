#!/bin/bash
# Extract the Wi-Fi (QCA6390, qcacld-3.0) inputs from YOUR OWN stock backup.
#
# Usage (Linux/WSL, root):
#   scripts/extract-wlan.sh BACKUP_DIR OUTPUT_DIR
#
# BACKUP_DIR is a full EDL backup as described in docs/device-preparation.md (lun0/super.bin,
# lun0/persist.bin, lun4/modem.bin). OUTPUT_DIR receives, laid out as they are requested
# through the kernel's firmware loader (install firmware/ under /usr/lib/firmware):
#   modules/qca_cld3_qca6390.ko               WLAN host driver (module "wlan"), signed by the
#                                             stock kernel's build key; never strip it (vendor)
#   firmware/qca6390/amss20.bin, m3.bin       chip firmware that cnss2 loads over MHI (modem)
#   firmware/qca6390/bdwlan*                  board data files, all variants; cnss2 picks one
#                                             by board ID and RF front end (modem)
#   firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini  driver configuration (vendor)
#   firmware/wlan/qca_cld/qca6390/wlan_mac.bin        this headset's MAC addresses (persist)
#   manifest.json                             sources and sha256 of every file
# The result is device-specific and proprietary: keep it private (assets/ is ignored by
# Git). The MAC addresses are never printed. Nothing touches the headset.
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
mkdir -p "$output"/{modules,firmware/qca6390,firmware/wlan/qca_cld/qca6390}
output=$(realpath "$output")
cld=$output/firmware/wlan/qca_cld/qca6390

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

# dump IMAGE PATH DEST: copy one regular file out of an ext4 image.
dump() {
    debugfs -R "stat $2" "$1" 2>/dev/null | grep -q 'Type: regular' ||
        { echo "missing $2 in $1" >&2; return 1; }
    debugfs -R "dump $2 $3" "$1" >/dev/null 2>&1
}

echo ">> vendor: host driver and its configuration"
(cd "$project" && python3 -m pico4.superimg extract "$backup/lun0/super.bin" vendor "$work/vendor.img") >/dev/null
dump "$work/vendor.img" /lib/modules/qca_cld3_qca6390.ko "$output/modules/qca_cld3_qca6390.ko"
# PICO OS's /vendor/firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini links here.
dump "$work/vendor.img" /etc/wifi/qca6390/WCNSS_qcom_cfg.ini "$cld/WCNSS_qcom_cfg.ini"
rm -f "$work/vendor.img"
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

echo ">> persist: MAC addresses"
# PICO OS links /mnt/vendor/persist/qca6390/wlan_mac.bin to this file; the driver reads it
# because the ini sets read_mac_addr_from_mac_file=1.
dump "$backup/lun0/persist.bin" /wlan_mac.bin "$cld/wlan_mac.bin"
python3 - "$cld/wlan_mac.bin" <<'EOF'
import re, sys
text = open(sys.argv[1], "rb").read().decode("ascii", "replace")
if not re.fullmatch(r"(Intf\dMacAddress=[0-9A-Fa-f]{12}\n)+END\n?", text):
    sys.exit("wlan_mac.bin: unexpected format (expected IntfNMacAddress=<12 hex> lines and END)")
EOF

echo ">> modem: chip firmware and board data"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$backup/lun4/modem.bin" '::/image/qca6390/amss20.bin' '::/image/qca6390/m3.bin' \
    "$output/firmware/qca6390/"
mcopy -n -i "$backup/lun4/modem.bin" '::/image/qca6390/bdwlan*' "$output/firmware/qca6390/"
for f in amss20.bin m3.bin bdwlan.elf; do
    [[ -s "$output/firmware/qca6390/$f" ]] || { echo "no qca6390/$f in the modem partition" >&2; exit 1; }
done

chmod -R u+rwX,go+rX "$output"
chmod 0600 "$cld/wlan_mac.bin"
python3 - "$backup" "$output" > "$output/manifest.json" <<'EOF'
import hashlib, json, sys, pathlib
backup, out = map(pathlib.Path, sys.argv[1:3])
identity = json.loads((backup / "manifest.json").read_text())["identity"]
files = {}
for p in sorted(out.rglob("*")):
    if p.is_file() and p.name != "manifest.json":
        files[str(p.relative_to(out))] = hashlib.sha256(p.read_bytes()).hexdigest()
sources = {"modules/": "vendor /lib/modules", "firmware/qca6390/": "modem /image/qca6390",
           "firmware/wlan/qca_cld/qca6390/WCNSS_qcom_cfg.ini": "vendor /etc/wifi/qca6390",
           "firmware/wlan/qca_cld/qca6390/wlan_mac.bin": "persist /wlan_mac.bin"}
print(json.dumps({"schema": 1, "source_backup": str(backup),
                  "fingerprint": identity.get("fingerprint"), "sources": sources,
                  "files": files}, indent=1))
EOF
echo ">> $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' "$output/manifest.json") files in $output"
