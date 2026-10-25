#!/bin/bash
# Extract PICO OS's head tracker and the Compute DSP it runs on from YOUR OWN stock backup
# (docs/xr.md#head-tracking).
#
# Usage (Linux/WSL, root):
#   scripts/extract-stock-slam.sh BACKUP_DIR OUTPUT_DIR
#
# BACKUP_DIR is a full EDL backup as described in docs/device-preparation.md (lun0/super.bin,
# lun4/modem.bin, lun4/dsp.bin). OUTPUT_DIR receives:
#   firmware/cdsp.mdt, cdsp.bNN, cdspr.jsn   the Compute DSP's image (modem partition)
#   root/system/bin/bootstrap/linker64      bionic's linker and C library (system), which
#   root/system/lib64/bootstrap/             need no Android runtime
#   root/system/lib64/                       libSlam.so (the head tracker), the auto exposure
#                                            (libExposureImageProcess.so) and the libraries
#                                            they need (system)
#   root/system/etc/pvr/config/              the tracker's configuration (system)
#   root/vendor/lib/rfsa/adsp/               its DSP modules (vendor)
#   root/vendor/dsp/cdsp/                    the DSP's process shells (dsp partition)
#   root/vendor/bin/cdsprpcd, root/vendor/lib64/  the DSP's root listener (vendor)
#   root/pico4/camera_otp.json               the camera modules' vendor ids, as PICO OS saved
#                                            them (userdata, when the backup has it)
#   manifest.json                            sources and sha256 of every file
# The result is proprietary: keep it private (assets/ is ignored by Git). Nothing touches the
# headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
backup=$(realpath "${1:?specify the backup directory}")
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
for f in lun0/super.bin lun4/modem.bin lun4/dsp.bin manifest.json; do
    [[ -s "$backup/$f" ]] || { echo "missing $backup/$f" >&2; exit 1; }
done
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$output"/{firmware,root}
output=$(realpath "$output")
root=$output/root

# Files must match the backup manifest before anything is read from them.
python3 - "$backup" <<'EOF'
import hashlib, json, sys, pathlib
root = pathlib.Path(sys.argv[1])
files = json.loads((root / "manifest.json").read_text())["files"]
for name in ("lun0/super.bin", "lun4/modem.bin", "lun4/dsp.bin"):
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
    symlink) echo "$2 in $1 is a link; name its target" >&2; return 1 ;;
    *) echo "missing $2 in $1" >&2; return 1 ;;
    esac
}

echo ">> modem: the Compute DSP's image and protection-domain map"
mcopy -n -i "$backup/lun4/modem.bin" '::/image/cdsp.*' '::/image/cdspr.jsn' "$output/firmware/"
[[ -s "$output/firmware/cdsp.mdt" && -s "$output/firmware/cdspr.jsn" ]] || { echo "no cdsp.mdt in the modem partition" >&2; exit 1; }

echo ">> dsp: the DSP's process shells"
mkdir -p "$root/vendor/dsp"
dump "$backup/lun4/dsp.bin" /cdsp "$root/vendor/dsp"
[[ -s "$root/vendor/dsp/cdsp/fastrpc_shell_3" ]] || { echo "no fastrpc_shell_3 in the dsp partition" >&2; exit 1; }

echo ">> system: the head tracker, the libraries it needs and its configuration"
(cd "$project" && python3 -m pico4.superimg extract "$backup/lun0/super.bin" system "$work/system.img") >/dev/null
mkdir -p "$root/system/bin/bootstrap" "$root/system/lib64/bootstrap" "$root/system/etc/pvr"
dump "$work/system.img" /system/bin/bootstrap/linker64 "$root/system/bin/bootstrap/linker64"
for f in libc.so libm.so libdl.so; do
    dump "$work/system.img" "/system/lib64/bootstrap/$f" "$root/system/lib64/bootstrap/$f"
