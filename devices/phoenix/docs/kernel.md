# Valve's kernel

The installed system runs Valve's kernel from the Steam Frame, `linux-valve` 7.2.0, with the PICO 4's
own patches, drivers and device tree from [kernel/](../kernel/); `boot` carries it. Valve's tree is
close to mainline and builds mainline's SM8250 (XR2) device trees. The maintenance stage 1 in
`recovery` stays on the stock Android kernel. This document holds the kernel's files, what is verified
on it and how it works.

## The two kernels

| | PICO 4 (stock) | Steam Frame |
| --- | --- | --- |
| Kernel | 4.19.81-perf+ (PICO OS 5.13.8), clang 8.0.12 (Android NDK) | 7.2.0 (`linux-72-deckard`, Valve's hotfix repository), GCC 15.1 |
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

## Files

The kernel's files are in [kernel/](../kernel/):

| File | Purpose |
| --- | --- |
| [sm8250-pico4-phoenix.dts](../kernel/sm8250-pico4-phoenix.dts) | the PICO 4's device tree on mainline's `sm8250.dtsi`: the stock tree's supplies for UFS, USB and the panels, its memory carve-outs, its unused GPIOs reserved, reboot modes, a RAM console, both DSI links with the panels, the GPU with its DDR vote, the GD32 MCU's UART, the three DSPs, the head IMU's supplies and clock, the keys |
| [pico4.config](../kernel/pico4.config) | merged over Valve's `gabe_defconfig`: the SM8250 drivers (with the display and GPU clock controllers, QFPROM, REFGEN), the panel driver, stage 1's USB gadget functions, the PON reboot modes, pstore, QRTR over GLINK |
| [patches/](../kernel/patches/) | fixes to Valve's tree that the PICO 4 needs (below) |
| [src/](../kernel/src/) | the PICO 4's own kernel sources, at their paths in the tree: the panel driver, the battery driver, the `pico4-ov7251` and `pico4-imx471` sensor drivers, the q6 calibration driver |
| [build-kernel.sh](../kernel/build-kernel.sh) | Valve's 7.2 source package (pinned by hash and commit), the patches and the own sources to `Image.gz` and the device tree |
| [dtbo_sink.py](../kernel/dtbo_sink.py) | the include that absorbs the ABL's board overlay (below) |
| [panel_sequence.py](../kernel/panel_sequence.py) | the include with the panels' command sequences, from the stock board overlay (below) |
| [recovery_image.py](../kernel/recovery_image.py) | a test image: the maintenance stage 1 on Valve's kernel as a whole recovery partition; `--installed` starts the installed system instead, with `--boot` as the boot partition's image, `--stage1` carries this tree's stage-1 files; stage 1's build information names the new kernel and `OUT.json` records it, so that `dev check` accepts the image's stage 1; `--gpu-firmware` for the GPU, `--wlan-firmware` for the QCA6390; `--cmdline` for extra kernel words (such as the panel driver's parameters); `--debug` and `--trace` for failed boots (below) |

## Results

| Boot and SoC | Result on the headset |
| --- | --- |
| Boot | Valve's 7.2 kernel boots stage 1; SSH over USB NCM answers 13 s after the reboot |
| Storage | UFS with all six LUNs and their partitions; stage 1 switches every block device read-only |
| SoC | 8 CPUs, 7.4 GB RAM; CPU frequencies through `cpufreq-hw` (three policies, up to 2.84 GHz); TSENS thermal zones; RPMh regulators at the stock voltages; SPMI, PMIC RTC, watchdog, `socinfo` |
| Reboots | `pico4-reboot bootloader` reaches fastboot; the firmware refuses the warm reset a panic asks for (`reboot=panic_warm`, from the stock command line; `SYSTEM_RESET2` answers -2). The tree's PS_HOLD reset, as on the stock kernel, then restarts the headset, about 40 s from a forced panic to SSH, with the RAM console kept. PSCI's ordinary reset (patch 0031) stands behind it and has not been needed |

