# Valve's VR session

Roadmap step 6 ([roadmap.md](roadmap.md)) runs the Steam Frame's VR session on the original
PICO 4 on Valve's Steam Frame 7.2 kernel: Valve's `gamescope-session.target` with gamescope
(OpenVR backend) and SteamVR 2.17.10. SteamVR's compositor drives both panels in direct mode
through Turnip's own display WSI on msm, and Monado's SteamVR plugin, `driver_monado`, supplies
the headset and both controllers in 6DoF from the PICO 4 driver of [xr.md](xr.md). Verified worn:
90 frames per second, the Steam client signed in with its library running games, x86 games
through Proton and FEX, Android apps through Lepton, and Steam Link from a PC. The current results
are in [kernel.md](kernel.md#results).
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
| Direct mode through Valve's Turnip on `msm` | the same, paced by `pico4-swapchain-usage` (four images, one frame ahead, vblanks signalled 5 ms early) |
| Semaphores shared between processes (DRM syncobjs on `msm`) | the same |
| System V semaphores (SteamVR's process locks) | the same; the kernel provides them |
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
opens the seat through logind (libseat). The script waits up to 10 s for
the user manager, then starts Valve's session through Valve's own `start-gamescope-session`:
`gamescope-session.target`, or another of Valve's targets that `/etc/pico4-poc/session.conf`
names (`PICO4_SESSION_TARGET=...`, an optional `EnvironmentFile`). SteamVR is the OpenXR
runtime (its first start links it into the user's `~/.config/openxr`) and `driver_monado`
owns the hardware. Valve's session is the only one: there is no session without SteamVR. The
unit needs the GPU's node.

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
nodes and the dma-buf heaps.
`pico4-userspace-check` confirms that `steamos` can read and write `/dev/dri/card0`,
`/dev/spidev0.0`, `/dev/ttyHS1`, `/dev/media0` and `/dev/video0`.

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
leaves out; stage 1 already sets `schedutil`).
[`pico4-micmute`](../userspace/rootfs/usr/lib/systemd/user/pico4-micmute.service), wanted by
`steamvr.service`, takes `steamvr-proxmicmute`'s place: it mutes PipeWire's default microphone while the
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
only the headset itself shows what SteamVR presents.

Further adaptations:

| Where | What | Why |
| --- | --- | --- |
| [`gamescope-session.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/gamescope-session.service.d/50-pico4.conf) | `WLR_LIBINPUT_NO_DEVICES=1` | with the OpenVR backend, input comes through SteamVR's overlays and gamescope's EIS socket; wlroots must not insist on libinput devices |
| | `MESA_LOADER_DRIVER_OVERRIDE=zink` | OpenGL on the GPU for Xwayland's glamor (and so DRI3 for X11 clients), zink on Turnip, the Frame's OpenGL ([graphics.md](graphics.md#opengl)) |
| [`steam.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/steam.service.d/50-pico4.conf) | `MESA_LOADER_DRIVER_OVERRIDE=zink` | OpenGL on the GPU for the Steam client, its web helper's GPU process and the games it starts; zink finds the GPU for the render node through the layer's DRM nodes |
| | `STEAM_EXTRA_ARGS=-cef-use-vulkan` | the web helper composites the client's UI on ANGLE's Vulkan backend instead of its OpenGL backend on zink, on which the UI flickers while a dialog opens or closes ([The Steam client](#the-steam-client)) |
| [`steamvr.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/steamvr.service.d/50-pico4.conf) | `XRT_COMPOSITOR_SCALE_PERCENTAGE=100` | `driver_monado` recommends the panel's render size, not Monado's default 140 % |
| | `ExecStop=-…vrstartup -shutdown` (Valve's stop with its exit status ignored) | when the session stops, `vrserver` is often gone before the stop runs; the shutdown request then finds no server and exits 255. Every stop failed the unit, and Valve's `steamvr-health-check` counted a failed launch, which after three in a row resets the Steam installation (unpacked again, then updated). A crash still fails the unit through its main process |
| [`/usr/bin/displays_turn_off_drm_master`](../userspace/rootfs/usr/bin/displays_turn_off_drm_master) | does nothing | stands in for the Frame's deckard-hw-support tool, which `steamvr.service` runs after SteamVR exits (`ExecStopPost` without `-`: a missing program fails the unit); the compositor releases the display itself |
| [`pico4-kthread-priorities.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-kthread-priorities.service) | the Frame's values: `gpu-worker` and `crtc_event:N` at SCHED_RR 99, `atomic-worker-N` at 88, `spi0` and the soundwire interrupt threads at 80, the workqueues `msm`, `ring0`–`ring3` and `dsi_drm_work_0/1` at priority 90 through Valve's `prio` attribute, at boot | SteamVR's render and compositor threads run at SCHED_RR 99, so on a shared CPU they would hold back their own submissions and flips. `ring0` hands the compositor's jobs to the GPU; at nice 0, on whichever little core is free, it delays them by up to 6 ms |
| `/opt/steamvr/bin/linuxarm64/vrcompositor-launcher` | `cap_sys_nice=eip` | the compositor raises its priority, as SteamVR's setup does on desktops |
| `~/.steam/steam`, `~/.steam/root` | links to `~/.local/share/Steam` | SteamVR finds its logs and the Steam folder through them; on the Frame the Steam client's `RUNSTEAM.sh` makes them |
| [`steamvr.service` start limit](../userspace/rootfs/etc/systemd/user/steamvr.service.d/50-pico4-restart-limit.conf) | restarts 25 s apart (`RestartMode=direct`), at most three starts in five minutes, after which `pico4-steamvr-failed.service` ends the session (it starts again within its own limit); each new session resets the counter | Valve's unit restarts SteamVR always, and a start takes 15–20 s, so systemd's default limit never applies. Each start resets the controller radio: a compositor that crashed on start buzzed the controllers every few seconds without end. The user manager outlives a session, so the session script resets the user units' counters (`systemctl --user reset-failed`) before it starts its target: the limit is for a crash loop within one session. It first waits until the session before has stopped SteamVR (its compositor crashes on the way out, so that stop can still run when the next session starts); a reset before the end of that stop would not count. `RestartMode=direct` keeps the unit out of the failed state between restarts |

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
  (exit status 42). The update is a one-time cost per client release.
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
  its own bubblewrap in the kernel's user namespaces. Beat Saber starts this way: the game runs under Proton, its OpenXR
  session reaches the focused state in SteamVR, and its Steam API initializes once the
  semaphore library reaches the container ([below](#steamvr-configuration));
  without that library the game waited in its Steam initialization and the headset stayed on
  SteamVR's "Waiting" screen. It is playable with the controllers (a song played through),
  below real time: SteamVR's reprojection carries it to 90 Hz. Under FEX the game
  heats the headset quickly (above 88 °C within a minute without PICO OS's VR CPU limits).
- **Game cores.** `steam-launch-wrapper` pins each game to `STEAM_LAUNCH_WRAPPER_AFFINITY_LIST`
  (desktop apps: `STEAM_DESKTOP_APP_AFFINITY_LIST`). Valve's `steam.service` sets `0xf8`,
  cores 3–7, which on the Frame's chip leaves its first cores to the system; on the XR2 core 3
  is a Cortex-A55 (`cpu_capacity` 351, the A77s 871, the prime core 1024), and Beat Saber's
  main thread ran there. The image sets `0xf0`, cores 4–7, the mask PICO OS's VR service gives
  a VR app's threads (`qvrservice_config.txt`, `performance_thread_attributes`). The GPU's top
  level here is 587 MHz, PICO OS's VR level too: the 670 MHz OPP is not in this speed bin.
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
  Lepton instead, which runs the Android build in a rootless podman container, as on the Frame.
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
session stopped. [`pico4-monado.socket`](../userspace/rootfs/usr/lib/systemd/system/pico4-monado.socket)
listens on `/run/pico4-monado/monado_comp_ipc` (mode 0666) for clients of any user; the first client starts
[`pico4-monado.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-monado.service), which takes
Vulkan's display in direct mode with three swapchain images.

### SteamVR's display

`vrcompositor` presents in direct mode, Vulkan's `VK_KHR_display` through Turnip's own display WSI on
msm, as on the Frame: one 4320×2160 display at 90 Hz. The kernel provides the DRM syncobjs its shared
semaphores need and the System V semaphores of its process locks.

- **Pacing and compressed scanout.** [`pico4-swapchain-usage`](../src/swapchain/pico4-swapchain-usage.c), an
  implicit Vulkan layer active only in `vrcompositor`, takes away the storage usage the compositor never
  uses, so that its swapchain images can be compressed, and paces it: four images,
  one frame ahead (`PICO4_FRAMES_AHEAD`), vblanks signalled 5 ms early (`PICO4_VSYNC_LEAD_US`). It publishes
  the lead in `$XDG_RUNTIME_DIR/pico4-display-lead-us`, and `driver_monado` predicts that much further.
- **What the check sees on msm.** `msm-frame-stats` also reports the web helpers' share of the GPU in
  its window and how long before its vblank each frame's last compositor job ended. A p99 below 1 ms
  fails the check, a vblank with no job in its refresh counting as 0, so it also fails when 1 % of the
  refreshes have no frame. On a table the web helpers' share varies, and with it what a run tests: 23-24 %
  with a margin of 1.4-5.3 ms in the first minutes after a boot, 0 % with 6.2-6.5 ms after a standby. What
  decides it is not known, so a table run does not reliably test a panel in view. Below 1 % the check
  still passes, with a warning that no Steam panel was tested. A game that saturates the GPU gives 0 ms
  and fails.
- **The compositor's crash at a stop.** `vrcompositor` ends with SIGSEGV about 40 ms after logging
  `Cleaning up`, when the render thread has ended and every client has gone: its cleanup calls a
  virtual method of a freed object (`vrcompositor+0x106a10`, the function that holds "Unable to release
  resource of unknown type"). A client's overlay that has had an image through `SetOverlayRaw` or
  `SetOverlayFromFile` is enough, also a 1x1 blank one, and also when the client cleared it long
  before, destroyed the overlay and shut down cleanly. An overlay with a Vulkan image through
  `SetOverlayTexture`, as other overlay applications set theirs, stops cleanly, as does a client
  without an image, so the lens panel's images are Vulkan textures.
  A stop still crashes with the Steam client, the lens panel and the room view all stopped first, so
  what leaves that image is not known; SteamVR's dashboard is untested. It costs a core dump of about 6 MB and no data: `coredump.conf.d/50-pico4-limit.conf` keeps all core
  dumps within 1 GB, removing the oldest, and Valve's health check reads `steamvr.service`'s result, which stays clean. Whether other SteamVR installations do this is not known.

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
- positions marked valid count, not only tracked ones, so controllers the tracker has not
  acquired yet appear where the arm model places them;
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

Measured results are in [kernel.md](kernel.md#results) and
[cooling.md](cooling.md); what is open is in [Open work](roadmap.md#open-work).

### Steam Link

Streaming PC VR to the headset uses SteamVR's Steam Link client, `vrlink` (in
`/opt/steamvr/tools/vrlink`), as on the Frame: the PC's SteamVR renders and encodes HEVC
(NVENC), and `vrlink` decodes it with the headset's video decoder and shows it through the local
SteamVR. The PC's `driver_vrlink` knows the client by the model "PICO 4" (its PICO 4 profile).

- **Finding it.** `vrlink` asks `vrdevice_path video_decode`; the image's
  [`vrdevice_path`](../userspace/rootfs/usr/local/bin/vrdevice_path) answers with the
  node of the codec's device (`aa00000.video-codec`) whose card is `Qualcomm Venus video decoder`
  (or `Qualcomm Venus video encoder`). It opens only those nodes: the Frame's `v4l2-ctl --list-devices` opens every video node, the cameras' too, which
  stops head tracking. `steam-video-codec-setup.service` links `/dev/video-dec0` and
  `/dev/video-enc0` after the codec is probed.
- **The decoder.** The decoder is mainline's Venus (`qcom-venus-decoder`), whose API is the
  one `vrlink` is written for. `vrlink` picks its picture
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
| Headset, Valve's kernel: the decoder with frames before the first key frame (4, 12 and 40, Main10 at 1280×5120) | first picture 160, 261 and 438 ms after the decoder's start, no errors; without patch 0028 no picture within 5 s |
| Headset, Valve's kernel: Steam Link from a PC (POOLS on an NVIDIA GPU, 1280×5120 Main10 over Wi-Fi), worn | picture, audio, head tracking and controllers work, by the wearer's report; about 22 minutes at 90 frames/s in one run |
| The picture's start | about 12 s after `vrlink`'s start, with a decoder reset right at the first video |
| Frame times in a run of POOLS | from the game's frame start to illumination 73 ms (median; 67–78 ms, 5th to 95th percentile); of it encode 11–22 ms, send to receive 4 ms, decode 6 ms, decode end to illumination 30 ms. While the PC's encoder takes 22 ms the stream runs at about 67 frames/s, with 11 ms at 90 |

## Build

The session's parts are packages of the userspace archive, which `scripts/build-userspace.sh`
(or `make userspace`) builds with GPU firmware and sensor data ([development.md](development.md)):

| Package | Sources | Installs |
| --- | --- | --- |
| `pico4-swapchain-usage` | [src/swapchain](../src/swapchain), [PKGBUILD](../userspace/pkgbuilds/pico4-swapchain-usage/PKGBUILD) | `libVkLayer_PICO4_swapchain_usage.so` |
| `pico4-mangohud` | MangoHud 0.8.4 (pinned release archive), [PKGBUILD](../userspace/pkgbuilds/pico4-mangohud/PKGBUILD) | `mangoapp`, `mangohud`, `mangohudctl`, the MangoHud layer |
| `pico4-monado` | [PKGBUILD](../userspace/pkgbuilds/pico4-monado/PKGBUILD) and patches, [src/monado](../src/monado) | `monado-service`, the OpenXR runtime, `driver_monado` |
| `deckard-steamvr-rel`, `deckard-steamvr-session` | the Frame's repository | SteamVR in `/opt/steamvr`; the session's scripts and user units |

The overlay in [userspace/rootfs](../userspace/rootfs) adds the units and drop-ins, the PAM
stack, the udev rules, the session script and
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
systemctl stop pico4-monado.service             # releases the hardware
systemctl start pico4-session.service
```

SteamVR's logs are in `/home/steamos/.local/share/Steam/logs`.

### pico4-valve-session-check

[`pico4-valve-session-check [--seconds SECONDS]`](../userspace/rootfs/usr/bin/pico4-valve-session-check)
runs Valve's session for a bounded time and returns to the configured one:

1. It saves `session.conf`, stops the Monado service, records the hottest thermal zone (the
   highest reading below 200 °C), selects `gamescope-session.target` and restarts
   `pico4-session.service`. SteamVR's old logs are removed.
2. It waits up to 180 s for the compositor's "Startup Complete". As soon as `vrserver` answers, it sets
   `power.turnOffScreensTimeout` to an hour (`vrcmd`): the headset lies still and off the head,
   so SteamVR would enter standby after 5 s and pause the compositor (the Frame's
   `pauseCompositorOnStandby`), leaving no frames to measure. SteamVR's startup still stalls the compositor a few
   times while the dashboard starts, so it waits 20 s (`settle_seconds`), then `msm-frame-stats`
   records SECONDS (default 30) of the kernel's flip, vblank and GPU job events.
3. It checks that `gamescope-session.target`, `steamvr.service` and `gamescope-session.service`
   are active, that the compositor started without an assertion and gamescope connected to it,
   that `msm-frame-stats` recorded the frames, and that SteamVR reports the headset and both controllers
   (`vrcmd --pollposes`).
4. It sets the standby timeout back to SteamVR's 5 s, restores the configured session and checks
   that `pico4-session.service` is active again.

It prints one JSON object (schema 1): status and errors, the startup time, the units' states,
what the compositor's log shows (startup, gamescope's connection, assertions, failed present
waits), the frame statistics (`frames`, from `msm-frame-stats`), each device's position (m) and rotation (rad), and the hottest
zone and the fan daemon's state (`pico4-fan state`, [cooling.md](cooling.md#checks)) before
and after. Its `status` is the integration verdict, and the exit status is 0 only if that
passed.

`performance` is a verdict of its own, from
[`session-performance`](../userspace/rootfs/usr/libexec/pico4-poc/session-performance). It
judges `msm-frame-stats`' recording of the measured window, so SteamVR's startup stays out of it,
and holds the window to these limits:

| Measure | Limit |
| --- | --- |
| commits per second | at least 85.5 (95 % of 90 Hz) |
| late commits (more than 1.5 refreshes after the one before) | at most 1 % |
| stalls (gaps between commits above 100 ms, the recording's `stalls`) | none |
| compositor's job margin before its vblank, p99 | at least 1 ms |
| hottest thermal zone at the end of the run | at most 90 °C, the agreed limit ([cooling.md](cooling.md)) |

It also reports the window's length, the GPU's time per job (`gpu_ms`), the job margin, the web
helpers' GPU share and the longest gap. On the host,
`validate_valve_session` in [tooling/userspace.py](../tooling/userspace.py) also requires
frames from msm's events (`target` msm), at least 36 commits/s, vblanks within 1 % of 90 Hz, finite
poses for the three devices, a position for each
controller, the fan record (a state or null, before and after), and a passed performance
verdict. The runner prints both verdicts.

### Acceptance

`python -m overpressure dev check` runs the stage-2 checks on the installed system
([hardware-testing.md](hardware-testing.md)). After the
userspace check and the head IMU, it stops the session (waiting until SteamVR and the Steam
client have exited) and runs Valve's session when the archive's manifest lists
`deckard-steamvr-rel`; afterwards the session runs again, also after a failed check:

- before Valve's session the runner waits until the hottest zone is below 70 °C (180 s at
  most), then runs `pico4-valve-session-check --seconds 30` with its stderr kept out of the
  JSON (`VALVE_SESSION_*` in [tooling/userspace.py](../tooling/userspace.py)). It keeps SteamVR's
  logs and the session user's journal with its results.

## Verified results

Current results are in [kernel.md](kernel.md#results).

| Check | Result |
| --- | --- |
| Headset: `vrcompositor` killed once in a running session (Valve's kernel) | the session ends and a new one runs within about 25 s; the new gamescope's imports of Steam's panel run without an error, the old one's last three fail as it goes; a requested restart of `steamvr.service` keeps the session and restarts gamescope with it, without an error |

## Next steps

See [Open work](roadmap.md#open-work).
