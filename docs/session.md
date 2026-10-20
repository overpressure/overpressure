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
control ([cooling.md](cooling.md)) has not been measured with it, and the Steam client is not
set up (no network, no Steam login).
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
| Steam client | not installed: it needs a network and a Steam login |
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
the user manager, then starts the session target in it:

- `pico4-session.target` (default): a graphical session whose OpenXR runtime is the Monado
  service. The script links Monado's socket into `$XDG_RUNTIME_DIR` and removes the user's
  OpenXR runtime link into `/opt/steamvr`, which SteamVR's first start
  (`steamvr_first_init.sh`) creates.
- `gamescope-session.target`: Valve's session, through Valve's own `start-gamescope-session`.
  SteamVR is the OpenXR runtime and `driver_monado` owns the hardware; the script removes the
  socket link.

`/etc/pico4-poc/session.conf` (`PICO4_SESSION_TARGET=...`, an optional `EnvironmentFile`)
selects the target, and restarting `pico4-session.service` switches to it.
`pico4-userspace-check` reads the same file to know which target must be active. The service
restarts 5 s after a failure.

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
`xorg-xrdb` (`gamescope-onready` runs them before it reports the session ready). The Steam
client (`deckard-steam-rel`, `deckard-steam`; 2.5 GB unpacked) and `sddm` count as installed
for dependency resolution only (`!name` in [packages.txt](../userspace/packages.txt)).
SteamVR's Frame-only parts are not extracted (`NoExtract` in
[pacman.conf](../userspace/pacman.conf)): the tracking driver, the Frame controller driver, the
controller radio's firmware and programming scripts, other headsets' drivers, the Android and
Windows binaries, and the eye-tracking and calibration tools. SteamVR's desktop tools
(`vrmonitor`, `overlay_viewer`, `hellovr_sdl`) are installed without the Qt5 and GLEW libraries
they need.

