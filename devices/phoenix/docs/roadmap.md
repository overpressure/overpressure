# Roadmap

The project runs Linux, a SteamOS-compatible userspace and Valve's VR session on the original
PICO 4 (2022, codename phoenix, Snapdragon XR2), on Valve's kernel from the Steam Frame with the
PICO's own drivers and device tree ([kernel.md](kernel.md)). The stock Android
kernel is no longer a target for the system; the way back to PICO OS stays. The plan has six steps,
from a recoverable development device to Valve's VR session; [research.md](research.md) holds the
background. All six steps work on the tested headset,
an original PICO 4. Open work: the rest of the direct boot's checks on the headset (the power key in charger
mode, a boot without the PC, `dev check` with the controllers), and a power press after a long sleep.

## Status

| Step | Status | Document |
| --- | --- | --- |
| 1. Recoverable development device | Works: EDL backup, engineering ABL, persistent unlock, stock restore verified by readback | [device-preparation.md](device-preparation.md) |
| 2. Minimal Linux | Works: root shell over USB-C, read-only storage, automatic return to fastboot | [stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md) |
| 3. Native graphics | Works: Valve's Mesa on msm; both panels at 90 Hz | [graphics.md](graphics.md) |
| 4. SteamOS-compatible userspace | Works: Holo Core aarch64 with the Frame's systemd 257.7, no failed units | [userspace.md](userspace.md) |
| 5. Native XR hardware | Works: head IMU, display, lens correction, tracking cameras, 6DoF head and controller tracking, RGB camera and the colour room view | [xr.md](xr.md) |
| 6. Valve's VR session | Works: SteamVR on both panels at 90 fps on the Steam Frame's compositor path, the Steam client and its library, x86 games through Proton and FEX, Android apps through Lepton, Steam Link from a PC. Heavy games run at what the GPU allows | [session.md](session.md), [cooling.md](cooling.md) |

Each step's document holds its design, measurements and reproduction steps.

## Constraints

- **Valve's kernel.** `boot` carries Valve's 7.2 kernel ([kernel.md](kernel.md))
  and starts the installed system, with both panels at 90 Hz, the GPU under Valve's own Mesa (Turnip on
  msm, OpenGL through zink), the GD32 MCU with fan control, the three DSPs, the head IMU at 1 kHz and the
  keys. The maintenance stage 1 in `recovery` stays on the stock Android kernel. The system no longer
  runs on that kernel and the image no longer drives the GPU there; its behaviour stays the reference for
  the hardware, and the way back to PICO OS stays.
