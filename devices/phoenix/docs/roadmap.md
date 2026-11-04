# Roadmap

The project runs Linux, a SteamOS-compatible userspace and Valve's VR session on the original
PICO 4 (2022, codename phoenix, Snapdragon XR2), on Valve's kernel from the Steam Frame with the
PICO's own drivers and device tree ([kernel-feasibility.md](kernel-feasibility.md)). It began on the
stock Android kernel, which is no longer pursued; the way back to PICO OS stays. The plan has six steps,
from a recoverable development device to Valve's VR session, and follows the strategy of the
background research in [research.md](research.md). All six steps work on the tested headset,
an original PICO 4. Open work: a lens that can go wrong after a wake, suspend and resume (USB and
the speakers), the sound's crackle, heavy games' frame rate and the Venus firmware's crash on H.264.

## Status

| Step | Status | Document |
| --- | --- | --- |
| 1. Recoverable development device | Works: EDL backup, engineering ABL, persistent unlock, stock restore verified by readback | [device-preparation.md](device-preparation.md) |
| 2. Minimal Linux | Works: root shell over USB-C, read-only storage, automatic return to PICO OS | [stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md) |
| 3. Native graphics | Works: Valve's Mesa on msm; both panels at 90 Hz | [graphics.md](graphics.md) |
| 4. SteamOS-compatible userspace | Works: Holo Core aarch64 with the Frame's systemd 257.7, no failed units | [userspace.md](userspace.md) |
| 5. Native XR hardware | Works: head IMU, display, lens correction, tracking cameras, 6DoF head and controller tracking, RGB camera and the colour room view | [xr.md](xr.md) |
| 6. Valve's VR session | Works: SteamVR on both panels at 90 fps on the Steam Frame's compositor path, the Steam client and its library, x86 games through Proton and FEX, Android apps through Lepton, Steam Link from a PC. Open: heavy games' frame rate | [session.md](session.md), [cooling.md](cooling.md) |

Each step's document holds its design, measurements and reproduction steps.

## Constraints

- **Valve's kernel.** Stage 1 and the installed system run on Valve's 7.2 kernel
  ([kernel-feasibility.md](kernel-feasibility.md)) with both panels at 90 Hz, the GPU under Valve's
  own Mesa (Turnip on msm, OpenGL through zink), the GD32 MCU with fan control, the three DSPs, the
  head IMU at 1 kHz and the keys. The stock Android kernel is no longer pursued and the image no
  longer drives the GPU there; its behaviour stays the reference for the hardware, and the way back
  to PICO OS stays.
- **Read-only storage.** Both stages switch every physical block device read-only. Only the
  explicit, backup-validated flash and restore paths write to the headset's storage (`boot`
  for the dev image or a test run, ABL through EDL); each write is checked against the
  backup's hash and verified by readback.
- **Development mode, stock on request.** The headset stays in development mode between
  tasks: the engineering ABL, and a development image in `boot` on which `python -m overpressure dev`
  boots each userspace archive, bounded by device-side deadlines. Tasks do not end on stock.
  The way back is verified whenever it is taken: `python -m overpressure cycle` checks stock boot by
  EDL readback, restores the stock ABL with readback and waits for PICO OS, either alone (after
  `python -m overpressure dev restore`, with `--restore-only`) or after an acceptance run
  (`python -m overpressure hwtest flash-run` with the userspace). Both read the headset's settings
  from a git-ignored `.env` ([.env.example](../../../.env.example)); see
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

- `python -m overpressure device` ([tooling/device.py](../tooling/device.py)) captures the headset's
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
  is `pico4-init`. `python -m overpressure build --firmware <build>` builds it from the boot image of
  the matching official OTA ([tooling/image.py](../tooling/image.py),
  [tooling/firmware.json](../tooling/firmware.json)).
- USB-C carries the debugging: an NCM network link (headset 172.16.42.1; DHCP gives the PC an
  address without gateway or DNS), SSH, telnet, a web page with logs and a hardware report,
  and a read-only log drive. There is no serial console: the stock kernel has no ACM function.
- Every physical block device is read-only; the root file system is in RAM.
- The stock kernel rejects Windows' NCM packets (`Wrong NDP SIGN`) until the host sends CDC
  `SET_CRC_MODE(0)`. [overpressure/windows_ncm.py](../../../overpressure/windows_ncm.py) sends it through UsbDk
  after each stage-1 boot and hands the device back to Windows' UsbNcm driver.
- A watchdog returns the headset to fastboot after 5 minutes without usable USB traffic or a
  login; test runs add a connection deadline and a maximum runtime. A kernel panic reboots
  instead of leaving the headset in Qualcomm's crash-dump mode.
- `python -m overpressure hwtest flash-run` writes the image to `boot`, checks a per-run SSH identity,
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