The build masks `sddm`, which `pico4-session.service` replaces, and the system units that need
Frame hardware or would touch the PICO's: `dsp_service`, `steamvr-program-ble` (a firmware OTA
for the Frame's controller radio, which must never run against the PICO's station),
`steamvr-set-kernel-thread-priorities` (for the Frame's kernel), `steamvr-v4l2loopback`,
`steam-video-codec-setup` (the Frame's video codec nodes) and the debug port forwards
(`steamvr-web-debug-portforward`, `steam-web-debug-portforward`). It masks these user units
globally: `steamvr-v4l2cam` (the headset-view loopback camera), `steamvr-proxmicmute`
(proximity microphone mute), `ibus-gamescope` (ibus is not installed), `steamos-scheduler` (the
sched_ext scheduler `scx_lavd` needs Linux 6.12; stage 1 already sets `schedutil`).

`steamvr-logs` forwards SteamVR's logs from `~/.local/share/Steam/logs` to the journal
(identifiers `vrserver`, `vrcompositor`, `vrclient_*`). It imports `inotify_simple`, which the
Frame's package installs for Python 3.12 only; the module is plain Python, and the build links
it into the image's Python.

gamescope runs without MangoHud's `mangoapp`, the Steam client's performance overlay: the
Frame's MangoHud needs libraries of the Frame's private base (spdlog 1.13, fmt 10, libtracefs),
and the overlay has nothing to show without the Steam client. The build removes the
`--mangoapp` option from `/usr/lib/steamos/gamescope-session`, which would otherwise restart the
missing program in a loop, and fails if the option is still there.

Further adaptations:

| Where | What | Why |
| --- | --- | --- |
| [`gamescope-session.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/gamescope-session.service.d/50-pico4.conf) | `PICO4_VRDISPLAY=1` | gamescope needs the GPU's DRM nodes (`VK_EXT_physical_device_drm`), which Turnip's KGSL backend lacks; the layer reports `msm_drm`'s, whose render node imports the GPU's dma-bufs |
| | `WLR_LIBINPUT_NO_DEVICES=1` | with the OpenVR backend, input comes through SteamVR's overlays and gamescope's EIS socket; wlroots must not insist on libinput devices |
| [`steamvr.service` drop-in](../userspace/rootfs/usr/lib/systemd/user/steamvr.service.d/50-pico4.conf) | `PICO4_VRDISPLAY=1`, `PICO4_VRDISPLAY_LOGFILE=%h/.local/share/Steam/logs/pico4-vrdisplay.txt` | the display layer for the compositor, logging beside SteamVR's logs |
| | `XRT_COMPOSITOR_SCALE_PERCENTAGE=100` | `driver_monado` recommends the panel's render size, not Monado's default 140 % |
| [`/usr/bin/displays_turn_off_drm_master`](../userspace/rootfs/usr/bin/displays_turn_off_drm_master) | does nothing | stands in for the Frame's deckard-hw-support tool, which `steamvr.service` runs after SteamVR exits (`ExecStopPost` without `-`: a missing program fails the unit); the compositor releases the display itself |
| `/opt/steamvr/bin/linuxarm64/vrcompositor-launcher` | `cap_sys_nice=eip` | the compositor raises its priority, as SteamVR's setup does on desktops |
| `~/.steam/steam`, `~/.steam/root` | links to `~/.local/share/Steam` | SteamVR finds its logs and the Steam folder through them; on the Frame the Steam client's `RUNSTEAM.sh` makes them |
| [`steamvr.service` start limit](../userspace/rootfs/etc/systemd/user/steamvr.service.d/50-pico4-restart-limit.conf) | at most three starts in three minutes | Valve's unit restarts SteamVR always, and a start takes 15–20 s, so systemd's default limit never applies. Each start resets the controller radio: a compositor that crashed on start buzzed the controllers every few seconds without end |

### Monado as a service

[`pico4-monado.socket`](../userspace/rootfs/usr/lib/systemd/system/pico4-monado.socket) listens
on `/run/pico4-monado/monado_comp_ipc` (mode 0666) for clients of any user. The first client
starts [`pico4-monado.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-monado.service)
(`monado-service` as root), which then owns the display, the head IMU, the cameras with
Basalt, the controllers and the lens motor until it is stopped. The OpenXR runtime library,
the system's active runtime (`/etc/openxr/1/active_runtime.json`), is Monado's IPC client.
Clients find the socket in their `$XDG_RUNTIME_DIR`: the session links it there, and root
clients set `XDG_RUNTIME_DIR=/run/pico4-monado`. Upstream's user units are not installed; they
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
session never run together: nothing connects to the socket in Valve's session.

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
  in flight, exactly like Monado's KMS target. A retire fence that does not signal within
  500 ms counts as a retire timeout.
- **Timing.** A vblank thread keeps one kernel vblank event queued: the vendor driver holds no
  vblank reference for commits on its own, and events only come while the CRTC is active. The
  events reach it late whenever it waits for the CPU or the layer's lock (up to 120 ms seen),
  so a vblank clock at real-time priority (`SCHED_FIFO` 50) signals the
  `vkRegisterDisplayEventEXT` fences and advances `vkGetSwapchainCounterEXT`: one tick per
  refresh period, at the time the kernel's newest event timestamp predicts. The events only
  correct its phase; it stops 250 ms after the last event. SteamVR takes the moment its fence
  signals as the vblank's time. Frame dumps and statistics are written without the lock. A
  present counts as shown (`vkWaitForPresentKHR`) when its retire fence signals.
- **GPU clock floor.** While presenting, the layer raises the KGSL floor to 587 MHz or faster
  and restores it afterwards, as the KMS target does: under a compositor's load the stock
  governor stays at 305 MHz, where a frame takes longer than a refresh period.
- **DRM nodes.** gamescope needs the GPU's DRM nodes (`VK_EXT_physical_device_drm`). KGSL is
  not a DRM device, so the layer reports `msm_drm`'s: `/dev/dri/card0` and the render node
  `/dev/dri/renderD128`, which imports the GPU's dma-bufs.

| Variable | Effect |
| --- | --- |
| `PICO4_VRDISPLAY=1` | enables the layer (`PICO4_VRDISPLAY_DISABLE=1` disables it) |
| `PICO4_VRDISPLAY_FAKE=1` or `=WxH` | no panels: a 90 Hz timer stands in for vblanks and flips, so the handshake can be tested on any Vulkan driver, such as lavapipe on a PC; `WxH` sets the display's size |
| `PICO4_VRDISPLAY_STATS=path` | statistics as JSON per swapchain, every ten seconds and when it is destroyed: commits and their rate, late commits, vblank rate, GPU and retire-fence waits and timeouts, present waits and their timeouts, the eyes' mean colours |
| `PICO4_VRDISPLAY_MOTION=path` | per commit: commit time, the vblank that shows it, the image change against the previous commit and its horizontal shift; with `PICO4_VRDISPLAY_MOTION_BAND=1` also 16 bands of the left eye in hex, for offline shift measurements (the latency probe in [xr.md](xr.md)) |
| `PICO4_VRDISPLAY_DUMP=path` | present `PICO4_VRDISPLAY_DUMP_FRAME` (default 300) as a PPM, downscaled by four; this also gives the statistics each eye's mean colour |
| `PICO4_VRDISPLAY_GPU_MIN_MHZ` | GPU clock floor while presenting (default 587; 0 leaves the governor alone) |
| `PICO4_VRDISPLAY_SYNC=off` | no semaphore emulation |
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
  the shared state; a holder that exits counts as signalled.

Every process that uses such a semaphore must run the layer (`PICO4_VRDISPLAY=1`, which
`steamvr.service` sets). `PICO4_VRDISPLAY_SYNC=off` leaves semaphores to the driver.
[`pico4-vrsync-test`](../src/vrdisplay/pico4-vrsync-test.c) shares one timeline between two
processes, the parent submitting with `vkQueueSubmit` and the child with `vkQueueSubmit2`. It
checks a GPU wait for the other process's GPU signal (by GPU timestamps), host waits for the
other process's signals, a host signal, a wait submitted before its signal, and a submission
whose second batch waits for its first batch's signal.

### System V semaphores

The stock kernel has no `CONFIG_SYSVIPC`. SteamVR's processes take their named locks (such as
`Global\VRBootstrapper`, which keeps a second SteamVR from starting) with
`semget`/`semop`/`semctl`; with `ENOSYS` they report a "DuplicateBootstrapper" and quit.
[`pico4-sysvsem`](../src/sysvsem/pico4-sysvsem.c), preloaded for every process through
[`/etc/ld.so.preload`](../userspace/rootfs/etc/ld.so.preload), implements these calls and
`semtimedop` on a table in `/dev/shm/pico4-sysvsem` (128 sets of up to 32 semaphores):

- a robust, process-shared mutex guards the table, so a process that dies holding it blocks no
  one;
- blocked operations sleep on a futex word that every change bumps;
- `SEM_UNDO` adjustments come back when a process exits or, if it died without running its
  destructor, when the next operation finds it gone.

The library is inert until a program calls it, PID 1 included. Its
[test](../src/sysvsem/test_pico4_sysvsem.c) uses the calls as SteamVR does (a named lock
created once, taken with `SEM_UNDO`, refused to a second taker with `IPC_NOWAIT`, released when
its holder exits or is killed) and runs in the package build, because the userspace build
calls `makepkg --nocheck`. `PICO4_SYSVSEM_TABLE` names another table file for tests; privileged
programs ignore it.

### SteamVR configuration

The session user's SteamVR settings ([steamvr.vrsettings](../userspace/session/steamvr.vrsettings),
in `~/.local/share/Steam/config`) disable the drivers of the Frame and of other headsets
(`driver_cv`, `driver_frame_hmd`, `driver_frame_controller`, `driver_indexhmd`,
`driver_indexcontroller`, `driver_htc`, `driver_prism`), enable `driver_monado`, force it
(`forcedDriver`, `activateMultipleDrivers` off, `requireHmd`) and set `enableLinuxVulkanAsync`.
The Frame's headset driver sets that last one in its own settings, which are not loaded with
that driver disabled. Without it the compositor creates no compute queue and stops at an
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
- the controllers' pose is the PICO 4 grip pose (unpatched, it asked them for a head pose);
- positions marked valid count, not only tracked ones, so the 3DoF controllers appear where
  the arm model places them;
