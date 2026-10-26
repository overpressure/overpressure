# Roadmap

The project runs Linux, a SteamOS-compatible userspace and Valve's VR session on the original
PICO 4 (2022, codename phoenix, Snapdragon XR2), always on its stock Android kernel
(4.19.81-perf+, PICO OS 5.13.7 and 5.13.8) and stock device trees. The plan has six steps,
from a recoverable development device to Valve's VR session, and follows the strategy of the
background research in [research.md](research.md). All six steps work on the tested headset,
an original PICO 4 on PICO OS 5.13.7. Open work: cooling, performance, controller positions and
the installed system's way back and power handling.

## Status

| Step | Status | Document |
| --- | --- | --- |
| 1. Recoverable development device | Works: EDL backup, engineering ABL, persistent unlock, stock restore verified by readback | [device-preparation.md](device-preparation.md) |
| 2. Minimal Linux | Works: root shell over USB-C, read-only storage, automatic return to PICO OS | [stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md) |
| 3. Native graphics | Works: Turnip Vulkan on KGSL; both panels at 90 and 72 Hz through the stock KMS driver | [graphics.md](graphics.md) |
| 4. SteamOS-compatible userspace | Works: Holo Core aarch64 with the Frame's systemd 257.7, no failed units | [userspace.md](userspace.md) |
| 5. Native XR hardware | Works: head IMU, display, lens correction, tracking cameras, 6DoF head tracking, 3DoF controllers. Open: controller positions | [xr.md](xr.md) |
| 6. Valve's VR session | Runs: SteamVR on both panels at 90 fps on the Steam Frame's compositor path. The Steam client signs in, games start from its library. Open: performance, cooling | [session.md](session.md), [cooling.md](cooling.md) |

Each step's document holds its design, measurements and reproduction steps.

## Constraints

- **Stock kernel.** No kernel or device-tree changes. The kernel loads only modules signed with
  PICO's per-build key, so its gaps are bridged in userspace: udev creates the device nodes
  (no devtmpfs), the QRTR name service runs in userspace, FastRPC uses the downstream
  `adsprpc` ABI, a preloaded library provides System V semaphores, and a Vulkan layer shares
  GPU semaphores between processes over sync files. systemd stays at 257.x: 258 needs Linux
  5.4.
- **Read-only storage.** Both stages switch every physical block device read-only. Only the
  explicit, backup-validated flash and restore paths write to the headset's storage (`boot`
  for the dev image or a test run, ABL through EDL); each write is checked against the
  backup's hash and verified by readback.
- **Development mode, stock on request.** The headset stays in development mode between
  tasks: the engineering ABL, and a development image in `boot` on which `python -m pico4 dev`
  boots each userspace archive, bounded by device-side deadlines. Tasks do not end on stock.
  The way back is verified whenever it is taken: `python -m pico4 cycle` checks stock boot by
  EDL readback, restores the stock ABL with readback and waits for PICO OS, either alone (after
  `python -m pico4 dev restore`, with `--restore-only`) or after an acceptance run
  (`python -m pico4 hwtest flash-run` with the userspace). Both read the headset's settings
  from a git-ignored `.env` ([.env.example](../.env.example)); see
  [hardware-testing.md](hardware-testing.md).
- **Private data.** Backups, vendor firmware, factory calibration, Valve's packages and the
  built images stay out of the repository. Builds take the GPU firmware and sensor data from
  the user's own backup and Valve's packages from its public repositories
  ([development.md](development.md)).

## Steps

### 1. Recoverable development device

**Goal.** Back up the stock firmware and device-specific partitions, validate the recovery
method, and unlock the original PICO 4 bootloader. Do not replace the stock operating system
until recovery has been tested.

**Status.**

- `python -m pico4 device` ([pico4/device.py](../pico4/device.py)) captures the headset's
  identity in PICO OS (`probe`), takes an EDL backup of every partition with a SHA-256 manifest
  (`backup`, `verify-backup`; userdata optional), stages the engineering ABL with a byte
  readback (`stage`), unlocks (`unlock`, `check-unlocked`) and restores the stock ABL
  (`restore-abl`). It implements the process documented by
  [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool) independently, without
  its scripts or images.
