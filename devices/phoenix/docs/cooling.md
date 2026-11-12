# Cooling

| | |
| --- | --- |
| What | The fan, the thermal policy, the VR CPU limits and the hardware limits of the system on Valve's kernel, following PICO OS |
| How | The fan hangs off the GD32 MCU that also moves the lenses and drives the backlight ([xr.md](xr.md#lens-distortion-and-ipd)); `pico4-fan` sends it PICO OS's duty frames over `/dev/ttyHS1`, from PICO OS's policy on the CPU and GPU zones. The kernel throttles at the stock trips; `pico4-battery-guard` takes PICO OS's battery decisions |
| Code | [`pico4-fan`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/pico4-fan) and its unit, [`battery-guard`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/battery-guard) (overpressure/packages' `pico4-power`), [`vr-performance`](../userspace/rootfs/usr/libexec/pico4/vr-performance), the trips in [the device tree](https://github.com/overpressure/linux/blob/phoenix/7.2/arch/arm64/boot/dts/qcom/sm8250-pico4-phoenix.dts) |
| State | The MCU acknowledges duties and reports the fan's speed; Valve's 30 s session check peaks at 65.5 °C, about 10 minutes of a worn game at 75.6 °C ([kernel.md](kernel.md#results)). The limit for the hottest zone is 90 °C |

Without fan control the 30 s session check went from 47 °C to 86 °C. PICO OS's early table is the default; that it
holds 10 minutes of a rendering session below 80 °C rests on one run (peak zone 79.5 °C,
[Verified results](#verified-results)). Sessions much beyond 10 minutes are unmeasured.

## How PICO OS cools the PICO 4

The fan is an AVC blower, BAPA0405R5HY001 in the public part list of
[PICO-documentation](https://github.com/thoricelli/PICO-documentation/blob/main/devices/README.md). Its control path in
the stock system and vendor partitions:

```text
pxrfanservice                 /system/bin, init class core, root (pxrfanservice.rc)
  libpxrfanservice.so         FanControl: CPU and GPU zones -> duty
    libgd32ipdclient.pxr.so   SetFanSpeed(duty), over binder
      gd32ipdservice          libgd32ipdservice.so: A0 0E 00 duty 00 xor on /dev/ttyHS1
        GD32F330 MCU          fan PWM; also the lens motor and the backlight
```

PICO OS does not use:

- **The kernel's fan driver.** The stock kernel has a PICO-modified `pwm-fan` (`CONFIG_SENSORS_PWM_FAN=y`, hwmon
  `pwmfan`), but no stock DTB, overlay or the live tree has a `pwm-fan` node; only an unreferenced pinctrl state
  `fan_pwm_test_active` (gpio49) remains. The hwmon lines in `init.picovr.rc` and pxrfanservice's sysfs branch serve
  other products: pxrfanservice uses the MCU when `ro.picovr.product.name` is `Phoenix`, the PICO 4.
- **thermal-engine.** Qualcomm's daemon is commented out in `init_thermal-engine.rc`, and `thermal-engine.conf` is the
  empty default. The `*-usr` zones (`user_space` governor, trips at 115 and 125 °C) have no listener.

The stock tree's zones:

| Zone | Trip | Cooling |
| --- | --- | --- |
| `cpu-1-*-step` (gold and prime cores) | 75 °C, 5 °C hysteresis | the prime core's frequency, one step |
| `gpuss-max-step` | 95 °C | GPU frequency, down to its floor |
| `pop-mem-step` | 95 °C | CPU 4 and CPU 7 frequency |
| `cwlan`, `video`, `ddr`, `q6-hvx`, `camera`, `cmpss`, `npu` `-step` | 100 °C | CDSP, GPU, modem, NPU |
| `cpu-*-step` | 110 °C | CPU isolation |
| `apc-*-max-step` | 120 °C | none |
| `pm8150*_tz` | 95, 115, 145 °C | none (PMIC) |

So PICO OS cools with the fan and the kernel's throttling. Valve's kernel takes the GPU, DDR and 100 °C trips onto
mainline's zones and keeps mainline's CPU trips at 90 and 95 °C, critical at 110 °C
([kernel.md](kernel.md#thermal-throttling)).

### The fan protocol

Frames as `gd32ipdservice` builds them (requests `A0 cmd p1 p2 p3 xor`, replies `79 … xor`):

| Request | Reply | Command |
| --- | --- | --- |
| `A0 0E 00 dd 00 xx` | `79` | SetFanSpeed, `dd` = duty 0–100 %; the service refuses 101 and up |
| `A0 0F 00 00 00 AF` | `79 lo hi xx` | GetFanSpeed: the duty |
| `A0 10 00 00 00 B0` | `79 lo hi xx` | GetFanRPM: the tachometer |
| `A0 26 00 00 00 86` | 8 bytes | one of GetGD32MCUVersion's three queries |
| `A0 30 00 00 00 90` | `79 dd lo hi 00 00 00 rr 00 xx` | GetFanInfo (`fan_info_t`): duty `dd`, RPM `hi lo`, `rr` 1 while the fan runs |
| `A0 29 …` | | SetFanRPM, for the RPM tables below |
| `A0 05 00 lo hi xx` | `79` | SetBrightness, the backlight level; `wakeUp` and `ScreenOffForShutDown` send it too |

Each transaction votes the port's clocks (`ioctl 0x544D`), flushes both queues, writes the 6 bytes and polls up to ten
times at 1 ms for a reply starting with `79`.

**An MCU that answers `F1`.** `gd32ipdservice` wakes the MCU as the screen comes on (`wakeUp`): SetBrightness with
the configured level (`sys_brt_level` in `com.pvr.configuration`; 63 below 30 or without one), every 10 ms, up to 50
times, until it answers `79`; PICO OS's shutdown sets the backlight to 0 (`ScreenOffForShutDown`: `A0 05 00 00 00 A5`).
Its `ResetMCU` sets the boot line high, the reset line high for 10 ms, then wakes it. On one headset after a restore to
stock and PICO OS's first-run setup, the MCU answered every request, these included, with a single `F1`, through cold
starts and the reset: no fan control, no IPD. PICO OS updates the MCU on its first boot (`libpxrinit.so`,
`check_GD_MCU_Update`, from `/system/etc/mcubin/`), and an update that a suspend interrupts fails ("FW/OTA FAILED
BECAUSE OF SYSTEM SUSPEND"). Only PICO OS's update repairs it: a restore to stock, a first boot kept awake until the
setup is through, then the install from the running PICO OS.

### PICO OS's policy

`FanControl` in `libpxrfanservice.so` (symbols from its `.gnu_debugdata`):

- **Input.** On `kona`, 14 zones every 200 ms: `cpu-0-0…3-usr`, `cpu-1-0…7-usr`, `gpuss-0-usr` and `gpuss-1-usr`.
- **Mean.** Each zone keeps its last 50 readings (10 s), ignoring readings above 110 °C; the hottest zone mean decides.
- **Table.** Six thresholds with a duty from each threshold up:

  | Table | Thresholds (°C) | Duty (%) |
  | --- | --- | --- |
  | early | 90, 85, 70, 60, 55, 50 | 75, 60, 45, 30, 30, 30 |
  | late | 90, 85, 80.5, 72, 60, 55 | 50, 45, 40, 35, 30, 0 |

  On the way down a duty holds one more step: in the band below threshold *k*, a fan faster than duty *k* runs at
  duty *k−1*. Between the two lowest thresholds nothing changes.
- **Which table.** The production batch: the ninth character of the serial number in `persist/falcon/sn`, then a date
  code compared with `persist.pxr.fancontrol.limit1/limit2/serial1` (defaults 717, 726, D). On late headsets
  `persist.pxr.fancontrol.control.type` can select RPM control with SetFanRPM: type 2 uses the late table's RPM
  column (8,500, 7,800, 7,170, 6,540, 5,700, 0 rpm), type 3, the default with eye tracking (the Pro), 13 steps from
  3,700 rpm at 55 °C to 7,500 rpm at 90 °C. Without eye tracking the default is type 1, duty.
- **Sending.** A changed duty goes out at once, an unchanged one again after 10 s.
- **Overheating.** When the hottest mean stays at or above 95.1 °C for two minutes, PICO OS shows a notice in VR,
  again every 30 s.
- **A stopped fan.** No reaction to a silent MCU or a fan at 0 rpm; `sysmonitor-services` only records the RPM.
- **Overrides.** `/data/picovr/fancontrol_config.txt` replaces tables. While a factory test runs
  (`pvr.factorytest.running`), the service sets 0 % once and stops, unless `pvr.fan.forced` is set.

The development headset is a standard PICO 4 of the late batch: control type 1, the late table.

## pico4-fan

[`pico4-fan`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/pico4-fan) (Python, standard library) runs as
[`pico4-fan.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/pico4-fan.service):

- **The stock policy.** The same 14 zones, matched by type, every 200 ms; means over 50 readings; the same tables and
  hysteresis; an unchanged duty resent every 10 s. The overheating notice becomes a journal warning.
- **Full speed when hot** (not in PICO OS): while any of the 14 sensors reads 95 °C or more, the fan runs at 100 %
  until the hottest mean is below 90 °C, then steps down through the table. 95 °C is where the kernel throttles the GPU.
- **Failsafe.** Without usable readings the fan runs at 100 %. A failed transaction is retried every second and logged
  once per outage, then once a minute; a fan that reports 0 rpm at 30 % or more three times in a row is logged once.
- **A fan that does not cool** (not in PICO OS): at PICO OS's overheating point (hottest mean at or above 95.1 °C for
  two minutes, then every 30 s), a failed last transaction or a stalled fan logs the reason and runs
  `systemctl poweroff`. The kernel throttles meanwhile.
- **The MCU.** Frames come only from an allowlist: SetFanSpeed with a duty of 0–100, the four queries and PICO OS's
  wake (SetBrightness with level 63, `A0 05 00 3F 00 9A`). Nothing can build a lens move, calibration write, firmware
  frame or another backlight level, so nothing puts the MCU to sleep. An answer of only `F1` (asleep) is followed by
  PICO OS's wake, as `wakeUp` sends it, and the request once more; the journal says so. Each transaction holds an exclusive `flock` on
  `/dev/ttyHS1`; Monado's PICO 4 driver takes the same lock for its IPD read ([pico4_ipd.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/pico4_ipd.c))
  and uses Monado's default IPD after waiting a second for it.
- **The table.** PICO OS's early table on every headset by default: Valve's GPU-bound session heats the headset more
  than PICO OS's apps, and the early table runs the fan at least as fast at every temperature
  ([Verified results](#verified-results)). `PICO4_FAN_PROFILE=auto` picks the table by batch as PICO OS does, from
  `/mnt/vendor/persist/falcon/sn` ([userspace.md](userspace.md#the-headsets-own-data)), and uses early without it. The
  MCU cannot tell the batch: GetFanInfo reports only duty, RPM and whether the fan runs.
- **Stopping.** The fan keeps its last duty when the service stops.
- **Where it runs.** The unit's condition is `/sys/class/tty/ttyHS1`, present from kernel boot (udev creates the
  `/dev` node later).

Settings, in `/etc/pico4/fan.conf` (the service's `EnvironmentFile`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `PICO4_FAN_PROFILE` | `early` | `early`, `late`, or `auto` (by batch, as PICO OS) |
| `PICO4_FAN_TABLE` | | own table, `threshold °C:duty %` pairs hottest first, e.g. `90:50,85:45,80.5:40,72:35,60:30,55:0` |
| `PICO4_FAN_BOOST` | `95:90:100` | full speed from °C, until the mean is below °C, at duty %; or `off` |

The daemon writes its state to `/run/pico4-fan/state.json` every second: duty, reason, RPM, the hottest mean, and
every sensor's reading and mean.

## VR CPU limits

While a VR app runs, PICO OS limits the CPU through Qualcomm's VR service: `svrSetPerformanceLevels` in
`libsvrapi.so` hands the app's levels to `qvrservice`, whose configuration (`/system/etc/qvr/qvrservice_config.txt`,
`performance_perf_hints`) maps each of the nine level pairs to a power hint in the vendor's `powerhint.xml`,
`0x130A`–`0x1312`. All nine hold the same resources (perf HAL opcodes, decoded with `commonresourceconfigs.xml`):

| Resource | Value |
| --- | --- |
| `msm_performance` `cpu_min_freq` / `cpu_max_freq`, big cores (4–6) | 864 / 2016 MHz |
| the same, little cores (0–3) | 576 / 1804 MHz (their own maximum) |
| the same, prime core (7) | 941 / 2054 MHz |
| `core_ctl` `min_cpus` | 3 big cores, 1 prime core |
| KGSL `min_pwrlevel` / `max_pwrlevel` | 5 / 0 |

Without limits the big cores reach 2419 MHz and the prime core 2842 MHz.
[`vr-performance`](../userspace/rootfs/usr/libexec/pico4/vr-performance) applies the CPU part through the cpufreq
policies (every core stays online, which meets `core_ctl`'s minimum).
[`pico4-vr-performance.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-vr-performance.service) runs while
`pico4-session.service` or `pico4-monado.service` runs and restores the limits it found when it stops. It also keeps
the big cores, where the compositor runs, at their limit (governor `performance`), and restores the governor: without
it the compositor misses frames while Steam's panel redraws ([kernel.md](kernel.md#results)). In the session with the
panel idle, 3 minutes each, this costs about 0.6 °C (hottest zone mean 58.7 and 59.7 °C against 58.4 and 58.7 °C);
the little cores' load falls from about 45 to about 31 %. The GPU's clock stays with msm's devfreq governor.

## Hardware limits

What protects the hardware, against PICO OS's limits. The stock values come from the stock firmware: the live tree
(DTB 3 with board overlay 7, the same overlay as in `recovery.img`), `services.jar`, `framework-res.apk` and the vendor
partition. The battery values are those of the profile the stock kernel picks on the development headset by its
battery ID, `5742753_delta_fpr_50k_5300mah`; the 50, 80, 100 and 220 kΩ profiles share them, the 20 kΩ one is for
6070 mAh, with 4.48 V and 5.8 A. "Read" means taken from images and sources; none of these limits has been triggered
on the headset.

| Limit | PICO OS, and where from | Here | Verified |
| --- | --- | --- | --- |
| SoC overheat, shutdown | none: every trip in the live tree is `passive`; core isolation at 110 °C, `apc-*-max-step` at 120 °C without cooling | `critical` at 110 °C on the CPU, cluster and GPU zones (mainline's `sm8250.dtsi`): the kernel's `orderly_poweroff`, forced only if it cannot start, no timer (`THERMAL_EMERGENCY_POWEROFF_DELAY_MS=0`) | read |
| SoC overheat, throttling | GPU and DDR 95 °C, other sensors 100 °C | the same, and the CPUs at 90 and 95 °C ([kernel.md](kernel.md#thermal-throttling)) | read |
| PMIC overheat | `pm8150*_tz` 95, 115, 145 °C | `pm8150` and `pm8150b` zones 95, 115, `critical` 145 °C (mainline) | read |
| Overheating notice | hottest mean 95.1 °C for 2 minutes (`libpxrfanservice.so`) | journal warning at the same point | host tests |
| Fan stopped or MCU silent | no reaction | `pico4-fan`: power-off at the same point; throttling before it | host tests |
| Battery overheat | above 59.0 °C shutdown, from 58.0 °C notice (`BatteryService.shutdownIfOverTempLocked`, `ExtBatteryServiceImpl.checkBatteryStatus`; `persist.pxr.shutdown_overtemp`, default 1) | `pico4-battery-guard`: power-off above 59.0 °C, also while charging, a journal notice from 58.0 °C, on the driver's `temp` | host tests; the temperature read on the headset |
| Empty battery | below 1 % shutdown, also while charging (`ExtBatteryServiceImpl.shouldShutdownLocked`; `persist.pxr.shutdown_lowpower`, default 1) | `pico4-battery-guard`: power-off below 1 %, also while charging | host tests |
| Low battery, screen off | not charging, at 4 % or less: 60 s after the screen went off, or once the level fell since (`MIN_BATTERY_LEVEL_SHUTDOWN`, `checkLowPowerByScreenState`) | `pico4-battery-guard`, with the backlight's `bl_power` as the screen; not while the headset sleeps | host tests |
| Float voltage | 4.40 V from 0 to 45 °C, 4.20 V from 45.1 to 50 °C (`qcom,max-voltage-uv`, `qcom,jeita-fv-ranges`) | the stock driver's vote, set every 30 s from the battery's temperature: the lowest of 4.35 V (the boot loader's, `pico,float-voltage-max-uv`), 4.20 V from 46.1 °C (45.1 °C and the stock 1 °C hysteresis, back at 44.0 °C) and the charger's warm zone | host tests on a fake PMIC; on the headset 4.35 V at 24 °C, `vbat_sns` 4.29 to 4.36 V at 100 % while charging under the session's load |
| Charge current | 2.65 A from 0 to 10 °C, 3.71 A to 20 °C, 5.3 A to 50 °C (`qcom,jeita-fcc-ranges`, `qcom,fastchg-current-ma`) | the boot loader's 2.0 A (`0x1061` at 0x28) at every temperature, below PICO OS's in every range | read on the headset |
| Charging temperature window | software JEITA, 0 to 50 °C (`qcom,sw-jeita-enable`); ADC codes hard `0x58cd`/`0x1c22`, soft `0x4ccc`/`0x20b8`, hysteresis `0x48d4`/`0x23c0`; notices at 45, 50 and 0 °C | the stock hard and soft codes, written at boot and read back (the boot loader's hard `0x581c`/`0x1811` and soft `0x45e2`/`0x20c4` cut off about 5 °C hotter); the charger's soft-zone compensation off, as the stock driver does (`0x1090` 0xdb to 0xd0); in a soft zone the stock handling: charge inhibit, the window widened to the hysteresis codes, the zone's voltage; outside 0 to 50 °C the charger stops; no notices; the battery's health shows the zone | host tests on a fake PMIC; read back on the headset |
| Charger's own temperature | 80.0 °C (`qcom,charger-temp-max`, `qcom,smb-temp-max`): on PM8150B the stock driver only reports these; `smb-temp-max` is for the SMB1390 charge pump, disabled in the stock tree; die regulation is the hardware's own | the same: nothing written | read |
| Charging end | at 200 mA charge current by the ADC, on a count of samples (`qcom,chg-term-src` 1, `qcom,chg-term-current-ma` −200); recharge below 98 % state of charge on one sample (`qcom,auto-recharge-soc`); safety timer 768 minutes; after a termination at 100 % a workaround suspends the input while the gauge's charge counter overshoots (`CHG_TERMINATION_WA`) | the stock termination, recharge and timer, written at boot and read back (the boot loader's: −100 mA, recharge by voltage, 384 minutes); the termination workaround does not run | host tests on a fake PMIC; read back on the headset |
| Battery current and voltage limiter | zones at 4.5 and 5.1 A, 2.9, 2.8 and 2.6 V (`pm8150b-ibat-*`, `pm8150b-vbat-*`), 3.0, 2.75 and 2.5 V (`pm8150l-vph-*`); `limits-dcvs` nodes | no nodes in the tree | read |
| Speaker protection | off: `persist.vendor.audio.speaker.prot.enable=false` | off | read |
| Speaker volume cap | none | none | read |

### Low battery

Only `pico4-battery-guard` acts on the level: logind has no battery policy, the power key is only a key, and UPower
(D-Bus activated, not enabled) has `PercentageAction=2` and `CriticalPowerAction=HybridSleep`, with Hibernate and
PowerOff as fallbacks.

[`battery-guard`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/battery-guard) reads the driver's `capacity`, `status` and
`temp` every 5 s and takes PICO OS's decision; the driver renews `temp` every 30 s, and without a reading the
temperature decides nothing. Below 1 % it powers off whatever `status` says: on a port too weak for the load, `status`
says `Charging` while the battery drains ([userspace.md](userspace.md#adaptations-in-the-image)). Powered off with the
cable in, the headset starts in charger mode and charges there. The guard does not run while the headset sleeps (the
driver takes no interrupts), so the level is judged again at the next wake.

### Speakers

The risk is the same as on PICO OS, which runs the speakers without active protection and without a cap:

- **Protection.** The vendor's `build.prop` sets `persist.vendor.audio.speaker.prot.enable=false`;
  `vendor.audio.feature.spkr_prot.enable=true` only loads `libspkrprot.so`, which reads that property and disables
  itself. The development headset's persist holds no speaker calibration. Only the `speaker-protected` mixer path,
  unused with protection off, routes `VISENSE` back to the DSP.
- **Volume.** PICO OS's media speaker curve ends at 0 dB (`audio_policy_volumes.xml`: −29.7 dB at 0, −20.1 at 33,
  −10.2 at 66, 0 at 100), and `config_safe_media_volume_enabled` is false.
- **Here.** `WSA_RXn Digital Volume` 84 as in `mixer_paths.xml`, compander and boost on, the amplifier registers of the
  stock kernel during playback (the kernel commit “ASoC: the speakers' amplifiers and codec path as the stock kernel runs them”), PICO's calibration with its processing blocks
  ([userspace.md](userspace.md#audio)). `VISENSE` stays off. A path whose topology the DSP refuses has none of PICO's
  processing.

PICO OS has no number to cap at, so the volume is not capped. That the property holds on a running PICO OS is read
from the images, not checked with `getprop`.

### Open work

- No limit has been triggered: the shutdown above 59.0 °C, the charger's zones and the charge's end are checked by
  host tests and register readback, not with a hot or a full battery.
- The stock driver's termination workaround does not run.
- A wake for a low battery during sleep.

## Build

The overlay in [userspace/rootfs](../userspace/rootfs) puts `pico4-fan` and its unit into the userspace archive, and
`scripts/build-userspace.sh` enables the unit ([development.md](development.md)). Nothing about the batch is in the
image: the `auto` profile reads the serial number from persist at start and keeps only the batch word;
`pico4-fan batch FILE` prints that word for a serial number file.

## Checks

- **`pico4-userspace-check`.** Where `/sys/class/tty/ttyHS1` exists, `pico4-fan.service` must be active and write its
  state; the result's `fan` object holds the service state and `pico4-fan state` (profile, duty, reason, boost, RPM,
  hottest sensor and mean, MCU error). The MCU must have acknowledged a duty (the daemon's `duty` is the last
  acknowledged one) within 10 s and report no error; otherwise the check fails and names the error. Without the UART
  the section is `skipped`.
- **The acceptance runner** requires the service (`validate_check` in [tooling/userspace.py](../tooling/userspace.py)),
  rejects a state without an acknowledged duty or with an MCU error, and keeps the `fan` object in `result.json`.
- **`pico4-valve-session-check`** records the daemon's state before and after the session (`fan.before`,
  `fan.after_run`; null without a daemon), next to the hottest zone.

## Run

As root on the headset (`python -m overpressure dev ssh COMMAND...` in the development loop):

```sh
pico4-fan status                     # duty, RPM, version and fan info from the MCU; the daemon's state
pico4-fan state                      # the daemon's state as one JSON line, as the checks read it
pico4-fan monitor --seconds 600      # JSON lines every 5 s: hottest zone, duty, RPM
systemctl stop pico4-fan.service     # before measuring the fan by hand:
pico4-fan set 40                     # one SetFanSpeed, then duty and RPM read back
pico4-fan sweep --settle 5           # duty 0-100 % against RPM; restores the duty it found
pico4-fan run --dry-run              # the policy without the MCU: logs the duties it would set
```

`monitor` reports the hottest zone as `pico4-valve-session-check` counts it (every zone below 200 °C), next to the
daemon's hottest CPU/GPU mean.

## Verified results

| Check | Result |
| --- | --- |
| Stock kernel configuration (live) | `SENSORS_PWM_FAN=y`, `PWM_QTI_LPG=y`, `PWM_SYSFS=y`, `GPIO_SYSFS=y`, `THERMAL_GOV_USER_SPACE=y`, `THERMAL_WRITABLE_TRIPS=y` |
| Stock DTBs (6), overlays (16) and the live tree (DTB 3, overlay 7) | no `pwm-fan` node; pinctrl `fan_pwm_test_active` (gpio49) unreferenced; thermal trips and cooling maps as in the table above |
| Stock vendor partition | `thermal-engine` service commented out, its configuration empty |
| Stock system partition | `pxrfanservice` starts at boot as root, MCU path for product `Phoenix`; zones, means, tables, hysteresis, timing and notice as above. GD32 service: SetFanSpeed `0E` (duty < 101, ack `79`), GetFanSpeed `0F`, GetFanRPM `10`; SetFanRPM `29` and GetFanInfo `30` through the generic command path, which recomputes the checksum |
| Development headset (`pico4-fan batch /mnt/vendor/persist/falcon/sn`) | late batch; standard PICO 4, control type 1; with `PICO4_FAN_PROFILE=auto` the daemon picks `late`, and at a hottest mean of 41 °C the fan stands |
| GetFanInfo at 0, 30, 45 and 60 % | `00 0000 … 00`, `1e d312 … 01`, `2d 131b … 01`, `3c 0e23 … 01`: duty, RPM (4,819, 6,931, 8,974) and running; no fan identity |
| Duty against RPM (`pico4-fan sweep`, 10–100 %) | 10 and 20 %: 3,650 and 3,670 rpm (the fan's lowest speed); 30 %: 5,265; 40 %: 6,770; 50 %: 8,160; 60 %: 9,470; 70 %: 10,740; 80 %: 11,940; 90 %: 13,070; 100 %: 14,150 rpm |
| `pico4-userspace-check` and `dev check` | pass with the duty acknowledged (30 %, about 5,080 rpm, no MCU error); a SetFanSpeed error fails the check and names it; Monado reads the IPD through the shared port lock |
| Valve's 30 s session check, installed system | hottest zone 65.5 °C, 90.03 commits/s; without fan control (stock kernel) the check went from 47.1 to 85.7 °C |
| Valve's session for 10 minutes at 90 fps, late table (stock kernel) | fan at 30 %, then 35 % (5,160–6,090 rpm); hottest CPU/GPU mean 61.8–73.5 °C in minutes 5–10, peak zone 77.5 °C; 31 gaps above 100 ms |
| The same with the early table (stock kernel) | fan at 45 % from the first minute (7,540–7,650 rpm); hottest mean 65.6–72.1 °C in minutes 5–10, peak zone 79.5 °C (from a warmer start); 8 gaps above 100 ms |
| Idle session with PICO OS's tracker, still, 3 minutes each without, with and again without the VR CPU limits (stock kernel) | hottest zone mean 59.7, 59.4 and 59.4 °C; peaks 75.6, 60.8 and 70.9 °C (the limits cap the boosts); big-core clock 921, 1,047 (the hint's floor) and 938 MHz; the tracker lost no pose and stayed within 1.1 mm |
| Build: [test_fan.py](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/test_fan.py) | frames against the stock bytes and the allowlist, replies, both tables and their hysteresis, boost and failsafe, means and timing, retries, the state file, zone selection, profile choice, the batch rule on synthetic serial numbers; a UART transaction over a pty on Linux; the power-off with a silent MCU or stalled fan, and none with a cooling fan, below the limit or after the MCU answers again |
| Build: [test_battery_guard.py](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/test_battery_guard.py) | PICO OS's levels and delay, screen on and off, charging, an unreadable battery |
| Host: [tests/test_userspace.py](../tests/test_userspace.py), [tests/test_hardware.py](../tests/test_hardware.py) | the userspace check's `fan` object and the session check's `fan.before`/`fan.after_run`, through the runner into `result.json` |
| Build: [test_pico4_lens.c](https://github.com/overpressure/packages/blob/main/phoenix/pico4-monado/test_pico4_lens.c) | the IPD reader gives up with `-EBUSY` after a second while another descriptor holds the port lock, and goes on once it is free |
