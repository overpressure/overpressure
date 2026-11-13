# Roadmap

Linux, a SteamOS-compatible userspace and Valve's VR session on the original PICO 4 (2022, codename phoenix,
Snapdragon XR2), on Valve's kernel from the Steam Frame with the PICO's own drivers and device tree
([kernel.md](kernel.md)).

**TL;DR.** All six steps work on the tested headset. The owner installs, upgrades and restores to stock through
the installer (`python -m overpressure install|upgrade|restore`, [install.md](install.md#the-installer)); the
headset builds its own system ([install.md](install.md#built-on-the-headset)), so the owner's computer builds
nothing and downloads nothing of PICO OS's.
Open: the rest of the direct boot's checks (the power key in charger mode, a boot without the PC, `dev check`
with the controllers), a power press after a long sleep, and the checks under [Open work](#open-work).
Background: [research.md](research.md).

## Status

| Step | Status | Document |
| --- | --- | --- |
| 1. Recoverable development device | Works: EDL backup, engineering ABL, persistent unlock, stock restore verified by readback, also through the installer | [device-preparation.md](device-preparation.md) |
| 2. Minimal Linux | Works: root shell over USB-C, read-only storage, automatic return to fastboot; in `recovery` on Valve's kernel, where the headset builds its system | [stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md) |
| 3. Native graphics | Works: Valve's Mesa on msm; both panels at 90 Hz | [graphics.md](graphics.md) |
| 4. SteamOS-compatible userspace | Works: Holo Core aarch64 with the Frame's systemd 257.7, no failed units | [userspace.md](userspace.md) |
| 5. Native XR hardware | Works: head IMU, display, lens correction, tracking cameras, 6DoF head and controller tracking, RGB camera and the colour room view | [xr.md](xr.md) |
| 6. Valve's VR session | Works: SteamVR on both panels at 90 fps on the Steam Frame's compositor path, the Steam client and its library, x86 games through Proton and FEX, Android apps through Lepton, Steam Link from a PC. Heavy games run at what the GPU allows | [session.md](session.md), [cooling.md](cooling.md) |

Each step's document holds its design, measurements and reproduction steps.

## Constraints

- **Valve's kernel.** `boot` carries Valve's 7.2 kernel and starts the installed system; the maintenance stage 1
  in `recovery` runs on the same kernel. The stock Android kernel runs neither: it stays in the acceptance
  runner's boot image, its behaviour stays the reference for the hardware, and the way back to PICO OS stays.
- **Read-only storage.** Both stages switch every physical block device read-only, except the installed
  system's data partition. Only the explicit, backup-validated paths write: `dev flash` (`recovery`),
  `pico4-install` in the maintenance stage 1 (`boot`, `super`, `userdata`, `metadata`, `misc`'s bootloader
  message), `dev restore` and the EDL tools; each write is verified by readback.
- **Development mode, stock on request.** The headset stays in development mode between tasks (engineering ABL,
  the dev image in `recovery`, the installed system in `boot`, `super` and `userdata`). The way back is verified
  whenever it is taken: `python -m overpressure cycle` checks stock boot by EDL readback, restores the stock ABL
  with readback and waits for PICO OS, alone (after `dev restore`, with `--restore-only`) or after an acceptance
  run (`hwtest flash-run`). Settings come from a git-ignored `.env` ([.env.example](../../../.env.example),
  [hardware-testing.md](hardware-testing.md)).
- **Safety on the host.** Every command that touches the headset asks a disclaimer first (`--accept-disclaimer`
  for unattended runs) and refuses on a computer on its battery below 30 %; `dev install` and `dev restore`
  refuse a headset below 20 % that is not charging.
- **Private data.** Backups, vendor firmware, factory calibration, Valve's packages and the built images stay
  out of the repository. Nothing is taken from PICO's OTA: the images use the headset's own `boot` and
  `recovery` from its backup, the system the headset's own files and its data from `persist` at boot, and Valve's packages from its public repositories ([distribution.md](distribution.md)).

## Steps

### 1. Recoverable development device

**Goal.** Back up the stock firmware and device-specific partitions, validate recovery, and unlock the
bootloader before replacing the stock operating system.

**Status.**

- `python -m overpressure device` ([tooling/device.py](../tooling/device.py)) captures the identity in PICO OS
  (`probe`), takes an EDL backup of every partition with a SHA-256 manifest (`backup`, `verify-backup`; userdata
  optional), stages the engineering ABL with a byte readback (`stage`), unlocks (`unlock`, `check-unlocked`) and
  restores the stock ABL (`restore-abl`). It implements the process of
  [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool) independently, without its scripts or
  images.
- The unlock persists across reboots when `oem setenforce 0` comes before the unlock commands; in the other order
  it fails (`00000050`) and the flags revert at the next reboot. Unlocking wipes user data.
- `adb reboot edl` enters EDL from PICO OS without buttons, so cycles run unattended.
- The tested engineering ABL (a signed Neo3 build) has no `fastboot boot`: Linux starts from a flashed
  partition. Flashing needs the engineering ABL and its per-session OEM authorization command.
- After a cycle, full EDL readbacks of `boot` (96 MiB) and `abl` (1 MiB) match the backup. The bootloader stays
  unlocked; PICO OS boots with SELinux enforcing.
- The backup exports and imports as one checked file (`python -m overpressure backup export|import FILE`), so
  another computer can restore the headset ([install.md](install.md#the-backup-as-one-file)).

**Limits.** The EDL reset at the end of a cycle can leave the headset off, with stock boot and ABL already
verified; a hard reset then starts PICO OS. The cause is not known.

### 2. Minimal Linux

**Goal.** A minimal initramfs on the PICO kernel with USB debugging: a Linux shell, not yet a VR interface.

**Status.**

- The maintenance stage-1 image is a standard Android boot image: the stock kernel and DTBs byte for byte, the
  ramdisk replaced by Alpine Linux 3.24 (aarch64) with `pico4-init` as PID 1.
  `python -m overpressure build --firmware <build>` builds it from the headset's own boot partition in its EDL
  backup ([tooling/image.py](../tooling/image.py); [tooling/firmware.json](../tooling/firmware.json) lists the
  verified builds).
- USB-C carries the debugging: an NCM link (headset 172.16.42.1; DHCP gives the PC an address without gateway or
  DNS), SSH, telnet, a web page with logs and a hardware report, and a read-only log drive. No serial console:
  the stock kernel has no ACM function.
- Every physical block device is read-only; the root file system is in RAM.
- The stock kernel rejects Windows' NCM packets (`Wrong NDP SIGN`) until the host sends CDC `SET_CRC_MODE(0)`;
  [overpressure/windows_ncm.py](../../../overpressure/windows_ncm.py) sends it through UsbDk after each stage-1
  boot. Valve's kernel needs no such request.
- A watchdog returns the headset to fastboot after 5 minutes without usable USB traffic or a login; test runs
  add a connection deadline and a maximum runtime. A kernel panic reboots instead of leaving the headset in
  Qualcomm's crash-dump mode.
- `python -m overpressure hwtest flash-run` writes the image to `boot`, checks a per-run SSH identity, the stock
  kernel, an ARM64 root shell, read-only storage (all 99 physical block entries) and the HTTP diagnostics, then
  flashes stock boot back and waits for PICO OS; the tested headset passes it, including the Windows NCM
  initialization, and an unusable USB link still returns it to PICO OS without a button press.

**Limits.** A kernel hard hang or a loss of host power can still need physical or EDL recovery. Windows 10 has
no NCM driver.

### 3. Native graphics

**Goal.** Drive both eye displays reliably at their native resolution and refresh rates with DRM/KMS and Vulkan.

**Status.**

- **Display.** The DPU drives two bonded DSI links with DSC as one 4320×2160 connector for the two 2160×2160
  panels (Sharp LS026B3SA), vblanks at 90.00 Hz, from the stock tree's power-on commands and timings. SteamVR's
  compositor scans out compressed GPU-rendered images without a copy. A wake reads both panels and resends
  before the lenses light ([kernel.md](kernel.md)).
- **GPU.** The Adreno 650 runs on msm as one device with the display, as on the Frame, under Valve's Mesa: Turnip
  (Vulkan 1.3) and OpenGL 4.6 through zink, at up to 587 MHz (the chip's speed bin and PICO OS's VR level).

**Limits.** The tests are bounded runs, not a long-duration reliability or Vulkan conformance claim.

### 4. SteamOS-compatible userspace

**Goal.** Run the Steam Frame's system services, input and graphics stack on the PICO 4.

**Status.**

- **Packages.** Valve's public Holo Core aarch64 preview resolves the system; the Frame's own builds come from its
  public hotfix repository, named in [packages.txt](https://github.com/overpressure/packages/blob/main/headsets/phoenix/packages.txt): systemd 257.7 (pinned with
  `IgnorePkg`), `steamos-customizations-deckard`, `steamos-manager`, gamescope, Valve's Mesa and SteamVR. Every
  upstream package is pinned by hash in `packages.lock`; a weekly upgrade check builds against Valve's newest
  ([development.md](development.md#upgrade-check)).
- **Start.** Valve's kernel mounts the system image from `super` read-only and starts `preinit` without a
  ramdisk; preinit applies the read-only policy, mounts the data and executes systemd
  ([install.md](install.md#how-it-starts)). The system sets up its own USB link and keeps its SSH host keys in
  the data.
- **Result.** systemd reaches `running` with no failed units; Turnip renders and verifies the Vulkan test.

### 5. Native XR hardware

**Goal.** IMU, cameras, 6DoF tracking, controllers and lens calibration, with Monado as the foundation.

**Status.** Monado with the project's `pico4` driver ([pico4-monado](https://github.com/overpressure/packages/tree/main/phoenix/pico4-monado)) runs inside SteamVR as
`driver_monado`, with the factory calibration from the headset's own `persist`.

- **Head IMU.** The ICM-42688 behind the sensor DSP (SLPI), over upstream FastRPC and QRTR: 1023.89 Hz, no gaps.
  The driver maps the DSP's timestamps onto `CLOCK_MONOTONIC`, starts level from gravity and learns the gyro bias
  while still.
- **Lenses and IPD.** Each eye is pre-distorted with the factory lens model; lines look straight. The IPD comes
  from the lens motor's GD32 MCU through one read-only command.
- **Tracking cameras.** The four OV7251s stream through CAMSS; the controller radio ("station", an nRF52832 on
  SPI) provides their 50 Hz frame sync and exposure: 1,500 frames per camera at 49.997 fps, none with an error,
  every timestamp from the hardware.
- **Controllers.** Inputs and IMU through the sensor DSP, positions from a reimplementation of PICO OS's
  controller tracking on the LED exposures; worn, both follow the hands. A controller that lost its pairing pairs
  again in the running session, then tracks and vibrates; the other keeps its pairing.
- **6DoF.** PICO OS's own tracker (`libSlam.so`, its front end on the Compute DSP) runs in a bionic root beside
  the driver, on all four cameras at 25 Hz and the IMU at 1 kHz. Still for 60 s: 0 poses lost, under a
  millimetre, about half a core. Worn it tracks walking and fast turns; rooms and play areas come back after a
  reboot. Basalt inside Monado remains selectable.
- **Room view.** The compositor's colour room view runs on the RGB camera and the depth mesh; worn it shows a
  full colour picture, also looking up.

### 6. Valve's VR session

**Goal.** The Frame-style gamescope/SteamVR session. Valve's headset-specific components are proprietary and
built for the Frame's hardware.

**Status.**

- **Session.** Valve's `gamescope-session.target` runs gamescope's OpenVR backend and SteamVR 2.17.10 from the
  Frame's repository, in a logind session of `steamos` that `pico4-session.service` starts at boot; it is the
  only session. The Frame's tracking driver, controller driver and radio firmware stay out of the image; its
  radio firmware updater is masked and must never run against the PICO's station.
- **Display.** SteamVR's compositor presents in direct mode through Turnip's display WSI: one 4320×2160 display at
  90 Hz. `pico4-swapchain-usage` holds the stock path's pacing: four images, one frame ahead, a lead of 5 ms.
- **Tracking.** `driver_monado` runs the step-5 driver inside SteamVR: the headset and both controllers in 6DoF.
- **Measured.** `dev check`: 90.03 commits/s over 30 s, 0.0 % late, no stalls, hottest zone 65.5 °C. Worn, no
  visible stutter with Steam's panel in view. Beat Saber (Proton and FEX) submits a median 44.8 frames/s while
  the compositor presents 89.6/s, held by the GPU at its top clock (92-96 % busy), not the CPU (every core below
  50 %); POOLS is GPU-limited at about 32 frames/s. Games start at 72 Hz, the Frame's default; SteamVR offers 72
  and 90 Hz.
- **Apps.** The Steam client signs in and starts games from its library, x86 games through Proton and FEX,
  Android apps through Lepton. Steam Link plays PC games with picture, sound, tracking and controllers, and its
  end returns to the local session: first picture about 12 s after the client starts (nearly all the PC's
  startup), a decoder reset at the first video of at most about 0.6 s, 30-43 ms from a decoded picture to its
  illumination (floor about 22 ms).
- **Heat.** `pico4-fan.service` runs PICO OS's fan policy ([cooling.md](cooling.md)): about 10 minutes of a worn
  game peak at 75.6 °C; the limit is 90 °C.
- **Other hardware.** Speakers and microphone are PipeWire's default sink and source, without xruns under the
  session ([userspace.md](userspace.md#audio)); SteamVR's sound and volume work, the microphone records at PICO
  OS's gain of 12, PICO OS's calibration loads per stream. Battery level and charging reach UPower and SteamVR.
- **Onboarding.** From an empty data partition, `pico4-onboarding` runs the first-run setup as SteamVR's scene in
  the room view: language (all 31 of the client's), time zone, Wi-Fi with its password page, controllers and the
  lens distance, in the client's words and fonts; the client then starts in the chosen language. It pairs
  controllers hands-free when none is there and shows the PICO 4's introduction once after sign-in; worn, the
  setup, the pairing and the introduction's replay work ([session.md](session.md#first-run-setup)). The client's
  screen reader (Orca) speaks.
- **Acceptance.** `dev check` runs 30 s of Valve's session on the installed system, with the headset and both
  controllers in SteamVR.

## Open work

**Open.**

- The direct boot's remaining checks: the power key held 2 s in charger mode, a boot without the PC, `dev check`
  with both controllers connected ([install.md](install.md#open-work)). Verified: preinit to the graphical
  target in 9.7 to 11.0 s, charger mode up to the key, the Wi-Fi re-probe, the keys on the data partition, a
  stopped PID 1 restarting the headset after 33 s, and the maintenance stage 1 after two boots in a row that
  never reached the graphical target.
- A short power press after more than an hour asleep. After an RTC wake from 3 h asleep, with the cable in,
  Steam suspended again within about 4 s, while 80 idle minutes in SteamVR's standby brought no suspend; whether
  a key wake behaves alike is not known.

**Not verified.**

- On the headset: a restore from an imported backup on another computer, `dev restore` interrupted part way
  ([install.md](install.md#an-interrupted-restore)), and a panic in the maintenance stage 1 on Valve's kernel. The
  installer's restore, install from PICO OS and upgrade ran on the command line with that stage 1; the window ran
  the restore with the stage 1 on the stock kernel.
- A second headset: everything ran on one unit ([device-preparation.md](device-preparation.md#verified-hardware)).
- Linux and macOS as the host: the host tests cover both, but no headset was installed from either (Windows
  installed it); the SSH proxy the headset's build downloads through (`ssh -R`), the Python assets extraction and
  EDL device counting through pyusb are not run on a Mac.
- The first-run setup's failure page (a refused password, a network out of range) worn: drawn and host-tested
  only. Its texts in the 30 languages besides English are machine-assisted and not reviewed. The screen reader's
  voice choice in the client's settings is not tried.
- Battery life unplugged: only inferred with the cable in.
- Sessions much longer than 10 minutes ([cooling.md](cooling.md)).
- MangoHud's `mangoapp`, the client's performance overlay: switching it on from the client's menu.
- PICO OS's head tracker with a covered camera and in a dim room.
- Bluetooth runs through `hci_qca` with PICO OS's firmware and the Frame's BlueZ ([kernel.md](kernel.md)); a
  paired device is not tested.

**Limits.**

- The kernel's `step_wise` zones throttle the CPUs from 90 and 95 °C and the GPU from 95 °C.
- PICO OS's speaker protection is absent: the amplifiers' voltage and current sense is off
  ([userspace.md](userspace.md#audio)).
- `vrcompositor` dumps core when the session stops ([session.md](session.md)).
- MPEG-2 ends in a session error from the firmware; H.264, HEVC, VP9 and VP8 decode through Venus.
- A controller that lies still from the start with a still headset can stay unacquired, as with PICO OS's rules
  ([xr.md](xr.md)).
- The controllers' battery level goes to SteamVR as a fifth of a full charge per level; that 5 is the top of the
  scale is inferred.
- The floor height of SteamVR's room setup is hard to set exactly.