- Normal and critical unlock persist across reboots when `oem setenforce 0` comes before the
  unlock commands. In the other order the unlock fails (`00000050`) and the flags revert at the
  next reboot. Unlocking wipes user data.
- `adb reboot edl` enters EDL from PICO OS without buttons, so cycles run unattended.
- The tested engineering ABL (a signed Neo3 build) has no `fastboot boot`, so Linux starts from
  a flashed `boot` partition (step 2), which holds the dev image in development mode. Flashing
  `boot` needs the engineering ABL and its per-session OEM authorization command.
- After a cycle, full EDL readbacks of `boot` (96 MiB) and `abl` (1 MiB) match the backup. The
  bootloader stays unlocked; PICO OS boots with SELinux enforcing.

**Open.** The EDL reset at the end of a cycle can leave the headset off, with stock boot and
ABL already verified; a hard reset then starts PICO OS. The cause is not known.

### 2. Minimal Linux

**Goal.** Prepare a minimal initramfs from the PICO kernel and device-tree information, with
serial or USB debugging. The first measurable success is a Linux shell, not a VR interface.

**Status.**

- The stage-1 image is a standard Android boot image: the stock kernel and DTBs byte for byte,
  with the ramdisk replaced by Alpine Linux 3.24 (aarch64) and the project's init, whose PID 1
  is `pico4-init`. `python -m pico4 build --firmware <build>` builds it from the boot image of
  the matching official OTA ([pico4/image.py](../pico4/image.py),
  [pico4/firmware.json](../pico4/firmware.json)).
- USB-C carries the debugging: an NCM network link (headset 172.16.42.1; DHCP gives the PC an
  address without gateway or DNS), SSH, telnet, a web page with logs and a hardware report,
  and a read-only log drive. There is no serial console: the stock kernel has no ACM function.
- Every physical block device is read-only; the root file system is in RAM.
- The stock kernel rejects Windows' NCM packets (`Wrong NDP SIGN`) until the host sends CDC
  `SET_CRC_MODE(0)`. [pico4/windows_ncm.py](../pico4/windows_ncm.py) sends it through UsbDk
  after each stage-1 boot and hands the device back to Windows' UsbNcm driver.
- A watchdog returns the headset to fastboot after 5 minutes without usable USB traffic or a
  login; test runs add a connection deadline and a maximum runtime. A kernel panic reboots
  instead of leaving the headset in Qualcomm's crash-dump mode.
- `python -m pico4 hwtest flash-run` writes the image to `boot`, checks a per-run SSH identity,
  the stock kernel, an ARM64 root shell, read-only storage (all 99 physical block entries) and
  the HTTP diagnostics, then flashes the stock boot image back and waits for the original
  PICO OS build. The tested headset passes this cycle, including the Windows NCM
  initialization. With a configured but unusable USB link, the watchdog and the runner bring
  it back to PICO OS without a button press.

**Limits.** A kernel hard hang or a loss of host power can still need physical or EDL
recovery. Windows 10 has no NCM driver.

### 3. Native graphics

**Goal.** Investigate DRM/KMS, display initialization, Mesa Turnip and Vulkan, and verify that
the two eye displays can be driven reliably at their native resolution and refresh rates.

**Status.**

- **Display.** `msm_drm` exposes one 4320×2160 DSI connector for the two 2160×2160 panels
  (Sharp LS026B3SA), with 90 and 72 Hz modes. Frames use the stock arrangement (six
  colour-component layers, routed to the eyes with `sspp_layout`) and the vendor fence
  contract: each commit requests the CRTC release fence and the connector retire fence and
  waits for the retire fence. With page-flip events alone, swapped frames come out corrupted.
  Output starts at the boot-time 90 Hz mode; a first frame at 72 Hz fails with
  `DMS not supported on first frame`.
- **Measured.** From a fresh boot, 90 s per mode: 8,100 flips at 89.9976 Hz and 6,481 flips
  at 72.0045 Hz, with no vblank sequence gaps and steady left and right images in the lenses;
  120 s runs give 90.0021 and 72.0082 Hz. Generic `drm_info` and `modetest` compute about
  32.83 and 26.27 Hz for these modes; the kernel's vblank events give the real rates.
