# Native XR hardware (roadmap step 5)

Step 5 asks for IMU access, camera acquisition, 6DoF tracking, controller connectivity and lens
calibration under Linux, on the stock PICO kernel (4.19.81-perf+, PICO OS 5.13.7). This file
holds the hardware inventory and the parts that work so far. The inventory comes from the stock
image (`backup/`, unpacked privately), the stock kernel and device tree, and read-only
`adb shell` observation of a running PICO OS. No vendor files are part of this repository.

> **Head IMU (3DoF) working on the tested headset, 2026-10-17.** In the step-4 userspace, a
> free software stack boots the sensor DSP and streams the ICM-42688 accelerometer and
> gyroscope at 512 Hz, with no gaps and monotonic DSP timestamps. A guided, edge-aligned
> 90° turn of the headset read +90.3 to +90.5°, and turning it back closed to within 0.4°.
>
> **OpenXR head orientation through Monado, 2026-10-18.** Monado 25.1.0 has a
> [PICO 4 driver](#monado-3dof-driver) that fuses this IMU at 1 kHz. An OpenXR client reads the
> head pose through the standard loader. Over 60 s on a still headset, every pose after warm-up
> was tracked, the sample-to-pose latency was about 1 ms, and the view rotated by 0.009°. The
> tilt agreed with the accelerometer within 0.2°.
>
> **OpenXR frames on both panels, 2026-10-18.** Monado's compositor now presents through the
> stock KMS driver with a [PICO 4 target](#display-output-through-monado). It uses the fenced
> six-layer path verified in step 3. A Vulkan OpenXR client rendered 5,400 frames in 60 s at
> 90.000 Hz with 3 late frames. The panels refreshed at 89.998 Hz, and every display fence
> arrived. Each eye's content was on its own panel, in the scanned-out image and as seen in
> the lenses. Frames with several layers also run at 90 Hz after three bandwidth fixes.
>
> **Lens distortion and IPD, 2026-10-18.** The compositor pre-distorts each eye with the
> headset's [factory lens model](#lens-distortion-and-ipd), including the stock chromatic
> aberration terms, over a 106.7° field of view. The driver reads the IPD read-only from the lens
> motor's MCU. In the lenses, grid lines and checkerboard edges looked straight, and the IPD
> read 72.0 mm, which matched the wheel setting.
>
> **Tracking cameras, 2026-10-18.** All four OV7251s
> [stream through Spectra CSL](#tracking-cameras) at the controller radio's 50 Hz frame sync. In
> 30 s every camera delivered 1,500 of 1,500 frames, with 17.5 ms mean latency from start of
> frame to delivery. The radio also sets the exposure.
>
> **Controllers, 2026-10-18.** The radio's controller stream reaches Linux through the sensor
> DSP. Both [controllers](#controllers) appear in OpenXR with buttons, touch, sticks, triggers and
> 3DoF orientation, at about 500 IMU samples and 100 input packets per second each. The inputs
> were checked button by button with the user.
>
> **6DoF head tracking, 2026-10-18.** Basalt runs inside Monado on all four cameras and the head
> IMU ([6DoF head tracking](#6dof-head-tracking)) at 25 Hz, without blocking the 3DoF path. A
> measured 50 cm slide read 0.52 m and returned to the start within 2–4 cm. A still headset held
> within millimetres. Walking with the headset was tracked in the right directions. A Monado
> service, controller positions (IR-LED tracking) and a controller check inside an OpenXR app are
> open; see [next steps](#next-steps).

## Inventory

The selected board overlay is `kona MTP` (dtbo index 7). Its XR nodes match the live system.

| Function | Hardware | Kernel interface (stock 4.19) | PICO OS user today |
| --- | --- | --- | --- |
| Head IMU | TDK-InvenSense ICM-42688 (`icm4x6xx`: accel, gyro, temperature) and AKM AK0991x magnetometer, on the sensor DSP (SLPI) | none on the AP; Qualcomm Sensor Core (SSC) via QMI over QRTR, SLPI boot via `/sys/kernel/boot_slpi` | `sscrpcd` (serves the registry to the DSP), `sensors.qti`, `pvrtrackingservice` via `libssc` |
| Tracking cameras | 4 × OmniVision OV7251 (640×480 mono global shutter, left/right master/slave) | Qualcomm Spectra CSL: `/dev/video0-3`, `/dev/v4l-subdev0-25`, `/dev/media0-2` (no plain V4L2 capture); frame-sync timestamps in `.../cam-sensor*/sixdof` | camera HAL (CamX) + PICO `quickcamera` HAL, `pvrtrackingservice`, `pxrseethroughservice` |
| Other cameras | IMX471 RGB; OV680 bridge; ET-left nodes (Pro only) | same Spectra stack | see-through, MR services |
| Controllers | Nordic nRF52832 radio ("station"), ESB or BLE firmware, on SPI | `/dev/spidev0.0` (`picovr,nordic`, world-writable on PICO OS); `/dev/stationdev` (`bytedance,station`: power, boot GPIO, sync IRQ, timestamps) | `stationservice` (`libstationopticsservice.so`): pairing, controller IMU stream with per-controller IMU calibration, IR-LED mode, firmware OTA |
| Controller pose | IR LEDs on the tracking rings, seen by the tracking cameras | cameras above | `pvrtrackingservice` with `libpico_controller_tracking` |
| IPD motor | GigaDevice GD32 MCU | `/dev/ttyHS1` (GENI UART, `984000.qcom,qup_uart`) | `gd32ipdservice` |
| Lens/display | Sharp LS026B3SA ×2 | `msm_drm` (verified in step 3) | `pxrhmdservice` reads lens and IPD tables |

The SSC registry also carries configurations for other boards (LSM6DST, LPS22HH, …). On this
headset the DSP reports only the ICM-42688 and AK0991x sensors listed above.

## Factory calibration on this headset

`persist` (backed up in `lun0/persist.bin`) holds per-unit calibration, in formats that
Monado/Basalt can consume with little conversion:

- `pvr/mav0/cam0..cam4/sensor.yaml`: EuRoC/Kalibr-style camera models (omni ξ + radtan distortion,
  intrinsics, extrinsics `T_BS` to the body frame, 640×480 @ 30 Hz). The factory report
  `pvr/mav0/info.txt` gives sub-0.25 px reprojection errors.
- `pvr/mav0/imu0/sensor.yaml`, `pvr/ImuData.txt` (a 512 Hz accel/gyro reference capture),
  `pvr/gyroBias.txt` (temperature table), plus the SSC registry's factory gyro/accel calibration
  under `sensors/registry`, which the DSP applies itself.
- `pvr/OpticalCalibrationData.json`, `pvr/lens/*`, `ipdcalidata.txt` (IPD ADC → position):
  display colour, lens and IPD data used by `pxrhmdservice`.

These are personal device data. Like the rest of the backup, they stay out of Git.

## Head IMU through the sensor DSP

The IMU is not wired to the application processor. It hangs off the SLPI, a Hexagon DSP running
Qualcomm's Sensor Core, so Linux has to do what PICO OS's init does. postmarketOS uses the same
free software route on mainline kernels; this project adapts it to the stock 4.19 vendor kernel:

1. **Name service.** `qrtr-ns` (linux-msm/qrtr) provides the QRTR name service. Linux 5.7+ has it in the
   kernel; 4.19 does not, and PICO OS also runs `qrtr-ns` in userspace. Upstream qrtr
   dropped it, so the build pins the last commit that has it.
2. **Firmware boot.** Writing to `/sys/kernel/boot_slpi/boot` makes the kernel's PIL loader boot
   `slpi.mdt`/`.bNN` from the headset's own modem partition. Before that, `slpi-boot` sets the
   subsystem's `restart_level` to `RELATED`. The kernel default is `SYSTEM`, where any DSP crash
   or restart request reboots the whole headset (observed once during bring-up).
3. **Service locator.** `pd-mapper` (linux-msm) answers the DSPs' service-locator queries from
   `slpi*.jsn`. It is patched to take directories as arguments, because PIL-loaded DSPs have no
   remoteproc sysfs entries to find them through.
4. **Registry server.** `hexagonrpcd` (linux-msm/hexagonrpc) attaches to the sensors process
   (`sensorspd`) and serves it the sensor configuration and this headset's registry through
   FastRPC. Two patches, both in
   [`hexagonrpc-vendor-kernel.patch`](userspace/pkg/pico4-sensors/hexagonrpc-vendor-kernel.patch):
   - **Downstream ioctl ABI.** hexagonrpc targets the upstream `misc/fastrpc.h` driver. A small,
     unit-tested translation layer drives the downstream `adsprpc` ABI instead: `GETINFO`
     selects the DSP channel, `INIT_ATTRS` with `ATTACH_SENSORS` attaches, and invocations use
     16-byte `remote_arg` entries. The two ABIs reuse ioctl numbers with different meanings,
     so a descriptor is only ever driven with the ABI it was attached with.
   - **Registry version file.** It also serves `sns_reg_version`, so the DSP accepts the
     existing registry.
5. **Client.** [`pico4-imu`](userspace/rootfs/usr/bin/pico4-imu) is the client: a dependency-free
   Python QMI client of the SNS client service (QMI 0x190). It finds sensors by data type,
   reads their attributes, streams accel and gyro, and reports rate, timestamp monotonicity,
   gaps, gravity, gyro bias and a gravity-corrected orientation estimate.

Findings on this kernel:

- **Secure channel.** The SLPI FastRPC channel is marked secure, so `/dev/adsprpc-smd` returns
  `EACCES` and `hexagonrpcd` must use `/dev/adsprpc-smd-secure`.
- **Persistent listener.** The FastRPC listener has to stay alive. If a listener dies, the
  sensors process keeps it registered: a new `hexagonrpcd` cannot attach until the SLPI
  restarts.
- **Read-only registry.** `hexagonfs` is read-only. During startup the DSP tries to rewrite the
  registry groups (about 1650 refused `temp.json` writes, then quiet). Its factory calibration
  is read from the registry and stays in effect.
- **Rate selection.** The SSC picks the next supported output data rate at or above a request
  and reports it: 500 Hz runs the ICM-42688 at 512 Hz, 512 or 1000 Hz at 1024 Hz. Timestamps
  are QTimer ticks (19.2 MHz), the same counter as the AP's arch timer.

The units (`pico4-sensors.target`: `pico4-qrtr-ns`, `pico4-slpi`, `pico4-pd-mapper`,
`pico4-sensorspd`) only start when the userspace was built with sensor data.

## Monado 3DoF driver

The `pico4-monado` package builds Monado 25.1.0 (pinned by commit) with a `pico4` driver.
The driver lives in [`userspace/pkg/pico4-monado`](userspace/pkg/pico4-monado), and
[`monado-pico4-driver.patch`](userspace/pkg/pico4-monado/monado-pico4-driver.patch) registers
it. The package builds Monado as an in-process OpenXR runtime and makes it the system's active
runtime (`/etc/openxr/1/active_runtime.json`).

- **Sensor service.** `pico4_ssc.c` is a C port of the `pico4-imu` client, limited to service
  lookup, sensor lookup, enable/disable and sensor events. Its unit test checks the encoder
  byte for byte against messages produced by the hardware-verified Python client. The test
  also runs during the package build.
- **Auto-probing.** The prober only creates the HMD where `/sys/kernel/boot_slpi` exists and
  the SNS client service answers. Elsewhere Monado continues without the driver: QEMU, other
  kernels, or a userspace built without sensor data.
- **Time base.** Sensor timestamps are QTimer ticks, and on this SoC the arch counter is the
  QTimer (`cntfrq_el0` = 19.2 MHz). The driver reads `cntvct_el0` alongside `CLOCK_MONOTONIC`
  and maps each sample into Monado's timeline. Measured sample-to-pose latency: about 1 ms.
- **Axes.** The IMU's +X is up, −Z forward and −Y right, derived from the factory camera
  extrinsics (the RGB camera looks along −Z with image-up +X). The driver rotates samples into
  the OpenXR head frame (x right, y up, z back).
- **Fusion.** Monado's `m_imu_3dof` runs at 1 kHz with gravity correction. Two additions came
  from the measurements:
  - **Warm-up.** The first accelerometer samples after enabling are off by about 2°. The
    driver skips 100 samples and starts the fusion level from the mean of the next 100.
  - **Gyro bias.** The bias (about 5 mrad/s here) tilts the estimate away from gravity faster
    than the correction pulls it back. The driver learns the bias in quarter-second windows in
    which gyro and accelerometer are quiet and the rate is below 0.02 rad/s. That excludes
    deliberate turns.
- **Display description.** Two 2160×2160 views side by side (4320×2160) at 90 Hz. The field of
  view and the distortion come from the lens data
  ([Lens distortion and IPD](#lens-distortion-and-ipd)). Without that data the driver falls back
  to the stock runtime's symmetric 104° and no distortion.

[`pico4-openxr-pose`](src/xr/pico4-openxr-pose.c) is the acceptance client. It uses the
OpenXR loader with `XR_MND_headless`, so it needs no graphics. It locates VIEW at the current
time every 10 ms and reports the system name, tracked/valid counts, rotation, heading drift
rate and the tilt from vertical. The tilt is measured in STAGE because LOCAL keeps only the
start yaw of a tilted head.

## Display output through Monado

Monado's direct-mode targets need `VK_KHR_display` or an X/Wayland server. Turnip's KGSL
backend has no display support, and the stock kernel's panels follow a vendor KMS contract
anyway. The package therefore adds a compositor target,
[`comp_window_pico4_kms.c`](userspace/pkg/pico4-monado/comp_window_pico4_kms.c). It
presents with the sequence that step 3 verified ([GRAPHICS.md](GRAPHICS.md)):

- **Selection.** The target is picked automatically wherever `/dev/dri/card0` is `msm_drm`
  with a connected 4320×2160 DSI output. `PICO4_KMS=0` turns it off.
- **Images.** Three linear Vulkan images (4320×2160, both eyes side by side), exportable as
  dma-bufs. Turnip allocates them from ION on this kernel. `msm_drm` imports them with PRIME
  and adds them as `XRGB8888` framebuffers. Compositor and display share the memory with no
  copy.
- **Layers.** The stock arrangement: six layers, one per colour component and eye, routed by
  `sspp_layout`. The right eye reads the right half of the shared image.
- **Commits.** The first commit sets the 90 Hz mode. Every later commit flips all six layers
  without blocking and requests the CRTC release fence and the connector retire fence.
  - Before each commit the target waits for the renderer's queue and for the previous
    commit's retire fence. Only then can the image acquired next be off screen.
  - Pacing uses Monado's fixed-period pacer at the HMD's 90 Hz, phase-locked to the
    timestamps of the panel's vblank events. On its own it counted frames from its creation,
    at an arbitrary phase against the panel.
- **Real-time threads.** Monado runs its compositor threads at real-time priority, and the
  stock kernel only grants that outside cgroups with the `cpu` controller: with
  `CONFIG_RT_GROUP_SCHED` on cgroup v2, `SCHED_FIFO` fails with `EPERM` there, even for root.
  systemd enables that controller by default, for CPU accounting and through the user
  manager: `user@.service` delegates it, and its slices set `CPUWeight`. Drop-ins in
  `userspace/rootfs/etc/systemd` turn all three off. Without real-time threads, the
  compositor competed with Basalt and missed 50–80 refreshes in 30 s, with
  client-fence stalls of up to 300 ms; with them it misses 1–4.
- **GPU clock.** While the target drives the panels, it keeps the GPU at 587 MHz or faster
  (KGSL `min_pwrlevel`; `PICO4_KMS_GPU_MIN_MHZ`, 0 turns it off). On exit it restores the
  previous floor. Under the compositor's load, the stock `msm-adreno-tz` governor stayed at
  305 MHz.

Monado has two composition paths:

- **Fast path.** A frame with a single projection layer is distorted straight from the client
  images into the target.
- **Layer squasher.** Any other frame (for example a projection plus a quad layer) is first
  rendered into per-eye scratch images, which a distortion pass then copies into the target.

A first version ran two-layer frames at 30 Hz, which looked visibly stuttery in the headset.
GPU timestamps around both passes showed that each pass was limited by memory bandwidth. The
memory ran at its full 2,096 MHz, and the GPU reached about 25 GB/s with real data. Three
changes in the patch bring the squasher to 90 Hz:

1. **Scratch images without storage use** when the compositor uses its graphics pipelines.
   Turnip on Adreno 6xx does not compress (UBWC) storage images, and Monado had requested
   storage for the compute path in every case. This cut the squash pass from 12.7 to 7.7 ms
   and distortion from 9.0 to 6.7 ms.
2. **Scratch images at panel size** (2160² per eye) instead of the 140% view size. Without lens
   distortion they map 1:1 to the panels; larger ones only cost bandwidth. With this, squash
   took 4.0 ms and distortion 5.2 ms.
3. **The GPU clock floor** above: squash then takes 2.3 ms.

The compute path is switched off for this build (`XRT_COMPOSITOR_COMPUTE=1` brings it back):
its writes into the linear images took twice as long. Measured on the headset, 10 s runs:

| Frame content (140% eye buffers, 3024²) | Setup | GPU time per frame | Frames on the panels |
| --- | --- | --- | --- |
| One projection layer | compute, 305 MHz | 18.5 ms | 43 Hz |
| One projection layer | graphics, final | 6.2 ms | 90 Hz |
| Projection + quad layer | graphics, original scratch images | 22–23 ms | 30 Hz |
| Projection + quad layer | no storage use | 14.4 ms | 45 Hz |
| Projection + quad layer | + panel-size scratch, 305 MHz | 9.1–10.3 ms | 61–65 Hz |
| Projection + quad layer | + 587 MHz floor (final) | 8.0 ms | 90 Hz |

[`pico4-openxr-display`](src/xr/pico4-openxr-display.c) is the acceptance client. It renders
through `XR_KHR_vulkan_enable2`:

- **Projection layer.** A dim grid with a large L (left eye, red) or R (right eye, blue).
- **Quad layer.** A checkerboard 1.5 m ahead in LOCAL space, so it stays put while the head
  turns. `--projection-only` leaves it out.
- **Report.** The frame rate, the display period, late frames and tracking.

The target writes its own statistics with `PICO4_KMS_STATS=path`. With
`PICO4_KMS_DUMP=path` it also saves one scanned-out image (commit `PICO4_KMS_DUMP_FRAME`,
default 300) as a PPM, and records the mean colour of each eye.

## Lens distortion and IPD

The lens model was read out of the stock compositor (`libpxrcompositor.so`, its warp-mesh
builder) and the configuration service that feeds it. It works like the Oculus mobile SDK's:

- **Distortion.** A point on the panel, `p` metres from the lens centre, is seen at tan-angle
  `t·s(|t|²)`, with `t = p / F` and `s` a polynomial K0…K10 in `|t|²`. `F` and the coefficients
  come per eye from `pvr/OpticalCalibrationData.json` in `persist`. On this unit they match the
  stock table entry for the lens model and mould cavity, so they are not measured per headset.
- **Lens centre.** The centre is the panel centre. The panels move with the lenses, so the
  IPD does not change the distortion. The calibrated offsets are below 0.1 mm and are ignored.
- **Chromatic aberration.** Red and blue scale the green tan-angle by
  `1 + CA0 + CA1·r² + CA4·r` and `1 + CA2 + CA3·r² + CA5·r`. The six terms come from the stock
  `SDK_Lens_ConfigFile.txt` in the system partition. `extract-sensor-data.sh` copies that file
  next to the calibration. The correction is off by default (`PICO4_LENS_CA=1` turns it on),
  as on PICO OS, whose rule files set `sdk_chromaticStaus = 0`. With three separate texture
  reads per pixel, the two-layer test scene dropped to 83–87 commits per second; geometry
  alone holds 87–88.

[`pico4_lens.c`](userspace/pkg/pico4-monado/pico4_lens.c) implements this, and the driver gives
it to Monado's distortion mesh (`compute_distortion`, one UV per colour channel). The polynomial
is only fitted inside the panel's inscribed circle and diverges in the corners. Beyond that
circle the tan-angles therefore grow steeply, so the image ends cleanly at the edge of the
rendered view. The field of view runs from the panel edges through the green channel: about
±53.3° per eye here, against the stock runtime's symmetric 104°.

The IPD comes from the GD32 MCU that drives the lens motor and the backlight, on `/dev/ttyHS1`
(115200 8N1). [`pico4_ipd.c`](userspace/pkg/pico4-monado/pico4_ipd.c) sends one command,
`A0 08 00 00 00 A8` (get position), once at start. The reply carries the lens separation in
0.01 mm and the Hall sensor reading, told apart by range. The module cannot build any other
frame: the MCU also takes motor moves, calibration writes and firmware. The driver passes the
separation to Monado's view poses and falls back to Monado's default if the MCU does not answer.

## Controller radio ("station")

The nRF52832 on `/dev/spidev0.0` relays the controllers. It also generates the tracking
cameras' frame sync (FSIN). PICO's `stationservice` drives it through a vendor-modified
`spidev`: fixed 40-byte frames (`AA` to the station, `BB` replies, `A5` notifications) with a
CRC-8. The kernel moves whole frames on the station's interrupt, so userspace only `write()`s,
`select()`s and `read()`s.

- **Data path.** Controller IMU, buttons, sticks, touch and battery do not come over SPI. The
  station streams them to the sensor DSP, where the SLPI firmware exposes them as the SSC
  sensor `ble_mcu`. The driver enables that sensor through the same client as the head IMU.
  SPI is only the control plane.
- **Bring-up.** A power cycle through the driver's power ioctl. The station then announces itself
  (`A5 06`) and waits for the camera frame rate (`19`, 50 Hz as stock) before it accepts camera
  commands.
- **Safety.** The same link carries station and controller firmware updates. The C module
  ([`pico4_station.c`](userspace/pkg/pico4-monado/pico4_station.c)) and the
  [`pico4-station`](userspace/rootfs/usr/bin/pico4-station) tool build frames only from an
  allowlist:
  - acknowledge, camera frame rate, camera init/stream/exposure;
  - in the tool also version, IMU stream and three read-only queries.

  Neither can send firmware, bootloader or flash commands, and neither touches the boot pin.
  The station reports firmware 2.96, the version PICO OS ships.

## Tracking cameras

The four OV7251s (640×480 mono, global shutter) only exist behind Qualcomm's Spectra camera
stack (CSL), which has no V4L2 capture path.
[`pico4_cam.c`](userspace/pkg/pico4-monado/pico4_cam.c) rebuilds the stock tracking pipeline
from the public CSL headers:

- **Sessions.** One CSL session per camera:
  - the sensor through the CCI driver: probe, power, and the stock OV7251 register table,
    including the stock per-module flip and mirror;
  - its CSIPHY;
  - an IFE-lite with one RDI output (raw data, no ISP firmware).

  PICO's kernel allows four links per session where the public header says two; the build
  patches the header.
- **IFE-lite.** The RDI only produces start-of-frame events once the INIT packet enables the
  CAMIF-lite module with a CDM register write. CamX does the same. The sensor sends RAW10, which
  is stored as `PLAIN16_10`.
- **Requests.** The ISP is alone on its request-manager link: the sensor follows the frame
  sync, not requests. Each camera keeps 8 requests in flight with bubble recovery; with 6, about
  10% of frames were lost. Buffer completion comes through `cam_sync` fences. The timestamp is
  the start-of-frame event's boot time, which the driver converts to Monado's clock.
- **Cached buffers.** Frames land in cached buffers, which are invalidated
  (`CAM_REQ_MGR_CACHE_OPS`) before the CPU reads them. With uncached buffers, reading frames
  stalled buffer delivery for about 100 ms every 10–13 frames. With cached buffers the latency
  is a constant 16–18 ms.
- **Kernel console.** The camera driver logs every start of frame at `KERN_INFO`. On the
  serial console each line costs about 13 ms in interrupt context, and capture collapsed after
  about 20 frames. `50-pico4-console.conf` keeps informational messages off the console.
- **udev.** systemd's `60-persistent-v4l.rules` is overridden. Its `v4l_id` probe opens the ICP
  node at boot, whose firmware load fails. That blocks PICO's CAM-HUB work queue, and every
  later open of `cam_sync` times out.

The station drives the sensors, which run in FSIN slave mode:

- **Start.** Each run resets the station, then sends camera init (`04 01 00 02 00`) and stream
  (`04 02 00 01`). All four sensors must be streaming before the station accepts them.
  Frames arrive at 49.99 Hz.
- **Exposure.** The sensors ignore their own exposure registers in this mode. Exposure and
  gain go through the station's camera command `04 03`: per camera, exposure in sensor lines of
  19.3 µs and gain in 1/16 steps. Exposure plus the 9 ms readout must fit the 20 ms period,
  and 3,000 lines stalled the sensors. The default of about 3 ms leaves rooms nearly black.
- **Alternating exposures.** Every other frame is a short exposure in which only the
  controllers' IR LEDs show (mean 23–28 of 1023). PICO uses those frames for controller
  tracking; the scene frames run at 25 Hz.

Sensor slots and the factory calibration `pvr/mav0/camN` do not share numbering. Covering one
lens at a time gave cam0 (top left as worn) = slot 0, cam1 (top right) = slot 2, cam2 (bottom
right) = slot 1 and cam3 (bottom left) = slot 3. A point seen by both lower cameras
triangulates with a 0 mm ray gap at 15 cm under this mapping, using the images as captured.
Every rotated or swapped variant gives negative depths or gaps of 5–10 cm. The factory model
(omni + radtan, Kalibr `T_BS` to the IMU frame) is refitted to Kannala-Brandt for Basalt
([`pico4_tcam.c`](userspace/pkg/pico4-monado/pico4_tcam.c)), at 0.21–0.52 px RMS.

[`pico4-cam`](src/xr/pico4-cam.c) tests the cameras on their own (`list`, `probe`,
`capture`). It also saves a scene frame of each camera and can log per-frame brightness and
latency.

## 6DoF head tracking

[`pico4_slam.c`](userspace/pkg/pico4-monado/pico4_slam.c) feeds Monado's SLAM tracker. Monado
loads Basalt through its VIT interface (`libbasalt.so` from the
[`pico4-basalt`](userspace/pkg/pico4-basalt/PKGBUILD) package):

- **Frames.** Frames of one sync pulse are grouped by start-of-frame time, and the
  controller-LED frames are skipped. The phase is told apart by brightness and counted through
  dropped frames. Each frame is converted to 8 bits: black level removed, slowly adapting
  digital gain. Sets go out in camera order with one timestamp, as Basalt requires.
- **Auto exposure.** A slow loop sets the station exposure from the first camera's
  brightness. It raises exposure first, up to 400 lines, then gain up to 4×.
- **Upright images.** The lower pair is mounted 180° apart, as the calibration's relative roll
  says. Basalt's stereo matching tracks patches between cameras and cannot follow that, so the
  driver turns each image that is upside down relative to the first camera's. It rotates that
  camera's calibration to match (`pico4_tcam_rotate_180`).
- **IMU.** Raw head IMU samples (512 Hz, halved for Basalt) in the factory body frame, which
  is the frame of the camera extrinsics.
- **Decoupling.** Basalt's queues are bounded and block their producers when its optical flow
  falls behind. Two feeder threads therefore stand between the driver and Basalt. One takes
  the newest frame set (an older waiting set is dropped), the other an IMU ring buffer. Neither
  the cameras nor the head IMU thread (3DoF, controllers) ever waits for Basalt.
  - **Why two feeders.** One feeder for both deadlocked: a blocked frame push held back the
    IMU samples that Basalt's estimator needed before its optical flow could take the next
    frame.
  - **Shutdown.** The driver stops the frames first and gives Basalt a second to work
    through its queue before stopping it. Basalt's optical flow carries on after its
    estimator has finished and then predicts from an invalid state, which aborted or crashed
    the process when it was behind.
- **Priority.** Without a config file, Basalt's TBB pool uses every core. Basalt's threads
  and feeders therefore start at nice 19, restricted to cores 0–5, which leaves the two fastest
  cores to the compositor and the app. They inherit both from the thread that starts them,
  TBB workers included; `PICO4_SLAM_CPUS` (a decimal bit mask) changes the set. With the
  compositor running, Basalt skips some sets (30–70 of 757 in 30 s).
- **Pose.** Basalt's pose (IMU frame, z-up world) is converted to the HMD frame and Monado's
  y-up world. While Basalt reports position, the driver uses it; otherwise the 3DoF fusion.

Options: `PICO4_SLAM=0` disables 6DoF. `PICO4_SLAM_CAMS` (default `0,1,2,3`) selects the
factory cameras, `PICO4_TCAM_SLOTS` the slot mapping. `PICO4_SLAM_UPRIGHT=0` keeps images as
captured. `PICO4_SLAM_MAX_EXPOSURE` and `PICO4_SLAM_BRIGHTNESS` tune exposure, and
`PICO4_SLAM_DUMP=dir` saves converted frames. Monado's `SLAM_WRITE_CSVS=1` writes Basalt's
trajectory.

Basalt is built for `armv8.2-a`, the XR2's Cortex-A77/A55 level. A `-march=native` build under
QEMU emitted SVE instructions. OpenCV's highgui links Qt 6, so `qt6-base` is part of the image.

## Controllers

[`pico4_blemcu.c`](userspace/pkg/pico4-monado/pico4_blemcu.c) parses the `ble_mcu` events:
200-byte payloads, each with a header (`5A`, version, station time in µs, records length) and
records `[length][type][id][time offset][data]`. The id's low bits select left (1) or right (2).
Record types:

- **IMU** (type 0): gyro at 16.4 LSB per °/s, accelerometer at 1,024 LSB per g, temperature,
  and a counter.
- **Inputs** (type 1): trigger, stick y (255 up), stick x (255 right) and grip as bytes. One byte of buttons: home,
  menu, A/X, B/Y, grip, stick click, trigger. One byte of touch: A/X, B/Y, stick, thumb rest,
  trigger.
- **Battery** (type 2).

[`pico4_controller.c`](userspace/pkg/pico4-monado/pico4_controller.c) makes each controller a
Monado device with Monado's existing PICO 4 controller profile
(`/interaction_profiles/bytedance/pico4_controller`). Orientation comes from `m_imu_3dof`,
levelled from gravity at start, with the IMU axes mapped to the grip (`x, -z, y`;
`PICO4_CONTROLLER_AXES_LEFT/RIGHT` override). Positions are a placeholder: a fixed offset
from the head, turned with its yaw. Real positions need the IR-LED tracking. Haptics are
accepted but do nothing yet.

## Build

Extract the inputs from **your own** EDL backup (as root, Linux/WSL). This needs `debugfs` and
`mcopy`. The script verifies each image against the backup manifest first:

```sh
bash scripts/extract-sensor-data.sh backup/stock-20261016 assets/sensor-data-5.13.7
```

This writes the SLPI firmware and protection-domain maps (modem partition), the sensor
configuration (vendor), the registry with its version file, and the factory calibration
(persist), plus a sha256 manifest. Then build the userspace with it (see
[USERSPACE.md](USERSPACE.md#build)):

```sh
bash scripts/build-userspace.sh /var/tmp/pico4-userspace-build "$PWD/out/userspace" \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
```

The `pico4-sensors` package (qrtr-ns, pd-mapper, hexagonrpcd) is built from pinned upstream
commits plus this project's patches. `pico4-basalt` builds Basalt and its submodules at pinned
commits (about an hour under QEMU). `pico4-monado` fetches the Spectra camera UAPI headers
from a pinned public kernel tree. The sensor data also carries the lens and camera calibration
the driver reads at run time. The extracted data and the archive are device-specific
and partly proprietary, so keep them private.

## Run

In the userspace, the sensor units start on boot. Then:

```sh
pico4-imu list accel gyro mag          # sensors and attributes the DSP reports
pico4-imu stream --seconds 10 --check --orientation [--csv imu.csv] [--rate 1000]
```

`--check` fails unless both streams deliver at least half the expected samples, the measured
rate is within 5% of the rate the DSP configured, no timestamp goes backwards, |g| is within
0.5 m/s² of 9.81 and the mean angular rate is below 0.05 rad/s. These limits are for a headset
lying still. With `--userspace` and a sensor-data build, `hardware_test.py flash-run` runs this
check after the userspace check and stores the sensor list, unit status and the IMU JSON.

Through OpenXR (Monado's null compositor; `PICO4_LOG=debug` adds the driver's latency, tilt
and bias every 5000 samples):

```sh
XRT_COMPOSITOR_NULL=1 pico4-openxr-pose 10 10
```

If `pico4-monado` is installed, the runner also runs this for 10 s. It then requires the PICO 4
system with orientation tracking, no invalid pose after the first second, a tilt within 2° of
the accelerometer's (from the IMU check), and a heading drift below 0.5°/s.

Monado runs inside the client, and Basalt prints its diagnostics to stdout, so both acceptance
clients point their stdout at stderr and keep the original stdout for the JSON report alone.

With the cameras and Basalt available, the HMD reports position tracking, and the client adds
`position_tracked`, the path length, the largest distance from the start and the last
position. A still headset should stay within centimetres; that needs a scene the lower cameras
can see in depth, not a surface a few centimetres away.

The cameras and the radio on their own (nothing else may use them at the same time):

```sh
pico4-cam list                                    # CSL nodes, slots, PHYs; powers nothing
pico4-cam capture --frames 1500 --out /tmp/cams   # all four at 50 Hz, JSON summary, one scene frame each
pico4-cam capture --exposure 300 --gain 32 --stats-log /tmp/frames.txt
pico4-station version                             # station firmware version
pico4-station listen --seconds 5                  # frames the station sends
```

`capture` resets the station, starts the frame sync and reports per camera the frames, errors,
frame rate and timestamp monotonicity.

On the panels, through Monado's main compositor and the KMS target. Nothing else may hold the
display:

```sh
PICO4_KMS_STATS=/run/kms.json PICO4_KMS_DUMP=/run/frame.ppm pico4-openxr-display 10 [--projection-only]
```

The runner then runs this for 30 s with both layers, so it exercises the layer squasher, and
requires all of the following:

- **Client.** The PICO 4 system, frames at 95% of 90 Hz or better, and at most 1% of them late.
- **Target.** The frames went through `pico4_kms`, the panels refreshed within 1% of 90 Hz, at
  least 85.5 commits per second, and no retire fence timed out.
- **Eyes.** In the scanned-out image, the left half is red and the right half blue: each eye's
  content is on its own panel.

The runner cannot see the panels themselves. As in step 3, the user has to confirm by eye that
the image appears in the lenses.

## Verification

Headset (PICO 4, PICO OS 5.13.7, stock kernel, step-4 userspace), 2026-10-17:

| Check | Result |
| --- | --- |
| SLPI boot from the backup's firmware | `Brought out of reset`, FastRPC and QRTR channels up, subsystem `ONLINE` |
| Sensors process attached | `hexagonrpcd` on `adsprpc-smd-secure`; QMI service 400 on node 9 |
| Sensors reported | ICM-42688 accel/gyro 12.8–512 Hz, ICM-42688 temperature, AK0991x magnetometer 10–100 Hz |
| Stream, headset still, 5 s | 511.95 Hz each, 0 gaps, 0 non-monotonic timestamps, \|g\| 9.796 m/s², mean \|ω\| 0.006 rad/s |
| Heading drift, still, uncorrected | −0.36° in 5 s |
| Guided 90° turn (edge-aligned) | plateau +90.3 … +90.5° (scale error < 0.6%) |
| Turn back to the start | filter heading +0.35° after 40 s |
| 1 kHz request | DSP configures 1000 Hz, measured 1023.9 Hz |
| Userspace check | `running`, no failed units, storage read-only, USB link unchanged |

The rows above come from development runs. The final archive (SHA-256
`f4bf1814a2433d8e742f1d784b797c7bd648bb61abf675e5a6013e7f63103769`, 386,783,539 bytes) then
passed the bounded acceptance run unattended: `flash-run --userspace` with the engineering ABL,
followed by restoration as in [USERSPACE.md](USERSPACE.md#headset).

| Acceptance (final archive) | Result |
| --- | --- |
| Handoff | received in 94 s, systemd SSH 118 s after the start of the transfer |
| Userspace check | passed: systemd 257 `running`, 0 failed units |
| `pico4-imu stream --check` (10 s) | pass: 5,066 samples per stream at 511.95 Hz, 0 gaps, 0 non-monotonic timestamps; \|g\| 9.797 m/s²; mean ω (−0.0008, −0.0026, −0.0042) rad/s |
| Orientation, still | filter roll/pitch −11.1°/−83.6° vs. accelerometer −11.9°/−83.5°; heading −0.69° in 10 s |
| Restoration | stock boot verified by EDL readback, stock ABL restored by readback, PICO OS 5.13.7 started |
| QEMU (same archive, 4.19 without an SLPI) | self-test passed; the sensor units are skipped by their conditions |

Monado driver, headset still, development builds (2026-10-18):

| Check | Result |
| --- | --- |
| OpenXR system | `Monado: PICO 4`, runtime `Monado(XRT) … v25.1.0`, orientation tracking |
| 60 s at 100 Hz | 5,882 locations, 0 invalid after the first second; rotation 0.009°, heading −0.006° (−0.0001°/s) |
| Tilt vs. accelerometer | 9.12° vs. 8.96–9.11° |
| Latency, sensor sample to pose | 0.94–1.16 ms |
| Learned gyro bias | (0.0039, −0.0009, −0.0056) rad/s in the head frame |
| Before warm-up averaging and bias learning | tilt 1.2–2° below the accelerometer, heading −0.10°/s |

The final archive with `pico4-monado` (SHA-256
`1665a0194959168af8bd29b82dfe0ac3afea9fe40c1a18dc74ed40bf67e9002f`, 388,962,094 bytes) passed the
unattended acceptance cycle on 2026-10-18:

| Acceptance (Monado archive) | Result |
| --- | --- |
| Handoff, userspace check | received in 86 s, SSH after 112 s; systemd `running`, 0 failed units |
| `pico4-imu stream --check` | pass: 5,066 samples per stream at 511.95 Hz, 0 gaps |
| `pico4-openxr-pose 10 10` | pass: `Monado: PICO 4`, 981 locations, 0 invalid after warm-up; tilt 9.08° vs. accelerometer 8.95°; heading −0.0002°/s |
| Restoration | stock boot verified by EDL readback, stock ABL restored, PICO OS 5.13.7 started |
| QEMU (same archive, no SLPI) | self-test passed; Monado's PICO 4 prober and the sensor units stay inactive |

Display output through Monado, headset still, development builds (2026-10-18):

| Check | Result |
| --- | --- |
| Target | `pico4_kms` on connector 29 / CRTC 140, `4320x2160x90` mode, six stock layers; images `B8G8R8A8_SRGB`, linear, pitch 17,408 bytes, imported from ION dma-bufs |
| 60 s, one projection layer (3024² per eye) | client 5,400 frames at 90.000 Hz, 3 late (max interval 19.5 ms); 5,343 commits; panel vblank 89.998 Hz; 0 retire fence timeouts; GPU 7.2 ms per frame |
| Scanned-out image (commit 300 / 2,000) | left half red L, right half blue R; eye means (70, 40, 25) and (26, 41, 72) |
| Projection + world-locked quad | correct image with stereo offset between the eyes, 30 Hz (multi-layer path, see above) |
| GPU temperature after 60 s | 38 °C |

The first archive with display output (SHA-256
`2b507e7c3b6f654e95a82db6b0263bca18c7aa3b95d781d5a6018f02b116cb0f`, 388,934,520 bytes, before the
squasher changes) passed the unattended acceptance cycle on 2026-10-18:

| Acceptance (display archive) | Result |
| --- | --- |
| Handoff, userspace check | received in 89 s, SSH after 114 s; systemd `running`, 0 failed units |
| `pico4-imu stream --check` | pass: 5,066 samples per stream at 511.95 Hz, 0 gaps |
| `pico4-openxr-pose 10 10` | pass: 977 locations, 0 invalid after warm-up; tilt 9.03° vs. accelerometer 8.93°; heading −0.0005°/s |
| `pico4-openxr-display 10 --projection-only` | pass: 901 frames at 90.004 Hz, 0 late; 882 commits (87.5/s including the mode set); panel vblank 89.998 Hz; 0 retire timeouts; GPU 7.1 ms per frame; scanned-out eye means (70, 40, 25) left and (26, 41, 72) right |
| Restoration | stock boot verified by EDL readback, stock ABL restored, PICO OS 5.13.7 started |
| QEMU (same archive, no `msm_drm`, no SLPI) | self-test passed |

The panels were not observed during these runs. The user then looked into the lenses during
two runs of the same archive, in a fresh dev-loop boot:

| Visual check | Measured | Seen in the lenses |
| --- | --- | --- |
| One projection layer, 30 s | 2,700 frames at 90.0 Hz, 2 late; panel 89.998 Hz | steady image: red L only in the left eye, blue R only in the right eye |
| Projection + world-locked quad, 60 s, head turned (first version) | 30.3 commits per second; orientation tracked throughout | the checkerboard stays in place in the room, but moves visibly in steps while the head turns: each frame stays on screen for three refreshes |

That stutter led to the squasher changes above. With them the same two-layer scene ran in a
development build at 90.004 Hz (1 late frame in 10 s, 89.6 commits per second including the
mode set). Before each of these runs, the GPU floor was at the stock level 6 (305 MHz), and it
was restored to level 6 afterwards.

The final archive then ran the two-layer scene again for 90 s, with the user turning their
head:

- **Measured.** Client 8,100 frames at 90.000 Hz, 7 late. 87.0 commits per second: about 3%
  of the refreshes repeated a frame, none for longer than one refresh (longest gap 21.9 ms).
  Panel at 89.998 Hz, no retire timeout, GPU 8.2 ms per frame.
- **Seen in the lenses.** The checkerboard stayed fixed in the room and moved smoothly, with
  good tracking and no visible latency.

The final archive (SHA-256 `01d98f14162d6e0816aa0f9408e223153d00dbdebab966571a4059548347228c`,
388,935,072 bytes) passed the unattended acceptance cycle, now with the two-layer scene:

| Acceptance (final archive) | Result |
| --- | --- |
| Handoff, userspace check | received in 90 s, SSH after 114 s; systemd `running`, 0 failed units |
| `pico4-imu stream --check` | pass: 5,066 samples per stream at 511.95 Hz, 0 gaps |
| `pico4-openxr-pose 10 10` | pass: 978 locations, 0 invalid after warm-up; tilt 4.02° vs. accelerometer 4.06°; heading −0.0005°/s |
| `pico4-openxr-display 10` (projection + quad) | pass: 899 frames at 89.97 Hz, 0 late; 881 commits (87.5/s including the mode set); panel vblank 89.998 Hz; 0 retire timeouts; GPU 7.9 ms per frame; scanned-out eye means (72, 45, 28) left and (29, 45, 72) right |
| Restoration | stock boot verified by EDL readback, stock ABL restored, PICO OS 5.13.7 started |
| QEMU (same archive, no `msm_drm`, no SLPI) | self-test passed |

Lens, IPD, cameras, controllers and the SLAM pipeline, development builds on the headset
(2026-10-18):

| Check | Result |
| --- | --- |
| Field of view from the lens data | left eye −53.32/+53.38° horizontal, +53.58/−53.12° vertical; right eye −53.49/+53.17°, +53.41/−53.25° |
| Lens correction, seen in the lenses | grid lines and checkerboard edges straight; image steady at 90 Hz. Colour fringes not judged (each eye showed its own colour) |
| IPD | 71.99 mm (Hall reading 888, consistent with the factory table); the wheel was at its widest, 72 mm |
| Station | power cycle, announcement and frame-rate answer every run; firmware 2.96 |
| Capture, 30 s, all four cameras | 1,500/1,500 frames each, 0 errors, 49.99 Hz, timestamps monotonic; start of frame to delivery 17.5 ms mean, 27.2 ms max |
| Capture before the cached buffers | 45–67 ms mean; ~100 ms stalls every 10–13 frames |
| Exposure through the station | scene frames mean 76 → 446 (of 1023) at 400 lines and 4× gain; LED frames 23–28 either way |
| Slot mapping | cover test: one slot dark per covered lens; logo triangulation 0 mm ray gap at 0.14/0.16 m |
| Controllers in OpenXR | both as `PICO 4 left/right controller`; per controller about 500 IMU samples and 100 input packets per second |
| Controller inputs, user pressing each in turn | trigger, grip, A/B/X/Y, menu, home, stick click and touch (A/X, B/Y, stick, thumb rest) decoded as pressed |
| Sticks, pushed right, left, up, down on both controllers | byte 2: 255, 0; byte 1: 255, 0. The first parser had x and y swapped |
| SLAM feed, 30 s, four cameras | 744 frame sets at 25 Hz, 0–1 incomplete, 0 dropped while Basalt was busy, 0 IMU samples dropped; frame to tracker 15–23 ms |
| SLAM accuracy, still headset, lower cameras 15 cm from a board | 1.6–4.8 m drift in 30 s: no stereo depth in this scene; not a valid accuracy test |
| 6DoF, headset on a table in a lit room, slid 50 cm (measured) and back, twice | 0.52 m and 0.49 m measured; back at the start within 2–4 cm; vertical within 1 cm on the second slide; at rest within millimetres over 15 s |
| 6DoF, held at head height, walking about 1 m forward/back and sideways | moves tracked in the right axes and back near the start (0.1–0.2 m); still phases within 3 cm. One run showed a 0.8 m jump while Basalt initialised in the first seconds |
| 6DoF, headset lowered from head height onto a table | 0.75 m down, then still within millimetres for 30 s |
| Image turn off (`PICO4_SLAM_UPRIGHT=0`), walking | tracks as well in this test; the turn stays on as the documented geometry |
| Before the frame-phase fix | 430–720 m drift in 30 s (Basalt fed the controller-LED frames) |

The step-5 archive (SHA-256 `f3b843c73903c6bd93b086eb08a5440dc1a6326d6c4593d4666f570430ee5e40`,
464,137,714 bytes) passed the unattended acceptance cycle on 2026-10-19:

| Acceptance (step-5 archive) | Result |
| --- | --- |
| Handoff, userspace check | received in 105 s, SSH after 123 s; systemd `running`, 0 failed units; Turnip verified 65,536 pixels |
| `pico4-imu stream --check` | pass: 5,066 samples per stream at 511.95 Hz, 0 gaps |
| `pico4-openxr-pose 10 10` | pass: 4 cameras + Basalt running, position tracked; tilt 2.01° vs. accelerometer 0.65°; heading 0.11°/s. The still headset faced a near surface again: 6.5 m position drift, not part of the check |
| `pico4-openxr-display 30` (projection + quad, SLAM running) | pass: 2,698 frames at 89.996 Hz, 2 late; 2,691 commits at 88.76/s, panel 89.998 Hz, 0 retire timeouts; scanned-out eye means (60, 32, 19) left and (21, 35, 64) right; Basalt skipped 73 of 757 sets |
| Restoration | stock boot verified by EDL readback, stock ABL restored, PICO OS 5.13.7 started |
| QEMU (same archive, no SLPI, no `msm_drm`) | self-test passed |

An earlier archive failed the same cycle (2026-10-18) on the display check alone: 83.7 commits
per second against the required 85.5. Four causes were found in the dev loop and fixed:

- **Real-time threads refused.** Monado's real-time threads were refused (see the KMS target).
- **Chromatic aberration.** The CA correction's bandwidth; it is now opt-in.
- **Session end.** A stall at session end weighed on a 10 s window; the check now runs 30 s.
- **Basalt.** A deadlock and a crash at Basalt shutdown, which the investigation also turned up.

Unit tests cover the protobuf/QMI encoding (Python and C), a simulated DSP (lookup,
attributes, streaming, disable), the acceptance limits, the orientation filter, the
super-image reader and the FastRPC ABI translation (in the hexagonrpc patch, `meson test`).
The package build also runs the C tests of the lens model and IPD reply parser, and of the
tracking helpers:
- station frames against golden bytes and the allowlist;
- the calibration parser, the Kannala-Brandt refit and the 180° turn;
- the controller packet parser.

## Next steps

1. **6DoF tuning.** Worn sessions with longer paths. Basalt's first seconds can jump before
   it has initialised, so the driver could hold the 3DoF pose until Basalt has settled. Other
   knobs: `optical_flow_image_safe_radius` for the fisheye borders, IMU noise, and a Basalt
   config file (the VIT build runs Basalt's built-in defaults).
   A still headset facing a surface a few centimetres away drifts by metres (no stereo depth);
   the driver could fall back to 3DoF position while Basalt reports too few landmarks.
2. **Controller check in an OpenXR app.** Grip orientation while worn and the bindings in a
   real application (step 6). Haptics need a station command still to be identified; it is not
   part of the allowlist yet.
3. **Monado service and session.** Monado runs in-process for now. As a service it would own
   the display, sensors, cameras and radio for several clients. Its socket lives in the
   client's `XDG_RUNTIME_DIR`, and upstream ships user units that exclude root. Where the
   socket goes depends on the session user that the gamescope/Steam session (roadmap step 6)
   brings.
4. **Controller positions.** The cameras already deliver the IR-LED frames. Constellation
   tracking needs the LED layout of the rings, which is in the controllers' calibration or
   PICO's encrypted tracking config, and a tracker (Monado has one for WMR controllers).
5. **Composition headroom.** The squasher fits 11 ms only with the GPU clock floor. Remaining
   options:
   - compressed (UBWC) scanout images, using the display's compressed formats;
   - compressed client swapchain images;
   - composing the layers directly through the distortion mesh, without scratch images;
   - cheaper chromatic aberration correction (it costs 2–5% of the refreshes in the
     two-layer scene, hence opt-in);
   - display-timing pacing from the retire fences' timestamps.
