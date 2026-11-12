# Valve's kernel

| | |
| --- | --- |
| What | The Steam Frame's kernel, `linux-valve` 7.2.0, close to mainline, with the PICO 4's changes, drivers and device tree; `boot` carries it and it runs the installed system. The maintenance stage 1 in `recovery` stays on the stock Android kernel |
| How | Its source is [overpressure/linux](https://github.com/overpressure/linux): Valve's commit and the project's commits on it, in layers (`common/7.2`, `sm8250/7.2`, `phoenix/7.2`). [kernel/release.json](../kernel/release.json) pins one of its releases; the installer downloads it, checked by hash, or builds the same commit, with the same result |
| Code | [kernel/](../kernel/): `release.json`, `build-tree.sh`, `imx471_mode.py`, `recovery_image.py`; [tooling/kernel.py](../tooling/kernel.py) |
| State | [Results](#results): boot, display, GPU, DSPs, sensors, cameras, audio, Wi-Fi, Bluetooth, video codec, battery and Valve's VR session verified on the headset |

## The two kernels

As shipped:

| | PICO 4 (stock) | Steam Frame |
| --- | --- | --- |
| Kernel | 4.19.81-perf+ (PICO OS 5.13.8), clang 8.0.12 (Android NDK) | 7.2.0 (`linux-72-deckard`, Valve's hotfix repository), GCC 15.1 |
| Source | Qualcomm vendor kernel (Android, `kona`), not published by PICO | Valve's `linux-valve` tree on gitlab.steamos.cloud, close to mainline |
| Built-in options (`=y`) | 1,669 | 2,545 |
| Modules | 11 in the kernel's build; vendor modules on `vendor`; signature required (`MODULE_SIG_FORCE`) | 30; no signature required |
| Device trees | PICO's, in the boot image (`kona`, header v2) and `dtbo` | mainline's arm64 Qualcomm trees, including SM8250 boards and the Frame's SM8650 |
| GPU | KGSL (`/dev/kgsl-3d0`), not a DRM device | msm DRM (DPU, DSI); Turnip on msm |
| Display | vendor SDE `msm_drm` | mainline DPU, atomic KMS |
| DSPs | downstream `adsprpc` FastRPC, subsystem restart | upstream `fastrpc`, remoteproc (`QCOM_Q6V5_PAS`), PDR helpers |
| Wi-Fi | `cnss2` with the vendor `qca_cld3` module | ath12k (the Frame's chip); ath11k, which the QCA6390 needs, is off (`pico4.config` enables it) |
| Security | SELinux, `ANDROID_PARANOID_NETWORK` | AppArmor, Yama, LoadPin, SafeSetID |
| Scheduler | `HZ=100`, WALT, `RT_GROUP_SCHED` | `HZ=1000`, no `RT_GROUP_SCHED`; `SCHED_CLASS_EXT`, which needs BTF (`pahole` in the build container) |

## Files

| File | Purpose |
| --- | --- |
| [release.json](../kernel/release.json) | the pin: overpressure/linux's tag (`phoenix/7.2-1`), its commit, the build image it was built with, and the SHA-256 of `Image.gz`, the device tree, `config` and `version` |
| [tooling/kernel.py](../tooling/kernel.py) | `kernel fetch` (the installer's step: the release's files into `out/kernel`), `kernel build` (the pinned commit built here in the release's build image, [below](#building-it-yourself)), `kernel check DIR`, `kernel contracts` ([below](#contracts)) |
| [build-tree.sh](../kernel/build-tree.sh) | a checkout of overpressure/linux's `phoenix/7.2` to `Image.gz`, the device tree, `config`, `version` and `build.json` (release, commit, banner, camera table, the tooling's commit, the build image, hashes); `uname -v` names the commit. That repository's CI builds each branch with it once, each tag twice on two machines, and releases a tag whose builds agree |
| [imx471_mode.py](../kernel/imx471_mode.py) | the RGB camera's register table, from the camera module in the stock vendor image: the file the driver loads |
| [recovery_image.py](../kernel/recovery_image.py) | boot images with Valve's kernel ([A one-shot test boot](#a-one-shot-test-boot)) |

`recovery_image.py KERNEL_OUT STOCK_RECOVERY [DEV_RECOVERY] OUT` builds on the dev image's recovery image:

| Option | Effect |
| --- | --- |
| (none) | a test image of the maintenance stage 1 on Valve's kernel, as a whole recovery partition |
| `--stage1 DIR`, `--gpu-firmware DIR`, `--wlan-firmware DIR` | this tree's stage-1 files, the GPU's and the QCA6390's firmware |
| `--direct` | no ramdisk: the kernel starts the installed system from `super` ([install.md](install.md#how-it-starts)); in `recovery` a one-shot test whose failed start reboots plainly (`pico4.failover=reboot`) |
| `--direct --boot` | the boot partition's image (what `dev boot-image` makes, `out/kernel/valve-boot.img`) |
| `--cmdline WORD` | an extra kernel word, replacing one with its key (repeatable) |
| `--debug`, `--trace FUNCTION` | for failed boots ([A one-shot test boot](#a-one-shot-test-boot)) |

`OUT.json` records the hash and build information (`{"kernel": VERSION, "boot": "direct"}` with `--direct`); `dev
check` takes the record whose kernel release runs.

## Results

| Boot and SoC | Result on the headset |
| --- | --- |
| Boot | Valve's kernel boots stage 1, SSH over USB NCM 13 s after the reboot. Without a ramdisk, `root=PARTLABEL=super` mounts the system image read-only 1.43 s after the kernel starts |
| Storage | UFS with all six LUNs and their partitions; every block device read-only |
| USB NCM | Windows 11's in-box driver works without the NCM initialization the stock kernel needs |
| SoC | 8 CPUs, 7.4 GB RAM; `cpufreq-hw` (three policies, up to 2.84 GHz); TSENS zones; RPMh regulators at the stock voltages; SPMI, PMIC RTC, watchdog, `socinfo` |
| Reboots | `pico4-reboot bootloader` reaches fastboot. The firmware refuses the warm reset a panic asks for (`reboot=panic_warm`; `SYSTEM_RESET2` answers -2); the tree's PS_HOLD reset restarts the headset, about 40 s from a forced panic to SSH, with the RAM console kept. PSCI's ordinary reset (the kernel commit “firmware: psci: a refused reset ends in a cold reset”) stands behind it, not yet needed |

| Display and GPU | Result on the headset |
| --- | --- |
| Display | DPU, both DSI controllers and PHYs and the panel driver bind as one DSI connector; the driver's DSC configuration equals the stock power-on sequence's byte for byte; the full power-on (supplies, VIO, reset, 20 stock commands on both links, backlight) completes; 4320x2160 at 90 Hz, vblanks at 90.00 Hz. At a boot's first power-on the panels read power mode 0x0c in prepare and 0x1c after the first light; a wake takes 0x1c as working |
| GPU | Adreno 650 (chip id 0x06050002); microcode, GMU firmware (v2.1.8) and PICO's zap shader load; devfreq 305–587 MHz (the chip's speed bin; PICO OS's VR level) with the stock DDR vote per level; one DRM device with the display, as on the Frame |
| Valve's Mesa | `deckard-mesa-linux-aarch64` 26.3.0-devel: Turnip on msm (Vulkan 1.3, conformance 1.2.7.1) renders `pico4-vulkan-test`'s frame with all 65536 pixels correct; OpenGL 4.6 core and compatibility and OpenGL ES 3.2 through zink, surfaceless and on GBM. No freedreno gallium driver: on the Frame, OpenGL is zink |
| In the lenses | a KMS test frame and vkcube (compressed scanout) on both panels; Valve's session in SteamVR's direct mode, worn: fluid, like stock. The LCDB stays on throughout |
| Wake | a wake resends the panels' power-on commands over the running video (the stock kernel sends them before video starts). The panels count 0 to about 15 link errors per wake, and a panel can take a command wrong (compression mode 0x34: lens corrupted; power mode 0x0c: lens dark). So a wake reads both panels while the lenses are dark, expects 0x1c and 0x01, and resends, three tries at most; `wake_test_sleep_in` forces a wrong reading. The link errors' cause is not known |

| MCU, DSPs, IMU and keys | Result on the headset |
| --- | --- |
| GD32 MCU | QUP0 SE1 as `ttyHS1` (its stock name); `pico4-fan status` reads duty, RPM, version ("BOOT02", as on the stock kernel) and fan info; its control lines as the stock serial driver sets them (boot 13 high, reset 12 low, sync 14 low) |
| DSPs | SLPI, CDSP and ADSP run under remoteproc with PICO's signed images (modem partition of PICO's public package); each brings up its IPC router, FastRPC (`/dev/fastrpc-sdsp`, `-cdsp`, `-cdsp-secure`, `-adsp`) and channels (`apr_audio_svc`, `cvp-glink-apps-dsp`, `cdsprmglink-apps-dsp`) |
| Head IMU | the SLPI's sensors process attaches to `hexagonrpcd` over `/dev/fastrpc-sdsp`, reads the registry from `persist` and lists the sensors over QRTR; `pico4-imu stream --rate 1000 --check` passes: TDK ICM-4x6xx accelerometer and gyroscope at 1023.89 Hz each, no gaps, monotonic, gravity 9.805 m/s², gyro bias 0.006 rad/s |
| Keys | PM8150's power key (`pm8941_pwrkey`) and RESIN key, the GPIO keys (volume, confirm, home, app) with the stock codes; `pico4-powerkey` runs |

| Installed system | Result on the headset |
| --- | --- |
| Boot | the installed system on Valve's kernel from `boot` (`dev install`, `recovery_image.py --direct --boot`); display and GPU bind without a ramdisk; `graphical.target` 9.7 to 11.0 s after the kernel starts ([install.md](install.md#verified-results)) |
| `dev check`, installed | passes: Valve's session with the headset and both controllers, 90.03 commits/s over 30 s, 0.0 % late, no stalls, hottest zone 65.5 °C |
| SteamVR | `gamescope-session.target` with `driver_monado`; `vrcompositor` in direct mode through Turnip's display WSI, 4320×2160 at 90 Hz, compressed scanout; 89.7 fps over 10 s with the CPUs' own governor (894 of 896 frames in one refresh), 5.9 ms compositor GPU time per frame, hottest zone 42 °C |
| Worn | fluid; Wi-Fi, Steam Store, game launches and Beat Saber work. From a boot, nothing pressed, the picture is there when the headset is picked up. Tracking, floor height, controllers and the colour room view work. Steam Link: picture, sound, tracking and controllers, and its end returns to the local session. Microphone at PICO OS's gain of 12: normal voice peaks at -12.4 dBFS, no clipping. Quitting games keeps the session |
| A worn game (Beat Saber through Proton and FEX, about 10 minutes) | the game submits a median 44.8 frames/s, half the panels' rate, the compositor presents 89.6/s; hottest zone 75.6 °C, GPU at 587 MHz, no tracker reset |
| Compositor beside Steam's panel | Steam's dashboard panel (1920x1080) costs the web helper about 60 jobs of 4.05 ms a second while it is in use or in view and for 50 s after a session start; the compositor's job grows from 5.8 to 7.4–9.3 ms. With the web helper rendering, GPU 96 % busy: 90.0 commits/s, 0–1 late of 3,600, worn no visible stutter. It takes four parts: preemption (the kernel commit “drm/msm/a6xx: ringbuffer preemption on the A650”), the Frame's kernel thread priorities (the submitting kworker real-time on cores 4–7), the big cores on `performance` (no frequency ramp per frame), and the stock path's pacing in `pico4-swapchain-usage` (four images, one frame ahead, 5 ms lead: a job starts 7.3 ms after a vblank and ends about 6 ms before its deadline). On one ring it reaches only 60–67 commits/s. The GPU stays at 587 MHz; a floor changes nothing |
| Preemption on the A650 | four rings, the compositor on the first, a client on the third; in 40 s of Steam's panel redrawing 7,000 preemptions, no faults or recoveries; a switch to the compositor's ring takes 0.25 ms median, at most 2 ms |
| Preempted jobs' pixels | 3,000 multisampled, blended, textured draws and a second pass, preempted about 5 times per job: the same hash in 60 of 60 frames as unpreempted, in GMEM, sysmem and Turnip's own choice |
| GPU faults under preemption | a client's protected register read, illegal packet or endless job: only that client's queue takes the fault; the compositor keeps its frame rate. A job in a CP wait cannot be preempted: the compositor stands still for 2 s until the hang check recovers, as on one ring |
| GPU page fault (`vkfault`, the first of a boot) | the job completes in 0.3 ms and reads 0; no recovery, no device lost, the compositor stays (the kernel commit “drm/msm: a GPU page fault does not stall the GPU, as on KGSL”). A real hang still recovers and stays with its process |
| Pose to panels | latency probe at 90 flips/s, one frame ahead, 5 ms lead: a pose reaches the vblank that shows it after 17–28 ms, 24 ms median; SteamVR adds 11 ms of its own, the driver predicts 17 ms (12 ms and the lead) |
| GPU bus vote | each GPU level votes its stock `bus-freq` on DDR (fixed; KGSL scaled between `bus-min` and `bus-max`). On the USB input with the battery full and the session idle, against every level at its `bus-min`: 0.8 % difference, within the 4 % spread. The fixed vote costs no measurable power |
| Fan | `pico4-fan` runs PICO OS's policy on the same 14 TSENS channels under mainline's names; 30 % at 30–53 °C |
| Heat | 10 minutes of Valve's session out of standby, head tracker on, no game, on a table: hottest zone 53 °C at 30 % fan, tracker at 40 % of a core, 88.7 fps, 6.9 ms GPU time |
| DSPs, head IMU | `pico4-slpi` and `pico4-cdsp` start the DSPs; `pico4-sensors.target` is reached once the IMU is listed, about 1 s after the sensors process starts (`hexagonrpcd` takes and drops its registry writes); the 1 kHz check passes |
| CDSP root listener | PICO OS's `cdsprpcd` in the tracker's root ([xr.md](xr.md#head-tracking)) attaches to the CDSP's guest OS, maps the DSP's memory request (the kernel commit “misc: fastrpc: map a client's buffer through mmap”), opens its listener and event handles; no measurable CPU, the 1 kHz IMU check passes beside it |
| CVP | no driver: PICO OS's pipeline opens a CVP session but takes its depth from a network on the CDSP; stand-ins in the tracker's root answer the session |
| Head tracker | PICO OS's `libSlam` on the CDSP, fed through CAMSS. The compute context banks are coherent, as in the stock tree (`dma-coherent`; without it the DSP sees stale frames); the bridge answers `FASTRPC_CONTROL_KALLOC`. Still on a table, 60 s: 61,384 poses, none lost, within 0.4 mm and 0.03°, 47 % of a core. Worn it tracks |
| Still headset, 60 s, room view | tracker 0 poses lost, 0.61 mm and 0.05° from its start with the room view off, 0.54 mm and 0.05° with the colour room view and mesh on; `pico4-slamd` 56 % of a core; hottest zone 61 °C |
| Tracking cameras | the OV7251s' control port belongs to the station: the SoC powers and clocks the modules and receives their frames, as PICO's stock kernel does without reading their chip ID. `pico4-ov7251` (the kernel commit “media: i2c: add the PICO 4's OV7251 tracking camera driver”) is a sensor subdevice without an I2C client: the stock power sequence (standby, 18 ms, MCLK, 1 ms, reset, 18 ms; TITAN_TOP GDSC held), 640×481 Y10 on one lane at 480 Mbit/s. Slot 0: PHY 5, CSID 0; slot 1: PHY 3 lane 0, CSID 1; slot 2: PHY 3 lane 2 (lane 3 its clock), CSID 2; slot 3: PHY 4, CSID 3, each to a VFE RDI. the kernel commit “media: camss: the hardware start of frame as a buffer's time” dates each buffer with the CSID's hardware start of frame. `pico4-cam capture`: 1,500 frames per camera at 49.997 fps, none with an error, timestamps from the hardware and monotonic. Against the IMU, worn, up to 186°/s: within 1 ms, the method's resolution |
| RGB camera | `pico4-imx471` on CCI 0, four lanes of CSIPHY 1 at 2064.8 Mbit/s, CSID 4 and the third IFE-lite: 150 frames, 0 errors, 50.01 fps beside the tracking cameras. CAMSS never sets the camera NoC's clock (19.2 MHz): the tree assigns 300 MHz, else the sensor's 1.7 GB/s overflow the IFE-lite. A missing sensor fails only its stream |
| Colour room view on the depth mesh | both eyes in colour, the compositor on its RGB NV12 shader, 251 meshes in 10 s with 14,841 vertices, 64–72 ms after their pair. The scanout is black above the RGB camera's field of view; worn none of it shows |
| Controller station | QUP SE19's SPI as the stock board overlay describes it, a spidev at 6 MHz (`/dev/spidev0.0`), its supply L5C as `station-power` (`/sys/devices/platform/station-power/state`) in place of the vendor power ioctl. The station announces itself 24 ms after a power cycle and answers the version and wake-up queries; a controller connects |
| Controller tracker | both controllers on a table, motors running: in a replay of 1,500 frames per camera both acquired within 5.4 s and stayed tracked, 16–18 LEDs per frame, 0.66 px median residual, spread about 2 mm; in Valve's session both reach SteamVR; worn both follow the hands |
| Video codec | Venus (the kernel commit “media: venus: build on its own again”; Valve's iris knows only SM8550 and is left out, since Venus leaves SM8250 to iris whenever iris is built) with PICO's `venus.mdt` in the stock carve-out; `pico4-venus-probe` probes it once the firmware is in `/usr/lib/firmware` (the kernel's own probe comes before the root file system and fails for good). `hevc_v4l2m2m` and `h264_v4l2m2m` decode 60 frames of 1080p60, `vp9_v4l2m2m` and `vp8_v4l2m2m` 60 frames of 720p30 |
| Battery | `pico4-pm8150b-battery` (the kernel commit “power: supply: add the PICO 4's PM8150B battery driver”) reads PM8150B as the stock kernel reports it: the fuel gauge's monotonic state of charge (`0x4009`, 255 at full, scaled as the stock driver does), the charger's state (`0x1006`) and USB input (`0x1310`), as the `battery` and `usb` supplies, every 30 s. `temp` is word 328 of the gauge's memory (10 bits, signed, 0.25 °C), read every 30 s through the direct window (`0x4858`) as the stock driver does on PM8150B; `health` is the charger's thermistor view (`0x100d`). At probe it writes the stock temperature window (`qcom,jeita-hard-thresholds`, `qcom,jeita-soft-thresholds`) to `0x1094`–`0x109b`, reads it back, and turns the charger's soft-zone compensation off. Each poll it does the stock soft-zone handling (`smblib_soft_jeita_arb_wa`) and software JEITA's float voltage (`qcom,jeita-fv-ranges`), capped by `pico,float-voltage-max-uv` (the boot loader's 4.35 V), each write read back; the charge current stays the boot loader's. On USB: 100 %, charging, as PICO OS reported; 24.0 °C on the table, matching the gauge's own whole-degree reading (`0x4288`); UPower shows battery and line power ([cooling.md](cooling.md#hardware-limits)) |
| Suspend | s2idle only; the RTC alarm, the power key and the GPIO keys wake. After a resume the session goes on: display, sensors, controllers and sound come back, and USB answers with the cable in. Steam can suspend again about 4 s after a wake; it kept the headset awake through 80 idle minutes in SteamVR's standby, so this is no plain idle timer. Drain is only known from charge rates on a 0.49 A port: +7.7 %/h over 3 h asleep, +3.9 %/h over the next 5.4 h, −14 %/h awake in standby, so 0.2–0.6 W or 1.1–1.4 W asleep (unresolved) and about 5 W awake; a drain on battery is not measured |
| Backlight in standby | SteamVR's standby keeps the panels on with black frames, so `driver_monado` writes the `pico4-panel` backlight's `bl_power` (the panels' enable line, GPIO 29) on `VREvent_EnterStandbyMode` and `VREvent_LeaveStandbyMode`; raising it, the driver resends the panels' power-on commands over the running video. A boot's panels stay dark after their first power-on until such a resend (why is not known), so the driver resends once 300 ms after it: the boot indication shows from about 5 s after the kernel starts. The GD32's `SetBrightness` is acked but changes nothing. Worn: dark without glow in standby, back at once without flicker |
| Wi-Fi after a reboot | a saved network reconnects by itself within a minute. A DHCP attempt can run its 45 s without a lease; NetworkManager then retries 0.1 s later and connects (iwd's own autoconnect stays offline) |
| Not implemented | speaker protection (voltage and current sense); the charger's configuration (it runs as the boot loader set it up) |

## Display

The stock tree drives both panels as one display: its panel node (Sharp LS026B3SA, 90 Hz video mode) names both DSI
controllers and PHYs, 2160x2160 per link, with DSC (four 540x16 slices per line, 8 bits per component, 8 bits per
pixel, two slices per packet). The Frame does the same (`sm8650-mp.dts`): one panel node on the first, master DSI
link, a second port to the second link, and a mode twice as wide, which msm splits.

The PICO 4 follows that model with its own panel driver ([`panel-pico4-ls026b3sa.c`](https://github.com/overpressure/linux/blob/phoenix/7.2/drivers/gpu/drm/panel/panel-pico4-ls026b3sa.c)):
4320x2160 at 90 Hz from the stock timings, VDDIO from L7A, reset (GPIO 75, stock 20/20/30 ms), VIO enable (GPIO 133)
and backlight enable (GPIO 29).

- The bootloader leaves VIO an input, so each line becomes an output on its first use; a value set on an input pin
  does nothing.
- The ±5.8 V bias comes from PM8150L's LCDB, which mainline has no driver for; it stays as the bootloader set it.
- GPIO 71 switches a 5.5 V supply with no user; it is held off, as on the stock kernel.
- L10C (3.0 V) supplies the GD32 MCU, which drives the backlight; it is always on.
- L7A: the stock range is 1.8–1.9 V and the stock driver asks for 1.9 V, which is no RPMh step. 1.904 V stalls CPU 0
  (hard lockup); the tree uses 1.896 V. One invalid regulator node fails the whole PMIC's set.
- The panels' command sequences come from the stock board overlay, as `pico4,on-command` and `pico4,off-command`
  in the tree's `sm8250-pico4-phoenix-panel.dtsi`; the driver sends each with its stock data type to both links.

The RGB camera's mode is PICO's data too, and not part of the kernel: `imx471_mode.py` takes it from
`/lib64/camera/com.qti.sensormodule.sunny_imx471.bin` in the stock vendor image: the `regSetting` list of 2328x1748 at
50 fps (frame length 4520, line length 2560) in the file's order, without exposure and gain (0x0202–0x0205), which the
driver writes as controls. The tool accepts only the module files it knows by SHA-256 (the sunny and liteon modules of
firmware 5.13.7, with the same tables) and refuses an unexpected structure. The assets' sensor data hold it as
`camera/imx471-mode.bin`, the system image as `/usr/lib/firmware/qcom/sm8250/pico4/imx471-mode.bin`, and
the driver loads it at its first stream: `IMX471M1`, the number of writes (32-bit little endian), then each write as
its register (16-bit big endian) and its value.

## DSPs and the GD32 MCU

Mainline's `qcom_q6v5_pas` loads PICO's signed images (`slpi.mdt`, `cdsp.mdt`, `adsp.mdt` with their `.bNN` segments)
through TrustZone, as the stock PIL does: the same PAS IDs, the stock carve-outs (the ADSP and SPSS regions moved to
the stock layout), the CX vote and the AOP signal. The CDSP also needs the stock loader's bus vote while it starts
(`qnm_npu` to `qns_cdsp_mem_noc`), or TrustZone refuses ("failed to authenticate image and release reset"). The images
are in the userspace's `/usr/lib/firmware`; stage 1 has none.

The MCU (fan, lens motor, backlight) is on QUP0 SE1, which mainline describes only as I2C and SPI; the tree adds the
UART (GPIOs 6 and 7, as in the stock tree) as `ttyHS1`. Its reset (GPIO 12), boot (13) and sync (14) lines stay
unused, as on the stock kernel.

The head IMU (TDK ICM-4x6xx on the sensor DSP's bus) needs from the apps side what the stock XR driver
(`qcom,xr-stdalonevwr-misc`) provides: VDDIO (L8C, 1.8 V) and VDD (L11C, 3.008 V), always on, and CLKIN from PM8150's
GPIO 3 in its first special function, the GPIO controller's default state. Without the clock it takes its
configuration but sends no samples. The sensors process also needs `CONFIG_QRTR_SMD`, the registry in `persist`, and
the downstream socinfo attributes mainline's `/sys/devices/soc0` lacks (`hw_platform` MTP, `platform_subtype` Unknown,
`platform_subtype_id` 0, `platform_version` 65536): `sensorspd` takes them from debugfs (`qcom_socinfo/`) and serves
them from a runtime copy of `hexagonrpcd`'s root.

## The installed system

| Part | How |
| --- | --- |
| Root | the kernel mounts `super` itself (`root=PARTLABEL=super`, read-only, `rootwait=20`) and starts preinit; no ramdisk. Without `super` it panics after 20 s, and `panic=5` reboots |
| Data partition | a partition counts its disk's read-only flag too: preinit makes the disk writable, its other partitions keep their own flag; the `/etc` overlay has no inodes index, so an update's new image is taken |
| Device nodes | devtmpfs, mounted on `/dev` by the kernel before preinit: without it systemd's device monitor ignores udev and no device starts a unit; udev's `devnode` helper stays for the block policy |
| DSPs | `/sys/class/remoteproc` (crash recovery restarts only that DSP), the kernel's name service and PD mapper |
| Sensor service | `/dev/fastrpc-sdsp`, socinfo from debugfs |
| Fan, throttling | `cpuN-thermal`, `cpuN-top/bottom-thermal`, `gpu-top/bottom-thermal` (the stock zones' TSENS channels); the stock trip points on mainline's zones ([below](#thermal-throttling)) |
| VR performance hint | PICO OS's frequency limits on the cpufreq policies, the big cores on `performance` at their limit; every core online ([cooling.md](cooling.md#vr-cpu-limits)) |
| GPU | msm's render node and devfreq governor; Turnip and msm provide direct mode and shared semaphores, as on the Frame; `pico4-kthread-priorities` with the Frame's values (Valve's real-time priority for the GPU's workqueues) |
| Panel probe | `pico4-panel-probe` writes `detect` to the connector's sysfs status on every boot ([SteamVR's direct mode](#steamvrs-direct-mode)) |
| Wi-Fi | ath11k on PCIe 0 (the QCA6390's power sequencer on the stock supplies), PICO's firmware and board data (`bdwlanq.elf`) under ath11k's names; `pico4-wlan-probe` binds it again once the image is mounted; the address from `wlan_mac.bin`; iwd keeps ath11k's interface (`/etc/iwd/main.conf`); the Frame's NetworkManager knows the band `no6` the Steam client asks for. The chip's own address changes each boot and the client saves it in its profiles, so `wlan-address` removes that line before NetworkManager starts |
| Bluetooth | `hci_qca` on QUP0's UART 6, the Frame's BlueZ; firmware and NV data from PICO's `bluetooth` partition, the address from `pico_bt_mac` (`pico4-bt-address`). `hci_qca` is built in and sets up before the image is mounted, so `pico4-bt-firmware` probes it again: the kernel offers bluetoothd a controller only after a successful setup |
| Audio | [userspace.md](userspace.md#audio) |
| Android games (Lepton) | the Frame's way: rootless podman (Arch's 5.6.2) with crun, overlay storage and the full ID map, the Frame's passt, `lepton-compat`, Valve's Android Mesa, the Android and Linux Vulkan layers and adb; binderfs in the session user's namespace; subordinate IDs 100000-165535 |
| Session | VT 1 in getty's place, as sddm on the Frame (logind requires a VT where the kernel has them) |
| systemd | 257.x, the Frame's 257.7 |
| PICO 4 driver | finds the headset by the remoteproc named `slpi` |

## Thermal throttling

The tree puts the stock kernel's throttling on mainline's thermal zones (step_wise):

| Sensor | Stock zone | From | Throttles |
| --- | --- | --- | --- |
| GPU (both) | `gpuss-max-step` | 95 °C | the GPU, every level (mainline: 85 °C) |
| DDR | `pop-mem-step` | 95 °C | the big cores and the prime core, every level |
| WLAN, video, DDR, Q6/HVX, camera, compute, NPU | their `-step` zones | 100 °C | the GPU to its second-lowest level, 400 MHz |

The stock tree also caps the CDSP, the modem and the NPU at 100 °C; they have no cooling devices on mainline. The CPUs
keep mainline's trips (90 and 95 °C, critical at 110 °C): the stock kernel isolates cores only at 110 °C and leaves the
rest to the CPUs' limits hardware, and 90 °C is the project's limit.

## SteamVR's direct mode

`vrcompositor` finds the panels through Vulkan (`VK_KHR_display`), acquires them with the DRM fd and paces itself with
`VK_EXT_display_control`, which Turnip's display WSI provides on msm. Three things make it reach 90 fps:

- **Compressed scanout.** The compositor asks for storage usage on its swapchain images and never uses it; the A650
  cannot compress storage images, and linear scanout costs about 11 ms of GPU time per frame (60–66 fps). The layer
  `pico4-swapchain-usage` removes the usage ([Valve's Mesa](#valves-mesa)), and the display scans out compressed
  XRGB8888 (`DRM_FORMAT_MOD_QCOM_COMPRESSED`).
- **The GPU's DDR vote.** Mainline gives the A650 no interconnect (msm's GMU bandwidth vote is for A7xx), so the DDR
  runs at what the CPUs ask for, and frames run late (7.8 instead of 5.9 ms on the GPU, 84.5 fps). The tree votes what
  the stock driver does: its LPDDR4X table (`ddr_device_type` 7) at each level's `bus-freq-ddr7`, 8,171,875 kB/s at
  587 MHz. The OPP core reads bandwidths only when the first OPP has one, so the unused 670 MHz OPP carries the top
  level too.
- **A probed connector.** Only the DRM master makes the kernel probe a connector's modes, and there is no fbdev client;
  a compositor that starts first sees "1024x768" and finds no headset display. The boot splash is the first master
  when the installed system starts; `pico4-panel-probe` writes `detect` to the connector's sysfs status on every boot,
  which fills its modes (4320x2160) without a master.

Every boot logs "DSI PLL(0) lock failed" and a warning that `dsi0_phy_pll_out_dsiclk` was already disabled, from
`dsi_phy_driver_probe`. It is harmless: the clock core enables the PHY's PLL for a reparent before the PHY's supplies
are on; the DSI driver locks the PLL itself when it enables the display.

With nobody wearing the headset, SteamVR enters standby after `power.turnOffScreensTimeout` (5 s) and presents black
frames; `pico4-valve-session-check` sets it to an hour with `vrcmd` and back afterwards. It counts frames from msm's own
events (`msm-frame-stats`: flips, vblanks, the compositor's GPU time), with the keys `session-performance` reads. A
10 s recording: 897 flips at 89.7/s, 3 late, vblanks at 90.0 Hz, 5.9 ms GPU time per frame (p90 6.0 ms). The scanout
is compressed and readable only by the DRM master, so the check has no image.

## Changes to Valve's tree

The project's commits on Valve's kernel are in [overpressure/linux](https://github.com/overpressure/linux), one branch
per layer, each taking its changes from the one below it; each commit says why it is there:

| Layer | What |
| --- | --- |
| `common/7.2` | fixes any headset on Valve's tree can meet: DDR information, the root's identity after the ABL's overlay, BCL, writebacks, Bluetooth's firmware wait, the DSPs' handover, FastRPC, the unaligned atomic fixup, GPU page faults, the DSP's PCM format, the deferred split shrinker, PSCI's reset, the WCD codec's interrupts; and the CI |
| `sm8250/7.2` | the SM8250's blocks: Venus, CAMSS (PHY settings, start of frame, the RGB camera's path, 16-bit samples), preemption on the A650, refresh changes on DPU 6.0 |
| `phoenix/7.2` | the PICO 4: its panel, battery, tracking and RGB camera drivers, audio as the stock kernel runs it, the device tree, `pico4.config` and its driver tests (`tools/testing/overpressure/phoenix/`) |

Every change moves up the layers by pull request, merged with a merge commit; nothing is rebased or force-pushed.
`valve/7.2` is Valve's own history: weekly, the Valve workflow moves it forward to the newest commit of Valve's public
`sm8650-7.2` branch through the [evlaV mirror](https://github.com/evlaV/deckard-linux-no-oopsies). Whenever a branch
moves, the Layers workflow opens a pull request from it into the layer built on it (`valve/7.2` into `common/7.2`, then
`common/7.2` into `sm8250/7.2`, then `sm8250/7.2` into `phoenix/7.2`); a pull request into a headset's branch is built
with its merge, and a conflict is resolved in the pull request. Rulesets let the layers change only by such pull
requests, with a passing build, and `valve/7.2` only move forward. A release is a tag `phoenix/7.2-N` that never moves;
this repository pins one.

## Valve's Mesa

The system's Mesa is Valve's `deckard-mesa-linux-aarch64`, unchanged, the same commit as the Android driver Lepton
uses: Vulkan through Turnip, OpenGL through zink, as on the Frame. It links two libraries outside Valve's set:
`libdisplay-info.so.1` (Turnip's display code), from the project's `overpressure-libdisplay-info`, and `libsensors.so.5`
(gallium's HUD), from Arch's `lm_sensors`.

`libVkLayer_PICO4_swapchain_usage` (`pico4-swapchain-usage`) removes the storage usage from `vrcompositor`'s
swapchains, so that it resolves into compressed scanout images. It also gives `vrcompositor` the stock path's frame
pacing, from `PICO4_FRAMES_AHEAD=1` and `PICO4_VSYNC_LEAD_US=5000` (`steamvr.service.d/50-pico4.conf`):

- four swapchain images: with SteamVR's two, its acquire waits for a flip, so no frame starts before a vblank;
- a present wait returns once the present before it was shown;
- the vblank counter ticks 5 ms early, with Valve's private query answering that tick's time: SteamVR's frame starts
  about 4 ms before the vblank it expects, so 7.3 ms after a real one, and has until the vblank after next. The layer
  publishes the lead in `$XDG_RUNTIME_DIR/pico4-display-lead-us`, and the driver predicts its poses that much further;
- a wait returns at a count SteamVR has not read, or it spins for a second
  ([session.md](session.md#performance-and-heat)); after waiting for its flip it also waits for the next tick, since a
  frame that started at once would be shown a refresh later and that queue would stay. SteamVR uses no display event
  fences on this path.

| SteamVR's compositor, headset on a table, out of standby | Frames per second | GPU time per frame |
| --- | --- | --- |
| Valve's Turnip as it is | 89.93 | 8.0 ms |
| Valve's whole Mesa with the layer, installed | 89.7 | 6.45 ms |

0.37–0.75 % of frames come late across session checks.

## Building it yourself

The installer downloads the pinned release. `kernel build` builds its commit instead, in the build image the release
names, and accepts the result only if every file equals the release's:

```sh
python -m overpressure --device phoenix kernel build   # the commit's tree (280 MB, no history), built, checked
python -m overpressure dev boot-image                   # out/kernel/valve-boot.img, the installed system's boot image
```

The same commit gives the same `Image.gz` in any work folder and on any host: `uname -v` names the commit and carries
its time (such as `#e05939d36466 SMP PREEMPT Thu Nov 12 08:35:14 UTC 2026`), `pahole` encodes BTF in one thread (its
threads order the type names differently each time), and `-ffile-prefix-map` maps the folders away in the debug
information, which the build ID hashes. Only overpressure/linux's CI builds: every tag twice on two machines, released
only when both agree. This repository's CI downloads the pinned release on every push and checks it against the pin.

## Contracts

Names and paths the kernel and this repository share, which no build checks: the RGB camera's table and the audio
calibration (where the driver loads them, where the image installs them, their format), the camera's size, exposure
limit and paused control, the CAMSS entities Monado links, the GPU workqueues `kthread-priorities` raises, the power
supplies and their attributes, the USB gadget functions, the panels' backlight, Venus's firmware name and the device
tree's name. `kernel contracts` reads the pinned commit's files and fails with both sides' values when one side
changed; CI runs it on every push.

## The ABL's board overlay

At boot the ABL lays a board overlay over the kernel's tree: from `dtbo`, or for recovery from the image's
`recovery_dtbo`. The PICO 4's is Qualcomm's "kona MTP" overlay (board-id 0x10008), 635 fragments through 222
downstream labels. Some labels (`spmi_bus`, `tlmm`, `soc`, `pdc`, `intc`) exist in mainline's tree too, where the
overlay would add downstream nodes to live ones. The tree is therefore built without dtc's symbol table and gets its
own from `sm8250-pico4-phoenix-dtbo-sink.dtsi`: every label of the stock overlays resolves to an empty node below
`/pico4-dtbo-sink`, which
has no compatible, so no driver sees what the overlay puts there. None of the overlays' nodes carries a compatible that
mainline searches the whole tree for (`ramoops`, `qcom,cmd-db`, `qcom,smem`, ...). `dtbo` stays untouched. The ABL
picks the tree by its `qcom,msm-id` and `qcom,board-id`.

## Kernel size

Valve's configuration builds a large kernel: 28.9 MB `Image.gz`, 153 MiB in memory with its BSS (the stock kernel:
35 MB), mostly a 16 MiB log buffer with its index tables and about 12 MB of BTF. The ABL checks the decompressed kernel
against its own region (UEFI's `KernelBaseAddr` and `KernelSize`): it loads the kernel at 0xA0080000 and the device
tree and ramdisk just below 0xB0000000, which leaves about 245 MB. Valve's configuration fits unchanged.

## A one-shot test boot

The test image goes to `recovery` (the whole partition with the stock vbmeta, as `dev image` builds it), never to
`boot`. With an empty bootloader message the ABL starts `boot`, the installed system; a reboot with reason `recovery`
starts the test kernel once. After a panic (`panic=5`), a stage-1 deadline or a hard reset, the next boot is the
installed system again. Afterwards `dev image`'s recovery image goes back to `recovery`. The default test image is
stage 1 on Valve's kernel; `--direct` makes one without a ramdisk that starts the installed system with the test
kernel, and its preinit reboots plainly after a failed start (`pico4.failover=reboot`), since a reboot into `recovery`
would start the test image again.

The ABL passes only the boot image header's first command line field (512 bytes with its terminator).
`recovery_image.py` leaves out the base image's words for Android and the downstream kernel, which stage 1 does not
read, and refuses a longer line.

The new kernel prints nowhere on its first boots (no serial port; USB only once the gadget runs). Its RAM console has
the stock layout (ramoops at 0xB0000000: 2 MiB, 1 MiB console, 1 MiB pmsg, no dump records, no ECC), so after the
reboot the installed system or stage 1 reads the failed boot's log from `/sys/fs/pstore/console-ramoops-0`. Only a
panic's reset keeps it: stage 1's return to fastboot corrupts it (stage 1's own log in `pmsg-ramoops-0` survives with
bit errors). For a boot that stalls, `--debug` logs every driver probe, prints every task's stack and every CPU's
backtrace on the panic, and makes stage 1 panic where it would return to fastboot; `--trace FUNCTION` records a
function's call graph and prints it on the panic. The hung-task detector skips killable and idle waits, and this
preemptible kernel reports no soft lockup for a busy loop, so a stalled probe shows only as `deferred_probe_initcall`
waiting.

## Sources

- The stock kernel's configuration: embedded in the PICO OS 5.13.8 boot image (`IKCONFIG`), read with
  `python -m overpressure inspect`.
- The Frame's kernel: `linux-72-deckard` 7.2.0 from Valve's hotfix repository
  (`archlinux-deckard-hotfixes/release/0.3.x`), configuration embedded in its image.
- The PICO 4's hardware: the stock device tree and `dtbo`.