- **GPU.** The Adreno 650 is driven by KGSL; it is not a DRM device on this kernel. Turnip built
  with its KGSL backend (Mesa 26.1.6, `-Dfreedreno-kmds=kgsl`) renders an offscreen Vulkan test
  and verifies all 65,536 pixels. Alpine's and Valve's Mesa builds lack that backend.
- The compositors of steps 5 and 6 present through this display path, scanning out
  GPU-rendered images without a copy.

**Limits.** The tests are bounded runs, not a long-duration reliability or Vulkan conformance
claim. The GPU firmware comes from the user's own backup.

### 4. SteamOS-compatible userspace

**Goal.** Study the public Steam Frame packages and the steamac and steam-frame-nixos
projects, and adapt system services, input and graphics dependencies to the PICO kernel
instead of using the Frame's kernel.

**Status.**

- **Packages.** Valve's public Holo Core aarch64 preview resolves the system. The Frame's own
  builds come from its public hotfix repository, named explicitly in
  [packages.txt](../userspace/packages.txt): systemd 257.7,
  `steamos-customizations-deckard`, `steamos-manager`, gamescope 3.16.28 and, for step 6,
  SteamVR. The preview's systemd 258 needs Linux 5.4, so the build takes 257.7 and the device
  pins it (`IgnorePkg`). Neither Valve's Turnip nor the preview's has the KGSL backend;
  `pico4-turnip-kgsl` installs beside the system Mesa. The current archive holds 409 packages:
  840 MB, 2,567 MiB installed.
- **Handoff.** Stage 1 receives the archive over SSH into ext4 on zram
  (`pico4-userspace receive`) and hands PID 1 to its systemd (`pico4-userspace switch`). The
  USB link, the SSH host key, read-only storage and the recovery deadlines carry over into
  stage 2.
- **On the stock kernel** systemd reaches `running` with no failed units (taint `old-kernel`
  only), and Turnip renders and verifies the Vulkan test inside the userspace. Adaptations: a
  udev rule creates the device nodes; the session user `steamos` is in the Android network
  groups that `ANDROID_PARANOID_NETWORK` requires for sockets; drop-ins keep systemd's `cpu`
  controller off, because with `CONFIG_RT_GROUP_SCHED` the kernel refuses real-time threads
  inside it; zram uses LZO, the only compressor the kernel offers.
- **QEMU.** [tests/qemu/run-userspace.sh](../tests/qemu/run-userspace.sh) boots the same
  stage 1 and archive on a mainline 4.19 kernel configured like the stock one: the same handoff
  and syscall surface, without the headset's drivers.

