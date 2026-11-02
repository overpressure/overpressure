# Feasibility: a kernel at the Steam Frame's level

Can the PICO 4's kernel be brought to the level of the Steam Frame's, so that Valve's Frame
userspace runs with the kernel features it was built for instead of the project's userspace
adaptations? This study compares the two kernels' configurations, examines whether the stock
kernel can be extended in place, and assesses the three ways to a newer kernel. The project's
rule so far is the stock kernel only ([roadmap.md](roadmap.md#constraints)); every way that
reaches the Frame's level changes that rule, which is the user's decision.

## Result

- **The stock kernel cannot be raised in place.** It loads only modules signed with PICO's
  per-build key, has no kexec, no `/dev/mem` and no BPF override. What it lacks is compiled
  out; nothing running on it can add it.
- **A rebuilt 4.19 does not reach the Frame's level.** Most missing options are configuration
  flags in 4.19, but the Frame's userspace also needs features that 4.19 does not have at all
  (systemd 258's minimum of 5.4, `io_uring`, `ntsync`, the msm DRM GPU driver with syncobjs),
  and PICO's kernel sources are not public.
- **The Frame's own kernel tree is the way to the Frame's level.** Valve's kernel,
  `linux-valve` 7.2.0, is a close-to-mainline tree, not a Qualcomm vendor kernel, and it
  already builds mainline's SM8250 (XR2) device trees. Mainline covers the XR2's SoC blocks
  (GPU, display over bonded DSI links with DSC, DSPs, camera subsystem, Wi-Fi, audio, storage,
  USB), and another XR2 headset, the Lynx R1, runs a close-to-mainline kernel under
  postmarketOS. What remains is board support for the PICO 4: its device tree, a panel driver,
  the camera synchronisation, the FastRPC interface of PICO OS's tracker, and power management.
- **Assessment.** Technically feasible, with a large but bounded effort (an estimated five to
  eight months of focused work, see [Effort](#effort)). The highest risks are charging and the
  fuel gauge (no mainline driver for the PM8150B's charger and fuel gauge is known), PICO OS's
  tracker on the mainline FastRPC driver, and the camera timestamps the tracker depends on.

## The two kernels

| | PICO 4 (stock) | Steam Frame |
| --- | --- | --- |
| Kernel | 4.19.81-perf+ (PICO OS 5.13.8), clang 8.0.12 (Android NDK) | 7.2.0 (`linux-72-deckard`, Valve's hotfix repository, built 2026-09-28), GCC 15.1 |
| Source | Qualcomm vendor kernel (Android, `kona`), not published by PICO | Valve's `linux-valve` tree on gitlab.steamos.cloud, close to mainline |
| Built-in options (`=y`) | 1,669 | 2,545 |
| Modules | 11 in the kernel's own build; vendor modules on `vendor` (audio, WLAN, ...); signature required (`MODULE_SIG_FORCE`, SHA-512, per-build key) | 30; no signature required |
| Device trees | PICO's, in the boot image (`kona`, header v2) and `dtbo` | mainline's arm64 Qualcomm trees, including the SM8250 boards (`qrb5165-rb5`, `sm8250-hdk`, ...) and the Frame's SM8650 boards |
| GPU | KGSL (`/dev/kgsl-3d0`), not a DRM device | msm DRM (`DRM_MSM`, DPU, DSI); Turnip on its msm backend |
| Display | vendor SDE `msm_drm` with vendor fences and `sspp_layout` | mainline DPU, atomic KMS |
| DSPs | downstream `adsprpc` FastRPC, downstream subsystem restart | upstream `fastrpc`, remoteproc (`QCOM_Q6V5_PAS`), PDR helpers |
| Wi-Fi | `cnss2` with the vendor `qca_cld3` module | ath12k (the Frame's chip); ath11k, which the XR2's QCA6390 needs, is not enabled |
| Security | SELinux, `ANDROID_PARANOID_NETWORK` | AppArmor, Yama, LoadPin, SafeSetID |
| Scheduler | `HZ=100`, WALT, `RT_GROUP_SCHED` | `HZ=1000`, no `RT_GROUP_SCHED`; Valve's configuration leaves `SCHED_CLASS_EXT` out |

The Frame's configuration enables SM8650 drivers; for an SM8250 board it needs the SM8250
clock, pinctrl and interconnect drivers and ath11k added. These are configuration changes,
not new code.

## What the Frame's userspace expects

Each gap, how the project bridges it on the stock kernel today, and whether a rebuilt 4.19
would close it:

| Feature (Frame: on) | Stock kernel | Bridge today | Rebuilt 4.19 |
| --- | --- | --- | --- |
| `USER_NS`, `PID_NS`, `IPC_NS`, `UTS_NS` | off | setuid bubblewrap for Steam Linux Runtime, containers share the host's PIDs; Lepton's Android container blocked | config |
| `MEMCG`, `CGROUP_PIDS`, `CGROUP_DEVICE` | off | `systemd-oomd` masked; no memory limits, a game that exhausts memory takes the session down | config |
| `SYSVIPC`, `POSIX_MQUEUE` | off | `pico4-sysvsem` preloaded into every process (semaphores, message queues); no System V shared memory | config |
| `BINFMT_MISC` | off | x86 Linux programs only through FEX started explicitly | config |
| `HIDRAW` | off | input only through evdev and uhid; no Steam Input on HID devices | config |
| `DEVTMPFS`, `FHANDLE` | off | udev rule creates every device node | config |
| `RT_GROUP_SCHED` | on | systemd's `cpu` controller kept off so real-time threads work | config |
| `ANDROID_PARANOID_NETWORK` | on | Android network groups for every user that opens sockets | config |
| zstd/LZ4, squashfs, erofs, btrfs | off (zram LZO only) | ext4 on zram with LZO | config |
| `USERFAULTFD`, `FANOTIFY`, `CHECKPOINT_RESTORE`, THP | off | none | config |
| systemd 258 and later (needs Linux 5.4) | 4.19 | systemd pinned at 257.7 (`IgnorePkg`) | no |
| `io_uring` (5.1) | absent | none | no |
| `NTSYNC` (6.14; Proton's synchronisation) | absent | Proton falls back to esync or the wineserver (`futex_waitv` for fsync needs 5.16); `systemd-modules-load` asks for `ntsync` in vain | no |
| `SCHED_CLASS_EXT` (6.12; Valve's `scx-scheds`) | absent | none | no |
| msm DRM GPU, DRM syncobjs, timeline semaphores | KGSL | `pico4-turnip-kgsl` (Mesa built with the KGSL backend), `pico4-vrdisplay` emulates SteamVR's shared semaphores over sync files, OpenGL through zink with a blit per swapchain image | no |
| Standard atomic KMS (DPU) | vendor SDE | `pico4-vrdisplay` drives the vendor fence contract and its own vblank timing | no |
| Upstream FastRPC ABI | downstream `adsprpc` | hexagonrpc translated to the downstream ABI | no |

With a Frame-level kernel the project's adaptation layer shrinks to what is specific to the
headset: `pico4-turnip-kgsl`, `pico4-sysvsem`, the semaphore emulation and most of the timing
work in `pico4-vrdisplay`, the devnode rule, the network groups, the systemd pin and the
`cpu`-controller drop-ins all go, and Valve's own Mesa runs the GPU as on the Frame.

## Extending the stock kernel in place

Every in-place route was checked against the stock configuration:

| Route | Stock kernel | Result |
| --- | --- | --- |
| Loadable modules | `MODULE_SIG_FORCE=y`, SHA-512, PICO's per-build key | only PICO's own modules load |
| kexec into another kernel | `KEXEC` off | impossible (and unnecessary: `boot` can be flashed) |
| Kernel memory from user space | `DEVMEM` off, no `/dev/kmem`, `STRICT_KERNEL_RWX` | impossible |
| BPF | `BPF_SYSCALL`, kprobes, uprobes, `BPF_EVENTS` on; no `BPF_KPROBE_OVERRIDE`, no `FUNCTION_ERROR_INJECTION` | observation only; cannot change kernel behaviour |
| Patched kernel image that accepts unsigned modules | the signature check is code in `Image`; `boot` is ours to flash | possible, but only tristate features can come as modules (`BINFMT_MISC`, a backported `ntsync`); namespaces, memcg, System V IPC, hidraw and devtmpfs are built into the core. Modules must match the stock build's symbol versions (`MODVERSIONS`) without its sources. Small gain, high risk |

The stock kernel's gaps are compiled out. Raising the kernel means replacing it.

## Way 1: a rebuilt 4.19

Rebuild the 4.19 vendor kernel with the Frame's options switched on.

- **Sources.** PICO has not published the phoenix kernel's sources (no public release found;
  a GPL request is possible). Qualcomm's public `kona` 4.19 trees on CodeLinaro are the closest
  base, without PICO's changes.
- **Vendor modules.** The audio techpack, `qca_cld3` and others on `vendor` are built against
  the stock kernel's exact symbol versions. A rebuilt kernel needs them rebuilt from the
  public Qualcomm sources too, configured like PICO's.
- **Ceiling.** It closes the configuration rows above, but not systemd 258, `io_uring`,
  `ntsync`, `sched_ext`, the msm DRM GPU path or the upstream DSP interfaces: Valve's Mesa,
  gamescope and SteamVR would still run on the project's KGSL and display layers.

Assessment: feasible in principle, months of work on an unpublished base, and it ends below
the Frame's level. Not recommended.

## Way 2: Valve's kernel tree with PICO 4 board support

Build `linux-valve` (7.2, the Frame's own tree) for SM8250 with a PICO 4 device tree.

### Precedents

- Valve's 7.2 package already contains mainline's SM8250 device trees; the tree builds for the
  XR2's SoC as it is.
- Mainline supports SM8250 boards (Qualcomm's RB5 and HDK, phones from Sony, Xiaomi and
  Samsung). The Xiaomi Pad 5 Pro (`sm8250-xiaomi-elish`) drives one panel over two bonded DSI
  links, as the PICO 4 does; DSC on bonded links is in the DPU driver, but no SM8250 board in
  mainline was found to use the two together.
- The Lynx R1, an XR2 headset, is a postmarketOS device on a close-to-mainline kernel
  (`linux-lynx-r1`; postmarketOS lists its audio codec and speaker amplifier as not working).

### Subsystems

| Subsystem | PICO 4 hardware (stock device tree) | Mainline | PICO 4 work | Risk |
| --- | --- | --- | --- | --- |
| Boot, UFS, USB gadget (NCM) | SM8250, UFS 3.1, DWC3 | supported | device tree; `qcom,msm-id`/`qcom,board-id` for the ABL's tree selection | low |
| GPU | Adreno 650 | msm DRM (a650 with GMU) | zap shader from the headset's firmware; clock and thermal tuning; Turnip's msm backend is Valve's Mesa | low–medium |
| Display | Sharp LS026B3SA, video mode, two DSI links, DSC, 4320×2160 at 72/90 Hz | DPU, bonded DSI, DSC | a panel driver from the stock tree's on-commands and timings; the GD32 MCU's backlight stays in user space | medium |
| SLPI (head IMU, controllers through `ble_mcu`, proximity) | SLPI with PICO's sensor registry | remoteproc and upstream FastRPC | `hexagonrpcd` runs on the upstream ABI it was written for (the downstream translation goes) | low |
| CDSP (PICO OS's tracker `libSlam`) | CDSP, PICO's signed DSP modules | upstream FastRPC, signed protection domains | `libSlam` calls Qualcomm's Android `libcdsprpc`, which speaks the downstream ABI: a translation to the upstream driver (the reverse of today's hexagonrpc layer) or a bionic build of an upstream-ABI FastRPC library | high |
| Tracking cameras | four OV7251 (of six camera sensor nodes), configured and triggered by the station at 50 Hz | CAMSS for SM8250 (CSIPHY, CSID, VFE and VFE-lite raw paths) | a sensor driver without a control port (the station owns it), SM8250's PHY tables in Valve's CAMSS, combo mode on CSIPHY 3, start-of-frame timestamps as precise as the stock CSL's (the tracker depends on them) | medium |
| CVP (depth mesh of the see-through view) | `/dev/cvp`, signed CVP firmware | no driver needed | PICO OS's pipeline opens a CVP session but takes its depth from a network on the Compute DSP: stand-ins in the tracker's root answer the session | none |
| Video decode (Steam Link) | Venus | iris (SM8250 support) | firmware from the headset | low–medium |
| Wi-Fi, Bluetooth | QCA6390 on PCIe, Bluetooth on UART | ath11k (QCA6390 hw2.0), `hci_qca` | board data from PICO's calibration in ath11k's format; enable ath11k in the configuration | low–medium |
| Audio | WCD938x codec, two WSA881x on SoundWire, LPASS macros | SM8250 machine driver, WCD938x, WSA881x, macros | machine routing in the device tree; PICO OS's calibration through the PICO's q6 calibration driver (patch 0013); the speakers' protection (voltage and current sense) is not applied | medium |
| Charging, fuel gauge | PM8150B charger (`qpnp-smb5`), its fuel gauge (`qpnp,fg`), SMB1390 charge pump | no driver known | drivers for charger and fuel gauge, or the hardware's defaults without battery data; an issue's battery level depends on it | high |
| Thermal, CPU frequency | TSENS, LMh, EPSS | supported | thermal zones and trip points from the stock tree; fan policy stays in user space | low |
| Station (controller radio), GD32 MCU | SPI with a vendor spidev power ioctl; UART | spidev, GENI serial | the station's power as a regulator or GPIO in the device tree instead of the vendor ioctl | low |
| Suspend | stock suspend works | SM8250 suspend partly | measure | medium |

### Boot and recovery

The way back stays as it is: the engineering ABL, `boot` flashed through the verified paths,
`dev restore` and EDL. The ABL boots any boot image on the unlocked headset; it picks the
device tree by the Qualcomm IDs, which the PICO 4's tree must carry. The new kernel has no
console on its first boots (no serial port; USB is up only once the gadget driver runs), so
early failures are read from a RAM console (ramoops) after a warm reboot into the stock
kernel. A wrong regulator or charger setting is the main hardware risk; the device tree takes
voltages and limits from the stock tree, not from guesses.

### Effort

Estimates for one experienced developer, in the order the work depends on:

| Phase | Exit criterion | Estimate |
| --- | --- | --- |
| 0. Boot | Valve's 7.2 kernel boots stage 1 from `boot`; SSH over USB NCM; read-only storage | 1–2 weeks |
| 1. Display and GPU | both panels at 90 Hz through DPU; Valve's Mesa on msm; SteamVR in direct mode through gamescope | 3–6 weeks |
| 2. SLPI and CDSP | head IMU, controllers and proximity through upstream FastRPC; PICO OS's tracker on the CDSP | 3–6 weeks |
| 3. Cameras | four OV7251 at 50 Hz with FSIN, timestamps within the tracker's tolerance; the still-headset gate passes | 3–6 weeks |
| 4. Wi-Fi, Bluetooth, audio, thermal, suspend | Wi-Fi joins, speakers and microphone, thermal throttling, suspend and resume | 3–5 weeks |
| 5. Power | charging at the charger's current, battery level in UPower and SteamVR | 4–8 weeks |
| 6. Integration | Valve's userspace without the stock-kernel adaptations; systemd from Valve's repository; the acceptance cycle passes | 2–3 weeks |

Together about five to eight months. Phases 0 and 1 settle the question cheaply: a booting
kernel with both panels and Valve's Mesa shows whether the rest is worth doing.

## Prototype

Phases 0 to 2 and the installed system with Valve's session run as a prototype beside the stock
kernel; the stock-kernel rule is lifted for it only. Its files are in [kernel/](../kernel/):

| File | Purpose |
| --- | --- |
| [sm8250-pico4-phoenix.dts](../kernel/sm8250-pico4-phoenix.dts) | the PICO 4's device tree on mainline's `sm8250.dtsi`: the stock tree's supplies for UFS, USB and the panels, its memory carve-outs, its unused GPIOs reserved, reboot modes, a RAM console, both DSI links with the panels, the GPU with its DDR vote, the GD32 MCU's UART, the three DSPs, the head IMU's supplies and clock, the keys |
| [pico4.config](../kernel/pico4.config) | merged over Valve's `gabe_defconfig`: the SM8250 drivers (with the display and GPU clock controllers, QFPROM, REFGEN), the panel driver, stage 1's USB gadget functions, the PON reboot modes, pstore, QRTR over GLINK |
| [patches/](../kernel/patches/) | fixes to Valve's tree that the PICO 4 needs (below) |
| [src/](../kernel/src/) | the PICO 4's own kernel sources, at their paths in the tree: the panel driver |
| [build-kernel.sh](../kernel/build-kernel.sh) | Valve's 7.2 source package (pinned by hash and commit), the patches and the own sources to `Image.gz` and the device tree |
| [dtbo_sink.py](../kernel/dtbo_sink.py) | the include that absorbs the ABL's board overlay (below) |
| [panel_sequence.py](../kernel/panel_sequence.py) | the include with the panels' command sequences, from the stock board overlay (below) |
| [recovery_image.py](../kernel/recovery_image.py) | the maintenance stage 1 on the new kernel as a whole recovery partition; `--installed` starts the installed system instead, `--stage1` carries this tree's stage-1 files; stage 1's build information names the new kernel and `OUT.json` records it, so that `dev check` accepts the image's stage 1; `--gpu-firmware` for the GPU, `--wlan-firmware` for the QCA6390; `--cmdline` for extra kernel words (such as the panel driver's parameters); `--debug` and `--trace` for failed boots (below) |
| [build-mesa-deps.sh](../kernel/build-mesa-deps.sh) | the two libraries Valve's Mesa links that the userspace lacks, from their pinned sources (below) |
| [tools/pico4-kmstest.c](../kernel/tools/pico4-kmstest.c) | a static KMS check for stage 1: the msm cards and their GPU, the connectors and modes, a test frame, the refresh rate |

### Results

| Phase 0 | Result on the headset |
| --- | --- |
| Boot | Valve's 7.2 kernel boots the maintenance stage 1 from `recovery`; SSH over USB NCM answers 13 s after the reboot |
| Storage | UFS with all six LUNs and their partitions; stage 1 switches every block device read-only |
| SoC | 8 CPUs, 7.4 GB RAM; CPU frequencies through `cpufreq-hw` (three policies, up to 2.84 GHz); TSENS thermal zones; RPMh regulators at the stock voltages; SPMI, PMIC RTC, watchdog, `socinfo` |
| Reboots | `pico4-reboot bootloader` reaches fastboot; a panic's warm reset fails ("Reboot failed"); the watchdog usually resets the headset about a minute later, with the RAM console kept, but not always: after one panic the headset stayed hung (no USB device answered) until a forced restart. Leaving a test boot therefore takes a plain reboot, never a panic |

| Phase 1 | Result on the headset |
| --- | --- |
| Display | the display subsystem, DPU, both DSI controllers and PHYs and the panel driver bind as one DSI connector (`msm-kms`); the driver's DSC configuration equals the parameter set in the panels' stock power-on sequence byte for byte; the panels' full power-on (supplies, VIO, reset, the 20 stock commands on both links, backlight) completes, and both panels answer the DCS power mode read with 0x0c: display on and normal mode, but sleep out and the booster off; a mode set of 4320x2160 at 90 Hz follows, with vblanks at 90.00 Hz |
| GPU | Adreno 650 (chip id 0x06050002), the microcode, the GMU firmware (v2.1.8) and PICO's zap shader load, the ring runs; devfreq from 305 MHz to 587 MHz (the chip's speed bin; PICO OS's VR level) with the stock DDR vote per level; one DRM device with the display, as on the Frame |
| Mesa | the userspace's Mesa 25.2.7 (Holo preview) in a chroot on this kernel: EGL on the surfaceless and GBM platforms, freedreno on the FD650, OpenGL 4.6 core and compatibility, OpenGL ES 3.2 |
| Valve's Mesa | the Frame's `deckard-mesa-linux-aarch64` 26.3.0-devel over the userspace in a chroot on this kernel: Turnip on msm ("Turnip Adreno (TM) 650", Vulkan 1.3, conformance 1.2.7.1) renders `pico4-vulkan-test`'s frame with all 65536 pixels correct; OpenGL 4.6 core and compatibility and OpenGL ES 3.2 through zink on Turnip, surfaceless and on GBM. The package has no freedreno gallium driver: on the Frame, OpenGL is zink. |
| In the lenses | kmstest's test frame and vkcube (compressed scanout) show on both panels; Valve's session in SteamVR's direct mode, seen worn: fluid, IMU head tracking without lag or freezes, "just like stock" (user). The panels' status byte reads 0x0c after our power-on and 0x1c as the bootloader leaves them, with the picture shown either way. The LCDB stays on throughout |
| Open | in one boot the lenses stayed dark with SteamVR presenting, when its mode set was the first after power-on (not yet reproduced); the DSI PHY's PLL fails to lock once at probe while the clock framework adopts the bootloader's display clocks (a warning, the display works after it) |

| Phase 2 | Result on the headset |
| --- | --- |
| GD32 MCU | QUP0's serial engine 1 as `ttyHS1` (its stock name); `pico4-fan status` reads duty, RPM, version ("BOOT02", as on the stock kernel) and fan info; the MCU's reset and boot lines stay as the bootloader set them, as on the stock kernel |
| DSPs | the SLPI, CDSP and ADSP run under mainline's remoteproc with PICO's signed images from the headset's modem partition; each brings up its IPC router, FastRPC (`/dev/fastrpc-sdsp`, `-cdsp`, `-cdsp-secure`, `-adsp`) and its own channels (the ADSP's `apr_audio_svc`, the CDSP's `cvp-glink-apps-dsp` and `cdsprmglink-apps-dsp`) |
| Head IMU | the sensors process on the SLPI attaches to `hexagonrpcd` over upstream FastRPC (`/dev/fastrpc-sdsp`), reads the headset's registry from `persist` and lists the sensors over QRTR (the kernel's own name service and PD mapper); `pico4-imu stream --rate 1000 --check` passes: accelerometer and gyroscope (TDK ICM-4x6xx) at 1023.89 Hz each over 10 s, no gaps, no out-of-order timestamps, gravity 9.805 m/s², gyro bias 0.006 rad/s |
| Keys | the stock tree's keys: PM8150's power key (`pm8941_pwrkey`) and RESIN key, and the GPIO keys (volume, confirm, home, app) with the stock codes; `pico4-powerkey` runs |

| Installed system | Result on the headset |
| --- | --- |
| Boot | the installed system (`super`, `userdata`) on Valve's kernel from `boot` (`dev install --boot`, the image from `recovery_image.py --installed --boot`), the maintenance stage 1 on the stock kernel in `recovery`; earlier as a one-shot from `recovery` (`--installed`): `graphical.target` 13.2 s after power-on (8.8 s kernel, 4.4 s userspace); the data partition and its disk writable, every other block device read-only; the `/etc` overlay without the inodes index, so that an update's new image is taken |
| RGB camera | `pico4-imx471` on CCI 0, four lanes of CSIPHY 1 at 2064.8 Mbit/s, CSID 4 and the third IFE-lite: 150 frames, 0 errors, 50.01 fps beside the tracking cameras. CAMSS never sets the camera NoC's clock, which stays at 19.2 MHz: the tree assigns 300 MHz, since the sensor's lines come at 1.7 GB/s and overflow the IFE-lite otherwise. The sensor powers up with its stream: a missing one fails only that stream |
| Colour room view on the depth mesh | scanned-out image with the headset lying still: both eyes in colour, the compositor on its RGB NV12 shader, 251 meshes in 10 s with 14,841 vertices, 64–72 ms after their pair; black above the RGB camera's field of view |
| Still headset, 60 s | tracker 0 poses lost, 0.61 mm and 0.05° from its start with the room view off, 0.54 mm and 0.05° with the colour room view and the mesh on; `pico4-slamd` 56 % of one core; hottest zone 61 °C |
| Compositor beside Steam's panel | no panel: 5.8 ms GPU per job. With Steam signed in, its dashboard panel (1920x1080, the Frame's size) in view: 7.4-9.3 ms by how much of the view it fills. The web helper renders it at about 60 jobs of 4.05 ms a second whenever the panel is in use or in view, and for the first 50 s after a session start. With all of the below, the web helper rendering, 96 % of the GPU: 90.0 commits/s, 0-1 late of 3,600, worn no visible stutter (user). It takes four things. On one ring: 60-67 commits/s, 40-45 % late, the compositor waiting up to 6.9 ms for the GPU. With preemption: p99 1.1 ms, 81-82 commits/s; the job still reaches the GPU through a kworker at nice 0, mostly on the little cores, p99 2.5 ms, maximum 6.3 ms. With the Frame's kernel thread priorities: that kworker real-time on cores 4-7, p99 0.33 ms, 84.5 commits/s. With the big cores on `performance`, no frequency ramp in each frame: p99 0.12 ms, 89.3-90 commits/s; worn this still fails: with the panel filling the view a job ends a few tenths of a millisecond past the last moment for the next vblank, 9-17 % of the vblanks get no frame, at any head speed, and the view overshoots and springs back on fast turns (without a web helper job 0-0.6 %, at up to 396 deg/s). With the stock path's pacing in `pico4-swapchain-usage` (four images, one frame ahead, a lead of 5 ms) a job starts 7.3 ms after a vblank and ends about 6 ms before the last moment. The GPU stays at 587 MHz throughout, a floor changes nothing, and neither a preemption nor a new panel image lengthens a job against its neighbours |
| Worn on kernel #58 (user) | fluid, Wi-Fi, Steam Store, game launches and Beat Saber work |
| Fixed since, worn check open | Speakers: S16_LE playback, a 1 kHz tone with its harmonics 41–50 dB below it. Android apps: the Frame controller's layout for our controllers. POOLS: patch 0021, it loads to its level selection on the table. Steam Link: patch 0020 and the controller type; `vrlink`'s path without the PC gives FFmpeg's picture, 8-bit and Main10 ([session.md](session.md#steam-link)) |
| Wi-Fi after a reboot | a saved network reconnects by itself within a minute of boot, without input. A boot can find no DHCP lease within 45 s. With DHCP's replies dropped through such an attempt, NetworkManager retries 0.1 s after it fails and connects by itself; left to iwd's autoconnect, it stayed offline until it was connected by hand. A second attempt may also run its 45 s without a lease with DHCP free; the third then connects within a second |
| Preemption on the A650 | four rings, the compositor on the first, a client at the default priority on the third. In 40 s of Steam's panel redrawing: 7,000 preemptions, no faults or recoveries; each switch to the compositor's ring takes 0.25 ms (median), at most 2 ms |
| Preempted jobs' pixels | a Vulkan job of 3,000 multisampled, blended, textured draws and a second pass reading the first, each job preempted about 5 times by the compositor: the same hash in 60 of 60 frames as without a compositor, in GMEM, in sysmem and with Turnip's own choice |
| GPU faults under preemption | a client's protected register read or illegal packet on the third ring (CP error), and a job that never ends: only that client's queue takes the fault, the compositor keeps running and shows the same frame rate, and the pixels above stay the same. A job in a CP wait cannot be preempted: the compositor stands still for 2 s until the GPU's hang check recovers it, as it does on one ring |
| Pose to panels | latency probe, compressed scanout, 90 flips/s, one frame ahead, a lead of 5 ms, 11 steps: a handed-out pose reaches the vblank that shows it after 17-28 ms, 24 ms median; SteamVR adds 11 ms of its own, the driver predicts 17 ms: 12 ms and the lead |
| Room view | scanned-out image with the headset lying still: both cameras in stereo behind the overlays, with the session's default settings, no depth mesh; black band and a stretched strip above the cameras' field of view |
| Wi-Fi after boot | iwd's radio on, persist's address kept, networks listed, disconnected; the band `no6` of the Steam client's connections accepted |
| `dev check`, installed | on the installed system booting Valve's kernel from `boot`, after a cold start: passed, Valve's session with the headset and both controllers in SteamVR, 89.9 frames per second over 30 s, 0.15 % late, no stalls, hottest zone 48 °C |
| Fan | `pico4-fan` runs PICO OS's policy on the same 14 TSENS channels under mainline's names; 30 % at 30–53 °C |
| DSPs, head IMU | `pico4-slpi` and `pico4-cdsp` start the DSPs through remoteproc; `pico4-sensors.target` is reached once the IMU is listed (35 s); the 1 kHz stream check passes |
| SteamVR | Valve's session (`gamescope-session.target`) with `driver_monado` and the PICO 4 headset; `vrcompositor` in direct mode through Turnip's own display WSI on msm, 4320×2160 at 90 Hz, compressed scanout images; 89.7 frames per second in a steady 10 s window with the CPUs' own governor (894 of 896 frames in one refresh), 5.9 ms of the compositor's GPU time per frame, hottest zone 42 °C |
| `dev check` | on a RAM boot of the userspace from the maintenance stage 1 on Valve's kernel: the userspace check (devtmpfs, storage policy, systemd running without failed units, Turnip's Vulkan test on msm, fan), the head IMU at 500 Hz, and Valve's session check's performance part: 90.03 frames per second over 30 s, none late, no stalls, 5.9 ms of GPU time per compositor job, hottest zone 43 °C. It fails only on the controllers, which need the station |
| Controller station | QUP SE19's SPI as the stock board overlay describes it, a plain spidev at 6 MHz as `/dev/spidev0.0`, its supply L5C as `station-power` (a regulator-output, `/sys/devices/platform/station-power/state`) in place of the vendor spidev's power ioctl. The station announces itself 24 ms after a power cycle and answers the version and wake-up queries; a controller connects. The camera commands are answered with a failure while the cameras have no power on this kernel |
| Video codec | Venus (patch 0009 gives it back its own files: Valve's iris series leaves it uncompilable, and Valve's iris knows only SM8550; iris is left out, since Venus leaves SM8250 to iris whenever iris is built) with PICO's firmware (`venus.mdt` from the headset's modem partition) in the stock carve-out; `pico4-venus-probe` probes it once the firmware is in `/usr/lib/firmware` (the kernel's own probe comes before the root file system and fails for good). Measured: the decoder and encoder nodes (`qcom-venus-decoder`, `-encoder`); FFmpeg's `hevc_v4l2m2m` decodes 60 frames of 1080p60 HEVC in 0.1 s. An H.264 decode through `h264_v4l2m2m` gave no frames and held the system |
| GPU bus vote | each GPU level votes its stock `bus-freq` on DDR (fixed, where KGSL scaled between the level's `bus-min` and `bus-max`). Measured on the USB input (PM8150B's ADC, `usb_in_i_uv`) with the battery full and the session idle, the GPU between 305 and 587 MHz: three 25 s windows each, 82,054 against 81,419 µV mean with every level at its stock `bus-min`, a difference of 0.8 % within the windows' 4 % spread; at 587 MHz both are level 11. The fixed vote costs no measurable power |
| Battery | the PICO's own driver (`pico4-pm8150b-battery`, patch 0008) reads PM8150B's registers as the stock kernel reports them: the fuel gauge's monotonic state of charge (`0x4009`, 255 at full, scaled to percent as the stock driver does), the charger's state (`0x1006`) and the USB input (`0x1310`), as the `battery` and `usb` power supplies, checked for changes every 30 s. On USB: 100 %, charging, as PICO OS reported at the same time; UPower shows the battery and the line power. The charger and the fuel gauge run as PICO's boot loader set them up (input current limit register `0x1370` at 0x1e) |
| Tracking cameras | The OV7251s' control port belongs to the station, not the SoC: PICO's stock kernel accepts a tracking camera's probe without reading its chip ID (`cam_sensor_match_id`) and drops its register writes (`cam_sensor_i2c_modes_util`, for the `reserved` mark in the I2C info), so the SoC powers the modules, clocks them and receives their frames. `pico4-ov7251` (patch 0014) is a sensor subdevice without an I2C client: the stock power sequence while streaming (standby, 18 ms, MCLK, 1 ms, reset, 18 ms; TITAN_TOP GDSC held), 640×481 Y10 on one lane at 480 Mbit/s. Each camera has its own CSIPHY → CSID → VFE RDI: slot 0 PHY 5, CSID 0; slot 1 PHY 3 (lane 0), CSID 1; slot 2 PHY 3 (lane 2, lane 3 its clock), CSID 2; slot 3 PHY 4, CSID 3. Patch 0016 dates each buffer with the CSID's hardware start of frame. Measured with `pico4-station camera init` and `on`: all four at 49.96 fps at once, frames with their metadata line and the scene; `pico4-cam capture`: 1,500 frames per camera at 49.997 fps, none with an error, every timestamp from the hardware and monotonic. |
| Head tracker | PICO OS's `libSlam` on the CDSP through the FastRPC bridge, fed through CAMSS. The compute context banks are coherent, as in the stock device tree (`dma-coherent`; without it the DSP saw stale frames and lost tracking for moments); the bridge answers `FASTRPC_CONTROL_KALLOC`, so the library lets the kernel allocate. Headset still on a table, 60 s on the installed system: 61,384 poses, none lost, within 0.4 mm and 0.03° of the first, 47 % of one core. SteamVR gets the head's poses. Worn check open |
| Controller tracker | Both controllers through the station and the sensor service, tracker as on the stock kernel. Lying on a table, about 50 cm in front of and 30 cm below the head, with their motors running: in a replay of 1,500 frames per camera both acquired 4.8 and 5.4 s in, then stayed tracked to the end, 16–18 LEDs per frame, 0.66 px median residual, spread about 2 mm under the motors; in Valve's session both reached SteamVR 56 cm in front of and 29 cm below the head. Worn check open |
| Suspend | s2idle only (`/sys/power/mem_sleep`); the RTC alarm, the power key and the GPIO keys are wake sources. With the stock DSP processes running, the freezer stopped their FastRPC server and the DSPs lost their reverse calls (patch 0011). Suspend and resume themselves are not verified; a reboot that hung in shutdown after this needed a forced restart |
| CDSP root listener | PICO OS's `cdsprpcd` in the tracker's root through the FastRPC bridge ([xr.md](xr.md#head-tracking)) on upstream FastRPC: it attaches to the CDSP's guest OS, maps the DSP's memory request (patch 0012) and opens its listener and event handles; no measurable CPU, and the IMU's 1 kHz check passes beside it (1023.89 Hz, no gaps) |
| Backlight in standby | SteamVR's standby keeps the panels on with black frames and its compositor holds the display, so the display layer's panel power-down has no place here. The panel driver's enable line (GPIO 29) is also a backlight device, `pico4-panel`; `driver_monado` writes its `bl_power` on `VREvent_EnterStandbyMode` and `VREvent_LeaveStandbyMode`. The GD32's `SetBrightness` is acked at every level but changed neither the MCU's display report nor the input current. Whether the lenses go dark needs an eye check |
| Heat | 10 minutes of Valve's session out of standby with the head tracker running and no game, on a table: hottest zone 53 °C at the fan's 30 %, the tracker at 40 % of a core, the compositor at 88.7 frames per second and 6.9 ms of GPU time |
| Not on this kernel yet | The speakers' protection (voltage and current sense), the charger's configuration (it runs as the boot loader set it up) |

### Display

The stock tree drives both panels as one display: its panel node (Sharp LS026B3SA, 90 Hz video
mode) names both DSI controllers and both PHYs, 2160x2160 per link, with DSC (four 540x16
slices per line, 8 bits per component, 8 bits per pixel, two slices per packet). Valve's Frame
does the same with its two panels (`sm8650-mp.dts`): one panel node on the first DSI link,
which is the master of the bonded pair, a second port to the second link, and a mode twice as
wide, which msm splits between the links. The PICO 4 follows that model with a panel driver of
its own (`src/drivers/gpu/drm/panel/panel-pico4-ls026b3sa.c`): 4320x2160 at 90 Hz from the stock
timings, the stock display's one switched supply (VDDIO from L7A), reset (GPIO 75 with the
stock 20/20/30 ms sequence), VIO enable (GPIO 133) and backlight enable (GPIO 29). The
bootloader leaves VIO an input, so each line becomes an output on its first use; a value set on
an input pin does nothing, which kept the panels dark. The ±5.8 V bias comes from PM8150L's
LCDB, for which mainline has no driver; it stays as the bootloader set it. With the stock
picture on, the stock kernel holds GPIOs 29, 75 and 133 high and GPIO 71 low: the 5.5 V that
GPIO 71 switches has no user there and is off, and the tree holds it off too. L10C (3.0 V) is
the GD32 MCU's supply, which drives the backlight; the stock tree gives it to the MCU's UART,
and the tree keeps it always on. The panels' command sequences are PICO's data: the build copies them
from the stock board overlay into the tree (`panel_sequence.py`, as `pico4,on-command` and
`pico4,off-command`), and the driver sends each command with its stock data type to both links.
The module parameter `keep_boot_state` leaves the panels as the bootloader set them up, and
`keep_supplies` (a comma-separated list) leaves single supplies alone; the driver logs every
step of the power-on, since a stall leaves only the RAM console.

### DSPs and the GD32 MCU

Mainline's `qcom_q6v5_pas` loads PICO's own signed images (`slpi.mdt`, `cdsp.mdt`,
`adsp.mdt`, with their `.bNN` segments) through TrustZone, as the stock PIL does: the same PAS
IDs, the stock carve-outs (mainline's CDSP and SLPI regions equal the stock ones; the ADSP and
SPSS regions are moved to the stock layout), the CX vote and the AOP signal. The compute DSP
also needs the bus vote the stock loader holds while it starts (master 154 to slave 10070, in
mainline the compute NoC's `qnm_npu` to `qns_cdsp_mem_noc`): without it TrustZone refuses the
start ("failed to authenticate image and release reset"). The images come from the userspace's
`/usr/lib/firmware`; stage 1 has none, so each DSP stays offline there until it is started
through `/sys/class/remoteproc`.

The MCU (fan, lens motor, backlight) hangs off QUP0's serial engine 1, which mainline describes
only as I2C and SPI; the tree adds the UART there, two wires on GPIOs 6 and 7 as in the stock
tree, aliased to `ttyHS1`. Its reset (GPIO 12), boot (GPIO 13) and sync (GPIO 14) lines stay
unused, as the stock kernel leaves them. L10C (3.0 V), its supply, is always on.

The head IMU (TDK ICM-4x6xx on the sensor DSP's bus) needs three things from the apps side,
which the stock kernel's XR driver (`qcom,xr-stdalonevwr-misc`) provides and mainline has no
driver for: its VDDIO (L8C, 1.8 V) and VDD (L11C, 3.008 V), which the tree keeps always on, and
its CLKIN from PM8150's GPIO 3 in its first special function, which the tree sets as the GPIO
controller's default state. Without the clock the IMU takes its configuration but sends no
samples. On the userspace side the sensors process needs `CONFIG_QRTR_SMD` (QRTR over the DSPs'
GLINK channels), the registry in `persist`, and the downstream socinfo attributes mainline's
`/sys/devices/soc0` lacks (`hw_platform` MTP, `platform_subtype` Unknown, `platform_subtype_id`
0, `platform_version` 65536). Mainline has the same SMEM values in debugfs
(`qcom_socinfo/hardware_platform` 8, `hardware_platform_subtype` 0, `platform_version`);
`sensorspd` names them as the stock kernel does and serves them from a runtime copy of
`hexagonrpcd`'s root.

### The installed system

The installed system runs on both kernels; what only the stock kernel needs stays off on
Valve's, and what Valve's kernel needs is found by the hardware, not by a kernel switch:

| Part | Stock kernel | Valve's kernel |
| --- | --- | --- |
| Data partition | stage 1 makes `userdata` writable | a partition counts its disk's read-only flag too: stage 1 makes the disk writable, its other partitions keep their own flag, and stage 2's block policy leaves that disk alone |
| DSPs | `/sys/kernel/boot_*`, restart level `RELATED`, userspace `qrtr-ns` and `pd-mapper` | `/sys/class/remoteproc` (crash recovery restarts only that DSP), the kernel's name service and PD mapper; the stock units skip themselves |
| Sensor service | `hexagonrpcd` on `/dev/adsprpc-smd-secure`, socinfo from sysfs | `/dev/fastrpc-sdsp`, socinfo from debugfs (above) |
| Fan | `cpu-0-N-usr`, `cpu-1-N-usr`, `gpuss-N-usr` | `cpuN-thermal`, `cpuN-top/bottom-thermal`, `gpu-top/bottom-thermal`: the same TSENS channels |
| Kernel throttling | the stock tree's `-step` zones | the same trip points on mainline's zones (below) |
| VR performance hint | `msm_performance` frequency limits and `core_ctl`'s online cores | the same frequency limits on the cpufreq policies, the big cores on `performance` at their limit; every core stays online |
| System V IPC | `pico4-sysvsem` preloaded into every process and the Steam client | the kernel's own; `pico4-stock-preload.service` and the user generator `50-pico4-stock-kernel` skip themselves |
| OpenGL | zink on Turnip for gamescope and Steam: Mesa's msm driver finds no GPU behind `msm_drm` | zink too, as on the Frame, whose Mesa has no other OpenGL driver (the userspace's Mesa would run freedreno: FD650, OpenGL 4.6) |
| GPU clock floor, kernel thread priorities | KGSL's `min_pwrlevel` while presenting; `pico4-kthread-priorities` | msm's devfreq governor; `pico4-kthread-priorities` with the Frame's values, among them Valve's real-time priority for the GPU's workqueues |
| Device nodes | udev's `devnode` helper creates them in the tmpfs `/dev` that stage 1 hands over (no devtmpfs) | devtmpfs creates them, and stage 1 hands its devtmpfs over as it is: systemd's device monitor follows udev only when `/dev` is a devtmpfs or udev already runs, and without it no device starts a unit; the helper stays for the block policy (every disk read-only) |
| systemd | pinned at 257.x (258 needs Linux 5.4) | the pin stays: the Frame's own systemd is 257.7 |
| Monado's own service | `pico4-monado` with its KMS target | skips itself (open: Monado outside SteamVR) |
| Wi-Fi | the stock qcacld-3.0 module with `pico4-wlfw-client` | ath11k on PCIe 0 (the QCA6390's power sequencer on the stock supplies), PICO's firmware and board data (`bdwlanq.elf`) under ath11k's names, the address from persist's `wlan_mac.bin` (`pico4-wlan-address`), iwd told to keep ath11k's interface (`/etc/iwd/main.conf`); the Frame's NetworkManager, which knows the band `no6` that the Steam client's Wi-Fi connections ask for. The chip's own address changes at each boot and the Steam client names it in the profiles it saves, so `wlan-address` takes that line out before NetworkManager starts; the stock units skip themselves |
| Bluetooth | none | `hci_qca` on QUP0's UART 6; firmware and NV data from PICO OS's `bluetooth` partition, mounted read-only (`pico4-bt-firmware`), the address from persist's `pico_bt_mac` (`pico4-bt-address`, the management interface's Set Public Address), the Frame's BlueZ. `hci_qca` is built in and sets the controller up before the partition is mounted; `pico4-bt-firmware` probes it again once the firmware is there, since the kernel offers a controller to bluetoothd only after a setup that succeeded |
| Android games (Lepton) | not on this branch (the stock kernel lacks user namespaces and binderfs; open-tasks carries a stand-in for podman) | the Frame's way: rootless podman (Arch's 5.6.2: the Frame's 5.5.2 links gpgme 11), the Frame's passt, `lepton-compat`, Valve's Android Mesa, the Android and Linux Vulkan layers and adb (with protobuf 25 beside it); the session user gets subordinate IDs 100000-165535. Measured on a RAM boot: binderfs mounts in the session user's namespace, podman runs rootless with crun, overlay storage and the full ID map. Lepton itself and the games come with Steam (network) |
| Video codec restart level, audio modules, calibration | `pico4-venus`, `pico4-audio-modules` (stock modules), `pico4-audiocal` (the stock kernel's calibration store) | skip themselves |
| Audio | the stock modules' card; `pico4-adsp` boots the ADSP through the stock loader, `pico4-audiopd` starts PICO OS's audio process on the downstream FastRPC ABI | `pico4-adsp` boots the ADSP through remoteproc; `pico4-audiopd` starts the audio process on upstream FastRPC (`INIT_CREATE_STATIC`, the stock remote heap given to the DSP's VMs); `pico4-lpass-macros` probes the VA macro again once that process runs (the AFE refuses its hardware vote before, and the driver has no bind file); the SM8250 machine driver's card "PICO4" with its own UCM profile (`conf.d/sm8250`) |
| Boot splash, panel probe | KGSL's display (`msm_drm`), six layers | msm's primary plane; `pico4-panel-probe` probes the panels' connector on every boot (`detect` in its sysfs status), also where the splash does not run: msm leaves it unprobed until a DRM master asks, and SteamVR's compositor then finds no direct-mode display |
| Power key | `qpnp_pon` | `pm8941_pwrkey` |
| Session | seat0 without VTs | the session takes VT 1 in getty's place, as sddm does on the Frame (logind requires a VT where the kernel has them) |
| GPU | `/dev/kgsl-3d0`; `pico4-vrdisplay` provides SteamVR's direct mode and shared semaphores | msm's render node; the layer stays unloaded (`PICO4_VRDISPLAY_DISABLE` from a user environment generator), Turnip and msm provide both, as on the Frame; `pico4-swapchain-usage` paces the compositor as `pico4-vrdisplay` did |
| PICO 4 driver | finds the headset by the SLPI subsystem | or by the remoteproc named `slpi` |

The userspace changes run on the headset from a bind mount over the installed files; the
installed image still has the stock-kernel versions.

### Thermal throttling

The tree takes the stock tree's kernel throttling onto mainline's thermal zones (step_wise):

| Sensor | Stock zone | From | Throttles |
| --- | --- | --- | --- |
| GPU (both) | `gpuss-max-step` | 95 °C | the GPU, every level (mainline: 85 °C) |
| DDR | `pop-mem-step` | 95 °C | the big cores and the prime core, every level |
| WLAN, video, DDR, Q6/HVX, camera, compute, NPU | their `-step` zones | 100 °C | the GPU to its second-lowest level, 400 MHz |

The stock tree also caps the CDSP, the modem and the NPU at 100 °C; they have no cooling devices
on mainline. The CPUs keep mainline's frequency trips (90 and 95 °C, critical at 110 °C): the
stock kernel isolates cores only at 110 °C and leaves the rest to the CPUs' limits hardware, and
90 °C is the project's limit.

### SteamVR's direct mode

`vrcompositor` finds the panels through Vulkan (`VK_KHR_display`), acquires them with the DRM
fd and paces itself with `VK_EXT_display_control`, which Turnip's display WSI provides on msm.
It asks for storage usage on its swapchain images and never uses it; the A650 cannot compress
storage images (Turnip refuses the compressed modifier with storage usage), so the display
scanned out linear images and the compositor needed about 11 ms of GPU time per frame, 60–66
frames per second. The display takes compressed XRGB8888 (`DRM_FORMAT_MOD_QCOM_COMPRESSED`),
which the stock kernel's display refused. `pico4-turnip-kgsl`'s `wsi-compressed-scanout.patch`
(the stock kernel's Mesa; on Valve's Mesa the layer `pico4-swapchain-usage` removes the usage
instead) adds two things to Mesa's display WSI:
- where the application's usage rules out a modifier the display takes, the application
  renders into ordinary images and each present copies them into compressed scanout images
  (Mesa's image blit, which Metal and DXGI use): 9.5 ms per frame, the copy 1.5 ms;
- the driconf option `vk_wsi_ignore_storage_usage`, set for `vrcompositor` in
  `/usr/share/drirc.d/50-pico4-steamvr.conf`, creates the swapchain images without the unused
  storage usage, so that SteamVR resolves straight into compressed images: 7.0 ms per frame, as
  on the stock kernel.

The rest was the DDR. The compositor submits each frame as one GPU job right after the previous
flip; the job has to finish, and msm's commit to start, before the next vblank. In a recording of
the kernel's own events (GPU submit, flush and retire, atomic commit, DPU kickoff, flip, vblank)
the frames that missed it ran 7.8 ms on the GPU instead of 5.9 ms, at the same 587 MHz, and
started 1.8 ms after their submit instead of 0.4 ms: 84.5 frames per second. Mainline's tree
gives the A650 no interconnect and its OPPs no bandwidth (msm's own bandwidth vote through the
GMU is for the A7xx), so the DDR ran at what the CPUs asked for. With the CPUs at their highest
frequencies the same session ran 89.8 frames per second without a late frame. The tree now votes
for the GPU what the stock driver does: its bus table for LPDDR4X (the bootloader's
`ddr_device_type` 7) at each level's `bus-freq-ddr7`, 8,171,875 kB/s at 587 MHz. With the CPUs'
own governor: 89.7 frames per second, 894 of 896 frames in one refresh, the GPU's time per frame
between 5.9 and 6.0 ms. The OPP core reads bandwidths only when the first OPP has one, so the
670 MHz OPP, which this speed bin does not use, carries the top level too.

Every boot logs "DSI PLL(0) lock failed" and a warning that `dsi0_phy_pll_out_dsiclk` was
already disabled, from `dsi_phy_driver_probe`. It is harmless: when the PHY registers its clock
provider, the clock core adopts the display clock controller's orphaned pixel clock source,
which has `CLK_OPS_PARENT_ENABLE`, and enables its new parent, the PHY's PLL, for the switch.
The PHY's supplies and power domain are not on yet, so the lock status reads 0 and the enable
fails; the core then disables the clock that never enabled. The DSI driver configures and locks
the PLL itself when it enables the display, and the panels work on every boot.

Only the DRM master makes the kernel probe a connector's modes; anyone else gets the list a
probe left. Without one, a compositor that starts first sees no real mode ("1024x768") and finds
no headset display (the kernel has no fbdev client that would probe). On both kernels the boot
splash in stage 1 is the first master and probes; on Valve's kernel it shows its picture on msm's
primary plane, across both panels. The splash runs only when the installed system starts, so on
msm `pico4-panel-probe` probes on every boot: writing `detect` to the connector's sysfs status
fills its modes (4320x2160) without a master.

With nobody wearing the headset, SteamVR enters standby after `power.turnOffScreensTimeout`
(5 s) and presents black frames; measurements set it to an hour with `vrcmd` first and back
afterwards, as `pico4-valve-session-check` does.

`pico4-valve-session-check` counts the frames from msm's own events on this kernel
(`msm-frame-stats`: completed flips, vblanks and the GPU time of the compositor's jobs), with the
display layer's statistics keys, so `session-performance` judges both kernels alike. A 10 s
recording of SteamVR: 897 flips at 89.7 per second, 3 late, vblanks at 90.0 Hz, 5.9 ms GPU time
per frame (p90 6.0 ms). The scanned-out image is compressed and readable only by the DRM master,
so on this kernel the check has no image of its own.

### Patches

| Patch | Why |
| --- | --- |
| 0001 `smem_dram` | SM8250's DDR information is the 14-frequency variant of the v3 layout, which the 7.2 parser reads as entry 13 of a 13-entry table; GCC treats that as undefined ("iteration 13 invokes undefined behavior") and drops the loop's exit, so the `qcom-smem` probe never returns and the boot stalls in `deferred_probe_initcall`. Valve builds with GCC too; the Frame's SM8650 has the v7 layout. The patch indexes the table from the raw data. |
| 0002 panel | the panel driver's configuration option and object |
| 0003 root identity | the ABL's board overlay replaces the root's compatible and model with "qcom,kona-mtp"; drivers matching on the machine (msm's UBWC configuration, cpufreq-dt's blocklist) then miss "qcom,sm8250", and the display fails to bind. The tree keeps its own values in `pico4,compatible` and `pico4,model`, which the kernel puts back after unflattening it. |
| 0004 GPU BCL | Valve's "drm/msm/a6xx: enable BCL" asks the secure world's GPU register service for Battery Current Limit on every a6xx GMU and fails the GPU when it refuses; SM8250's firmware refuses, and the failed GPU took the display with it. BCL now runs only where the secure world enabled it. |
| 0005 writeback | Valve's "drm/msm/dpu: register all writebacks" pairs two writeback blocks whenever the DSI links are bonded; SM8250 has one, the pair's second id comes from past the catalog, the KMS initialisation fails and its error path oopses in `dpu_kms_destroy()`, which left the headset hung without a watchdog reset. The pair is formed only where a second block exists. |
| 0006 Bluetooth firmware wait | Valve's "bluetooth: make sure firmware is avail before probe" defers every `hci_qca` probe until `qca/hmtbtfw20.tlv`, the Frame's WCN7850 firmware, loads; the QCA6390 has no such file (it loads `htbtfw20.tlv` when the device opens), so its probe deferred forever. The wait applies to the WCN7850 only. |
| 0008 battery | the battery driver's configuration option and object (mainline has no driver for PM8150B's charger or fuel gauge) |
| 0009 Venus | Valve's squashed iris series moves Venus under `vcodec/` and onto the code it shares with iris, but leaves `hfi_venus.c` and `core.c` on the old names, so Venus no longer compiles; this gives Venus back its own files as they were before the series |
| 0010 repeated handover | PICO's SLPI firmware raises its handover bit (the vendor kernel's proxy unvote) about five times a second while it runs; mainline logged each edge as an error, the vendor kernel acts on the first and ignores the rest. The handover is still issued once; a repeat is logged at debug level |
| 0011 interrupted FastRPC call | the freezer ends a FastRPC server's wait (`hexagonrpcd`, which serves the SLPI's sensors process and the ADSP's audio process) with `-ERESTARTSYS`, and upstream FastRPC sent the restarted call to the DSP again under a new context while the DSP's reply went to the first one: the reverse call was lost. In a suspend test the SLPI's sensors process died on it and the ADSP's audio process stalled until its watchdog fired. Like the vendor driver, the interrupted context is kept and the restarted call waits on it again; another call by that thread drops it |
| 0012 client buffer mmap | the vendor driver's mmap also maps a buffer the client allocated (its DMA-BUF at the client's address, with the client's flags, through the remote mmap method), which PICO OS's FastRPC library uses for the DSP's own memory requests (`apps_mem`); upstream mapped only buffers it allocates itself and refused the rest ("flag not supported 0x0"). Such a request now maps the client's buffer, and munmap finds it |
| 0013 audio calibration | PICO OS's HAL registers its custom topologies with the ADSP core, opens each path's COPP with the path's topology and sends its processing and volume parameters after the open; mainline opened every COPP with the null topology. q6core gains the core service's memory map and unmap and the topology registration (opcodes as PICO's `audio_q6.ko` builds them), q6adm an in-band set-parameter command (v6), and q6routing asks the PICO's calibration driver (`src/sound/soc/qcom/qdsp6/q6calibration-pico4.c`) for a path's topology and hands its parameters on; a path without calibration, or one whose topology the ADSP refuses, keeps the null topology |
| 0014 tracking camera driver | the configuration option and object of `pico4-ov7251` |
| 0015 CAMSS on SM8250 | Valve's combo mode work programs every PHY outside combo mode with SM8650's lane table and common setting, whatever the SoC. SM8250 gets its own table back (equal to the stock kernel's PHY 1.2.1 table), the 2-phase common setting, and a combo table with the stock kernel's values; the second endpoint's sensor links to the PHY's second sink pad, which the driver knew only for the Frame's sensor by name |
| 0016 hardware start of frame | a buffer was stamped when its write master finished, a readout and an interrupt's delay after the frame's start. The CSID stamps each stream's start of frame on the system counter (the registers the stock camera stack reads: RDI offsets 0x390 and 0x394, 0x290 and 0x294 on the lite CSIDs), and that dates the buffer on `CLOCK_MONOTONIC`, as the tracker needs to match frames to the IMU |
| 0017 the RGB camera's path | SM8250's XR variant has three more CSID-lite/IFE-lite pairs than mainline describes, and the tracking cameras take the four it knows. The RGB camera takes the next (registers at 0x0acdd400, its CSID 0x200 above, interrupts 449 and 266, as PICO's tree lists them), with the option and object of `pico4-imx471` |
| 0018 16-bit samples from an RDI | the bus writes whole 16-byte bursts: 2328 pixels of packed RAW10 make 2910 bytes a line. As PICO's stack, the client asks the CSID's source pad for 16-bit samples, 4656 bytes a line, with the decode format from the Bayer format it comes in as |
| 0019 preemption on the A650 | msm preempts only on 7xx, and a 4 ms job of the web helper holds the compositor back on this GPU. As PICO's KGSL on the same GPU and firmware, the submit path says where each ring is saved, yields after each submit and checks for a higher ring, without 7xx's postamble. A ring switch loses what hw_init set once, among it each block's 64-bit addressing, so that the next client faults on addresses without their upper half: the firmware restores it from KGSL's register list, read from PICO's kernel, which `CP_ME_INIT` now hands over with KGSL's mask and ordinals. `msm.enable_preemption=0` turns it off |
| 0020 compressed capture formats | Steam Link's client builds its pictures from the reported pitch and queues them with the reported size. Venus reported the 8-bit pitch for `QC10C` and sizes with an extradata margin the firmware does not use, so every picture buffer was refused. It now reports them as iris does ([session.md](session.md#steam-link)) |
| 0021 the unaligned atomic fixup | Valve's fixup for FEX's unaligned atomics reads 128 bits with a store back, and only the load had an exception table entry. On a page that reads but does not write yet, the store ended in an oops that took the thread with it: POOLS stayed in its first load forever, with its other threads waiting. With the entry the fault handler resolves the store and the instruction runs again |

Mainline sets RPMh regulators only to voltages on the regulator's own steps, and one invalid
regulator node fails the whole PMIC's set. The stock tree runs L7A (the panels' VDDIO) in
1.8–1.9 V and its panel driver asks for 1.9 V, which is no step (8 mV from 1.504 V): enabling the
next step up, 1.904 V, stalled CPU 0 (a hard lockup); 1.896 V, the closest step within the stock
range, works.

### Valve's Mesa

The Frame's Mesa (`deckard-mesa-linux-aarch64`) links two libraries that the userspace does not
have: `libdisplay-info.so.1` (Turnip's display code) and `libsensors.so.5` (gallium's HUD).
`build-mesa-deps.sh` cross-builds them from their pinned sources, libdisplay-info 0.1.1 and
lm_sensors 3.6.0 (only its library), with the userspace's `pnp.ids`, into a tar to unpack over
the userspace root:

```sh
# Linux/WSL: hwdata's pnp.ids (the userspace build root's), work and output folders
devices/phoenix/kernel/build-mesa-deps.sh <root>/usr/share/hwdata/pnp.ids \
    /var/tmp/pico4-kernel/mesa-deps /var/tmp/pico4-kernel/out
```

Decision: the system's Mesa is Valve's, `deckard-mesa-linux-aarch64` unchanged, the same commit as
the Android driver Lepton uses: Vulkan through Turnip, OpenGL through zink on it, as on the Frame.
It replaces the system's Mesa, whose only other user was the stock kernel, which the project no
longer pursues. `libVkLayer_PICO4_swapchain_usage` removes the storage usage from `vrcompositor`'s
swapchains, which it asks for and never uses, so that it resolves straight into compressed scanout
images.

The same layer gives `vrcompositor` the frame pacing the display layer gave it on the stock kernel,
from the same settings (`PICO4_VRDISPLAY_FRAMES_AHEAD=1`, `PICO4_VRDISPLAY_VSYNC_LEAD_US=5000`):
- four swapchain images. SteamVR asks for two, and then its acquire waits for a flip, so that no
  frame can start before a vblank, whatever else is set;
- a present wait returns once the present before it was shown;
- the vblank counter ticks 5 ms early, with Valve's private query answering that tick's time. SteamVR's
  frame starts about 4 ms before the vblank it expects, so 7.3 ms after a real one, and has until
  the vblank after next. The layer publishes the lead in `$XDG_RUNTIME_DIR/pico4-vrdisplay-lead-us`,
  and the driver predicts its poses that much further;
- a wait returns at a count SteamVR has not read, or it spins for a second
  ([session.md](session.md#one-second-waits)). When it had to wait for its flip, it also waits for the next
  tick: a frame that started at once would be shown a refresh later, and that queue would stay for good.
  SteamVR uses no display event fences on this path.

| SteamVR's compositor, headset on a table, out of standby | Frames per second | GPU time per frame |
| --- | --- | --- |
| Mesa 26.1.6 with `wsi-compressed-scanout.patch` | 89.76 | 6.5 ms |
| Valve's Turnip as it is | 89.93 | 8.0 ms |
| Valve's whole Mesa with the layer, installed | 89.7 | 6.45 ms |

0.37–0.75 % of frames came late across session checks with the whole Mesa, 0.45 % in the one run on
Mesa 26.1.6. OpenGL reports 4.6 through zink on Turnip. Open: Steam Link, FEX and Lepton's games on it, which need a network.

### Source

Valve's `linux-valve` repository needs a login, but Valve publishes each kernel package's GPL
source package, which carries the repository: `linux-72-deckard-7.2.0+g509b71a1bdcd-1.src.tar.gz`
(2.5 GiB) from `steamdeck-packages.steamos.cloud/archlinux-deckard-hotfixes/source/`, branch
`sm8650-7.2`, built with `gabe_defconfig`.

```sh
# Linux/WSL: the source package, the stock recovery image (python -m overpressure stock), work and output folders
devices/phoenix/kernel/build-kernel.sh linux-72-deckard-7.2.0+g509b71a1bdcd-1.src.tar.gz \
    cache/stock/<firmware>.recovery.img /var/tmp/pico4-kernel/work /var/tmp/pico4-kernel/out
python devices/phoenix/kernel/recovery_image.py /var/tmp/pico4-kernel/out \
    cache/stock/<firmware>.recovery.img out/dev/dev-recovery.img out/kernel/test-recovery.img
```

### The ABL's board overlay

Booting, the ABL lays a board overlay over the kernel's tree: from the `dtbo` partition, or for
recovery from the image's `recovery_dtbo`. The PICO 4's is Qualcomm's "kona MTP" overlay
(board-id 0x10008), 635 fragments addressed through 222 downstream labels. Several of these
labels (`spmi_bus`, `tlmm`, `soc`, `pdc`, `intc`) exist in mainline's tree too; applied there,
the overlay would add downstream PMIC, pinctrl and SoC nodes to live mainline nodes. The tree is
therefore built without dtc's symbol table and gets its own from `dtbo_sink.py`: every label of
the stock overlays resolves to an empty node below `/pico4-dtbo-sink`, which has no compatible,
so no driver sees what the overlay puts there. None of the overlays' nodes carries a compatible
that mainline searches for across the whole tree (`ramoops`, `qcom,cmd-db`, `qcom,smem`, ...).
The `dtbo` partition stays untouched.

### Kernel size

Valve's configuration builds a large kernel: 24.7 MB `Image.gz`, 141 MiB in memory with its BSS
(the stock kernel: 35 MB), mostly a 16 MiB log buffer with its index tables. The ABL checks the
decompressed kernel against a region of its own (from UEFI's `KernelBaseAddr` and `KernelSize`).
On the headset it loads the kernel at 0xA0080000 and the device tree and the ramdisk directly
below 0xB0000000, which leaves about 245 MB for the kernel; Valve's configuration fits
unchanged.

### A one-shot test boot

The test image goes to `recovery` (the whole partition with the stock vbmeta, as `dev image`
builds it), never to `boot`. With an empty bootloader message the ABL starts `boot`, the
installed system on the stock kernel; a reboot with reason `recovery` starts the test kernel
once. Whatever happens then, a panic (`panic=5`), a stage-1 deadline or a hard reset, the next
boot is the installed system again. Afterwards `dev image`'s recovery image goes back to
`recovery`.

The ABL passes only the boot image header's first command line field (512 bytes with its
terminator) and drops the extra one. `recovery_image.py` therefore leaves out the base image's
words for Android and the downstream kernel, which stage 1 does not read, and refuses a longer
line.

The new kernel prints nowhere on its first boots (no serial port, USB only once the gadget
runs). Its RAM console has the stock kernel's layout (ramoops at 0xB0000000: 2 MiB, 1 MiB
console, 1 MiB pmsg, no dump records, no ECC), so after the reboot the installed system on the
stock kernel reads the failed boot's log from `/sys/fs/pstore/console-ramoops-0`. Only a
panic's reset keeps it: stage 1's return to fastboot leaves it corrupted (stage 1's own log in
`pmsg-ramoops-0` survives with bit errors). For a boot that stalls, `recovery_image.py --debug`
logs every driver probe before it starts, prints every task's stack and every CPU's backtrace on
the panic, and makes stage 1 panic where it would return to fastboot; `--trace FUNCTION` records a
function's call graph and prints it on the panic. The hung-task detector skips killable and idle
waits, and this preemptible kernel reports no soft lockup for a busy loop, so a stalled probe
shows up only as `deferred_probe_initcall` waiting.

## Recommendation

1. Keep the stock kernel as the supported base until a new kernel passes the acceptance cycle;
   the two can coexist, since the kernel lives in the boot image.
2. If the Frame's level is the goal, take Way 2, not Way 1. Its prototype has passed phase 0
   (Valve's kernel runs stage 1 from `recovery`, see [Results](#results)), most of phase 1
   (both panels at 90 Hz through DPU, and the GPU under Valve's own Mesa: Turnip on msm and
   OpenGL through zink, as on the Frame) and the first part of phase 2: the GD32 MCU, the three
   DSPs with PICO's images, and the head IMU at 1 kHz over upstream FastRPC and QRTR. The
   installed system runs on it, and Valve's session presents through SteamVR's direct mode at
   89.7 frames per second without the stock kernel's display layer.
3. Decide early how charging is handled (phase 5): without it the headset is not usable away
   from development, whatever else works.

## Sources

- The stock kernel's configuration: embedded in the PICO OS 5.13.8 boot image (`IKCONFIG`),
  read with `python -m overpressure inspect`.
- The Frame's kernel: `linux-72-deckard` 7.2.0 and `deckard-linux-6.16-sm8650` from Valve's
  hotfix repository (`archlinux-deckard-hotfixes/release/0.3.x`), configuration embedded in
  their images.
- The PICO 4's hardware: the stock device tree and `dtbo`.
- [postmarketOS: Lynx R1](https://wiki.postmarketos.org/wiki/Lynx_R1_(lynx-r1)),
  [postmarketOS SM8250 kernel](https://pkgs.postmarketos.org/package/main/postmarketos/aarch64/linux-postmarketos-qcom-sm8250).
