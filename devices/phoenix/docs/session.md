# Valve's VR session

Roadmap step 6 ([roadmap.md](roadmap.md)): the Steam Frame's VR session on the PICO 4, on Valve's
Steam Frame 7.2 kernel.

| | |
| --- | --- |
| Works (worn) | 90 frames/s on both panels; the Steam client signs in and runs its library: x86 games through Proton and FEX, Android apps through Lepton; Steam Link from a PC |
| How | Valve's `gamescope-session.target`: gamescope (OpenVR backend) and SteamVR 2.17.10, whose compositor drives the panels in direct mode through Turnip's display WSI on msm; `driver_monado` supplies the headset and both controllers in 6DoF from the PICO 4 driver of [xr.md](xr.md) |
| Code | units, drop-ins, scripts and SteamVR files in [`pico4-session`](https://github.com/overpressure/packages/tree/main/phoenix/pico4-session), the checks in [`pico4-checks`](https://github.com/overpressure/packages/tree/main/phoenix/pico4-checks); `pico4-monado`, `overpressure-gamescope`, `overpressure-mangohud`, `pico4-swapchain-usage` in [overpressure/packages](https://github.com/overpressure/packages); setup panel in [pico4-monado's tools](https://github.com/overpressure/packages/tree/main/phoenix/pico4-monado) |
| Results | [kernel.md](kernel.md#results) |

Valve's packages come from the Frame's public repository ([userspace.md](userspace.md)); the build
output stays private.

## What the Frame runs

From its packages (`deckard-steamvr-session` 1.6.1-8, `deckard-steamvr-rel` r25358740,
`deckard-steam-rel` r1790377368) and the field notes of
[frame-control](https://github.com/saphid/frame-control) and
[steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos):

```text
sddm (autologin steamos) ─► start-gamescope-session gamescope-session.target   (user manager)
  gamescope-session.service   gamescope --backend openvr --xwayland-count 2 ... (one SteamVR overlay per app)
  steamvr.service             vrstartup → vrserver, vrcompositor, vrdashboard (CEF)
  steam.service               Steam client (-steamos3 -vrgamepadui), unpacked into ~/.local/share/Steam
system: dsp_service, steamvr-program-ble (controller radio firmware), deckard-fpga, v4l2loopback, ...
```

- **Display.** `vrcompositor` drives the panels in *direct mode*: it finds the display through
  `VK_KHR_display`, acquires it with the DRM fd (`VK_EXT_acquire_drm_display`) and paces itself with
  `VK_EXT_display_control`'s vblank events. The Frame's display is also one 4320×2160 DSI connector on `msm`.
- **Tracking.** `driver_cv` with Valve's camera tracking on the Frame's DSP (`dsp_service`), FPGA and
  controller radio; none of this hardware exists on the PICO 4.
- **OpenXR.** SteamVR is the session user's OpenXR runtime (`~/.config/openxr/1/active_runtime.json`).

## How the PICO 4 runs it

| Frame | PICO 4 |
| --- | --- |
| sddm autologin | `pico4-session.service`: a logind session for `steamos` through PAM, whose user manager starts the session target |
| `driver_cv`, `dsp_service`, FPGA, radio firmware | `driver_monado` with the PICO 4 driver of [xr.md](xr.md) in `vrserver` |
| Direct mode through Valve's Turnip on `msm` | the same, paced by `pico4-swapchain-usage` (four images, one frame ahead, vblanks signalled 5 ms early) |
| DRM syncobjs, System V semaphores | the same; the kernel provides them |
| `driver_frame_hmd`'s SteamVR settings | the session user's `steamvr.vrsettings` ([below](#steamvr-configuration)) |
| Steam client | the same package and unit ([below](#the-steam-client)) |
| — | Monado as a system service, the OpenXR runtime outside Valve's session |

### The session

[`pico4-session.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-session.service) (wanted by
`graphical.target`, needs `/dev/dri/renderD128`) runs
[`/usr/libexec/pico4/session`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/session) as `steamos` (uid 1000)
through the PAM stack [`pico4-session`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-session), without
authentication. `pam_env` runs before `pam_systemd` and sets class `user`, type `wayland` and seat
`seat0` ([`session.pam-env`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/session.pam-env)), as sddm does: a session
without a terminal would not get class `user`, and only with it does logind start the user manager and
count the session as active. gamescope opens the seat through logind (libseat). The unit also pulls in
`pico4-slamd` (the head tracker) and `pico4-vr-performance` (PICO OS's VR CPU limits,
[cooling.md](cooling.md)).

The script waits up to 10 s for the user manager, waits until a previous session's SteamVR has stopped,
resets the user units' failure counters (`systemctl --user reset-failed`) and execs Valve's
`start-gamescope-session` with `gamescope-session.target`, or the target `/etc/pico4/session.conf`
names (`PICO4_SESSION_TARGET=...`). SteamVR's first start links it as the OpenXR runtime. There is no
session without SteamVR.

Restarts:

- A session that ends by itself starts again after 5 s (`Restart=always`, as sddm's `Relogin=true`).
  Five starts within ten minutes stop it until the next boot or a deliberate start; deliberate restarts
  reset the count first (`systemctl reset-failed pico4-session.service`).
- A SteamVR that ends without a requested stop (a crashed `vrcompositor`) ends the session
  ([`steamvr-ended`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steamvr-ended)). Restarting only SteamVR
  would leave gamescope bound to the dead compositor: every DMA-BUF import of Steam's panel fails and
  the panel never shows. gamescope ignores the stop; its unit's 10 s end it.
- A SteamVR that failed for good (its start limit, [below](#valves-packages)) ends the session through
  `pico4-steamvr-failed.service`; otherwise `gamescope-session.target` stays up without SteamVR and the
  lenses stay dark.
- A requested restart of `steamvr.service` keeps the session and restarts gamescope with it. A
  deliberate stop stays stopped.

[`70-pico4-xr.rules`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-xr/70-pico4-xr.rules) opens the XR hardware
to the `video` group, of which `steamos` is a member: the controller radio (`spidev0.0`), the lens MCU's
UART (`ttyHS1`), the cameras' V4L2 and media nodes, the DRM card nodes and the dma-buf heaps.
`pico4-userspace-check` confirms `steamos` can open `/dev/dri/card0`, `/dev/spidev0.0`, `/dev/ttyHS1`,
`/dev/media0` and `/dev/video0`.

### Valve's packages

The build installs `deckard-steamvr-rel` (SteamVR), `deckard-steamvr-session`, `jq` (its `steamvr`
script), `at-spi2-core` (the dashboard's `vrwebhelper` links ATK and AT-SPI), `xorg-xhost` and
`xorg-xrdb` (`gamescope-onready` runs them) and the Steam client ([below](#the-steam-client)). `sddm`
counts as installed for dependency resolution only (`!sddm` in [packages.txt](https://github.com/overpressure/packages/blob/main/headsets/phoenix/packages.txt)).

Not extracted (`NoExtract` in [pacman.conf](https://github.com/overpressure/packages/blob/main/headsets/phoenix/pacman.conf)): the Frame's tracking driver
(`drivers/cv`), the firmware of the Frame's headset, its controller radio and other controllers, the
Windows binaries, and the eye-tracking and calibration tools. The Index, Vive, Touch and Frame controller
resources stay: SteamVR's remapping for the PICO 4's controllers needs their types, and the Frame's
Android games bind only the Frame controller ([Controllers in games](#controllers-in-games)). SteamVR's
Android client stays for Lepton. SteamVR's desktop tools (`vrmonitor`, `overlay_viewer`, `hellovr_sdl`)
are installed without the Qt5 and GLEW they need.

Masked system units: `sddm`, `dsp_service`, `steamvr-program-ble` (a firmware OTA for the Frame's radio,
which must never run against the PICO's station), `steamvr-set-kernel-thread-priorities` (its script
stops at its first entry here; `pico4-kthread-priorities` sets its values), `steamvr-v4l2loopback`,
`steamvr-web-debug-portforward`, `steam-web-debug-portforward`. Masked user units (global):
`steamvr-v4l2cam`, `steamvr-proxmicmute` and `steamos-scheduler` (`scx_lavd` needs an option Valve's
kernel configuration leaves out).
[`pico4-micmute`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-audio/pico4-micmute.service) replaces
`steamvr-proxmicmute`: it mutes PipeWire's default microphone while the wear sensor reports the headset
off the head (`$XDG_RUNTIME_DIR/pico4-worn`, written by the driver). The input method,
`ibus-gamescope`, runs with the Frame's `ibus`.

`steamvr-logs` forwards SteamVR's logs to the journal (identifiers `vrserver`, `vrcompositor`,
`vrclient_*`). Its `inotify_simple` is installed for Python 3.12 only; the build links it into the
image's Python.

**gamescope** is the Frame's version, upstream commit `ea3579ed` (Valve's 3.16.28-1), built as
`overpressure-gamescope` with one fix: its window manager added a window again on ReparentNotify to the root,
and the duplicate entries read a freed Vulkan override surface every vblank, which crashed gamescope
and the session. The patch makes `add_win()` skip a window it already lists (upstream's master still
has the bug). The build bundles libliftoff, vkroots, wlroots, libdisplay-info and wayland-protocols
1.47 statically, as Valve's binary does, with SDL2, AVIF screenshots, libei, PipeWire, DRM and OpenVR.

**MangoHud.** gamescope's session starts `mangoapp`, the Steam client's performance overlay. The Frame's
MangoHud links libraries of its private base, so `overpressure-mangohud` builds upstream's 0.8.4 from its
release archive with bundled subprojects, without NVIDIA support. The build fails if
`gamescope-session` does not start `mangoapp` or the program is missing: gamescope's reaper restarts a
`mangoapp` that cannot start without end. In VR mode each layer of an app's frame is a subview of its
SteamVR overlay (`….layer1` to `….layer7`); `mangoapp`'s window is one of them, so the HUD shows over
apps in gamescope, not over the Steam client's own UI (a SteamVR overlay gamescope forwards). Only the
panels show it, not gamescope's screenshots.

Further adaptations:

| Where | What | Why |
| --- | --- | --- |
| [`gamescope-session.service` drop-in](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/gamescope-session.service.d-50-pico4.conf) | `WLR_LIBINPUT_NO_DEVICES=1` | input comes through SteamVR's overlays and gamescope's EIS socket |
| | `MESA_LOADER_DRIVER_OVERRIDE=zink` | OpenGL on the GPU for Xwayland's glamor (and DRI3 for X11 clients), zink on Turnip ([graphics.md](graphics.md#opengl)) |
| [`steam.service` drop-in](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steam.service.d-50-pico4.conf) | `MESA_LOADER_DRIVER_OVERRIDE=zink` | OpenGL on the GPU for the client, its web helper and its games |
| | `STEAM_EXTRA_ARGS=-cef-use-vulkan` | the web helper composites on ANGLE's Vulkan backend; on its OpenGL backend the UI flickers ([The Steam client](#the-steam-client)) |
| | `STEAM_LAUNCH_WRAPPER_AFFINITY_LIST`, `STEAM_DESKTOP_APP_AFFINITY_LIST` `0xf0` | games on cores 4–7 ([The Steam client](#the-steam-client)) |
| | `STEAM_ENABLE_VOLUME_HANDLER=1` | without it Steam shows the volume keys' popup but leaves the volume alone |
| | `ExecStartPre` `steam-proton-mapping`; `wait-first-run` (`55-pico4-first-run.conf`) | Proton stable instead of Experimental; the [first-run setup](#first-run-setup) |
| [`steamvr.service` drop-in](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steamvr.service.d-50-pico4.conf) | `XRT_COMPOSITOR_SCALE_PERCENTAGE=100` | `driver_monado` recommends the panel's render size, not Monado's 140 % |
| | `PICO4_FRAMES_AHEAD=1`, `PICO4_VSYNC_LEAD_US=5000` | the pacing of [SteamVR's display](#steamvrs-display) |
| | `ExecStartPre` `steamvr-settings` | the image's SteamVR settings into the user's file ([below](#steamvr-configuration)) |
| | `ExecStop=-…vrstartup -shutdown` (exit status ignored) | at a session stop `vrserver` is often gone and the shutdown request exits 255; counted as failed launches, three in a row make Valve's `steamvr-health-check` reset the Steam installation. A crash still fails the unit |
| [`/usr/bin/displays_turn_off_drm_master`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/displays_turn_off_drm_master) | does nothing | stands in for the Frame's tool that `steamvr.service` runs at `ExecStopPost` (a missing program fails the unit) |
| [`pico4-kthread-priorities.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-kthread-priorities.service) | the Frame's values: `gpu-worker` and `crtc_event:N` SCHED_RR 99, `atomic-worker-N` 88, `spi0` and the soundwire interrupt threads 80; workqueues `msm`, `ring0`–`ring3`, `dsi_drm_work_0/1` priority 90 (Valve's `prio` attribute) | SteamVR's threads run at SCHED_RR 99 and would hold back their own submissions and flips; `ring0` at nice 0 delays the compositor's GPU jobs by up to 6 ms |
| `/opt/steamvr/bin/linuxarm64/vrcompositor-launcher` | `cap_sys_nice=eip` | the compositor raises its priority |
| `~/.steam/steam`, `~/.steam/root` | links to `~/.local/share/Steam` | SteamVR finds its logs and Steam through them (the Frame's `RUNSTEAM.sh` makes them) |
| [`steamvr.service` start limit](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/50-pico4-restart-limit.conf) | restarts 25 s apart (`RestartMode=direct`), at most three starts in five minutes, then `pico4-steamvr-failed.service` ends the session | Valve's unit restarts always and a start takes 15–20 s, so systemd's default limit never applies; each start resets the controller radio, so a crash loop buzzed the controllers without end. `RestartMode=direct` keeps the unit out of the failed state between restarts. The session script resets the counter per session |

### The Steam client

`deckard-steam-rel` (a native aarch64 client) installs the client as a 671 MiB bundle,
`/usr/lib/steam/steam.tar.zst`, with Valve's `steam.service`, which `gamescope-session.target` wants.

- **First start.** `steam-health-check --track-started` finds no `~/.local/share/Steam/.install-complete`
  and unpacks the bundle there (2.5 GB, on the data partition). `select_steam.sh` and `RUNSTEAM.sh` then
  run `steamrtarm64/steam -deckard -gamepadui -steamdeck -steamos3 -vrgamepadui`; its UI is a SteamVR
  overlay through gamescope.
- **Update.** The bundle is older than the current client of its beta branch (`linux_arm64_beta_…`), so
  the first start downloads about 650 MB, installs it and restarts (exit status 42), once per release.
- **The client's own setup (OOBE)** does not run. `steamui.so` counts `oobe_completed` and
  `oobe_stage_2_completed` (`CompletedOOBEStage1`, `CompletedOOBE` in `~/.steam/registry.vdf`) as done on
  SteamOS whenever the login controller knows no account, unless `/etc/steamos-oobe-image` (Valve's
  factory image) exists. An empty data partition therefore goes straight to the sign-in, which writes
  both keys. Force OOBE (`ForceOOBE`) runs it only in test mode. On the factory image stage 1 is welcome,
  language, time zone, network and update; stage 2, after the login, Steam Controller pairing and
  firmware, controller pairing, underscan and audio. The [first-run setup](#first-run-setup) covers it.
- **Time zone.** The client's setting runs `/usr/bin/steamos-polkit-helpers/steamos-set-timezone ZONE`
  (one of its 62 zones). The image's
  [own helper](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steamos-set-timezone) has timedated set
  a zone of the system's zoneinfo, which
  [polkit](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/50-pico4-timezone.rules) allows the session user.
- **Device type.** `steamclient.so` sets it from the device tree's first `compatible` entry and `model`:
  5001 (Steam Frame) for `qcom` with a model containing `SM8650`; `valve`, `lenovo` and `OEM` entries give
  544 (Deck), 545 (Legion Go S) and 546 (Steam Machine); anything else on SteamOS is 541 (generic). The
  PICO 4's `pico,phoenix` gives 541. The client sends it to Steam's servers; the guided tour, the Frame's
  controller pairing settings, the library's Frame compatibility labels and the hardware names in its
  texts follow it.
- **Guided tour.** The Frame's tour (controller pairing, health, playspace, laser mouse, system button,
  PC pairing) starts in VR only on device type 5001, so not here; replayed from the quick access menu's
  help it shows the generic SteamOS pages. Its done-mark is `Deck_GuidedTourVersionSeen` in
  `localconfig.vdf`.
- **Undeclared libraries.** The current client needs GTK 2 (`libgtk-x11-2.0.so.0` for `steamui.so` and
  `vgui2_s.so`; without it it exits 255 at once) and IBus (`libibus-1.0.so.5` for `steamwebhelper`). No
  aarch64 repository has GTK 2, so the build makes `overpressure-gtk2` from GTK 2.24.33 (libraries and modules
  only); the Frame's repository has `libibus`. The client polls processes with `lsof`. Its `libvideo.so`
  links the FFmpeg 8 and libvpx 6 it brings in `steamrtarm64`, resolving with the image's libva.
- **Repair loop.** The client's health check counts a start that ends within 5 s, or with an error within
  two minutes, as a failure; after five in a row it unpacks the bundle again, undoing any update. SteamVR's
  health check does the same after three failed SteamVR launches in a row (counted in `$XDG_RUNTIME_DIR`,
  cleared by a reboot).
- **Launch timeout.** SteamVR's dashboard shows a spinner until the client's VR interface sets its
  overlay's ready flag; after 120 s it shows "There was an issue launching Steam" with one button,
  "Restart Steam Frame", which restarts the headset. SteamVR names the Frame there on any hardware.
- **Games.** For Windows games the client downloads Proton 11 (ARM64), which runs the game inside Steam
  Linux Runtime 4 for arm64; pressure-vessel's bubblewrap uses the kernel's user namespaces. Beat Saber
  is playable under Proton and FEX, below real time; SteamVR's reprojection carries it to 90 Hz
  (measurements in [kernel.md](kernel.md#results)). Games with an Android build (Cubism) start through Valve's
  Lepton, in a rootless podman container, as on the Frame.
- **Game cores.** `steam-launch-wrapper` pins games to `STEAM_LAUNCH_WRAPPER_AFFINITY_LIST`. Valve's
  `0xf8` (cores 3–7) includes the XR2's core 3, a Cortex-A55 (`cpu_capacity` 351; the A77s 871, the
  prime core 1024). The image sets `0xf0`, cores 4–7, the mask PICO OS's VR service gives a VR app
  (`qvrservice_config.txt`). The GPU's top level is 587 MHz, PICO OS's VR level: the 670 MHz OPP is not
  in this speed bin.
- **Which Proton.** The client takes Valve's per-game recommendation: Proton stable (11.0 ARM64, DXVK
  2.7) for Beat Saber and Pools, Proton Experimental for others (The Lab). Experimental's DXVK 3.1
  requires `storageBuffer8BitAccess`, which Turnip offers only on Adreno 7xx; on the Adreno 650 DXGI
  finds no adapter and the game crashes in `dxgi.dll`. `steam-proton-mapping` (`ExecStartPre`, while the
  client is stopped) maps every game whose latest recommendation is Experimental to Proton stable in
  `config.vdf` as a user mapping (priority 250); the user's own mappings and the rest of the file stay
  unchanged, and new games are covered from the client's next start. On Proton stable The Lab starts with
  head tracking and controllers, far below real time. Inside a game's container SteamVR's client does
  not see `/usr/share/steamvr-monado` and logs "driver not found"; games start without it.
- **UI on the GPU.** On ANGLE's OpenGL backend (zink on Turnip) the client's overlay alternates between
  the new and an older frame from the moment a dialog opens until the next full repaint.
  `-cef-use-vulkan` (`--use-angle=vulkan`, commented out in the Frame's `RUNSTEAM.sh`) puts ANGLE on
  Turnip directly: no flicker, and the web helpers use 0.84 instead of 0.92 cores on the sign-in page.
  CPU compositing (`-cef-disable-gpu-compositing`) does not flicker either but takes 2.06 cores.
- **The Frame's system updater is not installed** (`atomupd-daemon`, `steamos-atomupd-client`): it must
  never run here. The client polls `steamos-select-branch` and `steamos-update`, which call
  `atomupd-manager`; the image's [`atomupd-manager`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/atomupd-manager)
  answers as an up-to-date system on the stable branch (`rel`, "No update available") and refuses to
  switch or update. The image's `/usr/bin/steamos-polkit-helpers/steamos-update` calls it directly (no
  root needed). OS and BIOS checks answer "up to date" (`steamos-update` exit 7, `jupiter-biosupdate
  check` exit 0). Once per start the updater proxy request over D-Bus fails (no `atomupd-daemon`), and
  the Steam Deck's `jupiter-initial-firmware-update check` and `jupiter-dock-updater --check` end in 127,
  as on the Frame: no repository ships them. `steam-runtime-launcher-service` is not packaged for arm64;
  the client disables it.

### Onboarding

What a new owner meets, from an empty data partition:

| Part | Covers | Here |
| --- | --- | --- |
| Steam's OOBE ([the client](#the-steam-client)) | welcome, language, time zone, network, update, sign-in; stage 2: Steam Controller, underscan, audio | does not run: only on Valve's factory image |
| [First-run setup](#first-run-setup) | welcome, language, time zone, Wi-Fi, controllers, lens distance | before the first sign-in, once |
| The Frame's guided tour | pairing, health, playspace, laser mouse, system button, PC pairing | does not run (device type 541) |
| SteamVR | playspace setup for a new room ([xr.md](xr.md#room-setup)) | when a game starts in a new room, or from Space Setup |
| Introduction | the PICO 4's controllers, health and safety, laser and PICO button, the playspace setup | after the first sign-in, once |

The setup and the introduction are [`pico4-onboarding`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-onboarding.cpp)
([`pico4-onboarding.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-onboarding.service), part of
`steamvr.service`): a SteamVR overlay 1.3 m in front of the wearer, level, over other overlays. It comes
back in front when the headset is put on and a second after it left the view (more than 30 degrees
aside, or 0.3 m above or below the eyes); while looked at it stays. During the setup it is SteamVR's
scene application, otherwise a background one.

- **Controllers.** With no controller connected 20 s after the start, the station listens for one
  controller after the other, 60 s each, through the driver's pairing socket
  ([xr.md](xr.md#pairing)), while the panel says what to press (trigger and PICO button together for
  about 6 s, PICO OS's instruction). With one controller connected, its laser pairs the other from the
  panel's Pair button.
- **Introduction.** Once after the first sign-in (`CompletedOOBE` in `~/.steam/registry.vdf`): the
  controllers, health and safety, the laser and the PICO button (press: the menu; hold: recenter), the
  playspace. Skip or Done writes `~/.local/share/pico4/onboarding-done`; the dashboard's Introduction tab
  shows it again.
- **Not covered:** the Frame tour's PC pairing; Steam Link is in the menu ([Steam Link](#steam-link)).

### First-run setup

Before the first sign-in the panel replaces the client's own setup. The client unpacks itself first (the
panel shows its texts and fonts), then waits in
[`wait-first-run`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/wait-first-run) (`ExecStartPre` of
`steam.service`) until the setup is finished or skipped, someone signed in before, or the panel's service
failed for good. Until then `gamescope-session.target` stays in its start job, and the checks
(`pico4-userspace-check`, `pico4-valve-session-check`) take SteamVR, gamescope and the setup's panel for
the session (`wait-first-run --pending`). During the setup:

- **Scene application.** It submits a frame to both eyes at every SteamVR frame, an even dark room, which
  the room view covers.
- **No wait for Steam.** `/settings/dashboard/expectSteam` is off; with it on, the system UI shows a
  spinner until Steam's UI is up and after 120 s the error whose only button restarts the headset.
- **Dashboard closed.** The system UI opens its dashboard at start, over the panel; the setup closes it
  as the system button does (`hide_dashboard_requested` to the mailbox `vrwebui_dashboard`). The system
  button still opens it.
- **Keyboard started.** SteamVR's keyboard starts on the first message to the mailbox
  `keyboard_vrwebui/main`, which the system UI sends only while its dashboard is fully visible; without it
  "Rejecting ShowKeyboard call because no keyboard implementation exists". The setup sends that message
  itself. At its first showing the keyboard loads its layout and passes no key ("render layout==null"),
  so the setup shows it once, 2 s after starting it, for 2 s.
- **Room view on** (the compositor's `set_camera_roomview_enabled`, [xr.md](xr.md#passthrough)); the dark
  room shows only while the cameras have no image. The system UI sets the room view as it starts, so the
  setup waits for its overlay (`system.systemui`) and 5 s more for its mailboxes.

SteamVR refuses most settings and messages from a scene application, so a short background application,
[`pico4-steamvr-switch`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-steamvr-switch.cpp), throws these switches. Messages go through
vrclient's `IVRMailbox_002`: its `open` takes a third string after the name and the handle, which must be
`nullptr`; any other value crashes the caller. A settings file written before SteamVR starts does not
work: on a first start SteamVR writes its own copy back. Steam's own path property `/steam/oobe` is not
used: without the client the dashboard's contents it pins are SteamVR's own bar. When the setup ends the
room view goes off, `expectSteam` on, the setup leaves its mark and restarts as the background
application for the introduction, and the client starts.

The client's own words come from its files at runtime (localization files and language-screen module in
`~/.local/share/Steam/steamui`): its welcome, language and zone names, the network page's texts and the
buttons. The panel's other texts are [pico4-onboarding.strings](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-onboarding.strings), in
all 31 of the client's languages; English is the reference, the others are machine-assisted translations
awaiting a native speaker's review. Pango lays them out; the image's fonts cover Latin, Greek and
Cyrillic, the client's Noto font (`clientui/fonts`) every other script.

| The client's page | Here |
| --- | --- |
| Welcome | the client's welcome, the controllers' state; Skip leaves the setup for good |
| Language | the client's 31 languages by their own names; a choice sets the client's language in `registry.vdf` ([`steam-language`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steam-language); a running client restarts, as its own setting does) and the panel's |
| Time zone | the client's 62 zones with its names and cities, set through `steamos-set-timezone` |
| Network | Wi-Fi networks (`nmcli`), strongest first; Wi-Fi turned on if off. A password page as in the client's settings: a field, a show switch, and SteamVR's keyboard in minimal mode, which sends each key to the panel and keeps no text. The password goes to `nmcli --ask` on its input only. A hidden network's name comes first on the same kind of page. The attempt shows a spinner, then a tick, or a cross with the reason and the password page again; a refused password's profile is deleted. Proxy and captive portals are in the client's settings |
| Update | none: the system is updated from the installer on a computer; the client updates itself at every start |
| Screen reader | none on the panel, which draws its pages itself; the client's own screen reader is as on the Frame: Orca (`orca.service`, started by `steamos-manager` when the client's setting turns it on), speaking through speech-dispatcher with espeak-ng |
| Stage 2: Steam Controller | none: no Valve radio; a Steam Controller pairs over Bluetooth in the client's settings |
| Stage 2: controller pairing | the PICO 4's controllers, as above |
| Stage 2: underscan | none: no television |
| Stage 2: audio | none: the headset's speakers; other devices in the client's settings |
| (none) | the lens distance, 62 to 72 mm in 0.5 mm steps, through `pico4-lens` |

Done on the last page, or Skip on the first, writes `~/.local/share/pico4/setup-done` and the client
starts; the introduction follows the first sign-in.

The pages' logic, zones, languages and the readers of the client's files and nmcli's output are in
[pico4_setup.h](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_setup.h) and [pico4_onboarding.h](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_onboarding.h), tested
on the host. `pico4-onboarding --render DIR [LANGUAGE]` draws every page as PNG without SteamVR.

| Check | Result |
| --- | --- |
| Worn, after an install with both controllers off | the panel listened for the left controller hands-free; the right one connected when switched on and its laser paired the left; the introduction ran to its end, left its mark and replayed from the Introduction tab |
| The setup's pages worn, from an empty data partition | open |

### Monado as a service

A development runtime outside Valve's session: the driver's OpenXR test clients (`pico4-openxr-pose`,
`pico4-openxr-display`, [xr.md](xr.md)) run through it with the session stopped.
[`pico4-monado.socket`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-monado.socket) listens on
`/run/pico4-monado/monado_comp_ipc` (mode 0666); the first client starts
[`pico4-monado.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/pico4-monado.service), which takes the
display in direct mode with three swapchain images.

### SteamVR's display

`vrcompositor` presents through `VK_KHR_display` on Turnip's display WSI: one 4320×2160 display at 90 Hz.

- **Pacing and compressed scanout.** [`pico4-swapchain-usage`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-swapchain-usage/pico4-swapchain-usage.c),
  an implicit Vulkan layer active only in `vrcompositor`, removes the storage usage the compositor never
  uses, so its swapchain images can be compressed, and paces it: four images, one frame ahead
  (`PICO4_FRAMES_AHEAD`), vblanks signalled 5 ms early (`PICO4_VSYNC_LEAD_US`). It publishes the lead in
  `$XDG_RUNTIME_DIR/pico4-display-lead-us`, and `driver_monado` predicts that much further.
- **What the check sees.** `msm-frame-stats` reports the web helpers' share of the GPU and how long
  before its vblank each frame's last compositor job ended; a vblank without a job counts as 0. The share
  depends on the client's library home: for a minute or two after the client starts it zooms its art and
  redraws its blur filters (about 12 backdrop blurs of 100 px over a blurred, zooming image, at
  device pixel ratio 1.5) every frame, 55 web frames/s at about 4 ms of GPU each, 22–24 % of the GPU; the
  compositor and the web helpers then fill the 11.1 ms of a refresh, and the margin varies from 0.1 to
  5.3 ms between session starts. Afterwards the panels stay visible and the share is 0 %. The check
  measures after that phase (`msm-frame-stats quiet`). Turnip's tile mode and ANGLE's backend change
  that cost by less than 15 %; the client's own Low Performance Mode (`library_low_perf_mode`, also set
  by Reduce Motion) turns those effects off, and is off as on the Frame. A game that saturates the GPU
  gives 0 ms and fails.
- **Where a frame without a job lost its time.** `msm-frame-stats trace SECONDS PID OUT` records the same
  events with the GPU's submits and flushes and the scheduler's switches; `msm-frame-stats misses OUT`
  splits each late job into the time from the vblank before to its submit, the submit's wait to its flush,
  the GPU's wait and the GPU's own time, against the medians of the frames on time, and names the threads
  that held the submitting thread's core and the other processes' GPU time in that refresh.
- **The compositor crashes at a stop.** About 40 ms after `Cleaning up`, with the render thread and every
  client gone, `vrcompositor` calls a virtual method of a freed object (`vrcompositor+0x106a10`, the
  function with "Unable to release resource of unknown type") and ends with SIGSEGV. Any client overlay
  that ever had an image through `SetOverlayRaw` or `SetOverlayFromFile` triggers it, even a cleared
  1×1 one; images through `SetOverlayTexture` (Vulkan) do not, so the project's panels use Vulkan
  textures. A stop still crashes with the Steam client, the lens panel and the room view all stopped, so
  the remaining source is unknown (SteamVR's dashboard is untested), as is whether other SteamVR
  installations do this. It costs a core dump of about 6 MB: `coredump.conf.d/50-pico4-limit.conf` keeps
  core dumps within 1 GB, and `steamvr.service`'s result stays clean.

### SteamVR configuration

The session user's [steamvr.vrsettings](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steamvr.vrsettings) (in
`~/.local/share/Steam/config`):

- disables `driver_cv`, `driver_frame_hmd`, `driver_frame_controller`, `driver_indexhmd`,
  `driver_indexcontroller`, `driver_htc` and `driver_prism`, enables and forces `driver_monado`
  (`forcedDriver`, `activateMultipleDrivers` off, `requireHmd`), and sets `enableLinuxVulkanAsync`:
  without it the compositor creates no compute queue and stops at an assertion; with it, it shares
  Turnip's single queue;
- carries the Frame's compositor, dashboard, power and tessellation settings from
  `frame_hmd_additional.vrsettings`, which SteamVR loads only with the Frame's driver: the Facet renderer,
  the system layer in the distortion pass (2x MSAA, 1400×1400), simplified and achromatic distortion
  shaders, scanout scaling, overlay quality 1, the blue dome, 72 Hz as the default per-app refresh rate.
  Left out are only eye tracking, cameras, VR link, the CV driver and SteamOS power profiles;
- follows the wear sensor for standby: `driver_monado` reports `/proximity` ([xr.md](xr.md)) and
  `power.pauseCompositorOnStandby` is on;
- carries the Frame's room-setup settings: a prompt for room setup when a game starts without a play
  area (`promptForChaperoneAtGameLaunch`, `requireChaperoneAtGameLaunch` off), no floor detection at the
  start, no automatic re-centring of the play area or on user interaction, boundary style 2.

The file lives on the data partition, copied from the image only on an install's first boot, and SteamVR
rewrites it; [`steamvr-settings`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/steamvr-settings) sets every key
of the image's copy (`/usr/share/pico4/steamvr.vrsettings`) in it before each start and keeps all others.
[openvrpaths.vrpath](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/openvrpaths.vrpath) (in `~/.config/openvr`) registers
`/usr/share/steamvr-monado` as an external driver, the runtime in `/opt/steamvr` and configuration and
logs under `~/.local/share/Steam`; `steamvr_first_init.sh` keeps both files. `pico4-monado` installs
`driver_monado.so` in `bin/linuxarm64`, where SteamVR for linuxarm64 looks (Monado's install says
`linux64`).

[`monado-steamvr-pico4.patch`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/monado-steamvr-pico4.patch) adapts
`driver_monado`:

- the render target is one eye (2160×2160): OpenVR asks per eye, Monado's screen spans both;
- the controllers' pose is the PICO 4 aim pose, the frame of their render models, which are PICO OS's
  own (`{monado}pico4_controller_left`, `_right`, [xr.md](xr.md#controllers)), with a haptic output;
- positions marked valid count, not only tracked ones, so controllers not yet acquired appear where the
  arm model places them;
- inputs a controller lacks are left out: one profile serves both hands, A/B on the right, X/Y on the left;
- the headset answers `IVRDisplayComponent_003`'s `ComputeInverseDistortion` with the exact inverse of
  its lens function (`pico4_hmd_compute_inverse_distortion`, a bisection along the ray from the lens
  centre). The Facet renderer builds its distortion mesh from it; SteamVR's numeric inversion fails at
  the border of the view, where stretched triangles flicker magenta during head turns.

SteamVR's dashboard has default bindings only for some controller types, so
[`pico4-vrcompositor-bindings.json`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-vrcompositor-bindings.json)
is the profile's `default_bindings` for `openvr.component.vrcompositor`: a press of `/input/system` (PICO
button right, menu button left) toggles the dashboard, a double press the room view
([xr.md](xr.md#passthrough)), holding the PICO button recenters; the aim pose is the laser, the trigger
clicks, the grip goes back, the joystick scrolls. The dashboard's content is the Steam client's VR UI
(`vrgamepadui`), so it shows once the client runs.

#### Controllers in games

SteamVR has its own type for the PICO 4's controllers, `pico_controller`: a stub profile and a remapping
(`default_pico_controller_profile.json`, `default_pico_controller_remapping.json` in SteamVR's
`resources/input`) with inputs under Touch's names. A game gets its own bindings for that type, or its
Touch bindings with Touch simulated, or its Index, Vive or OpenXR generic bindings remapped. vrserver
creates those source types from the resource-only drivers `oculus`, `indexcontroller` and `htc`; without
them the remapping fails ("Could not find input profile for controller type: oculus_touch"). Monado's
generated type, `monado_bytedance_pico4_controller`, has no game bindings and no remapping, so it gives
games no input.

The driver therefore reports `pico_controller` with
[`pico4-controller-profile.json`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-controller-profile.json)
(installed as `pico4_controller_profile.json`), whose `remapping` is SteamVR's own file.
`monado-steamvr-pico-controller.patch` names the inputs as SteamVR's profile does: trigger (value, click,
touch), grip (value, click), joystick (x, y, click, touch), A/B or X/Y (click, touch), `/input/system`
(menu button left, PICO button right) and the haptic output. The poses are the render model's components
(`aim`, `tip`, `grip`, `handgrip`, `openxr_aim`, `openxr_grip`; `tooling/controllermodel.py`).

The Frame's Android games (Cubism) bind only the Frame controller
(`/interaction_profiles/valve/frame_controller_valve`). The image keeps the Frame controller's SteamVR
resources (without firmware), and the build adds its layout
([frame-controller-remapping.json](https://github.com/overpressure/packages/blob/main/phoenix/pico4-session/frame-controller-remapping.json)) to
`default_pico_controller_remapping.json`, the only remapping SteamVR reads for `pico_controller`.
Without it those controllers stay at the origin without input.

### Performance and heat

Measured results are in [kernel.md](kernel.md#results) and [cooling.md](cooling.md); open work in
[roadmap.md](roadmap.md#open-work).

### Steam Link

PC VR streams through SteamVR's Steam Link client, `vrlink` (`/opt/steamvr/tools/vrlink`), as on the
Frame: the PC's SteamVR renders and encodes HEVC (NVENC); `vrlink` decodes it on the headset's video
decoder and shows it through the local SteamVR. The PC's `driver_vrlink` knows the client as "PICO 4".

- **Finding the codec.** `vrlink` asks `vrdevice_path video_decode`; the image's
  [`vrdevice_path`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/vrdevice_path) answers with the node of
  `aa00000.video-codec` whose card is `Qualcomm Venus video decoder` (or `encoder`). It opens only those
  nodes: the Frame's `v4l2-ctl --list-devices` opens every video node, which stops head tracking.
  `steam-video-codec-setup.service` links `/dev/video-dec0` and `/dev/video-enc0`.
- **The decoder** is mainline's Venus (`qcom-venus-decoder`). `vrlink` picks `QC10C` for `P010` (the PC
  sends Main10), `QC08C` otherwise, builds a `DRM_FORMAT_MOD_QCOM_COMPRESSED` image from the reported
  pitch and its own second-plane offset, and queues its dma-buf with the reported size, so both must be
  as iris reports them: the kernel commit “media: venus: compressed capture formats as iris reports them”. Otherwise videobuf2 refuses the dma-buf (`EFAULT`) and nothing
  is decoded.
- **The stream's start.** `vrlink` restarts the decoder after 800 ms without progress, also on the PC's
  first frame, so the stream resumes mid group of pictures. The firmware starts only on a stream's
  headers and otherwise holds every buffer; the kernel commit “media: venus: a decoder without pictures skips what comes before the headers” returns data before the headers to the client
  while no capture queue streams.
- **Buffers.** `vrlink` asks for 16 capture buffers; Venus would raise that to the firmware's 18. Patch
  0027 keeps a client's 16.
- **Controllers.** `vrlink` sends input only for `Prop_ControllerType_String` `frame_controller`, and the
  PC activates a controller only with a serial, a manufacturer and a type. `vrlink` reads poses and
  haptics through legacy actions
  ([pico4-vrlink-bindings.json](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4-vrlink-bindings.json)) and inputs
  by the Frame's paths. `libpico4-vrlink-compat.so`, in `vrlink`'s process only, reports
  `frame_controller` and a manufacturer, and maps the Frame's `thumbstick`, `dpad_down` and `dpad_up` to
  `joystick`, `x` and `y`. SteamVR on the headset keeps `pico_controller`.
- **The codec's address fault.** About 2.5 s after the last client closes the codec, and at boot, the
  IOMMU logs `Unhandled context fault` at address 0 for stream `0x2100` as the codec powers down.
  Decoding after it works (a 22-minute stream, repeated decoder starts), so it is harmless; what the
  codec reads there is unknown.
- **Timing.** The PC's SteamVR writes each frame's times to `vrlink_aftm_*.csv` in Steam's logs.

| Check | Result |
| --- | --- |
| `vrlink`'s path without the PC (Venus decodes into images built as `vrlink` builds them, a compute shader samples them through a YCbCr sampler) | 8-bit and Main10 at 1280×5120: image and reported size match (9,891,840 and 13,885,440 bytes), the picture is identical to FFmpeg's decode; 8-bit 1536×6144: sizes match (14,229,504 bytes) |
| Frames before the first key frame (4, 12 and 40, Main10 at 1280×5120) | first picture 160, 261 and 438 ms after the decoder's start, no errors; without the kernel commit “media: venus: a decoder without pictures skips what comes before the headers” none within 5 s |
| Steam Link from a PC (POOLS on an NVIDIA GPU, 1280×5120 Main10 over Wi-Fi), worn | picture, audio, head tracking and controllers work; about 22 minutes at 90 frames/s in one run |
| The picture's start | about 12 s after `vrlink`'s start, with a decoder reset at the first video |
| Frame times, POOLS | game frame start to illumination 73 ms median (67–78 ms, 5th–95th percentile): encode 11–22 ms, network 4 ms, decode 6 ms, decode end to illumination 30 ms. At 22 ms encode the stream runs at about 67 frames/s, at 11 ms at 90 |

## Build

The session's parts are packages of the userspace archive (`scripts/build-userspace.sh`,
[development.md](development.md)):

| Package | Sources | Installs |
| --- | --- | --- |
| `pico4-swapchain-usage` | [pico4-swapchain-usage](https://github.com/overpressure/packages/tree/main/phoenix/pico4-swapchain-usage), [PKGBUILD](https://github.com/overpressure/packages/blob/main/phoenix/pico4-swapchain-usage/PKGBUILD) | `libVkLayer_PICO4_swapchain_usage.so` |
| `overpressure-mangohud` | MangoHud 0.8.4 release archive, [PKGBUILD](https://github.com/overpressure/packages/blob/main/common/overpressure-mangohud/PKGBUILD) | `mangoapp`, `mangohud`, `mangohudctl`, the MangoHud layer |
| `pico4-monado` | [PKGBUILD](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/PKGBUILD) and patches, [pico4-monado](https://github.com/overpressure/packages/tree/main/phoenix/pico4-monado) | `monado-service`, the OpenXR runtime, `driver_monado` |
| `overpressure-gamescope` | upstream gamescope at `ea3579ed` and one patch | gamescope |
| `deckard-steamvr-rel`, `deckard-steamvr-session` | the Frame's repository | SteamVR in `/opt/steamvr`; the session's scripts and user units |

[`pico4-session`](https://github.com/overpressure/packages/tree/main/phoenix/pico4-session) adds the units, drop-ins, PAM stack, session script and the SteamVR
files, [`pico4-xr`](https://github.com/overpressure/packages/tree/main/phoenix/pico4-xr) the udev rules, [`pico4-checks`](https://github.com/overpressure/packages/tree/main/phoenix/pico4-checks) `pico4-valve-session-check`.

## Run

Valve's session starts at boot. As root on the headset (`python -m overpressure dev ssh COMMAND...`),
minding the heat:

```sh
systemctl restart pico4-session.service
runuser -u steamos -- env HOME=/home/steamos XDG_RUNTIME_DIR=/run/user/1000 \
  timeout 3 /opt/steamvr/bin/linuxarm64/vrcmd --pollposes      # SteamVR's device poses
```

Through the Monado service, with the session stopped:

```sh
systemctl stop pico4-session.service
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 30
systemctl stop pico4-monado.service             # releases the hardware
systemctl start pico4-session.service
```

SteamVR's logs are in `/home/steamos/.local/share/Steam/logs`.

### pico4-valve-session-check

[`pico4-valve-session-check [--seconds SECONDS]`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-checks/pico4-valve-session-check)
runs Valve's session for a bounded time and returns to the configured one:

1. It saves `session.conf`, stops the Monado service, records the hottest thermal zone (below 200 °C),
   removes SteamVR's old logs, selects `gamescope-session.target` and restarts `pico4-session.service`.
2. It waits up to 180 s for the compositor's "Startup Complete". Once `vrserver` answers it sets
   `power.turnOffScreensTimeout` to an hour, since a still headset off the head would enter standby
   after 5 s and pause the compositor. It waits 20 s for the dashboard's startup stalls, then until the
   Steam client's web helpers take less than 2 % of the GPU over 5 s, at most 180 s more (`msm-frame-stats
   quiet`; `settle_seconds` is the whole wait, `web_helpers_quiet` its result): after the client starts,
   its library home zooms its art for a minute or two and redraws its blur filters every frame, about
   4 ms of GPU per frame on the A650, and then draws nothing. Then `msm-frame-stats` records SECONDS
   (default 30) of flip, vblank and GPU job events.
3. It checks that `gamescope-session.target`, `steamvr.service` and `gamescope-session.service` are
   active, that the compositor started without an assertion and gamescope connected to it, that frames
   were recorded, and that SteamVR reports the headset and both controllers (`vrcmd --pollposes`).
4. It sets the standby timeout back to 5 s, restores the configured session and checks that
   `pico4-session.service` is active again.

It prints one JSON object (schema 1): status and errors, startup time, unit states, what the
compositor's log shows, the frame statistics, each device's position (m) and rotation (rad), and the
hottest zone and fan state (`pico4-fan state`, [cooling.md](cooling.md#checks)) before and after. Exit
status 0 only if `status` passed.

`performance` is a separate verdict from
[`session-performance`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-checks/session-performance) over the measured
window only:

| Measure | Limit |
| --- | --- |
| commits per second | at least 85.5 (95 % of 90 Hz) |
| late commits (more than 1.5 refreshes after the one before) | at most 1 % |
| stalls (gaps between commits above 100 ms) | none |
| compositor's job margin before its vblank, p99 | at least 1 ms |
| hottest thermal zone at the end | at most 90 °C ([cooling.md](cooling.md)) |

It also reports the window's length, GPU time per job (`gpu_ms`), the web helpers' GPU share and the
longest gap. On the host, `validate_valve_session` in [tooling/userspace.py](../tooling/userspace.py)
also requires frames from msm (`target` msm), at least 36 commits/s, vblanks within 1 % of 90 Hz, finite
poses for the three devices with a position for each controller, the fan record (a state or null), and a
passed performance verdict.

### Acceptance

`python -m overpressure dev check` runs the stage-2 checks on the installed system
([hardware-testing.md](hardware-testing.md)). After the userspace check and the head IMU, when the
archive's manifest lists `deckard-steamvr-rel`, it stops the session (until SteamVR and the client have
exited), waits until the hottest zone is below 70 °C (180 s at most), runs
`pico4-valve-session-check --seconds 30` (`VALVE_SESSION_*` in [tooling/userspace.py](../tooling/userspace.py))
and keeps SteamVR's logs and the session user's journal with the results. The session runs again
afterwards, also after a failed check.

## Verified results

Current results are in [kernel.md](kernel.md#results).

| Check | Result |
| --- | --- |
| `vrcompositor` killed once in a running session | the session ends and a new one runs within about 25 s; the new gamescope imports Steam's panel without an error; a requested restart of `steamvr.service` keeps the session and restarts gamescope, without an error |

## Next steps

See [Open work](roadmap.md#open-work).
