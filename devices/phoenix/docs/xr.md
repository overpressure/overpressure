# Native XR hardware (roadmap step 5)

The PICO 4's XR hardware under Linux, on the stock kernel (4.19.81-perf+, PICO OS 5.13.7) and
with free software only. The head IMU streams through the sensor DSP. Monado 25.1.0 with this
project's `pico4` driver gives OpenXR clients the head pose (3DoF fusion at 1 kHz, 6DoF from
Basalt on the four tracking cameras), shows their frames on both panels through the stock KMS
driver with the factory lens model and the IPD read from the lens motor, and provides both
controllers with inputs, their 6DoF pose from PICO OS's LED tracking (reimplemented), PICO OS's
own render models and vibration. Monado runs as a system service ([session.md](session.md#monado-as-a-service)); SteamVR
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
>   calibration writes and firmware. Never send such commands. The station modules, the IPD
>   reader and `pico4-fan` ([cooling.md](cooling.md)) build frames only from allowlists; keep it
>   that way.
> - **One owner for cameras and station.** The OV7251s stream only while the station pulses
>   FSIN, so every capture resets the station first. One process at a time may own the cameras
>   and the station: stop `pico4-monado.service` (or Valve's session) before `pico4-cam` or
>   `pico4-station`. The driver and `pico4-station` hold an exclusive lock (`flock`) on
>   `/dev/spidev0.0`; a second owner is refused before it can reset the station, and a second
>   runtime then runs without controllers and cameras.

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

`persist` holds per-unit calibration, in formats that Monado and Basalt can use with little
conversion:

- `pvr/mav0/cam0..cam4/sensor.yaml`: EuRoC/Kalibr-style camera models (omni ξ + radtan
  distortion, intrinsics, extrinsics `T_BS` to the body frame, 640×480 @ 30 Hz). The factory
  report `pvr/mav0/info.txt` gives reprojection errors below 0.25 px.
- `pvr/mav0/imu0/sensor.yaml`, `pvr/ImuData.txt` (a 512 Hz accel/gyro reference capture),
  `pvr/gyroBias.txt` (temperature table), plus the SSC registry's factory gyro/accel calibration
  under `sensors/registry`, which the DSP applies itself.
- `pvr/OpticalCalibrationData.json`, `pvr/lens/*`, `ipdcalidata.txt` (IPD ADC → position):
  display colour, lens and IPD data used by `pxrhmdservice`.

The image does not carry it: `persist` is mounted read-only at `/mnt/vendor/persist` at boot,
and `/usr/share/qcom/pico4/calibration/pvr` and the sensor registry link there
([userspace.md](userspace.md#the-headsets-own-data)), so every headset reads its own.

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
   FastRPC. Four changes, all in
   [hexagonrpc-vendor-kernel.patch](../userspace/pkgbuilds/pico4-sensors/hexagonrpc-vendor-kernel.patch):
   - **Downstream ioctl ABI.** hexagonrpc targets the upstream `misc/fastrpc.h` driver. A small,
     unit-tested translation layer drives the downstream `adsprpc` ABI instead: `GETINFO`
     selects the DSP channel, `INIT_ATTRS` with `ATTACH_SENSORS` attaches, and invocations use
     16-byte `remote_arg` entries. The two ABIs reuse ioctl numbers with different meanings,
     so a descriptor is only ever driven with the ABI it was attached with.
   - **Registry version file.** It also serves `sns_reg_version`, so the DSP accepts the
     existing registry.
   - **Static processes.** `-P NAME` starts a static process from the DSP's image instead of
     attaching, as PICO OS's library does for the ADSP's audio process
     ([userspace.md](userspace.md#audio)).
   - **`ftell`.** The file server answers `ftell` (method 8 of the file interface), which the
     audio process reads its configuration with; unanswered, the server ends its session.
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
| `pico4-sensorspd.service` | `hexagonrpcd -f /dev/adsprpc-smd-secure -d sdsp -s -R /usr/share/qcom/pico4`, once udev has created the node (no devtmpfs); restarted whenever it ends, because it ends successfully when the DSP restarts and the restarted DSP needs it again |
| `pico4-imu-ready.service` | `pico4-imu wait`: until the sensor service lists the accelerometer and gyroscope, at most 120 s; the target is reached after it |

The DSP loads its sensor registry for about 35 s after it boots, and until then the sensor
service lists no IMU. `pico4-session.service` and `pico4-monado.service` start after the
target, so the driver (Monado's, or SteamVR's `driver_monado`) does not probe before the IMU
is listed; a probe before that falls back to Monado's simulated headset, whose display
SteamVR cannot find.

**DSP restarts.** An SLPI subsystem restart (level `RELATED`, `/sys/kernel/boot_slpi/ssr`)
takes the sensor service away; the name service reports it, and the driver's SSC client sees it
as `-ECONNRESET`. A second without IMU samples counts the same. The driver then reports the head
pose untracked (orientation still valid), and its IMU thread opens a new client, looks the
sensors up again, maps the QTimer again and subscribes, in short steps so that a service stop
never waits for the DSP. The station and the cameras stay as they are. The stream is back once
the DSP has loaded its registry again, about 35 s after the restart.

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
- **Pose.** While the head tracker tracks, the HMD reports position tracking and the 6DoF pose
  ([head tracking](#head-tracking)); otherwise the 3DoF orientation.

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
    retire fence. Only then can the image acquired next be off screen. Each 500 ms without the
    signal counts as a retire timeout in the statistics; after a second the display is taken as
    stuck: the present fails with `VK_ERROR_SURFACE_LOST_KHR` and the fences stay open, so the
    image is never taken for a retired one ([pico4_retire.h](../src/monado/pico4_retire.h),
    tested with fake fences). Turning the display off ends the wait for good.
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
- **GPU clock and pacing.** The GPU runs at the stock `msm-adreno-tz` governor's clocks. The
  compositor starts each frame a full refresh before its vblank: `pico4-monado.service` sets
  Monado's fake pacer to 12 ms of compositor time (`U_PACING_COMP_MIN_TIME_MS`; its default 3 ms
  plus the 4 ms present offset left 7 ms, while a two-layer frame takes 9.3–9.4 ms at the
  governor's clocks). `PICO4_KMS_GPU_MIN_MHZ` can still set a clock floor through KGSL's
  `min_pwrlevel`; the target then restores KGSL's default level (`default_pwrlevel`, power level
  6, 305 MHz) on exit, as the unit does after any stop, so a crashed compositor's floor does not
  outlive it.

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
| Projection + quad layer | + 587 MHz floor | 8.0 ms | 90 Hz |
| Projection + quad layer, 30 s | governor's clocks, 3 ms compositor time | 9.4 ms | 89.2–89.6 Hz, 13–28 late |
| Projection + quad layer, 30 s | governor's clocks, 12 ms compositor time (final) | 9.2–9.5 ms | 89.2–89.75 Hz, 9–28 late |

[`pico4-openxr-display`](../src/xr/pico4-openxr-display.c) `[SECONDS] [--projection-only] [--sky]` is the
display acceptance client. It renders through `XR_KHR_vulkan_enable2`:

- **Projection layer.** A dim grid with a large L (left eye, red) or R (right eye, blue).
- **Quad layer.** A green and white checkerboard 1.5 m ahead in LOCAL space, so it stays put while
  the head turns. `--projection-only` leaves it out.
- **Content.** Static: each swapchain image is filled once by a buffer copy, so the frame loop
  measures the runtime, not the client.
- **Sky (`--sky`).** Instead of the static pattern, a grid sky fixed in LOCAL space (lines every
  15 degrees, the horizon brighter) drawn every frame for the pose the runtime predicts, at
  1584×1584 per eye like SteamVR's applications. It shows whether lines stay single while the
  head turns ([session.md](session.md#performance-and-heat)). The lines are clear rectangles
  in a render pass, merged along each line because the driver draws each rectangle on its own.
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
build any other frame: the MCU also takes motor moves, calibration writes and firmware. The MCU
also runs the fan, and `pico4-fan` ([cooling.md](cooling.md)) sends its frames on the same port:
both hold an exclusive `flock` on `/dev/ttyHS1` for each transaction, and the IPD read gives up
after waiting a second for it. The driver passes the separation to Monado's view poses and falls
back to Monado's default if the MCU does not answer or the port stays locked.

## Controller radio ("station")

The nRF52832 on `/dev/spidev0.0` relays the controllers and generates the tracking cameras'
frame sync (FSIN). PICO's `stationservice` drives it through a vendor-modified `spidev` that
moves whole frames on the station's interrupt, so userspace only `write()`s, `select()`s and
`read()`s.

- **Frames.** 40 bytes: header (`AA` to the station, `BB` replies, `A5` notifications), target
  (`01` the station, `02` the left and `03` the right controller; a command byte means different
  things per target) or notification type, command, payload (bytes 37–38 carry a sequence
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
| `04` | 28 bytes: `03`, 8 × (u16 exposure, u8 gain) in bytes 1–12 and 16–27, 52 (u16) and an event index in 13–15 ([cameras](#tracking-cameras)) | camera exposure and gain | yes | yes |
| `04` | `07 00`, rate | camera frame rate | – | yes |
| `04` | `05 01`, then `05 02` (not `05 03`) | the camera modules' ids from their OTP, as PICO OS reads them without saved ones | yes | – |
| `01` | none (sequence number in bytes 37–38) | firmware version | – | yes |
| `07` | controller, `01` on / `00` off | controllers' IMU stream to the DSP | – | yes |
| `22` | exactly `01 00` (reads; a first byte of 0 would set the tracker work mode) | work mode | – | yes |
| `29`, `30` | none | crash info, whitelist (read-only queries of the stock wake-up) | – | yes |
| `01` to `02`/`03` | play (`01`, or all zero to stop), amplitude, u16 duration in ms | controller vibration, as PICO OS's OpenXR haptics | yes | – |
| `07` to `02`/`03` | as `01`, then u16 frequency (40–500 Hz) | controller vibration with a frequency | yes | – |
| `02` to `02`/`03` | exactly one byte, the chunk index 1–12 | the controller's calibration record (read-only, as PICO OS on every connect) | yes | yes |
| `03` to `02`/`03` | none | the controller's info (read-only; PICO OS sends it, these controllers do not answer) | – | yes |

Neither can send firmware, bootloader or flash commands, and neither touches the boot pin. The C
module's unit test checks its frames against golden bytes from the hardware-verified Python tool
(the vibration frames against the layout above) and that every other command is refused.

**Controller connections and calibration.** The station reports each controller's connection,
as PICO OS's station service reads it:

- `A5 03`, hand in byte 2 (1 left, 2 right): the controller disconnected (switched off, out of
  range).
- `A5 02`, hand in byte 2, repeated a few times: it connected. Bytes 3–8 hold the software
  version, byte 9 the status (low four bits: 1 connected, 2 connected but invalid), bytes 10–11
  the hardware, bytes 13–29 the serial and bytes 30–35 the radio address. The serial names the
  controller's gyro bias table that PICO OS keeps in persist (`pvr/<serial>.txt`). The station
  power cycle at the driver's start makes both controllers connect again.
- The calibration record comes in 12 chunks of 32 bytes (`02` above). The reply `BB`, target,
  `02` echoes the index in byte 3 (`FF`: not ready, ask again after 50 ms; `EE`: error) with the
  data in bytes 4–35. Chunks 1–3 hold the IMU calibration (gyro and accelerometer: a 3 × 3 matrix
  row-major, then the bias; 24 little-endian floats), chunks 4–11 the 20 LED positions (metres,
  in the nominal LED model's frame), chunk 12 `imu_IR` (seven floats; the record counts as
  prepared when its fourth is 1 and the first IMU float is not 0). The controllers answer every
  request, also after PICO OS's 100 ms wait, so a reader skips late replies for another chunk.

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
- **Metadata line.** The sensor sends one embedded line before the 480 scene rows, and the RDI
  writes 481 lines, as PICO OS captures them (640×481). The line carries register values as
  10-bit samples (two bits up): gain in word 4, exposure in words 6 and 7, and the exposure
  phase in word 82 (1 scene, 2 controller LEDs), read by `pico4_cam_meta_decode`. The sensor
  stays programmed for 480 lines; with a 480-line capture the metadata took row 0 and the
  bottom scene row was lost.
- **Sets of one pulse.** The sensors centre their exposures on the station's sync pulse, so a
  frame's start of frame comes half its exposure after the pulse. Frames are grouped into a set
  by the start of frame less half the exposure (the metadata line's lines of 19.07 µs), within
  4 ms. PICO OS's auto exposure sets each camera on its own: grouped by start of frame alone,
  308 against 737 lines put one camera 4.1 ms after another and no set ever formed again.
- **Stalls and recovery.** When no scene set completes for a second while scene frames arrive,
  the driver logs each camera's frames, late and repeated frames, start-of-frame offsets and
  exposure. A camera that delivers nothing while the others do has a stuck link (after a load
  spike the ISP's request manager retried one request on every frame, "bubble", and that
  camera stayed silent for good); then, and after 3 s without a set or 1 s without any frame,
  the camera thread restarts the capture as at the start: the station's sync off, the sessions
  closed and opened, the sync on, the exposure set (at most once in 5 s). The station refuses
  the first restart's sync start after a single stop and takes it after a second one. With PICO
  OS's auto exposure a stall shorter than that gets one exposure for all cameras (the median of
  theirs), so the auto exposure, which runs on complete sets, can bring it down.
  `PICO4_CAPTURE_RESTART_TEST_S=N` (test only) restarts the capture every N seconds: the frames
  return within 1-2 s.
- **PICO OS's recovery, for comparison.** Its tracking service (`watchCameraLoop`,
  `AlgorithmManager::onError`) restarts the cameras in the same way: it lets the frame in
  processing finish, closes the cameras (the station's sync with them), waits 400 ms and opens
  them again. It does so on every request-manager error message, of any type, and when a camera's
  last frame is over 500 ms old, it has fewer than 80 % of its expected frames in a 2 s window,
  or 61 late frames in that window (checked every 100 ms). One restart runs at a time, and after
  6 failed ones it reports the system as irreparable. The driver keeps its own coarser triggers
  and 5 s limit; with them it recovers within 1–2 s, and leaving a game no longer drops tracking.
- **Cached buffers.** Frames land in cached buffers, which are invalidated
  (`CAM_REQ_MGR_CACHE_OPS`) before the CPU reads them. With uncached buffers, reading frames
  stalls buffer delivery for about 100 ms every 10–13 frames (latency 45–67 ms mean). With cached
  buffers the latency is a constant 16–18 ms.
- **Kernel console.** The camera driver logs every start of frame at `KERN_INFO`. On the serial
  console each line costs about 13 ms in interrupt context, and capture collapses after about
  20 frames. `/etc/sysctl.d/50-pico4-console.conf` sets `kernel.printk = 5 4 1 7`, which keeps
  informational messages off the console, and on the headset `pico4-console-quiet.service`
  lowers the level to 3: journald's forwarded warnings and errors on the console delayed the
  display interrupt ([session.md](session.md#performance-and-heat)).
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
  camera, exposure in sensor lines of 19.3 µs and gain in 1/16 steps (16 = 1×, up to 248),
  cameras 0–3 in bytes 1–12 and a second set in 16–27; the station sends no reply. By the frames'
  metadata the first set drives the scene frames, and the second changes neither phase: the
  driver fills it with PICO OS's LED setting, 4 lines at gain 26, within PICO OS's LED limits
  (10 lines, gain 32). Bytes 13–14 carry 52, the value PICO OS's tracking service sends at a
  50 Hz camera rate (160 at other rates), and byte 15 an event index that PICO OS counts 0–9,
  one per command. Once any such command has gone out, the controllers' LEDs show about 27 times
  more saturated samples in the LED exposures than before the first one (the same 4 lines at
  gain 26 by the metadata), whatever the command holds. Exposure plus the
  9 ms readout must fit the 20 ms period, about 570 lines; 3,000 lines stall the sensors. The station's default exposure, about
  3 ms, leaves rooms nearly black.
- **Alternating exposures.** Every other frame is a short exposure in which only the
  controllers' IR LEDs show (mean 23–28 of 1023): 4 lines at gain 26 by the metadata, whatever
  the scene exposure and whatever the command's second set holds, as in PICO OS. They stay
  unsaturated while the scene exposure changes. PICO uses those frames for controller tracking; the scene
  frames run at 25 Hz.

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
  N frames. Saves a scene frame of each camera (8-bit PGM of the scene rows, and the whole buffer
  as 16-bit raw, metadata line first) and prints per camera the frames, errors, frame rate from
  the start-of-frame timestamps, their monotonicity, the sample statistics and the frames of
  each exposure phase. `--stats-log FILE` logs per frame the slot, start-of-frame time,
  brightness, latency, request, phase, exposure and gain; `--exposure LINES --gain G` sets the
  station exposure; `--lines` and `--sensor-lines` (480–482) override the capture and sensor
  heights.

## Passthrough

SteamVR shows the forward cameras as its room view (the dashboard's camera button, or the
compositor's `set_camera_roomview_enabled`). How the Frame provides one:

- **Driver.** The Frame's HMD driver (`driver_cv`, not installed here) implements OpenVR's
  camera component (`IVRCameraComponent_003`) and a private passthrough interface; Valve's
  XR service captures the passthrough cameras for it. The Frame's settings turn the camera and
  the compositor's room view on (`camera.enableCamera`, `roomView`, `enableCompositorRoomView`).
- **Not passthrough.** `steamvr-v4l2cam` copies SteamVR's view out to a V4L2 loopback device (a
  virtual webcam); it stays masked, and the stock kernel has no `v4l2loopback`.
- **Signing in.** The Steam client's VR sign-in does not use the cameras: it offers "Mobile App -
  Tap to confirm" (Bluetooth LE, which the stock kernel's Bluetooth stack does not serve), "Mobile
  App - Scan QR code" (the QR code is shown in the headset and the phone scans it) and the
  password.

On the PICO 4 the camera component is `driver_monado`'s HMD
([monado-steamvr-camera.patch](../userspace/pkgbuilds/pico4-monado/monado-steamvr-camera.patch)),
fed from the scene frames the driver already captures in `vrserver`, so the cameras and the
station keep a single owner:

- **Cameras.** The lower pair looks forward, about 40° down, 11 cm apart: cam3 (left) and cam2
  (right). Their images lie on their sides; [pico4_passthrough.c](../src/monado/pico4_passthrough.c)
  turns each upright by the quarter turn that brings its image's "down" closest to the HMD's
  (`pico4_tcam_upright`), giving 480×640 per camera, with the fisheye intrinsics (the KB4 fit of
  the factory model, OpenVR's FTheta) and camera-to-head transforms turned alike.
- **Frames.** While a client streams, the camera thread copies each complete scene set's two
  raw images (never waiting: a set is skipped while the client converts the one before); the
  component's thread converts them (black level off, the driver's brightness gain, 8 bits) into
  SteamVR's buffers as NV12 with neutral chroma, left camera above right (480×1280,
  `Stereo|VerticalLayout`), at the scene rate of 25 Hz, with the exposure time and the head pose
  at the middle of the exposure. Controller-LED frames stay out; the exposure stays with the
  tracking.
- **Properties.** `HasCamera`, `HasCameraComponent`, two cameras, NV12, the camera-to-head
  transforms, FTheta distortion with k1–k4 per camera, neutral white balance.
- **Settings.** The Frame's: `camera.enableCamera`, `roomView` 2, no camera bounds, not on the
  controller, `contentAwareRoomviewMonochromeTint`; `dashboard.allowPassthroughPortalManager` off.
- **The public component alone.** `vrserver` takes it (a client's `GetCameraFrameSize` returns its
  480×1280 frame), but in SteamVR 2.17 the video stream and the room view are not `vrserver`'s:
  `vrcompositor` starts a camera only when the shared state `VR_CameraPassthroughState` marks one
  enabled, and only the private `IVRCameraPassthroughInternal_001` in `vrclient.so` writes that
  state, as the Frame's XR service does. `vrserver` has no implementation of it, so its producer is
  a client process of its own.
- **Depth-mesh queues.** The compositor's room view also connects to two block queues,
  `/xrservice/roomview3d/meshData` and `/xrservice/roomview3d/meshLayout` (OpenVR's
  `IVRBlockQueue_005`), which the Frame's XR service provides; only code inside `vrserver` may
  create block queues. The camera component creates both, empty, when the HMD activates; the
  compositor connects ("Depth mesh block queues connection successfull") and, with no mesh
  marked up to date, renders the camera view without depth reprojection: each eye sees its own
  camera, so the room is still seen in stereo.
- **The 3D room view.** With `camera.enableCamera`, `roomView` 2 and the HMD's
  `Prop_SupportsRoomViewDepthProjection_Bool` (2079), the compositor projects the camera images
  onto a depth mesh from those queues. Each value is an `IVRPaths_001` path property on the
  queue or on a block, stored in the block's header (a 16-byte header refuses them with
  `OutOfMemory`):
  - the meshData queue: bool `/meshData/meshIsUpToDate`, which turns the mesh on;
  - a meshLayout block: u64 `/meshLayout/layoutId`, int32 `vertexCount` and `indexCount`; the
    buffer holds uint32 triangle indices, and the compositor swaps each triangle's last two;
  - a meshData block: u64 `/meshData/meshId` and `layoutId` (the layout's), int32 `elementCount`
    (three per vertex), `rawFromMeshPose` (`HmdMatrix34`) and double `posePredictionBaseTime`
    (on the frames' clock); the buffer holds the vertices as float x, y, z.
  A geometric stand-in (a sphere around the head) was worse than no mesh when worn: head
  movements were over-corrected. The mesh has to be the measured room, as on the Frame.
- **PICO OS's see-through.** On the PICO 4, `pxrseethroughservice` feeds `libphotoid.so` each
  pair of the grey forward cameras with the tracker's result at the pair's time, and the
  tracker's map points while it tracks. Its pipeline (`vst_releaseA`, the only one it loads)
  computes depth with a neural network (`FLNet_lite_v2`, SNPE on the Compute DSP) and also opens
  a CVP session. It fills gaps from the map points and makes a hemisphere mesh around the head
  that ends at the ground. The ground is the tracker's own: with ground detection on
  (`setAlgorithmParameters("plane", "1")`), `libSlam` reports the floor's height and status in
  each pose result, and `libphotoid` takes them from the left image's result. The guardian
  service draws the mesh per eye, coloured from the RGB camera, with the predicted display
  pose. (`libUndistort` serves only the undistorted RGB image to apps.)
- **The same mesh here.** `pico4-slamd` runs `libphotoid` in the tracker's root and feeds it
  the same way ([vst.c](../src/stockslam/vst.c)): cam3 and cam2 after each scene set, with
  `libSlam`'s result nearest the set's time (a ring the IMU thread fills), and the map points
  from `libSlam`'s point callback. The CVP's firmware lies in the root's `/lib/firmware` and in
  the system's `/usr/lib/firmware`: the kernel reads `cvpss.mdt` in the root of the process that
  opens `/dev/cvp`, and its workers load the segments (`cvpss.bNN`) from the system's; with the
  segments missing there, the CVP stays offline and the pipeline fails. `cdsp-boot` sets the
  CVP's restart level to RELATED, and `pico4-slamd` checks it before the start. SNPE's DSP
  module lies in `/system/lib64`, the path SNPE gives the DSP. Each mesh goes into the shared
  feed's two mesh slots ([pico4_slam_feed.h](../src/monado/pico4_slam_feed.h)). Measured with the
  headset on a desk:
  - one mesh per pair (25 Hz): 14,841 vertices and 29,400 triangles, 0.9–5 m from the head,
    with the same triangles every time;
  - ready 72 ms after the middle of the pair's exposure (the set waits 30 ms for the IMU before
    `libSlam` gets it);
  - `pico4-slamd` at 110 % of one core while it feeds the pipeline, against 60 % without.

  `libphotoid` works in `libSlam`'s world (x up, y left, z back) but takes the head and the map
  points, and returns the mesh, in that world turned a quarter turn about z, (−y, x, z), which
  has OpenXR's axes (y up). `pico4-slamd` turns both inputs: fed `libSlam`'s result unchanged, a
  head turned 39° to the right put the hemisphere 39° below the horizon. The hemisphere follows
  the head: its axis is the head's forward direction at the pair's time. Gravity lies along the
  mesh's −y, and its floor lies at y = −0.75 m (`libphotoid`'s own estimate −0.74 m,
  `libSlam`'s 0.79 m). The triangles face the head (counter-clockwise seen from inside).
- **Publishing it.** While the room view asks for frames, `driver_monado` asks the driver for
  each new mesh. The driver stamps the feed, and `pico4-slamd` feeds the pipeline only within a
  second of a stamp, so it idles otherwise. The driver returns the vertices in the head pose's
  world (in a saved room with the room's turn and shift) and its place in SteamVR's raw space
  (the tracking origin's offset). The compositor draws the mesh in raw tracking space, as PICO
  OS draws its world-frame mesh, and takes each vertex into the shown camera frame through that
  frame's camera pose (its metadata, below). The camera component
  ([monado-steamvr-roomview-mesh.patch](../userspace/pkgbuilds/pico4-monado/monado-steamvr-roomview-mesh.patch))
  publishes each new mesh once, as the Frame's XR service does: the layout block when it
  changes, the data block (`rawFromMeshPose` = the mesh's raw place, `posePredictionBaseTime` =
  the middle of its pair's exposure on `CLOCK_MONOTONIC_RAW`), then `meshIsUpToDate`. It creates
  both queues with one block size, 512-byte headers, 4 data and 2 layout blocks and
  `/meshData/blockDataSize`, and reports `Prop_SupportsRoomViewDepthProjection_Bool`.
  `IVRServerInternal` slots 41 and 42 are not head poses: the Frame sends its plane anchors
  through 41 and a jump of the tracking world through 42, and neither is needed here.
- **The Frame's room view.** Without a mesh the compositor projects the cameras onto a fixed sphere
  (the Frame's "RoomView2D"). The Frame's "RoomView3D" publishes a measured mesh: its XR service
  computes depth from the stereo cameras on the CVP, smooths it, takes the floor from horizontal
  planes in the tracking map, and writes each mesh with the pose and time of the frames it came
  from.

The room view's producer is [`pico4-roomview`](../src/xr/pico4-roomview.c), a user unit that runs
with SteamVR ([pico4-roomview.service](../userspace/rootfs/usr/lib/systemd/user/pico4-roomview.service)):

```text
cameras ─▶ driver_monado (vrserver) ─▶ /dev/shm/pico4-roomview ─▶ pico4-roomview ─▶ vrcompositor
           upright 8-bit pair, the      (pico4_roomview.h)        R8 dma-bufs and     room view
           head pose at exposure                                  per-frame metadata
```

- **Frames from the driver.** When SteamVR's camera component asks for the camera info,
  `driver_monado` creates the shared memory
  ([pico4_roomview.h](../src/monado/pico4_roomview.h)): the two cameras' size, KB4 intrinsics and
  camera-to-head transforms, and a ring of four frames. While the producer reports that the
  compositor wants frames (with a fresh heartbeat), the camera thread hands each scene set over
  and a thread of the driver converts it (as for the component) into the ring, with the head pose
  at the middle of the exposure in the space the driver reports to SteamVR (the tracking origin's
  1.6 m offset included, as on every reported pose; only the 3D room view uses the position),
  without the 29 ms of prediction and display lead its reported poses carry (with it, the room
  trailed the head's turns). Otherwise nothing is copied.
- **Client.** `VR_Init` as a background application, then `IVRCompositor_029`: the frames'
  import goes over `vrclient`'s connection to the compositor, which only asking for a compositor
  interface opens (without it the import request gets no answer). Then the passthrough
  interface, `SetGraphicsDevice` with its own Vulkan device on Turnip, and `SetupCameraFrames`
  with three linear R8 images per camera, exported as dma-bufs (DRM format modifier, linear).
  `vrclient` imports an OPAQUE_FD timeline semaphore from the compositor into that device, so the
  device enables the external semaphore extensions and the process runs with the
  `pico4-vrdisplay` layer, which emulates those semaphores on KGSL. The view state's "available"
  flag goes on with the frames.
- **Each frame.** While the compositor asks for frames: the newest frame from the ring into a free
  image per camera (the pixel shader squares each sample, so the linear luma goes out as its
  square root), then the frame's metadata and `PublishFrame`. The metadata the room view's shaders
  read: the exposure time on the compositor's clock (`CLOCK_MONOTONIC_RAW`; a time after its now
  shows nothing), fx, fy, cx, cy in pixels, k1–k4, the camera's pose in raw tracking space at
  the exposure (+0x30: the head at the exposure × the camera in the head frame), the camera in
  the head frame (+0x60, from which the compositor picks per eye the camera nearest to it), a
  head-frame calibration (+0x90, identity), and the image's valid rectangle (0, 0, 1, 1; the
  pixel shader clamps its coordinate to it, so zero samples one texel). The camera poses are in
  the computer-vision axes (x right, y down, looking along +z; the vertex shader clamps the depth
  to 0.001, so OpenVR's camera axes send every point off the image). The compositor's
  reprojection (`vrcompositor`, emulated) is inverse(+0x30) × the point in raw space, with the
  render pose carried through inverse(eye-to-head) × +0x90 × inverse(render head): with the head
  at +0x90 instead, head motion turned about the tracking origin, 1.6 m from the head, and
  nodding warped the view while the head moved. Each camera's image stays its own.
- **Restarts.** A new compositor (its process changed) gets the frames set up again; without
  `vrserver` the producer exits, and the unit starts again with SteamVR.
- **Reprojection without a depth mesh.** Measured in frame dumps with the headset lying still, with
  the published exposure pose changed on purpose:
  - Turned by ±5°, the room view moves by 4.7 dump pixels per degree in pitch and 4.6 in yaw, in
    the directions the turns give. The compositor compensates head rotation between exposure and
    display equally in both axes.
  - Moved by ±10 cm, it moves by 2 dump pixels (about 0.4°). Without a depth mesh the compositor
    projects the image as if the room were 10–25 m away, so a nearby room does not follow head
    translation.
  - Nodding pivots about the neck, well below the eyes, and moves the cameras more than turning
    does. Up-down movement therefore shows the room trailing the head, and left-right far less.
  - With the depth mesh (RGB camera, camera pose in raw space), the same 10 cm move shifts the
    room by 10–17 dump pixels (2.1–3.6°): it follows at 1.6–2.7 m, the depth of the room
    in view, instead of 10–25 m.
- **Timing.** Worn, with turns up to 137°/s: 25 frames per second, 40 ms apart, every one with a
  head pose; a frame reaches the ring 27–45 ms after the middle of its exposure (16–63 ms at the
  extremes), so it is shown about 40–80 ms after it. Its rotation is as timely as the virtual
  world's: against the vblank that starts scanout, the image's shift rate follows the head's yaw
  rate 32 ms ahead in the room view and 28 ms ahead in the virtual space (display layer motion
  log with linear scanout, worn turns up to 170°/s). Worn with the RGB camera on the depth mesh
  and the camera pose in raw space, turning and nodding show the room fixed in place, without
  warp or visible lag. With colour and the mesh on for 60 s, the still headset's stock tracker
  lost no pose and stayed within 0.5 mm and 0.10° of its start (0.4 mm and 0.04° with the room
  view off); `pico4-slamd` then takes 104 % of one core against 54 %.
- **Colour from the RGB camera** (the default; `PICO4_ROOMVIEW_RGB=0` keeps the grey pair).
  PICO OS colours its see-through from the IMX471 RGB camera, every frame with its own capture
  pose, onto the depth mesh; the grey pair only builds the mesh. Here:
  - The IMX471 streams in the tracking cameras' capture as sensor slot 4
    ([pico4_cam.c](../src/monado/pico4_cam.c)), as PICO OS's module describes it: I2C address 0x34,
    ID 0x0471 at 0x0016, its power sequence, 2328×1748 RAW10 (RGGB, the 2×2-binned array)
    free-running at 50 fps on four lanes of CSIPHY 1 at 2064.8 Mbit/s (settle count 17), into an
    RDI of the free IFE-lite. The request manager has one owner, so it cannot run in a process of
    its own. 49.9 fps measured. Exposure goes to the sensor while it streams as a CONFIG list
    under group hold; the kernel then leaves the sensor in its CONFIG state, where
    `CAM_STOP_DEV` is refused, so its stream-off goes the same way.
  - The driver converts each frame on a thread of its own, pinned to the Cortex-A77 cores 4–6
    (9 ms a frame there, 22 ms on the little cores): each 2×2 cell one pixel (1164×874), black
    level 64 off, grey-world white balance, sRGB, full-range BT.601 NV12. The camera thread hands
    the frame over and gets it back after the conversion. An auto exposure aims at 18 % grey: a
    flicker-free 1/100 s first, then gain up to 16×, then up to 20 ms. 47 of the 50 frames a
    second reach the ring, 30.5 ms (median) after the middle of their exposure at the longest
    exposure; the conversion takes 44 % of a core.
  - The sensor streams only while the room view shows it, as PICO OS opens the camera only for
    its see-through: 5 s after the room view closes the driver stops the IMX471's stream
    (0x0100 = 0 as a CONFIG list) and starts it again when the room view opens. The ISP keeps
    its requests queued and the link stays up; the request manager's 5 s start-of-frame
    watchdog then fires on that link and is ignored. Opened again, 42–47 frames a second reach
    the ring at once, 28–31 ms (median) after the middle of their exposure.
  - The lens is PICO's omni model with its "fisheye62" distortion: the unified-model step with ξ,
    then θ = atan(|m|) of the omni-normalised point m, θd = θ(1 + k1θ² + … + k6θ¹²), the
    tangential terms on m, and the focal lengths. [pico4_tcam.c](../src/monado/pico4_tcam.c)
    projects it and fits KB4 for the room view's metadata.
  - `pico4-roomview` sets the frames up as NV12 (one dma-buf for both planes, linear) with both
    camera slots backed by the same images, and publishes each frame to both: the compositor's
    only colour input. A colour-bar test pattern (`pico4-roomview --test-pattern`) shows in the
    right colours.
  - Shared memory version 2 carries the format: two grey cameras or one NV12 image.

## Head tracking

The head is tracked by PICO OS's own tracker, `libSlam.so`, as on PICO OS
(`PICO4_SLAM_BACKEND=stock`, the default). It is a multi-camera visual-inertial tracker whose
front end (image pyramids, map reprojection, corner detection, the depth filter) runs on the
Compute DSP (CDSP); in Valve's idle session `pico4-slamd` takes about 70 % of one core and
SteamVR's `vrserver` about 120 %, where `vrserver` with Basalt took 350–390 %. Monado's Basalt
is deprecated: it remains selectable (`PICO4_SLAM_BACKEND=basalt`, [below](#basalt)) as it is,
without further work.
Without the tracker's data in the image, or without the station, the HMD stays on 3DoF.

```text
cameras, head IMU ─▶ pico4_slam.c (driver_monado or monado-service)
                        │ raw 10-bit scene sets, IMU samples        ▲ poses (IMU rate)
                        ▼                                           │
                     /dev/shm/pico4-slam (pico4_slam_feed.h)        │
                        │                                           │
                     pico4-slamd ── image processing ── libSlam.so ─┘
                        (bionic root /usr/lib/pico4-stock-slam)  ── FastRPC ──▶ CDSP
```

### PICO OS's tracker

- **Its root.** `libSlam.so` is an Android (bionic) library. It runs in a root of its own,
  `/usr/lib/pico4-stock-slam`, with PICO OS's bootstrap linker and bionic C library, which need no
  Android runtime: the tracker, the eleven libraries it needs (among them `libion.so`, without
  which its DSP optical flow cannot allocate memory and 6DoF never starts), its DSP modules,
  configuration (`/system/etc/pvr/config`), the DSP's process shells and its root listener
  `cdsprpcd`. [scripts/extract-stock-slam.sh](../scripts/extract-stock-slam.sh) takes them from
  the stock firmware ([development.md](development.md#stock-firmware)), `build-userspace.sh
  --stock-slam` installs them. Two stand-ins of this
  project replace Android pieces: a `libandroid.so` with the two trace calls the tracker uses, and
  `libpico4-slam-stubs.so`, preloaded, which sends Android's log calls to the journal and answers
  property lookups with nothing (the tracker's defaults). Programs in the root are built with the
  host's clang for `aarch64-linux-android29` against the root's own libraries, without an NDK
  ([src/stockslam](../src/stockslam)).
- **Its runner.** [`stock-slam`](../userspace/rootfs/usr/libexec/pico4-poc/stock-slam) starts a
  program of the root in a private mount namespace, chrooted, with `/dev`, `/proc` and `/sys`
  bound in, the headset's factory calibration (persist's `pvr/`) at `/mnt/vendor/persist/pvr` and `/mnt/vendor/pvr`
  (where the tracker reads it and its gyro bias table) and `/data` and `/tmp` as tmpfs.
- **The CDSP.** [`pico4-cdsp.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-cdsp.service)
  boots it as the SLPI: restart level RELATED first (the kernel's SYSTEM would reboot the headset
  on a DSP crash), then `/sys/kernel/boot_cdsp/boot`; its firmware (`cdsp.mdt`, `cdspr.jsn`) comes
  from the modem partition. `pico4-cdsprpcd.service` runs `cdsprpcd` in the root: without it the
  DSP's loader stalls on the tracker's first module. The tracker then opens a signed user PD on
  the CDSP (`fastrpc_shell_3`) and loads its modules there itself, through the stock
  `libcdsprpc.so` on this kernel's own FastRPC driver.
- **pico4-slamd.** [`pico4-slamd`](../src/stockslam/pico4-slamd.c)
  ([`pico4-slamd.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-slamd.service),
  wanted by Valve's session and Monado) creates `/dev/shm/pico4-slam` (group video, mode 0660)
  and feeds the tracker as PICO OS's tracking service does: `createHandle("")`,
  `headSensorModel` `icm4x6xx`, `startAlgorithm`, the result callback, `start6DOF` `6`; from one
  thread every IMU sample as {gyro, accel, temperature} with its time and the pose after it, from
  another each frame set once the IMU is 30 ms past it (on PICO OS the camera's latency does that;
  earlier, the tracker waited for the IMU in vain on nearly every frame). `updateImage` only
  queues a set (up to 2.5 ms); the tracker works on it inside a later `update6AxisIMU`, which
  then takes up to 11 ms, so poses can be up to about 18 ms apart. Each start of the driver bumps a generation in the
  shared memory; `pico4-slamd` then exits and comes back with a new tracker.
- **Images.** PICO OS's processing of each scene frame, reimplemented
  ([isp.h](../src/stockslam/isp.h)): the first scene row doubled (it replaces the sensor's metadata
  row; the last row drops out), black level 16 off, `v + (v >> 8)`, a per-pixel lens-shading gain
  (128 = 1×, up to 16× in the fisheyes' corners) and a 10-to-8-bit gamma table. The gain table is
  chosen by the camera modules' (vendor) ids, which PICO OS's station service keeps in
  `/data/stationservice/camera_otp.json` and, when it has no valid ones (all four 8 or 9),
  reads from the modules' OTP through the station: camera op 05 sub-op 01, then 02, each
  answered by `BB 01 04 05` with the four ids. The same here, as on a fresh install: the file
  lives on the data partition (`/var/lib/pico4-stock-slam/stationservice`, bound into the root);
  `pico4-slamd` hands the saved ids to the driver, which, the station's owner, reads the OTP
  before it starts a tracker when they are not valid (the two requests are in the station
  allowlist), and `pico4-slamd` saves what PICO OS would keep. It maps camera 0's id as PICO
  OS's ISP does: 8 → `config_lscSY.txt`, 9 and 13 → `config_lscLH.txt`, 7 →
  `config_lscneo3.txt`, any other (or none) → no lens shading. `PICO4_SLAMD_LSC` names a table
  by hand.
  No rotation: the tracker's calibration describes the sensors as read out.
- **Auto exposure.** PICO OS's own, `ExposurePredict` from `libExposureImageProcess.so` (with
  `libtianti_shm_dynamic.so` and `libc++_shared.so`, extracted into the root with the tracker;
  this project's `libandroid.so` adds the six hardware-buffer calls that library binds, which fail:
  they are used only between processes). `pico4-slamd` calls it as PICO OS's `calcExpo` thread
  does, on a thread of its own: on each scene set it is not still busy with, the four images after
  black level and lens shading and before the gamma table (16-bit, `isp.h`'s intermediate), the
  lines and gains the set was taken with, 50 fps, bit width 16, and flicker detection started on
  the first call. Its result (lines 13–737, gain 16–240 in steps of 16; cameras 2 and 3 share
  one setting) goes to the driver through the shared memory unless it is what the set was taken
  with or what was sent last; the driver sends it per camera as the scene phase of
  `CameraSetPara` (the LED phase unchanged) and runs no auto exposure of its own while
  `pico4-slamd` serves its generation with a fresh heartbeat. With per-camera exposures a set's
  frames differ in exposure; their frame counters still decide whether they form a set.
  `PICO4_SLAMD_AE=0` leaves the exposure to the driver's median loop ([Basalt](#basalt)).
- **Recovery.** As PICO OS's tracking service: when the tracker's confidence falls from 1 to 0
  and stays 0 for more than 10 s (`PICO4_SLAMD_RESTART_S`, 0 turns it off), `pico4-slamd` stops
  it and starts it again in the process (pre-stop, stop with the map saved, destroy, then the
  start sequence), with the frame thread paused around it; libSlam otherwise can stay in its
  relocalisation for good after a loss (error mask 16, confidence 0). The restarted tracker has
  a new world and relocalises in the room as at a start, also when that takes longer than the
  first 10 s (below).
- **IMU.** The raw samples of the factory body frame, at the DSP's 1 kHz, as PICO OS feeds them
  (its uncalibrated streams carry the same values; the service applies no bias or axis change),
  each with the ICM-42688's latest die temperature, which the tracker's gyro bias table is
  binned by. On PICO OS the sensor DSP inserts it into each gyro sample; here the driver streams
  the sensor's own `sensor_temperature` at 5 Hz (its fastest rate) beside it. Without a
  temperature, `pico4-slamd` passes 30 °C, the table's middle.
- **Poses.** The tracker's world and body frames have the factory body frame's axes (x up, y
  left, z back). PICO OS's client maps both with the HMD axes; the driver does the same (the
  inverse of `Q_B_H` on each side), adds the HMD origin and predicts along the pose's velocities to
  the requested time plus the display lead, at most 45 ms past the newest pose. PICO OS's
  `getPredictResult` extrapolates the same way (body rates on the right of the orientation,
  velocity and acceleration for the position) within 45 ms of a reference pose; its client asks
  for the pose's own time and predicts for the display elsewhere. A time already past (the
  controller tracker's LED exposures) is interpolated between the two poses around it in the
  shared memory. The tracker fuses the IMU itself, so the HMD takes its poses directly, without
  [pico4_fusion.c](../src/monado/pico4_fusion.c). A pose older than 200 ms, or one the tracker
  reports untracked, counts as lost: the HMD keeps the last position with the 3DoF orientation.
  The tracker relocalises in its own map.

### Room setup

PICO OS keeps a room across sessions with the tracker's map: `libSlam.so` loads the maps in
`/data/misc/user/0/maps` at `createHandle`, tries to relocalise in them for about 8 s after
`start6DOF`, and saves its map at the stop (`preStopAlgorithm`, `stopAlgorithm` with
`non_sleep`, `destroyHandle`), but only while `boundary/stdata.txt` holds a boundary;
`createHandle` and `start6DOF` empty the boundary files. After a relocalisation it writes the
saved room's boundary, in this session's world, to `boundary/stdataforalgorithm.txt`, and calls
the callback of `RelocResultCallbackRegister` once per tracker with the relocalisation's time
and pose, whenever the relocalisation happens; PICO OS's service learns of it that way. A boundary
file holds `vertexcount` and the points as `(a, b, c)` with b up; the tracker's world point is
(b, −a, c): `libSlam`'s writer prints a world point (x, y, z) as (−y, x, z), and its readers
store (a, b, c) as (b, −a, c). The map is saved in the session's world: the tracker restarted with
the headset untouched relocalises with the same transform (−100.45° and −100.41°, shifts within
2 mm).

- **Where it lives.** [`stock-slam`](../userspace/rootfs/usr/libexec/pico4-poc/stock-slam)
  binds `/var/lib/pico4-stock-slam/{maps,boundary}` (the data partition, mode 0700) onto those
  directories of the root, so the map survives reboots and system updates; it never leaves the
  headset.
- **A reference square.** Every map is saved with the same boundary in its own world: a 2 m
  square 1.5 m below where the tracker started. When the relocalisation callback comes and the
  copy is written, `pico4-slamd` fits the
  tracker's copy of it in this session's world ([room.h](../src/stockslam/room.h): a turn about
  the up axis and a shift, both worlds being gravity-aligned; accepted below 5 cm residual): that
  is the transform from the session's world to the saved room's, whichever saved map the tracker
  found. It publishes it in the shared memory and writes the copy to `stdata.txt`, so the stop
  saves the map again; the driver puts every pose of that generation into the room's world.
  Without a relocalisation 10 s after `start6DOF` the session is a new room, saved with the
  square as it is; a later relocalisation (a covered start, a restarted tracker) moves it into
  the saved room's world and universe then.
- **SteamVR's universe.** The map's identity (`getRelocMapInfo`) is the room's: `driver_monado`
  reports its hash as `Prop_CurrentUniverseId_Uint64` once the room is settled, so SteamVR keeps
  a play area per room in `chaperone_info.vrchap`, back in its place whenever the tracker
  relocalises in that room, and asks for a room setup in a new one. Until then the universe is 2.
- **Room setup in the headset.** SteamVR's dashboard shows its Space Setup menu (recenter,
  playspace setup, floor height, clear playspace) only for an HMD that reports
  `Prop_SupportsInHMDRoomSetup_Bool` (11003). Its steps past the recenter also need
  `Prop_VRLinkClientHMDSupportsRoomSetupRequests_Bool` (11004): without it the dashboard drops
  them ("HMD doesnt support setRoomSetupStep") and the buttons do nothing. Away from a VR Link
  server the steps run in SteamVR itself (`VRChaperoneSetup`), which keeps the result per
  universe in `chaperone_info.vrchap`. `driver_monado` reports both properties; the bundled
  `openvr_driver.h` predates their names. The playspace setup and the floor height adjustment
  run in the headset. Developer › Quick Calibration
  (floor and standing position) works either way. Clear Playspace is offered only by SteamVR's
  main-branch builds (`m_bSteamVRMain`), not by this release.
- **The detected floor.** The Frame's room setup takes the floor from the tracker. Its XR
  service detects the floor plane and sends its height in raw space to the HMD driver. While
  the dashboard's room-setup step (event 809, broadcast to drivers) is 3 (the boundary circle),
  6 (the silent detection at start, behind `steamvr/floorDetectAtStart`, off on the Frame) or 7
  (the playspace setup, also the required setup of a new universe), the driver hands the height
  to `vrserver`. It passes it through the room setup's transform (`/chaperone/room_setup/transform`,
  y′ = m₁₁·y + m₁₃) to the unversioned `IVRServerInternal_XXX`, slot 40. `vrserver` posts it to
  the compositor (`floor_detection_result`), whose floor target snaps to it; accepting the step
  commits it. NaN clears it when the step ends. The floor height adjustment (step 2) is manual
  on the Frame too. Here `libSlam`'s ground detection gives the floor: `pico4-slamd` turns it on
  (`plane` 1) and passes each result's ground status and height with the pose. The floor lies
  that far below the tracker world's origin, measured at 0.79 m below a headset on a desk;
  status 2 is an initial estimate (1.13 m there) and is not used. `driver_monado`
  ([monado-steamvr-floor.patch](../userspace/pkgbuilds/pico4-monado/monado-steamvr-floor.patch))
  does what the Frame's driver does, with that floor in its raw space (room and tracking origin
  applied). Slot 40 belongs to this `vrserver` build and has to be checked again with every new
  SteamVR.
- **Recenter.** Holding the PICO button (the dashboard's quick recenter) makes SteamVR reset its
  standing zero pose (`VREvent_StandingZeroPoseReset`, manual): standing space, and the play area
  kept in it, turn to where the head faces, and `chaperone_info.vrchap` keeps the pose of the
  last room setup, so after a restart the recenter is gone. The Frame keeps the chaperone
  universe (bounds and `standingAppWorldFromRawAppWorld`) in its XR service's persistent world
  with the map instead; that is not followed yet.
- **Verified.** A drawn play area comes back after a reboot exactly where it was drawn (the
  tracker relocalises in the saved room, and SteamVR finds the room's universe in
  `chaperone_info.vrchap`), and its boundary appears when walking toward its edge. With no play
  area for the current universe, SteamVR asks for a space setup when a game is launched.

### Frame times

The tracker, Basalt and the controller tracker take each frame's time from the camera hardware's
start-of-frame stamp, the request manager's `V4L_EVENT_CAM_REQ_MGR_SOF` (QTimer in ns), onto
`CLOCK_MONOTONIC` with the same offset as the IMU's samples, as PICO OS does. The boot-time stamp
(`V4L_EVENT_CAM_REQ_MGR_SOF_BOOT_TS`) is that stamp plus an offset the kernel takes once, at the
first frame after boot, late by however long that interrupt waited: on the headset it was 395 ms
off (constant within 0.2 ms). It is only the fallback. The time of a frame is the middle of its
exposure: the start of frame minus 52 µs, seven lines (133.5 µs) and half the exposure in lines
of 19.07 µs, from the frame's own metadata line, as PICO OS computes it.

The QTimer's offset to `CLOCK_MONOTONIC` is taken again every second, on the IMU thread and on the
camera thread. `systemd-timesyncd` steers `CLOCK_MONOTONIC` against the QTimer (16.6 ppm on the
headset, 1 ms a minute): an offset taken once at the driver's start is 0.3 s off after five
hours. Poses then look older than the 200 ms after which the HMD counts them lost, and the view
falls back to the IMU orientation extrapolated across the gap. Worn, every motion then overshot
and sprang back.

### Basalt

Deprecated: kept selectable as it is, without further work.
[pico4_slam.c](../src/monado/pico4_slam.c) feeds Monado's SLAM tracker with
`PICO4_SLAM_BACKEND=basalt`. Monado loads Basalt through its VIT interface (`libbasalt.so` from
the [`pico4-basalt`](../userspace/pkgbuilds/pico4-basalt/PKGBUILD) package, Monado's Basalt fork
pinned by commit).

- **Frames.** Frames of one sync pulse are grouped by the pulse (start of frame less half the
  exposure, within 4 ms, [above](#tracking-cameras)), and the
  controller-LED frames are skipped by the phase in their metadata line; a frame whose line does
  not decode is skipped too. A complete set goes on only as one exposure: the same exposure
  lines and analog gain in all four metadata lines, and each camera's ISP frame counter at its
  usual offset from the first camera's (the sensors share the sync pulse, so the offsets stay
  fixed; a new offset that holds for two sets is taken over). Otherwise the set is dropped and
  logged; the periodic report counts incomplete and inconsistent sets. Fed the LED frames as well, Basalt drifts by 430–720 m in 30 s.
  Basalt gets the 480 scene rows only. Each frame is converted
  to 8 bits: black level (16 of 1023) removed, slowly adapting digital gain (1–8×, aiming at a
  mean of 70). Sets go out in camera order with one timestamp, as Basalt requires; a set that
  misses a camera is dropped.
- **Frame time.** Each frame's time is the middle of its exposure ([Frame times](#frame-times)),
  from the exposure that frame's metadata line reports (not the newest one commanded, which may
  not have reached the sensor yet). The start-of-frame interrupt marks the readout after the
  exposure, up to 4 ms later and changing with the exposure, which would put the images out of
  step with the IMU while the head moves. The set gets the mean of its cameras' times; the
  driver logs their spread (0.00–0.01 ms). `PICO4_SLAM_EXPOSURE_CENTRE=0` uses the start of
  frame.
- **Auto exposure.** A slow loop sets the station exposure from the median brightness of the
  set's cameras and the exposure the set was taken with, every 25 sets (half a second). With the
  first camera alone, a headset lying with that camera against fabric drove the exposure to
  400 lines and gain 223 until the other three saturated and Basalt crept; the median ignores
  one covered or saturated camera. The digital gain follows the same median. It starts at 300 lines and 1× gain and aims at a mean signal of
  250 above black (10-bit), changing at most 4× per step and ignoring changes within 10%. It
  raises exposure first, up to 400 lines (longer exposures smear more in motion), then gain up
  to 15.5×, the sensors' highest analog gain. Capped at 4×, a moderately lit room stayed near
  black (8-bit mean 5–10) and Basalt lost track; gain adds noise, but no motion blur.
- **Upright images.** The lower pair is mounted 180° apart, as the calibration's relative roll
  says. Basalt's stereo matching tracks patches between cameras and cannot follow that, so the
  driver turns each image that is upside down relative to the first camera's. It rotates that
  camera's calibration to match (`pico4_tcam_rotate_180`).
- **IMU.** Raw head IMU samples in the factory body frame, which is the frame of the camera
  extrinsics; every second sample goes to Basalt (`PICO4_SLAM_IMU_DIVISOR`). The calibration
  given to Basalt declares the rate the DSP delivers, 1024 Hz for the 1 kHz request (512 Hz for
  500 Hz), divided by that divisor, and ICM-42688-class noise (`PICO4_SLAM_IMU_NOISE`,
  continuous-time densities: accelerometer 0.016, gyro 0.0016; bias walk 0.001 and 0.0001);
  Basalt estimates the biases. A 28-minute static capture of the head IMU (Allan deviation)
  measures white noise of 0.0007–0.0023 m/s²/√Hz for the accelerometer and
  0.00004–0.00032 rad/s/√Hz for the gyro, depending on the axis, with bias instabilities at
  8–42 s; the configured densities are about five times the worst axis, the usual margin for
  vibration and calibration error. PICO OS's own head tracker uses 0.03, 0.005, 1e-5 and 1e-6; given
  to Basalt they made it jiggle and drift more (still and worn A/B, below).
  The driver measures the rate over 10 s and logs it once, and again as a warning when it
  strays more than 5% from the declared one.
- **VIO settings.** Basalt runs on its built-in defaults except where
  [pico4-vio.json](../userspace/pkgbuilds/pico4-basalt/pico4-vio.json) changes them
  (`PICO4_SLAM_VIO_CONFIG`; pico4-basalt's `basalt-vio-config.patch` lets the tracker read the
  file while the driver supplies the calibration): `optical_flow_image_safe_radius` is 300 px,
  so no points are tracked in the fisheyes' dark corners (the image falls to the black level
  beyond about 330 px from the centre). Basalt logs "Using VIO config" when it loads the file.
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
  - **Basalt's own queues.** Basalt queues up to 10 frame sets before its optical flow and 10
    before its estimator. When it fell behind, the poses trailed the head by 641 ms.
    `basalt-realtime-input.patch` keeps one set waiting for the optical flow and two for the
    estimator: a new set replaces the oldest waiting one, and IMU pushes never block (a full IMU
    queue drops its oldest sample). Basalt logs the replaced sets. Its deterministic mode, for
    recorded datasets, keeps the original blocking queues. A pose now leaves Basalt 60–110 ms
    after its images were taken.
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
  world. `monado-slam-prediction.patch` keeps Basalt's own velocity estimate with each pose
  (Monado took the difference of two poses 40 ms apart) and hands the driver the newest pose
  with the time its images were taken (`t_slam_get_latest_pose`).
- **Fusion.** Basalt's poses arrive at 25 Hz and about 100 ms after their images.
  [pico4_fusion.c](../src/monado/pico4_fusion.c) keeps the head pose at the IMU's rate and takes
  them as corrections:
  - orientation: the 3DoF fusion's, turned into the filter's world by an offset that turns
    towards Basalt's at every IMU step with a time constant of 0.5 s (mostly the heading);
    following each Basalt pose at once made the view step 25 times a second;
  - position and velocity of the IMU: integrated from the accelerometer, gravity removed;
  - a Basalt pose is compared with the filter's state at the time of its images (a history of
    one second); that state, and every later one, moves towards Basalt's position and velocity
    with a time constant of 150 ms (`PICO4_FUSION_POSITION_MS`);
  - the output does not jump with a correction: the correction fades in, critically damped over
    50 ms (`PICO4_FUSION_SMOOTHING_MS`), so neither position nor velocity steps;
  - without a Basalt pose for 500 ms the position comes to rest and is held, untracked, and the
    orientation keeps its offset; the next pose is taken in full, and the output slides there.

  The HMD hands out the pose 24 ms later than the requested time (`PICO4_PREDICT_MS`), plus
  the display layer's vsync lead (5 ms in Valve's session), which the layer publishes in
  `$XDG_RUNTIME_DIR/pico4-vrdisplay-lead-us` ([session.md](session.md)), predicted from the
  newest state with its velocities.
- **Prediction.** SteamVR asks `driver_monado` for the present pose and extrapolates it to the
  panels itself, by only about 9 ms. A latency probe measured the rest of the pipeline: the
  driver turns the pose it hands out by 5° at known times (`PICO4_LATENCY_PROBE_MS`), and the
  display layer finds each step in the committed images and the vblank that shows it
  (`PICO4_VRDISPLAY_MOTION`); a reported 30°/s turn without turning shows how far SteamVR
  extrapolates. A pose reaches the panels 33 ms after it is handed out (32.6–33.5 ms for most
  steps), so about 24 ms went unpredicted: at 100°/s the image trailed the head by 2.4°, seen as
  lag on turns and as the world swimming with the head's small motions. The driver predicts
  those 24 ms from the fusion's IMU-rate state; in a blind worn A/B the user found it "almost
  flawless" against "horrible" without (Basalt also ran away three times in the latter).
- **Anchor.** Basalt's world is mapped onto the filter's by a heading and an offset, at first
  the identity. A pose more than 15 cm or 10° off is skipped as an outlier; when five in a row
  are, Basalt has jumped and stays there, and the anchor moves instead of the view.
- **Runaway.** Basalt can run away: in worn sessions its position ended 13.6 m and 1,750 m from
  the start, at up to 5 m/s, while the user stood in a room. A pose is implausible when
  - its velocity changed over the last 0.5 s by more than 0.5 m/s beyond what the accelerometer
    measured over the same time (a head cannot accelerate without the IMU feeling it),
  - it moves faster than 0.15 m/s while the IMU has felt neither turning nor acceleration for
    0.5 s (a steady creep), or
  - it moves faster than 4 m/s.

  Implausible poses are skipped; three in a row make a runaway. The filter then holds the
  position (untracked, orientation from the 3DoF fusion), and the driver resets Basalt
  (`t_slam_reset`, again every 5 s while it stays lost); Basalt starts a new map at its own
  origin and heading. Poses of images taken before the reset are skipped. After five consistent
  poses spanning the 0.5 s check, the anchor maps the new map onto the held position and
  tracking continues from there.
- **Without the fusion** (`PICO4_FUSION=0`), Monado's tracker predicts from Basalt's newest pose
  with the gyro for the orientation and Basalt's velocity plus the mean world acceleration since
  that pose for the position (`SLAM_PREDICTION_TYPE` 3). The patch adds a soft deadband on that
  acceleration (`SLAM_ACCEL_DEADBAND`) and reports poses more than 500 ms older than the
  requested time as untracked. Without a tracked pose the HMD takes the 3DoF orientation, turned
  by the difference to Basalt's at the last tracked pose, and holds the last position. Monado's
  default dead reckoning integrates the accelerometer without bound; its positions strayed
  50–270 mm from Basalt's.

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

- **IMU** (type 0): gyro (3 × int16) at 8.192 LSB per °/s (±4000°/s), accelerometer (3 × int16)
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
and aim poses. IMU samples are timed by the SSC event time minus the record's offset; the SSC
event time equals the packet's station time to within its microsecond truncation, so this is PICO
OS's timing. As in PICO OS's controller service, samples stamped at or after their arrival are
dropped; the controller tracker starts over on a backward step or a gap above 35 ms, as stock,
while the 3DoF orientation below skips duplicates and backward steps. When a controller's newest
sample is 0.2 s old (switched off, out of range), its 3DoF orientation holds, untracked, instead
of extrapolating the last turn. While the
controller tracker (below) has placed a controller, its pose comes from there: position tracked
while the tracker's output is 6DoF, and valid but not tracked while it holds the position (lying
still, or briefly unseen), which PICO OS publishes the same way. Before the first fix the
orientation comes from `m_imu_3dof`, levelled from the mean gravity of the first 100 samples,
with the IMU axes mapped to the aim frame (x right, y up, z back) as `x,-z,y`,
and the position is a placeholder: 0.18 m to the side, 0.35 m below and 0.30 m in front of the
head, turned with its heading. The controllers share the HMD's tracking origin, so Monado's
builders give head and controllers one offset. A vibration request (amplitude, duration) goes
out as one motor frame (command `01`, below) per request, mapped as PICO OS maps OpenXR haptics:
level = amplitude × 255, the duration in whole milliseconds and at least 10 ms, Monado's minimum
duration as 500 ms, 0 ms as a stop, and PICO OS's default strength of 75 %. The controller ends
the vibration itself. SteamVR's pulses without a duration (its hover feedback, 0 s at amplitude
0.1–0.2) are single short pulses: the SteamVR driver sends them as 10 ms, the motor's shortest,
not as Monado's minimum duration, which would buzz for half a second at every hover.

Either pose is the aim pose: PICO OS publishes the tracked pose as the aim and derives the grip as
aim × T, where T is a position and a quaternion (w, x, y, z) per hand, left block first, that its
tracking service takes from `sys_tracking_aim_to_grip_transform` or, without that key, from
built-in values (a turn of about 34° about x and about 5 cm of lever arm on this firmware).
[`tooling/aimgrip.py`](../tooling/aimgrip.py) reads the built-in values from the 5.13.7
`libpvrtrackingservice.so`, `extract-sensor-data.sh` writes them to
`calibration/controllers/aim-to-grip.json`, and the driver composes the grip pose the same way,
adding ω × lever arm to its linear velocity; without the file the grip pose is the aim pose. The
SteamVR driver reports the aim pose as the controller's pose, the frame of the render models.

The render models are PICO OS's own: `/system/pre_resource/data/misc/user/controller/Neo3Phx/`
holds a textured OBJ per hand (`60.obj` left, `61.obj` right, in centimetres) whose origin and
axes are the published aim pose: with no transform, PICO OS's LED rings, turned into the aim
frame and less the lever arm, lie within 1.5 mm of the surface.
[`tooling/controllermodel.py`](../tooling/controllermodel.py) converts them into SteamVR render
models (triangulated OBJ in metres, material, texture, and components for the aim and, from
`aim-to-grip.json`, the grip), `extract-sensor-data.sh` writes them to `rendermodels/`, and the
image build copies them to `/usr/share/steamvr-monado/resources/rendermodels/`, where SteamVR
finds `{monado}pico4_controller_left` and `_right`.

### Controller tracking

Each controller carries a ring of 20 infrared LEDs, which light in the station's short controller
exposure, every other camera frame (4 lines at gain 26). PICO OS's controller tracking library
(`/system/lib64/libpico_controller_tracking.pxr.so`) holds a nominal model of each ring: 20
points in metres, between 1.5 and 9.8 cm from the model's origin, the left ring the right one
mirrored in y, and one unit normal per LED. [`tooling/ledmodel.py`](../tooling/ledmodel.py) reads
both by their addresses in the 5.13.7 library and refuses anything that is not such a ring;
`extract-sensor-data.sh` writes them to `calibration/controllers/led-models.json`, installed with
the rest of the calibration. They are vendor data and stay out of the repository.

On every controller connect, as PICO OS does, the driver's station thread reads that
controller's calibration record from the station ([Controller radio](#controller-radio-station))
and hands it to the tracker with the serial from the connect notification:

- the IMU calibration (gyro and accelerometer matrices and biases, applied to the raw samples);
- the controller's own 20 LED positions, which replace the nominal ones (normals and the
  identification table stay);
- its temperature-binned gyro bias table, `calibration/pvr/<serial>.txt` from persist, read
  only (PICO OS also writes it back).

Left and right are read separately. The serial is not logged.

The tracker (`src/monado/pico4_ctrack_*`, C++17 with Eigen) reimplements the approach of that
library:

- **Frames.** A world with +x up, the headset body frame of the factory calibration, and the
  LED-model frame, which is also the controller IMU's frame after the fixed axis remap
  (x, y, z) → (−z, −x, y). There is no IMU-to-LED transform and no IMU-to-camera delay.
- **Blobs.** A SimpleBlobDetector-like detector: a fixed or adaptive (11 × 11 mean + 30)
  threshold, outer contours, polygon moments, area and circularity filters. Tracking detects in
  a window around the predicted LEDs; acquisition detects in the whole image at > 70 and clusters
  the blobs.
- **Acquisition** from one camera, cameras in a fixed order per hand:
  - five or more blobs: an LED-triple search with the IMU's rotation, extended to all blobs by
    EPnP; failing that, a geometric hash of 5-LED patterns, generated from the left model by the
    rule PICO OS's table was built with (identical to the table it ships);
  - three or four blobs: P3P with the IMU's rotation.

  Every result passes PICO OS's plausibility checks: brightness and footprint against the
  distance, the other cameras, and a range box around the head (|x|, |y| < 1.2 m, |z| < 0.8 m).
  Three consecutive acquisitions start tracking.
- **Tracking.** The facing LEDs, projected with the IMU's prediction, are associated with the
  blobs by shift hypotheses. A robust Levenberg-Marquardt fit over all four cameras follows, with
  a soft prior on the IMU's rotation; two to four LEDs give the translation alone. A failed frame
  re-acquires in the same frame.
- **Fusion.** A 15-state error-state filter: optical poses correct position and yaw behind
  chi-square and box gates; tilt comes from the IMU. The output is predicted 41 ms ahead of the
  newest IMU sample. A hand is lost after 1.2 s still or 3 s moving without optical updates, and
  on any gap of more than 35 ms between IMU samples.

In the driver, `pico4_slam.c` collects the LED exposures of the four cameras as sets. It converts
them to 8 bits by dropping the two lowest bits, as `pico4-cam capture --record-leds` stores them,
and hands them to the tracker's thread. That thread waits up to 60 ms for the IMU samples up to a
set's exposure time and takes the head pose at that time from the HMD's own track. The aim pose
is the LED-model frame turned like the 3DoF axis mapping, offset by PICO OS's lever arm of
about 2 cm, which its client adds in that frame after prediction.
[`tooling/leverarm.py`](../tooling/leverarm.py) reads it from the 5.13.7
`libpico_controller_tracking.client.so` and `extract-sensor-data.sh` writes it to
`calibration/controllers/lever-arm.json`; without it, or without the LED models, the tracker stays
off. `PICO4_CTRACK=0` turns the tracker off; `PICO4_CTRACK_TRACE=FILE` writes one CSV row per hand
after every update: the last frame set's acquisition and tracking result and the published pose.

The controller IMU arrives at about 500 samples per second per hand only while another sensor
streams too. With `ble_mcu` alone, the sensor DSP drops it to about 90 events per second, whatever
rate is requested, a few minutes after the DSP started. Then gaps of up to 0.1 s trip the 35 ms
rule about once a second. The driver always streams the head IMU at 1 kHz, and
`pico4-cam --record-imu` records the head IMU along with the controllers for the same reason.
`pico4-cam --record-scene` records the scene exposures (the head-tracking frames) in the format
of `--record-leds`, for replaying head trackers.

PICO OS sends exposure commands all the time, so its tracker sees the LEDs as they are after the
first such command (see the tracking cameras): at 0.7 m neighbouring LEDs merge into one blob and
the centroids move by up to 1 px against the frames before any command. Its station service sets
no LED level for the controllers: the LED gear it derives from the user's body height (station
command `2F`) goes only to the body trackers (devices 3–5), and its controller LED command (`1B`)
has no caller in the tracking service.

`pico4-ctrack-replay` runs the tracker over a pair of recordings, `pico4-cam capture
--record-leds LEDS --record-imu IMU`. It prints per-hand counts (JSON) and with `--trace` a CSV
line per frame set. The head is taken as still, its up direction the mean of the recorded head
accelerometer; `--head-up` gives it instead, and `--estimate-head-up` measures it from frames
where a controller lies still (for recordings without the head IMU). `--max-imu-gap` replaces
the 35 ms limit, `--delay left|right S` holds one hand back, and `--skip S` drops the first frame
sets while the IMU runs.

## Build

Extract the inputs from the stock firmware ([development.md](development.md#stock-firmware)),
as root on Linux/WSL. This needs `debugfs` and `mcopy`; the script first verifies the stock
directory against its manifest and never touches the headset:

```sh
bash devices/phoenix/scripts/extract-sensor-data.sh /var/tmp/pico4-stock/5.13.7-global-seko assets/sensor-data-5.13.7
```

| Output | Content (source partition) |
| --- | --- |
| `firmware/slpi.mdt`, `slpi.bNN`, `slpi*.jsn` | sensor DSP image and protection-domain maps (modem) |
| `sensors/config/*.json`, `sensors/sns_reg.conf` | sensor driver configuration and registry layout (vendor) |
| `calibration/pxrconfig/SDK_Lens_ConfigFile.txt` | stock lens configuration with the chromatic aberration terms (system) |
| `calibration/controllers/led-models.json` | the nominal LED rings of both controllers, read from PICO OS's controller tracking library (system; see Controllers) |
| `calibration/controllers/aim-to-grip.json` | the grip pose of each controller in its aim frame, read from PICO OS's tracking service (system; see Controllers) |
| `calibration/controllers/lever-arm.json` | the lever arm PICO OS adds to each aim pose, read from its controller tracking client (system; see Controller tracking) |
| `rendermodels/pico4_controller_*/` | SteamVR render models of both controllers, converted from PICO OS's controller models (system; see Controllers) |
| `manifest.json` | sources and SHA-256 of every file |

Then build the userspace with it (`make userspace` uses these defaults; see
[development.md](development.md#the-userspace-build) and [userspace.md](userspace.md)):

```sh
bash devices/phoenix/scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
```

The image gets the DSP firmware in `/usr/lib/firmware` and the sensor tree in
`/usr/share/qcom/pico4/`, which `hexagonrpcd` serves to the DSP and the driver reads; the registry
and the factory calibration there link to the headset's persist. The extracted data and the
archive are proprietary: keep them private.

PICO OS's head tracker comes the same way, from the stock system, vendor, modem and dsp
partitions:

```sh
bash devices/phoenix/scripts/extract-stock-slam.sh /var/tmp/pico4-stock/5.13.7-global-seko assets/stock-slam-5.13.7
```

| Output | Content (source partition) |
| --- | --- |
| `firmware/cdsp.mdt`, `cdsp.bNN`, `cdspr.jsn` | the Compute DSP's image and protection-domain map (modem) |
| `root/system/bin/bootstrap/linker64`, `root/system/lib64/bootstrap/` | bionic's linker and C library, which need no Android runtime (system) |
| `root/system/lib64/` | `libSlam.so` and the libraries it needs (system) |
| `root/system/etc/pvr/config/` | the tracker's configuration, lens-shading and gamma tables (system) |
| `root/vendor/lib/rfsa/adsp/` | the tracker's DSP modules (vendor) |
| `root/vendor/dsp/cdsp/` | the DSP's process shells (dsp) |
| `root/vendor/bin/cdsprpcd`, `root/vendor/lib64/` | the DSP's root listener (vendor) |
| `manifest.json` | sources and SHA-256 of every file |

`build-userspace.sh --stock-slam assets/stock-slam-5.13.7` installs the firmware in
`/usr/lib/firmware` and the root in `/usr/lib/pico4-stock-slam`, and builds `pico4-slamd` and
the root's stand-ins ([src/stockslam/build.sh](../src/stockslam/build.sh)) with the host's clang;
it first runs `test_isp` (the image processing against hand-computed values) natively.

- **`pico4-sensors`**: qrtr-ns, pd-mapper and hexagonrpcd from pinned linux-msm commits plus this
  project's patches. Its `meson test` covers the FastRPC ABI translation.
- **`pico4-basalt`**: Basalt and the submodules the library needs, at pinned commits.
- **`pico4-monado`**: Monado plus `src/monado` and `src/xr`. It fetches the Spectra camera UAPI
  headers from a pinned public kernel tree. Its build runs the C tests, because the userspace
  build uses `--nocheck`: `test_pico4_ssc` (encoder against the Python client's messages),
  `test_pico4_lens` (lens model, IPD request and reply parser) and `test_pico4_tracking` (station
  frames against golden bytes and the allowlist, the calibration parser, the Kannala-Brandt
  refit and the 180° turn, the controller packet parser).

The host tests (`python -m unittest`) cover `pico4-imu`'s protobuf/QMI
encoding, a simulated DSP (lookup, attributes, streaming, disable), the IMU acceptance limits,
the orientation filter, the runner's validators and the super-image reader.

## Run

The sensor units start at boot. The fan control ([cooling.md](cooling.md)) keeps the 30 s Valve
session check at 69 °C; longer runs are unmeasured, so keep display and SLAM runs short.

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

The stage-2 checks, `python -m overpressure dev check` in development mode and the same checks inside
`python -m overpressure hwtest flash-run --userspace ARCHIVE …` and `python -m overpressure cycle --userspace
ARCHIVE` ([hardware-testing.md](hardware-testing.md)), test an archive built with sensor data
after the userspace check: they record the sensor units' status and `pico4-imu list accel gyro`,
then require `pico4-imu stream --rate 500 --seconds 10 --check`. They continue with SteamVR's
display layer and Valve's session ([session.md](session.md#acceptance)); Monado's own OpenXR
clients are not part of them.

OpenXR clients reach the driver through the Monado service, a development runtime, with Valve's
session stopped (SteamVR's `driver_monado` holds the same hardware). `pico4-openxr-pose` passes
with the PICO 4 system tracking orientation, no invalid pose after the first second;
`pico4-openxr-display` with frames at 95% of 90 Hz or better and at most 1% of them late. As
root:

```sh
systemctl stop pico4-session.service
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-pose 10 10
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 10 [--projection-only] [--sky]
systemctl stop pico4-monado.service; cat /run/pico4-monado/kms.json
systemctl start pico4-session.service
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
| `PICO4_KMS_GPU_MIN_MHZ` | 0 | GPU clock floor while the target drives the panels (587 for 587 MHz or faster); 0 leaves the governor alone |
| `U_PACING_COMP_MIN_TIME_MS` | 12 (unit) | Monado's compositor time per frame: the frame starts this much plus 4 ms before its vblank |
| `PICO4_KMS_STATS` | unset | JSON statistics written when the target is destroyed |
| `PICO4_KMS_DUMP`, `PICO4_KMS_DUMP_FRAME` | unset, 300 | PPM of that commit's image, plus the eye means |
| `XRT_COMPOSITOR_COMPUTE` | off in this build | Monado's compute composition path |
| `PICO4_SLAM` | on | 6DoF |
| `PICO4_SLAM_BACKEND` | `stock` | `stock`: PICO OS's tracker through `pico4-slamd`; `basalt`: Monado's Basalt |
| `PICO4_SLAMD_LSC` | the vendor's | `pico4-slamd`'s lens-shading table in `/system/etc/pvr/config` instead of the one the camera modules' vendor picks; set in the unit's environment |
| `PICO4_SLAMD_DEBUG` | unset | `pico4-slamd` passes the tracker's debug and verbose log lines to the journal |
| `PICO4_SLAMD_AE` | on | `0`: no PICO OS auto exposure in `pico4-slamd`; the driver's own sets the exposure |
| `PICO4_SLAMD_RESTART_S` | 10 | seconds a tracker that lost its confidence stays lost before `pico4-slamd` restarts it, as PICO OS's service; `0` turns the restart off |
| `PICO4_SLAM_CAMS` | `0,1,2,3` | factory cameras for Basalt in tracker order, at least two; Basalt matches the first against the others |
| `PICO4_TCAM_SLOTS` | `0,2,1,3` | CSL slot of factory cameras cam0–cam3 |
| `PICO4_SLAM_UPRIGHT` | on | turn images that are upside down relative to the first camera's |
| `PICO4_SLAM_DIVISOR` | 1 | use every Nth scene frame set |
| `PICO4_SLAM_IMU_DIVISOR` | 2 | use every Nth head IMU sample |
| `PICO4_SLAM_MAX_EXPOSURE` | 400 | longest station exposure, sensor lines |
| `PICO4_SLAM_EXPOSURE_CENTRE` | on | frame times at the middle of the exposure instead of the start of frame |
| `PICO4_FUSION` | on | the IMU-rate head pose corrected by Basalt's poses; off: Monado's prediction |
| `PICO4_FUSION_POSITION_MS` | 150 | time constant pulling position and velocity to Basalt's |
| `PICO4_FUSION_SMOOTHING_MS` | 50 | time over which a correction reaches the output |
| `PICO4_FUSION_CSV` | unset | directory for `fusion_slam.csv` (each Basalt pose: latency, disagreement, outcome, velocity disagreement with the IMU, speed) and `fusion_pose.csv` (each pose handed out) |
| `PICO4_PREDICT_MS` | 24 | hand out the head pose this much later than asked for (with the fusion), to cover the pipeline SteamVR does not predict; the vsync lead the display layer publishes is added |
| `PICO4_LATENCY_PROBE_MS` | 0 (off) | latency probe for a headset lying still: 1 s phases (with 1000) of a 5° yaw step and of a reported 30°/s turn without turning; with `PICO4_VRDISPLAY_MOTION` and linear scanout (`PICO4_VRDISPLAY_UBWC=0`, session.md) |
| `PICO4_SLAM_RESET_AFTER_S` | 0 (off) | test of the runaway path: reset Basalt once, this many seconds after the fusion started |
| `SLAM_PREDICTION_TYPE` | 3 (gyro and acceleration) | without the fusion, Monado's prediction between Basalt's poses: 0 none, 1 pose only, 2 gyro, 4 dead reckoning |
| `SLAM_ACCEL_DEADBAND` | 0.3 | world acceleration (m/s²) that prediction ignores |
| `PICO4_SLAM_BRIGHTNESS` | 70 | mean 8-bit brightness the digital gain aims for |
| `PICO4_SLAM_VIO_CONFIG` | `/usr/share/pico4-basalt/pico4-vio.json` | Basalt's VIO settings (a full VioConfig JSON); empty for Basalt's built-in defaults |
| `PICO4_SLAM_IMU_NOISE` | `0.016,0.001,0.0016,0.0001` | Basalt's IMU noise model: accelerometer noise density, its bias random walk, gyro noise density, its bias random walk (continuous time) |
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
| Slot mapping | cover test: one slot dark per covered lens; a logo seen by the lower pair triangulates with a 0 mm ray gap at 0.14/0.16 m; with the 481-line capture (metadata line removed) a pen at 0.15–0.18 m triangulates with 1–2 mm gaps |
| Controllers in OpenXR | both as `PICO 4 left/right controller`; per controller about 500 IMU samples and 100 input packets per second |
| Controller inputs, each pressed in turn | trigger, grip, A/B/X/Y, menu, home, stick click and touch (A/X, B/Y, stick, thumb rest) decoded as pressed |
| Sticks pushed right, left, up, down, both controllers | x (record byte 2): 255, 0; y (byte 1): 255, 0 |
| Headset: SLPI restart (`RELATED`) during a Monado session, a pose client running 120 s | the driver saw the service go away at once and reported the pose untracked; `pico4-sensorspd` restarted after 2 s; the IMU stream came back after 36.6 s in the same Monado process; every pose valid, 1,652 of 2,376 tracked; the service stopped cleanly afterwards |
| Headset: frame identity in Valve's sessions (several runs, over 1,250 frame sets each) | no inconsistent and no incomplete set, while auto exposure moved from 300 lines at gain 16 to 400 lines at gain 46: all four sensors take a new exposure on the same frame |
| PICO OS's tracker in Valve's session, headset still on a table, 60 s | 61,391 poses, none lost, within 0.7 mm and 0.04° of the first; 25 frame sets and 1,024 IMU samples per second to `pico4-slamd`, none skipped, no frame without the hardware start of frame; `pico4-slamd` 71 % of one core, `vrserver` 116 % (with Basalt 346–388 %); hottest zone 55.8 °C (with Basalt 75.8 °C) |
| PICO OS's tracker worn, untethered: a closed walk through the room back to the start spot, facing the same way | 12.14 m in 58 s; back within 1.8 cm of the start; no pose lost; largest step between consecutive poses 0.7 mm; fastest turn 86 °/s, top speed 0.7 m/s |
| PICO OS's tracker: headset slid along a table and back to its spot, twice | 0.512 m and 0.495 m; back within 0.4 cm and 0.8 cm of the start; no pose lost |
| PICO OS's tracker worn in Valve's session: head turns, walking, controllers moved (user) | "feels and looks just like stock"; turns up to 458 °/s without a lost pose |
| PICO OS's tracker, headset turned and lifted by hand for 72 s (73,596 poses) | its angular rate equals the raw gyro and the poses' own rate (ratio 1.000), its velocity the positions' own (1.001); extrapolated 45 ms with them, within 0.07° and 0.3 mm of the pose that came 45 ms later (median; 0.63° and 5.5 mm without); after the HMD's conversion to OpenXR the angular velocity still matches the orientation's rate (0.999) |
| `CLOCK_MONOTONIC` against the raw counter (timesyncd running) | 0.998 ms in 60 s: 16.6 ppm |
| A Monado client while Valve's session runs | Monado's service is refused the station (locked by SteamVR's driver) before any reset, gets no cameras, and fails the client in 3 s (`xrGetSystem`); no core dump, a clean stop; Valve's session keeps its controllers and its tracker (no new generation) |
| IMU temperature to the tracker, Valve's session | `sensor_temperature` of the ICM-42688 at 44.1–44.3 °C with the SoC's hottest zone at 60–61 °C; still within 0.8 mm for 60 s, no pose lost |
| PICO OS's tracker, a recorded walk replayed (4 cameras and the IMU, hardware start-of-frame times) | back within 9 cm of the start; lost in the fastest part of a turn and relocalised in its own map |
| PICO OS's tracker, a still recording looped for 5 minutes | 39 % of one core, back within 6 mm of the start, farthest 7 mm, no jump |
| Frame times: the boot-time start-of-frame stamp against the hardware one | 395 ms late, constant within 0.2 ms over a boot |
| SLAM feed, 30 s, four cameras, no compositor | 744 frame sets at 25 Hz, 0–1 incomplete, none skipped while Basalt was busy, no IMU sample dropped; frame to tracker 15–23 ms |
| 6DoF: headset on a table in a lit room, slid 50 cm (measured) and back, twice | 0.50 m and 0.52 m; back at the start within 1.5 and 1.8 cm; at rest within 5–11 mm. In a dim room (exposure at its limit, gain 202–230) 0.48 m and back within 3.7 and 4.9 cm, with a 9 cm wander at rest before the first slide |
| 6DoF worn in Valve's session: a step forward and one to the left, each back onto a floor mark | steps of 0.57 m and 0.59 m at 92° to each other; back within 3.4 and 4.4 cm of the start; no reset in 2.8 minutes; user: tracking good, no freeze or jump |
| 6DoF: one camera covered, headset still, 40 s | exposure on the median: the others at 249, 257 and 533 above black (target 250), the covered one at 6; Basalt creeps at 0.15–0.17 m/s and the fusion holds and resets it four times. Uncovered in the same room: no reset in 65 s |
| 6DoF: lowered from head height onto a table | 0.75 m down, then still within millimetres for 30 s |
| 6DoF with `PICO4_SLAM_UPRIGHT=0`, walking | tracks as well in this test; the turn stays on as the documented geometry |
| Head IMU rate in the driver, 1 kHz request | 1023.9 Hz measured, 1024 Hz declared, 512 Hz to Basalt |
| SLAM pose latency, images taken to pose, headset still | 641 ms with Basalt's original queues; 81 ms with the bounded ones (62 ms with two cameras) |
| SLAM pose latency, worn in Valve's session | median 112 ms, 90th percentile 157 ms; no gap in the poses |
| Predicted pose against Basalt's, head moving | gyro prediction: rotation 15 ms and position 65 ms behind, 0.4 mm wander at rest; gyro and acceleration with the deadband: 12 ms and 31 ms, 1.5 mm |
| 6DoF worn in Valve's session, Monado's prediction (`PICO4_FUSION=0`) | tracks, no snaps to another heading; the image lags and jumps by 2–30 cm (the extrapolation of about 150 ms amplifies the velocity noise of 25 Hz poses) |
| Two-layer display test (30 s), GPU at the governor's clocks, 12–16 ms compositor time | 89.2–89.75 Hz, 9–28 late frames in 14 runs (median 89.5 Hz, 15.5 late), GPU 9.2–9.5 ms per frame; with the 587 MHz floor and 3 ms in the same conditions 89.5–89.6 Hz, 14–18 late, 8.5 ms: the late frames do not depend on the floor. Without Basalt (`PICO4_SLAM=0`) 89.77 Hz and 7 late in both runs; Basalt on the four small cores alone (`PICO4_SLAM_CPUS=15`) 6 and 24 late |
| GPU floor after a crash | `vrcompositor` killed while its floor (power level 1) was raised: level 6, KGSL's default, right after; a compositor that found level 1 left behind restores 6 on exit, and Monado's unit after its stop |
| Basalt VIO settings | Basalt logs "Using VIO config /usr/share/pico4-basalt/pico4-vio.json" when the PICO 4 driver starts SLAM |
| Exposure phases, `pico4-cam` with each set varied (80 frames per camera) | scene frames take the first set (60–400 lines, gain 16–128); LED frames 4 lines at gain 26 with the second set at 0/0, 2/20, 4/26, 8/30 or 10/32; LED frames peak at 344–377 of 1023, no sample saturated, while scene frames saturate 14–4,650 samples per frame |
| Metadata line, `pico4-cam` | decodes to the commanded exposure and gain (300 lines and gain 32, 120 lines and gain 16); every camera 50 scene and 50 LED frames of 100; LED frames 4 lines at gain 26; the 481st captured line continues the scene (mean row difference 8.8 against 8.4 one row up), 0 errors at 49.8 Hz |
| Fusion, headless, headset still, 30–40 s | agrees with Basalt within 0.3–0.6 mm (median), latency images to fusion 76–92 ms (median); a forced Basalt reset (`PICO4_SLAM_RESET_AFTER_S`) restarts it without a jump; velocity disagreement with the IMU at rest: 99th percentile 0.025 m/s |
| Fusion against Basalt, worn | the handed-out pose matches Basalt's at a 0 ms time shift (rotation within 0.35°), no lag of its own; still, it moves 0.36 mm and 0.024° around its 200 ms average (Monado's prediction 0.21 mm and 0.013°) |
| Staged jiggle, headset still, 4 × 27 s | Basalt 0.22–1.06 mm and 0.004–0.032° around its 200 ms average; fusion output 0.06–0.21 mm and 0.001–0.003°; SteamVR's extrapolation with the reported velocities 0.06–0.29 mm; slow drift 11–76 mm peak to peak (Basalt's) |
| IMU noise A/B (ours against PICO OS's values), still and worn blind | Basalt with PICO OS's values: 0.49–3.02 mm jiggle against 0.10–0.29 mm, two runaways worn against none; the user found ours calmer |
| Latency probe, headless, 13 steps | pose handed out to scanout 33.0 ms (median; 32.6–33.5 ms for most); SteamVR extrapolates a reported turn by 9.2 ms (median); 21.6 px per degree at the eye's centre |
| Latency probe in Valve's session check with the published 5 ms display lead (linear scanout, 74 commits/s), 36 phase changes | pose handed out to scanout 32.2–32.6 ms (medians of step on, step off and reported turn); 24 at 30.5–34.2 ms, 9 one frame earlier (19.3–22.1 ms), 3 later; SteamVR extrapolates a reported turn by 10.4 ms; 22.0 px per degree |
| Prediction 24 ms against none, worn blind A/B | user: "almost flawless" against "horrible" (Basalt ran away three times in the latter) |
| Head pose worn, standing still | 0.035° RMS around its 200 ms average at 1.7°/s, as in Basalt: the head's own motion (0.001° on a table) |
| Runaway handling, seven worn sessions of about a minute | Basalt within 0.55–1.51 m of the start (before: 13.6 m and 1,750 m in two of four sessions); three sessions had runaways in Basalt's first seconds, each held, reset and re-anchored; no pose above 3 m/s afterwards in five; correction size 4.6–8.0 mm (median) |
| 6DoF, lower cameras about 15 cm from a surface | 1.6–4.8 m drift in 30 s (6.5 m in a 10 s run): no stereo depth, not an accuracy test |
| Controller tracker on the headset (`pico4-ctrack-replay`), both controllers lying 0.71–0.73 m ahead, 30 s, LEDs before any exposure command | both acquired from the identification table in the third frame set and tracked in 747 of 750 frame sets, no loss; 20–23 LEDs per set, median residual 0.62–0.77 px; after 10 s of settling the optical pose stays within 1.9 mm and 0.9° (20–30 s: 1.4 mm and 0.3°); 2.7 s for 250 frame sets with file reading (under 11 ms per set) |
| Controller tracker in Monado's service, same controllers, after exposure commands | every LED set solved (1,842 of 1,849; 7 before the first head pose), no IMU wait timed out; the right controller tracked with 21–24 LEDs at 0.66–0.76 px; the left not acquired: its identification-table fits miss stock's limits by 12–46° of tilt with the brighter, merging LEDs (the replay of a recording with exposure commands shows the same) |
| Controllers in SteamVR (Valve's 30 s session check), same controllers | the tracked right controller 0.77 m from the headset and 0.30 m below it, against the tracker's 0.76 m and 0.27 m before the 2 cm lever arm; the unacquired left at the placeholder, 0.35 m below and 0.35 m out |
| Controllers worn in Valve's session: lying on the table while the head turns and nods | both acquired within about 9 s of the head moving; the right one at one world position for 53 s; the left tracked while lifted about 25 cm |
| Controllers worn: the right one slid 30 cm along a ruler, three times there and back | 30.4, 32.5, 33.3, 31.1, 32.0 and 31.6 cm (mean 31.8 cm) |
| Controllers worn: hands crossed three times | the models change sides with the hands and back; no swap, no jump |
| Controllers worn: lowered into the lap out of view for about 10 s, then raised | none closer than 0.49 m to the head; while lost, orientation steps 0.03 rad (median) and position steps under 1 cm, except the arm model following a turning controller (up to 1.6 cm); re-acquired when raised |
| Controllers worn, held still in front | the tracker's output within 0.2–0.4 mm and 0.06° (RMS about a 0.25 s mean), SteamVR's poses within 0.2–0.3 mm above 10 Hz; with PICO OS's render models no jiggle is visible (user) |
| Controller render models in SteamVR | both loaded from `{monado}pico4_controller_left/right`; at the hands and turned like the real controllers (user) |
| Controller vibration, station command `01` (`pico4-station vibrate`) | 1 s at level 255 (75 %) felt on the addressed controller, left and right; 300 ms not noticed |
| Vibration from SteamVR (`vrcmd --spamhaptics`, left controller) | 376 requests, 376 motor frames to the left controller, none to the right |
| Controller tracker, replay: held and moved, 30 s, controller IMU at 90 events per second (recorded without the head IMU) | left tracked in 79 % and right in 54 % of the frame sets; median residual 0.83–0.89 px |
| Controller tracker, replay: both controllers lying about 1 m ahead | no acquisition: the identification-table fits (0.05 px) lie at 0.99 m, outside PICO OS's range box (\|z\| < 0.8 m) |
| Controller IMU rate | about 865 radio packets and 495 samples per second per hand with the head IMU streaming, for 30 s and in Monado's service; with `ble_mcu` alone about 90 a few minutes after the DSP started, whatever rate is requested |
| Controller IMU rate, both controllers held and moved (median 234–260 °/s), 30 s | 494 and 499 samples per second per hand, 492–500 in every second; longest interval 4 ms |
| Controller gyro scale: the right controller turned like a key, three turns of about 90° between pauses, against the optical tracker | gyro rotation 0.993–1.003 times the optical rotation at 8.192 LSB per °/s, after subtracting the gyro's bias (about 1.5 °/s, from the controller lying still) |
| Controller samples, recordings of about 60,000 per hand (still, and moved) | no duplicate, backward or future-stamped sample; single samples lost now and then (4–9 per 30 s) |
| Controller calibration records from the station, both controllers | 12 chunks each; gyro and accelerometer matrices within 0.4 % of identity, gyro biases up to 0.016 rad/s, LED positions 0.3 mm (mean) and 0.6 mm (largest) from the nominal models; the serial of each connect notification names one of the two bias tables in persist (9 and 8 temperature bins) |
| Controllers worn in Valve's session (120 s): both held, the left switched off for 10 s and on again | both tracked with their own calibration; the left disconnected, held its place while off (user) and connected again, with its record re-read in 48 ms and tracking at once; event time minus station time 0.00–0.99 µs in every 30 s window, across the reconnect and under the session's load |
| Acceptance cycle (`flash-run --userspace`, then stock restoration) | passed: archive received in 86–105 s, SSH 112–123 s after the start of the transfer; systemd `running`, 0 failed units, storage read-only, USB link unchanged, Turnip 65,536 pixels; IMU, pose and display checks as above (two layers, Basalt running); stock boot verified by EDL readback, stock ABL restored, PICO OS started |
| Room view in Valve's session (`pico4-roomview`, still headset) | the compositor imports the frames ("Late initialization successful"); frame dumps show the room in both eyes, the dashboard over it; 25 frames per second per camera published, none skipped; with the room view on for 60 s the stock tracker lost no pose and stayed within 1.3 mm of its start (1.1 mm with it off), no tracker warnings |
| QEMU, same archive (4.19 without SLPI or `msm_drm`) | self-test passed; the sensor units and Monado's PICO 4 prober stay inactive |

## Next steps

1. **PICO OS's tracker.** Worn it tracks walking and fast turns as on PICO OS; a covered
   camera and a dim room are unchecked, and so is PICO OS's auto exposure worn.
   [Room setup](#room-setup): SteamVR's room setup runs in the headset, the floor height and
   the play area are set easily with the colour room view, and the play area comes back after a
   reboot.
2. **Controllers in applications.** SteamVR's dashboard toggles but stays hidden without the
   Steam client ([session.md](session.md)), so the laser, clicking and the grip frame are
   unchecked in it. A vibration strong enough to feel has not yet come from a SteamVR
   application: SteamVR's own test pulses are 2.5 % strong.
3. **Controller positions.** The tracker runs in the driver. A controller that lies still from
   the start is acquired only through the identification table, as on PICO OS: the table path
   trims the blob cluster to five, and with the LEDs as bright as in the session the five of a
   controller lying 0.7 m ahead need not form one of the table's 44 LED groups, so it stays
   unacquired until it or the head moves. The grip frame needs checking with SteamVR's dashboard,
   which needs the Steam client in the session.
4. **Passthrough.** The room view shows the RGB camera on PICO OS's depth mesh
   ([Passthrough](#passthrough)); worn, turning and nodding hold the room. Open: colour as
   the default, controller tracking with colour on, an auto exposure that leaves
   out the lens ring, and the CVP firmware in the system's `/lib/firmware` (the mesh needs it
   after a reboot).
