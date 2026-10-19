# Valve's VR session (roadmap step 6)

Step 6 brings up the Steam Frame's VR session on the original PICO 4: SteamVR with the
headset as its display and tracked devices, and gamescope showing Steam and desktop apps as
SteamVR overlays. It builds on the XR hardware of [XR.md](XR.md) and the SteamOS userspace of
[USERSPACE.md](USERSPACE.md), on the same stock kernel. Valve's packages come from the Frame's
public repository, as in step 4; the build output stays private.

> **Status, 2026-10-19: runs on the headset; acceptance cycle with stock restore passed.**
> Valve's own session target starts gamescope (OpenVR backend) and SteamVR 2.17.10. SteamVR's
> compositor drives both panels in direct mode through this project's Vulkan layer, and
> `driver_monado` gives it the headset (6DoF) and both controllers (3DoF, arm model). Two kernel
> gaps are bridged in userspace: System V semaphores and semaphores shared between processes.
> In the full session the Adreno 650 is the limit: about 50 frames per second. The Steam client
> needs a network and a Steam login, which the POC has neither of yet.

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
| `driver_cv`, `dsp_service`, FPGA, radio firmware | `driver_monado`, Monado's SteamVR plugin, with the PICO 4 driver of [XR.md](XR.md) in `vrserver` |
| Direct mode through Valve's Turnip on `msm` | `pico4-vrdisplay`, a Vulkan layer that implements the display path on the stock KMS driver |
| Semaphores shared between processes (DRM syncobjs on `msm`) | the same layer emulates them over sync files ([below](#shared-semaphores)) |
| System V semaphores (SteamVR's process locks) | `pico4-sysvsem`, a preloaded library ([below](#system-v-semaphores)) |
| `driver_frame_hmd`'s SteamVR settings | the relevant one in the session user's `steamvr.vrsettings` ([below](#steamvr-configuration)) |
| Steam client | not yet: no network, no Steam login |
| — | Monado as a system service: the OpenXR runtime outside Valve's session |

### The session

`pico4-session.service` (system) runs `/usr/libexec/pico4-poc/session` as `steamos` with the
PAM stack `pico4-session`. `pam_env` gives the session the class `user` before `pam_systemd`
registers it, so logind starts the user manager and the session counts as active. The script
then starts the session target in the user manager:

- `pico4-session.target` (default): a graphical session whose OpenXR runtime is the Monado
  service. The script links Monado's socket into `$XDG_RUNTIME_DIR`.
- `gamescope-session.target`: Valve's session, through Valve's own
  `start-gamescope-session`. SteamVR is the OpenXR runtime; `driver_monado` owns the hardware.

`/etc/pico4-poc/session.conf` selects the target (`PICO4_SESSION_TARGET=...`).

From the Frame's packages the build installs SteamVR and the session package, and leaves out
SteamVR's Frame-only drivers and tools (`NoExtract` in [pacman.conf](userspace/pacman.conf)):
the tracking driver, the Frame controller driver, the controller radio's firmware and
programming scripts, other headsets' drivers, Android and Windows binaries. gamescope runs
without MangoHud's `mangoapp`, the Steam client's performance overlay: the Frame's MangoHud
needs libraries of the Frame's private base, and the overlay has nothing to show without the
Steam client. The build removes that one option from Valve's session script, which would
otherwise restart the missing program in a loop. It masks the units that need Frame hardware or would
touch the PICO's: `sddm`, `dsp_service`, `steamvr-program-ble` (a firmware OTA for the Frame's
radio, which must never run against the PICO's station),
`steamvr-set-kernel-thread-priorities`, `steamvr-v4l2loopback`, `steam-video-codec-setup` and
the debug port forwards. The Steam client and `sddm` count as installed for dependency
resolution only (`!name` in [packages.txt](userspace/packages.txt)).

### Monado as a service

`pico4-monado.socket` listens on `/run/pico4-monado/monado_comp_ipc` for clients of any user;
the first client starts `pico4-monado.service` (root), which then owns the display, the head
IMU, the cameras with Basalt, the controllers and the lens motor until it is stopped. The
OpenXR runtime library is Monado's IPC client. Clients find the socket in their
`$XDG_RUNTIME_DIR`: the session links it there, and root clients set
`XDG_RUNTIME_DIR=/run/pico4-monado`. The KMS target writes its statistics when the service
stops (`/run/pico4-monado/kms.json`); a patch makes Monado's service shut down cleanly on
`SIGTERM`, which also lowers the GPU clock floor again. The statistics cover the service's
whole lifetime, so a measurement starts a fresh service for its client.

`driver_monado` in SteamVR opens the same hardware in-process, so the service and Valve's
session never run together: nothing connects to the socket in Valve's session.

### SteamVR's display: pico4-vrdisplay

`vrcompositor` presents only through direct mode: Vulkan's `VK_KHR_display` with
`VK_EXT_acquire_drm_display`, or a Wayland DRM lease. Turnip's KGSL backend has no display
(the GPU is not a DRM device on this kernel), and the stock `msm_drm` shows clean frames only
with the vendor fence contract and six colour layers ([GRAPHICS.md](GRAPHICS.md)).
[`pico4-vrdisplay`](src/vrdisplay/pico4-vrdisplay.c) is an implicit Vulkan layer, enabled by
`PICO4_VRDISPLAY=1`, that implements this path for one display, "PICO 4", 4320×2160 at 90 Hz:

- **Display queries.** The display, its mode and plane, and `vkGetDrmDisplayEXT` for the DSI
  connector. `vkAcquireDrmDisplayEXT` takes the compositor's DRM fd: as the first opener of
  the device it is DRM master.
- **Surface and swapchain.** Linear, dma-buf exportable images, imported as framebuffers,
  in FIFO order. A present waits for the frame's semaphores, then a present thread commits it
  atomically with the release and retire fences, one flip in flight, exactly like Monado's
  KMS target.
- **Timing.** A vblank thread keeps one kernel vblank event queued. It drives
  `vkRegisterDisplayEventEXT` fences, `vkGetSwapchainCounterEXT` and `vkWaitForPresentKHR`. A
  present counts as shown when its retire fence signals.
- **GPU clock floor.** While presenting, it raises the KGSL floor to 587 MHz, as the KMS
  target does (`/sys/class/kgsl/kgsl-3d0/min_pwrlevel` is writable for the `video` group).
- **Statistics.** `PICO4_VRDISPLAY_STATS` receives commits, vblank rate, fence waits and
  timeouts, present-wait timeouts and the scanned-out eye colours every ten seconds;
  `PICO4_VRDISPLAY_DUMP` one frame as a PPM.

`pico4-vrdisplay-test` drives the layer the way `vrcompositor` does and shows red on the left
eye and blue on the right. `PICO4_VRDISPLAY_FAKE=1` (or `=WxH`) replaces the panels with a
90 Hz timer, so the handshake can be tested on any Vulkan driver, such as lavapipe on a PC.

### Shared semaphores

SteamVR's compositor shares timeline semaphores with other processes as `OPAQUE_FD` handles
(its mirror textures first, then the swapchains of every client). Turnip's KGSL backend can
export a semaphore only as a sync file, and the stock kernel has no object that keeps a GPU
fence reachable from several processes: `msm_drm` has no syncobjs (`DRM_CAP_SYNCOBJ` is 0) and
KGSL no timelines. The compositor's semaphore creation then fails, and it exports the null
handle it got, which crashes Turnip. [`pico4-vrsync.c`](src/vrdisplay/pico4-vrsync.c), part of
the display layer, emulates these semaphores where the driver lacks them:

- **Handle.** The `OPAQUE_FD` handle is a memfd with the semaphore's shared state: the value
  it reached and up to 64 pending signal operations, each with the process that holds its
  fence. A robust, process-shared mutex guards it; a futex word wakes waiters.
- **Signal.** A queue signal operation goes to a binary semaphore instead. After the
  submission, its sync file becomes the operation's fence, held by the signalling process.
- **Wait.** A queue wait looks up the earliest pending signal operation that satisfies it,
  fetches its fence from the holder over a Unix socket (`SCM_RIGHTS`) and waits through a
  binary semaphore with that fence imported (nothing, if the value is reached). A wait
  submitted before its signal operation holds the submission until the signal is submitted
  (two seconds at most).
- **Fence thread.** A thread in each signalling process watches the fences it holds, records
  the values they reach and hands them out. The layer stays loaded while it runs.
- **Host.** `vkWaitSemaphores`, `vkSignalSemaphore` and `vkGetSemaphoreCounterValue` work on
  the shared state; a holder that exits counts as signalled.

Every process that uses such a semaphore must run the layer (`PICO4_VRDISPLAY=1`, which
`steamvr.service` sets). `pico4-vrsync-test` shares one timeline between two processes and
checks GPU waits (by timestamps), host waits and signals, and a wait submitted before its
signal. `PICO4_VRDISPLAY_SYNC=off` leaves semaphores to the driver.

### System V semaphores

The stock kernel has no `CONFIG_SYSVIPC`. SteamVR's processes take their named locks (such as
`Global\VRBootstrapper`, which keeps a second SteamVR from starting) with
`semget`/`semop`/`semctl`; with `ENOSYS` they report a "DuplicateBootstrapper" and quit.
[`pico4-sysvsem`](src/sysvsem/pico4-sysvsem.c), preloaded for every process through
[`/etc/ld.so.preload`](userspace/rootfs/etc/ld.so.preload), implements these calls on a table
in `/dev/shm/pico4-sysvsem`: a robust mutex, a futex for blocked operations, and `SEM_UNDO`
adjustments that come back when a process exits or is found gone. It is inert until a program
calls it. Its test runs in the package build.

### SteamVR configuration

The session user's SteamVR settings ([steamvr.vrsettings](userspace/session/steamvr.vrsettings))
disable the drivers of other headsets, force `driver_monado` and set
`enableLinuxVulkanAsync`. The Frame's headset driver sets that last one in its own settings,
which are not loaded with that driver disabled. Without it the compositor creates no compute
queue and stops at an assertion; with it, it shares Turnip's single queue for both.
[openvrpaths.vrpath](userspace/session/openvrpaths.vrpath) registers `driver_monado`.

[`monado-steamvr-pico4.patch`](userspace/pkg/pico4-monado/monado-steamvr-pico4.patch) adapts
`driver_monado`:

- the render target is one eye's size (2160×2160), not the whole panel;
- the controllers' pose is the PICO 4 grip pose (it asked them for a head pose);
- positions marked valid count, not only tracked ones, so the 3DoF controllers appear where
  the arm model places them.

### Performance and heat

In Valve's full session (gamescope's overlay and the dashboard's CEF renderer besides the
compositor) the GPU is 99 % busy at its 587 MHz floor, and the compositor presents about 42
frames per second. Without gamescope, SteamVR on its own presents most frames at 90 Hz;
about one in five misses its vblank and shows one refresh later.

Nothing cools the headset actively under this userspace: no fan control and no Qualcomm
`thermal-engine`, whose user-space thermal zones stay idle; only the kernel's `step_wise`
zones throttle the CPUs. Within minutes of the full session the GPU reached about 95 °C and the
CPU clusters 96–98 °C. The session check therefore runs the session for 30 s and records the
hottest zone before and after.

## Build

As for step 5 ([XR.md](XR.md#build)): `scripts/build-userspace.sh` with GPU firmware and sensor
data. The archive is 840 MB (2,567 MiB installed), 376 MB more than step 5's, almost all of it
SteamVR.

## Run

The session starts at boot (`graphical.target`). Through the Monado service:

```sh
XDG_RUNTIME_DIR=/run/pico4-monado pico4-openxr-display 30         # as root over SSH
runuser -u steamos -- env XDG_RUNTIME_DIR=/run/user/1000 pico4-openxr-display 30
systemctl stop pico4-monado.service                                # releases the hardware
```

Valve's session (mind the heat):

```sh
systemctl stop pico4-monado.service
echo PICO4_SESSION_TARGET=gamescope-session.target > /etc/pico4-poc/session.conf
systemctl restart pico4-session.service
rm /etc/pico4-poc/session.conf; systemctl restart pico4-session.service    # back to Monado
pico4-valve-session-check --seconds 30                             # both ways, as a check
```

The display layer and the shared semaphores on their own (the Monado service must be stopped
for the display):

```sh
PICO4_VRDISPLAY=1 PICO4_VRDISPLAY_STATS=/run/vrdisplay.json pico4-vrdisplay-test 15
runuser -u steamos -- env PICO4_VRDISPLAY=1 \
  VK_DRIVER_FILES=/usr/share/vulkan/icd.d/pico4_turnip_kgsl.aarch64.json pico4-vrsync-test
```

## Verification

| Check | Result |
| --- | --- |
| PC, lavapipe under QEMU user emulation, `PICO4_VRDISPLAY_FAKE=1` | `pico4-vrdisplay-test` passes: display "PICO 4" 4320×2160@90, 3-image FIFO swapchain, 232 frames in 4 s (emulation speed), vblank counter 6 → 362, scanned-out eyes (255,0,0) / (0,0,255) |
| PC, SteamVR 2.17.10 under QEMU user emulation | `vrstartup` and `vrserver` start, then `vrserver` aborts: its IPC asserts that the robust futex list is set up, which QEMU's user mode does not implement. SteamVR runs are hardware-only. |
| PC, QEMU with mainline 4.19.325 ([USERSPACE.md](USERSPACE.md#qemu-stage-1--userspace-on-a-stock-like-419-kernel)), final archive | `PICO4-USERSPACE-SELFTEST: PASS`: 2,678 MiB in zram, systemd 257 running with no failed units; the session user's logind session active with `pico4-session.target`, Monado's socket listening; 409 packages |
| Headset, 2026-10-19: `pico4-vrdisplay-test 15` | 1,291 frames on the panels, vblank 89.998 Hz, no fence timeout |
| Headset: `pico4-vrsync-test` as `steamos` | pass: the child's GPU work started at tick 3,562,566, after the parent's 160.5 ms of work ended (3,554,537); host waits and a host signal crossed; a wait submitted 200 ms before its signal held 203.5 ms, then completed 25 ms after it |
| Headset: SteamVR without gamescope (`vrstartup`) | `vrserver` with `driver_monado`: headset "PICO 4" at 90 Hz, render target 2160×2160, both controllers with the PICO 4 input profile. `vrcompositor` in direct mode through the layer, startup complete in 0.36 s; its default environment, lens-distorted, on both panels (frame 300); 250 of about 1,260 present waits over 15 ms |
| Headset: Valve's session (`gamescope-session.target`) | gamescope connected to SteamVR as overlay client; dashboard running; 2,700 commits at 42.5/s, vblank 89.998 Hz, no fence or GPU timeout, GPU 16.5 ms per frame (99 % busy); SteamVR poses for the headset (6DoF) and both controllers (arm model) |
| Headset: in the lenses (user), two minutes of Valve's session | the full SteamVR environment, "a little jittery but the majority looks good" (5,969 commits at 50/s); both controllers shown as SteamVR's generic locator, turning with the wrists, not moving in space (3DoF) |
| Headset: `pico4-valve-session-check --seconds 20` | passed: compositor presenting 15 s after the switch, 1,581 commits at 53.8/s, vblank 89.998 Hz, no fence or GPU timeout, eye means (10, 13, 19); headset and both controllers in SteamVR; the Monado session back afterwards. Hottest zone 53.8 °C before, 92.3 °C after |
| Headset, final archive (SHA-256 `a8f4f22e…`, 840 MB): userspace check, Monado service | check passed (session active, every device node accessible); OpenXR pose 960 of 960 located, tilt 1.18°, heading rate 0.005°/s; display client through the service (two layers, 30 s): 2,687 frames at 89.56 Hz, 11 late, KMS target 88.4 commits/s at vblank 89.998 Hz, no fence timeout, eyes left/right correct; statistics written on stop, GPU floor back to level 6; the session user's client through the socket link: 1,348 frames at 89.74 Hz |
| Headset, final archive: SteamVR's paths | `pico4-vrdisplay-test`: 1,293 frames, layer 89.0 commits/s, vblank 89.998 Hz, no timeout; `pico4-vrsync-test`: pass (GPU wait by timestamps, early wait held 202.6 ms, same-submission wait 0.3 ms); `pico4-valve-session-check --seconds 30`: passed, presenting after 16 s, 51.8 commits/s, GPU 12.1 ms per frame, no `mangoapp` restarts, hottest zone 54.3 → 91.1 °C |
| **Acceptance cycle, 2026-10-19** (`userspace-hardware-cycle.ps1`, final archive, stock restore) | **passed**, 9 minutes from stage 1 to the restored stock boot: archive received in 193 s; userspace check and head IMU; OpenXR pose 959 located, tilt 4.29° against the accelerometer's 4.28°; Monado display client 2,686 frames at 89.48 Hz, KMS target 89.6 commits/s at vblank 89.998 Hz, no fence timeout, eyes left/right correct; display layer 88.8 commits/s; shared semaphores; Valve's session presenting after 15 s at 51.2 commits/s, no fence or GPU timeout, headset and both controllers in SteamVR, hottest zone 47.1 → 85.7 °C. Stock boot and ABL then verified by EDL readback, and PICO OS 5.13.7 booted |
| First acceptance attempt, same day | failed in the runner, not on the headset: `systemctl` noted on stderr that `pico4-monado.socket` stays active, and the note ended up in the client's JSON (the runner now discards it; the host tests' fake headset prints it too). The cycle restored stock boot and ABL (both verified by readback), but after its final EDL reset the headset stayed off until a hard reset |

## Next steps

1. Performance in the full session: what gamescope's overlay and the dashboard cost on the GPU,
   and the Frame's other compositor settings (simplified shaders, overlay quality).
2. Cooling: the fan and a thermal policy under this userspace.
3. The Steam client: network (Wi-Fi or USB) and a Steam login.
4. Controller positions (IR tracking), as for Monado ([XR.md](XR.md)).