- **Display.** The DPU drives two bonded DSI links with DSC as one 4320×2160 connector for the two
  2160×2160 panels (Sharp LS026B3SA), with vblanks at 90.00 Hz. The panel driver comes from the stock
  tree's power-on commands and timings. SteamVR's compositor scans out compressed GPU-rendered images
  without a copy.
- **GPU.** The Adreno 650 runs on the msm DRM driver as one device with the display, as on the Frame,
  under Valve's Mesa: Turnip on msm (Vulkan 1.3) and OpenGL 4.6 through zink. It runs at up to 587 MHz,
  the chip's speed bin and PICO OS's VR level.

**Limits.** The tests are bounded runs, not a long-duration reliability or Vulkan conformance
claim. The GPU firmware comes from the user's own backup.

### 4. SteamOS-compatible userspace

**Goal.** Study the public Steam Frame packages and the steamac and steam-frame-nixos
projects, and run the Frame's system services, input and graphics stack on the PICO 4.

**Status.**

- **Packages.** Valve's public Holo Core aarch64 preview resolves the system. The Frame's own
  builds come from its public hotfix repository, named explicitly in
  [packages.txt](../userspace/packages.txt): systemd 257.7 (pinned with `IgnorePkg`),
  `steamos-customizations-deckard`, `steamos-manager`, gamescope, Valve's Mesa and SteamVR.
- **Handoff.** Stage 1 receives the archive over SSH into ext4 on zram
  (`pico4-userspace receive`) and hands PID 1 to its systemd (`pico4-userspace switch`). The
  USB link, the SSH host key, read-only storage and the recovery deadlines carry over into
  stage 2. Installed, the same system runs from `super` without the PC ([install.md](install.md)).
- **Result.** systemd reaches `running` with no failed units and Turnip renders and verifies the
  Vulkan test inside the userspace. Drop-ins keep systemd's `cpu` controller off, because with
  `CONFIG_RT_GROUP_SCHED` the kernel refuses real-time threads inside it.

### 5. Native XR hardware

**Goal.** Establish IMU access, camera acquisition, 6DoF tracking, controller connectivity and
lens calibration, with Monado as the foundation.

**Status.** Monado with the project's `pico4` driver ([src/monado/](../src/monado/)) runs inside
SteamVR as `driver_monado`. The factory calibration it uses comes from the headset's own `persist`.

- **Head IMU.** The ICM-42688 sits behind the sensor DSP (SLPI), reached over upstream FastRPC and
  QRTR: 1023.89 Hz with no gaps. Monado's driver maps the DSP's timestamps onto `CLOCK_MONOTONIC`,
  starts level from the measured gravity and learns the gyro bias while the headset is still.
- **Lenses and IPD.** Each eye is pre-distorted with the headset's factory lens model; lines look
  straight in the lenses. The IPD comes from the lens motor's GD32 MCU through one read-only command.
- **Tracking cameras.** The four OV7251s stream through CAMSS. The controller radio ("station", an
  nRF52832 on SPI) provides their 50 Hz frame sync and their exposure: 1,500 frames per camera at
  49.997 fps, none with an error, every timestamp from the hardware.
- **Controllers.** Inputs and IMU arrive through the sensor DSP, positions from a reimplementation of
  PICO OS's controller tracking on the cameras' LED exposures. Worn, both follow the hands.
- **6DoF.** PICO OS's own tracker (`libSlam.so`, its front end on the Compute DSP) runs in a bionic
  root beside the driver, on all four cameras at 25 Hz and the IMU at 1 kHz. Still for 60 s: 0 poses
  lost, under a millimetre, about half of one core. Worn it tracks walking and fast turns, and rooms
  and their play areas come back after a reboot. Basalt inside Monado remains selectable.
- **Room view.** The compositor's colour room view runs on the RGB camera and the depth mesh; worn it
  shows a full colour picture, also looking up.

