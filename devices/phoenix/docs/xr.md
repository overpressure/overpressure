# Native XR hardware (roadmap step 5)

The PICO 4's XR hardware under Linux, on Valve's kernel ([kernel.md](kernel.md)). The inventory comes
from the stock image, kernel and device tree and from read-only observation of PICO OS; no vendor
files are part of this repository.

**TL;DR.** Monado's `pico4` driver runs inside SteamVR (`driver_monado`) and, for OpenXR tests, in
`monado-service` ([session.md](session.md#monado-as-a-service)). Each part runs on the headset;
the measurements are under [Verified results](#verified-results).

| Part | How it works | Code |
| --- | --- | --- |
| Head IMU | ICM-42688 on the sensor DSP (SLPI), read over QMI/QRTR; 1 kHz | `sm8250-hexagonrpc` (hexagonrpcd), [pico4_ssc.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_ssc.c), [`pico4-imu`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-xr/pico4-imu) |
| Head 6DoF | PICO OS's `libSlam.so` on the Compute DSP, in a bionic root, fed by `pico4-slamd`; Basalt selectable | [src/stockslam/](../src/stockslam), [pico4_slam.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_slam.c) |
| Display, lenses, IPD | 2 × 2160×2160 at 90 Hz; PICO OS's lens model; IPD from the lens motor's MCU, set by `pico4-lens` | [pico4_lens.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_lens.c), [pico4_ipd.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_ipd.c) |
| Cameras | 4 × OV7251 and the IMX471 RGB camera on V4L2/CAMSS, frame sync from the station | [pico4_cam.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_cam.c), [`pico4-cam`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-cam.c) |
| Controllers | station (nRF52832) on SPI for control, inputs and IMU through the sensor DSP; LED tracking reimplemented from PICO OS; PICO OS's render models; vibration | [pico4_station.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_station.c), [pico4_controller.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_controller.c), `pico4_ctrack_*` |
| Room view | colour from the RGB camera on PICO OS's depth mesh, through SteamVR's private passthrough interface | [pico4_passthrough.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_passthrough.c), [`pico4-roomview`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-roomview.c), [vst.c](../src/stockslam/vst.c) |
| Rooms | the tracker's maps kept on the data partition; each room is a SteamVR universe | [room.h](../src/stockslam/room.h), [pico4-slamd.c](../src/stockslam/pico4-slamd.c) |

Packages: the driver in [pico4-monado](https://github.com/overpressure/packages/tree/main/phoenix/pico4-monado) and its Monado recipe and patches in
[pico4-monado](https://github.com/overpressure/packages/tree/main/phoenix/pico4-monado); the sensor daemons in
[sm8250-hexagonrpc](https://github.com/overpressure/packages/tree/main/sm8250/sm8250-hexagonrpc); OpenXR test clients and tools in [pico4-monado's tools](https://github.com/overpressure/packages/tree/main/phoenix/pico4-monado).

> **Rules for this hardware**
>
> - **DSP recovery.** [`remoteproc-boot`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/remoteproc-boot)
>   starts a DSP only with remoteproc's crash recovery on: a crash then restarts only that DSP; with
>   recovery off it would stay down.
> - **No firmware commands.** The station link (`/dev/spidev0.0`) also carries station and
>   controller firmware updates (OTA), and the GD32 MCU (`/dev/ttyHS1`) also takes motor moves,
>   calibration writes and firmware. The station modules, the IPD reader, `pico4-lens` and `pico4-fan`
>   ([cooling.md](cooling.md)) build frames only from allowlists; keep it that way.
> - **One owner for cameras and station.** The OV7251s stream only while the station pulses
>   FSIN, so every capture resets the station first. Stop `pico4-monado.service` (or Valve's
>   session) before `pico4-cam` or `pico4-station`. The driver and `pico4-station` hold an exclusive
>   `flock` on `/dev/spidev0.0`; a second owner is refused before it can reset the station, and a
>   second runtime runs without controllers and cameras.

## Inventory

The selected board overlay is `kona MTP` (dtbo index 7).

| Function | Hardware | On PICO OS (kernel 4.19) | Used on PICO OS by |
| --- | --- | --- | --- |
| Head IMU | ICM-42688 (`icm4x6xx`: accel, gyro, temperature) and AK0991x magnetometer, on the SLPI | Qualcomm Sensor Core (SSC), QMI over QRTR | `sscrpcd`, `sensors.qti`, `pvrtrackingservice` |
| Tracking cameras | 4 × OV7251 (640×480 mono global shutter) | Spectra CSL (`/dev/video0-3`, no plain V4L2 capture) | CamX + `quickcamera` HAL, `pvrtrackingservice`, `pxrseethroughservice` |
| Other cameras | IMX471 RGB; OV680 bridge; eye-tracking nodes (Pro only) | same Spectra stack | see-through, MR services |
| Controllers | nRF52832 radio ("station"), on SPI | `/dev/spidev0.0`; `/dev/stationdev` (power, boot GPIO, sync IRQ) | `stationservice` (`libstationopticsservice.so`) |
| Controller pose | 20 IR LEDs per controller, seen by the tracking cameras | cameras above | `pvrtrackingservice`, `libpico_controller_tracking` |
| IPD motor | GD32 MCU, also backlight and fan ([cooling.md](cooling.md)) | `/dev/ttyHS1` | `gd32ipdservice` |
| Lens/display | Sharp LS026B3SA ×2 | `msm_drm` ([graphics.md](graphics.md)) | `pxrhmdservice` |

On this headset the DSP reports only the ICM-42688 and AK0991x, although the SSC registry also
configures other boards' sensors.

## Factory calibration

`persist` holds per-unit calibration:

- `pvr/mav0/cam0..cam4/sensor.yaml`: EuRoC/Kalibr camera models (omni ξ + radtan, extrinsics
  `T_BS` to the body frame); `pvr/mav0/imu0/sensor.yaml`, `pvr/gyroBias.txt` (temperature table);
  the SSC registry's gyro and accelerometer calibration, which the DSP applies itself.
- `pvr/OpticalCalibrationData.json`, `pvr/lens/*`, `ipdcalidata.txt`: display, lens and IPD data.
- `pvr/<serial>.txt`: each paired controller's gyro bias table.

The image does not carry it: `persist` is mounted read-only at `/mnt/vendor/persist` at boot, and
`/usr/share/qcom/pico4/calibration/pvr` and the sensor registry link there
([userspace.md](userspace.md#the-headsets-own-data)).

## Head IMU

The IMU hangs off the SLPI, a Hexagon DSP running Qualcomm's Sensor Core:

1. **Firmware.** [`remoteproc-boot`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/remoteproc-boot) starts
   `slpi.mdt` (from the modem partition of PICO's public package) through remoteproc. The kernel
   provides QRTR's name service and the PD mapper.
2. **File server.** `hexagonrpcd` (linux-msm/hexagonrpc, [`sm8250-hexagonrpc`](https://github.com/overpressure/packages/blob/main/sm8250/sm8250-hexagonrpc/PKGBUILD))
   attaches to the sensors process (`sensorspd`) and serves it the sensor configuration and this
   headset's registry over FastRPC. This project's patches: it serves `sns_reg_version`; `-P NAME`
   starts a static process (the ADSP's audio process, [userspace.md](userspace.md#audio)); it answers
   `ftell`; and it takes the DSP's registry rewrites (`temp.json`, renamed over each group) and drops
   them, so `persist` stays read-only and the factory calibration stays in effect.
3. **Client.** `pico4-imu` is a dependency-free Python client of the SNS client service (QMI
   service 0x190, protobuf inside QMI). The driver's [pico4_ssc.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_ssc.c) is a C
   port of it; its unit test checks the encoder against the Python client's messages byte for byte.

Behaviour of the DSP:

- **Persistent listener.** If a FastRPC listener dies, the sensors process keeps it registered: a new
  `hexagonrpcd` cannot attach until the SLPI restarts.
- **Rate selection.** The SSC runs a sensor at the next supported rate at or above the request:
  500 Hz runs the ICM-42688 at 512 Hz, 512 or 1000 Hz at 1024 Hz.
- **Timestamps.** QTimer ticks (19.2 MHz), the same counter as the AP's arch timer.

| Unit (all in `pico4-sensors.target`) | Runs |
| --- | --- |
| `pico4-slpi.service` | `remoteproc-boot slpi` |
| `pico4-sensorspd.service` | `hexagonrpcd` on a runtime copy of `/usr/share/qcom/pico4` with the stock kernel's socinfo names; restarted whenever it ends, because it ends successfully when the DSP restarts |
| `pico4-imu-ready.service` | `pico4-imu wait --timeout 120`: until the sensor service lists the accelerometer and gyroscope |

`pico4-session.service` and `pico4-monado.service` start after the target: a driver that probes
before the IMU is listed falls back to Monado's simulated headset, whose display SteamVR cannot
find. The units start only where `/sys/class/remoteproc` and the sensor data exist (not in QEMU).

**DSP restarts.** An SLPI restart (or a second without IMU samples) makes the driver report the head
pose untracked, orientation still valid, while its IMU thread reconnects in short steps (a service
stop never waits for the DSP). The station and cameras stay as they are; the stream returns once
the restarted DSP lists the IMU again.

## Monado driver

`pico4-monado` builds Monado 25.1.0 (pinned by commit) with only the `pico4` driver and the simulated
HMD. [monado-pico4-driver.patch](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/monado-pico4-driver.patch)
registers the driver and drops the storage usage from scratch images unless the compute path runs.
The package makes Monado the active OpenXR runtime as a service (`monado-service` owns the hardware;
clients are its IPC clients). The HMD ([pico4_hmd.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_hmd.c)):

- **Probing.** Only where a remoteproc named `slpi` exists and the SNS service answers; sensor
  lookups are retried (five attempts of 3 s). Elsewhere Monado runs without the driver.
- **Time base.** The arch counter is the QTimer (`cntfrq_el0` = 19.2 MHz): the driver reads
  `cntvct_el0` between two `CLOCK_MONOTONIC` readings (narrowest of 16 pairs) and maps samples into
  Monado's timeline, again every second ([Frame times](#frame-times)).
- **Axes.** The IMU's +X is up, −Z forward, −Y right (from the factory camera extrinsics). Samples go
  into the OpenXR head frame (x right, y up, z back) as (−y, x, z).
- **3DoF fusion.** 1000 Hz (`PICO4_IMU_RATE`) into Monado's `m_imu_3dof` with gravity correction. It
  skips the first 100 accelerometer samples (about 2° off), starts level from the mean of the next
  100, and learns the gyro bias over quiet 256-sample windows (std below 0.01 rad/s and 0.05 m/s²,
  mean rate below 0.02 rad/s, |g| within 0.3 m/s²; later windows weigh 0.2).
- **Station.** The HMD owns the controller radio: it power-cycles it once at start (the
  controllers reconnect, the camera sync starts). Without the station there are no controllers and
  no 6DoF.
- **Display.** Two 2160×2160 views side by side at 90 Hz, opaque; field of view and distortion from
  the lens data, else a symmetric 104° without distortion.
- **Pose.** While the head tracker tracks, the 6DoF pose ([Head tracking](#head-tracking)); otherwise
  the 3DoF orientation.
- **Prediction.** SteamVR asks for the present pose and extrapolates by the HMD's
  `SecondsFromVsyncToPhotons` (11 ms). The driver hands out the pose `PICO4_PREDICT_MS` (12 ms) plus
  the display's pacing lead later (the lead `pico4-swapchain-usage` writes to
  `$XDG_RUNTIME_DIR/pico4-display-lead-us`, 5 ms): a pose handed out just before the compositor
  takes one reaches the vblank 12 ms later. The latency probe (`PICO4_LATENCY_PROBE_MS`, headset
  lying still) turns the handed-out pose by 5° at known times, to read the delay off the scanned-out
  image. A 10 ms error is 2° at 200°/s.

[`pico4-openxr-pose`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-openxr-pose.c) `[SECONDS] [INTERVAL_MS]` (defaults 5 and 10) is
the pose acceptance client: headless (`XR_MND_headless`), it locates VIEW and prints JSON (tracked
counts, rotation, heading rate, tilt in STAGE, and with position tracking the path length and
largest distance). Exit 0 means every location after the first second had a valid orientation.

## Lens distortion and IPD

The lens model is the stock compositor's (`libpxrcompositor.so`), like the Oculus mobile SDK's:

- **Distortion.** A panel point `p` metres from the lens centre is seen at tan-angle `t·s(|t|²)`,
  `t = p / F`, `s` a polynomial K0…K10. `F` and `A0`…`A10` come per eye from `distortion_parameter`
  in `pvr/OpticalCalibrationData.json` (on this unit the stock table entry for its lens model and
  mould cavity).
- **Lens centre.** The panel centre plus `lens_center_gos` (below 0.1 mm here). The panels move with
  the lenses, so the IPD does not change the distortion.
- **Chromatic aberration.** Red and blue scale the green tan-angle by `1 + CA0 + CA1·r² + CA4·r` and
  `1 + CA2 + CA3·r² + CA5·r`, from the stock `SDK_Lens_ConfigFile.txt`. Off by default as on PICO OS
  (`sdk_chromaticStaus = 0`); `PICO4_LENS_CA=1` turns it on, and then the two-layer test scene drops
  to 83–87 commits per second (87–88 without).

[pico4_lens.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_lens.c) feeds Monado's distortion mesh (one UV per channel). The
polynomial is fitted only inside the panel's inscribed circle (2% beyond allowed); outside it the
driver evaluates it at the circle and lets the tan-angle grow steeply, so the image ends cleanly.
The field of view, from the panel edges (2160 px at 21.3 µm) through the green channel, is about
±53.3° per eye.

**IPD read.** The GD32 MCU on `/dev/ttyHS1` (115200 8N1, raw). [pico4_ipd.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_ipd.c)
sends `A0 08 00 00 00 A8` (get position; the last byte is the XOR of the others) and waits up to
200 ms for `79 a_lo a_hi b_lo b_hi xor`: the lens separation in 0.01 mm (57.00–73.00 mm) and the Hall
reading, told apart by range. `pico4-fan` shares the port: both hold an exclusive `flock` per
transaction, and the IPD read gives up after a second. The driver re-reads every second; after a move
SteamVR gets the new eye positions and `Prop_UserIpdMeters_Float`. Without an answer it uses Monado's
default.

**IPD set.** The motor moves the lenses (no wheel). [`pico4-lens`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-xr/pico4-lens)
`set MM` does what `gd32ipdservice`'s `SetIPDPosition` does: `A0 22 00 00 00 82` (no timestamp
reports), `A0 07 00 lo hi xor` (the target in 0.01 mm, 62.00–72.00), then `A0 11 00 00 00 B1` every
50 ms until the reply `79 state xor` says 0 (1 while moving, 2 at the near end). 0.9 mm take 0.17 s
and end within 0.05 mm. The range is the PICO 4's specified 62–72 mm; PICO OS allows 58–72 mm with a
risk warning below 62, and the motor stops by itself at 59.4–59.9 mm.
[`pico4-lens-panel`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-lens-panel.cpp) adds a SteamVR dashboard control for it: a
slider, ±0.5 mm buttons and four presets (press moves, hold 1 s stores, in `~/.config/pico4-lens/presets`).

## Controller radio ("station")

The nRF52832 on `/dev/spidev0.0` relays the controllers and generates the tracking cameras' frame
sync (FSIN). On Valve's kernel it is a plain spidev: every transfer is full duplex, commands go out
as transfers and zeros poll for the next frame every 2 ms. The station repeats its last frame until
it has another, so only a frame that differs from the last is new.

- **Frames.** 40 bytes: header (`AA` to the station, `BB` replies, `A5` notifications), target (`01`
  station, `02` left, `03` right controller) or notification type, command, payload (bytes 37–38 a
  sequence number), CRC-8 (Dallas/Maxim, reflected `0x8C`, initial 0) over bytes 0–38.
- **Acknowledgements.** Every station frame except stream notifications (`A5 01`) is acknowledged
  with `FA` carrying its bytes 37–38.
- **Data path.** Controller IMU, buttons, sticks, touch and battery go from the station to the
  sensor DSP, which exposes them as the SSC sensor `ble_mcu`. SPI is only the control plane.
- **Bring-up.** The `station-power` regulator's sysfs state power-cycles it (off, 100 ms, on). It
  announces itself (`A5 06`) within 3 s and waits for the camera frame rate (`19`, `01 00 01`: 50 Hz,
  device type 0) before it accepts camera commands. The controllers reconnect on their own.
- **Sleep.** PICO OS has no command that wakes a controller. Its `goToSleep` sends `0D 00 00`
  (controllers to slow advertising) and cuts the station's power; `wakeUp` powers it on. The driver
  does the same when it closes (`pico4_station_sleep`), as does `pico4-station power off`. Awake
  controllers lying still reconnect without a press after at least 15 minutes off; after hours
  they need a button press.
- **Camera sync.** After a timeout the station refuses (`EIO`) every further start, so the tracker
  resets the station as the driver's start does, up to three times.
- **Firmware.** 2.96, the version PICO OS ships.

The C module and the `pico4-station` tool build frames only from this allowlist:

| Command | Payload | Purpose | C module | `pico4-station` |
| --- | --- | --- | --- | --- |
| `FA` | bytes 37–38 of the acknowledged frame | acknowledge | yes | yes |
| `0D` | exactly `00 00` (other first bytes are trackers) | controllers to slow advertising | yes | yes |
| `19` | `01` (50 Hz) or `02`, device type, `01` | answer to the announcement | yes | yes |
| `04` | `01 00 02 00` (exposure-first-row flag 2, resolution 0) | camera init | yes | yes |
| `04` | `02`, camera (0 all, 1–4), `01` on / `02` off | camera stream | yes | yes |
| `04` | 28 bytes: `03`, 8 × (u16 exposure, u8 gain) in bytes 1–12 and 16–27, 52 (u16) and an event index in 13–15 | camera exposure and gain | yes | yes |
| `04` | `07 00`, rate | camera frame rate | – | yes |
| `04` | `05 01`, then `05 02` | the camera modules' ids from their OTP | yes | – |
| `01` | none | firmware version | – | yes |
| `07` | controller, `01` on / `00` off | controllers' IMU stream to the DSP | – | yes |
| `22` | exactly `01 00` (a read; 0 would set the tracker work mode) | work mode | – | yes |
| `29`, `30` | none | crash info, whitelist (read-only) | – | yes |
| `02`, `08`, `03` | exactly one byte, `01` left or `02` right | enter pairing, stop pairing, unpair ([Pairing](#pairing)) | – | yes |
| `01` to `02`/`03` | play (`01`, or zeros to stop), amplitude, u16 ms | vibration | yes | – |
| `02` to `02`/`03` | exactly one byte, chunk 1–12 | the controller's calibration record (read-only) | yes | yes |
| `03` to `02`/`03` | none | controller info (read-only; these controllers do not answer) | – | yes |

Neither sends firmware, bootloader or flash commands or touches the boot pin. The C module's unit
test checks its frames against golden bytes from the Python tool and that every other command is
refused.

**Connections and calibration.**

- `A5 03`, hand in byte 2 (1 left, 2 right): disconnected.
- `A5 02`, hand in byte 2, repeated a few times: connected. Bytes 3–8 software version, byte 9 status
  (low four bits: 1 connected, 2 invalid), 10–11 hardware, 13–29 the serial (naming its
  `pvr/<serial>.txt`), 30–35 the radio address.
- The calibration record: 12 chunks of 32 bytes. The reply `BB`, target, `02` echoes the index in
  byte 3 (`FF`: not ready, retry after 50 ms; `EE`: error), data in bytes 4–35. Chunks 1–3: IMU
  calibration (gyro and accelerometer 3 × 3 matrix row-major, then bias; 24 floats); 4–11: the 20 LED
  positions in metres; 12: `imu_IR` (seven floats; valid when its fourth is 1 and the first IMU
  float is not 0). Late replies for another chunk are skipped.

### Pairing

Pairing lives in the station and the controllers: controllers paired under PICO OS connect as
they are. A replacement controller, one paired to another headset, or one unpaired from its own
buttons (Home and Trigger for 4 s; `A5 05`) needs pairing. `pico4-station pair left|right` does what
PICO OS's `EnterPairing`/`StopPairing` do:

1. `02` with the mask (`01` left, `02` right); `BB 01 02` with byte 4 `01` means it listens.
2. The owner holds Trigger and Home for 6 s; the station reports `A5 02`, status 1.
3. `08` with the mask ends the pairing, also after a connect or the timeout (60 s by default).

`pico4-station unpair left|right` sends `03` with the mask (`BB 01 03`, byte 4 `01`: done). The
driver reads a newly paired controller's calibration on connect; there is no file per controller.

With the session stopped `pico4-station` talks to the station itself. While a session runs,
`pair`, `unpair` and `status` go to the driver's pairing socket, `$XDG_RUNTIME_DIR/pico4-station`
(session user and root only), served by the station thread between reads (`pico4_station.h`): one
request per connection (`pair left|right [SECONDS]`, `unpair left|right`, `status`). A pairing answers
`pairing HAND` when the station listens, then `paired HAND SERIAL` or `timeout HAND`; a client that
hangs up stops it. The onboarding ([session.md](session.md#onboarding)) pairs through it.

## Tracking cameras

The four OV7251s (640×480 mono, global shutter, RAW10) run on V4L2 through CAMSS
([pico4_cam.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_cam.c), [kernel.md](kernel.md#results)): per slot CSIPHY, CSID,
VFE RDI at 640×481 Y10, multi-planar buffers in a ring, Y10P unpacked to 16 bits. A frame's time is
the CSID's hardware start of frame from its buffer. The sensors' control port belongs to the
station, which configures them over its own I2C:

- **Metadata line.** One embedded line precedes the 480 scene rows, so the capture is 640×481 as on
  PICO OS (with 480 lines the bottom scene row is lost). Its 10-bit samples carry gain (word 4),
  exposure (words 6, 7) and the exposure phase (word 82: 1 scene, 2 controller LEDs), decoded by
  `pico4_cam_meta_decode`. Scene and LED exposures alternate.
- **Sets of one pulse.** The sensors centre their exposures on the sync pulse, so frames are grouped
  by start of frame less half the exposure (lines of 19.07 µs), within 4 ms. Grouped by start of
  frame alone, per-camera exposures break the sets.
- **Stalls.** When no scene set completes for a second while frames arrive, the driver logs each
  camera's state. On a stuck camera, or 3 s without a set, or 1 s without any frame, the camera thread
  restarts the capture (sync off, sessions reopened, sync on, exposure set; at most once in 5 s); the
  station takes the sync start only after a second stop. Frames return within 1–2 s.
  `PICO4_CAPTURE_RESTART_TEST_S=N` restarts every N s (test only). PICO OS restarts its cameras the
  same way on finer triggers (a frame over 500 ms old, under 80% of the expected frames in 2 s).

[`pico4-cam`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-cam.c) `capture` tests the cameras alone: it resets the station, streams
until each camera delivered N frames, saves a scene frame per camera (8-bit PGM and 16-bit raw) and
prints per-camera JSON (frames, rate, monotonicity, statistics, exposure phases). `--stats-log FILE`
logs every frame; `--exposure LINES --gain G` sets the station exposure; `--record-leds`,
`--record-scene` and `--record-imu` record LED frames, scene frames and the controllers' radio packets
for replays. `pico4-cam rgb` tests the IMX471.

## Passthrough

SteamVR shows the forward cameras as its room view (the dashboard's camera button). On the Frame,
the HMD driver (`driver_cv`) implements OpenVR's camera component and Valve's XR service captures
the cameras, computes a depth mesh on the CVP and feeds the compositor (`RoomView3D`; without a mesh,
a fixed sphere, `RoomView2D`). The Steam client's VR sign-in does not use the cameras.
`steamvr-v4l2cam` (a virtual webcam) and `steamvr-v4l2loopback` stay masked.

On the PICO 4 `driver_monado`'s HMD provides the camera component
([monado-steamvr-camera.patch](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/monado-steamvr-camera.patch)) from
the frames the driver already captures in `vrserver`, so cameras and station keep one owner:

- **Cameras.** The lower pair looks forward, about 40° down, 11 cm apart: cam3 (left), cam2 (right).
  [pico4_passthrough.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_passthrough.c) turns each image upright
  (`pico4_tcam_upright`, 480×640), with the KB4 fit of the factory model (OpenVR's FTheta) and the
  camera-to-head transforms turned alike.
- **Component.** NV12, left above right (480×1280, `Stereo|VerticalLayout`), 25 Hz, with exposure
  time and head pose; properties `HasCamera`, `HasCameraComponent`, FTheta k1–k4, neutral white
  balance. Settings as the Frame's: `camera.enableCamera`, `roomView` 2,
  `contentAwareRoomviewMonochromeTint`, `dashboard.allowPassthroughPortalManager` off.
- **Why a separate producer.** In SteamVR 2.17 `vrcompositor` starts a camera only when the shared
  state `VR_CameraPassthroughState` marks one enabled, and only the private
  `IVRCameraPassthroughInternal_001` in `vrclient.so` writes it. `vrserver` has no implementation,
  so the producer is a client process: `pico4-roomview` (below).
- **Depth-mesh queues.** The room view connects to the block queues
  `/xrservice/roomview3d/meshData` and `/xrservice/roomview3d/meshLayout` (`IVRBlockQueue_005`); only
  code inside `vrserver` may create them, so the camera component does, when the HMD activates. With
  `Prop_SupportsRoomViewDepthProjection_Bool` (2079) the compositor projects the cameras onto the
  mesh; with the property and no mesh it draws black, so the HMD reports it only where the image
  carries PICO OS's pipeline. Without it each eye sees its own camera and close objects double.
  The values are `IVRPaths_001` path properties stored in the block header (512 bytes; 16 refuses
  them with `OutOfMemory`):
  - meshData queue: bool `/meshData/meshIsUpToDate` turns the mesh on;
  - meshLayout block: u64 `/meshLayout/layoutId`, int32 `vertexCount`, `indexCount`; uint32 triangle
    indices (the compositor swaps each triangle's last two);
  - meshData block: u64 `/meshData/meshId`, `layoutId`, int32 `elementCount` (three per vertex),
    `rawFromMeshPose` (`HmdMatrix34`), double `posePredictionBaseTime`; float x, y, z vertices.

  A geometric stand-in (a sphere around the head) is worse than none when worn: the mesh has to be
  the measured room.
- **PICO OS's see-through.** `pxrseethroughservice` feeds `libphotoid.so` each grey forward pair with
  the tracker's pose and map points. Its pipeline (`vst_releaseA`) computes depth with a neural
  network (`FLNet_lite_v2`, SNPE on the Compute DSP); it sets up a CVP depth-from-stereo session
  but never runs it. It fills gaps from the map points and builds a hemisphere mesh around the head
  that ends at the ground, which `libSlam` reports with ground detection on
  (`setAlgorithmParameters("plane", "1")`). The guardian service colours it from the RGB camera.
- **The same mesh here.** `pico4-slamd` runs `libphotoid` in the tracker's root
  ([vst.c](../src/stockslam/vst.c)): cam3 and cam2 after each scene set, `libSlam`'s pose nearest the
  set's time and its map points. The stand-ins ([stubs.c](../src/stockslam/stubs.c)) answer the CVP
  session, so neither `/dev/cvp` nor CVP firmware is needed (a call of `cvpDfs_Sync` would fail
  loudly). SNPE's DSP module lies in `/system/lib64`, the path SNPE gives the DSP. Meshes go into the
  feed's two mesh slots ([pico4_slam_feed.h](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_slam_feed.h)).
- **Axes trap.** `libphotoid` takes the head and map points, and returns the mesh, in `libSlam`'s
  world turned a quarter turn about z, (−y, x, z); `pico4-slamd` turns both inputs (fed unchanged,
  a head turned 39° right put the hemisphere 39° below the horizon). The mesh's floor lies along −y;
  triangles face the head.
- **Publishing.** While the room view asks for frames, `driver_monado` asks the driver for each new
  mesh; `pico4-slamd` feeds the pipeline only within a second of the driver's stamp, so it idles
  otherwise. Vertices come in the head pose's world (with a saved room's turn and shift) with their
  place in SteamVR's raw space.
  [monado-steamvr-roomview-mesh.patch](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/monado-steamvr-roomview-mesh.patch)
  publishes each mesh once, as the Frame's XR service does: the layout block when it changes, the data
  block (`posePredictionBaseTime` = mid-exposure on `CLOCK_MONOTONIC_RAW`), then `meshIsUpToDate`;
  both queues use 512-byte headers, 4 data and 2 layout blocks. `IVRServerInternal` slots 41 (plane
  anchors) and 42 (tracking world jumps) are not needed.

The producer [`pico4-roomview`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-roomview.c) is a user unit that runs with SteamVR
([pico4-roomview.service](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-roomview.service)):

```text
cameras ─▶ driver_monado (vrserver) ─▶ /dev/shm/pico4-roomview ─▶ pico4-roomview ─▶ vrcompositor
           converted frame, the         (pico4_roomview.h)        dma-bufs and        room view
           head pose at exposure                                  per-frame metadata
```

- **Shared memory.** [pico4_roomview.h](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_roomview.h) (version 2): camera size, KB4
  intrinsics, camera-to-head transforms, a ring of four frames, format (two grey cameras or one NV12
  image). The driver fills it only while the producer's heartbeat says the compositor wants frames,
  with the head pose at mid-exposure in the space it reports to SteamVR (tracking origin included)
  and without prediction (with it the room trailed the head's turns).
- **Client.** `VR_Init` as a background application, then `IVRCompositor_029` (only asking for a
  compositor interface opens `vrclient`'s connection that the frame import uses), the passthrough
  interface, `SetGraphicsDevice` with its own Vulkan device on Turnip (external semaphore
  extensions enabled: `vrclient` imports an OPAQUE_FD timeline semaphore), and `SetupCameraFrames`
  with three linear images per camera exported as dma-bufs.
- **Each frame.** The newest ring frame into a free image, then its metadata and `PublishFrame`. The
  metadata the shaders read: exposure time on `CLOCK_MONOTONIC_RAW` (a time after the compositor's
  now shows nothing), fx, fy, cx, cy, k1–k4, the camera pose in raw tracking space at the exposure
  (+0x30), the camera in the head frame (+0x60, the compositor picks the camera nearest each eye),
  a head-frame calibration (+0x90, identity) and the valid rectangle (0, 0, 1, 1; zero samples one
  texel). Camera poses use computer-vision axes (x right, y down, +z forward): with OpenVR's camera
  axes every point leaves the image. The compositor reprojects with inverse(+0x30) × the point in
  raw space; putting the head at +0x90 instead warped the view on every head motion.
- **Restarts.** A new compositor process gets the frames set up again; without `vrserver` the
  producer exits and the unit starts again with SteamVR.
- **Colour** (the default; `PICO4_ROOMVIEW_RGB=0` keeps the grey pair). As on PICO OS, the grey pair
  builds the mesh and the IMX471 colours it:
  - The IMX471 streams as capture slot 4 through `pico4-imx471` ([kernel.md](kernel.md)): 2328×1748
    RAW10 RGGB (2×2 binned), free-running at 50 fps on four lanes of CSIPHY 1 (2064.8 Mbit/s), CSID 4
    and the third IFE-lite. Exposure and gain are V4L2 controls.
  - A driver thread pinned to the A77 cores 4–6 converts each frame (9 ms): 2×2 cells to 1164×874,
    black level 64, grey-world white balance, sRGB, full-range BT.601 NV12. Auto exposure aims at
    18% grey: 1/100 s (flicker-free), then gain up to 16×, then up to 20 ms.
  - The sensor streams only while the room view shows it: 5 s after it closes the driver pauses it
    (`pico4-imx471`'s paused control).
  - The lens is PICO's omni model with "fisheye62" distortion; [pico4_tcam.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_tcam.c)
    projects it and fits KB4 for the metadata.
  - `pico4-roomview` publishes the NV12 frame (one linear dma-buf) to both camera slots.
    `--test-pattern` shows colour bars.

## Head tracking

The head is tracked by PICO OS's own tracker, `libSlam.so` (`PICO4_SLAM_BACKEND=stock`, the default):
a multi-camera visual-inertial tracker whose front end runs on the Compute DSP (CDSP). In Valve's
idle session `pico4-slamd` takes about 70% of one core and `vrserver` about 120% (with Basalt
350–390%). [Basalt](#basalt) stays selectable as it is. Without the tracker's data in the image, or
without the station, the HMD stays on 3DoF.

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

- **Its root.** `/usr/lib/pico4-stock-slam`, with PICO OS's bootstrap linker and bionic C library
  (no Android runtime): the tracker and its libraries (`libion.so` among them, without which the DSP
  optical flow cannot allocate and 6DoF never starts), DSP modules, configuration
  (`/system/etc/pvr/config`), the DSP's process shells and `cdsprpcd`, the headset's own: preinit binds them in
  read-only from the data partition's copy and the `dsp` partition ([install.md](install.md#pico-oss-files)).
  Stand-ins of this project: `libandroid.so` (the
  trace calls and the hardware-buffer calls, which fail) and `libpico4-slam-stubs.so` (preloaded:
  Android logging to the journal, empty property lookups). Programs in the root are built with the
  host's clang for `aarch64-linux-android29`, without an NDK ([src/stockslam](../src/stockslam)).
- **Its runner.** [`stock-slam`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-xr/stock-slam) chroots a program
  of the root in a private mount namespace with `/dev`, `/proc`, `/sys`, persist's `pvr/` at
  `/mnt/vendor/persist/pvr` and `/mnt/vendor/pvr`, and tmpfs `/data` and `/tmp`.
- **The CDSP.** [`pico4-cdsp.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-xr/pico4-cdsp.service)
  boots it with `remoteproc-boot cdsp` (`cdsp.mdt`, `cdspr.jsn`). `pico4-cdsprpcd.service` runs
  `cdsprpcd` in the root; without it the DSP's loader stalls on the tracker's first module. The
  tracker opens a signed user PD (`fastrpc_shell_3`) and loads its modules itself. The DSP's context
  banks must be coherent, as in the stock device tree, or it reads stale frames.
- **The FastRPC bridge.** The stock `libcdsprpc.so` speaks the vendor kernel's ioctls on
  `/dev/adsprpc-smd` and allocates through ION; Valve's kernel has upstream FastRPC
  (`/dev/fastrpc-cdsp`) and DMA-BUF heaps. `stock-slam` preloads `libpico4-fastrpc-bridge.so`
  ([fastrpc_bridge.h](../src/stockslam/fastrpc_bridge.h)): GETINFO opens the upstream node, INIT and
  INIT_ATTRS become INIT_ATTACH, INIT_CREATE or INIT_CREATE_STATIC, INVOKE becomes the upstream INVOKE,
  MMAP and MUNMAP keep their layouts, CONTROL says the kernel allocates (`FASTRPC_CONTROL_KALLOC`), and
  ION allocations come from `/dev/dma_heap/system`. The DSP's memory requests (`apps_mem`) are mapped
  by descriptor through the kernel commit “misc: fastrpc: map a client's buffer through mmap”. Its translations have native tests.
- **pico4-slamd.** [`pico4-slamd`](../src/stockslam/pico4-slamd.c)
  ([unit](https://github.com/overpressure/packages/blob/main/phoenix/pico4-xr/pico4-slamd.service), wanted by Valve's session
  and Monado) creates `/dev/shm/pico4-slam` (group video, 0660) and drives the tracker as PICO OS's
  tracking service does: `createHandle("")`, `headSensorModel` `icm4x6xx`, `startAlgorithm`, the
  result callback, `start6DOF` `6`; one thread passes every IMU sample, another each frame set once
  the IMU is 30 ms past it (earlier, the tracker waits for the IMU in vain). `updateImage` only
  queues; the work happens inside a later `update6AxisIMU` (up to 11 ms), so poses can be up to about
  18 ms apart. Each driver start bumps a generation in the shared memory; `pico4-slamd` then exits
  and comes back with a new tracker.
- **Images.** PICO OS's processing, reimplemented in [isp.h](../src/stockslam/isp.h): the first scene
  row doubled (replacing the metadata row), black level 16 off, `v + (v >> 8)`, a per-pixel
  lens-shading gain (128 = 1×) and a 10-to-8-bit gamma table; no rotation. The lens-shading table
  follows camera 0's module (vendor) id: 8 → `config_lscSY.txt`, 9 and 13 → `config_lscLH.txt`, 7 →
  `config_lscneo3.txt`, else none (`PICO4_SLAMD_LSC` overrides). The ids live in
  `/var/lib/pico4-stock-slam/stationservice/camera_otp.json` (PICO OS's `camera_otp.json`, bound into
  the root); when they are not valid, the driver reads them from the modules' OTP through the station
  (op `05`) before it starts a tracker, and `pico4-slamd` saves them.
- **Recovery.** When the tracker's confidence falls from 1 to 0 and stays 0 for more than 10 s
  (`PICO4_SLAMD_RESTART_S`, 0 off), `pico4-slamd` restarts it in the process (pre-stop, stop with the
  map saved, destroy, start), as PICO OS's service does; libSlam otherwise can stay in
  relocalisation for good. The restarted tracker relocalises in the room as at a start.
- **IMU.** Raw samples of the factory body frame at 1 kHz, as PICO OS feeds them, each with the
  ICM-42688's latest die temperature (the gyro bias table is binned by it): the driver streams
  `sensor_temperature` at 5 Hz beside the IMU; without one `pico4-slamd` passes 30 °C.
- **Poses.** World and body frames have the body frame's axes (x up, y left, z back); the driver
  maps both into HMD axes (inverse of `Q_B_H` on each side), adds the HMD origin and predicts along
  the pose's velocities to the requested time plus the display lead, at most 45 ms past the newest
  pose, as PICO OS's `getPredictResult` does. Past times (the controller tracker's LED exposures)
  are interpolated. The HMD takes the poses directly (the tracker fuses the IMU). A pose older than
  200 ms, or untracked, counts as lost: the HMD keeps the last position with the 3DoF orientation.

### Auto exposure

PICO OS's own, `ExposurePredict` from `libExposureImageProcess.so`, called as its `calcExpo` thread
does, on a thread of `pico4-slamd`: per scene set it is not busy with, the four 16-bit images after
black level and lens shading, the set's lines and gains, 50 fps, flicker detection on. Its result
(lines 13–737, gain 16–240 in steps of 16; cameras 2 and 3 share one) goes to the driver unless
unchanged; the driver sends it per camera as the scene phase of `CameraSetPara` and runs no auto
exposure of its own while `pico4-slamd` serves its generation with a fresh heartbeat. Frame counters,
not exposures, decide whether frames form a set. `PICO4_SLAMD_AE=0` leaves the exposure to the
driver's median loop ([Basalt](#basalt)).

### Room setup

PICO OS keeps a room with the tracker's map: `libSlam.so` loads the maps in `/data/misc/user/0/maps`
at `createHandle`, tries to relocalise for about 8 s after `start6DOF`, and saves its map at the stop
(`preStopAlgorithm`, `stopAlgorithm` `non_sleep`, `destroyHandle`) only while `boundary/stdata.txt`
holds a boundary (`createHandle` and `start6DOF` empty it). After a relocalisation it writes the
saved boundary in this session's world to `boundary/stdataforalgorithm.txt`; `pico4-slamd` watches
that file (the callback `RelocResultCallbackRegister` is not called with this configuration). Boundary
files hold `vertexcount` and points `(a, b, c)`, b up; the world point is (b, −a, c).

- **Where it lives.** `stock-slam` binds `/var/lib/pico4-stock-slam/{maps,boundary}` (data partition,
  mode 0700) into the root, so maps survive reboots and updates and never leave the headset.
- **Room ids.** Every map is saved with its room's reference quadrilateral as its boundary, 1.5 m
  below the room's first start: three sides of 1.5 m + v × 0.2 mm, v 12 bits each, which carry the
  room's id ([room.h](../src/stockslam/room.h)). After a relocalisation `pico4-slamd` reads the id from
  the copy's sides and fits the copy to the room's shape (a turn about up and a shift, accepted below
  5 cm residual): the transform from the session's world to the room's. It publishes it in the shared
  memory and writes the copy to `stdata.txt`; the driver puts every pose of that generation into the
  room's world. The tracker relocalises about 1 s after its first frames. Without a relocalisation
  10 s after the first frame set (`PICO4_SLAMD_RELOCALISE_S`) the session becomes a new room with a
  random id; a later relocalisation moves it into the saved room. Rooms saved with the plain 2 m
  square (no id) get an id at their next relocalisation and keep their universe through
  `boundary/pico4_rooms.txt`; play areas drawn against the plain square can be off by a quarter
  turn until drawn again.
- **SteamVR's universe.** The room's identity `room-<id>` hashes to its universe (never 0, 1 or
  424242, which `vrserver` reserves). Once the room is settled, `driver_monado` switches universes
  as the Frame's driver does, through `vrserver`'s unversioned `IVRChaperoneServerDriver_XXX`: slot 1
  restores a universe `chaperone_info.vrchap` holds (`bool (uint64 universe, const HmdMatrix34_t *,
  bool refine)`, identity, false), else slot 0 creates it (`void (uint64 universe, bool keepOrigins)`,
  true: a 1 m default play area, room setup not done, so SteamVR asks for the playspace setup).
  Setting only `Prop_CurrentUniverseId_Uint64` leaves a new universe without a standing space. Until
  the room is settled the universe is 2. The tracker's map name (`getRelocMapInfo`) is no identity:
  after a merge it can be the session's new map.
- **Room setup in the headset.** SteamVR's Space Setup menu needs `Prop_SupportsInHMDRoomSetup_Bool`
  (11003), and its steps past the recenter `Prop_VRLinkClientHMDSupportsRoomSetupRequests_Bool`
  (11004; without it "HMD doesnt support setRoomSetupStep"). `driver_monado` reports both; away from
  a VR Link server the steps run in SteamVR (`VRChaperoneSetup`) and the result is kept per universe
  in `chaperone_info.vrchap`. Clear Playspace exists only in SteamVR's main-branch builds.
- **The detected floor.** As on the Frame, the driver hands the floor height to `vrserver` while the
  dashboard's room-setup step (event 809) is 3 (boundary circle), 6 (silent detection at start) or
  7 (playspace setup): through the room setup's transform (`/chaperone/room_setup/transform`,
  y′ = m₁₁·y + m₁₃) to the unversioned `IVRServerInternal_XXX`, slot 40, which posts it to the
  compositor (`floor_detection_result`); NaN clears it when the step ends. Here `libSlam`'s ground
  detection gives the floor (`plane` 1, `setPlaneDetect`; status 1 per fitted plane, 1.3 m below the
  head if none). The first height depends on what the cameras see (a bed or table gives 1.2 m
  instead of 1.6 m), so each of these steps restarts the detection (`pico4_hmd_detect_floor`, the
  feed's `floor_requests`;
  [monado-steamvr-floor.patch](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/monado-steamvr-floor.patch)).
  **Slot 40 belongs to this `vrserver` build: check it again with every new SteamVR.**
- **Recenter.** Holding the PICO button runs room-setup step 1: SteamVR resets only its seated zero
  pose; the standing pose and play area stay, and the recenter is gone after a SteamVR restart.

### Frame times

The tracker, Basalt and the controller tracker take each frame's time from its buffer: the CSID's
hardware start of frame (the kernel commit “media: camss: the hardware start of frame as a buffer's time”) on `CLOCK_MONOTONIC`, carried in QTimer ns onto the
IMU's clock. The frame's time is the middle of its exposure: start of frame minus 52 µs, seven lines
(133.5 µs) and half the exposure in lines of 19.07 µs, from its metadata line, as PICO OS computes it.

**Trap: re-take the clock offset.** `systemd-timesyncd` steers `CLOCK_MONOTONIC` against the QTimer
(16.6 ppm here, 1 ms a minute), so the QTimer offset is taken again every second on the IMU and camera
threads. Taken once, it is 0.3 s off after five hours: poses look older than 200 ms and every motion
overshoots and springs back.

### Basalt

Deprecated: selectable (`PICO4_SLAM_BACKEND=basalt`) as it is, without further work. Monado loads
Basalt through its VIT interface (`libbasalt.so` from [`pico4-basalt`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-basalt/PKGBUILD),
Monado's fork, pinned; built for `armv8.2-a`, since `-march=native` under QEMU emits SVE). Arch's
OpenCV highgui links Qt, so `qt6-base` is in the image.

- **Frames.** Scene sets only (LED frames make Basalt drift by hundreds of metres), with one exposure
  and gain in all four metadata lines and each camera's ISP frame counter at its usual offset; other
  sets are dropped and counted. 480 scene rows, 8 bits after black level 16 and a digital gain
  (1–8×, aiming at mean `PICO4_SLAM_BRIGHTNESS`). Images upside down relative to the first camera are
  turned with their calibration (`pico4_tcam_rotate_180`); Basalt's stereo matching cannot follow
  the lower pair's 180°.
- **Auto exposure.** Every 25 sets from the median brightness of the cameras (one covered or
  saturated camera is ignored): from 300 lines and 1×, aiming at 250 above black (10-bit), at most 4×
  per step; exposure first up to `PICO4_SLAM_MAX_EXPOSURE` (400 lines, against motion blur), then gain
  up to 15.5×.
- **IMU.** Raw body-frame samples, every second one (`PICO4_SLAM_IMU_DIVISOR`), with the delivered
  rate declared (1024 Hz for 1 kHz) and noise `PICO4_SLAM_IMU_NOISE` (about five times the measured
  Allan deviation; PICO OS's tracker's values made Basalt jiggle and run away).
- **VIO settings.** [pico4-vio.json](https://github.com/overpressure/packages/blob/main/phoenix/pico4-basalt/pico4-vio.json)
  (`basalt-vio-config.patch`): `optical_flow_image_safe_radius` 300 px, outside which the fisheyes are
  black. Basalt logs "Using VIO config" when it loads it.
- **Decoupling.** Two feeder threads (the newest frame set; a 2,048-sample IMU ring) keep the cameras
  and the IMU thread from waiting on Basalt; one feeder for both deadlocks.
  `basalt-realtime-input.patch` keeps one set waiting for the optical flow and two for the estimator
  and never blocks IMU pushes (unbounded queues made poses trail by 641 ms). At shutdown the driver
  stops the frames and gives Basalt a second before stopping it, or its optical flow aborts.
- **Priority.** Basalt, its TBB workers and feeders run at nice 19 on cores 0–5 (`PICO4_SLAM_CPUS`),
  leaving the two fastest cores to the compositor and the app.
- **Fusion.** Basalt's 25 Hz poses arrive about 100 ms late. [pico4_fusion.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_fusion.c)
  keeps the pose at the IMU rate: the 3DoF orientation turned towards Basalt's (0.5 s), the
  accelerometer integrated for position, pulled to Basalt's position and velocity at the images'
  time (`PICO4_FUSION_POSITION_MS`), corrections faded in over `PICO4_FUSION_SMOOTHING_MS`. Without a
  Basalt pose for 500 ms the position is held, untracked.
- **Anchor and runaway.** A pose more than 15 cm or 10° off is an outlier; five in a row move the
  anchor. A pose is implausible when its velocity change over 0.5 s exceeds the accelerometer's by
  0.5 m/s, when it moves above 0.15 m/s with the IMU quiet for 0.5 s, or above 4 m/s; three in a row
  hold the position and reset Basalt (`t_slam_reset`, every 5 s while lost), then re-anchor.
- **Without the fusion** (`PICO4_FUSION=0`), Monado's prediction from Basalt's newest pose (the
  driver sets `SLAM_PREDICTION_TYPE` 3, gyro plus mean acceleration above `SLAM_ACCEL_DEADBAND`;
  `monado-slam-prediction.patch`); poses over 500 ms old are untracked.

## Controllers

[pico4_blemcu.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_blemcu.c) parses the `ble_mcu` events as PICO OS's
`SlpiDataParserV2` does. Each is a 200-byte radio packet: `5A`, format version, station time in µs
(bytes 4–11, LE), the records' length (byte 12), then records `[length][type][id][time offset][data]`.
The id's low six bits name the device (1 left, 2 right, 3–5 trackers); bit 7 adds a time offset (2
bytes, 4 with bit 6) in µs before the packet's time.

- **IMU** (type 0): gyro 3 × int16 at 8.192 LSB per °/s, accelerometer 3 × int16 at 1,024 LSB per g,
  temperature (raw / 132.48 + 25 °C), a 16-bit counter.
- **Inputs** (type 1): trigger, stick y (255 up), stick x (255 right), grip as bytes (sticks centred
  at 128). Buttons: home (bit 0), menu, A/X, B/Y, grip, stick click (bit 5), trigger (bit 7). Touch:
  A/X (bit 0), B/Y, stick, thumb rest, trigger (bit 4).
- **Battery** (type 2): one byte, a fifth of a charge per level (0–5), reported to SteamVR as a
  percentage; level 1 is low.

[pico4_controller.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_controller.c) makes each controller a Monado device
(`PICO 4 left/right controller`) with Monado's profile `/interaction_profiles/bytedance/pico4_controller`.

- **Timing.** IMU samples are timed by the SSC event time (the packet's station time) minus the
  record's offset, as on PICO OS. Samples stamped at or after their arrival are dropped; the
  controller tracker starts over on a backward step or a gap above 35 ms.
- **Pose.** While the controller tracker has placed a controller, its pose: tracked while 6DoF, valid
  but untracked while it holds the position (still or briefly unseen), as PICO OS publishes it.
  Before the first fix: `m_imu_3dof` orientation (IMU axes to the aim frame as `x,-z,y`) at a
  placeholder 0.18 m to the side, 0.35 m below and 0.30 m in front of the head. A controller whose
  newest sample is 0.2 s old holds its orientation, untracked.
- **Aim and grip.** The tracked pose is the aim pose; the grip is aim × T, per hand, from PICO OS's
  tracking service (about 34° about x and 5 cm of lever arm), read by
  [`tooling/aimgrip.py`](../tooling/aimgrip.py) into `calibration/controllers/aim-to-grip.json`. Without
  the file the grip is the aim. SteamVR gets the aim pose, the render models' frame.
- **Vibration.** One motor frame (command `01`) per request, mapped as PICO OS maps OpenXR haptics:
  level = amplitude × 255, whole milliseconds, at least 10 ms, Monado's minimum duration as 500 ms,
  0 ms as a stop, at PICO OS's default strength of 75% (so PICO OS's low-battery limit never applies).
  SteamVR's pulses without a duration (hover feedback) go out as 10 ms.
- **Render models.** PICO OS's textured OBJs (`/system/pre_resource/data/misc/user/controller/Neo3Phx/`,
  `60.obj` left, `61.obj` right), whose origin is the aim pose, converted by
  [`tooling/controllermodel.py`](../tooling/controllermodel.py) and installed to
  `/usr/share/steamvr-monado/resources/rendermodels/` as `{monado}pico4_controller_left` and `_right`.
  The battery strip on the base (group `Power`) is the models' `status` component, with UVs on
  SteamVR's battery pips (four steps; none at level 0).

### Controller tracking

Each controller's ring of 20 IR LEDs lights in the station's short controller exposure, every other
camera frame (4 lines at gain 26). [`tooling/ledmodel.py`](../tooling/ledmodel.py) reads the nominal
rings (20 points and normals per hand) from PICO OS's `libpico_controller_tracking.pxr.so` into
`calibration/controllers/led-models.json`. On every connect the station thread reads the
controller's calibration record and hands it to the tracker: the IMU calibration, its own 20 LED
positions (replacing the nominal ones) and its gyro bias table `pvr/<serial>.txt` (read only;
`pico4-persist` copies the root-only files to `/run/pico4-controllers/` for SteamVR's driver). The
serial is not logged.

The tracker (`pico4_ctrack_*` in `pico4-monado`, C++17 with Eigen) reimplements that library's approach:

- **Frames.** World +x up (the headset body frame), the LED-model frame equal to the controller IMU's
  after the remap (x, y, z) → (−z, −x, y).
- **Blobs.** Threshold (fixed, or adaptive 11 × 11 mean + 30), outer contours, moments, area and
  circularity filters; a window around predicted LEDs while tracking, the whole image at > 70 for
  acquisition.
- **Acquisition** from one camera: five or more blobs, an LED-triple search with the IMU's rotation
  and EPnP, else a geometric hash of 5-LED patterns (the rule PICO OS's table was built with); three
  or four blobs, P3P. Every result passes PICO OS's plausibility checks (brightness and footprint
  against distance, the other cameras, |x|, |y| < 1.2 m, |z| < 0.8 m around the head); three in a row
  start tracking.
- **Tracking.** Facing LEDs projected with the IMU's prediction, associated by shift hypotheses, then a
  robust Levenberg-Marquardt fit over all cameras with a soft prior on the IMU rotation.
- **Fusion.** A 15-state error-state filter (optical poses correct position and yaw; tilt from the
  IMU), predicted 41 ms ahead of the newest IMU sample. A hand is lost after 1.2 s still or 3 s moving
  without optical updates, or on an IMU gap over 35 ms.
- **At rest.** A hand still for 3 s, or back from an IMU gap, tries no P3P until it has moved for 3 s
  or is tracked (as PICO OS). Controllers lying side by side look like one cluster that only P3P
  takes, so controllers put down together are found when picked up.

`pico4_slam.c` converts the LED sets to 8 bits and hands them to the tracker's thread, which waits up
to 60 ms for the IMU up to the exposure and takes the head pose at that time. The aim pose is the
LED-model frame offset by PICO OS's lever arm (about 2 cm, [`tooling/leverarm.py`](../tooling/leverarm.py)
→ `calibration/controllers/lever-arm.json`); without it or the LED models the tracker stays off.
`PICO4_CTRACK=0` turns it off; `PICO4_CTRACK_TRACE=FILE` writes a CSV row per hand and update.

**Trap: the controller IMU rate.** With `ble_mcu` the only sensor streaming, the DSP drops it from
about 500 to about 90 events per second a few minutes after its start, and the gaps trip the 35 ms
rule. The driver always streams the head IMU at 1 kHz, and `pico4-cam --record-imu` records it too.

PICO OS sends exposure commands all the time, so its tracker sees LEDs as they are after the first
one (at 0.7 m neighbours merge, centroids move up to 1 px). It sets no LED level for the controllers
(`2F` goes only to body trackers; `1B` has no caller).

`pico4-ctrack-replay` runs the tracker over `pico4-cam capture --record-leds LEDS --record-imu IMU`
recordings and prints per-hand JSON (`--trace` a CSV line per set). The head counts as still, up from
the recorded head accelerometer (`--head-up`, or `--estimate-head-up` from a still controller);
`--max-imu-gap`, `--delay left|right S`, `--skip S`.

## Build

PICO OS's files are the headset's own: the install takes them from its backup and writes them to the data
partition (`python -m overpressure stock-files`, [install.md](install.md#pico-oss-files)); the DSP images come
from its `modem` partition at boot. The same extraction, as folders, from a backup or PICO's OTA
([development.md](development.md#stock-firmware)):

```sh
python -m overpressure assets sensor-data stock-slam [--backup DIR]
```

| `sensor-data` output | Content (source partition) |
| --- | --- |
| `firmware/slpi.mdt`, `slpi.bNN`, `slpi*.jsn` | sensor DSP image and protection-domain maps (modem) |
| `sensors/config/*.json`, `sensors/sns_reg.conf` | sensor driver configuration and registry layout (vendor) |
| `calibration/pxrconfig/SDK_Lens_ConfigFile.txt` | stock lens configuration with the chromatic aberration terms (system) |
| `calibration/controllers/led-models.json`, `aim-to-grip.json`, `lever-arm.json` | LED rings, grip transforms and lever arm from PICO OS's tracking libraries (system) |
| `rendermodels/pico4_controller_*/` | SteamVR render models from PICO OS's controller models (system) |
| `manifest.json` | sources and SHA-256 of every file |

| `stock-slam` output | Content (source partition) |
| --- | --- |
| `firmware/cdsp.mdt`, `cdsp.bNN`, `cdspr.jsn` | the Compute DSP's image and protection-domain map (modem) |
| `root/system/bin/bootstrap/linker64`, `root/system/lib64/bootstrap/` | bionic's linker and C library (system) |
| `root/system/lib64/` | `libSlam.so`, the auto exposure, the depth mesh (`libphotoid.so`, SNPE) and what they link (system) |
| `root/system/etc/pvr/config/` | the tracker's configuration, lens-shading and gamma tables (system) |
| `root/vendor/lib/rfsa/adsp/` | the tracker's DSP modules (vendor) |
| `root/vendor/dsp/cdsp/` | the DSP's process shells (dsp) |
| `root/vendor/bin/cdsprpcd`, `root/vendor/lib64/` | the DSP's root listener (vendor) |
| `manifest.json` | sources and SHA-256 of every file |

The userspace build ([development.md](development.md#the-userspace-build)) builds `pico4-slamd` and the
stand-ins ([src/stockslam/build.sh](../src/stockslam/build.sh)) with the host's clang after running
`test_isp` natively, against stand-ins of bionic's `libc.so`, `libm.so` and `libdl.so`
([bionic-stubs.c](../src/stockslam/bionic-stubs.c)); the binaries are the same as against PICO OS's own. The
image's tracker root holds them and empty mount points for PICO OS's parts. The extracted data is
proprietary: keep it private.

- **`sm8250-hexagonrpc`**: hexagonrpcd from a pinned linux-msm commit plus this project's patches.
- **`pico4-basalt`**: Basalt and its submodules at pinned commits.
- **`pico4-monado`**: Monado plus the PICO 4 driver and its tools, beside the recipe; its build runs the driver's C tests
  (the userspace build uses `--nocheck`).

The host tests (`python -m unittest`) cover `pico4-imu`'s encoding against a simulated DSP, the IMU
acceptance limits, the orientation filter and the runner's validators.

## Run

The sensor units start at boot.

```sh
pico4-imu list accel gyro mag          # sensors and attributes the DSP reports
pico4-imu stream --seconds 10 --check --orientation [--csv imu.csv] [--rate 1000]
pico4-imu raw ble_mcu                  # any sensor type, events decoded generically
```

`stream` requests 500 Hz unless `--rate` says otherwise. `--check` (for a headset lying still) fails
unless both streams deliver half the expected samples, the DSP configured at least 95% of the
requested rate, the measured rate is within 5% of it, no timestamp goes backwards, |g| is within
0.5 m/s² of 9.81 and each mean angular rate component is below 0.05 rad/s.

`python -m overpressure dev check` ([hardware-testing.md](hardware-testing.md)) records the sensor
units and `pico4-imu list accel gyro`, requires `pico4-imu stream --rate 500 --seconds 10 --check`,
and continues with Valve's session ([session.md](session.md#acceptance)).

OpenXR clients reach the driver through the Monado service, with Valve's session stopped (both own
the same hardware). `pico4-openxr-pose` passes with orientation tracked and no invalid pose after the
first second; `pico4-openxr-display` with frames at 95% of 90 Hz or better and at most 1% late. As
root:

```sh
systemctl stop pico4-session.service
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-pose 10 10
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 10 [--projection-only] [--sky]
systemctl stop pico4-monado.service
systemctl start pico4-session.service
```

The driver reads its options from the environment of the process that runs it (`monado-service` or
SteamVR's `driver_monado`), not the client's. For example, Basalt's trajectory as CSV from a headless
session:

```sh
systemctl stop pico4-monado.service; mkdir -p /tmp/slam
systemctl set-environment PICO4_SLAM_BACKEND=basalt SLAM_WRITE_CSVS=1 SLAM_CSV_PATH=/tmp/slam/ XRT_COMPOSITOR_NULL=1
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-pose 30
systemctl stop pico4-monado.service
systemctl unset-environment PICO4_SLAM_BACKEND SLAM_WRITE_CSVS SLAM_CSV_PATH XRT_COMPOSITOR_NULL
```

| Variable | Default | Effect |
| --- | --- | --- |
| `PICO4_LOG` | `info` | driver log level; `debug` adds latency, tilt and gyro bias every 5000 samples |
| `PICO4_IMU_RATE` | 1000 | head IMU rate requested from the DSP, Hz |
| `PICO4_CALIBRATION_DIR` | `/usr/share/qcom/pico4/calibration` | factory lens, camera and controller calibration |
| `PICO4_LENS_CA` | off | chromatic aberration correction |
| `XRT_COMPOSITOR_COMPUTE` | 0 (service) | Monado's compute composition path |
| `PICO4_PREDICT_MS` | 12 | hand out the head pose this much (plus the display lead) later than asked for |
| `PICO4_LATENCY_PROBE_MS` | 0 (off) | latency probe for a still headset: periods of this many ms cycle through a 5° yaw step and a reported 30°/s turn |
| `PICO4_SLAM` | on | 6DoF |
| `PICO4_SLAM_BACKEND` | `stock` | `stock`: PICO OS's tracker through `pico4-slamd`; `basalt`: Monado's Basalt |
| `PICO4_SLAMD_LSC` | by module id | `pico4-slamd`'s lens-shading table in `/system/etc/pvr/config`; set in the unit's environment |
| `PICO4_SLAMD_DEBUG` | unset | `pico4-slamd` passes the tracker's debug and verbose log lines to the journal |
| `PICO4_SLAMD_AE` | on | `0`: no PICO OS auto exposure; the driver's own sets the exposure |
| `PICO4_SLAMD_RESTART_S` | 10 | seconds a tracker that lost its confidence stays lost before a restart; `0` off |
| `PICO4_SLAMD_RELOCALISE_S` | 10 | seconds from the first frames without a relocalisation until the session is a new room |
| `PICO4_CAMERA_HZ` | 50 | tracking camera rate: 50 as stock, or 60 (scene frames at 30 Hz instead of 25) |
| `PICO4_TCAM_SLOTS` | `0,2,1,3` | capture slot of factory cameras cam0–cam3 |
| `PICO4_CAPTURE_RESTART_TEST_S` | 0 (off) | test: restart the camera capture every N seconds |
| `PICO4_ROOMVIEW_RGB` | on | room view in colour from the IMX471; `0`: the grey pair |
| `PICO4_CTRACK` | on | controller tracker |
| `PICO4_CTRACK_TRACE` | unset | CSV file: per hand and update, the last set's acquisition and tracking result and the pose |
| `PICO4_CONTROLLER_AXES_LEFT`, `_RIGHT` | `x,-z,y` | controller IMU axes in the aim frame |
| `PICO4_SLAM_CAMS` | `0,1,2,3` | Basalt: factory cameras in tracker order, at least two |
| `PICO4_SLAM_UPRIGHT` | on | Basalt: turn images upside down relative to the first camera's |
| `PICO4_SLAM_DIVISOR` | 1 | Basalt: use every Nth scene set |
| `PICO4_SLAM_IMU_DIVISOR` | 2 | Basalt: use every Nth head IMU sample |
| `PICO4_SLAM_MAX_EXPOSURE` | 400 | Basalt: longest station exposure, sensor lines |
| `PICO4_SLAM_BRIGHTNESS` | 70 | Basalt: mean 8-bit brightness the digital gain aims for |
| `PICO4_SLAM_EXPOSURE_CENTRE` | on | Basalt: frame times at mid-exposure instead of the start of frame |
| `PICO4_SLAM_VIO_CONFIG` | `/usr/share/pico4-basalt/pico4-vio.json` | Basalt's VIO settings; empty for its defaults |
| `PICO4_SLAM_IMU_NOISE` | `0.016,0.001,0.0016,0.0001` | Basalt: accel noise density, its bias walk, gyro noise density, its bias walk |
| `PICO4_SLAM_CPUS` | 63 (cores 0–5) | Basalt: CPU bit mask (decimal); 0 = no restriction |
| `PICO4_SLAM_DUMP` | unset | Basalt: directory for PGM frames of every 500th set |
| `PICO4_SLAM_RESET_AFTER_S` | 0 (off) | Basalt: reset once this many seconds after the fusion started (tests the runaway path) |
| `PICO4_FUSION` | on | Basalt: the IMU-rate fusion; off: Monado's prediction |
| `PICO4_FUSION_POSITION_MS` | 150 | Basalt: time constant pulling position and velocity to Basalt's |
| `PICO4_FUSION_SMOOTHING_MS` | 50 | Basalt: time over which a correction reaches the output |
| `PICO4_FUSION_CSV` | unset | Basalt: directory for `fusion_slam.csv` and `fusion_pose.csv` |
| `SLAM_PREDICTION_TYPE` | 3 (gyro and acceleration) | Basalt without the fusion: 0 none, 1 pose only, 2 gyro, 4 dead reckoning |
| `SLAM_ACCEL_DEADBAND` | 0.3 | Basalt without the fusion: world acceleration (m/s²) the prediction ignores |

The cameras and the radio on their own (stop the Monado service first):

```sh
pico4-cam capture --frames 1500 --out /tmp/cams   # all four at 50 Hz, JSON summary, one scene frame each
pico4-cam capture --exposure 300 --gain 32 --stats-log /tmp/frames.txt
pico4-station version                             # station firmware version
pico4-station listen --seconds 5                  # frames the station sends
```

## Verified results

On the headset on Valve's kernel ([kernel.md](kernel.md#results)), lying still unless stated
otherwise.

| Check | Result |
| --- | --- |
| Sensors reported | ICM-42688 accel/gyro 12.8–512 Hz, ICM-42688 temperature, AK0991x magnetometer 10–100 Hz |
| `pico4-imu stream --check`, 500 Hz, 10 s | 5,066 samples per stream at 511.95 Hz, 0 gaps, 0 non-monotonic timestamps; \|g\| 9.796–9.797 m/s²; mean \|ω\| 0.005–0.006 rad/s |
| 1 kHz request | DSP configures 1000 Hz, measured 1023.9 Hz |
| `pico4-imu` orientation | roll/pitch within 0.8° of the accelerometer's; uncorrected heading drift about −0.07°/s; a guided 90° turn reads +90.3 to +90.5° |
| OpenXR system | `Monado: PICO 4`, runtime `Monado(XRT) … v25.1.0`; orientation and position tracking |
| Head pose, 3DoF, 60 s at 100 Hz | none invalid after the first second; heading −0.0001°/s; tilt within 0.2° of the accelerometer |
| Latency, IMU sample to pose | 0.94–1.16 ms |
| Scanned-out image and lenses | red L only in the left eye, blue R only in the right; grid lines straight, steady at 90 Hz; the checkerboard stays fixed in the room with no visible latency |
| Field of view from the lens data | left eye −53.32/+53.38° horizontal, +53.58/−53.12° vertical; right eye −53.49/+53.17°, +53.41/−53.25° |
| IPD | 71.99 mm (Hall 888) with the lenses at their widest, 72 mm |
| Station | power cycle, announcement and frame-rate answer every time; firmware 2.96 |
| Cameras | slot mapping by cover test; a pen at 0.15–0.18 m triangulates with 1–2 mm ray gaps; metadata line decodes the commanded exposure and gain, 50 scene and 50 LED frames of 100, 0 errors at 49.8 Hz |
| Frame sets in Valve's sessions (over 1,250 sets each) | no inconsistent or incomplete set while the exposure changed: all four sensors take a new exposure on the same frame |
| PICO OS's tracker in Valve's session, still on a table, 60 s | 61,391 poses, none lost, within 0.7 mm and 0.04°; 25 sets and 1,024 IMU samples per second, none skipped; `pico4-slamd` 71% of a core, `vrserver` 116% (Basalt 346–388%); hottest zone 55.8 °C (Basalt 75.8 °C) |
| PICO OS's tracker worn, untethered: a closed walk back to the start spot | 12.14 m in 58 s; back within 1.8 cm; no pose lost; largest step between poses 0.7 mm; turns to 86°/s, 0.7 m/s |
| PICO OS's tracker: slid along a table and back, twice | 0.512 m and 0.495 m; back within 0.4 and 0.8 cm; no pose lost |
| PICO OS's tracker worn in Valve's session (user) | "feels and looks just like stock"; turns up to 458°/s without a lost pose |
| PICO OS's tracker turned and lifted by hand, 72 s | angular rate equals the raw gyro (ratio 1.000), velocity the positions' (1.001); extrapolated 45 ms, within 0.07° and 0.3 mm (median) of the later pose |
| `libSlam`'s pose against the gyro, 30 s | at a constant rate about 3.5 ms behind its stamp; newest pose stamped 7.9–9.0 ms in the future |
| `CLOCK_MONOTONIC` against the raw counter (timesyncd) | 0.998 ms in 60 s: 16.6 ppm |
| IMU temperature to the tracker | ICM-42688 at 44.1–44.3 °C with the SoC at 60–61 °C; still within 0.8 mm for 60 s |
| A Monado client while Valve's session runs | refused the station before any reset, no cameras, the client fails in 3 s; Valve's session keeps controllers and tracker |
| Latency probe, compressed scanout, 89 flips/s | pose handed out to the vblank that shows it 12–23 ms, 18 ms median; SteamVR extrapolates 10.6–12.1 ms |
| Head pose worn, standing still | 0.035° RMS around its 200 ms average: the head's own motion (0.001° on a table) |
| Basalt (deprecated) | on a table slid 0.50 m and back within 1.5–1.8 cm; worn steps back within 3.4–4.4 cm; pose latency worn 112 ms median; seven worn sessions within 0.55–1.51 m of the start, runaways held and reset; 1.6–4.8 m drift in 30 s with the lower cameras 15 cm from a surface |
| Controllers in OpenXR | both as `PICO 4 left/right controller`; per controller about 500 IMU samples and 100 input packets per second |
| Controller inputs and sticks | every button, trigger, grip and touch decoded; sticks 0 and 255 at the ends of both axes |
| Controller tracker replay, both lying 0.71–0.73 m ahead, 30 s, LEDs before any exposure command | both acquired in the third set, tracked in 747 of 750 sets; median residual 0.62–0.77 px; optical pose within 1.9 mm and 0.9° after 10 s; under 11 ms per set |
| Controller tracker in Monado's service, same controllers, after exposure commands | the right tracked at 0.66–0.76 px; the left not acquired: its fits miss stock's limits by 12–46° of tilt with the brighter, merging LEDs |
| Controller tracker replay, both lying about 1 m ahead | no acquisition: the fits lie at 0.99 m, outside PICO OS's range box (\|z\| < 0.8 m) |
| Controllers worn: lying on the table while the head moves | both acquired within about 9 s; the right one at one world position for 53 s |
| Controllers worn: slid 30 cm along a ruler, three times there and back | 30.4–33.3 cm (mean 31.8 cm) |
| Controllers worn: hands crossed, lowered out of view for 10 s | no swap or jump; none closer than 0.49 m to the head; re-acquired when raised |
| Controllers worn, held still | within 0.2–0.4 mm and 0.06°; no visible jiggle with PICO OS's render models (user) |
| Render models in SteamVR | `{monado}pico4_controller_left/right` at the hands, turned like the real controllers (user) |
| Vibration | `pico4-station vibrate`: 1 s at 255 felt on the addressed controller; `vrcmd --spamhaptics` left: 376 requests, 376 motor frames to the left, none to the right |
| Controller IMU rate | 494–499 samples per second per hand held and moved, longest interval 4 ms; about 90 with `ble_mcu` alone |
| Controller gyro scale against the optical tracker | 0.993–1.003 at 8.192 LSB per °/s (bias about 1.5°/s removed) |
| Controller samples, about 60,000 per hand | no duplicate, backward or future-stamped sample; 4–9 single samples lost per 30 s |
| Controller calibration records | 12 chunks each; matrices within 0.4% of identity, gyro biases up to 0.016 rad/s, LED positions 0.3 mm (mean) from the nominal models |
| Controllers worn, 120 s: the left switched off for 10 s and on | held its place while off, record re-read in 48 ms on reconnect, tracked at once; event time minus station time 0.00–0.99 µs |
| Station power cycled 10 times, awake controllers still | both reconnect within 0.3–0.4 s each time |
| Station off 30 s to 15 minutes, awake controllers still | both reconnect within 1.3 s, without a press |
| Pairing in the running session | a left controller that had lost its pairing connected about 10 s into a pairing, then tracked and vibrated |
| Room view in Valve's session, still headset | 25 frames per second per camera published, none skipped; the stock tracker within 1.3 mm over 60 s (1.1 mm without) |
| Room view worn, colour on the depth mesh | 25 frames per second, shown about 40–80 ms after mid-exposure; turning and nodding show the room fixed in place without warp or visible lag; still, the tracker within 0.5 mm and 0.10° over 60 s; `pico4-slamd` 104% of a core (54% without) |
| Depth mesh, headset on a desk | 14,841 vertices and 29,400 triangles per pair (25 Hz), 0.9–5 m from the head; ready 72 ms after mid-exposure; floor at −0.75 m (`libSlam` 0.79 m) |
| Room view reprojection, frame dumps | head rotation compensated equally in pitch and yaw (4.6–4.7 px per degree); with the mesh a 10 cm move follows at 1.6–2.7 m depth (without: as if 10–25 m away) |
| RGB camera | 50.01 fps; 47 of 50 frames per second reach the ring, 30.5 ms (median) after mid-exposure; conversion 44% of a core |
| Rooms | a drawn play area comes back after a reboot where it was drawn and its boundary shows near its edge; it stays after tracker restarts relocalising at any turn (worn at −3° and 165°) and after SteamVR restarts; a tracker crashed with SIGSEGV is back in the same room within 13 s; a playspace setup in a known room committed the detected floor to the millimetre |

## Next steps

See [Open work](roadmap.md#open-work).
