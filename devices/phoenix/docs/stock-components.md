# Stock components

What the PICO 4's images take unchanged from PICO OS's stock image, where each comes from, what
uses it and what could replace it. Every file below is extracted at build time from the user's
own stock firmware (PICO's public OTA, `python -m overpressure stock`, or the device backup) by
the scripts named; none is in the repository. What loads was measured on one headset (camera
module vendor 9): the kernel log for the WLAN files, the files a tracker start opens for the rest. Device-specific data (calibration, OTP, persist)
is not listed: the image links it from the headset's own `persist` partition at boot
([userspace.md](userspace.md#the-headsets-own-data)).

Classes: **signed** (firmware or images that TrustZone, a DSP or the ABL verifies: they stay),
**free** (free software can replace it), **data** (configuration or models that could be
generated or shipped differently), **reimplementation** (a program whose behaviour would have to
be reimplemented).

| Component | Stock files (partition: path) | Extracted by | Used by | Class |
| --- | --- | --- | --- | --- |
| Kernel, device tree, boot header | boot.img (OTA) | `overpressure stock` | the maintenance stage 1 and the way back to PICO OS; the system runs Valve's kernel | signed |
| Recovery device-tree overlay, vbmeta | recovery | `dev image` | the maintenance stage 1 in `recovery` | signed (the ABL checks the AVB chain) |
| GPU firmware | vendor `/firmware`: a650_gmu.bin, a650_sqe.fw, a650_zap.mdt and .b00–.b02 | `assets graphics-firmware` | msm | zap signed; GMU and SQE in linux-firmware (same files unverified) |
| Video codec firmware | modem `/image`: venus.mdt, venus.b00–.b10, venus.b19 | `assets graphics-firmware` | Venus (Steam Link's decoder) | signed |
| SLPI image and PD maps | modem `/image`: slpi.mdt, slpi.b00–.b20, slpir.jsn, slpius.jsn | `assets sensor-data` | the SLPI (IMU, wear sensor) | signed |
| Sensor driver configuration and registry layout | vendor `/etc/sensors/config` (44 json), sns_reg_config | `assets sensor-data` | the SLPI's registry | data; the whole directory ships, which files load is not measured |
| ADSP image and PD maps | modem `/image`: adsp.mdt, adsp.b00–.b18, adspr.jsn, adspua.jsn | `assets audio` | the ADSP | signed |
| Audio PD libraries | vendor `/etc/acdbdata/adsp_avs_config.acdb`; `/lib/rfsa/adsp`: capi_v2_ailab_ecns, two aptX codecs, capi_v2_effectcpp, libapps_mem_heap, libc++.so.1, libc++abi.so.1, sami_audio_config.json | `assets audio` | the ADSP's audio PD | signed DSP code; which modules load is not measured |
| Audio calibration | vendor `acdbdata/wsa8810/base/MTP`: Global, Handset, Speaker `_cal.acdb` | `assets audio` | `pico4-audiocal` | data |
| WLAN firmware, board data | modem `/image/qca6390`: amss20.bin, m3.bin, bdwlanq.elf | `assets wlan` | the QCA6390, ath11k | firmware signed; the board file data: this headset loads bdwlanq.elf as ath11k's `board.bin` |
| CDSP image and PD map | modem `/image`: cdsp.mdt, cdsp.b00–.b11, cdspr.jsn | `assets stock-slam` | the CDSP | signed |
| CDSP process shells and libraries | dsp `/cdsp` (30 files: fastrpc_shell_3, fastrpc_shell_unsigned_3, libc++.so.1, libc++abi.so.1, sysmon, vpp, nn, dsp_streamer, hta, hcp, ubwcdma skels, map_SHARED_LIBS) | `assets stock-slam` | the CDSP's user PDs | signed; a tracker start opens only fastrpc_shell_3 |
| CDSP root listener | vendor `/bin/cdsprpcd`, `/lib64/libcdsp_default_listener.so`, libcdsprpc.so | `assets stock-slam` | `pico4-cdsprpcd.service` | reimplementation (a FastRPC listener) |
| Bionic runtime | system `/system/bin/bootstrap/linker64`, `/system/lib64/bootstrap`: libc, libm, libdl; `/system/lib64`: ld-android, libc++, liblog, libcutils, libbase, libion | `assets stock-slam` | `pico4-slamd`'s root | free (another bionic: an NDK or AOSP build) |
| Head tracker | system `/system/lib64`: libSlam, libslamDSP, libceres, libminiglog, libcdsprpc; vendor `/lib/rfsa/adsp`: libslamDSP_skel, libpico_cv_dsp_skel, libfastcvadsp, libdspCV_skel, libc++.so.1, libc++abi.so.1 | `assets stock-slam` | `pico4-slamd` | reimplementation (Basalt, deprecated, is the free alternative) |
| Auto exposure | system `/system/lib64`: libExposureImageProcess, libtianti_shm_dynamic, libc++_shared | `assets stock-slam` | `pico4-slamd` | reimplementation (ExposurePredict) |
| See-through depth mesh | system `/system/lib64`: libphotoid, libSNPE, libhexagon_runtime, libcvp2, libcvp2_hfi, libcvp_common; `/system/etc/pxr`: vst_releaseA/B.json.cpt, the `vst` networks (FLNet_lite_v2); vendor `/lib/rfsa/adsp`: libcvpdsp_skel, libhexagon_nn_skel, libsnpe_dsp_v66_domains_v2_skel, libsnpe_dsp_domains_v2 | `assets stock-slam` | `pico4-slamd` (the room view's 3D mesh) | reimplementation; the networks data; a tracker start opens vst_releaseA and the FLNet_lite_v2 networks, not vst_releaseB |
| Tracker configuration | system `/system/etc/pvr/config`: config_3dof.yaml, config_exposure, config_gamma, config_gma8, config_gma10, config_head, config_lscLH/SY/neo3, config_marker.yaml, config_pico.yaml, config_reloc, selfcali.yaml, mask/cam0–3/mask.yaml | `assets stock-slam` | libSlam, `pico4-slamd` (lens shading, gamma), the auto exposure | data; a tracker start opens config_3dof, exposure, gma10, head, lscLH (the lens shading of camera vendor 9; lscSY and lscneo3 are the other vendors'), marker, pico, reloc, selfcali and the four masks |
| Lens configuration | system `/system/etc/pxrconfig/config/SDK_Lens_ConfigFile.txt` | `assets sensor-data` | the driver (chromatic aberration) | data |
| Controller render models | system `/system/pre_resource/data/misc/user/controller/Neo3Phx` (left/right .obj, .mtl, .png, .json) | `assets sensor-data` | SteamVR | data |
| Controller LED models, aim-to-grip pose, lever arm | values read from libpico_controller_tracking.pxr.so, libpvrtrackingservice.so, libpico_controller_tracking.client.so | `assets sensor-data` (`tooling/ledmodel.py`, `aimgrip.py`, `leverarm.py`) | controller tracking, aim and grip poses | data (values, not files) |

Not taken from the stock image: the station, the IMU's SSC client, the fan, the IPD motor, the
lens distortion, the wear sensor, the camera OTP reader, the power key, the VR CPU limits, the
audio calibration loader, the image processing (`isp.h`), `pico4-slamd` and the Steam Link
controller library (`pico4-vrlink`) are this project's own implementations; hexagonrpcd comes
from linux-msm; the engineering ABL and the Firehose loader come
from public Neo3 packages; Valve's packages are not from the stock image.