- inputs a controller lacks are left out instead of being missed every frame: one profile
  serves both hands, with A/B on the right controller and X/Y on the left.

### Performance and heat

In Valve's full session, with gamescope's overlay and the dashboard's CEF renderer besides the
compositor, the GPU is 99 % busy at its 587 MHz floor and the compositor presents 42–54 frames
per second; a present waits 12–16.5 ms on average for its rendering. SteamVR on its own,
without gamescope, presents most frames at 90 Hz; about one in five misses its vblank and
shows one refresh later.

The uneven cadence in the lenses comes from the compositor's frame loop. After each present,
`vrcompositor` waits up to 15 ms for that present to be shown (`WaitForPendingPresent`). A frame
needs about 10 ms of GPU time after the present, then on average half a refresh (5.75 ms) until
its retire fence signals at the next vblank, so most waits time out. The loop then runs at the
15 ms timeout plus the compositor's own work, about 55 frames per second, out of phase with the
vblank: a mix of one- and two-refresh frames. Closing it needs a frame that fits the 11.1 ms
period with room for the phase, or a 72 Hz mode (13.9 ms).

`vrcompositor` and gamescope create their queues with a raised global priority, and with
[the Turnip patch](graphics.md#run-the-tests) their KGSL contexts run at level 1 on the highest
ringbuffer, ahead of the dashboard's CEF renderer at level 8. In the 30 s session check this
took the compositor from 53.1 to 55.6 commits/s, the mean GPU wait per frame from 11.5 to
10.3 ms (longest 35.6 to 25.7 ms) and the late commits from 1,486 to 1,259; the present waits
still time out.

gamescope and the dashboard's web helper submit little GPU work of their own (17 submissions
each in 30 s, against the compositor's 2,776); their cost is the overlay layers the compositor
draws.

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
`TimeSinceLastVSync` equal to the system's uptime, while the vblank count beside it advanced
at 90 Hz. The compositor keeps presenting during such a wait, so the layer's statistics did not
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

## Build

The session's parts are packages of the userspace archive, which `scripts/build-userspace.sh`
(or `make userspace`) builds with GPU firmware and sensor data ([development.md](development.md)):

| Package | Sources | Installs |
| --- | --- | --- |
| `pico4-vrdisplay` | [src/vrdisplay](../src/vrdisplay), [PKGBUILD](../userspace/pkgbuilds/pico4-vrdisplay/PKGBUILD) | `libVkLayer_PICO4_vrdisplay.so` with its implicit layer manifest, `pico4-vrdisplay-test`, `pico4-vrsync-test` |
| `pico4-sysvsem` | [src/sysvsem](../src/sysvsem), [PKGBUILD](../userspace/pkgbuilds/pico4-sysvsem/PKGBUILD) | `libpico4-sysvsem.so` |
| `pico4-monado` | [PKGBUILD](../userspace/pkgbuilds/pico4-monado/PKGBUILD) and patches, [src/monado](../src/monado) | `monado-service`, the OpenXR runtime, `driver_monado` |
| `deckard-steamvr-rel`, `deckard-steamvr-session` | the Frame's repository | SteamVR in `/opt/steamvr`; the session's scripts and user units |

The overlay in [userspace/rootfs](../userspace/rootfs) adds the units and drop-ins, the PAM
stack, the udev rules, `/etc/ld.so.preload`, the session script and
`pico4-valve-session-check`; [userspace/session](../userspace/session) holds the session user's
SteamVR files.

## Run

The session starts at boot with `pico4-session.target`. The commands below run as root on the
headset over SSH (`python -m pico4 dev ssh COMMAND...` in the development loop). Through the
Monado service:

```sh
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 30          # a root client
runuser -u steamos -- env XDG_RUNTIME_DIR=/run/user/1000 pico4-openxr-display 30
systemctl stop pico4-monado.service             # releases the hardware, writes kms.json
```

Valve's session (mind the heat):

```sh
systemctl stop pico4-monado.service
echo PICO4_SESSION_TARGET=gamescope-session.target > /etc/pico4-poc/session.conf
systemctl restart pico4-session.service
runuser -u steamos -- env HOME=/home/steamos XDG_RUNTIME_DIR=/run/user/1000 \
  timeout 3 /opt/steamvr/bin/linuxarm64/vrcmd --pollposes      # SteamVR's device poses
rm /etc/pico4-poc/session.conf; systemctl restart pico4-session.service     # back to Monado
```

The stage-2 root filesystem lives in RAM, so a new boot starts without `session.conf`.
SteamVR's logs, including the layer's `pico4-vrdisplay.txt`, are in
`/home/steamos/.local/share/Steam/logs`.

The display layer and the shared semaphores on their own (the Monado service must be stopped
for the display; `VK_DRIVER_FILES` selects Turnip with the KGSL backend, since the Mesa package
beside it has no driver for this GPU):

```sh
PICO4_VRDISPLAY=1 PICO4_VRDISPLAY_STATS=/run/vrdisplay.json pico4-vrdisplay-test 15
runuser -u steamos -- env PICO4_VRDISPLAY=1 \
  VK_DRIVER_FILES=/usr/share/vulkan/icd.d/pico4_turnip_kgsl.aarch64.json pico4-vrsync-test
```

### pico4-valve-session-check

[`pico4-valve-session-check [--seconds SECONDS]`](../userspace/rootfs/usr/bin/pico4-valve-session-check)
runs Valve's session for a bounded time and returns to the configured one:

1. It saves `session.conf`, stops the Monado service, records the hottest thermal zone (the
   highest reading below 200 °C), selects `gamescope-session.target` and restarts
   `pico4-session.service`. A runtime drop-in for `steamvr.service` has the layer write its
   statistics and frame 300 into `/run/pico4-valve-session`; SteamVR's old logs are removed.
2. It waits up to 180 s for the compositor's "Startup Complete" and the layer's first
   statistics (ten seconds of frames), then lets the session run for SECONDS (default 30).
3. It checks that `gamescope-session.target`, `steamvr.service` and `gamescope-session.service`
   are active, that the compositor started without an assertion and gamescope connected to it,
   that the layer wrote statistics, and that SteamVR reports the headset and both controllers
   (`vrcmd --pollposes`).
4. It restores the configured session and checks that `pico4-session.service` is active again.

It prints one JSON object (schema 1): status and errors, the startup time, the units' states,
what the compositor's log shows (startup, gamescope's connection, assertions, failed present
waits), the layer's statistics, each device's position (m) and rotation (rad), and the hottest
zone and the fan daemon's state (`pico4-fan state`, [cooling.md](cooling.md#checks)) before
and after. The exit status is 0 only if the check passed. On the host,
`validate_valve_session` in [pico4/userspace.py](../pico4/userspace.py) also requires at least
36 commits/s (40 % of 90 Hz, since the session is GPU-bound), vblanks within 1 % of 90 Hz, no
retire or GPU timeouts, a scanned-out image that is not black, finite poses for the three
devices, a position for each controller, and the fan record (a state or null, before and after).

