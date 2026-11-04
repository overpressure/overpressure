# Valve's VR session

Roadmap step 6 ([roadmap.md](roadmap.md)) runs the Steam Frame's VR session on the original
PICO 4 and its stock 4.19 kernel: Valve's `gamescope-session.target` with gamescope (OpenVR
backend) and SteamVR 2.17.10. SteamVR's compositor drives both panels in direct mode through
this project's Vulkan layer, `pico4-vrdisplay`, and Monado's SteamVR plugin, `driver_monado`,
supplies the headset (6DoF) and both controllers (3DoF) from the PICO 4 driver of
[xr.md](xr.md). Semaphores shared between processes and System V semaphores, which the kernel
cannot provide, are implemented in userspace. Outside Valve's session, Monado runs as a
socket-activated system service. The session is verified on the headset, including an
acceptance cycle with stock restore. It is GPU-bound at 42–54 frames per second, the fan
control ([cooling.md](cooling.md)) has not been measured with it, and the Steam client starts
with it but is not signed in yet.
Valve's packages come from the Frame's public repository ([userspace.md](userspace.md)); the
build output stays private.

## What the Frame runs

The Frame's session, from its packages (`deckard-steamvr-session` 1.6.1-8,
`deckard-steamvr-rel` r25358740, `deckard-steam-rel` r1790377368) and the field notes of
[frame-control](https://github.com/saphid/frame-control) and
[steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos):

```text
sddm (autologin steamos) ─► start-gamescope-session gamescope-session.target   (user manager)
  gamescope-session.service   gamescope --backend openvr --xwayland-count 2 ... (one SteamVR overlay per app)
  steamvr.service             vrstartup → vrserver, vrcompositor, vrdashboard (CEF)
  steam.service               Steam client (-steamos3 -vrgamepadui), unpacked into ~/.local/share/Steam
system: dsp_service, steamvr-program-ble (controller radio firmware), deckard-fpga, v4l2loopback, ...
```

- **Display.** `vrcompositor` drives the panels itself in *direct mode*: it finds the
  headset's display through Vulkan (`VK_KHR_display`), acquires it with the DRM fd
  (`VK_EXT_acquire_drm_display`) and paces itself with `VK_EXT_display_control`'s vblank
  events and counter. The Frame's display is also one 4320×2160 DSI connector on `msm`.
- **Tracking.** `driver_cv` with Valve's camera tracking (`libArcturusPerception`) on the
  Frame's DSP (`dsp_service`), FPGA and controller radio. None of this hardware exists on the
  PICO 4.
- **OpenXR.** SteamVR is the OpenXR runtime of the session user
  (`~/.config/openxr/1/active_runtime.json`).

## How the PICO 4 runs it

| Frame | PICO 4 |
| --- | --- |
| sddm autologin | `pico4-session.service`: a logind session for `steamos` through PAM, whose user manager starts the session target |
| `driver_cv`, `dsp_service`, FPGA, radio firmware | `driver_monado`, Monado's SteamVR plugin, with the PICO 4 driver of [xr.md](xr.md) in `vrserver` |
| Direct mode through Valve's Turnip on `msm` | `pico4-vrdisplay`, a Vulkan layer that implements the display path on the stock KMS driver |
| Semaphores shared between processes (DRM syncobjs on `msm`) | the same layer emulates them over sync files ([below](#shared-semaphores)) |
| System V semaphores (SteamVR's process locks) | `pico4-sysvsem`, a preloaded library ([below](#system-v-semaphores)) |
| `driver_frame_hmd`'s SteamVR settings | `enableLinuxVulkanAsync` in the session user's `steamvr.vrsettings` ([below](#steamvr-configuration)) |
| Steam client | the same package and unit; unpacked, updated and started with the session ([below](#the-steam-client)) |
| — | Monado as a system service: the OpenXR runtime outside Valve's session |

### The session

[`pico4-session.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-session.service)
(system, wanted by `graphical.target`) runs
[`/usr/libexec/pico4-poc/session`](../userspace/rootfs/usr/libexec/pico4-poc/session) as
`steamos` (uid 1000) with the PAM stack
[`pico4-session`](../userspace/rootfs/etc/pam.d/pico4-session), without authentication
(`User=` is fixed). `pam_env` runs before `pam_systemd` and gives the session class `user`,
type `wayland` and seat `seat0`
([`session.pam-env`](../userspace/rootfs/etc/pico4-poc/session.pam-env)), as sddm does on the
Frame. A session without a terminal would not get class `user` by itself, and only with it
does logind start the user manager and count the session as active. gamescope's Wayland server
opens the seat through logind (libseat); the seat has no VTs. The script waits up to 10 s for
the user manager, then starts Valve's session through Valve's own `start-gamescope-session`:
`gamescope-session.target`, or another of Valve's targets that `/etc/pico4-poc/session.conf`
names (`PICO4_SESSION_TARGET=...`, an optional `EnvironmentFile`). SteamVR is the OpenXR
runtime (its first start links it into the user's `~/.config/openxr`) and `driver_monado`
owns the hardware. Valve's session is the only one: there is no session without SteamVR. The
unit needs the GPU (`ConditionPathExists=/dev/kgsl-3d0`), so it does not start in QEMU.

A session that ends by itself starts again 5 s later (`Restart=always`), as sddm's
`Relogin=true` does on the Frame. A SteamVR that ends without a stop being asked for, such as
after a `vrcompositor` that dies, ends the session
([`steamvr-ended`](../userspace/rootfs/usr/libexec/pico4-poc/steamvr-ended)): its unit would
restart it (`RestartMode=direct`, which does not reach gamescope), and gamescope stays bound to the
compositor that is gone, every DMA-BUF import of Steam's panel fails and the panel never shows.
gamescope ignores the stop, and its unit's 10 s end it. A SteamVR that failed for good
(its own start limit, below) ends the session too: `pico4-steamvr-failed.service` stops
`gamescope-session.target`, which would otherwise stay without SteamVR and leave the lenses
dark. A requested restart of SteamVR keeps the session and restarts gamescope with it. A
deliberate stop stays stopped. Five starts within ten minutes end the session's
restarts: it stays stopped until the next boot or a deliberate start. Deliberate restarts, such as the session check's, reset the count
first (`systemctl reset-failed pico4-session.service`).

In Valve's session SteamVR's compositor and `driver_monado` run as `steamos`, so
[`70-pico4-xr.rules`](../userspace/rootfs/usr/lib/udev/rules.d/70-pico4-xr.rules) opens the XR
hardware to the `video` group, of which `steamos` is a member: the controller radio
(`spidev0.0`), the lens MCU's UART (`ttyHS1`), the cameras' V4L2 and media nodes, the DRM card
nodes, and ION and the dma-buf heaps, without which Turnip's KGSL backend offers a non-root
client no `VK_KHR_external_memory_fd`. The KGSL `min_pwrlevel` is writable for `video` too
([`pico4-gpu.conf`](../userspace/rootfs/usr/lib/tmpfiles.d/pico4-gpu.conf)).
`pico4-userspace-check` confirms that `steamos` can read and write `/dev/dri/card0`,
`/dev/kgsl-3d0`, `/dev/ion`, `/dev/spidev0.0`, `/dev/ttyHS1`, `/dev/media0` and `/dev/video0`.

### Valve's packages

From the Frame's repository the build installs SteamVR (`deckard-steamvr-rel`) and the session
package (`deckard-steamvr-session`), with `jq` for its `steamvr` script, `at-spi2-core` (the
dashboard's CEF renderer, `vrwebhelper`, links ATK and AT-SPI), and `xorg-xhost` and
`xorg-xrdb` (`gamescope-onready` runs them before it reports the session ready), and the Steam
client ([below](#the-steam-client)). `sddm` counts as installed for dependency resolution only
(`!name` in [packages.txt](../userspace/packages.txt)).
SteamVR's Frame-only parts are not extracted (`NoExtract` in
[pacman.conf](../userspace/pacman.conf)): the tracking driver, the Frame controller driver, the
controller radio's firmware and programming scripts, other controllers' firmware, the Android
and Windows binaries, and the eye-tracking and calibration tools. The resources of the Index,
Vive and Touch controllers (profiles, bindings, render models) stay: they define the controller
types SteamVR remaps games' bindings from ([Controllers in games](#controllers-in-games)). SteamVR's desktop tools
(`vrmonitor`, `overlay_viewer`, `hellovr_sdl`) are installed without the Qt5 and GLEW libraries
they need.

The build masks `sddm`, which `pico4-session.service` replaces, and the system units that need
Frame hardware or would touch the PICO's: `dsp_service`, `steamvr-program-ble` (a firmware OTA
for the Frame's controller radio, which must never run against the PICO's station),
`steamvr-set-kernel-thread-priorities` (its script stops at its first entry here, as the soundwire
interrupt threads refuse `taskset`; `pico4-kthread-priorities` below sets its values), `steamvr-v4l2loopback` and the debug port forwards
(`steamvr-web-debug-portforward`, `steam-web-debug-portforward`). It masks these user units
globally: `steamvr-v4l2cam` (the headset-view loopback camera), `steamvr-proxmicmute` and
`steamos-scheduler` (the sched_ext scheduler `scx_lavd` needs an option Valve's kernel configuration
leaves out; stage 1 already sets `schedutil`). Valve's `proxmicmute` mutes the microphone through ALSA's simple mixer (the
codec's `VA_DEC0`/`VA_DEC1` capture volumes), and that mixer does not load on this kernel: the
MSM PCM volume controls fail while their PCM is closed ("load error: No such device").
[`pico4-micmute`](../userspace/rootfs/usr/lib/systemd/user/pico4-micmute.service), wanted by
`steamvr.service`, takes its place: it mutes PipeWire's default microphone while the
headset's wear sensor reports it off the head (`$XDG_RUNTIME_DIR/pico4-worn`, written by the
driver) and unmutes it when the headset is worn, or when it stops. The session's input method, `ibus-gamescope`
(Valve's unit running `ibus-daemon`), runs with the Frame's `ibus` package.

`steamvr-logs` forwards SteamVR's logs from `~/.local/share/Steam/logs` to the journal
(identifiers `vrserver`, `vrcompositor`, `vrclient_*`). It imports `inotify_simple`, which the
Frame's package installs for Python 3.12 only; the module is plain Python, and the build links
it into the image's Python.

gamescope itself is the Frame's version, upstream's commit `ea3579ed` (Valve's 3.16.28-1), built
here (`pico4-gamescope`) with one fix. Its window manager adds a window to its list on CreateNotify
and again on ReparentNotify to the root, without checking for an entry it already has. The
steamwebhelper's overlay windows got two to four entries each; every entry copied the window's
shared Vulkan override surface, but only one was cleared when that surface was destroyed, and
every vblank `handle_presented_for_window()` read the others' freed surface. Once the memory was
reused (The Lab starting) gamescope crashed and took the session down. The patch makes `add_win()`
skip a window it already lists; upstream's master still has the code. The build bundles
libliftoff, vkroots, wlroots, libdisplay-info and wayland-protocols 1.47 (wlroots 0.20 needs it)
statically, as Valve's binary does, with the SDL2 backend, AVIF screenshots, libei, PipeWire,
DRM and OpenVR. Valve's package also has `steamos-session-shell`, which nothing in the image calls.

gamescope's session starts MangoHud's `mangoapp`, the Steam client's performance overlay
(`pico4-mangohud`). The Frame's MangoHud links libraries of the Frame's private base (spdlog 1.13,
fmt 10, libtracefs); the image builds upstream's MangoHud 0.8.4 from its release archive with its
bundled subprojects (spdlog among them) against the image's libraries, without NVIDIA support.
The build fails if `gamescope-session` does not start `mangoapp` or the program is missing:
gamescope's reaper restarts a `mangoapp` that cannot start without end (15,077 times in a minute
without its `libglfw`).

In VR mode gamescope hands each layer of an app's frame to SteamVR as a subview of the app's
overlay (`….layer1` to `….layer7`). `mangoapp`'s window, the external overlay, is one of these
layers, so the HUD appears over apps that run in gamescope. The Steam client's own UI is a
SteamVR overlay that gamescope forwards rather than composes, and it gets no HUD. gamescope's
screenshots and the window's X11 contents do not show the HUD in VR mode, but the panels do:
only the display layer's frame dumps (`PICO4_VRDISPLAY_DUMP`) or the headset itself show what
SteamVR presents.

Further adaptations:

| Where | What | Why |
| --- | --- | --- |
| [`gamescope-session.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/gamescope-session.service.d/50-pico4.conf) | `PICO4_VRDISPLAY=1` | gamescope needs the GPU's DRM nodes (`VK_EXT_physical_device_drm`), which Turnip's KGSL backend lacks; the layer reports `msm_drm`'s, whose render node imports the GPU's dma-bufs |
| | `WLR_LIBINPUT_NO_DEVICES=1` | with the OpenVR backend, input comes through SteamVR's overlays and gamescope's EIS socket; wlroots must not insist on libinput devices |
| | `MESA_LOADER_DRIVER_OVERRIDE=zink` | OpenGL on the GPU for Xwayland's glamor (and so DRI3 for X11 clients): Mesa's loader would pick the `msm` driver for `msm_drm`'s render node, which has no GPU, and fall back to llvmpipe ([graphics.md](graphics.md#opengl)) |
| [`steam.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/steam.service.d/50-pico4.conf) | `PICO4_VRDISPLAY=1`, `MESA_LOADER_DRIVER_OVERRIDE=zink` | OpenGL on the GPU for the Steam client, its web helper's GPU process and the games it starts; zink finds the GPU for the render node through the layer's DRM nodes |
| | `STEAM_EXTRA_ARGS=-cef-use-vulkan` | the web helper composites the client's UI on ANGLE's Vulkan backend instead of its OpenGL backend on zink, on which the UI flickers while a dialog opens or closes ([The Steam client](#the-steam-client)) |
| [`steamvr.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/steamvr.service.d/50-pico4.conf) | `PICO4_VRDISPLAY=1`, `PICO4_VRDISPLAY_LOGFILE=%h/.local/share/Steam/logs/pico4-vrdisplay.txt` | the display layer for the compositor, logging beside SteamVR's logs |
| | `XRT_COMPOSITOR_SCALE_PERCENTAGE=100` | `driver_monado` recommends the panel's render size, not Monado's default 140 % |
| | `ExecStop=-…vrstartup -shutdown` (Valve's stop with its exit status ignored) | when the session stops, `vrserver` is often gone before the stop runs; the shutdown request then finds no server and exits 255. Every stop failed the unit, and Valve's `steamvr-health-check` counted a failed launch, which after three in a row resets the Steam installation (unpacked again, then updated). A crash still fails the unit through its main process |
| [`/usr/bin/displays_turn_off_drm_master`](../userspace/rootfs/usr/bin/displays_turn_off_drm_master) | does nothing | stands in for the Frame's deckard-hw-support tool, which `steamvr.service` runs after SteamVR exits (`ExecStopPost` without `-`: a missing program fails the unit); the compositor releases the display itself |
| [`pico4-kthread-priorities.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-kthread-priorities.service) | on Valve's kernel the Frame's values: `gpu-worker` and `crtc_event:N` at SCHED_RR 99, `atomic-worker-N` at 88, `spi0` and the soundwire interrupt threads at 80, the workqueues `msm`, `ring0`–`ring3` and `dsi_drm_work_0/1` at priority 90 through Valve's `prio` attribute. On the stock kernel the GPU dispatcher (`kgsl_worker_thr`, SCHED_FIFO 12) and the display's commit and event threads (`crtc_commit:N`, `crtc_event:N`, SCHED_FIFO 61) at SCHED_RR 99, at boot | SteamVR's render and compositor threads run at SCHED_RR 99, so on a shared CPU they would hold back their own submissions and flips. `ring0` hands the compositor's jobs to the GPU; at nice 0, on whichever little core is free, it delays them by up to 6 ms. On the stock kernel the station's SPI thread (`spi0`) already runs at SCHED_FIFO 99, and the GPU's workqueues (`kgsl-events`, `kgsl-workqueue`) at nice -20, which is all a workqueue takes there |
| `/opt/steamvr/bin/linuxarm64/vrcompositor-launcher` | `cap_sys_nice=eip` | the compositor raises its priority, as SteamVR's setup does on desktops |
| `~/.steam/steam`, `~/.steam/root` | links to `~/.local/share/Steam` | SteamVR finds its logs and the Steam folder through them; on the Frame the Steam client's `RUNSTEAM.sh` makes them |
| [`steamvr.service` start limit](../userspace/rootfs/etc/systemd/user/steamvr.service.d/50-pico4-restart-limit.conf) | restarts 25 s apart (`RestartMode=direct`), at most three starts in five minutes, after which `pico4-steamvr-failed.service` ends the session (it starts again within its own limit); each new session resets the counter | Valve's unit restarts SteamVR always, and a start takes 15–20 s, so systemd's default limit never applies. Each start resets the controller radio: a compositor that crashed on start buzzed the controllers every few seconds without end. The user manager outlives a session, so the session script resets the user units' counters (`systemctl --user reset-failed`) before it starts its target: the limit is for a crash loop within one session. It waits for the session before to have stopped SteamVR first (its compositor crashes on the way out, so that stop can still run when the next session starts); a reset before the end of that stop did not count, and the third session check in five minutes hit the limit. `RestartMode=direct` keeps the unit out of the failed state between restarts |

Every stop of SteamVR ends `vrcompositor` with a segmentation fault in its own shutdown code: a
virtual call through an object that is already freed (its vtable and the call's target lie in
freed heap memory). No frame of the display layer or of Turnip is on the stack. The unit does
not fail, because the compositor is not its main process; systemd-coredump keeps a core of
5–6 MB per stop, within its defaults (at most 10 % of the data partition, two weeks).

### The Steam client

The Frame's client package (`deckard-steam-rel`, a native aarch64 client) installs the client
only as a 671 MiB bundle, `/usr/lib/steam/steam.tar.zst`, with Valve's `steam.service` and its
scripts. `gamescope-session.target` wants `steam.service`, so the client starts with Valve's
session:

- **First start.** The unit's `steam-health-check --track-started` finds no
  `~/.local/share/Steam/.install-complete` and "repairs": it unpacks the bundle there (2.5 GB,
  on the data partition of the installed system) and sets the sentinel. `select_steam.sh` and
  `RUNSTEAM.sh` then run `steamrtarm64/steam -deckard -gamepadui -steamdeck -steamos3
  -vrgamepadui` from that folder; its UI is a SteamVR overlay through gamescope.
- **Update.** The bundle is older than the current client of its beta branch
  (`linux_arm64_beta_…`), so the first start downloads about 650 MB, installs it and restarts
  (exit status 42). Extraction and the session's start load every core: at full clocks the
  hottest CPU mean reaches 86–89 °C within 15–35 s, with the big cores at 1.48/1.52 GHz it stays
  below 90 °C. The update is a one-time cost per client release.
- **Libraries the package does not declare.** The current client needs GTK 2
  (`libgtk-x11-2.0.so.0` for `steamui.so` and `vgui2_s.so`; without it the updated client exits
  with status 255 at once) and IBus (`libibus-1.0.so.5` for `steamwebhelper`). The Frame's
  repository has `libibus`; no aarch64 repository has GTK 2, so the build makes `pico4-gtk2`
  from GTK 2.24.33's release tarball (libraries and modules only). `gtk2-compat` in Holo's
  `extra` ships only pkg-config files. The client polls processes with `lsof`. Its video module
  (`libvideo.so`) links FFmpeg 8 (`libavcodec.so.62`, `libavutil.so.60`, …) and libvpx 6, which
  the client brings in `steamrtarm64`; they resolve with the image's libva. The unversioned
  `libav*.so` beside them are FFmpeg 7 builds (`libavutil.so.59`) that nothing loads.
- **Repair loop.** The health check counts a start that ends within 5 s, or with an error
  within two minutes, as a failure; after five in a row it unpacks the bundle again, which undoes
  any update. A client that fails at once therefore alternates between the bundle, an update
  and five failures. SteamVR's health check does the same after three failed SteamVR launches
  in a row (the count lives in `$XDG_RUNTIME_DIR`, so a reboot clears it).
- **Games.** For Windows games the client downloads Proton 11 (ARM64), which runs the game
  inside Steam Linux Runtime 4 for arm64. Its container (pressure-vessel) is set up by
  bubblewrap, which without user namespaces needs root: its own bubblewrap fails ("Creating
  new namespace failed"), and it then takes the image's `/usr/bin/bwrap`, which is setuid
  (pressure-vessel's `bwrap-setuid` mode). A setuid bubblewrap sets up mount namespaces as
  root and runs the program as the user; without PID namespaces the container shares the
  host's process IDs. Beat Saber starts this way: the game runs under Proton, its OpenXR
  session reaches the focused state in SteamVR, and its Steam API initializes once the
  semaphore library reaches the container ([below](#system-v-semaphores-and-message-queues));
  without that library the game waited in its Steam initialization and the headset stayed on
  SteamVR's "Waiting" screen. It is playable with the controllers (a song played through),
  below real time: SteamVR's reprojection carries it to 90 Hz. Under FEX the game
  heats the headset quickly (above 88 °C within a minute without PICO OS's VR CPU limits).
- **Game cores.** `steam-launch-wrapper` pins each game to `STEAM_LAUNCH_WRAPPER_AFFINITY_LIST`
  (desktop apps: `STEAM_DESKTOP_APP_AFFINITY_LIST`). Valve's `steam.service` sets `0xf8`,
  cores 3–7, which on the Frame's chip leaves its first cores to the system; on the XR2 core 3
  is a Cortex-A55 (`cpu_capacity` 351, the A77s 871, the prime core 1024), and Beat Saber's
  main thread ran there. The image sets `0xf0`, cores 4–7, the mask PICO OS's VR service gives
  a VR app's threads (`qvrservice_config.txt`, `performance_thread_attributes`). The same song,
  the same 15 s window (KGSL tracepoints, below): the game's GPU share 39.1 % against 54.0 %
  (212.8 against 290.5 batches/s, a third more frames), SteamVR's mean application frame
  35.8/35.9 ms against 30.8/28.7 ms (CPU/GPU); the compositor took 27 % of the GPU (about
  3 ms per frame) either way. The GPU then runs 81 % busy at its 587 MHz, often 99 %.
  KGSL's 670 MHz level is a turbo level on this kernel (`normal_pwrlevel` 1): with
  `max_pwrlevel` 0 the governor stays at 587 MHz, and with `overclock_mode` 1 as well it
  chose 670 MHz about 9 % of the time, with no gain (55.2 % GPU share). PICO OS does not use
  it for VR apps either: only its system optimisation service's benchmark path (`sysoptservice`,
  called from no code in 5.13.7) sets `overclock_mode`, and an app's GPU levels go through
  `HmdServiceCore` to the boost framework, which tops out at 587 MHz (`max_pwrlevel` 1).
- **Which Proton.** The client takes Valve's recommendation per game (its compat log's "non-user
  mapping"): Proton stable (11.0 ARM64, DXVK 2.7) for Beat Saber and Pools, Proton Experimental
  (ARM64) for others, The Lab among them. Experimental's DXVK 3.1 requires
  `storageBuffer8BitAccess`, which Turnip offers only on Adreno 7xx (the Frame's GPU); on the
  Adreno 650 it finds no adapter, DXGI hands the game a null object and it crashes in `dxgi.dll`
  ("No displays available" from The Lab). `steam-proton-mapping` (`ExecStartPre` of
  `steam.service`, while the client is stopped) therefore maps every game whose latest
  recommendation is Experimental to Proton stable in `config.vdf`, as a user mapping (priority
  250, what the per-game compatibility setting writes); games with a mapping of the user's keep
  it, the rest of the file stays byte for byte, and a newly installed game is covered from the
  next start of the client. On Proton stable The Lab, an OpenVR game, starts with head tracking
  and the controllers, but far below real time. Inside a game's container SteamVR's
  client does not see the external driver path (`/usr/share/steamvr-monado`, the container has
  its own `/usr`) and logs "driver not found"; games start without it.
  Games with an Android build (Cubism) start through Valve's
  Lepton instead, which runs the Android build in a podman container; podman is not in the
  image.
- **UI on the GPU.** The client's web helper (CEF) draws the UI with ANGLE. On ANGLE's OpenGL
  backend, zink on Turnip, the UI's overlay alternates between the new and an older frame from
  the moment a dialog opens (the sign-in's "More Options..." dialog: in panel images every fifth
  present, the window's brightness jumped back and forth 42 times in 16 s) until the next full
  repaint. The client's `-cef-use-vulkan` (`--use-angle=vulkan`), which Valve's `RUNSTEAM.sh`
  leaves commented out on the Frame, puts ANGLE on Turnip directly: no alternation, and the web
  helpers use 0.84 instead of 0.92 cores on the sign-in page. Compositing on the CPU
  (`-cef-disable-gpu-compositing`) does not flicker either, but takes 2.06 cores.
- **Not installed.** `atomupd-daemon` and `steamos-atomupd-client`, SteamOS's updater for the
  Frame's own system images, which must never run here. The client asks Valve's
  `steamos-select-branch` and `steamos-update` every few seconds for the tracked branch and for
  updates, and both call `atomupd-manager`: the image's
  [`atomupd-manager`](../userspace/rootfs/usr/bin/atomupd-manager) answers as an up-to-date
  system on the stable branch (`rel` to the client, "No update available") and refuses to switch
  branches or update. The client's request to set the updater's proxy over D-Bus still fails
  once per start. `steam-runtime-launcher-service` (Steam Runtime's game launcher service) is not
  packaged for arm64; the client disables it.

### Monado as a service

A development runtime, not part of the session: the driver's OpenXR test clients
(`pico4-openxr-pose`, `pico4-openxr-display`, [xr.md](xr.md)) run through it, with Valve's
session stopped. [`pico4-monado.socket`](../userspace/rootfs/usr/lib/systemd/system/pico4-monado.socket) listens
on `/run/pico4-monado/monado_comp_ipc` (mode 0666) for clients of any user. The first client
starts [`pico4-monado.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-monado.service)
(`monado-service` as root), which then owns the display, the head IMU, the cameras (head
tracking through `pico4-slamd`, [xr.md](xr.md#head-tracking)), the controllers and the lens
motor until it is stopped. The OpenXR runtime library,
the system's active runtime (`/etc/openxr/1/active_runtime.json`), is Monado's IPC client.
Clients find the socket in their `$XDG_RUNTIME_DIR`: they set
`XDG_RUNTIME_DIR=/run/pico4-monado`. Upstream's user units are not installed; they
would start a second Monado per user. The unit sets no `CPUWeight` or `CPUQuota`: the cgroup
`cpu` controller would cost the compositor its real-time threads ([xr.md](xr.md)).

The KMS target writes its statistics when the service stops
(`PICO4_KMS_STATS=/run/pico4-monado/kms.json`), and the image of one commit
(`PICO4_KMS_DUMP=/run/pico4-monado/frame.ppm`).
[`monado-service-sigterm.patch`](../userspace/pkgbuilds/pico4-monado/monado-service-sigterm.patch)
makes the service shut down on `SIGTERM` and `SIGINT` as it does on input on stdin. Without
it the process dies without shutting down the compositor and drivers, so the display target
keeps its state and the GPU its clock floor. The statistics cover the service's whole
lifetime, so a measurement starts a fresh service for its client: stop the service, and the
client's connection starts it again.

`driver_monado` in SteamVR opens the same hardware in-process, so the service and Valve's
session never run together: nothing in Valve's session connects to the socket, and a client
started while the session runs gets no system (the service fails to open the display).

### SteamVR's display: pico4-vrdisplay

`vrcompositor` presents only through direct mode: Vulkan's `VK_KHR_display` with
`VK_EXT_acquire_drm_display`, or a Wayland DRM lease. Turnip's KGSL backend has no display
(the GPU is not a DRM device on this kernel), and the stock `msm_drm` shows clean frames only
with the vendor fence contract and six colour layers ([graphics.md](graphics.md)).
[`pico4-vrdisplay`](../src/vrdisplay/pico4-vrdisplay.c) is an implicit Vulkan layer
(`VK_LAYER_PICO4_vrdisplay`) that implements this path for one display, "PICO 4", 4320×2160 at
90 Hz. It provides the instance extensions `VK_KHR_display`, `VK_KHR_get_display_properties2`,
`VK_EXT_direct_mode_display`, `VK_EXT_acquire_drm_display` and
`VK_EXT_display_surface_counter`, and the device extension `VK_EXT_display_control`. Every
other call passes through; without the panels, the layer stays out of the way.

- **Display queries.** The display, its mode and plane, and `vkGetDrmDisplayEXT` for the DSI
  connector. `vkAcquireDrmDisplayEXT` takes the compositor's DRM fd: as the first opener of
  `/dev/dri/card0` it is DRM master, so the layer probes the device without keeping it open.
  Without an acquire, the layer uses a master fd of the device in the process, or opens one.
- **Surface and swapchain.** Three or four linear, dma-buf exportable images of the display's
  size, imported as XRGB8888 or XBGR8888 framebuffers; FIFO only. A present waits for the
  frame's semaphores (a wait longer than 2 s counts as a GPU timeout), then a present thread
  commits the image atomically to the six layers with the release and retire fences, one flip
  in flight, exactly like Monado's KMS target. A flip counts as shown only when its retire
  fence signals: until then the image before it may still be scanned out and stays in use.
  Each 500 ms without the signal counts as a retire timeout; after a second the display is
  taken as stuck and the surface as lost, so acquires, presents and present waits return
  `VK_ERROR_OUT_OF_DATE_KHR` instead of waiting for good, and destroying the swapchain turns
  the display off before its images go. `steamvr.service` sets `PICO4_VRDISPLAY_UBWC=2`, which
  compresses the scanout (see [Performance and heat](#performance-and-heat)): the application
  renders straight into compressed RGBA images imported with Qualcomm's compressed modifier;
  its views of them and its presenting render passes get the RGBA variant of its BGRA format.
- **Timing.** A vblank thread keeps one kernel vblank event queued: the vendor driver holds no
  vblank reference for commits on its own, and events only come while the CRTC is active. The
  events reach it late whenever it waits for the CPU or the layer's lock (up to 120 ms seen),
  so a vblank clock at real-time priority (`SCHED_FIFO` 50) signals the
  `vkRegisterDisplayEventEXT` fences and advances `vkGetSwapchainCounterEXT`: one tick per
  refresh period, at the time the kernel's newest event timestamp predicts. The events only
  correct its phase, and refreshes are counted by time: the kernel stamps a vblank when it
  handles the interrupt, now and then 2–3 ms late, so a later stamp moves the phase by at most
  20 µs and an earlier one at once. It stops 250 ms after the last event. With a lead
  (`PICO4_VRDISPLAY_VSYNC_LEAD_US`) the clock signals each vblank that much early. SteamVR's
  compositor reads the counter through Valve's private counter (`0x7ffffffd`, from the Steam
  Frame's Mesa: the count and that vblank's `CLOCK_MONOTONIC` time in ns, as
  `drmCrtcGetSequence` gives them); the layer answers with the early signal's time, as for
  the fences. The lead is the layer's alone: the process
  that drives the display publishes the lead it applies in
  `$XDG_RUNTIME_DIR/pico4-vrdisplay-lead-us` (microseconds; removed when the display goes),
  and the PICO 4 driver in SteamVR reads it and predicts its poses that much further, for the
  real vblank (0 without the file). The present trace records each vblank at the time the
  clock predicts for the kernel's event and the early signal as an event of its own. Frame
  dumps and statistics are written without the lock. A
  present counts as shown (`vkWaitForPresentKHR`) when its retire fence signals, or with
  `PICO4_VRDISPLAY_FRAMES_AHEAD=N` once the present N before it is committed, and in both
  cases not before the counter shows a count the client has not read yet: a shown present
  means a new vblank, and `vrcompositor` reads the counter right after the wait
  ([one-second waits](#one-second-waits)). Clients that never read the counter see no
  difference.
- **GPU clock floor.** While presenting, the layer raises the KGSL floor to 587 MHz or faster
  (`PICO4_VRDISPLAY_GPU_MIN_MHZ`): under SteamVR's load the stock governor stays at 305 MHz,
  where a frame takes longer than a refresh period. Afterwards it restores KGSL's default level
  (`default_pwrlevel`), also when it found the floor already raised, and `steamvr.service` does
  the same after every stop: a compositor that crashed cannot restore its floor, and the GPU
  would otherwise stay at 587 MHz or faster with nothing running.
- **The compositor's crash at a stop.** `vrcompositor` ends with SIGSEGV about 40 ms after logging
  `Cleaning up`, when the render thread has ended and every client has gone: its cleanup calls a
  virtual method of a freed object (`vrcompositor+0x106a10`, the function that holds "Unable to release
  resource of unknown type"). A client's overlay that has had an image through `SetOverlayRaw` or
  `SetOverlayFromFile` is enough, also a 1x1 blank one, and also when the client cleared it long
  before, destroyed the overlay and shut down cleanly. An overlay with a Vulkan image through
  `SetOverlayTexture`, as other overlay applications set theirs, stops cleanly, as does a client
  without an image, so the lens panel's images are Vulkan textures.
  Long sessions before the panel existed crashed too, on both Mesas, probably from the Steam client's
  overlays. It costs a core dump of about 6 MB and no data: Valve's health check reads
  `steamvr.service`'s result, which stays clean. Whether other SteamVR installations do this is not known.
- **Panels off when idle.** SteamVR's standby stops presenting, but it never asks for the
  display's power (`vkDisplayPowerControlEXT`), and the panels would show the last frame at the
  587 MHz floor. After 5 s without a present (`PICO4_VRDISPLAY_IDLE_OFF_MS`) the layer turns
  the panels off as PICO OS's compositor does with the screen: planes off and the CRTC inactive,
  which powers the panels and their backlight down, and the GPU floor back to KGSL's default.
  The next present sets the mode again (the panels are on after about 460 ms, as at the start),
  raises the floor and shows its frame. The kernel's vblank events stop with the CRTC; the
  layer's vblank clock goes on from its last phase meanwhile, so that SteamVR's waits keep their
  timing, and on the first event afterwards follows the kernel again while its count goes on by
  time: the counter SteamVR reads never goes back. A power-off request
  (`vkDisplayPowerControlEXT` off or suspend) turns the panels off the same way. With
  `power.pauseCompositorOnStandby` on (the Frame's default), SteamVR goes to standby about 8 s
  after the wear sensor reports the headset off the head, the panels go off 5 s later, and
  putting it on brings them back in 450-480 ms.
- **DRM nodes.** gamescope needs the GPU's DRM nodes (`VK_EXT_physical_device_drm`). KGSL is
  not a DRM device, so the layer reports `msm_drm`'s: `/dev/dri/card0` and the render node
  `/dev/dri/renderD128`, which imports the GPU's dma-bufs. In Valve's session two processes
  read them, once each:
  - `vrcompositor` uses the primary node to match the display it acquires (`card0`, the real
    display device); its rendering stays on KGSL.
  - gamescope opens the render node (twice) as its DRM device and its clients' main device.
    Importing the clients' dma-bufs into its Vulkan device (Turnip on KGSL) works, since
    dma-bufs carry no device. Mesa's loader finds no GPU driver for that node (the `msm`
    driver needs an msm GPU); gamescope's session and the Steam client therefore load zink,
    OpenGL on Turnip, which finds the GPU for the node through these DRM nodes
    ([graphics.md](graphics.md#opengl)). Xwayland then runs glamor on the GPU.
  The dashboard's web helpers load the layer but use only KGSL. Monado does not load it: its
  service has no `PICO4_VRDISPLAY=1`, and `driver_monado` runs in `vrserver`, which had no
  Vulkan loaded (no layer mapped) during the session.

| Variable | Effect |
| --- | --- |
| `PICO4_VRDISPLAY=1` | enables the layer (`PICO4_VRDISPLAY_DISABLE=1` disables it) |
| `PICO4_VRDISPLAY_FAKE=1` or `=WxH` | no panels: a 90 Hz timer stands in for vblanks and flips, so the handshake can be tested on any Vulkan driver, such as lavapipe on a PC; `WxH` sets the display's size; `PICO4_VRDISPLAY_FAKE_STUCK=N` stops retiring flips from commit N on (a stuck display) |
| `PICO4_VRDISPLAY_STATS=path` | statistics as JSON per swapchain, every ten seconds and when it is destroyed: commits and their rate, late commits, vblank rate, kernel vblank intervals on the 10 ms tick, GPU and retire-fence waits and timeouts, present waits and their timeouts, the eyes' mean colours |
| `PICO4_VRDISPLAY_MOTION=path` | per commit: commit time, the vblank that shows it, the image change against the previous commit and its horizontal shift; with `PICO4_VRDISPLAY_MOTION_BAND=1` also 16 bands of the left eye in hex, for offline shift measurements (the latency probe in [xr.md](xr.md)); reads the linear scanout images, so it needs `PICO4_VRDISPLAY_UBWC=0` |
| `PICO4_VRDISPLAY_TRACE=path` | the present timeline, written with the statistics (`PICO4_VRDISPLAY_STATS` must be set): one line per event (kind, time, present id, argument) for queued presents, finished rendering, commits, retire fences, `vkWaitForPresentKHR` begin and end, vblanks at the kernel's time, their early signal, kernel vblank events, acquired images and counter reads (with the reading thread) |
| `PICO4_VRDISPLAY_VSYNC_LEAD_US` | signal vblanks (event fences and the counter) this many microseconds early; SteamVR's service sets 5000. The layer publishes the lead it applies, and the PICO 4 driver adds that to its prediction |
| `PICO4_VRDISPLAY_FRAMES_AHEAD=N` | a present counts as shown once the present N before it is committed (default 0: when its retire fence signals; at most 2); `steamvr.service` sets 1, so the GPU renders the next frame while SteamVR waits for the last one |
| `PICO4_VRDISPLAY_DUMP=path` | present `PICO4_VRDISPLAY_DUMP_FRAME` (default 300) as a PPM, downscaled by four; this also gives the statistics each eye's mean colour. With compressed scanout and `PICO4_VRDISPLAY_DUMP_EVERY=N` also every N presents after it, as `path.COMMIT`, for artefacts that only show in motion |
| `PICO4_VRDISPLAY_GPU_MIN_MHZ` | GPU clock floor while presenting (default 587; 0 leaves the governor alone) |
| `PICO4_VRDISPLAY_IDLE_OFF_MS` | panels off after this long without a present (default 5000; 0 never) |
| `PICO4_VRDISPLAY_SYNC=off` | no semaphore emulation; `force`: emulation even where the driver shares semaphores |
| `PICO4_VRDISPLAY_SYNC_FAULT=export,import,spares` | test only: the semaphore emulation pretends these failures ([below](#shared-semaphores)) |
| `PICO4_VRDISPLAY_UBWC=1` | compressed scanout: tiled, compressed images for the application, converted at each present into compressed RGBA scanout images. The CPU cannot read compressed images: the dumped present also copies the application's image into a host-visible buffer, and the motion data are off |
| `PICO4_VRDISPLAY_UBWC=2` | compressed scanout without the conversion (`steamvr.service` sets it): the application renders into the compressed RGBA scanout images; its image views of them and its render passes with a `PRESENT_SRC` final layout get the RGBA variant of its BGRA format, which changes the byte order in memory, not the colours |
| `PICO4_VRDISPLAY_PASS_BARRIER=1` | a full memory barrier before every render pass of the application (`steamvr.service` sets it): without it chunks of SteamVR's overlays are missing in the right eye ([graphics.md](graphics.md)). The layer answers the render-pass calls by their core and extension names and calls the driver's by whichever it has: the Facet renderer uses the extension names, and a missing core entry crashed it |
| `PICO4_VRDISPLAY_IMAGES=1` | log every image the application creates: size, mip levels, layers, samples, format, tiling, usage, flags, external memory and modifiers |
| `PICO4_VRDISPLAY_LOG=debug` | logs every display call |
| `PICO4_VRDISPLAY_LOGFILE=path` | appends the log there instead of stderr |

[`pico4-vrdisplay-test`](../src/vrdisplay/pico4-vrdisplay-test.c) drives the layer the way
`vrcompositor` does (display, DRM acquire, display-plane surface, FIFO swapchain) and shows red
on the left eye and blue on the right while a second thread waits for vblank events. It passes
if frames and the swapchain's vblank counter run at 85.5 Hz or faster (95 % of 90 Hz;
`--min-hz HZ`, where 0 skips the rate checks) and events arrive at 90 % of the rate (the
watcher re-arms between events and can miss one). Without `/dev/dri/card0`, or with
`PICO4_VRDISPLAY_FAKE=1`, it skips the DRM acquire.

### Shared semaphores

SteamVR's compositor shares timeline semaphores with other processes as `OPAQUE_FD` handles
(its mirror textures first, then the swapchains of every client). Turnip's KGSL backend can
export a semaphore only as a sync file, and the stock kernel has no object that keeps a GPU
fence reachable from several processes: `msm_drm` has no syncobjs (`DRM_CAP_SYNCOBJ` is 0) and
KGSL no timelines. The compositor's semaphore creation then fails, and it exports the null
handle it got, which crashes Turnip. [`pico4-vrsync.c`](../src/vrdisplay/pico4-vrsync.c), part
of the display layer, emulates `OPAQUE_FD` semaphores, timeline and binary, where the driver
cannot export them but can import and export sync files, and then reports `OPAQUE_FD` as
exportable and importable:

- **Handle.** The `OPAQUE_FD` handle is a memfd with the semaphore's shared state: the value
  it reached and up to 64 pending signal operations, each with the process that holds its
  fence. A robust, process-shared mutex guards it; a futex word wakes waiters.
- **Signal.** A queue signal operation goes to a binary semaphore instead. After the
  submission, its sync file becomes the operation's fence, held by the signalling process.
- **Wait.** A queue wait looks up the earliest pending signal operation that satisfies it,
  fetches its fence from the holder over a Unix socket (`SCM_RIGHTS`, abstract name
  `pico4-vrsync.<pid>`) and waits through a binary semaphore with that fence imported (nothing,
  if the value is reached). A wait submitted before its signal operation holds the submission
  until the signal is submitted (two seconds at most).
- **Fence thread.** A thread in each signalling process watches the fences it holds, records
  the values they reach and hands them out. The thread keeps the layer loaded, which the
  loader would otherwise unload after the last instance.
- **Host.** `vkWaitSemaphores`, `vkSignalSemaphore` and `vkGetSemaphoreCounterValue` work on
  the shared state; a holder that exits counts as signalled. A wait that times out or takes
  over 250 ms is logged with the value, the value reached and the pending signal operations.
- **Failures.** A value counts as reached only on proof that the work finished:
  - a signal operation whose fence cannot be exported waits for the submission's own fence
    (or the device) before its values count as reached;
  - a fence that cannot be imported, or finds no free binary semaphore, is waited for on the
    CPU before the submission;
  - a signal operation that finds no free binary semaphore within two seconds fails the
    submission;
  - a fence that signals with an error is logged, and one that is no valid file never counts.

  Two cases still continue without proof: a holder that exits, and a wait whose signal
  operation is not submitted within two seconds (logged, then submitted without the wait).
  Failing them would stop the compositor where it now goes on.

Every process that uses such a semaphore must run the layer (`PICO4_VRDISPLAY=1`, which
`steamvr.service` sets). `PICO4_VRDISPLAY_SYNC=off` leaves semaphores to the driver, `force`
emulates them even where the driver shares them itself. `PICO4_VRDISPLAY_SYNC_FAULT` (test
only) pretends failures, comma-separated: `export`, `import`, `spares`.
[`pico4-vrsync-test`](../src/vrdisplay/pico4-vrsync-test.c) shares one timeline between two
processes, the parent submitting with `vkQueueSubmit` and the child with `vkQueueSubmit2`. It
checks a GPU wait for the other process's GPU signal (by GPU timestamps), host waits for the
other process's signals, a host signal, a wait submitted before its signal, and a submission
whose second batch waits for its first batch's signal.

### System V semaphores and message queues

The stock kernel has no `CONFIG_SYSVIPC`. SteamVR's processes take their named locks (such as
`Global\VRBootstrapper`, which keeps a second SteamVR from starting) with
`semget`/`semop`/`semctl`; with `ENOSYS` they report a "DuplicateBootstrapper" and quit.
[`pico4-sysvsem`](../src/sysvsem/pico4-sysvsem.c), preloaded for every process through
`/etc/ld.so.preload` (a link to `/run/pico4-poc/ld.so.preload`, which
[`pico4-stock-preload.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-stock-preload.service)
writes on the stock kernel only; a mainline kernel has System V IPC), implements these calls and
`semtimedop` on a table in `/dev/shm/pico4-sysvsem` (128 sets of up to 32 semaphores):

- a robust, process-shared mutex guards the table, so a process that dies holding it blocks no
  one;
- blocked operations sleep on a futex word that every change bumps;
- `SEM_UNDO` adjustments come back when a process exits or, if it died without running its
  destructor, when the next operation finds it gone.

The Steam API in a game takes such semaphores too (`steamclient.so`; with `ENOSYS` it asserts
in `threadtools.cpp` and the game waits in its Steam initialization). Games run in Steam Linux
Runtime's container, whose `/etc` has no `ld.so.preload`; pressure-vessel imports `LD_PRELOAD`
instead, as `/run/host/usr/lib/...`, so on the stock kernel the user generator
[`50-pico4-stock-kernel`](../userspace/rootfs/usr/lib/systemd/user-generators/50-pico4-stock-kernel)
gives the Steam client's unit `LD_PRELOAD=/usr/lib/libpico4-sysvsem.so` for everything it starts. `/dev/shm` is shared with
the container, and with it the table.

The library is inert until a program calls it, PID 1 included. Its
[test](../src/sysvsem/test_pico4_sysvsem.c) uses the calls as SteamVR does (a named lock
created once, taken with `SEM_UNDO`, refused to a second taker with `IPC_NOWAIT`, released when
its holder exits or is killed) and runs in the package build, because the userspace build
calls `makepkg --nocheck`. `PICO4_SYSVSEM_TABLE` names another table file for tests; privileged
programs ignore it.

gamescope hands MangoHud's `mangoapp` its frame timings through a message queue (`msgget`,
`msgsnd` without waiting; `mangoapp` waits in `msgrcv`). The same library implements
`msgget`/`msgsnd`/`msgrcv`/`msgctl` ([pico4-sysvmsg.c](../src/sysvsem/pico4-sysvmsg.c)) on
`/dev/shm/pico4-sysvmsg`, with the kernel's default limits: 32 queues, messages of up to 8192
bytes, 16,384 bytes per queue, receiving by type as the kernel does. An existing table is opened
without `O_CREAT`, which `fs.protected_regular` refuses on another user's file in `/dev/shm`.
[Its test](../src/sysvsem/test_pico4_sysvmsg.c) passes against the kernel's own queues too (on
the build host).

### SteamVR configuration

The session user's SteamVR settings ([steamvr.vrsettings](../userspace/session/steamvr.vrsettings),
in `~/.local/share/Steam/config`) disable the drivers of the Frame and of other headsets
(`driver_cv`, `driver_frame_hmd`, `driver_frame_controller`, `driver_indexhmd`,
`driver_indexcontroller`, `driver_htc`, `driver_prism`), enable `driver_monado`, force it
(`forcedDriver`, `activateMultipleDrivers` off, `requireHmd`) and set `enableLinuxVulkanAsync`.
They also carry the Steam Frame's own compositor, dashboard, power and tessellation settings
from its driver's `frame_hmd_additional.vrsettings`, which SteamVR loads only with that
driver: the Facet renderer, the system layer drawn in the distortion pass (2x MSAA, 1400×1400),
simplified and achromatic distortion shaders, scanout scaling, overlay quality 1, the blue dome
background, 72 Hz as the default per-app refresh rate. The Frame is the baseline; left out are
only its eye tracking, cameras, VR link, its CV driver and SteamOS power profiles, which the
PICO 4 lacks. Standby follows the wear sensor, as on the Frame: `driver_monado` reports the
headset worn or off the head (`/proximity`, [xr.md](xr.md)), and `power.pauseCompositorOnStandby`
is on. The Frame's headset driver sets that last one in its own settings, which are not loaded
with that driver disabled. Of the Frame driver's room-setup settings it carries the prompt for room
setup when a game starts without a play area (`promptForChaperoneAtGameLaunch`; not required:
`requireChaperoneAtGameLaunch` off), no floor detection at the start, the play area not
re-centred automatically, recentring not triggered by user interaction and the boundary
style 2. The file is on the data partition, where an install copies it from the image only on
its first boot, and SteamVR rewrites it; [`steamvr-settings`](../userspace/rootfs/usr/libexec/pico4-poc/steamvr-settings)
(`ExecStartPre` of `steamvr.service`) sets every key of the image's copy
(`/usr/share/pico4-poc/steamvr.vrsettings`) in it before each start and keeps all other keys. Without it the compositor creates no compute queue and stops at an
assertion; with it, it shares Turnip's single queue for both.
[openvrpaths.vrpath](../userspace/session/openvrpaths.vrpath), in `~/.config/openvr`, registers
`/usr/share/steamvr-monado` as an external driver, with the runtime in `/opt/steamvr` and
configuration and logs under `~/.local/share/Steam`. SteamVR's first start
(`steamvr_first_init.sh`) keeps both files. `pico4-monado` installs `driver_monado.so` in
`/usr/share/steamvr-monado/bin/linuxarm64`, the directory SteamVR for linuxarm64 loads drivers
from; Monado's own install names it `linux64`.

[`monado-steamvr-pico4.patch`](../userspace/pkgbuilds/pico4-monado/monado-steamvr-pico4.patch)
adapts `driver_monado`:

- the render target is one eye's size (2160×2160): OpenVR asks for one eye, and Monado's
  screen spans both eyes on the side-by-side panels;
- the controllers' pose is the PICO 4 aim pose (unpatched, it asked them for a head pose), the
  frame of their render models;
- the controllers are drawn with PICO OS's own models, `{monado}pico4_controller_left` and
  `_right` ([xr.md](xr.md#controllers)), and get a haptic output (`/output/haptic`);
- positions marked valid count, not only tracked ones, so the 3DoF controllers appear where
  the arm model places them;
- inputs a controller lacks are left out instead of being missed every frame: one profile
  serves both hands, with A/B on the right controller and X/Y on the left;
- the headset also answers `IVRDisplayComponent_003` with the exact inverse of its lens
  function (`ComputeInverseDistortion`, from `pico4_hmd_compute_inverse_distortion`). The
  Facet renderer builds its distortion mesh from the inverse; without it SteamVR inverts
  `ComputeDistortion` numerically, which failed at the border of the field of view: stretched
  triangles there flickered in magenta during head turns. The lens model is radial, so the
  inverse is a bisection along the ray from the lens centre, also through the steep
  continuation beyond the fitted radius.

SteamVR's dashboard has default bindings only for some controller types (Index, Vive, Rift,
Windows Mixed Reality), not for the PICO 4's. The package installs
[`pico4-vrcompositor-bindings.json`](../userspace/pkgbuilds/pico4-monado/pico4-vrcompositor-bindings.json)
as the profile's `default_bindings` for `openvr.component.vrcompositor`: a press of the PICO
button (right) or the menu button (left), each hand's `/input/system`, toggles the dashboard, a double press the room view
([xr.md](xr.md#passthrough)), as the Frame's bindings do with its headset's system button, and
holding the PICO button recenters; the aim pose is the laser, the trigger clicks, the grip goes
back and the joystick scrolls. The dashboard's content comes from the Steam client's VR UI
(`vrgamepadui`), so it shows once the client runs in the session.

#### Controllers in games

SteamVR has its own controller type for the PICO 4's controllers, `pico_controller`: a stub
profile and a remapping (`default_pico_controller_profile.json` and
`default_pico_controller_remapping.json` in SteamVR's `resources/input`), with inputs under
Touch's names. A game gets its own bindings for that type, or, through the remapping, its Touch
bindings with the Touch controller, render model and headset simulated, or its Index, Vive or
OpenXR generic bindings with their inputs remapped. vrserver creates those source types at
start from the resource-only drivers `oculus`, `indexcontroller` and `htc`
(`{oculus}/input/touch_profile.json` and so on); without them the remapping fails to parse
("Could not find input profile for controller type: oculus_touch"). Monado's generated profile reports its own
type, `monado_bytedance_pico4_controller`, which no game has bindings for and SteamVR has no
remapping for: games got no controller input at all.

The driver reports SteamVR's type with the project's profile
([`pico4-controller-profile.json`](../userspace/pkgbuilds/pico4-monado/pico4-controller-profile.json),
installed as `pico4_controller_profile.json`), whose `remapping` is SteamVR's own file (linked
into the driver's resources). `monado-steamvr-pico-controller.patch` creates the inputs under the
names of SteamVR's profile: trigger (value, click, touch), grip (value, click), joystick (x, y,
click, touch), A/B or X/Y (click, touch), `/input/system` (the menu button on the left, the PICO
button on the right) and the haptic output. The poses are the render model's components
(`aim`, `tip`, `grip`, `handgrip`, `openxr_aim`, `openxr_grip`; `tooling/controllermodel.py`).

The Frame's Android games, such as Cubism, bind only the Frame controller
(`/interaction_profiles/valve/frame_controller_valve`). The image therefore keeps the Frame
controller's SteamVR resources, without its radio's firmware, and the build adds its layout
([frame-controller-remapping.json](../userspace/session/frame-controller-remapping.json), without
simulation) to SteamVR's `default_pico_controller_remapping.json`, the only remapping SteamVR
reads for `pico_controller`. Without it the controllers stay at the origin, without input.

### Performance and heat

With the Steam Frame's compositor settings ([SteamVR configuration](#steamvr-configuration))
Valve's session presents 90 frames per second at the GPU's 587 MHz floor: 90.0 commits/s with
0–1 late in 10 s windows, worn 36,242 commits with 31 late. The compositor needs 7.0 ms of GPU
time per frame (one 2x-MSAA pass over both panels with the system layer in it, resolved into
the scanout image; clears 2.0 ms, resolve 1.2 ms), the GPU is about 72 % busy with gamescope's
share, and 94 % of the frames reach the panels 21–22 ms after their present. The hottest zone
stays at 55–62 °C. In the lenses (user): fluid, no doubled lines, no flicker at the borders.
SteamVR's one-second waits ([below](#one-second-waits)) are gone.

SteamVR's desktop settings, which the session ran before, are described below: 83–90 frames per
second with the GPU 97–98 % busy, 11.6 ms per frame with the dashboard open, grid lines doubling
during head turns.

The limit was the compositor's frame loop. After each present, `vrcompositor` waits up to
15 ms for that present to be shown (`WaitForPendingPresent`) and only then starts the next
frame. A frame needs about 10 ms of GPU time after its present and finished about 3 ms after a
vblank, then waited about 7 ms for the next one; most waits timed out, and the loop ran at the
15 ms timeout plus the compositor's own work: 16.7 ms per frame, about 58 frames per second,
with the GPU 61–65 % busy (KGSL tracepoints, below). Neither a faster GPU (KGSL's 670 MHz
level, 57.5–58.3 commits/s) nor a vblank lead of 6–9 ms changed that. With
`PICO4_VRDISPLAY_FRAMES_AHEAD=1`, which `steamvr.service` sets, a present counts as shown once
the one before it is committed: SteamVR records the next frame while the GPU renders this one,
and the GPU is the limit again. Without the conversion into the scanout images
(`PICO4_VRDISPLAY_UBWC=2`, 0.8 ms per frame) a frame fits the period at 587 MHz. A present now
reaches the panels about as late as before (18–19.5 ms after the present, against 19 ms), but
not always equally late: 73 % of the frames 18 ms after their present, 8 % 21–22 ms, 8 %
29–30 ms and 1.4 % 33 ms. SteamVR draws its system layer for one predicted time and does not
reproject it afterwards, so each such step moves the picture by up to one refresh of head
motion: the doubled lines. Monado's own compositor, which places layers with the newest pose at
display time, shows none (`pico4-openxr-display --sky`, a grid sky redrawn every frame at
1584×1584 per eye: no doubling worn, 88.9 commits/s). At 670 MHz 89 % of the frames reach the
panels 18–19 ms after their present.

SteamVR starts each frame about 4 ms before the vblank it expects (its running start). The
layer signals vblanks early (`PICO4_VRDISPLAY_VSYNC_LEAD_US`) and the driver predicts that much
further, so that the pose still matches the real vblank. With one frame ahead, 10 s each:

| Lead | Commits per second | Late |
| --- | --- | --- |
| 0 | 80.3 | 36 of 804 |
| 2 ms | 85.5–87.5 | 6–31 |
| 3 ms | 82.4–86.7 | 7–25 |
| 4 ms | 87.4–89.7 | 1–7 |
| 6 ms | 79.5 | 16 of 795 |

With the desktop settings 4 ms was best. The Frame's path (Facet) stalls for one second now and
then with 4 ms (8–14 of SteamVR's one-second waits in 30 s), runs 90.0 commits/s with 5 ms and
81 with 6 ms; `steamvr.service` sets 5 ms.

#### One-second waits

`vrcompositor` paces its frames by the vblank counter. After each present wait it reads the
counter with Valve's private query (its `PostVsyncEvent`): when the count has moved since its
previous read, it publishes the count and the vblank's time; when it has not, it polls 25
times about 15 µs apart and then publishes the old count. Its `WaitForPresent` then waits for
a count it has not seen yet, and with the old count it spins for a second and logs `Aborting
WaitForPresent after 1.000000 seconds!`. On the Frame a present wait returns after the
present's flip, so a vblank has always passed since the previous one.

Here two things differed:
- The layer answered Valve's query with the count only, so SteamVR's vblank time stayed 0 and
  every abort reported a `TimeSinceLastVSync` equal to the uptime. The layer now answers as
  the Frame's Mesa does.
- A present counted as shown ahead (`PICO4_VRDISPLAY_FRAMES_AHEAD=1`) returns its wait at a
  commit, and the counter ticks 5 ms before the vblank (the lead). A frame committed just
  after a tick and the next one committed before the following tick returned two waits within
  one count. The present trace with counter reads showed this before each of SteamVR's waits:
  the second read came 8.8–10.2 ms after the first, 0.8–1.8 ms before the next tick, followed
  by 25 more reads and the second-long gap. A wait now returns only once the counter shows a
  count the client has not read yet.

The waits fell on the startup and on the Steam client's activity: 2–6 in every start, now and
then one or two in a measured window. The vblank time alone changed nothing (4, 4 and 0 waits
in three starts against 2, 4 and 3). With both changes, 17 starts in a row had none.

The session check's frame dump (`PICO4_VRDISPLAY_DUMP`, present 300, about 3.5 s into the
compositor's frames) runs on the layer's commit thread. Reading the uncached image in place,
every fourth pixel, took 130–140 ms: one gap of 125–165 ms in every check's startup, between
commits 300 and 301 in the present trace. The dump now copies each sampled row into cached
memory in one go. The startup's largest gaps left, 40–77 ms, are SteamVR's: its present
waits return in time, and its next present follows 40–60 ms later while the dashboard starts.

The serial console must stay quiet during a session. journald copies warnings and errors into
the kernel log for pstore, and SteamVR logs errors by the thousand while it misses frames (about
60 per second in its GPU-heavy defaults). At the console level 5 of
`50-pico4-console.conf` each of them was also printed on the 115200-baud UART console, and the
kernel writes the console with interrupts off. journald then spent minutes flushing them on
CPU7, at 100 % and the CPU's top clock, and CPU7 takes the display interrupt (`msm_drm`): the
vblank events and retire fences came on the 10 ms scheduler tick (eight steps of 10 ms, one
of 20 ms, the panels still at 90 Hz), presents counted as shown up to 10 ms late, and the
present waits timed out by the hundreds for a whole session. On the headset
`pico4-console-quiet.service` sets the console level to 3, so only critical messages reach
the UART; the kernel log, and with it pstore, keeps everything. The layer counts the kernel's
vblank intervals of 9.6–10.4 ms (`vblank_tick_intervals` of `vblank_intervals` in its
statistics) and logs the state once per ten seconds when more than a quarter of the intervals
show it.

`vrcompositor` and gamescope create their queues with a raised global priority, and with
[the Turnip patch](graphics.md#run-the-tests) their KGSL contexts run at level 1 on the highest
ringbuffer, ahead of the dashboard's CEF renderer at level 8. In the 30 s session check this
took the compositor from 53.1 to 55.6 commits/s, the mean GPU wait per frame from 11.5 to
10.3 ms (longest 35.6 to 25.7 ms) and the late commits from 1,486 to 1,259; the present waits
still time out.

gamescope and the dashboard's web helper submit little GPU work of their own (17 submissions
each in 30 s, against the compositor's 2,776); their cost is the overlay layers the compositor
draws.

The GPU time per frame comes from the stock kernel's KGSL tracepoints in tracefs:
`kgsl_issueibcmds` names the context of each submission, and `adreno_cmdbatch_retired` gives its
start and retire on the GPU's 19.2 MHz always-on counter. In 10 s of Valve's session one context
submits steadily, the compositor's: one batch per frame of 15.7 ms (median) and a second of a few
microseconds. Neither gamescope nor the web helper shows up in the window; the display layer
uses no GPU. A headset lying still counts as idle, and SteamVR pauses its compositor after the
screens' five-second standby timeout (`power.pauseCompositorOnStandby`, default on); profiles of
a still headset turn that off. One SteamVR setting at a time, against the defaults:

| Setting | Compositor GPU per frame (median) | Frames per second |
| --- | --- | --- |
| defaults | 15.7 ms | 53.8 |
| `motionSmoothing` off, `enableAchromaticDistortion`, `enableScanoutScaling` or `enableLinuxVulkanAsync` | 15.7 ms | 52–54 |
| `showSkydome` off | 12.6 ms | 58.1 |
| `overlayRenderQuality_2` 1 | 10.8 ms | 58.3 |
| `enableSimplifiedShaders` | 9.4 ms | 58.0 |
| simplified shaders and overlay quality 1 | 4.7 ms | 66.0 |
| the same without the dome | 3.3 ms | 60.2 |

These figures predate compressed scanout. With the classic renderer the simplified shaders
draw nothing: with `enableSimplifiedShaders` alone every frame is black, in the dumps and in
the lenses, so their times measure no picture. The Frame sets them together with the Facet
renderer, where they draw the scene.

With the desktop settings, compressed scanout, one frame ahead and the dashboard open, a frame takes 11.6 ms; the
clears are 3.25 ms of it. SteamVR clears its system layer to (0, 0, 0, 1) with depth 1.0, and
the lens-distortion target to zero over one eye's half, so a clear of the compressed images'
metadata alone, which only yields zeros, does not apply; the sky pass in the GPU's tile memory
costs as much as in system memory (1.90 against 1.93 ms per eye: 75 tiles, each with its own
overhead).

SteamVR sizes its render targets from a GPU budget: `vrcompositor` logs "GPU speed override on
platform: 450" (MP/s) for this build, against the 839 MP/s the driver recommends (2 × 2160² at
90 Hz), and scales to 0.54 of the area: 1584×1584 per eye for applications and 1580×1580 for its
own system layer (sky, grid, dashboard and overlays). `supersampleManualOverride` with
`supersampleScale` 1.0 gives applications the native 2160×2160; the system layer stays at
1580×1580, also with `GpuSpeed.gpuSpeedHorsepower` 840 or `overlayRenderQuality_2` 4, and the
empty scene keeps its rate (57.5 commits/s worn). The session runs SteamVR's automatic scale.

Inside the compositor's frame, Turnip's own trace (`MESA_GPU_TRACES=print`,
`MESA_GPU_TRACEFILE=path` in SteamVR's environment) times every render pass on the GPU and
records whether it ran tiled; SteamVR's debug labels name the passes. With the defaults, per
eye and frame:

| Pass (SteamVR's label) | Target | GPU time per eye |
| --- | --- | --- |
| Skybox Panorama (the aurora sky, a 4096×2048 texture without mipmaps) | 1580×1580, 4× MSAA with depth, cleared and stored | 2.3–3.0 ms |
| Grid, two locators, seven system UI overlays, laser pointer (one render pass each) | the same target, loaded and stored again | 1.3–1.7 ms |
| Resolve of the MSAA target | 1580×1580 | 0.9 ms |
| Scene / Lens Distortion | the layer's linear 4320×2160 scanout image | 3.2–3.4 ms |

That is about 16.5 ms per frame, the 15.7 ms of the KGSL profile plus the trace's own cost.
Every pass runs in system memory, not in the GPU's on-chip tile memory (GMEM):
- Valve's `deckard-steamvr-session` loads `/usr/share/deckard/mesavars.sh` into
  `steamvr.service`, and its `VRCOMPOSITOR_TU_DEBUG=sysmem,preempt` becomes the compositor's
  `TU_DEBUG`.
- Without `sysmem`, Turnip's autotune still picks system memory for every pass, also with
  `TU_AUTOTUNE_ALGO=prefer_gmem` or `profiled`. The MSAA passes store all samples and depth
  (32 bytes per pixel) for the next pass and the separate resolve, so tiles would save nothing.
- `gmem` in `TU_DEBUG` switches `vrcompositor` to its single-buffer mode, and it crashes at
  start (SIGSEGV).

No pass uses tessellation shaders: Turnip checks for them before it asks the autotune.

The layer's image log (`PICO4_VRDISPLAY_IMAGES=1`) lists SteamVR's own images as optimally
tiled, the MSAA targets compressed (UBWC); only the layer's scanout images are linear. Pinning
the GPU's bus vote to its maximum (`devfreq/soc:qcom,gpubw` `min_freq`; KGSL never raises it
from 0, while the CPUs' votes keep the DDR at its top rate) changed nothing: 53.6–56.6 commits/s
against 54.8–55.0. PICO OS's performance hints ask for at most 587 MHz, the clock KGSL caps the
GPU at (`max_pwrlevel` 1).

The linear scanout images are what costs. The display's planes take Qualcomm's compressed
(UBWC) modifier, but for XBGR8888 only: their capabilities list `XB24/5/1` and `AB24/5/1`, and
a compressed XRGB8888 framebuffer is refused ("unsupported fmt: XR24 modifier
0x500000000000001"). SteamVR renders B8G8R8A8 only; offered only RGBA formats, it fails to
create its swapchain. Turnip compresses no image with storage usage on this GPU, and SteamVR
asks for storage usage without using it. With `PICO4_VRDISPLAY_UBWC=1` SteamVR renders into
tiled, compressed images without storage usage, and each present blits them into compressed
R8G8B8A8 scanout images. Per frame, from Turnip's trace:

| Pass | Linear scanout | Compressed scanout |
| --- | --- | --- |
| Lens distortion, both eyes | 6.6 ms | 3.6 ms |
| MSAA passes of the system layer | 8.6 ms | 6.3 ms |
| MSAA resolves | 2.0 ms | 1.3 ms |
| Conversion into the scanout image | – | 0.8 ms |
| Compositor in all | 17.3 ms | 12.1 ms |

The other passes gain as well, because the display then fetches compressed images and leaves
memory bandwidth to the GPU. Without the trace, 20 s each in turn: 56.5 and 57.6 commits/s
(GPU wait 10.5 and 10.2 ms) against 51.8 and 52.7 (12.1 and 11.2 ms), no retire timeout. At
12 ms a frame still misses the 11.1 ms period. In the lenses, in a blind comparison, the colours
and the picture are right and the motion looks the same: grid lines double during head turns,
frames held for two refreshes at about 55 per second.

`PICO4_VRDISPLAY_UBWC=2`, which `steamvr.service` sets, drops the conversion: SteamVR renders
into the compressed R8G8B8A8 scanout images themselves. The layer gives its views of them, and
its render passes whose attachment ends in `PRESENT_SRC`, the R8G8B8A8 variant of their
B8G8R8A8 format; the shaders write the same colours in the planes' byte order. A dumped frame
shows the scene in the right colours, and SteamVR asks for nothing else on these images (a
storage view would fail without storage usage).

The Frame driver's compositor settings (in its `frame_hmd_additional.vrsettings`, not loaded
with that driver disabled) cut the compositor's GPU time but are not usable here. They switch
to the Facet renderer with simplified, achromatic distortion shaders and draw the dashboard in
the distortion pass. With them and without the background dome (`showSkydome` off; any dome
costs 3–4 ms per frame at the panels' resolution, whatever the render scale) a frame needed
8.9 ms and the layer's statistics showed almost every frame at 90 Hz with no present-wait
timeout. In the lenses the picture warped with every head movement. Checked one setting at a
time (in 3DoF, `PICO4_SLAM=0`), the Facet renderer distorts the rendered scene: SteamVR's
dashboard, drawn in the distortion pass, stays correct and stable, while the scene does not.
Facet builds inverse distortion coordinates from the driver's distortion
(`CreateInverseDistortUVs`). Without the Facet renderer the dashboard cannot be drawn in the
distortion pass (`vrcompositor` exits right after setting up its MSAA), and with both off the
remaining settings show a correct, stable picture: 2,390 commits at 60.7/s in 30 s, 1,123 late,
8.2 ms GPU wait per frame, 149 present-wait timeouts, no one-second wait. With the Facet
renderer the same settings reach 87.5/s with 79 late.

It also froze every few seconds, in SteamVR's one-second waits: `vrcompositor` logged
`Aborting WaitForPresent after 1.000000 seconds!` (47 in four minutes), each with a
`TimeSinceLastVSync` equal to the system's uptime (the layer did not report vblank times
yet), while the vblank count beside it advanced at 90 Hz. The compositor keeps presenting during such a wait, so the layer's statistics did not
show it. The layer then signalled SteamVR's vblank fences when the kernel's events reached it:
33 of 2,649 more than 2 ms late, one 122 ms, followed by a burst of vblanks within a few
milliseconds. With the vblank clock (above) the same settings run without a single such wait:
3,392 commits at 87.5/s in 30 s, 79 late, 7.2 ms GPU wait per frame.

`pico4-fan.service` drives the fan with PICO OS's policy ([cooling.md](cooling.md)); Qualcomm's
`thermal-engine` does not run here, as on PICO OS, and the kernel's `step_wise` zones throttle.
With the fan at 30 %, the 30 s session check takes the hottest zone from 45 °C to 69 °C. The
figures that follow were measured without fan control. Valve's session took the hottest thermal
zone from 47–54 °C to 86–92 °C
within a minute, and a few minutes of it brought the GPU to about 95 °C and the CPU clusters
to 96–98 °C. `pico4-valve-session-check` therefore runs the session for 30 s by default and
records the hottest zone and the fan's state before and after, and the acceptance runner lets
the headset cool below 70 °C first.

### Steam Link

Streaming PC VR to the headset uses SteamVR's Steam Link client, `vrlink` (in
`/opt/steamvr/tools/vrlink`), as on the Frame: the PC's SteamVR renders and encodes HEVC
(NVENC), and `vrlink` decodes it with the headset's video decoder and shows it through the local
SteamVR. The PC's `driver_vrlink` knows the client by the model "PICO 4" (its PICO 4 profile).

- **Decoder.** Venus (the `msm_vidc` driver, `/dev/video32` decoder and `/dev/video33` encoder).
  Its firmware (`venus.mdt`, `venus.b*`) comes from the stock modem partition
  (`extract-graphics-firmware.sh`) into `/usr/lib/firmware`; the kernel starts it when a process
  first opens a codec node. `pico4-venus.service` sets its restart level to `RELATED` before
  anything can (a codec crash then restarts Venus, not the headset).
- **Finding it.** `vrlink` asks `vrdevice_path video_decode`; the image's
  [`vrdevice_path`](../userspace/rootfs/usr/local/bin/vrdevice_path) answers with the
  `qcom,vidc` node whose card is `msm_vidc_vdec` (or `msm_vidc_venc`). It opens only those
  nodes: the Frame's `v4l2-ctl --list-devices` opens every video node, the cameras' too, which
  stops head tracking. `steam-video-codec-setup.service` links `/dev/video-dec0` and
  `/dev/video-enc0` after `pico4-venus.service`.
- **The decoder API.** `vrlink` is written for the Frame's mainline stateful decoder; the stock
  kernel's driver has the downstream one. `libpico4-vrlink-compat.so`
  ([`src/vrlink`](../src/vrlink)), preloaded by `run_vrlink.sh` only, translates:
  - Buffers: MMAP bitstream buffers, and the client's own picture buffers (DMABUF, memory from
    its Vulkan device), become the driver's USERPTR buffers with the dma-buf fd in the plane;
    the driver's extradata plane is allocated in the layer and hidden.
  - Formats: the client's `V4L2_PIX_FMT_QC08C` is the driver's `NV12_UBWC` (Venus's compressed
    NV12, which the GPU imports with `DRM_FORMAT_MOD_QCOM_COMPRESSED`).
  - The first picture size: the driver's firmware parses only with both queues streaming, so a
    256×256 placeholder capture queue streams with the bitstream until the driver's
    port-settings event, which becomes `V4L2_EVENT_SOURCE_CHANGE`.
  - Resets: `vrlink` drops pending input by streaming the bitstream off and on. The driver's
    stream-off ends its session, and it takes a flush of the bitstream alone without doing
    anything; the layer flushes both ports and queues the client's pictures again.
  - Smaller pictures: after the stream's size drops (the PC sends 1536×6144 first, then
    1280×5120), the firmware still takes only picture buffers of the largest size, and the
    driver checks each dma-buf's size. The layer raises the client's dedicated dma-buf
    allocations (`vkAllocateMemory`, reached through `vrlink`'s `dlsym` of
    `vkGetInstanceProcAddr`) to that size.
  - Display delay 0 becomes the driver's decode order and low-latency controls.
- **On Valve's kernel.** The decoder is mainline's Venus (`qcom-venus-decoder`), whose API is the
  one `vrlink` is written for: the translation above stays unattached. `vrlink` picks its picture
  format from what the driver reports after the source change: `QC10C` for `P010` (the PC sends
  Main10), `QC08C` otherwise. It builds a `DRM_FORMAT_MOD_QCOM_COMPRESSED` image from the reported
  pitch and its own offset of the second plane, and queues the image's dma-buf with the reported
  size as its length, so both must be iris's: patch 0020 reports `QC10C`'s real pitch and sizes
  without an extradata margin. Otherwise videobuf2 refuses the dma-buf (`EFAULT`), no picture is
  decoded and the bitstream queue fills up.
- **The stream's start.** `vrlink` restarts the decoder when it sees no progress for 800 ms, also
  on the PC's first frame, about 5 s after its start, and the stream goes on in the middle of a
  group of pictures. The firmware starts only on a stream's headers: with other data first it holds
  every buffer and answers no flush, so that each stop takes the driver's full second and `vrlink`
  restarts it again. Patch 0028 returns data before the headers to the client while no capture queue
  streams.
- **Buffers.** `vrlink` asks for 16 capture buffers and has 16 images; Venus would raise the count to
  the firmware's minimum of 18 for this stream. Patch 0027 keeps a client's 16; the firmware decodes
  with them.
- **Controllers.** `vrlink` sends a controller's input only if its
  `Prop_ControllerType_String` is `frame_controller`, and the PC's driver reads that input only for
  that type. The PC activates a controller only when its static properties have a serial, a
  manufacturer and a type. `vrlink` reads poses and haptics through its legacy actions
  ([pico4-vrlink-bindings.json](../userspace/pkgbuilds/pico4-monado/pico4-vrlink-bindings.json)) and
  the inputs by the Frame's paths (`IVRPaths`). So `libpico4-vrlink-compat.so`, in `vrlink`'s process
  only, reports `frame_controller` and a manufacturer, and turns the Frame's `thumbstick`,
  `dpad_down` and `dpad_up` paths into our `joystick`, `x` and `y`, the reverse of
  [frame-controller-remapping.json](../userspace/session/frame-controller-remapping.json). SteamVR
  on the headset keeps `pico_controller`.
- **The codec's address fault.** Each time the codec powers down, about 2.5 s after its last client
  closed it, the IOMMU logs `Unhandled context fault` at address 0 for stream `0x2100`. It also comes
  at boot, after the probe's power-up. With no client it does not come, and decoding after it works
  (a stream of 22 minutes, repeated decoder starts), so it is harmless. What the codec reads at
  address 0 while it powers down is not known.
- **Timing.** SteamVR on the PC writes each frame's times to `vrlink_aftm_*.csv` in Steam's logs.

| Check | Result |
| --- | --- |
| Headset, Valve's kernel: `vrlink`'s path without the PC (Venus decodes into dma-bufs of images built as `vrlink` builds them, a compute shader samples them through a YCbCr sampler) | 8-bit and Main10 at 1280×5120: the image and the reported size match (9,891,840 and 13,885,440 bytes), the sampled picture is identical to FFmpeg's decode of the same frame; 8-bit 1536×6144: the sizes match (14,229,504 bytes) |
| Headset: the decoder through the layer, driven as `vrlink` drives it (NVENC HEVC test streams at 90 fps, Vulkan pictures as `vrlink` creates them) | steady 1280×5120, a reset, a size increase, a size decrease and a decrease with a reset: 268–269 of 270 pictures each at 90 fps, Venus stays up |
| Headset: a decoded picture through the GPU (`vrlink`'s explicit `QCOM_COMPRESSED` import, copied to linear NV12) | identical to FFmpeg's decode of the same frame, also after a size decrease; Turnip sizes the image as Venus does (9,891,840 bytes at 1280×5120) |
| Headset, Valve's kernel: the decoder with frames before the first key frame (4, 12 and 40, Main10 at 1280×5120) | first picture 160, 261 and 438 ms after the decoder's start, no errors; without patch 0028 no picture within 5 s |
| Headset, Valve's kernel: Steam Link from a PC (POOLS on an NVIDIA GPU, 1280×5120 Main10 over Wi-Fi), worn | picture, audio, head tracking and controllers work, by the wearer's report; about 22 minutes at 90 frames/s in one run |
| The picture's start | about 8 s after `vrlink`'s start and 3 s after the PC's first frame, with 3 decoder restarts (without patch 0028: 18 and about 100 s) |
| Frame times in a run of POOLS | from the game's frame start to illumination 73 ms (median; 67–78 ms, 5th to 95th percentile); of it encode 11–22 ms, send to receive 4 ms, decode 6 ms, decode end to illumination 30 ms. While the PC's encoder takes 22 ms the stream runs at about 67 frames/s, with 11 ms at 90 |

## Build

The session's parts are packages of the userspace archive, which `scripts/build-userspace.sh`
(or `make userspace`) builds with GPU firmware and sensor data ([development.md](development.md)):

| Package | Sources | Installs |
| --- | --- | --- |
| `pico4-vrdisplay` | [src/vrdisplay](../src/vrdisplay), [PKGBUILD](../userspace/pkgbuilds/pico4-vrdisplay/PKGBUILD) | `libVkLayer_PICO4_vrdisplay.so` with its implicit layer manifest, `pico4-vrdisplay-test`, `pico4-vrsync-test` |
| `pico4-sysvsem` | [src/sysvsem](../src/sysvsem), [PKGBUILD](../userspace/pkgbuilds/pico4-sysvsem/PKGBUILD) | `libpico4-sysvsem.so` |
| `pico4-mangohud` | MangoHud 0.8.4 (pinned release archive), [PKGBUILD](../userspace/pkgbuilds/pico4-mangohud/PKGBUILD) | `mangoapp`, `mangohud`, `mangohudctl`, the MangoHud layer |
| `pico4-monado` | [PKGBUILD](../userspace/pkgbuilds/pico4-monado/PKGBUILD) and patches, [src/monado](../src/monado) | `monado-service`, the OpenXR runtime, `driver_monado` |
| `deckard-steamvr-rel`, `deckard-steamvr-session` | the Frame's repository | SteamVR in `/opt/steamvr`; the session's scripts and user units |

The overlay in [userspace/rootfs](../userspace/rootfs) adds the units and drop-ins, the PAM
stack, the udev rules, `/etc/ld.so.preload`, the session script and
`pico4-valve-session-check`; [userspace/session](../userspace/session) holds the session user's
SteamVR files.

## Run

Valve's session starts at boot. The commands below run as root on the headset over SSH
(`python -m overpressure dev ssh COMMAND...` in the development loop). Valve's session (mind the heat):

```sh
systemctl restart pico4-session.service
runuser -u steamos -- env HOME=/home/steamos XDG_RUNTIME_DIR=/run/user/1000 \
  timeout 3 /opt/steamvr/bin/linuxarm64/vrcmd --pollposes      # SteamVR's device poses
```

Through the Monado service, with the session stopped:

```sh
systemctl stop pico4-session.service
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 30
systemctl stop pico4-monado.service             # releases the hardware, writes kms.json
systemctl start pico4-session.service
```

SteamVR's logs, including the layer's `pico4-vrdisplay.txt`, are in
`/home/steamos/.local/share/Steam/logs`.

The display layer and the shared semaphores on their own (the session must be stopped for the
display; `VK_DRIVER_FILES` selects Turnip with the KGSL backend, since the Mesa package
beside it has no driver for this GPU):

```sh
PICO4_VRDISPLAY=1 PICO4_VRDISPLAY_STATS=/run/vrdisplay.json pico4-vrdisplay-test 15
runuser -u steamos -- env PICO4_VRDISPLAY=1 \
  VK_DRIVER_FILES=/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json pico4-vrsync-test
```

### pico4-valve-session-check

[`pico4-valve-session-check [--seconds SECONDS]`](../userspace/rootfs/usr/bin/pico4-valve-session-check)
runs Valve's session for a bounded time and returns to the configured one:

1. It saves `session.conf`, stops the Monado service, records the hottest thermal zone (the
   highest reading below 200 °C), selects `gamescope-session.target` and restarts
   `pico4-session.service`. A runtime drop-in for `steamvr.service` has the layer write its
   statistics and frame 300 into `/run/pico4-valve-session`; SteamVR's old logs are removed.
2. It waits up to 180 s for the compositor's "Startup Complete" and the layer's first
   statistics (ten seconds of frames). As soon as `vrserver` answers, it sets
   `power.turnOffScreensTimeout` to an hour (`vrcmd`): the headset lies still and off the head,
   so SteamVR would enter standby after 5 s and pause the compositor (the Frame's
   `pauseCompositorOnStandby`), leaving no frames to measure. SteamVR's startup still stalls the compositor a few
   times while the dashboard starts, so the measured window begins with the first statistics
   update (every ten seconds) that adds no stall, or after 60 s (`settle_seconds`). The
   session then runs for SECONDS (default 30).
3. It checks that `gamescope-session.target`, `steamvr.service` and `gamescope-session.service`
   are active, that the compositor started without an assertion and gamescope connected to it,
   that the layer wrote statistics, and that SteamVR reports the headset and both controllers
   (`vrcmd --pollposes`).
4. It sets the standby timeout back to SteamVR's 5 s, restores the configured session and checks
   that `pico4-session.service` is active again.

It prints one JSON object (schema 1): status and errors, the startup time, the units' states,
what the compositor's log shows (startup, gamescope's connection, assertions, failed present
waits), the layer's statistics, each device's position (m) and rotation (rad), and the hottest
zone and the fan daemon's state (`pico4-fan state`, [cooling.md](cooling.md#checks)) before
and after. Its `status` is the integration verdict, and the exit status is 0 only if that
passed.

`performance` is a verdict of its own, from
[`session-performance`](../userspace/rootfs/usr/libexec/pico4-poc/session-performance). It
compares the layer's statistics at the start of the measured window with those at the end, so
SteamVR's startup stays out of it, and holds the window to these limits:

| Measure | Limit |
| --- | --- |
| commits per second | at least 85.5 (95 % of 90 Hz) |
| late commits (more than 1.5 refreshes after the one before) | at most 1 % |
| stalls (gaps between commits above 100 ms, the layer's `stalls`) | none |
| SteamVR's present waits that ran out (`present_wait_timeouts`) | none |
| hottest thermal zone at the end of the run | at most 90 °C, the agreed limit ([cooling.md](cooling.md)) |

It also reports the window's length and mean GPU wait per frame, the session's longest gap, and
the stalls during startup (`startup`), which no limit covers. On the host,
`validate_valve_session` in [tooling/userspace.py](../tooling/userspace.py) also requires at least
36 commits/s over the whole session, vblanks within 1 % of 90 Hz, no retire or GPU timeouts, a
scanned-out image that is not black, finite poses for the three devices, a position for each
controller, the fan record (a state or null, before and after), and a passed performance
verdict. The runner prints both verdicts.

### Acceptance

`python -m overpressure dev check` (development loop), `python -m overpressure hwtest flash-run --userspace
ARCHIVE …` and `python -m overpressure cycle --userspace ARCHIVE` (unattended, ending on stock boot and
ABL) run the same stage-2 checks ([hardware-testing.md](hardware-testing.md)). After the
userspace check and the head IMU, they stop the session (waiting until SteamVR and the Steam
client have exited) and run `pico4-vrdisplay-test 15` and `pico4-vrsync-test` when the
archive's manifest lists `pico4-vrdisplay`, then Valve's session when it lists
`deckard-steamvr-rel`; afterwards the session runs again, also after a failed check:

- the display layer test must hold the display rate, show red left and blue right on the
  panels and lose no fences (`validate_vr_display`); the semaphore test must pass all five
  checks (`validate_vr_sync`);
- before Valve's session the runner waits until the hottest zone is below 70 °C (180 s at
  most), then runs `pico4-valve-session-check --seconds 30` with its stderr kept out of the
  JSON (`VALVE_SESSION_*` in [tooling/userspace.py](../tooling/userspace.py)). It keeps SteamVR's
  logs and the session user's journal with its results.

## Verified results

| Check | Result |
| --- | --- |
| PC: lavapipe under QEMU user emulation, `PICO4_VRDISPLAY_FAKE=1` | `pico4-vrdisplay-test` passes: display "PICO 4" 4320×2160 at 90 Hz, 3-image FIFO swapchain, 232 frames in 4 s (emulation speed), vblank counter 6 → 362, scanned-out eyes (255,0,0) / (0,0,255) |
| PC: SteamVR 2.17.10 under QEMU user emulation | `vrstartup` and `vrserver` start, then `vrserver` aborts: its IPC asserts that the robust futex list is set up, which QEMU's user mode does not implement. SteamVR runs only on the headset |
| QEMU: mainline 4.19.325 test kernel ([userspace.md](userspace.md)) | `PICO4-USERSPACE-SELFTEST: PASS`: systemd 257 running with no failed units, Monado's socket listening; Valve's session needs the GPU and does not start under QEMU |
| Headset: `pico4-vrdisplay-test 15` | about 1,290 frames on the panels, layer 88.8–89.0 commits/s, vblank 89.998 Hz, no fence timeout |
| Headset: `pico4-vrsync-test` as `steamos` | passes: the child's GPU work starts after the parent's 160.5 ms of GPU work ends (GPU timestamps); host waits and a host signal cross between the processes; a wait submitted 200 ms before its signal holds 202.6–203.5 ms and completes 25 ms after the signal; a wait for an earlier batch of the same submission takes 0.3 ms |
| Headset: SteamVR without gamescope (`vrstartup`) | `vrserver` with `driver_monado`: headset "PICO 4" at 90 Hz, render target 2160×2160, both controllers with the PICO 4 input profile. `vrcompositor` in direct mode through the layer, startup complete in 0.36 s; its default environment, lens-distorted, on both panels; 250 of about 1,260 present waits over 15 ms |
| Headset: Valve's session (`gamescope-session.target`) | gamescope connected to SteamVR as an overlay client; dashboard running; 2,700 commits at 42.5/s, vblank 89.998 Hz, no fence or GPU timeout, GPU 99 % busy, 16.5 ms GPU wait per frame; SteamVR poses for the headset (6DoF) and both controllers (arm model) |
| Headset: Valve's session in the lenses (user, two minutes) | the full SteamVR environment, slightly jittery but mostly good (5,969 commits at 50/s); both controllers shown as SteamVR's generic locator, turning with the wrists, not moving in space (3DoF) |
| Headset: `pico4-valve-session-check`, 30 s, compressed scanout | passes: 56.5 commits/s, 1,271 of 2,229 late, 10.8 ms GPU wait per frame, no retire timeout; scanned-out eye means (11.2, 13.7, 18.2) and (10.9, 13.5, 18.1) |
| Headset: Valve's session, direct compressed scanout, one frame ahead, 4 ms lead | 10 s windows: 87.4–89.7 commits/s, 1–7 of about 880 late, no present-wait timeout; 3 minutes: 83–88/s, hottest zone 71–79 °C with the fan at 35 %; GPU 97–98 % busy (KGSL), 11.6 ms per frame with the dashboard open; a dumped frame in the right colours |
| Headset: blind worn comparison against the conversion without frames ahead | 4,097 commits with 39 late and 1 present-wait timeout against 4,040 with 2,275 late and 2,544; in the lenses a little more fluid, grid lines still doubling in head turns, also in 3DoF |
| Headset: `pico4-openxr-display --sky` through the Monado service, worn, 3DoF | a grid sky redrawn every frame at 1584×1584 per eye and a world-locked quad: no doubled lines; 88.9 commits/s, 65 of 7,925 late |
| Headset: `pico4-valve-session-check`, 20 and 30 s | passes: startup 15–16 s, 51–54 commits/s, vblank 89.998 Hz, 12.1 ms GPU wait per frame, no fence or GPU timeout, no `mangoapp` restarts, scanned-out eye means about (10, 13, 19); headset and both controllers in SteamVR; hottest zone 47–54 °C before, 86–92 °C after |
| Headset: `pico4-valve-session-check`, 30 s, KGSL context priorities | passes: `vrcompositor` and gamescope at KGSL level 1, the CEF renderer at 8; 2,188 commits at 55.6/s, 1,259 late, 10.3 ms GPU wait per frame (longest 25.7 ms), 5.75 ms retire wait, 1,847 of 2,187 present waits over SteamVR's 15 ms; without the priorities 53.1/s, 1,486 late, 11.5 ms (35.6 ms) |
| Headset: Valve's session with the Frame's compositor settings, worn in 3DoF (user) | 36,242 commits, 31 late, hottest zone 62 °C; 10 s windows on the table 90.0 commits/s, 0–1 late; compositor 7.0 ms of GPU per frame at 90/s; 94 % of the frames on the panels 21–22 ms after their present; in the lenses fluid, no doubled lines, no flicker at the borders with the exact inverse and its linear continuation |
| Headset: `pico4-valve-session-check`, 30 s, vblank clock | SteamVR's defaults: 54.7 commits/s, 10.6 ms GPU wait per frame, no one-second wait; the Frame driver's settings without `waitForSceneGraph`: 3,392 commits at 87.5/s, 79 late, 7.2 ms, 6 of the present waits over 15 ms, no one-second wait (16 in 30 s before the clock) |
| Headset: Valve's session worn with SteamVR's defaults, 2.8 minutes (user) | 52.2 commits/s, longest gap 86 ms, 12.1 ms GPU wait per frame, no one-second wait; in the lenses no freeze, but head turns judder slightly (about 52 of 90 refreshes get a new frame); hottest zone 78.7 °C |
| Headset: Valve's session, SteamVR's defaults without `waitForSceneGraph`, 30 s | 52.2 commits/s, longest gap 32.8 ms, 11.7 ms GPU wait per frame, no one-second wait |
| Headset: GPU profile, 10 s per setting (KGSL tracepoints) | compositor 15.7 ms of GPU per frame with SteamVR's defaults; simplified shaders 9.4 ms, overlay quality 1 10.8 ms, no dome 12.6 ms, both settings 4.7 ms; with both 66 frames per second, limited by the present waits |
| Headset: present timeline (`PICO4_VRDISPLAY_TRACE`), 30 s each | SteamVR's defaults: 15.3 ms from present to finished rendering, 370 of 498 present-wait timeouts on frames still rendering; simplified shaders and overlay quality 1: 4.8 ms, every other frame missing its vblank (60.7 commits/s); the same with a 6 ms vsync lead: 88.9 commits/s, 37 late, 32 present-wait timeouts; with the defaults the lead changes nothing (53.4 and 51.6/s) |
| Headset: a light session 20 s after Valve's GPU-heavy check, twice each | console level 5: journald at 100 % on CPU7, 1,149 and 1,156 of about 1,730 kernel vblank intervals on the 10 ms tick, 61 commits/s, 619 and 577 present-wait timeouts; with the display interrupt moved to CPUs 4–6 still 44 %. Console level 3: 0 and 2 on the tick, 89.2 and 88.9 commits/s, 11 and 18 timeouts |
| Headset: GPU and display kernel threads at SCHED_RR 99 (`pico4-kthread-priorities`) | Valve's 30 s check, its last 10 s window: 49.3 commits/s, 325 of 461 late, longest gap 128 ms without; 51.0/s, 301 of 475, 139 ms with. Simplified shaders and overlay quality 1: 88.0-88.4 commits/s with, 88.2-88.8 without: no measurable change |
| Headset: first Valve session after a boot, three boots in a row | the IMU listed 35.1 s after `pico4-imu-ready.service` started; each first session presented with the PICO 4 driver, no restart. A driver probing earlier finds no IMU and falls back to Monado's simulated headset, whose display SteamVR cannot find (render watchdog abort) |
| Headset: `steamvr.service` stopped directly (`systemctl --user stop`), then the session started again, three rounds | each start presents after 15 s at 87–88 commits/s, no user-manager restart. The earlier failures to build the lens mesh came from SteamVR's own numerical inverse of the lens; the driver now gives it the exact inverse |
| PC: `tests/test_vrdisplay_fake.sh` (the layer on lavapipe, fake display) | presents and retires on a working display, and Valve's counter reports the newest vblank with its time; with the display stuck from commit 20 the retire timeouts are counted, no further present counts as shown, and the client gets `VK_ERROR_OUT_OF_DATE_KHR` after about 1 s instead of waiting |
| Headset: present trace with the 5 ms lead, Valve's session, 30 s | 4,434 vblanks recorded at the kernel's vblank time (median −0.6 µs, 90 % within −41 to +24 µs of its stamps), their display events signalled 4.94 ms earlier; the driver reads the published 5000 µs lead; 90.0 commits/s, no stall |
| Headset: DRM node users in Valve's session | the layer is loaded in gamescope, `vrcompositor` and two `vrwebhelper` processes; gamescope and `vrcompositor` read the DRM properties once each; gamescope holds `renderD128` open twice, `vrcompositor` `card0`; with zink (`MESA_LOADER_DRIVER_OVERRIDE=zink`) Xwayland and the Steam client run OpenGL on the KGSL Turnip ([graphics.md](graphics.md#opengl)) |
| Headset: Valve's 30 s check with the Steam client and its UI on the GPU, installed system | 90.0 commits/s, 0.0–0.07 % late, no stall in the window in six of nine runs; the others 84.0–87.0 commits/s with one or two of SteamVR's one-second waits; 2–6 such waits during startup in every run. Before zink, with the client crashing in a loop, 83.8 commits/s and two waits |
| Headset: the sign-in's "More Options..." dialog opened and cancelled four times, panel images every fifth present | ANGLE on zink: the client's window alternates between the dialog and the sign-in page, its brightness jumping back and forth 42 times; with `-cef-use-vulkan` and with CPU compositing none; web helpers on the sign-in page 0.92 cores (zink), 0.84 (Vulkan), 2.06 (CPU) |
| Headset: present trace with counter reads, Valve's session | before each of SteamVR's one-second waits: one present wait returned just after a counter tick, the next 8.8–10.2 ms later and 0.8–1.8 ms before the following tick, then 25 reads of the same count |
| Headset: Valve's session with the vblank time and the present waits held for an unread count | five 30 s checks in a row: 89.94–90.0 commits/s, 0.0–0.15 % late, no stall in the window, no one-second wait; twelve more starts with 25 s each: none |
| Headset: Valve's 20 s check with the frame dump copying rows first, two runs | 90.0 and 89.97 commits/s, 0.06 % late; the startup's largest gap 46 and 77 ms (125–165 ms in every check before); eye means measured |
| Headset: `pico4-vrdisplay-test` with five 8 s pauses (`--pauses 5`) | each pause: panels off after 5 s without a present (backlight powered down), GPU floor back to the default level, the vblank counter going on at 90 Hz (720 counts in 8 s); each next present: panels on after 459-473 ms, floor raised again; 87.3 frames/s between the pauses, 2 late, no stall |
| Headset: Valve's session with `pauseCompositorOnStandby` on, headset lying still, orientation only (`PICO4_SLAM=0`) | SteamVR enters standby after about 15 s and stops presenting; 5 s later the panels go off, the GPU drops to 305 MHz at its default floor, and the hottest zone falls from 40 to 30 °C in 100 s. With 6DoF the tracker's runaways count as motion, and SteamVR does not enter standby |
| Headset: Valve's session with `mangoapp` (60 s) | one `mangoapp` process throughout (before, without its library, 15,077 restarts a minute); gamescope's queue to it in `pico4-sysvmsg`, its messages received by `mangoapp`; MangoHud warns about two Steam settings it does not know (`no_display`, `nis_steam_sharpness`) |
| Headset: Valve's session, `glxgears` in gamescope with the HUD switched on in `mangoapp`'s configuration, panel images every 15th present for 25 s | the HUD (GPU and CPU load, frame rate, frame time and its graph) over `glxgears` in every image; SteamVR lists the app's overlay and three of its layer subviews as visible |
| Headset: X11 test clients in gamescope's Xwayland (the environment of `steam.service`) | `glxinfo`: direct rendering on `zink Vulkan 1.3(Turnip Adreno (TM) 650)`, OpenGL 4.6; `glxgears` synchronized to the refresh at 88.9–89.8 FPS; `vkcube` (xcb) 900 frames in 10.1 s, 88.8 FPS; no llvmpipe or glamor failure in Xwayland's log; `ibus-gamescope` active |
| Headset: `pico4-vrsync-test` with injected faults (`PICO4_VRDISPLAY_SYNC_FAULT`) | `export` and `import` pass every check, waiting for the submission or on the CPU instead; `spares` fails the submission (`vkQueueSubmit` returns an error) without any value counting as reached |
| Headset: SteamVR stopped with the session, two rounds | each stop clean ("Stopped SteamVR Launcher"), Valve's failure tracker empty; before, every stop failed the unit (`vrstartup -shutdown` exit 255) |
| Headset: five starts of Valve's session in 252 s, then a forced crash loop | all five present, no start limit hit |
| Headset: four Valve session checks back to back (45 s apart; eight SteamVR starts in about seven minutes) | all four passed (89.9–90.0 commits/s), no start limit hit; before the wait for SteamVR's stop the third of three failed on the start limit; the next session starts SteamVR with clean counters |
| Headset: `vrcompositor` killed once in a running session (Valve's kernel) | the session ends and a new one runs within about 25 s; the new gamescope's imports of Steam's panel run without an error, the old one's last three fail as it goes; a requested restart of `steamvr.service` keeps the session and restarts gamescope with it, without an error |
| Headset: `vrcompositor` killed in a running Valve session | `vrserver` quits everything; either SteamVR restarts itself and the session goes on, or gamescope segfaults 16 s later and the session starts again after 5 s, Valve's target active within 30 s of the kill |
| Headset: gamescope killed (SIGSEGV) in a running session | Valve's session starts again, its target active after 10 s and SteamVR's compositor up 14 s later; five such crashes in two minutes hit the session's start limit |
| Headset: deliberate stop of `pico4-session.service` while gamescope is slow to stop | stays stopped (60 s observed); before, the stop timed out at the 15 s default and the unit failed |
| Headset: Monado service | userspace check passes (session active, every listed device node accessible to `steamos`); OpenXR pose: 959–960 of 960 located, tilt 4.29° against the accelerometer's 4.28°, heading rate 0.005°/s; display client through the service (two layers, 30 s): about 2,686 frames at 89.5–89.6 Hz, 11 late, KMS target 88.4–89.6 commits/s at vblank 89.998 Hz, no fence timeout, eyes left/right correct; statistics written when the service stops, GPU floor back to the stock level 6 (305 MHz); the session user's client through the socket link: 1,348 frames at 89.74 Hz |
| Headset: acceptance cycle with stock restore (flash-run with the archive, EDL readback of boot, stock ABL restore) | passes in about 8 minutes from PICO OS to PICO OS on stock boot and ABL, 39 s of it receiving the 801 MiB archive: userspace check with the fan service, head IMU, OpenXR pose (tilt 3.8°), Monado display client at 89.9 frames/s, display layer at 86 frames/s on a 90 Hz vblank, shared semaphores, Valve's session presenting after 15 s at 51.3 commits/s with the headset and both controllers in SteamVR (hottest zone 45.3 → 69.4 °C with the fan at 30 %; 47.1 → 85.7 °C without fan control); stock boot and ABL verified by EDL readback; PICO OS 5.13.7 boots |

## Next steps

1. **Performance.** A compositor frame within the 11.1 ms period with room for the vblank
   phase and with the lens correction intact: the Facet renderer's distortion of the scene
   with `driver_monado`'s lens model. The panels' 72 Hz mode would give 13.9 ms but needs a 90 Hz frame before
   the switch.
2. **Cooling.** Measure the fan control ([cooling.md](cooling.md)) on the headset, with the
   full session for 10 minutes against an agreed limit for the hottest zone.
3. **The Steam client.** It updates and starts with the session, its UI on the GPU; signing in,
   the library, video playback and gamescope's overlays of Steam's apps are not verified yet.
4. **Controller positions.** IR-LED tracking, as for Monado ([xr.md](xr.md)); until then the
   controllers are 3DoF, placed by the arm model.
5. **Controllers in games.** As SteamVR's `pico_controller`
   ([Controllers in games](#controllers-in-games)): Beat Saber's menus and sabers, and an OpenVR
   game, are still to be checked in the headset.