**Open.** Kernel features the Steam client and games will miss: user, PID, IPC and UTS
namespaces (Steam Linux Runtime's containers), `binfmt_misc` (FEX for x86 games), `hidraw`
(Steam Input; input comes only through evdev and uhid), System V shared memory and message
queues, and memcg (no resource limits; `systemd-oomd` is masked). How to run the Steam Linux
Runtime on this kernel is undecided.

### 5. Native XR hardware

**Goal.** Establish IMU access, camera acquisition, 6DoF tracking, controller connectivity and
lens calibration, with Monado as the foundation.

**Status.** Monado 25.1.0 with the project's `pico4` driver ([src/monado/](../src/monado/)) is
the system's OpenXR runtime and runs as a socket-activated system service. The factory
calibration it uses comes from the user's own backup.

- **Head IMU.** The ICM-42688 sits behind the sensor DSP (SLPI). linux-msm's qrtr-ns, pd-mapper
  and hexagonrpcd, ported to the stock kernel's downstream FastRPC ABI, boot the DSP and serve
  its sensor registry. The `pico4-imu` client streams it at 512 Hz with no gaps and monotonic
  DSP timestamps; a guided 90° turn reads +90.3 to +90.5°. Monado's driver fuses it at 1 kHz:
  it maps the DSP's QTimer timestamps onto `CLOCK_MONOTONIC`, starts level from the measured
  gravity and learns the gyro bias while the headset is still. A headless OpenXR client gets a
  tracked pose about 1 ms after the sample; on a still headset the view rotates 0.009° in 60 s.
- **Display.** A Monado compositor target presents through the stock KMS driver with the fenced
  six-layer path of step 3, sharing linear dma-buf images with the display without a copy.
  OpenXR frames reach the panels at 90 Hz with one projection layer. Frames with several layers
  reach 90 Hz with compressible scratch images at panel size and a GPU clock floor of 587 MHz.
- **Lenses and IPD.** The compositor pre-distorts each eye with the headset's factory lens
  model over a 106.7° field of view; lines look straight in the lenses. The chromatic
  aberration terms are implemented but off by default, as on PICO OS. The IPD comes from the
  lens motor's GD32 MCU through one read-only command (71.99 mm with the wheel at 72 mm).
- **Tracking cameras.** The four OV7251s stream through Qualcomm's Spectra camera stack (CSL)
  without vendor libraries. The controller radio ("station", an nRF52832 on SPI) provides their
  50 Hz frame sync and their exposure: in 30 s each camera delivers 1,500 of 1,500 frames,
  17.5 ms on average from start of frame to delivery.
- **Controllers.** Inputs and IMU arrive through the sensor DSP (`ble_mcu`). Both controllers
  appear in OpenXR with the PICO 4 controller profile, all buttons, touch sensors, sticks and
  triggers, and 3DoF orientation, at about 500 IMU samples and 100 input packets per second
  each.
- **6DoF.** PICO OS's own tracker (`libSlam.so`, its front end on the Compute DSP) runs in a
  bionic root beside the driver, on all four cameras at 25 Hz and the IMU at 1 kHz. Still, it
  holds within a millimetre for a minute at about 70 % of one core (Basalt took about 3.5
  cores). Worn, a closed 12 m walk ends within 1.8 cm of the start; slid 50 cm on a table, it
  returns within 1 cm. Basalt inside Monado is deprecated; it remains selectable as it is.
- **Acceptance.** In the step's acceptance cycle, with 6DoF running, an OpenXR client renders
  2,698 frames in 30 s at 89.996 Hz (2 late), each eye on its own panel.

**Open.** 6DoF robustness; see
[Open work](#open-work).

### 6. Valve's VR session

**Goal.** Once tracking and graphics work, bring up the Frame-style gamescope/SteamVR session.
Valve's headset-specific components are proprietary and built for the Frame's hardware.

**Status.**

- Valve's `gamescope-session.target` runs gamescope's OpenVR backend and SteamVR 2.17.10 from
  the Frame's repository, in a logind session of the `steamos` user that
  `pico4-session.service` starts. The Frame's tracking driver, controller driver and radio
  firmware stay out of the image. The Frame's radio firmware updater is masked: it must never
  run against the PICO's station.
- **Display.** SteamVR's compositor presents only in direct mode. The `pico4-vrdisplay` Vulkan
  layer gives it the panels as one 4320×2160 display at 90 Hz, through the stock KMS driver with
  the fenced path of step 3.
- **Tracking.** `driver_monado`, Monado's SteamVR plugin, runs the step-5 driver inside
  SteamVR: the headset with 6DoF, both controllers with 3DoF and an arm model.
- **Kernel gaps.** SteamVR's process locks use System V semaphores, which the preloaded
  `pico4-sysvsem` provides. Its compositor shares GPU semaphores between processes, which
  neither Turnip's KGSL backend nor the kernel supports (no DRM syncobjs); the display layer
  emulates them over sync files.
- **Measured.** In the full session (gamescope's overlay and the dashboard besides the
  compositor) the GPU is 99 % busy at 587 MHz, and the compositor presents about 42–54 frames
  per second against the panels' 90 Hz. SteamVR on its own presents most frames at 90 Hz;
  about one in five is a refresh late. In the lenses the SteamVR environment shows, slightly
  jittery; the controllers turn with the wrists but do not move in space.
- **Heat.** Without fan control Valve's session takes the hottest thermal zone from about 54 °C
  to 92 °C within a minute. The userspace enables `pico4-fan.service`, which runs PICO OS's fan
  policy ([cooling.md](cooling.md)): with the fan at 30 % the 30 s session check takes the
  hottest zone from 45 °C to 69 °C, against 47 °C to 86 °C without it. Session checks run for
  30 s.
- **Session.** Valve's session is the only session; it starts at boot. Monado's service remains
  as a development runtime for the driver's own OpenXR clients, with the session stopped (both
  own the hardware).
- **Acceptance.** The acceptance cycle includes 30 s of Valve's session: presenting 15 s after
  the switch at about 51 frames per second, no fence or GPU timeout, the headset and both
  controllers in SteamVR.

**Open.** Performance, cooling and controller positions;
see [Open work](#open-work).

## Milestones

The milestones of the research's feasibility assessment ([research.md](research.md#feasibility))
and where they stand.

| Milestone | Status |
| --- | --- |
| Unlock the PICO 4 bootloader | Done: normal and critical unlock persist with the engineering ABL (step 1) |
| Boot minimal Linux | Done: stock kernel with an Alpine initramfs, root shell over USB-C (step 2) |
| Obtain accelerated Linux graphics | Done: Turnip Vulkan on KGSL and both panels at 90 and 72 Hz through KMS (step 3); the compositors of steps 5 and 6 scan out GPU-rendered images |
| Run the ARM64 SteamOS userspace | Done: Holo Core aarch64 with the Frame's systemd 257.7 and SteamOS services, no failed units (step 4) |
| Display a Steam-style desktop in the headset | Done: SteamVR's environment and dashboard in the lenses, the Steam client's library as SteamVR's overlay (step 6) |
| Full 6DoF and controllers | Partly: 6DoF head tracking and 3DoF controllers with all inputs (step 5); controller positions are open |
| Complete standalone SteamVR experience | Partly: the installed system starts without the PC; in Valve's session SteamVR runs on the panels at 90 fps and games start from the Steam library; cooling and power handling are open |

## Open work

### Cooling

Details: [cooling.md](cooling.md).

The kernel's `step_wise` zones throttle the prime core from 75 °C and the GPU only from 95 °C.
On PICO OS the fan, an AVC blower, hangs off the GD32 MCU that also moves the lenses and drives
the backlight; `pxrfanservice` sets its duty over that MCU's UART (`/dev/ttyHS1`). The kernel's
`pwm-fan` driver has no device-tree node, and Qualcomm's `thermal-engine` is off on PICO OS as
well.

[`pico4-fan`](../userspace/rootfs/usr/bin/pico4-fan) runs PICO OS's policy with the same
frames: 14 CPU and GPU zones every 200 ms, 10 s means, PICO's duty tables with their
hysteresis. It adds full speed while any sensor reads 95 °C or more, until the hottest mean is
below 90 °C, and 100 % without usable readings. It builds frames only from an allowlist
(SetFanSpeed 0–100 % and four queries) and shares the UART with Monado's IPD read through an
exclusive `flock`. The userspace build enables its service, and the acceptance checks require
it on the headset, with a duty the MCU acknowledged, and record its duty and RPM. On the headset the MCU acknowledges its
duty and reports the fan's speed (about 4,900 rpm at 30 %), and Valve's 30 s session check
peaks at 69 °C instead of 86 °C. Remaining:

1. **Longer sessions.** The hottest-zone limit is 90 °C. PICO OS's early table, the default,
   holds 10 minutes of Valve's session at 90 fps at a hottest CPU/GPU mean of 65.6–72.1 °C with
   the fan at 45 % ([cooling.md](cooling.md)); longer sessions and games are unmeasured.

### Performance

Details: [session.md](session.md), [xr.md](xr.md).

Valve's session runs the Steam Frame's compositor path at 90 frames per second at 587 MHz:
7.0 ms of GPU per frame, the GPU about 72 % busy, frames reaching the panels at a steady
21–22 ms after their present, the hottest zone below 62 °C, and in the lenses fluid without
doubled lines.

- The headset's proximity sensor in `driver_monado`, so that SteamVR's standby works as on the
  Frame (its compositor keeps running on standby until then).
- One gap of 125–165 ms during each start of Valve's session
  ([session.md](session.md#one-second-waits)).

### Steam client and network

Details: [session.md](session.md), [userspace.md](userspace.md).

- The Steam client is in the image as Valve's 671 MiB bundle; its first start unpacks it on
  the data partition (2.5 GB) and updates it. It starts with Valve's session and shows its UI
  as SteamVR overlays; pointer, clicks, haptics and its settings work. Its UI, its web helper
  and Xwayland run OpenGL on the GPU through zink ([graphics.md](graphics.md#opengl)). Open:
  a fluid login page and library in the headset (checked by the user), signing in, and a worn
  check that SteamVR's one-second waits no longer come while the client works with its
  overlays.
- The headset reaches the internet over the USB link when the PC shares its connection
  ([userspace.md](userspace.md#internet-over-usb)). Wi-Fi comes up and scans
  ([userspace.md](userspace.md#wi-fi)) and joins WPA2 and WPA3 networks.
- MangoHud's `mangoapp`, the client's performance overlay, shows its HUD in the headset over
  apps in gamescope (built against the image's libraries, its frame timings through the System
  V message queues of `pico4-sysvsem`); switching it on from the client's menu is unverified.
  The client's video module uses the FFmpeg 8 it brings itself; playback is unverified.
- The client will meet the kernel limits of step 4: no namespaces for Steam Linux Runtime's
  containers, no `binfmt_misc` for FEX, no `hidraw` for Steam Input.

### Controllers

Details: [xr.md](xr.md).

- **Positions.** Constellation tracking of the controllers' IR LEDs: a reimplementation of PICO
  OS's controller tracking, with the LED rings read from the headset's own firmware, runs in the
  driver on the LED exposures and the controller IMU. Worn in Valve's session, both controllers
  follow the hands (a 30 cm move, crossing hands, out of view and back); a controller that lies
  still from the start with a still headset can stay unacquired (as with PICO OS's rules).
- **Models.** SteamVR draws PICO OS's own controller models, read from the headset's firmware.
- **Haptics.** Vibration goes to the addressed controller through the station's motor command,
  the only controller command in the allowlist; firmware and OTA commands stay excluded.
- **SteamVR.** The controllers are SteamVR's own PICO 4 controller type (`pico_controller`), so
  games' bindings for it, or their Touch, Index and Vive bindings through SteamVR's remapping,
  apply; in a game this is unchecked. A press of the PICO or menu button opens the dashboard,
  whose content comes from the Steam client, a double press the room view, and the laser and
  trigger clicks work in it.

### Head tracking

Details: [xr.md](xr.md).

- PICO OS's tracker is the default; worn it tracks walking and fast turns (up to 458 °/s)
  without a lost pose. A covered camera and a dim room are unchecked.
- Its inputs are PICO OS's: the lens-shading table by the camera modules' vendor and PICO
  OS's own auto exposure. Its maps persist on the data partition, and SteamVR gets a tracking
  universe per room; SteamVR's room setup runs in the headset and its play area comes back
  after a reboot; the floor height is hard to set exactly.

### Standalone boot

The system can be installed ([install.md](install.md)), following PICO OS's layout: stage 1 in
`boot` mounts the system image from `super` read-only and the data from `userdata`, without
deadlines; the maintenance stage 1 in `recovery` keeps the development loop (USB boots into
RAM) and does installs. Installed, the system starts without the PC and reaches `running`
with no failed units, and data in `/home`, `/var`, `/etc` and `/root` survives reboots and
system updates. Open:

1. **The way back.** `dev restore` for an installed system (stock `super`, `boot`, `recovery`,
   wiped data) is not yet verified on the headset.
2. **Power.** The lenses show a boot indication until Valve's session starts. The power key
   follows PICO OS (a short press suspends, 2 s held powers off), and a power-off with the
   cable attached ends in charger mode, from which the key starts the system; verified on the
   headset. After a resume Valve's session goes on with 6DoF, both controllers and the cameras.

### Other hardware

Wi-Fi joins WPA2 networks ([userspace.md](userspace.md#wi-fi)). Audio works: the speakers and
the microphone are PipeWire's default sink and source, and playback runs without xruns under
Valve's session ([userspace.md](userspace.md#audio)); SteamVR's sound and volume work in the
headset, the microphone records speech, and PICO OS's calibration is loaded per stream. Bluetooth has not been brought up under the userspace; suspend and resume work ([install.md](install.md#power-key)). Battery
level and charging state reach UPower and SteamVR ([userspace.md](userspace.md)).