- **Read-only storage.** Both stages switch every physical block device read-only, except the installed
  system's data partition. Only the explicit, backup-validated paths write to the headset's storage:
  `dev flash` (`recovery`), `pico4-install` in the maintenance stage 1 (`boot`, `super`, `userdata`,
  `metadata`, `misc`'s bootloader message), `dev restore` and the EDL tools; each write is verified by
  readback.
- **Development mode, stock on request.** The headset stays in development mode between
  tasks: the engineering ABL, the dev image as the maintenance stage 1 in `recovery`, on which
  `python -m overpressure dev` boots each userspace archive, bounded by device-side deadlines, and the
  installed system in `boot`, `super` and `userdata`. Tasks do not end on stock.
  The way back is verified whenever it is taken: `python -m overpressure cycle` checks stock boot by
  EDL readback, restores the stock ABL with readback and waits for PICO OS, either alone (after
  `python -m overpressure dev restore`, with `--restore-only`) or after an acceptance run
  (`python -m overpressure hwtest flash-run` with the userspace). Both read the headset's settings
  from a git-ignored `.env` ([.env.example](../../../.env.example)); see
  [hardware-testing.md](hardware-testing.md).
- **Private data.** Backups, vendor firmware, factory calibration, Valve's packages and the
  built images stay out of the repository. Builds take the stock firmware files from PICO's public OTA,
  the headset's own data from `persist` at boot, and Valve's packages from its public repositories
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
  a flashed partition: the maintenance stage 1 in `recovery`, the installed system's boot image in `boot`.
  Flashing needs the engineering ABL and its per-session OEM authorization command.
- After a cycle, full EDL readbacks of `boot` (96 MiB) and `abl` (1 MiB) match the backup. The
  bootloader stays unlocked; PICO OS boots with SELinux enforcing.

**Limits.** The EDL reset at the end of a cycle can leave the headset off, with stock boot and
ABL already verified; a hard reset then starts PICO OS. The cause is not known.

### 2. Minimal Linux

**Goal.** Prepare a minimal initramfs from the PICO kernel and device-tree information, with
serial or USB debugging. The first measurable success is a Linux shell, not a VR interface.

**Status.**

- The maintenance stage-1 image is a standard Android boot image: the stock kernel and DTBs byte for byte,
  with the ramdisk replaced by Alpine Linux 3.24 (aarch64) and the project's init, whose PID 1
  is `pico4-init`. `python -m overpressure build --firmware <build>` builds it from the boot image of
  the matching official OTA ([tooling/image.py](../tooling/image.py),
  [tooling/firmware.json](../tooling/firmware.json)). The installed system's boot image carries Valve's
  kernel and tree and no ramdisk ([install.md](install.md)).
- USB-C carries the debugging: an NCM network link (headset 172.16.42.1; DHCP gives the PC an
  address without gateway or DNS), SSH, telnet, a web page with logs and a hardware report,
  and a read-only log drive. There is no serial console: the stock kernel has no ACM function.
- Every physical block device is read-only; the root file system is in RAM.
- The stock kernel rejects Windows' NCM packets (`Wrong NDP SIGN`) until the host sends CDC
  `SET_CRC_MODE(0)`. [overpressure/windows_ncm.py](../../../overpressure/windows_ncm.py) sends it through UsbDk
  after each stage-1 boot and hands the device back to Windows' UsbNcm driver. Valve's kernel, under
  the installed system, needs no such request.
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
  without a copy. A wake reads both panels and resends before the lenses light
  ([kernel.md](kernel.md)).
- **GPU.** The Adreno 650 runs on the msm DRM driver as one device with the display, as on the Frame,
  under Valve's Mesa: Turnip on msm (Vulkan 1.3) and OpenGL 4.6 through zink. It runs at up to 587 MHz,
  the chip's speed bin and PICO OS's VR level.

**Limits.** The tests are bounded runs, not a long-duration reliability or Vulkan conformance
claim. The GPU firmware comes from PICO's public OTA.

### 4. SteamOS-compatible userspace

**Goal.** Study the public Steam Frame packages and the steamac and steam-frame-nixos
projects, and run the Frame's system services, input and graphics stack on the PICO 4.

**Status.**

- **Packages.** Valve's public Holo Core aarch64 preview resolves the system. The Frame's own
  builds come from its public hotfix repository, named explicitly in
  [packages.txt](../userspace/packages.txt): systemd 257.7 (pinned with `IgnorePkg`),
  `steamos-customizations-deckard`, `steamos-manager`, gamescope, Valve's Mesa and SteamVR.
- **Start.** Valve's kernel mounts the system image from `super` read-only and starts its `preinit`
  without a ramdisk; preinit applies the read-only storage policy, mounts the data partition and
  executes systemd ([install.md](install.md#how-it-starts)). The system sets up its own USB link
  (the gadget and address of the maintenance stage 1) and keeps its SSH host keys in the data.
- **Result.** systemd reaches `running` with no failed units and Turnip renders and verifies the
  Vulkan test inside the userspace.

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
  PICO OS's controller tracking on the cameras' LED exposures. Worn, both follow the hands. A
  controller that lost its pairing pairs again in the running session, as PICO OS's station service
  does it, and then tracks and vibrates; the other one keeps its pairing.
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
- **Measured.** `dev check` on the installed system: 90.03 commits/s over 30 s, 0.0 % late, no stalls,
  hottest zone 65.5 °C. Worn the view shows no visible stutter with Steam's panel in view. Beat Saber
  through Proton and FEX submits a median 44.8 frames/s while the compositor presents 89.6/s: the GPU holds
  it there, at its top clock (92-96 % busy), not the CPU (every core below 50 %). POOLS is limited by the
  GPU, at about 32 frames/s. Games start at 72 Hz, the Frame's default for apps; SteamVR's settings offer 72
  and 90 Hz.
- **Apps.** The Steam client signs in and starts games from its library, x86 games through Proton and
  FEX, Android apps through Lepton. Steam Link plays PC games from a PC with picture, sound, tracking
  and controllers, and its end returns to the local session. Its first picture comes about 12 s after the
  client starts, nearly all of it the PC's startup; a decoder reset at the first video costs at most about
  0.6 s; 30-43 ms pass between a decoded picture and its illumination, by run, with a floor of about 22 ms.
- **Heat.** `pico4-fan.service` runs PICO OS's fan policy ([cooling.md](cooling.md)): about 10 minutes
  of a worn game peak at 75.6 °C, the limit being 90 °C.
- **Other hardware.** The speakers and the microphone are PipeWire's default sink and source, and playback
  runs without xruns under Valve's session ([userspace.md](userspace.md#audio)); SteamVR's sound and volume
  work in the headset, the microphone records speech at PICO OS's gain of 12, and PICO OS's calibration is
  loaded per stream. Battery level and charging state reach UPower and SteamVR
  ([userspace.md](userspace.md)).
- **Session.** Valve's session is the only session; it starts at boot.
- **Onboarding.** `pico4-onboarding` pairs controllers hands-free when none is there and shows the
  PICO 4's introduction once after Steam's first-run setup; worn, both and the replay from the
  dashboard work ([session.md](session.md#onboarding)).
- **Acceptance.** `dev check` runs 30 s of Valve's session on the installed system, with the headset
  and both controllers in SteamVR.

**Open.** See [Open work](#open-work).

## Open work

**Open.** The direct boot's remaining checks on the headset: the power key held 2 s in charger
mode, a boot without the PC, `dev check` with both controllers connected
([install.md](install.md#open-work)). Verified: preinit to the graphical target (9.7 to 11.0 s,
stage 1 in `boot` 23.3 s), charger mode up to the key, the Wi-Fi re-probe, the keys on the data
partition. A stopped PID 1 restarts the headset after 33 s (the watchdog's bark panics), and the
third boot in a row that never reached the graphical target starts the maintenance stage 1.

A short power press after more than an hour asleep. After an RTC wake from 3 h
asleep, with the cable in, Steam suspended again within about 4 s, while 80 idle minutes in SteamVR's standby
brought no suspend. Whether a key wake behaves alike is not known.

**Not verified.**

- `dev restore` for an installed system: implemented and tested on the host, not on the headset
  ([install.md](install.md)).
- A second headset: everything ran on one unit ([device-preparation.md](device-preparation.md#verified-hardware)).
- macOS as the host: the host tests cover it, a headset was never installed from one; Linux hosts built images,
  Windows also installed them. The stock firmware and assets in Python (any system, no Linux) match the shell
  extraction byte for byte on Windows and Linux; macOS and EDL device counting through pyusb on a Mac are not run.
- The first run with only the headset, from an empty data partition.
- Battery life unplugged: only inferred with the cable in.
- Sessions much longer than 10 minutes ([cooling.md](cooling.md)).
- MangoHud's `mangoapp`, the client's performance overlay: switching it on from the client's menu.
- PICO OS's head tracker with a covered camera and in a dim room.
- Bluetooth runs through `hci_qca` with PICO OS's firmware and the Frame's BlueZ
  ([kernel.md](kernel.md)); a paired device is not recorded as tested.

**Limits.**

- The kernel's `step_wise` zones throttle the CPUs from 90 and 95 °C and the GPU from 95 °C.
- PICO OS's speaker protection is absent, as the amplifiers' voltage and current sense is off
  ([userspace.md](userspace.md#audio)).
- `vrcompositor` dumps core when the session stops ([session.md](session.md)).
- MPEG-2 ends in a session error from the firmware; H.264, HEVC, VP9 and VP8 decode through Venus.
- A controller that lies still from the start with a still headset can stay unacquired, as with PICO OS's
  rules ([xr.md](xr.md)).
- The controllers' battery level goes to SteamVR as a fifth of a full charge per level; that 5 is the top
  of the scale is inferred.
- The floor height of SteamVR's room setup is hard to set exactly.
