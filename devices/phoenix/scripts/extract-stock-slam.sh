#!/bin/bash
# Extract PICO OS's head tracker and the Compute DSP it runs on from the stock firmware
# (docs/xr.md#head-tracking).
#
# Usage (Linux/WSL, root):
#   scripts/extract-stock-slam.sh STOCK_DIR OUTPUT_DIR
#
# STOCK_DIR comes from `python -m overpressure stock` (system.img, vendor.img, modem.bin, dsp.bin from
# PICO's public OTA). OUTPUT_DIR receives:
#   firmware/cdsp.mdt, cdsp.bNN, cdspr.jsn   the Compute DSP's image (modem partition)
#   firmware/cvpss.mdt, cvpss.bNN            the CVP's image (modem partition)
#   root/system/bin/bootstrap/linker64      bionic's linker and C library (system), which
#   root/system/lib64/bootstrap/             need no Android runtime
#   root/system/lib64/                       libSlam.so (the head tracker), the auto exposure
#                                            (libExposureImageProcess.so), the see-through's depth
#                                            mesh (libphotoid.so, with the CVP's and the DSP
#                                            network's libraries) and the libraries they need
#   root/system/etc/pvr/config/              the tracker's configuration (system)
#   root/system/etc/pxr/vst*                 the depth mesh's configuration and networks (system)
#   root/vendor/lib/rfsa/adsp/               their DSP modules (vendor)
#   root/vendor/dsp/cdsp/                    the DSP's process shells (dsp partition)
#   root/vendor/bin/cdsprpcd, root/vendor/lib64/  the DSP's root listener (vendor)
#   manifest.json                            sources and sha256 of every file
# The result is proprietary: keep it private (assets/ is ignored by Git). Nothing touches the
# headset.
set -euo pipefail
project=$(cd "$(dirname "$0")/.." && pwd)
. "$project/scripts/stock-lib.sh"
stock_dir "${1:-}" system.img vendor.img modem.bin dsp.bin
output=${2:?specify an output directory}
[[ $EUID == 0 ]] || { echo 'root is required (debugfs on image files)' >&2; exit 2; }
command -v debugfs >/dev/null && command -v mcopy >/dev/null ||
    { echo 'debugfs (e2fsprogs) and mcopy (mtools) are required' >&2; exit 2; }
[[ ! -e "$output" ]] || { echo "$output exists; choose a new directory" >&2; exit 1; }

mkdir -p "$output"/{firmware,root}
output=$(realpath "$output")
root=$output/root
export MTOOLS_SKIP_CHECK=1

echo ">> modem: the Compute DSP's image and protection-domain map"
mcopy -n -i "$stock/modem.bin" '::/image/cdsp.*' '::/image/cdspr.jsn' "$output/firmware/"
[[ -s "$output/firmware/cdsp.mdt" && -s "$output/firmware/cdspr.jsn" ]] || { echo "no cdsp.mdt in the modem partition" >&2; exit 1; }
echo ">> modem: the CVP's image (depth from stereo for the see-through's depth mesh)"
mcopy -n -i "$stock/modem.bin" '::/image/cvpss.*' "$output/firmware/"
[[ -s "$output/firmware/cvpss.mdt" ]] || { echo "no cvpss.mdt in the modem partition" >&2; exit 1; }

echo ">> dsp: the DSP's process shells"
mkdir -p "$root/vendor/dsp"
dump "$stock/dsp.bin" /cdsp "$root/vendor/dsp"
[[ -s "$root/vendor/dsp/cdsp/fastrpc_shell_3" ]] || { echo "no fastrpc_shell_3 in the dsp partition" >&2; exit 1; }

echo ">> system: the head tracker, the libraries it needs and its configuration"
mkdir -p "$root/system/bin/bootstrap" "$root/system/lib64/bootstrap" "$root/system/etc/pvr"
dump "$stock/system.img" /system/bin/bootstrap/linker64 "$root/system/bin/bootstrap/linker64"
for f in libc.so libm.so libdl.so; do
    dump "$stock/system.img" "/system/lib64/bootstrap/$f" "$root/system/lib64/bootstrap/$f"
done
# libandroid and liblog's log calls are replaced by the image's own stand-ins (src/stockslam).
for f in libSlam.so libslamDSP.so libceres.so libminiglog.so libcdsprpc.so libcutils.so libbase.so libc++.so \
    ld-android.so liblog.so libion.so libExposureImageProcess.so libtianti_shm_dynamic.so libc++_shared.so; do
    dump "$stock/system.img" "/system/lib64/$f" "$root/system/lib64/$f"
done
dump "$stock/system.img" /system/etc/pvr/config "$root/system/etc/pvr"
[[ -s "$root/system/etc/pvr/config/config_lscLH.txt" ]] || { echo "no config_lscLH.txt in /system/etc/pvr/config" >&2; exit 1; }

echo ">> system: the see-through's depth mesh (docs/xr.md#passthrough), its libraries and configuration"
for f in libphotoid.so libSNPE.so libhexagon_runtime.so libcvp2.so libcvp2_hfi.so libcvp_common.so; do
    dump "$stock/system.img" "/system/lib64/$f" "$root/system/lib64/$f"
done
mkdir -p "$root/system/etc/pxr"
for f in vst_releaseA.json.cpt vst_releaseB.json.cpt; do
    dump "$stock/system.img" "/system/etc/pxr/$f" "$root/system/etc/pxr/$f"
done
dump "$stock/system.img" /system/etc/pxr/vst "$root/system/etc/pxr"
[[ -d "$root/system/etc/pxr/vst/FLNet_lite_v2" ]] || { echo "no FLNet_lite_v2 in /system/etc/pxr/vst" >&2; exit 1; }

echo ">> vendor: the tracker's DSP modules and the DSP's root listener"
mkdir -p "$root/vendor/lib/rfsa/adsp" "$root/vendor/bin" "$root/vendor/lib64"
for f in libslamDSP_skel.so libpico_cv_dsp_skel.so libfastcvadsp.so libdspCV_skel.so libc++.so.1 libc++abi.so.1 \
    libcvpdsp_skel.so libhexagon_nn_skel.so; do
    dump "$stock/vendor.img" "/lib/rfsa/adsp/$f" "$root/vendor/lib/rfsa/adsp/$f"
done
# The DSP network's DSP side ships with the system's libraries: SNPE has the DSP load it by that path.
for f in libsnpe_dsp_v66_domains_v2_skel.so libsnpe_dsp_domains_v2.so; do
    dump "$stock/system.img" "/system/lib64/$f" "$root/system/lib64/$f"
done
dump "$stock/vendor.img" /bin/cdsprpcd "$root/vendor/bin/cdsprpcd"
for f in libcdsp_default_listener.so libcdsprpc.so; do
    dump "$stock/vendor.img" "/lib64/$f" "$root/vendor/lib64/$f"
done

write_manifest "$output" '{"firmware/": "modem /image", "root/vendor/dsp/": "dsp /cdsp",
    "root/system/": "system", "root/vendor/": "vendor"}'