done
# libandroid and liblog's log calls are replaced by the image's own stand-ins (src/stockslam).
for f in libSlam.so libslamDSP.so libceres.so libminiglog.so libcdsprpc.so libcutils.so libbase.so libc++.so \
    ld-android.so liblog.so libion.so libExposureImageProcess.so libtianti_shm_dynamic.so libc++_shared.so; do
    dump "$work/system.img" "/system/lib64/$f" "$root/system/lib64/$f"
done
dump "$work/system.img" /system/etc/pvr/config "$root/system/etc/pvr"
[[ -s "$root/system/etc/pvr/config/config_lscLH.txt" ]] || { echo "no config_lscLH.txt in /system/etc/pvr/config" >&2; exit 1; }
rm -f "$work/system.img"

# The camera modules' vendor ids choose the lens-shading table. PICO OS's station service reads
# them from the modules' OTP once and keeps them in /data/stationservice/camera_otp.json
# (userdata); it trusts saved values of 8 and 9 only. Found in the userdata dump as text, the
# file's or a log line's; without one pico4-slamd falls back to the vendor-9 table.
echo ">> userdata: the camera modules' vendor ids (camera_otp.json)"
userdata=""
for f in lun0/userdata.bin lun0/userdata.bin.partial; do
    [[ -s "$backup/$f" ]] && { userdata=$backup/$f; break; }
done
mkdir -p "$root/pico4"
if [[ -n $userdata ]]; then
    python3 - "$userdata" "$root/pico4/camera_otp.json" <<'EOF'
import collections, json, mmap, re, sys
key = rb'"camera%d"\s*:\s*(\d+)'
pattern = re.compile(rb"\s*,\s*".join(key % i for i in range(4)))
found = collections.Counter()
with open(sys.argv[1], "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
    for match in pattern.finditer(m):
        ids = tuple(int(v) for v in match.groups())
        if all(v in (8, 9) for v in ids):
            found[ids] += 1
if not found:
    print("no saved vendor ids in the userdata dump: pico4-slamd uses the vendor-9 table", file=sys.stderr)
    sys.exit(0)
if len(found) > 1:
    sys.exit(f"the userdata dump saves different vendor ids: {dict(found)}")
ids = next(iter(found))
with open(sys.argv[2], "w") as out:
    json.dump({f"camera{i}": v for i, v in enumerate(ids)}, out, indent=1)
    out.write("\n")
print("vendor ids", ",".join(map(str, ids)))
EOF
else
    echo "no userdata in the backup: pico4-slamd uses the vendor-9 table" >&2
fi

echo ">> vendor: the tracker's DSP modules and the DSP's root listener"
(cd "$project" && python3 -m pico4.superimg extract "$backup/lun0/super.bin" vendor "$work/vendor.img") >/dev/null
mkdir -p "$root/vendor/lib/rfsa/adsp" "$root/vendor/bin" "$root/vendor/lib64"
for f in libslamDSP_skel.so libpico_cv_dsp_skel.so libfastcvadsp.so libdspCV_skel.so libc++.so.1 libc++abi.so.1; do
    dump "$work/vendor.img" "/lib/rfsa/adsp/$f" "$root/vendor/lib/rfsa/adsp/$f"
done
dump "$work/vendor.img" /bin/cdsprpcd "$root/vendor/bin/cdsprpcd"
for f in libcdsp_default_listener.so libcdsprpc.so; do
    dump "$work/vendor.img" "/lib64/$f" "$root/vendor/lib64/$f"
done
rm -f "$work/vendor.img"

python3 - "$output" "$backup" <<'EOF'
import hashlib, json, pathlib, sys
out, backup = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
files = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(out.rglob("*")) if p.is_file()}
sources = {"firmware/": "modem /image", "root/vendor/dsp/": "dsp /cdsp", "root/system/": "super system",
           "root/vendor/": "super vendor", "root/pico4/camera_otp.json": "userdata /data/stationservice"}
manifest = {"backup": json.loads((backup / "manifest.json").read_text()).get("firmware"),
            "sources": sources, "files": files}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"{len(files)} files")
EOF
