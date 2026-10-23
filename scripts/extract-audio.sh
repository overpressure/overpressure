#!/bin/bash
# Extract the audio (ADSP, sound card) inputs from YOUR OWN stock backup.
#
# Usage (Linux/WSL, root):
#   scripts/extract-audio.sh BACKUP_DIR OUTPUT_DIR
#
# BACKUP_DIR is a full EDL backup as described in docs/device-preparation.md (lun0/super.bin,
# lun4/modem.bin). OUTPUT_DIR receives (docs/userspace.md#audio):
#   modules/audio_*.ko, modules.dep    PICO OS's audio modules and their dependencies, signed by
#                                      the stock kernel's build key; never strip them (vendor)
#   firmware/adsp.mdt, adsp.bNN         the audio DSP's image, loaded by the kernel's PIL (modem)
#   firmware/adspr.jsn, adspua.jsn      its protection-domain maps, for pd-mapper (modem)
#   audiopd/acdb/adsp_avs_config.acdb   the audio process's AVS configuration (vendor acdbdata)
#   audiopd/dsp/adsp/                   the audio libraries PICO OS serves it (vendor lib/rfsa/adsp)
#   manifest.json                       sources and sha256 of every file
# The result is device-specific and proprietary: keep it private (assets/ is ignored by Git).
# Nothing touches the headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
backup=$(realpath "${1:?specify the backup directory}")
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
for f in lun0/super.bin lun4/modem.bin manifest.json; do
    [[ -s "$backup/$f" ]] || { echo "missing $backup/$f" >&2; exit 1; }
done
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$output"/{modules,firmware,audiopd/acdb,audiopd/dsp/adsp}
output=$(realpath "$output")

# Files must match the backup manifest before anything is read from them.
python3 - "$backup" <<'EOF'
import hashlib, json, sys, pathlib
root = pathlib.Path(sys.argv[1])
files = json.loads((root / "manifest.json").read_text())["files"]
for name in ("lun0/super.bin", "lun4/modem.bin"):
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

echo ">> vendor: modules, AVS configuration and DSP libraries"
(cd "$project" && python3 -m pico4.superimg extract "$backup/lun0/super.bin" vendor "$work/vendor.img") >/dev/null
dump "$work/vendor.img" /lib/modules/modules.dep "$work/modules.dep"
# PICO OS's list (vendor init.target.rc) and everything it depends on.
python3 - "$work/modules.dep" > "$work/modules.txt" <<'EOF'
import sys
deps = {}
for line in open(sys.argv[1]):
    name, _, rest = line.partition(":")
    deps[name.rsplit("/", 1)[-1][:-3]] = [p.rsplit("/", 1)[-1][:-3] for p in rest.split()]
wanted = """audio_q6_pdr audio_q6_notifier audio_snd_event audio_apr audio_adsp_loader audio_q6
audio_native audio_usf audio_pinctrl_wcd audio_pinctrl_lpi audio_swr audio_platform audio_hdmi
audio_stub audio_wcd_core audio_wsa881x audio_bolero_cdc audio_wsa_macro audio_va_macro
audio_rx_macro audio_tx_macro audio_wcd938x audio_wcd938x_slave audio_machine_kona""".split()
names, todo = set(), list(wanted)
while todo:
    m = todo.pop()
    if m not in deps:
        sys.exit(f"modules.dep has no {m}")
    if m not in names:
        names.add(m)
        todo += deps[m]
print("\n".join(sorted(names)))
EOF
while read -r m; do
    dump "$work/vendor.img" "/lib/modules/$m.ko" "$output/modules/$m.ko"
done < "$work/modules.txt"
grep -E '^/vendor/lib/modules/audio_' "$work/modules.dep" > "$output/modules/modules.dep"
dump "$work/vendor.img" /etc/acdbdata/adsp_avs_config.acdb "$output/audiopd/acdb/adsp_avs_config.acdb"
# The audio process's share of PICO OS's ADSP library path: its audio modules, the C++ runtime
# they link, its heap module and audio configuration. The rest of the directory (vision, neural
# network, SLAM and sensor libraries, about 72 MB) belongs to processes this system does not run.
debugfs -R "rdump /lib/rfsa/adsp $work" "$work/vendor.img" >/dev/null 2>&1
for f in "$work"/adsp/capi_v2_*.so "$work"/adsp/libc++.so.1 "$work"/adsp/libc++abi.so.1 \
    "$work"/adsp/libapps_mem_heap.so "$work"/adsp/sami_audio_config.json; do
    [[ -s "$f" ]] || { echo "missing ${f#$work/} in the vendor partition" >&2; exit 1; }
    cp "$f" "$output/audiopd/dsp/adsp/"
done
rm -f "$work/vendor.img"
# The kernel enforces module signatures (MODULE_SIG_FORCE) and modversions: the modules must be
# the stock build's, unmodified.
python3 - "$output/modules" <<'EOF'
import pathlib, sys
for ko in sorted(pathlib.Path(sys.argv[1]).glob("*.ko")):
    data = ko.read_bytes()
    if not data.endswith(b"~Module signature appended~\n"):
        sys.exit(f"{ko.name}: no appended module signature")
    if b"vermagic=4.19.81-perf+ SMP preempt mod_unload modversions aarch64\0" not in data:
        sys.exit(f"{ko.name}: vermagic is not the stock kernel's")
EOF

echo ">> modem: the audio DSP's image and protection-domain maps"
export MTOOLS_SKIP_CHECK=1
mcopy -n -i "$backup/lun4/modem.bin" '::/image/adsp.*' '::/image/adspr.jsn' '::/image/adspua.jsn' \
    "$output/firmware/"
for f in adsp.mdt adsp.b00 adspr.jsn adspua.jsn; do
    [[ -s "$output/firmware/$f" ]] || { echo "no $f in the modem partition" >&2; exit 1; }
done

chmod -R u+rwX,go+rX "$output"
python3 - "$backup" "$output" > "$output/manifest.json" <<'EOF'
import hashlib, json, sys, pathlib
backup, out = map(pathlib.Path, sys.argv[1:3])
identity = json.loads((backup / "manifest.json").read_text())["identity"]
files = {}
for p in sorted(out.rglob("*")):
    if p.is_file() and p.name != "manifest.json":
        files[str(p.relative_to(out))] = hashlib.sha256(p.read_bytes()).hexdigest()
sources = {"modules/": "vendor /lib/modules", "firmware/": "modem /image",
           "audiopd/acdb/": "vendor /etc/acdbdata", "audiopd/dsp/adsp/": "vendor /lib/rfsa/adsp"}
print(json.dumps({"schema": 1, "source_backup": str(backup),
                  "fingerprint": identity.get("fingerprint"), "sources": sources,
                  "files": files}, indent=1))
EOF
echo ">> $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["files"]))' "$output/manifest.json") files in $output"
