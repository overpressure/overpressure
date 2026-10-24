# Cooling

The fan and a thermal policy for the Linux userspace on the stock kernel and device tree. The
fan is not a Linux device on either system: it hangs off the GD32 MCU that also moves the lenses
and drives the backlight ([xr.md](xr.md#lens-distortion-and-ipd)), and PICO OS's
`pxrfanservice` sets its duty over that MCU's UART. [`pico4-fan`](../userspace/rootfs/usr/bin/pico4-fan)
runs the same policy with the same frames under stage 2: the userspace build enables its
service, and the acceptance checks require it on the headset. On the headset the MCU
acknowledges its duty and reports the fan's speed, and Valve's 30 s session check takes the
hottest thermal zone from 45 °C to 69 °C with the fan at 30 %. Without fan control the same
check went from 47 °C to 86 °C, and a minute of the session from 54 °C to 92 °C
([session.md](session.md#performance-and-heat)). The limit for the hottest zone is 90 °C; with
PICO OS's early table, the default, 10 minutes of Valve's session stay below 80 °C.

## How PICO OS cools the PICO 4

The fan is an AVC blower, BAPA0405R5HY001 in the public part list of
[PICO-documentation](https://github.com/thoricelli/PICO-documentation/blob/main/devices/README.md),
which also lists the `pxrfanservice` system service. Its control path in the stock system and
vendor partitions:

```text
pxrfanservice                 /system/bin, init class core, root (pxrfanservice.rc)
  libpxrfanservice.so         FanControl: CPU and GPU zones -> duty
    libgd32ipdclient.pxr.so   SetFanSpeed(duty), over binder
      gd32ipdservice          libgd32ipdservice.so: A0 0E 00 duty 00 xor on /dev/ttyHS1
        GD32F330 MCU          fan PWM; also the lens motor and the backlight
```

What PICO OS does not use, on either system:

- **The kernel's fan driver.** The stock kernel has a PICO-modified `pwm-fan`
  (`CONFIG_SENSORS_PWM_FAN=y`): hwmon `pwmfan` with a `pwm_rpm` tachometer attribute and the
  GPIOs `pwm_pwr_enable` and `fan_pwm_irq`. None of the stock DTBs or overlays has a `pwm-fan`
  node, and neither has the live tree. The only trace is a pinctrl state, `fan_pwm_test_active`
  (gpio49), that nothing references. The hwmon lines in `init.picovr.rc` (`hwmon1/pwm1`,
  `fan1_target`) and pxrfanservice's sysfs branch serve other products: pxrfanservice uses the
  MCU when `ro.picovr.product.name` is `Phoenix`, the PICO 4.
- **thermal-engine.** Qualcomm's daemon is off: its service is commented out in
  `init_thermal-engine.rc`, and `thermal-engine.conf` is the empty default. The `*-usr` zones
  (`user_space` governor, trips at 115 and 125 °C) have no listener on either system.

The kernel's `step_wise` zones come from the same device tree on both systems, so they throttle
alike:

| Zone | Trip | Cooling |
| --- | --- | --- |
| `cpu-1-*-step` (gold and prime cores) | 75 °C, 5 °C hysteresis | the prime core's frequency, one step |
| `gpuss-max-step` | 95 °C | GPU frequency, down to its floor |
| `pop-mem-step` | 95 °C | CPU 4 and CPU 7 frequency |
| `cwlan`, `video`, `ddr`, `q6-hvx`, `camera`, `cmpss`, `npu` `-step` | 100 °C | CDSP, GPU, modem, NPU |
| `cpu-*-step` | 110 °C | CPU isolation |
| `apc-*-max-step` | 120 °C | none |
| `pm8150*_tz` | 95, 115, 145 °C | none (PMIC) |

PICO OS therefore cools with the fan and the kernel's throttling; stage 2 lacks only the fan.

### The fan protocol

Frames as `gd32ipdservice` builds them, in the MCU's framing (requests `A0 cmd p1 p2 p3 xor`,
replies `79 … xor`):

| Request | Reply | Command |
| --- | --- | --- |
| `A0 0E 00 dd 00 xx` | `79` | SetFanSpeed, `dd` = duty 0–100 %; the service refuses 101 and up |
| `A0 0F 00 00 00 AF` | `79 lo hi xx` | GetFanSpeed: the duty |
| `A0 10 00 00 00 B0` | `79 lo hi xx` | GetFanRPM: the tachometer |
| `A0 26 00 00 00 86` | 8 bytes | one of GetGD32MCUVersion's three queries |
| `A0 30 00 00 00 90` | `79 dd lo hi 00 00 00 rr 00 xx` | GetFanInfo (`fan_info_t`): duty `dd`, RPM `hi lo`, `rr` 1 while the fan runs; the other bytes read zero |
| `A0 29 …` | | SetFanRPM, for the RPM tables below |

Each transaction votes the port's clocks (`ioctl 0x544D`), flushes both queues, writes the
6 bytes and polls up to ten times at 1 ms for a reply starting with `79`.

### PICO OS's policy

`FanControl` in `libpxrfanservice.so` (symbols from its `.gnu_debugdata`):

- **Input.** On `kona` the service reads 14 zones every 200 ms: `cpu-0-0…3-usr`,
  `cpu-1-0…7-usr`, `gpuss-0-usr` and `gpuss-1-usr` (zones 1–4, 7–15 and 24).
- **Mean.** Each zone keeps its last 50 readings (10 s) and ignores readings above 110 °C; the
  hottest zone mean decides.
- **Table.** Six thresholds with a duty from each threshold up:

  | Table | Thresholds (°C) | Duty (%) |
  | --- | --- | --- |
  | early | 90, 85, 70, 60, 55, 50 | 75, 60, 45, 30, 30, 30 |
  | late | 90, 85, 80.5, 72, 60, 55 | 50, 45, 40, 35, 30, 0 |

  On the way down a duty holds until the temperature falls one more step: in the band below
  threshold *k*, a fan running faster than duty *k* runs at duty *k−1*. Between the two lowest
  thresholds nothing changes.
- **Which table.** The production batch decides. The serial number in `persist/falcon/sn` (its
  ninth character, then a date code compared with the properties
  `persist.pxr.fancontrol.limit1/limit2/serial1`, defaults 717, 726, D) selects "early" or
  "late". `persist.pxr.fancontrol.control.type` can switch late headsets to RPM control with
  SetFanRPM: type 2 uses the late table's RPM column (8,500, 7,800, 7,170, 6,540, 5,700 and
  0 rpm), type 3, the default with eye tracking (the Pro), 13 steps from 3,700 rpm at 55 °C to
  7,500 rpm at 90 °C. Without eye tracking the default is 1, duty.
- **Sending.** A changed duty goes out at once, an unchanged one again after 10 s.
- **Overheating.** When the hottest mean stays at or above 95.1 °C for two minutes, PICO OS
  shows a notice in VR, again every 30 s.
- **Overrides.** `/data/picovr/fancontrol_config.txt` replaces tables. While a factory test runs
  (`pvr.factorytest.running`), the service sets 0 % once and stops controlling the fan, unless
  `pvr.fan.forced` is set.

The development headset is a standard PICO 4 of the late batch: control type 1, the "late"
table.

## pico4-fan

[`pico4-fan`](../userspace/rootfs/usr/bin/pico4-fan) (Python, standard library) runs as
[`pico4-fan.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-fan.service):

- **The stock policy.** The same 14 zones, matched by type, every 200 ms; means over 50
  readings; the same tables and hysteresis; an unchanged duty resent every 10 s. The overheating
  notice becomes a journal warning.
- **Full speed when hot**, which PICO OS does not have: while any of the 14 sensors reads 95 °C
  or more, the fan runs at 100 % until the hottest mean is below 90 °C, then steps down through
  the table. 95 °C is where the kernel starts throttling the GPU.
- **Failsafe.** Without usable readings the fan runs at 100 %. A failed transaction is retried
  every second and logged once per outage, then once a minute; a fan that reports 0 rpm at 30 %
  or more three times in a row is logged once.
- **The MCU.** Frames come only from an allowlist: SetFanSpeed with a duty of 0–100 and the
  four queries above. Nothing can build a lens move, calibration write or firmware frame. Each
  transaction holds an exclusive `flock` on `/dev/ttyHS1`. Monado's PICO 4 driver takes the same
  lock for its IPD read ([pico4_ipd.c](../src/monado/pico4_ipd.c)); after waiting a second for
  it, the driver uses Monado's default IPD.
- **The table.** PICO OS's early table on every headset by default. Valve's GPU-bound session
  heats the headset more than PICO OS's own apps, and the early table runs the fan at least as
  fast at every temperature (see [Verified results](#verified-results) for both tables in a
  10-minute session). `PICO4_FAN_PROFILE=auto` picks the table by batch as PICO OS does: it
  reads `/usr/share/qcom/pico4/calibration/fan-profile`, one word from the private sensor data
  (see [Build](#build)), and uses "early" without it. The MCU cannot tell: GetFanInfo reports
  the fan's duty, RPM and whether it runs, nothing that identifies the fan or its batch. This
  headset's batch is late.
- **Stopping.** The fan keeps its last duty when the service stops.
- **Where it runs.** The unit's condition is `/sys/class/tty/ttyHS1`, present from kernel boot
  on the headset (udev creates the `/dev` node later); QEMU has none, so the unit is skipped
  there.

Settings, in `/etc/pico4-poc/fan.conf` (the service's `EnvironmentFile`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `PICO4_FAN_PROFILE` | `early` | `early`, `late`, or `auto` (by batch, as PICO OS) |
| `PICO4_FAN_TABLE` | | own table, `threshold °C:duty %` pairs hottest first, e.g. `90:50,85:45,80.5:40,72:35,60:30,55:0` |
| `PICO4_FAN_BOOST` | `95:90:100` | full speed from °C, until the mean is below °C, at duty %; or `off` |

The daemon writes its state to `/run/pico4-fan/state.json` every second: duty, reason, RPM,
the hottest mean, and every sensor's reading and mean.

## VR CPU limits

While a VR app runs, PICO OS limits the CPU through Qualcomm's VR service. `svrSetPerformanceLevels`
in `libsvrapi.so` hands the app's CPU and GPU levels to `qvrservice`, whose configuration
(`/system/etc/qvr/qvrservice_config.txt`, `performance_perf_hints`) maps each of the nine level
pairs to a power hint in the vendor's `powerhint.xml`, `0x130A`–`0x1312`. All nine hints hold
the same resources (perf HAL opcodes, decoded with the vendor's `commonresourceconfigs.xml`; the
hint's clusters count big, little, prime):

| Resource | Value |
| --- | --- |
| `msm_performance` `cpu_min_freq` / `cpu_max_freq`, big cores (4–6) | 864 / 2016 MHz |
| the same, little cores (0–3) | 576 / 1804 MHz (their own maximum) |
| the same, prime core (7) | 941 / 2054 MHz |
| `core_ctl` `min_cpus` | 3 big cores, 1 prime core |
| KGSL `min_pwrlevel` / `max_pwrlevel` | 5 / 0 |

Without limits the big cores reach 2419 MHz and the prime core 2842 MHz. The stock kernel has
the same `msm_performance` and `core_ctl` nodes, and
[`vr-performance`](../userspace/rootfs/usr/libexec/pico4-poc/vr-performance) applies the CPU part:
[`pico4-vr-performance.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-vr-performance.service)
runs it while `pico4-session.service` or `pico4-monado.service` runs (both want the unit, which
stops when neither does) and puts the limits it found back when it stops. The GPU part stays
with the compositors, which hold KGSL's floor themselves while they present
([session.md](session.md)).

## Build

The overlay in [userspace/rootfs](../userspace/rootfs) puts `pico4-fan` and its unit into the
userspace archive, and `scripts/build-userspace.sh` enables the unit
([development.md](development.md)). `scripts/extract-sensor-data.sh` writes
`calibration/fan-profile` into the sensor data: it pipes the serial number from the persist
backup into `pico4-fan batch -`, which prints only `early` or `late`, so the serial goes no
further. The build installs the file with the rest of the calibration. Sensor data extracted
without this step has no `fan-profile`; the daemon then uses the early table.

## Checks

- **`pico4-userspace-check`.** Where `/sys/class/tty/ttyHS1` exists, `pico4-fan.service` must
  be active and write its state. The result's `fan` object holds the service state and
  `pico4-fan state`: profile, duty, reason, boost, RPM, hottest sensor and mean, and the MCU
  error if any. The MCU must have acknowledged a duty (the daemon's `duty` is the last
  acknowledged one) and report no error; otherwise the check fails and names the MCU's error.
  It waits up to 10 s for the first acknowledgement. In QEMU the section is `skipped`.
- **The acceptance runner** requires the service on the headset (`validate_check` with
  `require_fan` in [pico4/userspace.py](../pico4/userspace.py)), rejects a state without an
  acknowledged duty or with an MCU error, and keeps the `fan` object in `result.json`.
- **`pico4-valve-session-check`** records the daemon's state before and after the session
  (`fan.before`, `fan.after_run`; null without a daemon), next to the hottest zone.

## Run

As root on the headset (`python -m pico4 dev ssh COMMAND...` in the development loop):

```sh
pico4-fan status                     # duty, RPM, version and fan info from the MCU; the daemon's state
pico4-fan state                      # the daemon's state as one JSON line, as the checks read it
pico4-fan monitor --seconds 600      # JSON lines every 5 s: hottest zone, duty, RPM
systemctl stop pico4-fan.service     # before measuring the fan by hand:
pico4-fan set 40                     # one SetFanSpeed, then duty and RPM read back
pico4-fan sweep --settle 5           # duty 0-100 % against RPM; restores the duty it found
pico4-fan run --dry-run              # the policy without the MCU: logs the duties it would set
```

`monitor` reports the hottest zone as `pico4-valve-session-check` counts it (every zone below
200 °C), next to the daemon's hottest CPU/GPU mean.

## Verified results

| Check | Result |
| --- | --- |
| Stock kernel configuration (live) | `SENSORS_PWM_FAN=y`, `PWM_QTI_LPG=y`, `PWM_SYSFS=y`, `GPIO_SYSFS=y`, `THERMAL_GOV_USER_SPACE=y`, `THERMAL_WRITABLE_TRIPS=y` |
| Stock kernel image | PICO's `pwm-fan`: hwmon `pwmfan`, `pwm1_enable`, `pwm_rpm`; GPIOs `pwm_pwr_enable`, `fan_pwm_irq` |
| Stock DTBs (6), overlays (16) and the live tree (DTB 3, overlay 7) | no `pwm-fan` node; pinctrl `fan_pwm_test_active` (gpio49) unreferenced |
| Stock vendor partition | `thermal-engine` service commented out, its configuration empty |
| Stock system partition: `pxrfanservice` | starts at boot as root; MCU path for product `Phoenix`; zones, means, tables, hysteresis, timing and overheating notice as above |
| Stock system partition: GD32 service and client | SetFanSpeed `A0 0E 00 dd 00 xx` (duty < 101, ack `79`), GetFanSpeed `0F`, GetFanRPM `10` (`79 lo hi xx`); SetFanRPM `29` and GetFanInfo `30` through the generic command path, which recomputes the checksum |
| Live tree thermal zones | trips and cooling maps as in the table above |
| Development headset (persist backup, through `scripts/extract-sensor-data.sh`'s step) | late batch; standard PICO 4, control type 1 |
| Headset: `pico4-fan` with the sensor data extracted again | profile `late` from `calibration/fan-profile`; at a hottest mean of 41 °C the fan stands (the late table runs it from 55 °C) |
| Headset: GetFanInfo at 0, 30, 45 and 60 % | `00 0000 … 00`, `1e d312 … 01`, `2d 131b … 01`, `3c 0e23 … 01`: duty, RPM (4,819, 6,931, 8,974) and running; no fan identity |
| Host: [tests/test_fan.py](../tests/test_fan.py) | passes: frames against the stock bytes and the allowlist, replies, both tables and their hysteresis, boost and failsafe, the loop's means and timing, retries, the state file and what the checks read from it, zone selection, profile choice, the batch rule on synthetic serial numbers (the serial is never printed), the unit; one UART transaction over a pty on Linux |
| Host: [tests/test_userspace.py](../tests/test_userspace.py), [tests/test_hardware.py](../tests/test_hardware.py) | pass: the userspace check's `fan` object (active with a state; skipped only without `require_fan`; a state without an acknowledged duty or with an MCU error fails) and the session check's `fan.before`/`fan.after_run`, through the runner into `result.json` |
| Headset: `pico4-userspace-check` and `dev check` | pass with the duty acknowledged (30 %, about 5,080 rpm, no MCU error); a daemon state with a SetFanSpeed error fails the check and names it |
| Headset: `pico4-fan.service` in the acceptance cycle | active; the MCU answers the queries and acknowledges SetFanSpeed, no MCU error in the journal; the fan reads 0 % and 0 rpm before the daemon, then runs at 30 % from the early table (hottest mean 35–43 °C) at 4,849–4,914 rpm; Monado reads the IPD (71.99 mm) through the shared port lock |
| Headset: duty against RPM (`pico4-fan sweep`, 10–100 %) | 10 and 20 %: 3,650 and 3,670 rpm (the fan's lowest speed); 30 %: 5,265; 40 %: 6,770; 50 %: 8,160; 60 %: 9,470; 70 %: 10,740; 80 %: 11,940; 90 %: 13,070; 100 %: 14,150 rpm |
| Headset: Valve's session (SteamVR's defaults, 54 frames per second) for 10 minutes from idle, late table | hottest zone 48 → 66 °C in the first minute (the load step: the hottest mean passed 60 °C and the fan went to 30 % within it), 73–75 °C from the fifth minute with the fan at 35 % (about 6,050 rpm), peak 91.1 °C (one CPU sensor, mean 73.9 °C) after 645 s |
| Headset: Valve's session for 10 minutes on the Frame's compositor path (90 fps), late table | fan at 30 %, then 35 % (5,160–6,090 rpm); hottest CPU/GPU mean 61.8–73.5 °C in minutes 5–10, peak zone 77.5 °C; 31 gaps above 100 ms |
| Headset: the same with the early table | fan at 45 % from the first minute (7,540–7,650 rpm); hottest mean 65.6–72.1 °C in minutes 5–10, peak zone 79.5 °C (from a warmer start); 8 gaps above 100 ms |
| Headset: the same with `PICO4_FAN_TABLE=80:100,75:80,70:60,65:45,60:30,55:0` | fan at 45 % in the first minutes, 60 % (9,600 rpm) from the fourth; hottest zone 68–72 °C, peak 84.5 °C (mean 72.6 °C) |
| Headset: Valve's session check (30 s) with the fan | hottest zone 45.3 °C before, 69.4 °C after; the fan stayed at 30 % (the early table holds 30 % up to a 70 °C mean); without fan control the same check went from 47.1 to 85.7 °C |
| Build: [test_pico4_lens.c](../src/monado/test_pico4_lens.c) | the IPD reader gives up with `-EBUSY` after a second while another descriptor holds the port lock, and goes on once it is free |

## Next steps

1. **Longer sessions.** The limit for the hottest zone is 90 °C, and the session check holds
   runs to it. On the Frame's compositor path both of PICO OS's tables keep a 10-minute Valve
   session below 80 °C; the faster early table lowers the hottest mean by only about 1.5 °C,
   because the heat sits in the CPU cores, which the kernel throttles from 75 °C. Sessions
   beyond 10 minutes and with games are unmeasured.