| Display and GPU | Result on the headset |
| --- | --- |
| Display | the display subsystem, DPU, both DSI controllers and PHYs and the panel driver bind as one DSI connector (`msm-kms`); the driver's DSC configuration equals the parameter set in the panels' stock power-on sequence byte for byte; the panels' full power-on (supplies, VIO, reset, the 20 stock commands on both links, backlight) completes; a mode set of 4320x2160 at 90 Hz follows, with vblanks at 90.00 Hz. At a boot's first power-on the panels read power mode 0x0c in prepare (display on and normal mode, the sleep-out bit clear) and 0x1c after the first light (sleep out and display on); at a wake the driver takes 0x1c as working |
| GPU | Adreno 650 (chip id 0x06050002), the microcode, the GMU firmware (v2.1.8) and PICO's zap shader load, the ring runs; devfreq from 305 MHz to 587 MHz (the chip's speed bin; PICO OS's VR level) with the stock DDR vote per level; one DRM device with the display, as on the Frame |
| Valve's Mesa | the system's Mesa, the Frame's `deckard-mesa-linux-aarch64` 26.3.0-devel: Turnip on msm ("Turnip Adreno (TM) 650", Vulkan 1.3, conformance 1.2.7.1) renders `pico4-vulkan-test`'s frame with all 65536 pixels correct; OpenGL 4.6 core and compatibility and OpenGL ES 3.2 through zink on Turnip, surfaceless and on GBM. The package has no freedreno gallium driver: on the Frame, OpenGL is zink. |
| In the lenses | a KMS test frame and vkcube (compressed scanout) show on both panels; Valve's session in SteamVR's direct mode, seen worn: fluid, "just like stock" (user). The LCDB stays on throughout |
| Wake | a wake resends the panels' power-on commands over the running video, which the stock kernel does not (it sends them before video starts). The panels count link errors on them, 0 to about 15 per wake, and a panel can take a command wrong: in 30 worn wakes one panel once read compression mode 0x34 (lens corrupted) and once power mode 0x0c (lens dark). So a wake reads both panels while the lenses are dark, expecting 0x1c and 0x01, and resends the commands, three tries at most; `wake_test_sleep_in` forces a wrong reading to test it. The stock DSI scheduler brings no gain: the hardware already sends in the blanking. The cause of the link errors is not known |

| MCU, DSPs, IMU and keys | Result on the headset |
| --- | --- |
| GD32 MCU | QUP0's serial engine 1 as `ttyHS1` (its stock name); `pico4-fan status` reads duty, RPM, version ("BOOT02", as on the stock kernel) and fan info; the MCU's reset and boot lines stay as the bootloader set them, as on the stock kernel |
| DSPs | the SLPI, CDSP and ADSP run under mainline's remoteproc with PICO's signed images from the headset's modem partition; each brings up its IPC router, FastRPC (`/dev/fastrpc-sdsp`, `-cdsp`, `-cdsp-secure`, `-adsp`) and its own channels (the ADSP's `apr_audio_svc`, the CDSP's `cvp-glink-apps-dsp` and `cdsprmglink-apps-dsp`) |
| Head IMU | the sensors process on the SLPI attaches to `hexagonrpcd` over upstream FastRPC (`/dev/fastrpc-sdsp`), reads the headset's registry from `persist` and lists the sensors over QRTR (the kernel's own name service and PD mapper); `pico4-imu stream --rate 1000 --check` passes: accelerometer and gyroscope (TDK ICM-4x6xx) at 1023.89 Hz each over 10 s, no gaps, no out-of-order timestamps, gravity 9.805 m/s², gyro bias 0.006 rad/s |
| Keys | the stock tree's keys: PM8150's power key (`pm8941_pwrkey`) and RESIN key, and the GPIO keys (volume, confirm, home, app) with the stock codes; `pico4-powerkey` runs |

