# SPDX-License-Identifier: Unlicense
"""The PICO 4's assets: every file the userspace build takes from PICO OS, out of PICO's public OTA.

    python -m overpressure assets [--firmware NAME] [--stock DIR] [--assets DIR] [KIND...]

First the stock folder (python -m overpressure stock: PICO's full OTA's partitions, about 11 GB,
the OTA members kept and not fetched again), then each KIND (all by default) into
ASSETS/KIND-VERSION (VERSION: the firmware's number, 5.13.7), each into a fresh folder that
replaces the old one only when it is complete. Python on any system: the images are read by
overpressure's ext4 and FAT readers (docs/development.md#stock-firmware). Each folder holds a
manifest.json with its sources and every file's SHA-256, which scripts/build-userspace.sh reads:

  graphics-firmware  a650_gmu.bin, a650_sqe.fw, a650_zap.mdt and .b00-.b02 (vendor /firmware),
                     venus.mdt and .bNN, the video codec (modem /image)
  sensor-data        firmware/slpi*: the sensor DSP (modem); sensors/config/ and sns_reg.conf
                     (vendor /etc/sensors); calibration/pxrconfig/SDK_Lens_ConfigFile.txt (system);
                     calibration/controllers/led-models.json, aim-to-grip.json, lever-arm.json,
                     read from PICO OS's tracking libraries (system); rendermodels/: SteamVR's
                     models of both controllers (system /system/pre_resource); camera/imx471-mode.bin:
                     the RGB camera's register table, read from PICO OS's camera module (vendor
                     /lib64/camera), which the kernel's driver loads
  wlan               the QCA6390: firmware/qca6390/amss20.bin, m3.bin, bdwlanq.elf, its Wi-Fi
                     (modem /image/qca6390); firmware/qca/htbtfw20.tlv, htnv20.bin, its Bluetooth
                     (bluetooth /image)
  audio              firmware/adsp*: the audio DSP and its protection-domain maps (modem);
                     audiopd/: its AVS configuration and the DSP libraries the audio process uses
                     (vendor); calibration/*.acdb: the speaker and handset calibration (vendor)
  stock-slam         firmware/cdsp*: the Compute DSP (modem); root/: PICO OS's head tracker,
                     auto exposure and depth mesh with the bionic linker and libraries they need,
                     their configuration (system), their DSP modules and the DSP's root listener
                     (vendor) and the DSP's process shells (dsp)

The headset's own factory data is not among them: the image reads it from persist at boot. The
result is proprietary: keep it private (assets/ is ignored by Git). Nothing touches the headset.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from devices.phoenix.kernel import imx471_mode
from overpressure import REPO
from overpressure.assets import AssetError, BackupFolder, StockFolder, copy_matching, dump, widen_modes, write_manifest
from overpressure.ext4 import Ext4Error
from overpressure.fat import FatError

from . import FIRMWARE, aimgrip, controllermodel, ledmodel, leverarm, stock

Log = Callable[[str], None]

# -- what each kind takes ---------------------------------------------------------------------
GPU_FIRMWARE = ("a650_gmu.bin", "a650_sqe.fw", "a650_zap.mdt", "a650_zap.b00", "a650_zap.b01", "a650_zap.b02")
#: The video codec (Venus) for Steam Link's decoder.
VENUS = ("venus.*",)
SENSOR_DSP = ("slpi*",)
LENS_CONFIG = "/system/etc/pxrconfig/config/SDK_Lens_ConfigFile.txt"
LED_LIBRARY = "/system/lib64/libpico_controller_tracking.pxr.so"
AIM_GRIP_LIBRARY = "/system/lib64/libpvrtrackingservice.so"
LEVER_ARM_LIBRARY = "/system/lib64/libpico_controller_tracking.client.so"
CONTROLLER_MODELS = "/system/pre_resource/data/misc/user/controller/Neo3Phx"
#: The RGB camera's module, whose register table the kernel's IMX471 driver loads.
CAMERA_MODULE = "/lib64/camera/com.qti.sensormodule.sunny_imx471.bin"
WLAN_FIRMWARE = ("amss20.bin", "m3.bin", "bdwlanq.elf")
#: The QCA6390's Bluetooth firmware and NV data, under the names hci_qca asks for.
BT_FIRMWARE = ("htbtfw20.tlv", "htnv20.bin")
#: PICO OS's audio HAL loads its calibration from the WSA8810 MTP database: the global file and
#: the files of the speaker and handset devices are what the system's two paths use.
AUDIO_CALIBRATION = tuple(f"MTP_base_Wsa8810_{f}_cal.acdb" for f in ("Global", "Speaker", "Handset"))
#: The audio process's share of PICO OS's ADSP library path: its audio modules, the C++ runtime
#: they link, its heap module and audio configuration. The rest of the directory (vision, neural
#: network, SLAM and sensor libraries, about 72 MB) belongs to processes this system does not run.
AUDIO_DSP_LIBRARIES = ("capi_v2_*.so", "libc++.so.1", "libc++abi.so.1", "libapps_mem_heap.so", "sami_audio_config.json")
ADSP_FIRMWARE = ("adsp.*", "adspr.jsn", "adspua.jsn")
ADSP_REQUIRED = ("adsp.mdt", "adsp.b00", "adspr.jsn", "adspua.jsn")
CDSP_FIRMWARE = ("cdsp.*", "cdspr.jsn")
SLAM_BOOTSTRAP = ("libc.so", "libm.so", "libdl.so")
#: The head tracker and auto exposure with what they link; libandroid and liblog's log calls are
#: replaced by the image's own stand-ins (overpressure/packages' pico4-slamd).
SLAM_LIBRARIES = (
    "libSlam.so",
    "libslamDSP.so",
    "libceres.so",
    "libminiglog.so",
    "libcdsprpc.so",
    "libcutils.so",
    "libbase.so",
    "libc++.so",
    "ld-android.so",
    "liblog.so",
    "libion.so",
    "libExposureImageProcess.so",
    "libtianti_shm_dynamic.so",
    "libc++_shared.so",
)
#: The see-through's depth mesh (docs/xr.md#passthrough) and its CVP and DSP network backends.
DEPTH_MESH_LIBRARIES = (
    "libphotoid.so",
    "libSNPE.so",
    "libhexagon_runtime.so",
    "libcvp2.so",
    "libcvp2_hfi.so",
    "libcvp_common.so",
)
DEPTH_MESH_CONFIG = ("vst_releaseA.json.cpt", "vst_releaseB.json.cpt")
SLAM_DSP_MODULES = (
    "libslamDSP_skel.so",
    "libpico_cv_dsp_skel.so",
    "libfastcvadsp.so",
    "libdspCV_skel.so",
    "libc++.so.1",
    "libc++abi.so.1",
    "libcvpdsp_skel.so",
    "libhexagon_nn_skel.so",
)
#: The DSP network's DSP side ships with the system's libraries: SNPE has the DSP load it by that path.
SNPE_DSP = ("libsnpe_dsp_v66_domains_v2_skel.so", "libsnpe_dsp_domains_v2.so")
DSP_LISTENER_LIBRARIES = ("libcdsp_default_listener.so", "libcdsprpc.so")


def _require(path: Path, what: str) -> None:
    """Fail unless @p path is a file with data, or (for a directory's @p what) a directory."""
    if not (path.is_dir() if what.endswith("/") else path.is_file() and path.stat().st_size):
        msg = f"no {what}"
        raise AssetError(msg)


# -- the kinds ----------------------------------------------------------------------------------
def graphics_firmware(s: StockFolder, out: Path, log: Log) -> None:
    """Extract the GPU firmware the kernel loads (docs/graphics.md) and the video codec's (Venus)."""
    log(">> vendor: GPU firmware")
    vendor = s.ext4("vendor.img")
    for name in GPU_FIRMWARE:
        dump(vendor, f"/firmware/{name}", out / name)
    log(">> modem: the video codec's image (Venus)")
    copy_matching(s.fat("modem.bin"), "/image", VENUS, out)
    _require(out / "venus.mdt", "venus.mdt in the modem partition")


def sensor_data(s: StockFolder, out: Path, log: Log) -> None:
    """Extract the head-IMU sensor stack, the lens and controller data (docs/xr.md) and the RGB camera's table."""
    vendor, system = s.ext4("vendor.img"), s.ext4("system.img")
    for folder in ("firmware", "sensors", "calibration/pxrconfig", "calibration/controllers", "camera"):
        (out / folder).mkdir(parents=True, exist_ok=True)
    log(">> vendor: sensor config")
    dump(vendor, "/etc/sensors/config", out / "sensors")
    dump(vendor, "/etc/sensors/sns_reg_config", out / "sensors/sns_reg.conf")
    log(">> vendor: the RGB camera's register table")
    try:
        pairs = imx471_mode.known_mode(vendor.read(CAMERA_MODULE), CAMERA_MODULE)
    except ValueError as exc:
        raise AssetError(str(exc)) from exc
    (out / "camera/imx471-mode.bin").write_bytes(imx471_mode.table(pairs))

    log(">> system: lens configuration, controller LED models, aim-to-grip transforms, lever arms, controller models")
    dump(system, LENS_CONFIG, out / "calibration/pxrconfig/SDK_Lens_ConfigFile.txt")
    controllers = out / "calibration/controllers"
    try:
        library = system.read(LED_LIBRARY)
        leds = ledmodel.models(library)
        led_json = ledmodel.to_json(leds, ledmodel.normals(library, leds), Path(LED_LIBRARY).name)
        aim_json = aimgrip.to_json(aimgrip.transforms(system.read(AIM_GRIP_LIBRARY)), Path(AIM_GRIP_LIBRARY).name)
        lever_json = leverarm.to_json(leverarm.lever_arms(system.read(LEVER_ARM_LIBRARY)), Path(LEVER_ARM_LIBRARY).name)
    except (ledmodel.LedModelError, aimgrip.AimGripError, leverarm.LeverArmError) as exc:
        raise AssetError(str(exc)) from exc
    for name, text in (("led-models.json", led_json), ("aim-to-grip.json", aim_json), ("lever-arm.json", lever_json)):
        (controllers / name).write_bytes(text.encode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="pico4-models-") as work:
        dump(system, CONTROLLER_MODELS, Path(work))
        try:
            worst = controllermodel.convert(
                Path(work) / Path(CONTROLLER_MODELS).name,
                out / "rendermodels",
                json.loads(aim_json),
                json.loads(led_json),
                json.loads(lever_json),
            )
        except (OSError, ValueError, KeyError) as exc:
            raise AssetError(str(exc)) from exc
    for hand, distance in worst.items():
        log(f"{hand}: every LED within {distance * 1000:.1f} mm of the model")

    log(">> modem: sensor DSP firmware")
    copy_matching(s.fat("modem.bin"), "/image", SENSOR_DSP, out / "firmware")
    _require(out / "firmware/slpi.mdt", "slpi.mdt in the modem partition")


def wlan(s: StockFolder, out: Path, log: Log) -> None:
    """Extract the QCA6390's Wi-Fi (ath11k) and Bluetooth (hci_qca) firmware; the MAC addresses stay in persist."""
    target = out / "firmware/qca6390"
    target.mkdir(parents=True)
    log(">> modem: chip firmware and board data")
    copy_matching(s.fat("modem.bin"), "/image/qca6390", WLAN_FIRMWARE, target)
    for name in WLAN_FIRMWARE:
        _require(target / name, f"qca6390/{name} in the modem partition")
    bluetooth = out / "firmware/qca"
    bluetooth.mkdir(parents=True)
    log(">> bluetooth: Bluetooth firmware and NV data")
    copy_matching(s.fat("bluetooth.bin"), "/image", BT_FIRMWARE, bluetooth)
    for name in BT_FIRMWARE:
        _require(bluetooth / name, f"{name} in the bluetooth partition")


def audio(s: StockFolder, out: Path, log: Log) -> None:
    """Extract the audio DSP's image, the audio process's files and the calibration (docs/userspace.md#audio)."""
    for folder in ("firmware", "audiopd/acdb", "audiopd/dsp/adsp", "calibration"):
        (out / folder).mkdir(parents=True)
    vendor = s.ext4("vendor.img")
    log(">> vendor: AVS configuration and DSP libraries")
    dump(vendor, "/etc/acdbdata/adsp_avs_config.acdb", out / "audiopd/acdb/adsp_avs_config.acdb")
    for name in AUDIO_CALIBRATION:
        dump(vendor, f"/etc/acdbdata/wsa8810/base/MTP/{name}", out / "calibration" / name)
    names = vendor.listdir("/lib/rfsa/adsp")
    for pattern in AUDIO_DSP_LIBRARIES:
        found = sorted(n for n in names if fnmatch.fnmatchcase(n, pattern))
        for name in found or [pattern]:
            inode = vendor.lookup(f"/lib/rfsa/adsp/{name}") if name in names else None
            if inode is None or not inode.is_file or not inode.size:
                msg = f"missing adsp/{name} in the vendor partition"
                raise AssetError(msg)
            dump(vendor, f"/lib/rfsa/adsp/{name}", out / "audiopd/dsp/adsp" / name)
            if os.name == "posix":  # cp copies the image's mode, less the umask
                (out / "audiopd/dsp/adsp" / name).chmod(inode.permissions & 0o755)

    log(">> modem: the audio DSP's image and protection-domain maps")
    copy_matching(s.fat("modem.bin"), "/image", ADSP_FIRMWARE, out / "firmware")
    for name in ADSP_REQUIRED:
        _require(out / "firmware" / name, f"{name} in the modem partition")


def stock_slam(s: StockFolder, out: Path, log: Log) -> None:
    """Extract PICO OS's head tracker and the Compute DSP it runs on (docs/xr.md#head-tracking)."""
    root = out / "root"
    for folder in ("firmware", "root/vendor/dsp", "root/system/bin/bootstrap", "root/system/lib64/bootstrap"):
        (out / folder).mkdir(parents=True)
    log(">> modem: the Compute DSP's image and protection-domain map")
    copy_matching(s.fat("modem.bin"), "/image", CDSP_FIRMWARE, out / "firmware")
    _require(out / "firmware/cdsp.mdt", "cdsp.mdt in the modem partition")
    _require(out / "firmware/cdspr.jsn", "cdspr.jsn in the modem partition")

    log(">> dsp: the DSP's process shells")
    dump(s.ext4("dsp.bin"), "/cdsp", root / "vendor/dsp")
    _require(root / "vendor/dsp/cdsp/fastrpc_shell_3", "fastrpc_shell_3 in the dsp partition")

    system, vendor = s.ext4("system.img"), s.ext4("vendor.img")
    log(">> system: the head tracker, the libraries it needs and its configuration")
    (root / "system/etc/pvr").mkdir(parents=True)
    dump(system, "/system/bin/bootstrap/linker64", root / "system/bin/bootstrap/linker64")
    for name in SLAM_BOOTSTRAP:
        dump(system, f"/system/lib64/bootstrap/{name}", root / "system/lib64/bootstrap" / name)
    for name in SLAM_LIBRARIES:
        dump(system, f"/system/lib64/{name}", root / "system/lib64" / name)
    dump(system, "/system/etc/pvr/config", root / "system/etc/pvr")
    _require(root / "system/etc/pvr/config/config_lscLH.txt", "config_lscLH.txt in /system/etc/pvr/config")

    log(">> system: the see-through's depth mesh (docs/xr.md#passthrough), its libraries and configuration")
    for name in DEPTH_MESH_LIBRARIES:
        dump(system, f"/system/lib64/{name}", root / "system/lib64" / name)
    (root / "system/etc/pxr").mkdir(parents=True)
    for name in DEPTH_MESH_CONFIG:
        dump(system, f"/system/etc/pxr/{name}", root / "system/etc/pxr" / name)
    dump(system, "/system/etc/pxr/vst", root / "system/etc/pxr")
    _require(root / "system/etc/pxr/vst/FLNet_lite_v2", "FLNet_lite_v2 in /system/etc/pxr/vst/")

    log(">> vendor: the tracker's DSP modules and the DSP's root listener")
    for folder in ("vendor/lib/rfsa/adsp", "vendor/bin", "vendor/lib64"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    for name in SLAM_DSP_MODULES:
        dump(vendor, f"/lib/rfsa/adsp/{name}", root / "vendor/lib/rfsa/adsp" / name)
    for name in SNPE_DSP:
        dump(system, f"/system/lib64/{name}", root / "system/lib64" / name)
    dump(vendor, "/bin/cdsprpcd", root / "vendor/bin/cdsprpcd")
    for name in DSP_LISTENER_LIBRARIES:
        dump(vendor, f"/lib64/{name}", root / "vendor/lib64" / name)


@dataclass(frozen=True)
class Kind:
    """One assets folder: the stock files it reads, its manifest's sources, how it is made."""

    images: tuple[str, ...]  #: the stock folder's files, checked against its manifest first
    sources: dict[str, str]  #: manifest.json's "sources": a path prefix -> partition and path
    extract: Callable[[StockFolder, Path, Log], None]
    widen: bool = True  #: everyone may read the files afterwards (chmod -R u+rwX,go+rX)


#: assets/NAME-VERSION, in the order they are extracted.
KINDS = {
    "graphics-firmware": Kind(
        ("vendor.img", "modem.bin"), {"a650_": "vendor /firmware", "venus.": "modem /image"}, graphics_firmware
    ),
    "sensor-data": Kind(
        ("system.img", "vendor.img", "modem.bin"),
        {
            "firmware/": "modem /image",
            "sensors/": "vendor /etc/sensors",
            "calibration/": "system",
            "rendermodels/": "system /system/pre_resource",
            "camera/": "vendor /lib64/camera",
        },
        sensor_data,
    ),
    "wlan": Kind(
        ("modem.bin", "bluetooth.bin"),
        {"firmware/qca6390/": "modem /image/qca6390", "firmware/qca/": "bluetooth /image"},
        wlan,
    ),
    "audio": Kind(
        ("vendor.img", "modem.bin"),
        {
            "firmware/": "modem /image",
            "audiopd/acdb/": "vendor /etc/acdbdata",
            "audiopd/dsp/adsp/": "vendor /lib/rfsa/adsp",
            "calibration/": "vendor /etc/acdbdata/wsa8810/base/MTP",
        },
        audio,
    ),
    "stock-slam": Kind(
        ("system.img", "vendor.img", "modem.bin", "dsp.bin"),
        {
            "firmware/": "modem /image",
            "root/vendor/dsp/": "dsp /cdsp",
            "root/system/": "system",
            "root/vendor/": "vendor",
        },
        stock_slam,
        widen=False,
    ),
}


def extract(name: str, folder: StockFolder, output: Path, log: Log = print) -> int:
    """Extract the assets of kind @p name from @p folder into the new folder @p output; return its file count."""
    if output.exists():
        msg = f"{output} exists; choose a new directory"
        raise AssetError(msg)
    kind = KINDS[name]
    folder.check(*kind.images)
    output.mkdir(parents=True)
    kind.extract(folder, output, log)
    if kind.widen:
        widen_modes(output)
    count = write_manifest(output, folder.firmware, kind.sources)
    log(f">> {count} files in {output}")
    return count


def current(folder: Path, name: str) -> bool:
    """Tell whether @p folder holds kind @p name's assets as today's recipe extracts them (its sources)."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("sources") == KINDS[name].sources


def version_of(firmware: str) -> str:
    """Return the firmware's number (5.13.7 of 5.13.7-global-seko): the assets folders' suffix."""
    return firmware.split("-", 1)[0]


#: The stock folder's images in a backup: its partition and, in super, the logical partition.
BACKUP_IMAGES: dict[str, tuple[str, str | None]] = {
    "system.img": ("super", "system"),
    "vendor.img": ("super", "vendor"),
    "modem.bin": ("modem", None),
    "dsp.bin": ("dsp", None),
    "bluetooth.bin": ("bluetooth", None),
}


def backup_folder(backup: Path) -> BackupFolder:
    """Return the headset's backup @p backup as a stock folder: the same images, from its partitions."""
    return BackupFolder(backup, BACKUP_IMAGES)


def firmware_version(fingerprint: str | None) -> str:
    """Return the firmware's number from a build fingerprint (Pico/Phoenix_ovs/PICOA8110:10/5.13.7/...)."""
    parts = (fingerprint or "").split("/")
    if len(parts) < 4 or not parts[3]:  # noqa: PLR2004  # brand/product/device:release/version/...
        msg = f"no firmware version in the backup's fingerprint {fingerprint!r}"
        raise AssetError(msg)
    return parts[3]


def extract_all(folder: StockFolder, version: str, assets: Path, kinds: tuple[str, ...], log: Log) -> list[Path]:
    """Extract each kind from @p folder into @p assets/KIND-@p version, each replacing the old folder when complete."""
    done = []
    for name in kinds:
        out = assets / f"{name}-{version}"
        new = out.with_name(out.name + ".new")
        log(f">> {out}")
        shutil.rmtree(new, ignore_errors=True)
        extract(name, folder, new, log)
        shutil.rmtree(out, ignore_errors=True)
        new.rename(out)
        done.append(out)
    return done


def build(  # noqa: PLR0913  # what to extract and where; the stock step and the log keyword-only
    firmware: str,
    stock_dir: Path,
    assets: Path,
    kinds: tuple[str, ...] = tuple(KINDS),
    *,
    fetch: Callable[[str, Path, Log], dict] = stock.build,
    log: Log = print,
) -> list[Path]:
    """Build the stock folder of @p firmware in @p stock_dir, then each kind into @p assets/KIND-VERSION.

    Each kind goes into a fresh KIND-VERSION.new that replaces the old folder only when complete.
    """
    started = time.monotonic()
    fetch(firmware, stock_dir, log)
    with StockFolder(stock_dir) as folder:
        done = extract_all(folder, version_of(firmware), assets, kinds, log)
    log(f">> assets for {firmware}: {' '.join(p.name for p in done)} ({time.monotonic() - started:.0f} s)")
    return done


def from_backup(backup: Path, assets: Path, kinds: tuple[str, ...] = tuple(KINDS), *, log: Log = print) -> list[Path]:
    """Extract each kind from the headset's own backup @p backup into @p assets/KIND-VERSION (no download).

    The files are the same as from PICO's OTA of the backup's firmware; persist, the headset's own
    data, is not read.
    """
    started = time.monotonic()
    with backup_folder(backup) as folder:
        version = firmware_version(folder.firmware)
        done = extract_all(folder, version, assets, kinds, log)
    log(f">> assets of {version} from {backup}: {' '.join(p.name for p in done)} ({time.monotonic() - started:.0f} s)")
    return done


def main(argv: list[str] | None = None) -> int:
    """Run the assets command line."""
    ap = argparse.ArgumentParser(prog="python -m overpressure assets", description=__doc__.split("\n\n")[0])
    ap.add_argument("kinds", nargs="*", metavar="KIND", help=f"of {', '.join(KINDS)} (default: all)")
    ap.add_argument("--firmware", default=FIRMWARE, help=f"firmware.json entry (default {FIRMWARE})")
    ap.add_argument("--stock", type=Path, help="the stock folder (default cache/pico4-stock/FIRMWARE)")
    ap.add_argument("--assets", type=Path, default=REPO / "assets", help="where the KIND-VERSION folders go")
    ap.add_argument("--backup", type=Path, help="take them from this EDL backup of the headset instead (no download)")
    args = ap.parse_args(argv)
    unknown = [k for k in args.kinds if k not in KINDS]
    if unknown:
        ap.error(f"unknown KIND {', '.join(unknown)}; known: {', '.join(KINDS)}")
    stock_dir = args.stock or REPO / "cache" / "pico4-stock" / args.firmware
    try:
        if args.backup:
            from_backup(args.backup, args.assets, tuple(args.kinds) or tuple(KINDS))
        else:
            build(args.firmware, stock_dir, args.assets, tuple(args.kinds) or tuple(KINDS))
    except (AssetError, Ext4Error, FatError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
