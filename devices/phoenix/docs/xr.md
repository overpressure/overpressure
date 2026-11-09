# Native XR hardware (roadmap step 5)

The PICO 4's XR hardware under Linux, on Valve's kernel ([kernel.md](kernel.md)).
The head IMU streams through the
sensor DSP. Monado with this project's `pico4` driver runs inside SteamVR and gives it the head pose
(6DoF from PICO OS's `libSlam` on the CDSP through `pico4-slamd`, with Basalt selectable), the
factory lens model and the IPD read from the lens motor, and provides both
controllers with inputs, their 6DoF pose from PICO OS's LED tracking (reimplemented), PICO OS's
own render models and vibration. Monado runs as a system service ([session.md](session.md#monado-as-a-service)); SteamVR
uses the same driver through `driver_monado`. The inventory comes from the stock image, kernel and
device tree and from read-only `adb shell` observation of PICO OS; no vendor files are part of
this repository.

Code: the driver in [src/monado/](../src/monado), the Monado
recipe and patches in [userspace/pkgbuilds/pico4-monado/](../userspace/pkgbuilds/pico4-monado),
the sensor daemons in [userspace/pkgbuilds/pico4-sensors/](../userspace/pkgbuilds/pico4-sensors),
the OpenXR test clients and `pico4-cam` in [src/xr/](../src/xr), and the Python tools
[`pico4-imu`](../userspace/rootfs/usr/bin/pico4-imu) and
[`pico4-station`](../userspace/rootfs/usr/bin/pico4-station).

> **Rules for this hardware**
>
> - **DSP recovery.** [`remoteproc-boot`](../userspace/rootfs/usr/libexec/pico4/remoteproc-boot)
>   starts a DSP only with remoteproc's crash recovery on: a crash then restarts only that DSP; with
>   recovery off it would stay down.
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
Qualcomm's Sensor Core:

1. **Name service and service locator.** The kernel has both (`QRTR`, its own PD mapper).
2. **Firmware boot.** [`remoteproc-boot`](../userspace/rootfs/usr/libexec/pico4/remoteproc-boot) starts
   `slpi.mdt`/`.bNN` from the modem partition of PICO's public package (installed to `/usr/lib/firmware`) through
   remoteproc, whose recovery restarts only that DSP after a crash.
3. **Registry server.** `hexagonrpcd` (linux-msm/hexagonrpc,
   [`pico4-sensors`](../userspace/pkgbuilds/pico4-sensors/PKGBUILD)) attaches to the sensors process
   (`sensorspd`) and serves it the sensor configuration and this headset's registry through
   FastRPC. Its patches:
   - **Registry version file.** It also serves `sns_reg_version`, so the DSP accepts the
     existing registry.
   - **Static processes.** `-P NAME` starts a static process from the DSP's image instead of
     attaching, as PICO OS's library does for the ADSP's audio process
     ([userspace.md](userspace.md#audio)).
   - **`ftell`.** The file server answers `ftell` (method 8 of the file interface), which the
     audio process reads its configuration with; unanswered, the server ends its session.
4. **Client.** `pico4-imu` is a dependency-free Python client of the SNS client service (QMI
   service 0x190 over QRTR; requests and events inside the QMI messages are protobuf-encoded).
   It finds sensors by data type, reads their attributes, streams accel and gyro, and reports
   rate, timestamp monotonicity, gaps, gravity, gyro bias and a gravity-corrected orientation
   estimate.

Behaviour of the DSP:

- **Persistent listener.** The FastRPC listener has to stay alive. If a listener dies, the
  sensors process keeps it registered: a new `hexagonrpcd` cannot attach until the SLPI
  restarts.
- **Read-only registry.** `hexagonfs` is read-only. During startup the DSP rewrites each registry
  group: to `temp.json`, then renamed over the group (`apps_std` method 33, whose two paths are all
  that is known of it). Refused, it retried about 1650 times, 22 ms apart, and the IMU was listed
  after 37 s. So `hexagonrpcd` takes these writes and drops them. The DSP goes on from the registry
  in `persist`, as it did when they were refused: its factory calibration stays in effect, and what
  it would have saved is not kept.
- **Rate selection.** The SSC runs a sensor at the next supported output data rate at or above
  the request and reports the requested rate as configured: 500 Hz runs the ICM-42688 at
  512 Hz, 512 or 1000 Hz at 1024 Hz.
- **Timestamps.** QTimer ticks (19.2 MHz), the same counter as the AP's arch timer.

| Unit (all in `pico4-sensors.target`) | Runs |
| --- | --- |
| `pico4-slpi.service` | `remoteproc-boot slpi` |
| `pico4-sensorspd.service` | `sensorspd`: `hexagonrpcd -f /dev/fastrpc-sdsp -d sdsp -s -R` on a runtime copy of `/usr/share/qcom/pico4` with the stock kernel's socinfo names, once udev has created the node (no devtmpfs); restarted whenever it ends, because it ends successfully when the DSP restarts and the restarted DSP needs it again |
| `pico4-imu-ready.service` | `pico4-imu wait`: until the sensor service lists the accelerometer and gyroscope, at most 120 s; the target is reached after it |

The DSP loads its sensor registry for about 35 s after it boots, and until then the sensor
service lists no IMU. `pico4-session.service` and `pico4-monado.service` start after the
target, so the driver (Monado's, or SteamVR's `driver_monado`) does not probe before the IMU
is listed; a probe before that falls back to Monado's simulated headset, whose display
SteamVR cannot find.

**DSP restarts.** An SLPI restart takes the sensor service away; the name service reports it, and the driver's SSC client sees it
as `-ECONNRESET`. A second without IMU samples counts the same. The driver then reports the head
pose untracked (orientation still valid), and its IMU thread opens a new client, looks the
sensors up again, maps the QTimer again and subscribes, in short steps so that a service stop
never waits for the DSP. The station and the cameras stay as they are. The stream is back once
the DSP has loaded its registry again, about 35 s after the restart.

The units start only where `/sys/class/remoteproc` and the sensor data exist: on the headset,
with a userspace built with sensor data. Elsewhere (QEMU) their conditions skip them.

## Monado driver

`pico4-monado` builds Monado 25.1.0 (pinned by commit) with the `pico4` driver from
[src/monado/](../src/monado).
[monado-pico4-driver.patch](../userspace/pkgbuilds/pico4-monado/monado-pico4-driver.patch)
registers the driver and carries Monado's compositor change: scratch images without the storage
usage unless the compute path runs.
Only the `pico4` driver and Monado's simulated HMD (headless fallback) are built. The package
makes Monado the system's active runtime (`/etc/openxr/1/active_runtime.json`) and builds it as a
service: `monado-service` owns the hardware, and the OpenXR runtime library is its IPC client.
SteamVR's `driver_monado` runs the same driver in-process.

The HMD ([pico4_hmd.c](../src/monado/pico4_hmd.c)):

- **Sensor service.** [pico4_ssc.c](../src/monado/pico4_ssc.c) is a C port of the `pico4-imu`
  client, limited to service lookup, sensor lookup, enable/disable and sensor events. Its unit
  test checks the encoder byte for byte against messages produced by the hardware-verified
  Python client.
- **Auto-probing.** The prober creates the HMD only where a remoteproc named `slpi` exists and the
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
  partition, which the sensor data's assets copy next to the calibration. The correction is
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
[pico4_ipd.c](../src/monado/pico4_ipd.c) sends one command once at start: `A0 08 00 00 00 A8` (get
position; the last byte is the XOR of the others). The reply `79 a_lo a_hi b_lo b_hi xor`
carries the lens separation in 0.01 mm and the Hall sensor reading, told apart by range (the
separation lies within 57.00–73.00 mm); the driver waits up to 200 ms for it. The module cannot
build any other frame: the MCU also takes motor moves, calibration writes and firmware. The MCU
also runs the fan, and `pico4-fan` ([cooling.md](cooling.md)) sends its frames on the same port:
both hold an exclusive `flock` on `/dev/ttyHS1` for each transaction, and the IPD read gives up
after waiting a second for it. The driver passes the separation to Monado's view poses and falls
back to Monado's default if the MCU does not answer or the port stays locked. It reads the position
again every second; after a move, SteamVR gets the new eye positions and its `Prop_UserIpdMeters_Float`.

The headset has no IPD wheel: the motor moves the lenses, from a setting. [`pico4-lens`](../userspace/rootfs/usr/bin/pico4-lens)
`set MM` does what PICO OS's `gd32ipdservice` does with `SetIPDPosition`: `A0 22 00 00 00 82` (no
timestamp reports; stock's reply `79`), `A0 07 00 lo hi xor` (the separation in 0.01 mm, 62.00–72.00),
then `A0 11 00 00 00 B1` every 50 ms, whose reply `79 state xor` says 1 while moving and 0 at the end.
The tool builds only these frames and the position query, and the lenses stay where they are until
the next move. 0.9 mm take 0.17 s and end within 0.05 mm of the target. The range is the PICO 4's
specified 62–72 mm. PICO OS's service takes 58–72 mm and its settings offer that range with a 0.5 mm
step, but below 62 mm they ask the user to accept the risk of the lenses pressing on the nose; the
motor stops by itself between 59.4 and 59.9 mm and reports its near end (state 2).

SteamVR shows the IPD when it changes but has no control that sets it, so
[`pico4-lens-panel`](../src/xr/pico4-lens-panel.cpp) adds one to its dashboard, for people taking
turns with the headset: a slider, buttons for half a millimetre and four presets (a press moves the
lenses, holding one for a second stores the distance, in `~/.config/pico4-lens/presets`). It runs
`pico4-lens`, and the driver hands SteamVR the new distance, whose own display then shows it.

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
- **Bring-up.** The `station-power` regulator output's sysfs state (`enabled`, `disabled`)
  power-cycles the station: off, 100 ms, on. The station announces itself (`A5 06`: protocol,
  radio type) within 3 s and waits for the camera frame rate (command `19`, payload `01 00 01`:
  50 Hz as stock, device type 0) before it accepts camera commands. The controllers reconnect on
  their own.
- **Sleep.** PICO OS has no command that wakes a controller. Its `goToSleep` sends `0D 00 00`
  (two tries of 100 ms; the reply `BB 01 0D` has 1 in byte 4), which leaves the controllers
  advertising slowly, and then cuts the station's power; `wakeUp` only powers the station on again,
  and the controllers reconnect. The driver does the same when it closes
  (`pico4_station_sleep`), as does `pico4-station power off`.
- **Camera sync at a session's start.** After a timeout the station refuses (`EIO`) every further
  start, also after a stop, so the tracker resets the station as the driver's start does, up to three
  times, and logs each try. The controllers reconnect on their own.
- **How long a controller waits.** Awake controllers lying still reconnect without a press after at
  least 15 minutes without the station (5 minutes without the slow-advertising command, the longest
  tried). After hours without a link they connect only after a button press. The time between is not
  measured.
- **Firmware.** The station reports firmware 2.96, the version PICO OS ships.
- **The spidev.** The bus is a plain spidev, whose every transfer is full duplex: commands go out
  as transfers, zeros (no header, which the station ignores) ask for the station's next frame,
  polled every 2 ms. The station returns its last frame on every transfer until it has another, so
  only a frame that differs from the last is new.

The same link carries station and controller firmware updates. The C module
([pico4_station.c](../src/monado/pico4_station.c)) and the `pico4-station` tool therefore build
frames only from an allowlist:

| Command | Payload | Purpose | C module | `pico4-station` |
| --- | --- | --- | --- | --- |
| `FA` | bytes 37–38 of the acknowledged frame | acknowledge | yes | yes |
| `0D` | exactly `00 00` (the controllers; other first bytes are trackers) | controllers to slow advertising, before the station's power goes | yes | yes |
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
| `02`, `08`, `03` | exactly one byte, `01` left or `02` right (the trackers' higher bits and USB pairing's `03` with a radio address are refused) | enter pairing, stop pairing, unpair ([pairing](#pairing)) | – | yes |
| `01` to `02`/`03` | play (`01`, or all zero to stop), amplitude, u16 duration in ms | controller vibration, as PICO OS's OpenXR haptics | yes | – |
| `02` to `02`/`03` | exactly one byte, the chunk index 1–12 | the controller's calibration record (read-only, as PICO OS on every connect) | yes | yes |
| `03` to `02`/`03` | none | the controller's info (read-only; PICO OS sends it, these controllers do not answer) | – | yes |

Neither can send firmware, bootloader or flash commands, and neither touches the boot pin. The C
module's unit test checks its frames against golden bytes from the hardware-verified Python tool
(the vibration frame against the layout above) and that every other command is refused.

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

### Pairing

The pairing lives in the station and the controllers, not in PICO OS's data: controllers paired
under PICO OS connect on the installed system as they are. A replacement controller, one paired to
another headset, or one unpaired from its own buttons (Home and Trigger held for 4 s; the station
reports `A5 05`, hand in byte 2) needs pairing again. `pico4-station pair left|right` does it as PICO
OS's station service (`EnterPairing`, `StopPairing`, `UnbondController` in
`libstationopticsservice.so`):

1. `02` with the controller's mask (`01` left, `02` right). The station answers `BB 01 02`, byte 4
   `01` when it listens for that controller.
2. The owner holds the controller's Trigger and Home for 6 s (PICO OS's instruction). The station
   reports the controller with `A5 02`, status 1, like any connect.
3. `08` with the same mask ends the pairing, also after the controller connected or the time ran out
   (60 s by default), so that the station does not stay open to other controllers.

`pico4-station unpair left|right` sends `03` with the mask (`BB 01 03`, byte 4 `01`: done); the
controller then connects only after a new pairing. The driver reads a newly paired controller's
calibration from the controller on its connect, as for any other; it uses no file per controller.

The station has one owner. With the session stopped `pico4-station` sends the commands itself; while
a session runs its driver owns the station, and `pico4-station pair`, `unpair` and `status` go to the
driver's pairing socket, `$XDG_RUNTIME_DIR/pico4-station` (the session user's and root's only). The
driver's station thread serves it between its reads (`pico4_station.h`): one request per connection
(`pair left|right [SECONDS]`, `unpair left|right`, `status`), the same three commands with the same
waits. A pairing answers `pairing HAND` when the station listens and `paired HAND SERIAL` or
`timeout HAND` once it has stopped listening; a client that hangs up stops it. The onboarding
([session.md](session.md#onboarding)) pairs through it.

Verified in the running session: a left controller that had lost its pairing connected about 10 s
into a pairing (trigger and PICO button held), then tracked and vibrated; the right controller,
switched on while the station listened for the left one, reconnected with its pairing.

## Tracking cameras

The four OV7251s (640×480 mono, global shutter, RAW10) run on V4L2 through CAMSS
([pico4_cam.c](../src/monado/pico4_cam.c), [kernel.md](kernel.md#results)): media
links and formats per slot (CSIPHY, CSID, VFE RDI, 640×481 Y10), multi-planar buffers in a ring, Y10P
unpacked to 16 bits. A frame's time comes from its buffer (the CSID's hardware start of frame); the
QTimer time follows from an offset measured once a second. The sensors' control port belongs to the
station, which configures them over its own I2C and sends the frame sync:

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
  exposure. A camera that delivers nothing while the others do has a stuck link; then, and after 3 s without a set or 1 s without any frame,
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
  and 5 s limit; with them it recovers within 1–2 s, and leaving a game keeps tracking.

[`pico4-cam`](../src/xr/pico4-cam.c) tests the cameras on their own:

- `capture`: resets the station, starts the frame sync and streams until each camera delivered
  N frames. Saves a scene frame of each camera (8-bit PGM of the scene rows, and the whole buffer
  as 16-bit raw, metadata line first) and prints per camera the frames, errors, frame rate from
  the start-of-frame timestamps, their monotonicity, the sample statistics and the frames of
  each exposure phase. `--stats-log FILE` logs per frame the slot, start-of-frame time,
  brightness, latency, frame counter, phase, exposure and gain; `--exposure LINES --gain G` sets the
  station exposure.

## Passthrough

SteamVR shows the forward cameras as its room view (the dashboard's camera button, or the
compositor's `set_camera_roomview_enabled`). How the Frame provides one:

- **Driver.** The Frame's HMD driver (`driver_cv`, not installed here) implements OpenVR's
  camera component (`IVRCameraComponent_003`) and a private passthrough interface; Valve's
  XR service captures the passthrough cameras for it. The Frame's settings turn the camera and
  the compositor's room view on (`camera.enableCamera`, `roomView`, `enableCompositorRoomView`).
- **Not passthrough.** `steamvr-v4l2cam` copies SteamVR's view out to a V4L2 loopback device (a
  virtual webcam); it stays masked, as does `steamvr-v4l2loopback`.
- **Signing in.** The Steam client's VR sign-in does not use the cameras: it offers "Mobile App -
  Tap to confirm" (Bluetooth LE), "Mobile
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
  compositor connects ("Depth mesh block queues connection successfull"). Without the HMD's
  `Prop_SupportsRoomViewDepthProjection_Bool` it renders the camera view without depth
  reprojection: each eye sees its own camera, so the room is still seen in stereo, and close
  objects double. With the property and no mesh, it draws black: the HMD reports the property only
  where the image carries PICO OS's pipeline.
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
  A geometric stand-in (a sphere around the head) is worse than no mesh when worn: head
  movements are over-corrected. The mesh has to be the measured room, as on the Frame.
- **PICO OS's see-through.** On the PICO 4, `pxrseethroughservice` feeds `libphotoid.so` each
  pair of the grey forward cameras with the tracker's result at the pair's time, and the
  tracker's map points while it tracks. Its pipeline (`vst_releaseA`, the only one it loads)
  computes depth with a neural network (`FLNet_lite_v2`, SNPE on the Compute DSP). It also sets up a
  depth-from-stereo session on the CVP with its buffers (256×128), which it never runs:
  `cvpDfs_Sync` is not called. It fills gaps from the map points and makes a hemisphere mesh around the head
  that ends at the ground. The ground is the tracker's own: with ground detection on
  (`setAlgorithmParameters("plane", "1")`), `libSlam` reports the floor's height and status in
  each pose result, and `libphotoid` takes them from the left image's result. The guardian
  service draws the mesh per eye, coloured from the RGB camera, with the predicted display
  pose. (`libUndistort` serves only the undistorted RGB image to apps.)
- **The same mesh here.** `pico4-slamd` runs `libphotoid` in the tracker's root and feeds it
  the same way ([vst.c](../src/stockslam/vst.c)): cam3 and cam2 after each scene set, with
  `libSlam`'s result nearest the set's time (a ring the IMU thread fills), and the map points
  from `libSlam`'s point callback. The tracker's stand-ins
  ([stubs.c](../src/stockslam/stubs.c)) answer the CVP session, so neither `/dev/cvp` nor the CVP's
  firmware is needed; `cvpQueryImageInfo` stays `libcvp2`'s own calculation, and a call of
  `cvpDfs_Sync` would fail loudly. SNPE's DSP
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
  without the prediction its reported poses carry (with it, the room
  trailed the head's turns). Otherwise nothing is copied.
- **Client.** `VR_Init` as a background application, then `IVRCompositor_029`: the frames'
  import goes over `vrclient`'s connection to the compositor, which only asking for a compositor
  interface opens (without it the import request gets no answer). Then the passthrough
  interface, `SetGraphicsDevice` with its own Vulkan device on Turnip, and `SetupCameraFrames`
  with three linear R8 images per camera, exported as dma-bufs (DRM format modifier, linear).
  `vrclient` imports an OPAQUE_FD timeline semaphore from the compositor into that device, so the
  device enables the external semaphore extensions, which Turnip on msm provides. The view state's "available"
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
  extremes), so it is shown about 40–80 ms after it. Worn with the RGB camera on the depth mesh
  and the camera pose in raw space, turning and nodding show the room fixed in place, without
  warp or visible lag. With colour and the mesh on for 60 s, the still headset's stock tracker
  lost no pose and stayed within 0.5 mm and 0.10° of its start (0.4 mm and 0.04° with the room
  view off); `pico4-slamd` then takes 104 % of one core against 54 %.
- **Colour from the RGB camera** (the default; `PICO4_ROOMVIEW_RGB=0` keeps the grey pair).
  PICO OS colours its see-through from the IMX471 RGB camera, every frame with its own capture
  pose, onto the depth mesh; the grey pair only builds the mesh. Here:
  - The IMX471 streams in the tracking cameras' capture as sensor slot 4
    ([pico4_cam.c](../src/monado/pico4_cam.c)) through its own driver, `pico4-imx471`
    ([kernel.md](kernel.md)): 2328×1748 RAW10 (RGGB, the 2×2-binned array)
    free-running at 50 fps on four lanes of CSIPHY 1 at 2064.8 Mbit/s, CSID 4 and the third
    IFE-lite, whose CSID writes the 16-bit samples the conversion takes, as PICO's stack has it.
    50.01 fps measured. Exposure and gain are its V4L2 controls.
  - The driver converts each frame on a thread of its own, pinned to the Cortex-A77 cores 4–6
    (9 ms a frame there, 22 ms on the little cores): each 2×2 cell one pixel (1164×874), black
    level 64 off, grey-world white balance, sRGB, full-range BT.601 NV12. The camera thread hands
    the frame over and gets it back after the conversion. An auto exposure aims at 18 % grey: a
    flicker-free 1/100 s first, then gain up to 16×, then up to 20 ms. 47 of the 50 frames a
    second reach the ring, 30.5 ms (median) after the middle of their exposure at the longest
    exposure; the conversion takes 44 % of a core.
  - The sensor streams only while the room view shows it, as PICO OS opens the camera only for
    its see-through: 5 s after the room view closes the driver pauses the IMX471's stream
    (`pico4-imx471`'s paused control) and resumes it when the room view opens.
  - The lens is PICO's omni model with its "fisheye62" distortion: the unified-model step with ξ,
    then θ = atan(|m|) of the omni-normalised point m, θd = θ(1 + k1θ² + … + k6θ¹²), the
    tangential terms on m, and the focal lengths. [pico4_tcam.c](../src/monado/pico4_tcam.c)
    projects it and fits KB4 for the room view's metadata.
  - `pico4-roomview` sets the frames up as NV12 (one dma-buf for both planes, linear) with both
    camera slots backed by the same images, and publishes each frame to both: the compositor's
    only colour input. A colour-bar test pattern (`pico4-roomview --test-pattern`) shows in the
    right colours.
  - The scanout is black above the camera's field of view; worn none of that shows, also looking up.
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
  `cdsprpcd`. [tooling/assets.py](../tooling/assets.py) (`python -m overpressure assets`) takes them from
  the stock firmware ([development.md](development.md#stock-firmware)), `build-userspace.sh
  --stock-slam` installs them. Two stand-ins of this
  project replace Android pieces: a `libandroid.so` with the two trace calls the tracker uses, and
  `libpico4-slam-stubs.so`, preloaded, which sends Android's log calls to the journal and answers
  property lookups with nothing (the tracker's defaults). Programs in the root are built with the
  host's clang for `aarch64-linux-android29` against the root's own libraries, without an NDK
  ([src/stockslam](../src/stockslam)).
- **Its runner.** [`stock-slam`](../userspace/rootfs/usr/libexec/pico4/stock-slam) starts a
  program of the root in a private mount namespace, chrooted, with `/dev`, `/proc` and `/sys`
  bound in, the headset's factory calibration (persist's `pvr/`) at `/mnt/vendor/persist/pvr` and `/mnt/vendor/pvr`
  (where the tracker reads it and its gyro bias table) and `/data` and `/tmp` as tmpfs.
- **The CDSP.** [`pico4-cdsp.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-cdsp.service)
  boots it through `remoteproc-boot cdsp`, as the SLPI; its firmware (`cdsp.mdt`, `cdspr.jsn`) comes
  from the modem partition of PICO's public package. `pico4-cdsprpcd.service` runs `cdsprpcd` in the root: without it the
  DSP's loader stalls on the tracker's first module. The tracker then opens a signed user PD on
  the CDSP (`fastrpc_shell_3`) and loads its modules there itself, through the stock
  `libcdsprpc.so` and the bridge below.
- **The FastRPC bridge.** The stock libcdsprpc speaks the vendor kernel's FastRPC ioctls on
  `/dev/adsprpc-smd` and allocates through ION on `/dev/ion`; Valve's kernel has the upstream
  FastRPC driver (`/dev/fastrpc-cdsp`) and DMA-BUF heaps. `stock-slam` therefore preloads
  `libpico4-fastrpc-bridge.so` ([fastrpc_bridge.h](../src/stockslam/fastrpc_bridge.h)), which gives
  the vendor nodes descriptors of their own and translates the requests the stock libraries build
  (read from the stock `libcdsprpc.so` and `libion.so`): GETINFO opens the domain's upstream node
  onto the descriptor, INIT and INIT_ATTRS become INIT_ATTACH, INIT_CREATE or INIT_CREATE_STATIC,
  the INVOKE family becomes the upstream INVOKE with 24-byte arguments, MMAP and MUNMAP keep their
  layouts, CONTROL, SETMODE and MUNMAP_FD are answered (CONTROL says the kernel allocates:
  `FASTRPC_CONTROL_KALLOC`, so the library maps without a buffer of its own and upstream's MMAP allocates), and ION allocations come from
  `/dev/dma_heap/system` (the legacy ION probe fails, so libion takes the new ABI). Its translations
  are tested natively before the bionic library is built. The DSP asks `cdsprpcd` for memory
  (`apps_mem`), which the library maps by descriptor at its own address with flags 0; Valve's kernel
  maps such a client buffer through patch 0012, as the vendor driver does. On the headset
  `cdsprpcd` attaches to the CDSP's guest OS through the bridge, maps the DSP's memory and opens
  its listener, uses no measurable CPU, and the IMU keeps its 1 kHz stream beside it;
  the DSP's context banks are coherent, as in the stock device tree, or it reads stale frames.
- **pico4-slamd.** [`pico4-slamd`](../src/stockslam/pico4-slamd.c)
  ([`pico4-slamd.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-slamd.service),
  wanted by Valve's session and Monado) creates `/dev/shm/pico4-slam` (group video, mode 0660)
  and feeds the tracker as PICO OS's tracking service does: `createHandle("")`,
  `headSensorModel` `icm4x6xx`, `startAlgorithm`, the result callback, `start6DOF` `6`; from one
  thread every IMU sample as {gyro, accel, temperature} with its time and the pose after it, from
  another each frame set once the IMU is 30 ms past it (on PICO OS the camera's latency does that;
  earlier, the tracker waits for the IMU in vain on nearly every frame). `updateImage` only
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
saved room's boundary, in this session's world, to `boundary/stdataforalgorithm.txt`. It also has a
callback for this, `RelocResultCallbackRegister`, which it calls only with a flag in its
configuration set and does not call here, so `pico4-slamd` watches the file. A boundary
file holds `vertexcount` and the points as `(a, b, c)` with b up; the tracker's world point is
(b, −a, c): `libSlam`'s writer prints a world point (x, y, z) as (−y, x, z), and its readers
store (a, b, c) as (b, −a, c). The map is saved in the session's world: the tracker restarted with
the headset untouched relocalises with the same transform (−100.45° and −100.41°, shifts within
2 mm).

- **Where it lives.** [`stock-slam`](../userspace/rootfs/usr/libexec/pico4/stock-slam)
  binds `/var/lib/pico4-stock-slam/{maps,boundary}` (the data partition, mode 0700) onto those
  directories of the root, so the map survives reboots and system updates; it never leaves the
  headset.
- **A reference shape.** Every map is saved with its room's reference quadrilateral as its
  boundary, in the room's world, 1.5 m below where the room's first session started. The shape
  carries the room's id ([room.h](../src/stockslam/room.h)): three sides of 1.5 m + v × 0.2 mm,
  v 12 bits each; a turn and a shift keep the sides, and the tracker keeps a map's boundary
  through every save and merge. When the tracker writes the copy after a relocalisation,
  `pico4-slamd` reads the room's id from the copy's sides and fits the copy to the room's shape
  (a turn about the up axis and a shift, both worlds being gravity-aligned; accepted below 5 cm
  residual): that is the transform from the session's world to the saved room's, whichever saved
  map the tracker found. It publishes it in the shared memory and writes the copy to
  `stdata.txt`, so the stop saves the map again; the driver puts every pose of that generation
  into the room's world. The tracker relocalises about 1 s after its first frames. Without a
  relocalisation 10 s after its first frame set (the cameras can start 10 s late after a failed
  sync) the session is a new room with a new random id, saved with its shape as it is; a later
  relocalisation (a covered start) moves it into the saved room's world and universe then.
- **SteamVR's universe.** The room's identity is `room-` and its id; its hash is the room's
  universe (never 0, 1 or 424242, which `vrserver` reserves). Once the room is settled,
  `driver_monado` switches universes as the Frame's driver does, through `vrserver`'s chaperone
  server (`IVRChaperoneServerDriver_XXX`, unversioned): slot 1 restores a universe that
  `chaperone_info.vrchap` holds (`bool (uint64 universe, const HmdMatrix34_t *persistentMapFromMap,
  bool refine)`, identity and false), else slot 0 creates it (`void (uint64 universe, bool
  keepOrigins)`, true): a 1 m default play area, room setup not done, so SteamVR asks for the
  playspace setup, and its entry written to the file. `vrserver` then sets the HMD's
  `Prop_CurrentUniverseId_Uint64` itself; it re-reads it every frame and, for a universe the file
  does not hold, keeps no standing space at all, so setting only the property would leave a
  new room's playspace setup without a floor. SteamVR keeps a play area per room, back in its
  place whenever the tracker relocalises in that room. Until then the universe is 2. A new room's
  universe waits for a wearer: a headset started on a table can miss the tracker's 8 s to relocalise
  and find its saved room after that, and each such start would leave an entry in the file. The tracker's own
  map name (`getRelocMapInfo`, the name of its current map object) is no identity: after a merge
  it can be the session's new map, and a saved room then came back as a new universe. Rooms saved
  with the plain 2 m square from before the ids (it reads as a reserved id) get an id and shape at
  their next relocalisation and keep the identity their universe came from, the map name then
  reported, in `boundary/pico4_rooms.txt`. A tracker crashed with SIGSEGV in a session comes back
  in the same room and universe within 13 s.
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
  on the Frame too, and on the Frame a controller touching the floor pushes the target down
  (the lowest controller in room-setup space).
  Here `libSlam`'s ground detection gives the floor: `pico4-slamd` turns it on (`plane` 1,
  `setPlaneDetect`) and passes each result's ground status and height with the pose. The floor
  lies that far below the tracker world's origin. While the detection runs, each plane it fits
  replaces the last (status 1; 1.3 m below the head if it fits none). After a start the height
  comes about a second in and then stays for the whole session. Status 2 and 3 are the height a saved map carries
  and are not used. PICO OS's own services take the first height as it is
  (`sys.pvr.floorheightforalgorithm`). What the cameras see in that first second decides it: with
  the user standing in one room, starts gave 1.61–1.65 m and 1.17–1.26 m (a bed or table). So
  each room-setup step that detects the floor (3, 6, 7) starts the ground detection again
  (`pico4_hmd_detect_floor`, the feed's `floor_requests`), and the floor comes from what the
  head sees during the setup. `driver_monado`
  ([monado-steamvr-floor.patch](../userspace/pkgbuilds/pico4-monado/monado-steamvr-floor.patch))
  does what the Frame's driver does, with that floor in its raw space (room and tracking origin
  applied); in a known room a playspace setup committed the detected floor to the millimetre.
  Slot 40 belongs to this `vrserver` build and has to be checked again with every new SteamVR.
- **Recenter.** Holding the PICO button (the dashboard's quick recenter) runs room-setup step 1
  and resets SteamVR's seated zero pose only: `chaperone_info.vrchap` gets the universe's new
  `seated` pose, its `standing` pose stays ("Standing transform was not updated"), so the play
  area does not turn, and after a SteamVR restart the recenter is gone. The Frame keeps the
  chaperone universe (bounds and `standingAppWorldFromRawAppWorld`) in its XR service's
  persistent world with the map instead; here SteamVR's `chaperone_info.vrchap` keeps it.
- **Turned relocalisations.** A play area drawn in a room stays where it was drawn after a
  tracker restart that relocalises with any turn (checked worn at -3 and 165 deg) and after a
  SteamVR restart. Play areas drawn before the room ids, against the plain square, can be off
  by a quarter turn (the square fits four ways); drawing them once more fixes them.
- **Verified.** A drawn play area comes back after a reboot exactly where it was drawn (the
  tracker relocalises in the saved room, and SteamVR finds the room's universe in
  `chaperone_info.vrchap`), and its boundary appears when walking toward its edge. With no play
  area for the current universe, SteamVR asks for a space setup when a game is launched.

### Frame times

The tracker, Basalt and the controller tracker take each frame's time from its buffer: the CSID's
hardware start of frame (patch 0016) on `CLOCK_MONOTONIC`, carried in QTimer ns onto the IMU's
clock, as PICO OS does. The time of a frame is the middle of its
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

  The HMD hands out the pose 12 ms later than the requested time (`PICO4_PREDICT_MS`), predicted
  from the newest state with its velocities.
- **Prediction.** SteamVR asks `driver_monado` for the present pose and extrapolates it itself by
  the HMD's `SecondsFromVsyncToPhotons`, 11 ms (10.6–12.1 ms measured). The driver predicts the
  rest: the time from handing out a pose to the vblank that starts its scanout. A latency probe
  measures it: the driver turns the pose it hands out by 5° at known times
  (`PICO4_LATENCY_PROBE_MS`, a period that is no multiple of the refresh sweeps the steps across
  it), and the image on the plane shows each step at a vblank. A pose handed out just before the
  compositor takes one reaches the vblank 12 ms later, and the display's pacing adds its lead of
  5 ms; steps arrive 17–28 ms after they start, 24 ms in the median. An
  image in the panel's flash is thus 28 ms old, of which the driver predicts 17 ms and SteamVR 11
  ms. Too much prediction overshoots at the end of a fast turn and the view springs back, too
  little trails the head: a 10 ms error is 2° at 200°/s.
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
- **Battery** (type 2): one byte, a fifth of a full charge per level, which goes to SteamVR as a percentage.
  PICO OS's controller service counts level 1 as low. Its `SetMotor` limits a low controller to 70 % only
  where the strength is 100 %; the driver runs at PICO OS's default of 75 %, so the limit never applies.
  Its settings draw six steps, 0 to 5; levels 1, 2 and 4 have been seen.

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
not as Monado's minimum duration, which would buzz for half a second at every hover. SteamVR's
own test pulses are 2.5 % strong.

Either pose is the aim pose: PICO OS publishes the tracked pose as the aim and derives the grip as
aim × T, where T is a position and a quaternion (w, x, y, z) per hand, left block first, that its
tracking service takes from `sys_tracking_aim_to_grip_transform` or, without that key, from
built-in values (a turn of about 34° about x and about 5 cm of lever arm on this firmware).
[`tooling/aimgrip.py`](../tooling/aimgrip.py) reads the built-in values from the 5.13.7
`libpvrtrackingservice.so`, the assets command writes them to
`calibration/controllers/aim-to-grip.json`, and the driver composes the grip pose the same way,
adding ω × lever arm to its linear velocity; without the file the grip pose is the aim pose. The
SteamVR driver reports the aim pose as the controller's pose, the frame of the render models.

The render models are PICO OS's own: `/system/pre_resource/data/misc/user/controller/Neo3Phx/`
holds a textured OBJ per hand (`60.obj` left, `61.obj` right, in centimetres) whose origin and
axes are the published aim pose: with no transform, PICO OS's LED rings, turned into the aim
frame and less the lever arm, lie within 1.5 mm of the surface.
[`tooling/controllermodel.py`](../tooling/controllermodel.py) converts them into SteamVR render
models (triangulated OBJ in metres, material, texture, and components for the aim and, from
`aim-to-grip.json`, the grip), the assets command writes them to `rendermodels/`, and the
image build copies them to `/usr/share/steamvr-monado/resources/rendermodels/`, where SteamVR
finds `{monado}pico4_controller_left` and `_right`.

The battery shows where PICO OS has its bar, a strip on the controller's base (the models' group
`Power`). SteamVR's system UI draws its controller status panel on a model's `status` component, so
that strip becomes the component, a mesh that is hidden unless the panel is on it, with its UVs on the
panel's battery pips. SteamVR shows four steps: one for 20 %, two for 40 %, three for 60 % and 80 %, four
for 100 %, and nothing at level 0.

### Controller tracking

Each controller carries a ring of 20 infrared LEDs, which light in the station's short controller
exposure, every other camera frame (4 lines at gain 26). PICO OS's controller tracking library
(`/system/lib64/libpico_controller_tracking.pxr.so`) holds a nominal model of each ring: 20
points in metres, between 1.5 and 9.8 cm from the model's origin, the left ring the right one
mirrored in y, and one unit normal per LED. [`tooling/ledmodel.py`](../tooling/ledmodel.py) reads
both by their addresses in the 5.13.7 library and refuses anything that is not such a ring;
the assets command writes them to `calibration/controllers/led-models.json`, installed with
the rest of the calibration. They are vendor data and stay out of the repository.

On every controller connect, as PICO OS does, the driver's station thread reads that
controller's calibration record from the station ([Controller radio](#controller-radio-station))
and hands it to the tracker with the serial from the connect notification:

- the IMU calibration (gyro and accelerometer matrices and biases, applied to the raw samples);
- the controller's own 20 LED positions, which replace the nominal ones (normals and the
  identification table stay);
- its temperature-binned gyro bias table, `calibration/pvr/<serial>.txt` from persist, read
  only (PICO OS also writes it back). The files there are root's alone, so `pico4-persist` copies
  them to `/run/pico4-controllers/` for SteamVR's driver, which runs as the session's user.

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
- **Controllers at rest.** A hand that was still for 3 s, or came back from an IMU gap, tries no
  P3P until it has moved for 3 s without pause or is tracked (as in PICO OS's library). P5P stays
  on, but controllers lying side by side look like one cluster of 17 LEDs or more, which only P3P
  takes. So controllers put down together are found when they are picked up, not before; one
  that stays tracked remains tracked at rest.

In the driver, `pico4_slam.c` collects the LED exposures of the four cameras as sets. It converts
them to 8 bits by dropping the two lowest bits, as `pico4-cam capture --record-leds` stores them,
and hands them to the tracker's thread. That thread waits up to 60 ms for the IMU samples up to a
set's exposure time and takes the head pose at that time from the HMD's own track. The aim pose
is the LED-model frame turned like the 3DoF axis mapping, offset by PICO OS's lever arm of
about 2 cm, which its client adds in that frame after prediction.
[`tooling/leverarm.py`](../tooling/leverarm.py) reads it from the 5.13.7
`libpico_controller_tracking.client.so` and the assets command writes it to
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
in Python on any system; the command first verifies the stock folder against its manifest and
never touches the headset:

```sh
python -m overpressure assets sensor-data
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
python -m overpressure assets stock-slam
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

- **`pico4-sensors`**: hexagonrpcd from a pinned linux-msm commit plus this project's patches.
- **`pico4-basalt`**: Basalt and the submodules the library needs, at pinned commits.
- **`pico4-monado`**: Monado plus `src/monado` and `src/xr`. Its build runs the driver's C tests,
  because the userspace build uses `--nocheck`.

The host tests (`python -m unittest`) cover `pico4-imu`'s protobuf/QMI
encoding, a simulated DSP (lookup, attributes, streaming, disable), the IMU acceptance limits,
the orientation filter and the runner's validators.

## Run

The sensor units start at boot. The fan control ([cooling.md](cooling.md)) keeps the 30 s Valve
session check at about 64 °C and about 10 minutes of a worn game at 75.6 °C.

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

The stage-2 checks, `python -m overpressure dev check` on the installed system
([hardware-testing.md](hardware-testing.md)), test an archive built with sensor data
after the userspace check: they record the sensor units' status and `pico4-imu list accel gyro`,
then require `pico4-imu stream --rate 500 --seconds 10 --check`. They continue with Valve's
session ([session.md](session.md#acceptance)); Monado's own OpenXR
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
systemctl stop pico4-monado.service
systemctl start pico4-session.service
```



The driver reads its options from the environment of the process that runs it: `monado-service`
(`pico4-monado.service`) or SteamVR's `driver_monado`, not the OpenXR client. For example,
Basalt's trajectory as CSV files (`tracking.csv` among them) from a headless pose session, with
Monado's `SLAM_WRITE_CSVS`/`SLAM_CSV_PATH` and the null compositor instead of Vulkan's display:

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
| `PICO4_CALIBRATION_DIR` | `/usr/share/qcom/pico4/calibration` | factory lens and camera calibration |
| `PICO4_LENS_CA` | off | chromatic aberration correction |
| `XRT_COMPOSITOR_COMPUTE` | 0 (service) | Monado's compute composition path |
| `PICO4_SLAM` | on | 6DoF |
| `PICO4_SLAM_BACKEND` | `stock` | `stock`: PICO OS's tracker through `pico4-slamd`; `basalt`: Monado's Basalt |
| `PICO4_SLAMD_LSC` | the vendor's | `pico4-slamd`'s lens-shading table in `/system/etc/pvr/config` instead of the one the camera modules' vendor picks; set in the unit's environment |
| `PICO4_SLAMD_DEBUG` | unset | `pico4-slamd` passes the tracker's debug and verbose log lines to the journal |
| `PICO4_SLAMD_AE` | on | `0`: no PICO OS auto exposure in `pico4-slamd`; the driver's own sets the exposure |
| `PICO4_SLAMD_RESTART_S` | 10 | seconds a tracker that lost its confidence stays lost before `pico4-slamd` restarts it, as PICO OS's service; `0` turns the restart off |
| `PICO4_SLAMD_RELOCALISE_S` | 10 | seconds from the tracker's first frames until `pico4-slamd` takes the session for a new room, without a relocalisation |
| `PICO4_CAMERA_HZ` | 50 | the tracking cameras' rate: 50 as stock, or PICO OS's other rate, 60 (scene frames at 30 Hz instead of 25) |
| `PICO4_SLAM_CAMS` | `0,1,2,3` | factory cameras for Basalt in tracker order, at least two; Basalt matches the first against the others |
| `PICO4_TCAM_SLOTS` | `0,2,1,3` | capture slot of factory cameras cam0–cam3 |
| `PICO4_SLAM_UPRIGHT` | on | turn images that are upside down relative to the first camera's |
| `PICO4_SLAM_DIVISOR` | 1 | use every Nth scene frame set |
| `PICO4_SLAM_IMU_DIVISOR` | 2 | use every Nth head IMU sample |
| `PICO4_SLAM_MAX_EXPOSURE` | 400 | longest station exposure, sensor lines |
| `PICO4_SLAM_EXPOSURE_CENTRE` | on | frame times at the middle of the exposure instead of the start of frame |
| `PICO4_FUSION` | on | the IMU-rate head pose corrected by Basalt's poses; off: Monado's prediction |
| `PICO4_FUSION_POSITION_MS` | 150 | time constant pulling position and velocity to Basalt's |
| `PICO4_FUSION_SMOOTHING_MS` | 50 | time over which a correction reaches the output |
| `PICO4_FUSION_CSV` | unset | directory for `fusion_slam.csv` (each Basalt pose: latency, disagreement, outcome, velocity disagreement with the IMU, speed) and `fusion_pose.csv` (each pose handed out) |
| `PICO4_PREDICT_MS` | 12 | hand out the head pose this much later than asked for, to cover the time to the vblank that SteamVR does not predict |
| `PICO4_LATENCY_PROBE_MS` | 0 (off) | latency probe for a headset lying still: 1 s phases (with 1000) of a 5° yaw step and of a reported 30°/s turn without turning; read from the scanned-out image |
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
pico4-cam capture --frames 1500 --out /tmp/cams   # all four at 50 Hz, JSON summary, one scene frame each
pico4-cam capture --exposure 300 --gain 32 --stats-log /tmp/frames.txt
pico4-station version                             # station firmware version
pico4-station listen --seconds 5                  # frames the station sends
```

## Verified results

On the headset, lying still unless stated otherwise. Current results on Valve's kernel:
[kernel.md](kernel.md#results).

| Check | Result |
| --- | --- |
| Sensors reported | ICM-42688 accel/gyro 12.8–512 Hz, ICM-42688 temperature, AK0991x magnetometer 10–100 Hz |
| `pico4-imu stream --check`, 500 Hz, 10 s | 5,066 samples per stream at 511.95 Hz, 0 gaps, 0 non-monotonic timestamps; \|g\| 9.796–9.797 m/s²; mean \|ω\| 0.005–0.006 rad/s |
| 1 kHz request | DSP configures 1000 Hz, measured 1023.9 Hz |
| `pico4-imu` orientation estimate | roll/pitch −11.1°/−83.6° against the accelerometer's −11.9°/−83.5°; uncorrected heading drift about −0.07°/s (−0.36° in 5 s, −0.69° in 10 s) |
| Guided 90° turn, edge-aligned (`pico4-imu`) | plateau +90.3 to +90.5° (scale error below 0.6%); turned back to the start, the filter heading read +0.35° after 40 s |
| OpenXR system | `Monado: PICO 4`, runtime `Monado(XRT) … v25.1.0`; orientation tracking, position tracking |
| Head pose, 3DoF, 60 s at 100 Hz | 5,882 locations, none invalid after the first second; rotation 0.009°, heading −0.006° (−0.0001°/s); 10 s runs drift 0.0002–0.0005°/s |
| Tilt against the accelerometer | within 0.2° (for example 9.12° against 8.96–9.11°, 4.02° against 4.06°); with Basalt drifting in front of a near surface 2.01° against 0.65°, heading 0.11°/s |
| Latency, sensor sample to pose | 0.94–1.16 ms |
| Learned gyro bias | (0.0039, −0.0009, −0.0056) rad/s in the head frame |
| Scanned-out image | left half red L, right half blue R; eye means for example (70, 40, 25) and (26, 41, 72) |
| Seen in the lenses | one layer: a steady image, red L only in the left eye, blue R only in the right eye. Two layers, head turning: the checkerboard has a stereo offset between the eyes, stays fixed in the room and moves smoothly, with no visible latency |
| Lens correction, seen in the lenses | grid lines and checkerboard edges straight, image steady at 90 Hz; colour fringes not judged (each eye showed one colour) |
| Field of view from the lens data | left eye −53.32/+53.38° horizontal, +53.58/−53.12° vertical; right eye −53.49/+53.17°, +53.41/−53.25° |
| IPD | 71.99 mm (Hall reading 888, consistent with the factory table) with the lenses at their widest, 72 mm |
| Station | power cycle, announcement and frame-rate answer every time; firmware 2.96 |
| Exposure through the station | scene frames mean 76 → 446 (of 1023) at 400 lines and 4× gain; LED frames 23–28 either way |
| Slot mapping | cover test: one slot dark per covered lens; a logo seen by the lower pair triangulates with a 0 mm ray gap at 0.14/0.16 m; with the 481-line capture (metadata line removed) a pen at 0.15–0.18 m triangulates with 1–2 mm gaps |
| Controllers in OpenXR | both as `PICO 4 left/right controller`; per controller about 500 IMU samples and 100 input packets per second |
| Controller inputs, each pressed in turn | trigger, grip, A/B/X/Y, menu, home, stick click and touch (A/X, B/Y, stick, thumb rest) decoded as pressed |
| Sticks pushed right, left, up, down, both controllers | x (record byte 2): 255, 0; y (byte 1): 255, 0 |
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
| Basalt VIO settings | Basalt logs "Using VIO config /usr/share/pico4-basalt/pico4-vio.json" when the PICO 4 driver starts SLAM |
| Exposure phases, `pico4-cam` with each set varied (80 frames per camera) | scene frames take the first set (60–400 lines, gain 16–128); LED frames 4 lines at gain 26 with the second set at 0/0, 2/20, 4/26, 8/30 or 10/32; LED frames peak at 344–377 of 1023, no sample saturated, while scene frames saturate 14–4,650 samples per frame |
| Metadata line, `pico4-cam` | decodes to the commanded exposure and gain (300 lines and gain 32, 120 lines and gain 16); every camera 50 scene and 50 LED frames of 100; LED frames 4 lines at gain 26; the 481st captured line continues the scene (mean row difference 8.8 against 8.4 one row up), 0 errors at 49.8 Hz |
| Fusion, headless, headset still, 30–40 s | agrees with Basalt within 0.3–0.6 mm (median), latency images to fusion 76–92 ms (median); a forced Basalt reset (`PICO4_SLAM_RESET_AFTER_S`) restarts it without a jump; velocity disagreement with the IMU at rest: 99th percentile 0.025 m/s |
| Fusion against Basalt, worn | the handed-out pose matches Basalt's at a 0 ms time shift (rotation within 0.35°), no lag of its own; still, it moves 0.36 mm and 0.024° around its 200 ms average (Monado's prediction 0.21 mm and 0.013°) |
| Staged jiggle, headset still, 4 × 27 s | Basalt 0.22–1.06 mm and 0.004–0.032° around its 200 ms average; fusion output 0.06–0.21 mm and 0.001–0.003°; SteamVR's extrapolation with the reported velocities 0.06–0.29 mm; slow drift 11–76 mm peak to peak (Basalt's) |
| IMU noise A/B (ours against PICO OS's values), still and worn blind | Basalt with PICO OS's values: 0.49–3.02 mm jiggle against 0.10–0.29 mm, two runaways worn against none; the user found ours calmer |
| `libSlam`'s pose against the gyro, headset still, 30 s (fit R² 0.999) | orientation at stamp T = the gyro integrated up to T − 9.5 ms + `angular` × 6 ms; `angular` is a 36-tap FIR over the gyro from T − 11 to T − 46 ms, gain 1, about 3 ms behind its stamp below 8 Hz, 6.5× at 30 Hz, 8× at 50 Hz; at a constant rate the pose is about 3.5 ms behind its stamp; newest pose stamped 7.9–9.0 ms in the future, newest IMU sample 0.8–1.7 ms old |
| Latency probe on Valve's kernel and Mesa, compressed scanout, 89 flips/s, period 1003 ms, 23 steps | pose handed out to the vblank that shows it 12–23 ms, 18 ms median; commit to vblank 10 ms; SteamVR extrapolates a reported turn by 10.6–12.1 ms (linear scanout) |
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
| Station power cycled 10 times, with awake controllers lying still | both reconnect each time within 0.3–0.4 s of the power, and neither drops in the 5 s after |
| Station off with awake controllers lying still, then on | after 30 s, 2, 5, 10 and 15 minutes with the slow-advertising command, and 30 s, 2 and 5 minutes without it: both reconnect within 1.3 s, without a press |
| Room view in Valve's session (`pico4-roomview`, still headset) | the compositor imports the frames ("Late initialization successful"); frame dumps show the room in both eyes, the dashboard over it; 25 frames per second per camera published, none skipped; with the room view on for 60 s the stock tracker lost no pose and stayed within 1.3 mm of its start (1.1 mm with it off), no tracker warnings |

## Next steps

See [Open work](roadmap.md#open-work).