| Installed system | Result on the headset |
| --- | --- |
| Boot | the installed system (`super`, `userdata`) on Valve's kernel from `boot` (`dev install`, the image from `recovery_image.py --installed --boot`), the maintenance stage 1 on the stock kernel in `recovery`: `graphical.target` 13.2 s after power-on (8.8 s kernel, 4.4 s userspace); the data partition and its disk writable, every other block device read-only; the `/etc` overlay without the inodes index, so that an update's new image is taken |
| RGB camera | `pico4-imx471` on CCI 0, four lanes of CSIPHY 1 at 2064.8 Mbit/s, CSID 4 and the third IFE-lite: 150 frames, 0 errors, 50.01 fps beside the tracking cameras. CAMSS never sets the camera NoC's clock, which stays at 19.2 MHz: the tree assigns 300 MHz, since the sensor's lines come at 1.7 GB/s and overflow the IFE-lite otherwise. The sensor powers up with its stream: a missing one fails only that stream |
| Colour room view on the depth mesh | scanned-out image with the headset lying still: both eyes in colour, the compositor on its RGB NV12 shader, 251 meshes in 10 s with 14,841 vertices, 64–72 ms after their pair. The scanout is black above the RGB camera's field of view; worn none of that shows, also looking up |
| Still headset, 60 s | tracker 0 poses lost, 0.61 mm and 0.05° from its start with the room view off, 0.54 mm and 0.05° with the colour room view and the mesh on; `pico4-slamd` 56 % of one core; hottest zone 61 °C |
| Compositor beside Steam's panel | no panel: 5.8 ms GPU per job. With Steam signed in, its dashboard panel (1920x1080, the Frame's size) in view: 7.4-9.3 ms by how much of the view it fills. The web helper renders it at about 60 jobs of 4.05 ms a second whenever the panel is in use or in view, and for the first 50 s after a session start. With all of the below, the web helper rendering, 96 % of the GPU: 90.0 commits/s, 0-1 late of 3,600, worn no visible stutter (user). It takes four things. On one ring: 60-67 commits/s, 40-45 % late, the compositor waiting up to 6.9 ms for the GPU. With preemption: p99 1.1 ms, 81-82 commits/s; the job still reaches the GPU through a kworker at nice 0, mostly on the little cores, p99 2.5 ms, maximum 6.3 ms. With the Frame's kernel thread priorities: that kworker real-time on cores 4-7, p99 0.33 ms, 84.5 commits/s. With the big cores on `performance`, no frequency ramp in each frame: p99 0.12 ms, 89.3-90 commits/s; worn this still fails: with the panel filling the view a job ends a few tenths of a millisecond past the last moment for the next vblank, 9-17 % of the vblanks get no frame, at any head speed, and the view overshoots and springs back on fast turns (without a web helper job 0-0.6 %, at up to 396 deg/s). With the stock path's pacing in `pico4-swapchain-usage` (four images, one frame ahead, a lead of 5 ms) a job starts 7.3 ms after a vblank and ends about 6 ms before the last moment. The GPU stays at 587 MHz throughout, a floor changes nothing, and neither a preemption nor a new panel image lengthens a job against its neighbours |
| Worn (user) | Fluid, Wi-Fi, Steam Store, game launches and Beat Saber work. From a boot, nothing pressed, the picture is there when the headset is picked up, repeatedly. Tracking, floor height, controllers and the colour room view work. Steam Link: picture, sound, tracking and controllers, and its end returns to the local session. Microphone at PICO OS's gain of 12: normal voice peaks at -12.4 dBFS, no clipping. Quitting games keeps the session |
| A worn game (Beat Saber through Proton and FEX, about 10 minutes) | the game submits a median 44.8 frames/s, half the panels' rate, while the compositor presents 89.6/s; hottest zone 75.6 °C, GPU at 587 MHz, no tracker reset |
| GPU page fault (`vkfault`, the first of a boot) | the job completes in 0.3 ms and reads 0, no recovery, no device lost, the compositor stays. A real hang still recovers and stays with its process |
| Wi-Fi after a reboot | a saved network reconnects by itself within a minute of boot, without input. A boot can find no DHCP lease within 45 s. With DHCP's replies dropped through such an attempt, NetworkManager retries 0.1 s after it fails and connects by itself; left to iwd's autoconnect, it stayed offline until it was connected by hand. A second attempt may also run its 45 s without a lease with DHCP free; the third then connects within a second |
| Preemption on the A650 | four rings, the compositor on the first, a client at the default priority on the third. In 40 s of Steam's panel redrawing: 7,000 preemptions, no faults or recoveries; each switch to the compositor's ring takes 0.25 ms (median), at most 2 ms |
| Preempted jobs' pixels | a Vulkan job of 3,000 multisampled, blended, textured draws and a second pass reading the first, each job preempted about 5 times by the compositor: the same hash in 60 of 60 frames as without a compositor, in GMEM, in sysmem and with Turnip's own choice |
| GPU faults under preemption | a client's protected register read or illegal packet on the third ring (CP error), and a job that never ends: only that client's queue takes the fault, the compositor keeps running and shows the same frame rate, and the pixels above stay the same. A job in a CP wait cannot be preempted: the compositor stands still for 2 s until the GPU's hang check recovers it, as it does on one ring |
| Pose to panels | latency probe, compressed scanout, 90 flips/s, one frame ahead, a lead of 5 ms, 11 steps: a handed-out pose reaches the vblank that shows it after 17-28 ms, 24 ms median; SteamVR adds 11 ms of its own, the driver predicts 17 ms: 12 ms and the lead |
| `dev check`, installed | on the installed system booting Valve's kernel from `boot`: passed, Valve's session with the headset and both controllers in SteamVR, 90.03 commits/s over 30 s, 0.0 % late, no stalls, hottest zone 65.5 °C |
| Fan | `pico4-fan` runs PICO OS's policy on the same 14 TSENS channels under mainline's names; 30 % at 30–53 °C |
| DSPs, head IMU | `pico4-slpi` and `pico4-cdsp` start the DSPs through remoteproc; `pico4-sensors.target` is reached once the IMU is listed (35 s); the 1 kHz stream check passes |
| SteamVR | Valve's session (`gamescope-session.target`) with `driver_monado` and the PICO 4 headset; `vrcompositor` in direct mode through Turnip's own display WSI on msm, 4320×2160 at 90 Hz, compressed scanout images; 89.7 frames per second in a steady 10 s window with the CPUs' own governor (894 of 896 frames in one refresh), 5.9 ms of the compositor's GPU time per frame, hottest zone 42 °C |
| Controller station | QUP SE19's SPI as the stock board overlay describes it, a plain spidev at 6 MHz as `/dev/spidev0.0`, its supply L5C as `station-power` (a regulator-output, `/sys/devices/platform/station-power/state`) in place of the vendor spidev's power ioctl. The station announces itself 24 ms after a power cycle and answers the version and wake-up queries; a controller connects. |
| Video codec | Venus (patch 0009 gives it back its own files: Valve's iris series leaves it uncompilable, and Valve's iris knows only SM8550; iris is left out, since Venus leaves SM8250 to iris whenever iris is built) with PICO's firmware (`venus.mdt` from the headset's modem partition) in the stock carve-out; `pico4-venus-probe` probes it once the firmware is in `/usr/lib/firmware` (the kernel's own probe comes before the root file system and fails for good). Measured: the decoder and encoder nodes (`qcom-venus-decoder`, `-encoder`); FFmpeg's `hevc_v4l2m2m` and `h264_v4l2m2m` decode 60 frames of 1080p60, and `vp9_v4l2m2m` and `vp8_v4l2m2m` 60 frames of 720p30 |
| GPU bus vote | each GPU level votes its stock `bus-freq` on DDR (fixed, where KGSL scaled between the level's `bus-min` and `bus-max`). Measured on the USB input (PM8150B's ADC, `usb_in_i_uv`) with the battery full and the session idle, the GPU between 305 and 587 MHz: three 25 s windows each, 82,054 against 81,419 µV mean with every level at its stock `bus-min`, a difference of 0.8 % within the windows' 4 % spread; at 587 MHz both are level 11. The fixed vote costs no measurable power |
| Battery | the PICO's own driver (`pico4-pm8150b-battery`, patch 0008) reads PM8150B's registers as the stock kernel reports them: the fuel gauge's monotonic state of charge (`0x4009`, 255 at full, scaled to percent as the stock driver does), the charger's state (`0x1006`) and the USB input (`0x1310`), as the `battery` and `usb` power supplies, checked for changes every 30 s. On USB: 100 %, charging, as PICO OS reported at the same time; UPower shows the battery and the line power. The charger and the fuel gauge run as PICO's boot loader set them up (input current limit register `0x1370` at 0x1e) |
| Tracking cameras | The OV7251s' control port belongs to the station, not the SoC: PICO's stock kernel accepts a tracking camera's probe without reading its chip ID (`cam_sensor_match_id`) and drops its register writes (`cam_sensor_i2c_modes_util`, for the `reserved` mark in the I2C info), so the SoC powers the modules, clocks them and receives their frames. `pico4-ov7251` (patch 0014) is a sensor subdevice without an I2C client: the stock power sequence while streaming (standby, 18 ms, MCLK, 1 ms, reset, 18 ms; TITAN_TOP GDSC held), 640×481 Y10 on one lane at 480 Mbit/s. Each camera has its own CSIPHY → CSID → VFE RDI: slot 0 PHY 5, CSID 0; slot 1 PHY 3 (lane 0), CSID 1; slot 2 PHY 3 (lane 2, lane 3 its clock), CSID 2; slot 3 PHY 4, CSID 3. Patch 0016 dates each buffer with the CSID's hardware start of frame. Measured with `pico4-station camera init` and `on`: all four at 49.96 fps at once, frames with their metadata line and the scene; `pico4-cam capture`: 1,500 frames per camera at 49.997 fps, none with an error, every timestamp from the hardware and monotonic. Frame times against the IMU, worn, 70 s of head motion up to 186°/s, from image motion fitted to the integrated gyro: within 1 ms on both cameras measured, the method's resolution; no stock-kernel figure from the same measurement. |
| Head tracker | PICO OS's `libSlam` on the CDSP through the FastRPC bridge, fed through CAMSS. The compute context banks are coherent, as in the stock device tree (`dma-coherent`; without it the DSP saw stale frames and lost tracking for moments); the bridge answers `FASTRPC_CONTROL_KALLOC`, so the library lets the kernel allocate. Headset still on a table, 60 s on the installed system: 61,384 poses, none lost, within 0.4 mm and 0.03° of the first, 47 % of one core. SteamVR gets the head's poses; worn it tracks |
| Controller tracker | Both controllers through the station and the sensor service, tracker as on the stock kernel. Lying on a table, about 50 cm in front of and 30 cm below the head, with their motors running: in a replay of 1,500 frames per camera both acquired 4.8 and 5.4 s in, then stayed tracked to the end, 16–18 LEDs per frame, 0.66 px median residual, spread about 2 mm under the motors; in Valve's session both reached SteamVR 56 cm in front of and 29 cm below the head; worn both follow the hands |
| Suspend | s2idle only (`/sys/power/mem_sleep`); the RTC alarm, the power key and the GPIO keys are wake sources. With the stock DSP processes running, the freezer stopped their FastRPC server and the DSPs lost their reverse calls (patch 0011). After a resume the session goes on: the display, sensors, controllers and sound come back, and USB answers with the cable in, without a replug. After a wake Steam can suspend again: seen about 4 s after an RTC wake from 3 h asleep, with the cable in. It left the headset awake through 80 idle minutes in SteamVR's standby, so this is no plain idle timer. The drain while suspended is only known from charge rates with the cable in on a 0.49 A port: the battery gained 7.7 %/h over 3 h asleep and 3.9 %/h over the next 5.4 h, and lost 14 %/h awake in SteamVR's standby. With the same input while asleep, that means 0.2 to 0.6 W or 1.1 to 1.4 W asleep and about 5 W awake; the two sleep figures are unresolved, and a drain on battery is not measured |
| CDSP root listener | PICO OS's `cdsprpcd` in the tracker's root through the FastRPC bridge ([xr.md](xr.md#head-tracking)) on upstream FastRPC: it attaches to the CDSP's guest OS, maps the DSP's memory request (patch 0012) and opens its listener and event handles; no measurable CPU, and the IMU's 1 kHz check passes beside it (1023.89 Hz, no gaps) |
| CVP | no driver: PICO OS's pipeline opens a CVP session but takes its depth from a network on the Compute DSP, and stand-ins in the tracker's root answer the session |
| Backlight in standby | SteamVR's standby keeps the panels on with black frames and its compositor holds the display, so a panel power-down through the display stack has no place here. The panel driver's enable line (GPIO 29) is also a backlight device, `pico4-panel`; `driver_monado` writes its `bl_power` on `VREvent_EnterStandbyMode` and `VREvent_LeaveStandbyMode`. The GD32's `SetBrightness` is acked at every level but changed neither the MCU's display report nor the input current. Raising the line alone leaves the lenses dark: the driver sends the panels' power-on commands again while it is low, over the running video. The same commands light a boot's panels, which stay dark after their first power-on until a resume or this (why is not known; the display's GPIOs, supplies and clocks read the same in both states), so the driver does this once, 300 ms after a boot's first power-on: the boot indication shows from about 5 s after the kernel starts. Seen worn: dark without glow in standby, back at once and without flicker, repeatedly |
| Heat | 10 minutes of Valve's session out of standby with the head tracker running and no game, on a table: hottest zone 53 °C at the fan's 30 %, the tracker at 40 % of a core, the compositor at 88.7 frames per second and 6.9 ms of GPU time |
| Not implemented | The speakers' protection (voltage and current sense), the charger's configuration (it runs as the boot loader set it up) |

## Display

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

## DSPs and the GD32 MCU

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

## The installed system

How the installed system runs on Valve's kernel:

| Part | How |
| --- | --- |
| Data partition | a partition counts its disk's read-only flag too: stage 1 makes the disk writable, its other partitions keep their own flag, and stage 2's block policy leaves that disk alone |
| DSPs | `/sys/class/remoteproc` (crash recovery restarts only that DSP), the kernel's name service and PD mapper |
| Sensor service | `/dev/fastrpc-sdsp`, socinfo from debugfs (above) |
| Fan | `cpuN-thermal`, `cpuN-top/bottom-thermal`, `gpu-top/bottom-thermal`: the stock zones' TSENS channels |
| Kernel throttling | the stock trip points on mainline's zones (below) |
| VR performance hint | PICO OS's frequency limits on the cpufreq policies, the big cores on `performance` at their limit; every core stays online |
| System V IPC | the kernel's own |
| OpenGL | zink, as on the Frame, whose Mesa has no other OpenGL driver |
| GPU clock floor, kernel thread priorities | msm's devfreq governor; `pico4-kthread-priorities` with the Frame's values, among them Valve's real-time priority for the GPU's workqueues |
| Device nodes | devtmpfs creates them, and stage 1 hands its devtmpfs over as it is: systemd's device monitor follows udev only when `/dev` is a devtmpfs or udev already runs, and without it no device starts a unit; udev's `devnode` helper stays for the block policy (every disk read-only) |
| systemd | pinned at 257.x: the Frame's own systemd is 257.7 |
| Monado's own service | `pico4-monado`, a development runtime with Vulkan's display in direct mode ([session.md](session.md#monado-as-a-service)) |
| Wi-Fi | ath11k on PCIe 0 (the QCA6390's power sequencer on the stock supplies), PICO's firmware and board data (`bdwlanq.elf`) under ath11k's names, the address from persist's `wlan_mac.bin` (`pico4-wlan-address`), iwd told to keep ath11k's interface (`/etc/iwd/main.conf`); the Frame's NetworkManager, which knows the band `no6` that the Steam client's Wi-Fi connections ask for. The chip's own address changes at each boot and the Steam client names it in the profiles it saves, so `wlan-address` takes that line out before NetworkManager starts |
| Bluetooth | `hci_qca` on QUP0's UART 6; firmware and NV data from PICO OS's `bluetooth` partition, mounted read-only (`pico4-bt-firmware`), the address from persist's `pico_bt_mac` (`pico4-bt-address`, the management interface's Set Public Address), the Frame's BlueZ. `hci_qca` is built in and sets the controller up before the partition is mounted; `pico4-bt-firmware` probes it again once the firmware is there, since the kernel offers a controller to bluetoothd only after a setup that succeeded |
| Android games (Lepton) | the Frame's way: rootless podman (Arch's 5.6.2: the Frame's 5.5.2 links gpgme 11), the Frame's passt, `lepton-compat`, Valve's Android Mesa, the Android and Linux Vulkan layers and adb (with protobuf 25 beside it); the session user gets subordinate IDs 100000-165535. binderfs mounts in the session user's namespace, podman runs rootless with crun, overlay storage and the full ID map |
| Audio | `pico4-adsp` boots the ADSP through remoteproc; `pico4-audiopd` starts PICO OS's audio process on upstream FastRPC (`INIT_CREATE_STATIC`, the stock remote heap given to the DSP's VMs); `pico4-lpass-macros` probes the VA macro again once that process runs (the AFE refuses its hardware vote before, and the driver has no bind file); the SM8250 machine driver's card "PICO4" with its own UCM profile (`conf.d/sm8250`) |
| Boot splash, panel probe | msm's primary plane; `pico4-panel-probe` probes the panels' connector on every boot (`detect` in its sysfs status), also where the splash does not run: msm leaves it unprobed until a DRM master asks, and SteamVR's compositor then finds no direct-mode display |
| Power key | `pm8941_pwrkey` |
| Session | the session takes VT 1 in getty's place, as sddm does on the Frame (logind requires a VT where the kernel has them) |
| GPU | msm's render node; Turnip and msm provide SteamVR's direct mode and shared semaphores, as on the Frame; `pico4-swapchain-usage` paces the compositor |
| PICO 4 driver | finds the headset by the remoteproc named `slpi` |

## Thermal throttling

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

## SteamVR's direct mode

`vrcompositor` finds the panels through Vulkan (`VK_KHR_display`), acquires them with the DRM
fd and paces itself with `VK_EXT_display_control`, which Turnip's display WSI provides on msm.
It asks for storage usage on its swapchain images and never uses it, and the A650 cannot compress
storage images (Turnip refuses the compressed modifier with storage usage); scanning out linear images,
the compositor needed about 11 ms of GPU time per frame, 60–66 frames per second. The display takes
compressed XRGB8888 (`DRM_FORMAT_MOD_QCOM_COMPRESSED`), and the layer `pico4-swapchain-usage` removes the
usage ([Valve's Mesa](#valves-mesa)).

The DDR is the other part. The compositor submits each frame as one GPU job right after the previous
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
no headset display (the kernel has no fbdev client that would probe). The boot splash in stage 1 is
the first master and probes; it shows its picture on msm's primary plane, across both panels. The
splash runs only when the installed system starts, so `pico4-panel-probe` probes on every boot:
writing `detect` to the connector's sysfs status
fills its modes (4320x2160) without a master.

With nobody wearing the headset, SteamVR enters standby after `power.turnOffScreensTimeout`
(5 s) and presents black frames; measurements set it to an hour with `vrcmd` first and back
afterwards, as `pico4-valve-session-check` does.

`pico4-valve-session-check` counts the frames from msm's own events (`msm-frame-stats`: completed
flips, vblanks and the GPU time of the compositor's jobs), with the keys `session-performance` reads. A 10 s
recording of SteamVR: 897 flips at 89.7 per second, 3 late, vblanks at 90.0 Hz, 5.9 ms GPU time
per frame (p90 6.0 ms). The scanned-out image is compressed and readable only by the DRM master,
so the check has no image of its own.

## Patches

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
| 0022 GPU page faults | with stall-on-fault the first fault of a boot stalls the GPU, hang detection fires about 80 ms later and the recovery loses the faulting context: a job of the compositor's that read an unmapped address as a game quit took the session down. PICO's KGSL stalls only if its fault policy asks for it, and its initial policy does not: it sets HUPCF, the read terminates and the job runs on. Stalling is now `msm.stall_on_fault=1`, for debugging; hangs still recover through the hangcheck |
| 0023 sound formats | the ADSP's 24-bit PCM has its samples in the top 24 bits of a 32-bit word, and q6asm-dai offered it as S24_LE, so that played samples arrived 48 dB low and recorded ones 48 dB hot. The front end now offers S32_LE |
| 0024 speakers' amplifiers and codec path | read from both kernels on the same headset during playback: mainline kept the WSA881x's gain in register mode where the stock kernel lets the compander set it over the amplifier's COMP port, and differed in the boost preset, its zero-cross control, the over-current hold and the macro's top, boost, compander and path configuration; the speaker gain offset of -1.5 dB was half applied. A fade stalled and then dropped; it now has the stock kernel's shape. The registers now match the stock kernel's, except the voltage and current sense, the temperature readings and the unused battery limiter. |
| 0025 DSP stream and port | what the stock kernel's q6 modules send, same headset and calibration: a stream's post-processing topology is the DSP's default where mainline asked for none; a port starts with its calibrated topology (`AFE_PARAM_ID_SET_TOPOLOGY`), which mainline never sent; every device path runs at 16 bits; periods have no step of 480 frames. |
| 0026 SoundWire frame | read from the stock kernel's WSA and TX masters during playback: a synchronization period of 6 frames (4 kHz at 50 rows, 16 columns, 19.2 Mbit/s) where mainline leaves the field at 0, and an offset of 0 where mainline wrote the tree's 0xff, which means none. The active frame control now reads the stock kernel's 0x5000f. |
| 0027 a decoder client's capture buffers | Steam Link's client asks for 16 capture buffers and has images for 16. Venus raised the count to the firmware's minimum, 18 for its stream, so that the client queued buffers without an image and stuck after the first frame. A client's 16 now stand; the firmware decodes with them ([session.md](session.md#steam-link)) |
| 0028 data before the headers | the firmware starts on a stream's headers: with other data first it holds every buffer and answers no flush. Steam Link's client restarts the decoder in the middle of a stream, and each stop took the driver's full second, until a key frame happened to come first. As a stateful decoder does, data before the headers now goes back to the client ([session.md](session.md#steam-link)) |
| 0029 the deferred split shrinker | with `cgroup.memory=nokmem` on the command line only a shrinker flagged `SHRINKER_NONSLAB` may be aware of memory cgroups, and the deferred split shrinker lacks the flag: it warned at a game's first huge page (id 0, a map of 0), and a cgroup's partly mapped huge pages were not split under memory pressure |
| 0030 a refresh change is a mode set on DPU 6.0 | Valve's seamless path for a refresh-only change, written for the Frame's DPU 10.0, leaves the XR2's DPU 6.0 at the old rate: the state says 72 Hz, vblanks stay 11.11 ms apart and the compositor judders. Without it the change is a full mode set, and the panels power-cycle on a switch |
| 0031 a refused reset ends in a cold reset | the firmware refuses the warm reset that the stock command line asks for on a panic, and the system halted until a long press. The patch logs the firmware's answer and, after the other restart handlers, tries the ordinary reset that every plain reboot uses |
| 0032 a failed recovery masks the interrupt | Venus's recovery enables the core's interrupt before HFI's init, and when that fails it powered the core down before disabling it. An interrupt in that window made the handler read registers of a core held in reset: an external abort in interrupt context, a panic. A failed attempt now masks it first. Taken from the abort's stack and the source; no firmware exception has run on it |
| 0033 H.264's end of stream | on this firmware (VIDEO.VPU.1.0-00093) a stop command on H.264 with the driver's 0xdeadb000 address takes a firmware exception; with a NULL address, which the driver otherwise uses only for firmware up to 1.0.87, the stream ends cleanly. HEVC, VP9 and VP8 end cleanly with the old address |
| 0034 the codec's interrupt loop | a cable replug during a suspend raises a codec alert at the wake, while the codec can't yet be resumed. `wcd_interrupt_callback` ignored its failed status reads and spun in the SoundWire interrupt thread: 100000 rounds until the interrupt was disabled, USB left `not attached`, and a hung task panic about 195 s later, with Steam waiting in `venc_close`. The loop now ends on a failed read |
| 0035 the codec's alert during sleep | a replug trips the codec's jack detect switch (`WCD938X_IRQ_MBHC_SW_DET`, status 0x10), awake or asleep, though no jack is fitted; why the cable reaches that switch is not known. During a system sleep the codec's runtime PM is disabled, so the codec's system resume now reads and handles the interrupt: no errors, USB back, no panic, sound passes `pico4-suspend-check`. The jack controls stay off and audio stays on the built-in speakers and microphone. The codec's SoundWire status then reads `Alert` until a status change; later interrupts still arrive with it |

Mainline sets RPMh regulators only to voltages on the regulator's own steps, and one invalid
regulator node fails the whole PMIC's set. The stock tree runs L7A (the panels' VDDIO) in
1.8–1.9 V and its panel driver asks for 1.9 V, which is no step (8 mV from 1.504 V): enabling the
next step up, 1.904 V, stalled CPU 0 (a hard lockup); 1.896 V, the closest step within the stock
range, works.

## Valve's Mesa

The Frame's Mesa (`deckard-mesa-linux-aarch64`) links two libraries outside Valve's set:
`libdisplay-info.so.1` (Turnip's display code), which the project's package `pico4-libdisplay-info`
builds, and `libsensors.so.5` (gallium's HUD), from Arch's `lm_sensors`.

The system's Mesa is Valve's, `deckard-mesa-linux-aarch64` unchanged, the same commit as the Android
driver Lepton uses: Vulkan through Turnip, OpenGL through zink on it, as on the Frame.
`libVkLayer_PICO4_swapchain_usage` removes the storage usage from `vrcompositor`'s
swapchains, which it asks for and never uses, so that it resolves straight into compressed scanout
images.

The same layer gives `vrcompositor` the stock path's frame pacing, from `PICO4_FRAMES_AHEAD=1` and
`PICO4_VSYNC_LEAD_US=5000`:
- four swapchain images. SteamVR asks for two, and then its acquire waits for a flip, so that no
  frame can start before a vblank, whatever else is set;
- a present wait returns once the present before it was shown;
- the vblank counter ticks 5 ms early, with Valve's private query answering that tick's time. SteamVR's
  frame starts about 4 ms before the vblank it expects, so 7.3 ms after a real one, and has until
  the vblank after next. The layer publishes the lead in `$XDG_RUNTIME_DIR/pico4-display-lead-us`,
  and the driver predicts its poses that much further;
- a wait returns at a count SteamVR has not read, or it spins for a second
  ([session.md](session.md#performance-and-heat)). When it had to wait for its flip, it also waits for the next
  tick: a frame that started at once would be shown a refresh later, and that queue would stay for good.
  SteamVR uses no display event fences on this path.

| SteamVR's compositor, headset on a table, out of standby | Frames per second | GPU time per frame |
| --- | --- | --- |
| Valve's Turnip as it is | 89.93 | 8.0 ms |
| Valve's whole Mesa with the layer, installed | 89.7 | 6.45 ms |

0.37–0.75 % of frames came late across session checks with the whole Mesa. OpenGL reports 4.6 through
zink on Turnip.

## Source

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

## The ABL's board overlay

Booting, the ABL lays a board overlay over the kernel's tree: from the `dtbo` partition, or for
recovery from the image's `recovery_dtbo`. The PICO 4's is Qualcomm's "kona MTP" overlay
(board-id 0x10008), 635 fragments addressed through 222 downstream labels. Several of these
labels (`spmi_bus`, `tlmm`, `soc`, `pdc`, `intc`) exist in mainline's tree too; applied there,
the overlay would add downstream PMIC, pinctrl and SoC nodes to live mainline nodes. The tree is
therefore built without dtc's symbol table and gets its own from `dtbo_sink.py`: every label of
the stock overlays resolves to an empty node below `/pico4-dtbo-sink`, which has no compatible,
so no driver sees what the overlay puts there. None of the overlays' nodes carries a compatible
that mainline searches for across the whole tree (`ramoops`, `qcom,cmd-db`, `qcom,smem`, ...).
The `dtbo` partition stays untouched. The ABL picks the tree by its Qualcomm IDs, which the tree
carries (`qcom,msm-id`, `qcom,board-id`).

## Kernel size

Valve's configuration builds a large kernel: 24.7 MB `Image.gz`, 141 MiB in memory with its BSS
(the stock kernel: 35 MB), mostly a 16 MiB log buffer with its index tables. The ABL checks the
decompressed kernel against a region of its own (from UEFI's `KernelBaseAddr` and `KernelSize`).
On the headset it loads the kernel at 0xA0080000 and the device tree and the ramdisk directly
below 0xB0000000, which leaves about 245 MB for the kernel; Valve's configuration fits
unchanged.

## A one-shot test boot

The test image goes to `recovery` (the whole partition with the stock vbmeta, as `dev image`
builds it), never to `boot`. With an empty bootloader message the ABL starts `boot`, the
installed system; a reboot with reason `recovery` starts the test kernel
once. Whatever happens then, a panic (`panic=5`), a stage-1 deadline or a hard reset, the next
boot is the installed system again. Afterwards `dev image`'s recovery image goes back to
`recovery`.

The ABL passes only the boot image header's first command line field (512 bytes with its
terminator) and drops the extra one. `recovery_image.py` therefore leaves out the base image's
words for Android and the downstream kernel, which stage 1 does not read, and refuses a longer
line.

The new kernel prints nowhere on its first boots (no serial port, USB only once the gadget
runs). Its RAM console has the stock kernel's layout (ramoops at 0xB0000000: 2 MiB, 1 MiB
console, 1 MiB pmsg, no dump records, no ECC), so after the reboot the installed system or the
maintenance stage 1 reads the failed boot's log from `/sys/fs/pstore/console-ramoops-0`. Only a
panic's reset keeps it: stage 1's return to fastboot leaves it corrupted (stage 1's own log in
`pmsg-ramoops-0` survives with bit errors). For a boot that stalls, `recovery_image.py --debug`
logs every driver probe before it starts, prints every task's stack and every CPU's backtrace on
the panic, and makes stage 1 panic where it would return to fastboot; `--trace FUNCTION` records a
function's call graph and prints it on the panic. The hung-task detector skips killable and idle
waits, and this preemptible kernel reports no soft lockup for a busy loop, so a stalled probe
shows up only as `deferred_probe_initcall` waiting.

## Sources

- The stock kernel's configuration: embedded in the PICO OS 5.13.8 boot image (`IKCONFIG`),
  read with `python -m overpressure inspect`.
- The Frame's kernel: `linux-72-deckard` 7.2.0 from Valve's hotfix repository
  (`archlinux-deckard-hotfixes/release/0.3.x`), configuration embedded in its image.
- The PICO 4's hardware: the stock device tree and `dtbo`.
