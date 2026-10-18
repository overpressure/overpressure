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
> tilt agreed with the accelerometer within 0.2°. Display output through Monado's compositor,
> cameras, controllers and 6DoF are still open; see [next steps](#next-steps).

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
- **Display description.** Two 2160×2160 views side by side (4320×2160), 90 Hz and a
  provisional 104° field of view. There is no distortion yet; the lens data replaces this
  later.

[`pico4-openxr-pose`](src/xr/pico4-openxr-pose.c) is the acceptance client. It uses the
OpenXR loader with `XR_MND_headless`, so it needs no graphics. It locates VIEW at the current
time every 10 ms and reports the system name, tracked/valid counts, rotation, heading drift
rate and the tilt from vertical. The tilt is measured in STAGE because LOCAL keeps only the
start yaw of a tilted head.

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
commits plus this project's patches. The extracted data and the archive are device-specific
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

Unit tests cover the protobuf/QMI encoding (Python and C), a simulated DSP (lookup,
attributes, streaming, disable), the acceptance limits, the orientation filter, the
super-image reader and the FastRPC ABI translation (in the hexagonrpc patch, `meson test`).

## Next steps

1. **Monado display output.** Present Monado's compositor on the two panels through the
   step-3 KMS path (fenced flips, `sspp_layout` eye routing), then run it as a service.
2. **Tracking cameras.** Capture raw OV7251 frames through Spectra CSL (sensor I2C, CSIPHY,
   CSID, IFE-lite RDI), with the `sixdof` frame-sync timestamps.
3. **6DoF.** Run Monado's SLAM (Basalt) on camera + IMU with the factory Kalibr calibration.
4. **Controllers.** Implement the station SPI protocol (pairing, controller IMU, buttons;
   never the OTA commands), then IR-LED constellation tracking.
5. **Lens calibration.** Convert `OpticalCalibrationData.json` and the lens/IPD tables into
   distortion meshes for the compositor.
