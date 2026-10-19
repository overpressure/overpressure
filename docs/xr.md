# Native XR hardware (roadmap step 5)

The PICO 4's XR hardware under Linux, on the stock kernel (4.19.81-perf+, PICO OS 5.13.7) and
with free software only. The head IMU streams through the sensor DSP. Monado 25.1.0 with this
project's `pico4` driver gives OpenXR clients the head pose (3DoF fusion at 1 kHz, 6DoF from
Basalt on the four tracking cameras), shows their frames on both panels through the stock KMS
driver with the factory lens model and the IPD read from the lens motor, and provides both
controllers with inputs and 3DoF orientation. Controller positions (IR-LED tracking) and haptics
are open. Monado runs as a system service ([session.md](session.md#monado-as-a-service)); SteamVR
uses the same driver through `driver_monado`. The inventory comes from the stock image, kernel and
device tree and from read-only `adb shell` observation of PICO OS; no vendor files are part of
this repository.

Code: the driver and the KMS compositor target in [src/monado/](../src/monado), the Monado
recipe and patches in [userspace/pkgbuilds/pico4-monado/](../userspace/pkgbuilds/pico4-monado),
the sensor daemons in [userspace/pkgbuilds/pico4-sensors/](../userspace/pkgbuilds/pico4-sensors),
the OpenXR test clients and `pico4-cam` in [src/xr/](../src/xr), and the Python tools
[`pico4-imu`](../userspace/rootfs/usr/bin/pico4-imu) and
[`pico4-station`](../userspace/rootfs/usr/bin/pico4-station).

> **Rules for this hardware**
>
> - **SLPI restart level.** Set the SLPI subsystem's `restart_level` to `RELATED` before the
>   sensor DSP boots or restarts; [`slpi-boot`](../userspace/rootfs/usr/libexec/pico4-poc/slpi-boot)
>   does this. With the kernel default `SYSTEM`, any DSP crash or restart request reboots the
>   whole headset.
> - **No firmware commands.** The station link (`/dev/spidev0.0`) also carries station and
>   controller firmware updates (OTA), and the GD32 MCU (`/dev/ttyHS1`) also takes motor moves,
>   calibration writes and firmware. Never send such commands. The station modules and the IPD
>   reader build frames only from allowlists; keep it that way.
> - **One owner for cameras and station.** The OV7251s stream only while the station pulses
>   FSIN, so every capture resets the station first. One process at a time may own the cameras
>   and the station: stop `pico4-monado.service` (or Valve's session) before `pico4-cam` or
>   `pico4-station`.

## Inventory

The selected board overlay is `kona MTP` (dtbo index 7). Its XR nodes match the live system.

| Function | Hardware | Kernel interface (stock 4.19) | Used on PICO OS by |
| --- | --- | --- | --- |
| Head IMU | TDK-InvenSense ICM-42688 (`icm4x6xx`: accel, gyro, temperature) and AKM AK0991x magnetometer, on the sensor DSP (SLPI) | none on the AP; Qualcomm Sensor Core (SSC) via QMI over QRTR, SLPI boot via `/sys/kernel/boot_slpi` | `sscrpcd` (serves the registry to the DSP), `sensors.qti`, `pvrtrackingservice` via `libssc` |
| Tracking cameras | 4 × OmniVision OV7251 (640×480 mono global shutter, left/right master/slave) | Qualcomm Spectra CSL: `/dev/video0-3`, `/dev/v4l-subdev0-25`, `/dev/media0-2` (no plain V4L2 capture); frame-sync timestamps in `.../cam-sensor*/sixdof` | camera HAL (CamX) + PICO `quickcamera` HAL, `pvrtrackingservice`, `pxrseethroughservice` |
| Other cameras | IMX471 RGB; OV680 bridge; ET-left nodes (Pro only) | same Spectra stack | see-through, MR services |
| Controllers | Nordic nRF52832 radio ("station"), ESB or BLE firmware, on SPI | `/dev/spidev0.0` (`picovr,nordic`, world-writable on PICO OS); `/dev/stationdev` (`bytedance,station`: power, boot GPIO, sync IRQ, timestamps) | `stationservice` (`libstationopticsservice.so`): pairing, controller IMU stream with per-controller IMU calibration, IR-LED mode, firmware OTA |
| Controller pose | IR LEDs on the tracking rings, seen by the tracking cameras | cameras above | `pvrtrackingservice` with `libpico_controller_tracking` |
| IPD motor | GigaDevice GD32 MCU, also backlight and fan ([cooling.md](cooling.md)) | `/dev/ttyHS1` (GENI UART, `984000.qcom,qup_uart`) | `gd32ipdservice` |
| Lens/display | Sharp LS026B3SA ×2 | `msm_drm` ([graphics.md](graphics.md)) | `pxrhmdservice` reads lens and IPD tables |

The SSC registry also carries configurations for other boards (LSM6DST, LPS22HH, …). On this
headset the DSP reports only the ICM-42688 and AK0991x sensors.

## Factory calibration

`persist` (`lun0/persist.bin` in an EDL backup) holds per-unit calibration, in formats that
Monado and Basalt can use with little conversion:

- `pvr/mav0/cam0..cam4/sensor.yaml`: EuRoC/Kalibr-style camera models (omni ξ + radtan
  distortion, intrinsics, extrinsics `T_BS` to the body frame, 640×480 @ 30 Hz). The factory
  report `pvr/mav0/info.txt` gives reprojection errors below 0.25 px.
- `pvr/mav0/imu0/sensor.yaml`, `pvr/ImuData.txt` (a 512 Hz accel/gyro reference capture),
  `pvr/gyroBias.txt` (temperature table), plus the SSC registry's factory gyro/accel calibration
  under `sensors/registry`, which the DSP applies itself.
- `pvr/OpticalCalibrationData.json`, `pvr/lens/*`, `ipdcalidata.txt` (IPD ADC → position):
  display colour, lens and IPD data used by `pxrhmdservice`.

[`extract-sensor-data.sh`](../scripts/extract-sensor-data.sh) copies `pvr/` into the sensor data
([Build](#build)); the image has it in `/usr/share/qcom/pico4/calibration`. It is per-device data
and stays out of Git, like the rest of the backup.

## Head IMU through the sensor DSP

The IMU is not wired to the application processor. It hangs off the SLPI, a Hexagon DSP running
Qualcomm's Sensor Core, so Linux has to do what PICO OS's init does. postmarketOS uses the same
free software on mainline kernels; [`pico4-sensors`](../userspace/pkgbuilds/pico4-sensors/PKGBUILD)
builds it from pinned linux-msm commits with patches for the stock 4.19 vendor kernel:

1. **Name service.** `qrtr-ns` (linux-msm/qrtr) provides the QRTR name service. Linux 5.7+ has
   it in the kernel; 4.19 does not, and PICO OS also runs it in userspace. Upstream qrtr
   dropped it, so the build pins the last commit that has it.
2. **Firmware boot.** Writing to `/sys/kernel/boot_slpi/boot` makes the kernel's PIL loader boot
   `slpi.mdt`/`.bNN` from the headset's own modem partition (installed to `/usr/lib/firmware`).
   `slpi-boot` first sets the subsystem's `restart_level` to `RELATED` (see the rules above),
   then waits up to 30 s for the subsystem state `ONLINE`.
3. **Service locator.** `pd-mapper` (linux-msm) answers the DSPs' service-locator queries from
   `slpi*.jsn`. [A patch](../userspace/pkgbuilds/pico4-sensors/pd-mapper-jsn-dirs.patch) makes it
   read the `.jsn` files from directories given as arguments, because PIL-loaded DSPs have no
   remoteproc sysfs entries to find them through.
4. **Registry server.** `hexagonrpcd` (linux-msm/hexagonrpc) attaches to the sensors process
   (`sensorspd`) and serves it the sensor configuration and this headset's registry through
   FastRPC. Two changes, both in
   [hexagonrpc-vendor-kernel.patch](../userspace/pkgbuilds/pico4-sensors/hexagonrpc-vendor-kernel.patch):
   - **Downstream ioctl ABI.** hexagonrpc targets the upstream `misc/fastrpc.h` driver. A small,
     unit-tested translation layer drives the downstream `adsprpc` ABI instead: `GETINFO`
     selects the DSP channel, `INIT_ATTRS` with `ATTACH_SENSORS` attaches, and invocations use
     16-byte `remote_arg` entries. The two ABIs reuse ioctl numbers with different meanings,
     so a descriptor is only ever driven with the ABI it was attached with.
   - **Registry version file.** It also serves `sns_reg_version`, so the DSP accepts the
     existing registry.
5. **Client.** `pico4-imu` is a dependency-free Python client of the SNS client service (QMI
   service 0x190 over QRTR; requests and events inside the QMI messages are protobuf-encoded).
   It finds sensors by data type, reads their attributes, streams accel and gyro, and reports
   rate, timestamp monotonicity, gaps, gravity, gyro bias and a gravity-corrected orientation
   estimate.

Behaviour of this kernel and DSP:

- **Secure channel.** The SLPI FastRPC channel is marked secure, so `/dev/adsprpc-smd` returns
  `EACCES` and `hexagonrpcd` must use `/dev/adsprpc-smd-secure`.
- **Persistent listener.** The FastRPC listener has to stay alive. If a listener dies, the
  sensors process keeps it registered: a new `hexagonrpcd` cannot attach until the SLPI
  restarts.
- **Read-only registry.** `hexagonfs` is read-only. During startup the DSP tries to rewrite the
  registry groups (about 1650 refused `temp.json` writes), then stays quiet. Its factory
  calibration is read from the registry and stays in effect.
- **Rate selection.** The SSC runs a sensor at the next supported output data rate at or above
  the request and reports the requested rate as configured: 500 Hz runs the ICM-42688 at
  512 Hz, 512 or 1000 Hz at 1024 Hz.
- **Timestamps.** QTimer ticks (19.2 MHz), the same counter as the AP's arch timer.

| Unit (all in `pico4-sensors.target`) | Runs |
| --- | --- |
| `pico4-qrtr-ns.service` | `qrtr-ns -f` |
| `pico4-slpi.service` | `slpi-boot`, after the name service |
| `pico4-pd-mapper.service` | `pd-mapper /usr/lib/firmware` |
| `pico4-sensorspd.service` | `hexagonrpcd -f /dev/adsprpc-smd-secure -d sdsp -s -R /usr/share/qcom/pico4`, once udev has created the node (no devtmpfs) |

The units start only where `/sys/kernel/boot_slpi` and the sensor data exist: on the headset,
with a userspace built with sensor data. Elsewhere (QEMU) their conditions skip them.

## Monado driver

`pico4-monado` builds Monado 25.1.0 (pinned by commit) with the `pico4` driver from
[src/monado/](../src/monado).
[monado-pico4-driver.patch](../userspace/pkgbuilds/pico4-monado/monado-pico4-driver.patch)
registers the driver and the KMS target and carries the compositor changes described below.
Only the `pico4` driver and Monado's simulated HMD (headless fallback) are built. The package
makes Monado the system's active runtime (`/etc/openxr/1/active_runtime.json`) and builds it as a
service: `monado-service` owns the hardware, and the OpenXR runtime library is its IPC client.
SteamVR's `driver_monado` runs the same driver in-process.

The HMD ([pico4_hmd.c](../src/monado/pico4_hmd.c)):

- **Sensor service.** [pico4_ssc.c](../src/monado/pico4_ssc.c) is a C port of the `pico4-imu`
  client, limited to service lookup, sensor lookup, enable/disable and sensor events. Its unit
  test checks the encoder byte for byte against messages produced by the hardware-verified
  Python client.
- **Auto-probing.** The prober creates the HMD only where `/sys/kernel/boot_slpi` exists and the
  SNS client service answers; sensor lookups are retried (five attempts of 3 s) while the DSP
  starts. Elsewhere Monado continues without the driver: QEMU, other kernels, or a userspace
  built without sensor data.
- **Time base.** Sensor timestamps are QTimer ticks, and on this SoC the arch counter is the
  QTimer (`cntfrq_el0` = 19.2 MHz). The driver reads `cntvct_el0` between two `CLOCK_MONOTONIC`
  readings (the narrowest of 16 pairs) and maps each sample into Monado's timeline.
- **Axes.** The IMU's +X is up, −Z forward and −Y right, derived from the factory camera
  extrinsics (the RGB camera looks along −Z with image-up +X). The driver rotates samples into
  the OpenXR head frame (x right, y up, z back) as (−y, x, z).
- **Fusion.** The driver requests 1000 Hz (`PICO4_IMU_RATE`) and runs Monado's `m_imu_3dof` with
  gravity correction on every gyro sample. Two additions keep the estimate level and still:
  - **Warm-up.** The first accelerometer samples after enabling are off by about 2°. The driver
    skips 100 samples and starts the fusion level from the mean of the next 100.
  - **Gyro bias.** The bias (about 5 mrad/s here) tilts the estimate away from gravity faster
    than the correction pulls it back. The driver learns it over windows of 256 samples (a
    quarter second) in which gyro and accelerometer are quiet (standard deviations below
    0.01 rad/s and 0.05 m/s²), the mean rate is below 0.02 rad/s and |g| is within 0.3 m/s² of
    gravity, which excludes deliberate turns. Later windows update it with weight 0.2.

  Without them the tilt is 1.2–2° below the accelerometer's and the heading drifts at −0.10°/s.
- **Controller radio.** The HMD owns the station: it power-cycles it once at start, which makes
  the controllers reconnect and lets the camera sync start, and acknowledges its frames from a
  thread of its own. Without the station there are no controllers and no 6DoF.
- **Display description.** Two 2160×2160 views side by side (4320×2160) at 90 Hz, opaque. The
  field of view and the distortion come from the lens data
  ([Lens distortion and IPD](#lens-distortion-and-ipd)). Without that data the driver falls back
  to the stock runtime's symmetric 104° and no distortion.
- **Pose.** While Basalt tracks, the HMD reports position tracking and the 6DoF pose
  ([6DoF head tracking](#6dof-head-tracking)); otherwise the 3DoF orientation.

[`pico4-openxr-pose`](../src/xr/pico4-openxr-pose.c) `[SECONDS] [INTERVAL_MS]` is the pose
acceptance client. It uses the OpenXR loader with `XR_MND_headless`, so it needs no graphics. It
locates VIEW at the current time every INTERVAL_MS (default 10 ms) and prints JSON: the system
name, tracked/valid counts, rotation, heading change and rate, and the tilt from vertical. The
tilt is measured in STAGE because LOCAL keeps only the start yaw of a tilted head. With position
tracking it adds `position_tracked`, the path length, the largest distance from the start and
the last position. Exit status 0 means every location after the first second had a valid
orientation.

## Display output through Monado

Monado's direct-mode targets need `VK_KHR_display` or an X/Wayland server. Turnip's KGSL backend
has no display support, and the stock kernel's panels follow a vendor KMS contract anyway. The
driver therefore adds a compositor target,
[comp_window_pico4_kms.c](../src/monado/comp_window_pico4_kms.c). It presents with the fenced
sequence of [graphics.md](graphics.md); unfenced page flips show corrupted output on this kernel.

- **Selection.** The target comes first in Monado's list and is picked wherever `/dev/dri/card0`
  is `msm_drm` with a connected DSI output that has a 4320×2160 90 Hz mode. `PICO4_KMS=0` turns
  it off. It must become DRM master, so nothing else may hold the display.
- **Images.** Three linear Vulkan images (4320×2160, both eyes side by side), exportable as
  dma-bufs. Turnip allocates them from ION on this kernel. `msm_drm` imports them with PRIME and
  adds them as `XRGB8888` framebuffers (`XBGR8888` for RGBA formats). Compositor and display
  share the memory with no copy. One image is on screen, one is committed for the next vblank,
  one is rendered.
- **Layers.** The stock arrangement: four VIG (scaler) and two DMA layers, one per colour
  component and eye, routed by `sspp_layout` and blended per component (`blend_op`
  `layer_color`). The right eye reads the right half of the shared image.
- **Commits.** The first commit sets the 90 Hz mode and blocks. Every later commit flips all six
  layers without blocking (retried up to 20 times on `EBUSY`) and requests the CRTC release fence
  (`output_fence`) and the connector retire fence (`RETIRE_FENCE`).
  - Before each commit the target waits for the renderer's queue and for the previous commit's
    retire fence (500 ms timeout, counted in the statistics). Only then can the image acquired
    next be off screen.
  - One vblank event stays queued, because this vendor driver holds no vblank reference for
    commits on its own.
  - Pacing uses Monado's fixed-period pacer at the HMD's 90 Hz, phase-locked to the timestamps
    of the panel's vblank events. On its own the pacer counts periods from its creation, at an
    arbitrary phase against the panel; commits can then land just after vblank and miss a third
    of the refreshes.
- **Real-time threads.** Monado runs its compositor threads at real-time priority, and the stock
  kernel only grants that outside cgroups with the `cpu` controller: with
  `CONFIG_RT_GROUP_SCHED` on cgroup v2, `SCHED_FIFO` fails with `EPERM` there, even for root.
  systemd enables that controller by default, for CPU accounting and through the user manager.
  Drop-ins in `userspace/rootfs/etc/systemd` turn it off: `DefaultCPUAccounting=no` for the
  system and user managers, `Delegate=memory pids` for `user@.service`, and an empty
  `CPUWeight=` for the user's `app`, `background` and `session` slices. Units added later must
  not set `CPUWeight`/`CPUQuota` or delegate `cpu`. Without real-time threads the compositor
  competes with Basalt and misses 50–80 refreshes in 30 s, with client-fence stalls of up to
  300 ms; with them it misses 1–4.
- **GPU clock.** While the target drives the panels, it keeps the GPU at 587 MHz or faster
  through KGSL's `min_pwrlevel` (`PICO4_KMS_GPU_MIN_MHZ`, 0 turns it off) and restores the
  previous floor (stock: power level 6, 305 MHz) on exit. Under the compositor's load the stock
  `msm-adreno-tz` governor stays at 305 MHz.

Monado has two composition paths:

- **Fast path.** A frame with a single projection layer is distorted straight from the client
  images into the target.
- **Layer squasher.** Any other frame (for example a projection plus a quad layer) is first
  rendered into per-eye scratch images, which a distortion pass then copies into the target.

Each pass is limited by memory bandwidth (GPU timestamps around both passes): the memory runs at
its full 2,096 MHz, and the GPU reaches about 25 GB/s with real data. Without the following three
changes in the patch, two-layer frames run at 30 Hz, each frame staying on the panels for three
refreshes, which shows as visible stepping in the lenses:

1. **Scratch images without storage use** when the compositor uses its graphics pipelines.
   Turnip on Adreno 6xx does not compress (UBWC) storage images, and upstream Monado requests
   storage use for the compute path in every case. This cuts the squash pass from 12.7 to 7.7 ms
   and distortion from 9.0 to 6.7 ms.
2. **Scratch images at panel size** (2160² per eye) instead of the 140% view size. Larger ones
   only cost bandwidth. Squash then takes 4.0 ms and distortion 5.2 ms.
3. **The GPU clock floor** above: squash then takes 2.3 ms.

The compute path is off in this build (`XRT_COMPOSITOR_COMPUTE=1` brings it back): its writes
into the linear images take twice as long. Measured on the headset, 10 s runs:

| Frame content (140% eye buffers, 3024²) | Setup | GPU time per frame | Frames on the panels |
| --- | --- | --- | --- |
| One projection layer | compute, 305 MHz | 18.5 ms | 43 Hz |
| One projection layer | graphics, final | 6.2 ms | 90 Hz |
| Projection + quad layer | graphics, upstream scratch images | 22–23 ms | 30 Hz |
| Projection + quad layer | no storage use | 14.4 ms | 45 Hz |
| Projection + quad layer | + panel-size scratch, 305 MHz | 9.1–10.3 ms | 61–65 Hz |
| Projection + quad layer | + 587 MHz floor (final) | 8.0 ms | 90 Hz |

[`pico4-openxr-display`](../src/xr/pico4-openxr-display.c) `[SECONDS] [--projection-only]` is the
display acceptance client. It renders through `XR_KHR_vulkan_enable2`:

- **Projection layer.** A dim grid with a large L (left eye, red) or R (right eye, blue).
- **Quad layer.** A green and white checkerboard 1.5 m ahead in LOCAL space, so it stays put while
  the head turns. `--projection-only` leaves it out.
- **Content.** Static: each swapchain image is filled once by a buffer copy, so the frame loop
  measures the runtime, not the client.
- **Report.** The frame rate, the display period, late frames (an interval above 1.5 periods) and
  tracking, all after the first second. Exit status 0 needs frames at 95% of the display rate or
  better, at most 1% of them late, and tracked orientation.

With `PICO4_KMS_STATS=path` the target writes its statistics as JSON when it is destroyed:
commits and commit rate, the longest interval and late commits (over 1.5 periods), `EBUSY`
retries, retire waits and timeouts, vblanks, the panel rate and repeated vblanks, and the GPU
and retire waits. With `PICO4_KMS_DUMP=path` it also saves the image of commit
`PICO4_KMS_DUMP_FRAME` (default 300) as a PPM, downscaled by four, and records the mean colour of
each eye. `pico4-monado.service` sets both (`/run/pico4-monado/kms.json`, `frame.ppm`).

## Lens distortion and IPD

The lens model is the stock compositor's (`libpxrcompositor.so`, its warp-mesh builder, fed by a
configuration service). It works like the Oculus mobile SDK's:

- **Distortion.** A point on the panel, `p` metres from the lens centre, is seen at tan-angle
  `t·s(|t|²)`, with `t = p / F` and `s` a polynomial K0…K10 in `|t|²`. `F` (metres per tan-angle
  at the centre) and the coefficients (`A0`…`A10`) come per eye from `distortion_parameter` in
  `pvr/OpticalCalibrationData.json`. On this unit they match the stock table entry for the lens
  model and mould cavity, so they are not measured per headset.
- **Lens centre.** The panel centre, shifted by the file's `lens_center_gos` offset (below
  0.1 mm on this unit). The panels move with the lenses, so the IPD does not change the
  distortion.
- **Chromatic aberration.** Red and blue scale the green tan-angle by
  `1 + CA0 + CA1·r² + CA4·r` and `1 + CA2 + CA3·r² + CA5·r`. The six terms
  (`sdk_lens_chromaticAberration0…5`) come from the stock `SDK_Lens_ConfigFile.txt` in the system
  partition, which `extract-sensor-data.sh` copies next to the calibration. The correction is
  off by default (`PICO4_LENS_CA=1` turns it on), as on PICO OS, whose rule files set
  `sdk_chromaticStaus = 0`. With three separate texture reads per pixel, the two-layer test scene
  drops to 83–87 commits per second; geometry alone holds 87–88.

[pico4_lens.c](../src/monado/pico4_lens.c) implements this, and the driver gives it to Monado's
distortion mesh (`compute_distortion`, one UV per colour channel). The polynomial is only fitted
inside the panel's inscribed circle (the code allows 2% beyond it) and diverges in the corners.
Beyond that circle the driver evaluates it at the circle and lets the tan-angle grow steeply, so
the image ends cleanly at the edge of the rendered view. The field of view runs from the panel
edges (one eye's panel: 2160 px at 21.3 µm, 46 mm) through the green channel: about ±53.3° per
eye (106.7° horizontally), against the stock runtime's symmetric 104°.

The IPD comes from the GD32 MCU that drives the lens motor, on `/dev/ttyHS1` (115200 8N1, raw).
[pico4_ipd.c](../src/monado/pico4_ipd.c) sends one command once at start, after the GENI UART
clock-vote ioctl (`0x544D`) that the stock service also issues: `A0 08 00 00 00 A8` (get
position; the last byte is the XOR of the others). The reply `79 a_lo a_hi b_lo b_hi xor`
carries the lens separation in 0.01 mm and the Hall sensor reading, told apart by range (the
separation lies within 57.00–73.00 mm); the driver waits up to 200 ms for it. The module cannot
build any other frame: the MCU also takes motor moves, calibration writes and firmware. The
driver passes the separation to Monado's view poses and falls back to Monado's default if the
MCU does not answer.

## Controller radio ("station")

The nRF52832 on `/dev/spidev0.0` relays the controllers and generates the tracking cameras'
frame sync (FSIN). PICO's `stationservice` drives it through a vendor-modified `spidev` that
moves whole frames on the station's interrupt, so userspace only `write()`s, `select()`s and
`read()`s.

- **Frames.** 40 bytes: header (`AA` to the station, `BB` replies, `A5` notifications), target
  (`01`, the station) or notification type, command, payload (bytes 37–38 carry a sequence
  number), and a CRC-8 (Dallas/Maxim: reflected polynomial `0x8C`, initial value 0) over bytes
  0–38.
- **Acknowledgements.** As the stock service does, every frame from the station except stream
  notifications (`A5 01`) is acknowledged with command `FA` carrying its bytes 37–38.
- **Data path.** Controller IMU, buttons, sticks, touch and battery do not come over SPI. The
  station streams them to the sensor DSP, where the SLPI firmware exposes them as the SSC sensor
  `ble_mcu`. The driver enables that sensor through the same client as the head IMU. SPI is
  only the control plane.
- **Bring-up.** The vendor spidev's power ioctl (`_IOW('k', 9, int)`, `0x80046B09`; 1 on, 0 off)
  power-cycles the station: off, 100 ms, on. The station announces itself (`A5 06`: protocol,
  radio type) within 3 s and waits for the camera frame rate (command `19`, payload `01 00 01`:
  50 Hz as stock, device type 0) before it accepts camera commands. The controllers reconnect on
  their own.
- **Firmware.** The station reports firmware 2.96, the version PICO OS ships.

The same link carries station and controller firmware updates. The C module
([pico4_station.c](../src/monado/pico4_station.c)) and the `pico4-station` tool therefore build
frames only from an allowlist:

| Command | Payload | Purpose | C module | `pico4-station` |
| --- | --- | --- | --- | --- |
| `FA` | bytes 37–38 of the acknowledged frame | acknowledge | yes | yes |
| `19` | `01` (50 Hz) or `02`, device type, `01` | answer to the announcement | yes | yes |
| `04` | `01 00 02 00` (exposure-first-row flag 2 as on PICO OS, resolution 0) | camera init | yes | yes |
| `04` | `02`, camera (0 all, 1–4), `01` on / `02` off | camera stream | yes | yes |
| `04` | 28 bytes: `03`, 8 × (u16 exposure, u8 gain) in bytes 1–12 and 16–27, a u16 and an event index in 13–15 ([cameras](#tracking-cameras)) | camera exposure and gain | yes | yes |
| `04` | `07 00`, rate | camera frame rate | – | yes |
| `01` | none (sequence number in bytes 37–38) | firmware version | – | yes |
| `07` | controller, `01` on / `00` off | controllers' IMU stream to the DSP | – | yes |
| `22` | exactly `01 00` (reads; a first byte of 0 would set the tracker work mode) | work mode | – | yes |
| `29`, `30` | none | crash info, whitelist (read-only queries of the stock wake-up) | – | yes |

Neither can send firmware, bootloader or flash commands, and neither touches the boot pin. The C
module's unit test checks its frames against golden bytes from the hardware-verified Python tool
and that every other command is refused.

## Tracking cameras

The four OV7251s (640×480 mono, global shutter, RAW10) only exist behind Qualcomm's Spectra
camera stack (CSL), which has no V4L2 capture path. [pico4_cam.c](../src/monado/pico4_cam.c)
rebuilds the stock tracking pipeline from the public CSL UAPI headers (CAF LA.UM.9.12 kona, from
a pinned public kernel tree, the closest public match of the stock kernel):

- **Discovery.** The nodes are found at run time from the media graph and `CAM_QUERY_CAP`, not
  from fixed node numbers.
- **Probe.** All four sensors are probed (`CAM_SENSOR_PROBE_CMD`: I2C address, chip-ID check
  `0x300A` = `0x7750`, power sequence) before any is powered for streaming: reset and standby
  are shared GPIOs, and a probe ends with a power-down.
- **Sessions.** One CSL session per camera:
  - the sensor through the CCI driver: acquire (power-up) and the stock OV7251 register table
    (mainline's VGA 60 fps table with PICO's module differences, 24 MHz MCLK), including the
    stock per-module flip and mirror;
  - its CSIPHY: one lane at 480 Mbit/s, settle count 14, combo mode where two sensors share a
    PHY;
  - an IFE-lite with one RDI output (raw data, no pixel pipeline, no ISP firmware), on a free
    CSID-lite/IFE-lite pair.

  PICO's kernel allows four links per session where the public header says two; the build
  patches the header (`MAX_LINKS_PER_SESSION`).
- **IFE-lite.** The RDI only produces start-of-frame events once the INIT packet enables the
  CAMIF-lite module with a CDM register write; the kernel never does. CamX does the same. The
  sensor sends RAW10 (CSI data type `0x2B`), stored as `PLAIN16_10`. Packet opcodes follow CamX,
  `(device type << 24) | opcode`; the ISP decodes them as `(opcode + 1) & 0xF`, so INIT is
  `0x0F000000` and UPDATE `0x0F000001`.
- **Requests.** The ISP is alone on its request-manager link: the sensor follows the frame sync,
  not requests. Each camera keeps 8 requests in flight with bubble recovery; with 6, about 10% of
  frames are lost. Buffer completion comes through `cam_sync` fences, each with a user payload,
  so one `poll()` covers all cameras. The timestamp is the start-of-frame event's boot time,
  which the driver converts to Monado's clock.
- **Cached buffers.** Frames land in cached buffers, which are invalidated
  (`CAM_REQ_MGR_CACHE_OPS`) before the CPU reads them. With uncached buffers, reading frames
  stalls buffer delivery for about 100 ms every 10–13 frames (latency 45–67 ms mean). With cached
  buffers the latency is a constant 16–18 ms.
- **Kernel console.** The camera driver logs every start of frame at `KERN_INFO`. On the serial
  console each line costs about 13 ms in interrupt context, and capture collapses after about
  20 frames. `/etc/sysctl.d/50-pico4-console.conf` sets `kernel.printk = 5 4 1 7`, which keeps
  informational messages off the console.
- **udev.** systemd's `60-persistent-v4l.rules` is overridden. Its `v4l_id` probe opens the ICP
  node at boot, whose firmware load fails. That blocks PICO's CAM-HUB work queue, and every
  later open of `cam_sync` times out.

| CSL slot | Module (8-bit I2C address) | Factory camera, as worn | Flip and mirror in the register table |
| --- | --- | --- | --- |
| 0 | left (`E2`) | cam0, top left | yes |
| 1 | right (`E4`) | cam2, bottom right | no |
| 2 | "slave" (`E6`) | cam1, top right | no |
| 3 | "master" (`E8`) | cam3, bottom left | yes |

The station drives the sensors, which run in FSIN slave mode:

- **Start.** Each run resets the station, then sends camera init (command `04`, payload
  `01 00 02 00`) and stream on for all cameras (`04`, `02 00 01`) once all four sensors stream.
  The station initialises them over its own I2C and refuses unless all four are on. Frames
  arrive at 49.99 Hz.
- **Exposure.** The sensors ignore their own exposure registers in this mode. Exposure and gain
  go through the station's camera command `04 03`, laid out as the stock `CameraSetPara`: per
  camera, exposure in sensor lines of 19.3 µs and gain in 1/16 steps (16 = 1×), cameras 0–3 in
  bytes 1–12 and again in 16–27; the station sends no reply. Exposure plus the 9 ms readout must
  fit the 20 ms period; 3,000 lines stall the sensors. The station's default exposure, about
  3 ms, leaves rooms nearly black.
- **Alternating exposures.** Every other frame is a short exposure in which only the
  controllers' IR LEDs show (mean 23–28 of 1023). PICO uses those frames for controller
  tracking; the scene frames run at 25 Hz.

Covering one lens at a time darkens exactly one slot, which gives the mapping in the table
(`PICO4_TCAM_SLOTS`, default `0,2,1,3` for cam0–cam3). Under it, a point seen by both lower
cameras triangulates with a 0 mm ray gap at 15 cm, using the images as captured; every rotated
or swapped variant gives negative depths or gaps of 5–10 cm. The factory model (omni + radtan,
Kalibr `T_BS` to the IMU frame) is refitted to Kannala-Brandt (KB4, same principal point) for
Basalt in [pico4_tcam.c](../src/monado/pico4_tcam.c), at 0.21–0.52 px RMS.

[`pico4-cam`](../src/xr/pico4-cam.c) tests the cameras on their own:

- `list`: CSL nodes, slots, PHYs, the ISP's IOMMU handles and the media entities; powers nothing.
- `probe [SLOT...]`: power up, read the chip ID, power down.
- `capture`: resets the station, starts the frame sync and streams until each camera delivered
  N frames. Saves a scene frame of each camera (8-bit PGM and 16-bit raw) and prints per camera
  the frames, errors, frame rate from the start-of-frame timestamps, their monotonicity and the
  sample statistics. `--stats-log FILE` logs per frame the slot, start-of-frame time, brightness,
  latency and request; `--exposure LINES --gain G` sets the station exposure.

## 6DoF head tracking

[pico4_slam.c](../src/monado/pico4_slam.c) feeds Monado's SLAM tracker. Monado loads Basalt
through its VIT interface (`libbasalt.so` from the
[`pico4-basalt`](../userspace/pkgbuilds/pico4-basalt/PKGBUILD) package, Monado's Basalt fork
pinned by commit). Without `libbasalt.so` or the station the HMD stays on 3DoF.

- **Frames.** Frames of one sync pulse are grouped by start-of-frame time (within 4 ms), and the
  controller-LED frames are skipped. The phase is told apart by the first camera's brightness
  (trusted after 8 frames of each phase) and counted in sync periods, so it survives dropped
  frames. Fed the LED frames as well, Basalt drifts by 430–720 m in 30 s. Each frame is converted
  to 8 bits: black level (16 of 1023) removed, slowly adapting digital gain (1–8×, aiming at a
  mean of 70). Sets go out in camera order with one timestamp, as Basalt requires; a set that
  misses a camera is dropped.
- **Auto exposure.** A slow loop sets the station exposure from the first camera's brightness
  every 25 sets (half a second). It starts at 300 lines and 1× gain and aims at a mean signal of
  250 above black (10-bit), changing at most 4× per step and ignoring changes within 10%. It
  raises exposure first, up to 400 lines (longer exposures smear more in motion), then gain up
  to 4×; more gain mostly adds noise for the optical flow.
- **Upright images.** The lower pair is mounted 180° apart, as the calibration's relative roll
  says. Basalt's stereo matching tracks patches between cameras and cannot follow that, so the
  driver turns each image that is upside down relative to the first camera's. It rotates that
  camera's calibration to match (`pico4_tcam_rotate_180`).
- **IMU.** Raw head IMU samples in the factory body frame, which is the frame of the camera
  extrinsics; every second sample goes to Basalt (`PICO4_SLAM_IMU_DIVISOR`). The calibration
  given to Basalt declares the DSP's rate as 512 Hz before that halving, and ICM-42688-class
  noise (standard deviations: accelerometer 0.016, gyro 0.0016; bias walk 0.001 and 0.0001);
  Basalt estimates the biases.
- **HMD origin.** Basalt tracks the body (IMU) frame. The HMD origin between the eyes sits at
  (−0.025, 0.052, 0.04) m in the body frame (x up, y left, z back): centred between the cameras,
  a little below and behind them. It only affects how position follows head rotation.
- **Decoupling.** Basalt's queues are bounded and block their producers when its optical flow
  falls behind. Two feeder threads therefore stand between the driver and Basalt. One takes the
  newest frame set (an older waiting set is dropped), the other drains a ring buffer of 2,048 IMU
  samples (the oldest is dropped when it is full). Neither the cameras nor the head IMU thread
  (3DoF, controllers) ever waits for Basalt.
  - **Why two feeders.** One feeder for both deadlocks: a blocked frame push holds back the IMU
    samples that Basalt's estimator needs before its optical flow can take the next frame.
  - **Shutdown.** The driver stops the frames first and gives Basalt one second to work through
    its queue (at most 10 sets) before stopping it. Basalt's optical flow carries on after its
    estimator has finished and then predicts from an invalid state, which aborts (in Sophus) or
    crashes the process when the optical flow lags behind.
- **Priority.** Without a config file, Basalt's TBB pool uses every core. Basalt's threads and
  feeders therefore start at nice 19, restricted to cores 0–5 (the XR2's four small and two of
  its big cores), which leaves the two fastest cores to the compositor and the app. They inherit
  both from the thread that starts them, TBB workers included, and TBB sizes its pool from that
  CPU set; `PICO4_SLAM_CPUS` changes the set. With the compositor running, Basalt skips some sets
  (30–73 of 757 in 30 s).
- **Pose.** Basalt's pose (IMU frame, z-up world) is converted to the HMD frame and Monado's y-up
  world. While Basalt reports a tracked position, the driver uses it; otherwise the 3DoF fusion.

Basalt is built for `armv8.2-a`, the XR2's Cortex-A77/A55 level; its default `-march=native`
describes the build host, and a build under QEMU emits SVE instructions. Arch's OpenCV links its
highgui module against Qt without depending on it, and Monado and Basalt load that module, so
`qt6-base` is part of the image.

## Controllers

[pico4_blemcu.c](../src/monado/pico4_blemcu.c) parses the `ble_mcu` events, laid out as the
stock tracking service's parser (`SlpiDataParserV2`) reads them. Each is one 200-byte radio
packet: `5A`, format version, station time in µs (bytes 4–11, little endian), the length of the
records (byte 12), then records `[length][type][id][time offset][data]`. The id's low six bits
name the device (1 left controller, 2 right, 3–5 trackers); bit 7 adds a time offset before the
data (2 bytes, 4 with bit 6), in µs before the packet's station time. Record types:

- **IMU** (type 0): gyro (3 × int16) at 16.4 LSB per °/s (±2000°/s), accelerometer (3 × int16)
  at 1,024 LSB per g, temperature (raw / 132.48 + 25 °C) and a 16-bit counter.
- **Inputs** (type 1): trigger, stick y (255 up), stick x (255 right) and grip as bytes, in that
  order (sticks centred at 128). One byte of buttons: home (bit 0), menu, A/X, B/Y, grip, stick
  click (bit 5), trigger (bit 7). One byte of touch: A/X (bit 0), B/Y, stick, thumb rest,
  trigger (bit 4).
- **Battery** (type 2): one byte.

Other types belong to trackers and diagnostics.

[pico4_controller.c](../src/monado/pico4_controller.c) makes each controller a Monado device
(`PICO 4 left/right controller`) with Monado's existing PICO 4 controller profile
(`/interaction_profiles/bytedance/pico4_controller`): A/B or X/Y click and touch, menu, system,
trigger click, value and touch, thumbstick click, touch and axes, squeeze click and value, grip
and aim poses. IMU samples are timed by the SSC event time minus the record's offset.
Orientation comes from `m_imu_3dof`, levelled from the mean gravity of the first 100 samples,
with the IMU axes mapped to the grip frame (x right, y up, z back along the handle) as `x,-z,y`.
Positions are a placeholder: 0.18 m to the side, 0.35 m below and 0.30 m in front of the head,
turned with its heading. Real positions need the IR-LED tracking. Haptics are accepted but do
nothing.

## Build

Extract the inputs from **your own** EDL backup ([device-preparation.md](device-preparation.md)),
as root on Linux/WSL. This needs `debugfs` and `mcopy`; the script first verifies
`lun0/super.bin`, `lun0/persist.bin` and `lun4/modem.bin` against the backup manifest and never
touches the headset:

```sh
bash scripts/extract-sensor-data.sh backup/stock assets/sensor-data-5.13.7
```

| Output | Content (source partition) |
| --- | --- |
| `firmware/slpi.mdt`, `slpi.bNN`, `slpi*.jsn` | sensor DSP image and protection-domain maps (modem) |
| `sensors/config/*.json`, `sensors/sns_reg.conf` | sensor driver configuration and registry layout (vendor) |
| `sensors/registry/`, `sns_reg_version` | this headset's registry with its factory calibration (persist) |
| `calibration/pvr/` | factory camera, IMU and lens calibration (persist) |
| `calibration/pxrconfig/SDK_Lens_ConfigFile.txt` | stock lens configuration with the chromatic aberration terms (system) |
| `manifest.json` | sources and SHA-256 of every file |

Then build the userspace with it (`make userspace` uses these defaults; see
[development.md](development.md#the-userspace-build) and [userspace.md](userspace.md)):

```sh
bash scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
```

The image gets the DSP firmware in `/usr/lib/firmware` and the sensor tree and calibration in
`/usr/share/qcom/pico4/`, which `hexagonrpcd` serves to the DSP and the driver reads. The
extracted data and the archive are device-specific and partly proprietary: keep them private.

- **`pico4-sensors`**: qrtr-ns, pd-mapper and hexagonrpcd from pinned linux-msm commits plus this
  project's patches. Its `meson test` covers the FastRPC ABI translation.
- **`pico4-basalt`**: Basalt and the submodules the library needs, at pinned commits.
- **`pico4-monado`**: Monado plus `src/monado` and `src/xr`. It fetches the Spectra camera UAPI
  headers from a pinned public kernel tree. Its build runs the C tests, because the userspace
  build uses `--nocheck`: `test_pico4_ssc` (encoder against the Python client's messages),
  `test_pico4_lens` (lens model, IPD request and reply parser) and `test_pico4_tracking` (station
  frames against golden bytes and the allowlist, the calibration parser, the Kannala-Brandt
  refit and the 180° turn, the controller packet parser).

The host tests (`python -m unittest discover -s tests`) cover `pico4-imu`'s protobuf/QMI
encoding, a simulated DSP (lookup, attributes, streaming, disable), the IMU acceptance limits,
the orientation filter, the runner's validators and the super-image reader.

## Run

The sensor units start at boot. Nothing cools the headset actively under this userspace
([cooling.md](cooling.md)): keep display and SLAM runs short.

```sh
pico4-imu list accel gyro mag          # sensors and attributes the DSP reports
pico4-imu stream --seconds 10 --check --orientation [--csv imu.csv] [--rate 1000]
pico4-imu raw ble_mcu                  # any sensor type, events decoded generically
```

`stream` requests 500 Hz unless `--rate` says otherwise. `--check` fails unless both streams
deliver at least half the expected samples, the DSP configured at least 95% of the requested
rate, the measured rate is within 5% of the configured one, no timestamp goes backwards, |g| is
within 0.5 m/s² of 9.81 and each component of the mean angular rate is below 0.05 rad/s. These
limits are for a headset lying still.

`python -m pico4 hwtest flash-run --userspace ARCHIVE …` (also inside
`python -m pico4 cycle --userspace ARCHIVE`; [hardware-testing.md](hardware-testing.md)) checks
an archive built with sensor data after the userspace check: it records the sensor units' status
and `pico4-imu list accel gyro`, then requires
`pico4-imu stream --rate 500 --seconds 10 --check`. With `pico4-monado` installed it continues,
each client on a fresh Monado service:

- **`pico4-openxr-pose 10 10`.** The PICO 4 system with orientation tracking, no invalid pose after
  the first second, a tilt within 2° of the accelerometer's (from the IMU check) and a heading
  drift below 0.5°/s.
- **`pico4-openxr-display 30`**, with both layers, so it exercises the layer squasher:
  - client: the PICO 4 system, frames at 95% of 90 Hz or better, at most 1% of them late;
  - target: the frames went through `pico4_kms`, the panels refreshed within 1% of 90 Hz, at least
    85.5 commits per second, no retire fence timeout;
  - eyes: in the scanned-out image the left half is red and the right half blue (mean difference
    at least 20), so each eye's content is on its own panel.

  The check runs 30 s because at session end the compositor waits up to a few hundred
  milliseconds on the last frame's fence, which would weigh on a shorter window. The runner
  cannot see the panels: that the image appears in the lenses needs a person looking.

OpenXR clients reach the driver through the Monado service; as root (the session user's clients
find the socket in their own runtime directory):

```sh
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-pose 10 10
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 10 [--projection-only]
systemctl stop pico4-monado.service; cat /run/pico4-monado/kms.json
```

The service writes the KMS statistics and the frame dump when it stops; they cover its whole
lifetime, so a measurement starts a fresh service. Basalt's diagnostics go to the service's
journal; both clients keep their stdout for the JSON report and point file descriptor 1 at
stderr, for runtimes that run in-process. With the cameras and Basalt, a still headset stays
within centimetres only in a scene the lower cameras can see in depth, not in front of a surface
a few centimetres away.

The driver reads its options from the environment of the process that runs it: `monado-service`
(`pico4-monado.service`) or SteamVR's `driver_monado`, not the OpenXR client. For example,
Basalt's trajectory as CSV files (`tracking.csv` among them) from a headless pose session, with
Monado's `SLAM_WRITE_CSVS`/`SLAM_CSV_PATH` and the null compositor instead of the KMS target:

```sh
systemctl stop pico4-monado.service; mkdir -p /tmp/slam
systemctl set-environment SLAM_WRITE_CSVS=1 SLAM_CSV_PATH=/tmp/slam/ XRT_COMPOSITOR_NULL=1
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-pose 30
systemctl stop pico4-monado.service
systemctl unset-environment SLAM_WRITE_CSVS SLAM_CSV_PATH XRT_COMPOSITOR_NULL
```

| Variable | Default | Effect |
| --- | --- | --- |
| `PICO4_LOG` | `info` | driver log level; `debug` adds latency, tilt and gyro bias every 5000 samples |
| `PICO4_IMU_RATE` | 1000 | head IMU rate requested from the DSP, Hz |
| `PICO4_CALIBRATION_DIR` | `/usr/share/qcom/pico4/calibration` | factory lens and camera calibration |
| `PICO4_LENS_CA` | off | chromatic aberration correction |
| `PICO4_KMS` | on | KMS compositor target |
| `PICO4_KMS_GPU_MIN_MHZ` | 587 | GPU clock floor while the target drives the panels; 0 leaves the governor alone |
| `PICO4_KMS_STATS` | unset | JSON statistics written when the target is destroyed |
| `PICO4_KMS_DUMP`, `PICO4_KMS_DUMP_FRAME` | unset, 300 | PPM of that commit's image, plus the eye means |
| `XRT_COMPOSITOR_COMPUTE` | off in this build | Monado's compute composition path |
| `PICO4_SLAM` | on | 6DoF |
| `PICO4_SLAM_CAMS` | `0,1,2,3` | factory cameras for Basalt in tracker order, at least two; Basalt matches the first against the others |
| `PICO4_TCAM_SLOTS` | `0,2,1,3` | CSL slot of factory cameras cam0–cam3 |
| `PICO4_SLAM_UPRIGHT` | on | turn images that are upside down relative to the first camera's |
| `PICO4_SLAM_DIVISOR` | 1 | use every Nth scene frame set |
| `PICO4_SLAM_IMU_DIVISOR` | 2 | use every Nth head IMU sample |
| `PICO4_SLAM_MAX_EXPOSURE` | 400 | longest station exposure, sensor lines |
| `PICO4_SLAM_BRIGHTNESS` | 70 | mean 8-bit brightness the digital gain aims for |
| `PICO4_SLAM_CPUS` | 63 (cores 0–5) | CPUs for Basalt and its feeders, decimal bit mask; 0 = no restriction |
| `PICO4_SLAM_DUMP` | unset | directory for PGM frames of every 500th set |
| `PICO4_CONTROLLER_AXES_LEFT`, `_RIGHT` | `x,-z,y` | controller IMU axes in the grip frame |

The cameras and the radio on their own (stop the Monado service first):

```sh
pico4-cam list                                    # CSL nodes, slots, PHYs; powers nothing
pico4-cam capture --frames 1500 --out /tmp/cams   # all four at 50 Hz, JSON summary, one scene frame each
pico4-cam capture --exposure 300 --gain 32 --stats-log /tmp/frames.txt
pico4-station version                             # station firmware version
pico4-station listen --seconds 5                  # frames the station sends
```

## Verified results

On the headset (PICO OS 5.13.7, stock kernel), lying still unless stated otherwise.

| Check | Result |
| --- | --- |
| SLPI boot from the headset's firmware | `Brought out of reset`, FastRPC and QRTR channels up, subsystem `ONLINE` |
| Sensors process | `hexagonrpcd` attached on `adsprpc-smd-secure`; SNS client service (QMI 0x190) on QRTR node 9 |
| Sensors reported | ICM-42688 accel/gyro 12.8–512 Hz, ICM-42688 temperature, AK0991x magnetometer 10–100 Hz |
| `pico4-imu stream --check`, 500 Hz, 10 s | 5,066 samples per stream at 511.95 Hz, 0 gaps, 0 non-monotonic timestamps; \|g\| 9.796–9.797 m/s²; mean \|ω\| 0.005–0.006 rad/s |
| 1 kHz request | DSP configures 1000 Hz, measured 1023.9 Hz |
| `pico4-imu` orientation estimate | roll/pitch −11.1°/−83.6° against the accelerometer's −11.9°/−83.5°; uncorrected heading drift about −0.07°/s (−0.36° in 5 s, −0.69° in 10 s) |
| Guided 90° turn, edge-aligned (`pico4-imu`) | plateau +90.3 to +90.5° (scale error below 0.6%); turned back to the start, the filter heading read +0.35° after 40 s |
| OpenXR system | `Monado: PICO 4`, runtime `Monado(XRT) … v25.1.0`; orientation tracking, position tracking with Basalt |
| Head pose, 3DoF, 60 s at 100 Hz | 5,882 locations, none invalid after the first second; rotation 0.009°, heading −0.006° (−0.0001°/s); 10 s runs drift 0.0002–0.0005°/s |
| Tilt against the accelerometer | within 0.2° (for example 9.12° against 8.96–9.11°, 4.02° against 4.06°); with Basalt drifting in front of a near surface 2.01° against 0.65°, heading 0.11°/s |
| Latency, sensor sample to pose | 0.94–1.16 ms |
| Learned gyro bias | (0.0039, −0.0009, −0.0056) rad/s in the head frame |
| KMS target | `pico4_kms` on connector 29 / CRTC 140, mode `4320x2160x90`, six stock layers; images `B8G8R8A8_SRGB`, linear, pitch 17,408 bytes, imported from ION dma-bufs |
| One projection layer (3024² per eye), 60 s | client 5,400 frames at 90.000 Hz, 3 late (longest interval 19.5 ms); 5,343 commits; panels 89.998 Hz; no retire fence timeout; GPU 7.2 ms per frame, 38 °C afterwards |
| Projection + world-locked quad, 90 s, head turning | client 8,100 frames at 90.000 Hz, 7 late; 87.0 commits per second (about 3% of the refreshes repeat a frame, none for more than one refresh; longest gap 21.9 ms); panels 89.998 Hz; no retire timeout; GPU 8.2 ms per frame |
| Two layers with Basalt running, 30 s | 2,698 frames at 89.996 Hz, 2 late; 88.8 commits per second; panels 89.998 Hz; no retire timeout; Basalt skipped 73 of 757 sets |
| Scanned-out image | left half red L, right half blue R; eye means for example (70, 40, 25) and (26, 41, 72) |
| Seen in the lenses | one layer: a steady image, red L only in the left eye, blue R only in the right eye. Two layers, head turning: the checkerboard has a stereo offset between the eyes, stays fixed in the room and moves smoothly, with no visible latency |
| Lens correction, seen in the lenses | grid lines and checkerboard edges straight, image steady at 90 Hz; colour fringes not judged (each eye showed one colour) |
| Field of view from the lens data | left eye −53.32/+53.38° horizontal, +53.58/−53.12° vertical; right eye −53.49/+53.17°, +53.41/−53.25° |
| IPD | 71.99 mm (Hall reading 888, consistent with the factory table) with the wheel at its widest, 72 mm |
| Station | power cycle, announcement and frame-rate answer every time; firmware 2.96 |
| Capture, 30 s, all four cameras | 1,500/1,500 frames each, 0 errors, 49.99 Hz, monotonic timestamps; start of frame to delivery 17.5 ms mean, 27.2 ms max |
| Exposure through the station | scene frames mean 76 → 446 (of 1023) at 400 lines and 4× gain; LED frames 23–28 either way |
| Slot mapping | cover test: one slot dark per covered lens; a logo seen by the lower pair triangulates with a 0 mm ray gap at 0.14/0.16 m |
| Controllers in OpenXR | both as `PICO 4 left/right controller`; per controller about 500 IMU samples and 100 input packets per second |
| Controller inputs, each pressed in turn | trigger, grip, A/B/X/Y, menu, home, stick click and touch (A/X, B/Y, stick, thumb rest) decoded as pressed |
| Sticks pushed right, left, up, down, both controllers | x (record byte 2): 255, 0; y (byte 1): 255, 0 |
| SLAM feed, 30 s, four cameras, no compositor | 744 frame sets at 25 Hz, 0–1 incomplete, none skipped while Basalt was busy, no IMU sample dropped; frame to tracker 15–23 ms |
| 6DoF: headset on a table in a lit room, slid 50 cm (measured) and back, twice | 0.52 m and 0.49 m; back at the start within 2–4 cm; vertical within 1 cm on the second slide; at rest within millimetres over 15 s |
| 6DoF: held at head height, walking about 1 m forward/back and sideways | moves tracked in the right axes and back near the start (0.1–0.2 m); still phases within 3 cm; one run jumped 0.8 m while Basalt initialised in the first seconds |
| 6DoF: lowered from head height onto a table | 0.75 m down, then still within millimetres for 30 s |
| 6DoF with `PICO4_SLAM_UPRIGHT=0`, walking | tracks as well in this test; the turn stays on as the documented geometry |
| 6DoF, lower cameras about 15 cm from a surface | 1.6–4.8 m drift in 30 s (6.5 m in a 10 s run): no stereo depth, not an accuracy test |
| Acceptance cycle (`flash-run --userspace`, then stock restoration) | passed: archive received in 86–105 s, SSH 112–123 s after the start of the transfer; systemd `running`, 0 failed units, storage read-only, USB link unchanged, Turnip 65,536 pixels; IMU, pose and display checks as above (two layers, Basalt running); stock boot verified by EDL readback, stock ABL restored, PICO OS started |
| QEMU, same archive (4.19 without SLPI or `msm_drm`) | self-test passed; the sensor units and Monado's PICO 4 prober stay inactive |

## Next steps

1. **6DoF tuning.** Worn sessions with longer paths are untested. Basalt's first seconds can
   jump before it has initialised, so the driver could hold the 3DoF pose until Basalt has
   settled. A still headset facing a surface a few centimetres away drifts by metres (no stereo
   depth); the driver could fall back to the 3DoF position while Basalt reports too few
   landmarks. Other knobs: `optical_flow_image_safe_radius` for the fisheye borders, the IMU
   noise, and a Basalt config file (the VIT build runs Basalt's built-in defaults). The
   calibration declares the head IMU at 512 Hz (halved for Basalt), while a 1 kHz request runs
   it at 1024 Hz in `pico4-imu`; which rate the driver's stream has is not recorded.
2. **Controllers in applications.** In SteamVR ([session.md](session.md)) both controllers appear
   with the PICO 4 input profile and turn with the wrists while worn; they stay where the arm
   model puts them. SteamVR uses its generic bindings for this controller type; clicking in its
   dashboard is unchecked. Haptics need a station command that is not identified; it is not in
   the allowlist.
3. **Controller positions.** The cameras already deliver the IR-LED frames. Constellation
   tracking needs the LED layout of the rings, which is in the controllers' calibration or PICO's
   encrypted tracking config, and a tracker (Monado has one for WMR controllers).
4. **Composition headroom.** The squasher fits the 11 ms period only with the GPU clock floor.
   Remaining options:
   - compressed (UBWC) scanout images, using the display's compressed formats;
   - compressed client swapchain images;
   - composing the layers directly through the distortion mesh, without scratch images;
   - cheaper chromatic aberration correction (it costs 2–5% of the refreshes in the two-layer
     scene, hence opt-in);
   - display-timing pacing from the retire fences' timestamps.