### Acceptance

`python -m pico4 dev check` (development loop), `python -m pico4 hwtest flash-run --userspace
ARCHIVE …` and `python -m pico4 cycle --userspace ARCHIVE` (unattended, ending on stock boot and
ABL) run the same stage-2 checks ([hardware-testing.md](hardware-testing.md)). After the
userspace check, the head IMU and Monado's pose and display clients, they run
`pico4-vrdisplay-test 15` and `pico4-vrsync-test` when the archive's manifest lists
`pico4-vrdisplay`, then Valve's session when it lists `deckard-steamvr-rel`:

- the display layer test must hold the display rate, show red left and blue right on the
  panels and lose no fences (`validate_vr_display`); the semaphore test must pass all five
  checks (`validate_vr_sync`);
- before Valve's session the runner waits until the hottest zone is below 70 °C (180 s at
  most), then runs `pico4-valve-session-check --seconds 30` with its stderr kept out of the
  JSON (`VALVE_SESSION_*` in [pico4/userspace.py](../pico4/userspace.py)). It keeps SteamVR's
  logs and the session user's journal with its results.

## Verified results

| Check | Result |
| --- | --- |
| PC: lavapipe under QEMU user emulation, `PICO4_VRDISPLAY_FAKE=1` | `pico4-vrdisplay-test` passes: display "PICO 4" 4320×2160 at 90 Hz, 3-image FIFO swapchain, 232 frames in 4 s (emulation speed), vblank counter 6 → 362, scanned-out eyes (255,0,0) / (0,0,255) |
| PC: SteamVR 2.17.10 under QEMU user emulation | `vrstartup` and `vrserver` start, then `vrserver` aborts: its IPC asserts that the robust futex list is set up, which QEMU's user mode does not implement. SteamVR runs only on the headset |
| QEMU: mainline 4.19.325 test kernel ([userspace.md](userspace.md)) | `PICO4-USERSPACE-SELFTEST: PASS`: systemd 257 running with no failed units, the session user's logind session active with `pico4-session.target`, Monado's socket listening |
| Headset: `pico4-vrdisplay-test 15` | about 1,290 frames on the panels, layer 88.8–89.0 commits/s, vblank 89.998 Hz, no fence timeout |
| Headset: `pico4-vrsync-test` as `steamos` | passes: the child's GPU work starts after the parent's 160.5 ms of GPU work ends (GPU timestamps); host waits and a host signal cross between the processes; a wait submitted 200 ms before its signal holds 202.6–203.5 ms and completes 25 ms after the signal; a wait for an earlier batch of the same submission takes 0.3 ms |
| Headset: SteamVR without gamescope (`vrstartup`) | `vrserver` with `driver_monado`: headset "PICO 4" at 90 Hz, render target 2160×2160, both controllers with the PICO 4 input profile. `vrcompositor` in direct mode through the layer, startup complete in 0.36 s; its default environment, lens-distorted, on both panels; 250 of about 1,260 present waits over 15 ms |
| Headset: Valve's session (`gamescope-session.target`) | gamescope connected to SteamVR as an overlay client; dashboard running; 2,700 commits at 42.5/s, vblank 89.998 Hz, no fence or GPU timeout, GPU 99 % busy, 16.5 ms GPU wait per frame; SteamVR poses for the headset (6DoF) and both controllers (arm model) |
| Headset: Valve's session in the lenses (user, two minutes) | the full SteamVR environment, slightly jittery but mostly good (5,969 commits at 50/s); both controllers shown as SteamVR's generic locator, turning with the wrists, not moving in space (3DoF) |
| Headset: `pico4-valve-session-check`, 20 and 30 s | passes: startup 15–16 s, 51–54 commits/s, vblank 89.998 Hz, 12.1 ms GPU wait per frame, no fence or GPU timeout, no `mangoapp` restarts, scanned-out eye means about (10, 13, 19); headset and both controllers in SteamVR; the Monado session back afterwards; hottest zone 47–54 °C before, 86–92 °C after |
| Headset: `pico4-valve-session-check`, 30 s, KGSL context priorities | passes: `vrcompositor` and gamescope at KGSL level 1, the CEF renderer at 8; 2,188 commits at 55.6/s, 1,259 late, 10.3 ms GPU wait per frame (longest 25.7 ms), 5.75 ms retire wait, 1,847 of 2,187 present waits over SteamVR's 15 ms; without the priorities 53.1/s, 1,486 late, 11.5 ms (35.6 ms) |
| Headset: Valve's session with the Frame driver's compositor settings, no background dome (user, four minutes) | layer: 13,068 commits, 598 late, 8.4 ms GPU wait per frame, no present-wait timeout, hottest zone up to 69.4 °C; in the lenses the picture warped with head movement (lens correction not applied as before) and froze every few seconds: 47 of SteamVR's one-second present waits |
| Headset: `pico4-valve-session-check`, 30 s, vblank clock | SteamVR's defaults: 54.7 commits/s, 10.6 ms GPU wait per frame, no one-second wait; the Frame driver's settings without `waitForSceneGraph`: 3,392 commits at 87.5/s, 79 late, 7.2 ms, 6 of the present waits over 15 ms, no one-second wait (16 in 30 s before the clock) |
| Headset: Monado service | userspace check passes (session active, every listed device node accessible to `steamos`); OpenXR pose: 959–960 of 960 located, tilt 4.29° against the accelerometer's 4.28°, heading rate 0.005°/s; display client through the service (two layers, 30 s): about 2,686 frames at 89.5–89.6 Hz, 11 late, KMS target 88.4–89.6 commits/s at vblank 89.998 Hz, no fence timeout, eyes left/right correct; statistics written when the service stops, GPU floor back to the stock level 6 (305 MHz); the session user's client through the socket link: 1,348 frames at 89.74 Hz |
| Headset: acceptance cycle with stock restore (flash-run with the archive, EDL readback of boot, stock ABL restore) | passes in about 8 minutes from PICO OS to PICO OS on stock boot and ABL, 39 s of it receiving the 801 MiB archive: userspace check with the fan service, head IMU, OpenXR pose (tilt 3.8°), Monado display client at 89.9 frames/s, display layer at 86 frames/s on a 90 Hz vblank, shared semaphores, Valve's session presenting after 15 s at 51.3 commits/s with the headset and both controllers in SteamVR (hottest zone 45.3 → 69.4 °C with the fan at 30 %; 47.1 → 85.7 °C without fan control); stock boot and ABL verified by EDL readback; PICO OS 5.13.7 boots |

## Next steps

1. **Performance.** A compositor frame within the 11.1 ms period with room for the vblank
   phase and with the lens correction intact: the Facet renderer's distortion of the scene
   with `driver_monado`'s lens model. The panels' 72 Hz mode would give 13.9 ms but needs a 90 Hz frame before
   the switch.
2. **Cooling.** Measure the fan control ([cooling.md](cooling.md)) on the headset, with the
   full session for 10 minutes against an agreed limit for the hottest zone.
3. **The Steam client.** A network (Wi-Fi or USB) and a Steam login; gamescope's desktop
   overlays need the client.
4. **Controller positions.** IR-LED tracking, as for Monado ([xr.md](xr.md)); until then the
   controllers are 3DoF, placed by the arm model.
5. **Dashboard input.** Not verified: SteamVR loads its generic bindings for the PICO 4
   controller type, and clicking in the dashboard is still to be checked.
