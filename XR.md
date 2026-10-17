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
> Cameras, controllers, 6DoF and Monado integration are still open; see [next steps](#next-steps).

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

Unit tests cover the protobuf/QMI encoding, a simulated DSP (lookup, attributes, streaming,
disable), the acceptance limits, the orientation filter, the super-image reader and the
FastRPC ABI translation (in the hexagonrpc patch, `meson test`).

## Next steps

1. **Monado 3DoF driver.** Feed the IMU into Monado as the HMD's pose source, with bias
   estimation and timestamps on the QTimer/`CLOCK_MONOTONIC` mapping. Present through the
   step-3 display path.
2. **Tracking cameras.** Capture raw OV7251 frames through Spectra CSL (sensor I2C, CSIPHY,
   CSID, IFE-lite RDI), with the `sixdof` frame-sync timestamps.
3. **6DoF.** Run Monado's SLAM (Basalt) on camera + IMU with the factory Kalibr calibration.
4. **Controllers.** Implement the station SPI protocol (pairing, controller IMU, buttons;
   never the OTA commands), then IR-LED constellation tracking.
5. **Lens calibration.** Convert `OpticalCalibrationData.json` and the lens/IPD tables into
   distortion meshes for the compositor.