**Open.** See [Open work](#open-work).

### 6. Valve's VR session

**Goal.** Once tracking and graphics work, bring up the Frame-style gamescope/SteamVR session.
Valve's headset-specific components are proprietary and built for the Frame's hardware.

**Status.**

- Valve's `gamescope-session.target` runs gamescope's OpenVR backend and SteamVR 2.17.10 from
  the Frame's repository, in a logind session of the `steamos` user that
  `pico4-session.service` starts. The Frame's tracking driver, controller driver and radio
  firmware stay out of the image. The Frame's radio firmware updater is masked: it must never
  run against the PICO's station.
- **Display.** SteamVR's compositor presents in direct mode through Turnip's own display WSI on msm:
  one 4320×2160 display at 90 Hz. `pico4-swapchain-usage` holds the stock path's pacing: four images,
  one frame ahead, a lead of 5 ms.
- **Tracking.** `driver_monado`, Monado's SteamVR plugin, runs the step-5 driver inside SteamVR: the
  headset and both controllers with 6DoF.
- **Measured.** `dev check`: 90.0 commits/s, 0.04 % late, no stalls over 30 s. Worn the view shows no
  visible stutter with Steam's panel in view. Beat Saber through Proton and FEX submits a median
  44.8 frames/s while the compositor presents 89.6/s. POOLS is limited by the GPU, at about 32 frames/s.
- **Apps.** The Steam client signs in and starts games from its library, x86 games through Proton and
  FEX, Android apps through Lepton. Steam Link plays PC games from a PC with picture, sound, tracking
  and controllers, and its end returns to the local session.
- **Heat.** `pico4-fan.service` runs PICO OS's fan policy ([cooling.md](cooling.md)): about 10 minutes
  of a worn game peak at 75.6 °C, the limit being 90 °C.
- **Session.** Valve's session is the only session; it starts at boot.
- **Acceptance.** `dev check` runs 30 s of Valve's session on the installed system, with the headset
  and both controllers in SteamVR.

**Open.** See [Open work](#open-work).

## Milestones

The milestones of the research's feasibility assessment ([research.md](research.md#feasibility))
and where they stand.

| Milestone | Status |
| --- | --- |
| Unlock the PICO 4 bootloader | Done: normal and critical unlock persist with the engineering ABL (step 1) |
| Boot minimal Linux | Done: Valve's kernel with an Alpine initramfs, root shell over USB-C (step 2) |
| Obtain accelerated Linux graphics | Done: Valve's Mesa on msm, both panels at 90 Hz, compressed scanout (step 3) |
| Run the ARM64 SteamOS userspace | Done: Holo Core aarch64 with the Frame's systemd 257.7 and SteamOS services, no failed units (step 4) |
| Display a Steam-style desktop in the headset | Done: SteamVR's environment and dashboard in the lenses, the Steam client's library as SteamVR's overlay (step 6) |
| Full 6DoF and controllers | Done: head and controller tracking, all inputs and vibration (step 5) |
| Complete standalone SteamVR experience | Done for the tested games: starts without the PC, Steam's library, x86 and Android apps, Steam Link (step 6). Open: heavy games' frame rate and suspend, see [Open work](#open-work) |

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
   the fan at 45 % ([cooling.md](cooling.md)); about 10 minutes of Beat Saber peak at 75.6 °C. Sessions
   much longer than that are unmeasured.

### Performance

Details: [session.md](session.md), [xr.md](xr.md).

Valve's session runs the Steam Frame's compositor path at 90 frames per second at 587 MHz, and worn
the view is fluid, also with Steam's panel in view.

- Beat Saber submits half the panels' rate.
- Games start at 72 Hz, the Frame's default for apps; SteamVR's settings offer 72 and 90 Hz.
- Steam Link: the first picture follows a decoder reset about 14 s after the client starts, and
  30 ms pass between a decoded picture and its illumination.

### Steam client and network

Details: [session.md](session.md), [userspace.md](userspace.md).

- The Steam client signs in, and its store and library work in the headset. `vrcompositor` dumps core
  when the session stops.
- Wi-Fi joins WPA2 and WPA3 networks and reconnects after a reboot; the headset also reaches the
  internet over the USB link when the PC shares its connection
  ([userspace.md](userspace.md#internet-over-usb)).
- MangoHud's `mangoapp`, the client's performance overlay: switching it on from the client's menu is
  unverified.
- Video: HEVC decodes through Venus. H.264 crashes Venus's firmware, which then stalls storage and
  halts the headset until a 10 s power press; do not run FFmpeg's `h264_v4l2m2m`.

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
  apply. A press of the PICO or menu button opens the dashboard,
  whose content comes from the Steam client, a double press the room view, and the laser and
  trigger clicks work in it.
- **Battery.** The controllers' battery level goes to SteamVR as a fifth of a full charge per level, and
  SteamVR's status panel shows it as pips where PICO OS has its bar. That 5 is the top of the scale is inferred.

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
   cable attached ends in charger mode, from which the key starts the system. After a resume the
   USB link is gone if the cable is in and the speakers are silent.
3. **Wake.** From a boot the picture comes by itself when the headset is picked up, repeatedly.
   Why an earlier dark wake went away is not known. A lens can go wrong after a wake while the other
   works: seen once grey (left, through three more wakes, cured by a suspend) and once corrupted
   (right, cleared by the next wake); the cause is not known. The headset does not go to standby in
   a dark room.

### Other hardware

Audio works: the speakers and
the microphone are PipeWire's default sink and source, and playback runs without xruns under
Valve's session ([userspace.md](userspace.md#audio)); SteamVR's sound and volume work in the
headset, the microphone records speech at PICO OS's gain of 12, and PICO OS's calibration is loaded per stream; the end of a sound still crackles at the speakers and loud starts overshoot for about 250 ms (open, [userspace.md](userspace.md#audio)). Bluetooth has not been brought up under the userspace. Battery
level and charging state reach UPower and SteamVR ([userspace.md](userspace.md)).
