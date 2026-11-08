# Hardware testing

Three commands run stage 1 on a prepared headset ([device-preparation.md](device-preparation.md)).
The development loop, `python -m overpressure dev`, is the everyday route: the headset stays in
[development mode](#development-mode), with the engineering ABL, the dev image as the maintenance
stage 1 in `recovery` and the installed system on Valve's kernel, so that each attempt installs a
system image and runs the stage-2 checks.
The acceptance runner, `python -m overpressure hwtest`, boots one instrumented stage-1 image on the
stock kernel, checks it, then returns the headset to its original PICO OS. The acceptance cycle,
`python -m overpressure cycle`, runs one `flash-run` unattended between staging the engineering ABL and
restoring stock boot and stock ABL, both verified by EDL readback; it is the way back to stock,
not a step of every task. Every ADB and fastboot action names the headset's serial and every
wait is bounded; Linux writes the headset's storage only through `pico4-install` and the installed
system's data partition, and device-side deadlines return the headset to fastboot when the host
stops answering.

## Overview

| Command | Writes | Ends in | Use |
| --- | --- | --- | --- |
| `hwtest preflight` | temporary files under `/data/local/tmp`, removed again | PICO OS, never left | the image's userspace binaries on the stock kernel |
| `hwtest flash-run` | `boot`, restored afterwards | the original PICO OS | stage 1 and the way back |
| `cycle` | `abl` and `boot`, both restored | PICO OS on stock boot and stock ABL | back to stock, alone or after an acceptance run; unattended |
| `dev` | `abl` with `dev stage-abl`; `recovery` with `dev flash`; `boot`, `super` and `userdata` with `dev install`, until `dev restore` | development mode (the installed system, the maintenance stage 1 or fastboot), kept between tasks; PICO OS after `dev restore` | everyday development |

`flash-run` needs the engineering ABL: it answers the runner's unlock check, while the stock ABL
reports its unlock state as `DeviceUnlocked=true`, a form the check does not accept. The
engineering ABL has no `fastboot boot` (it answers `unknown command` after the transfer), so
every acceptance boot writes `boot` and restores it.

## Requirements

- **Headset**, prepared as in [device-preparation.md](device-preparation.md): identity record,
  verified EDL backup, unlocked bootloader, engineering ABL and Firehose loader. `hwtest`,
  `cycle` and `dev stage-abl` start from PICO OS with USB debugging authorized, `dev flash` from
  PICO OS or development mode, the other `dev` commands from development mode. `adb reboot edl`
  works from PICO OS and EDL needs no buttons, so the cycle and the dev loop need nobody at the
  headset.
- **Host**: Python 3.9+, Android platform-tools (`adb`, `fastboot`), OpenSSH (`ssh`, `ssh-keygen`)
  and, for the EDL steps of `cycle` and `dev stage-abl`, edl-ng. A USB network driver for the
  headset's NCM link and a route to 172.16.42.1; on Windows also the
  [NCM initialization](#windows-ncm-initialization). The tools change no host network settings and
  install no drivers; driver setup and physical recovery from a hard hang need a person.
- **Images**, built from the repository root ([development.md](development.md)):
  `out/pico4-stage1-<firmware>.img` from `python -m overpressure build --firmware <firmware>`, for the
  firmware entry that `python -m overpressure match` finds for the headset; for the installed
  system, the images in [install.md](install.md#install-and-update), from
  `out/userspace/pico4-userspace.tar.zst` with `pico4-userspace.manifest.json` beside it.
- **One at a time**: concurrent runs against one headset are unsupported.

## Settings for cycle and dev

`cycle` and `dev` read their settings from `.env` at the repository root
([tooling/localconfig.py](../tooling/localconfig.py)). The file describes one headset, so Git ignores
it; [.env.example](../../../.env.example) lists the keys. Environment variables of the same names
override it. Lines are `KEY=VALUE`, `#` starts a comment, and quotes around a value are removed.
Relative paths are relative to the repository root. A command that needs an empty key stops with
`set PICO4_... in .env`. `hwtest` takes command-line options instead; the cycle passes these
settings to it.

| Key | Default | Meaning |
| --- | --- | --- |
| `PICO4_ADB_SERIAL` | required | serial in `adb devices` |
| `PICO4_FASTBOOT_SERIAL` | required | serial in `fastboot devices`, also the USB gadget's serial in stage 1 and the installed system |
| `PICO4_EDL_SERIAL` | required for EDL writes | `Serial Number` in edl-ng's output; no ABL is written to another device |
| `PICO4_IDENTITY` | `backup/device.json` | identity record (`python -m overpressure device probe`); its SoC ID derives the OEM authorization and is checked at login |
| `PICO4_BACKUP` | `backup/stock` | verified EDL backup with the stock `boot` and `abl` |
| `PICO4_LOADER` | `assets/firehose.elf` | signed Firehose loader |
| `PICO4_ENGINEERING_ABL` | `assets/engineering-abl.elf` | engineering ABL image |
| `PICO4_ENGINEERING_ABL_SHA256` | required for staging | its expected SHA-256 |
| `PICO4_EDL` | empty: the fetched edl-ng (`python -m overpressure tools`), else PATH | edl-ng executable |
| `PICO4_FIRMWARE` | `5.13.7-global-seko` | entry of [tooling/firmware.json](../tooling/firmware.json); selects `out/pico4-stage1-<firmware>.img` |
| `PICO4_NCM_PYTHON` | empty: on Windows the Python that runs overpressure when it has pyusb and libusb-package, else no NCM initialization | Python with pyusb and libusb-package |
| `PICO4_PLATFORM_TOOLS` | empty: the fetched platform-tools, else `PATH` | folder with `adb` and `fastboot` |
| `PICO4_SSH`, `PICO4_SSH_KEYGEN` | `ssh`, `ssh-keygen` | OpenSSH executables |

## Development mode

Between tasks the headset stays in development mode: the engineering ABL in `abl`, the dev image
as the maintenance stage 1 in `recovery` on the stock kernel, and the installed system on Valve's
kernel in `boot`, `super` and `userdata` ([install.md](install.md)). `dev recovery` keeps every
boot in the maintenance stage 1 until `dev normal`, and no step needs PICO OS. A task leaves the
headset in this mode; nothing is restored when it ends. Enter the mode once, from PICO OS, with the
images of [install.md](install.md#install-and-update) built:

```sh
python -m overpressure dev stage-abl        # engineering ABL through EDL
python -m overpressure dev image
python -m overpressure dev flash            # dev image into recovery; the maintenance stage 1 starts
python -m overpressure dev install          # boot, super, userdata; the installed system starts
```

Then iterate:

```sh
python -m overpressure container run -- bash devices/phoenix/scripts/build-system-image.sh \
    out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
python -m overpressure dev recovery         # into the maintenance stage 1
python -m overpressure dev install          # writes what changed; the installed system starts
python -m overpressure dev check            # the runner's stage-2 checks
python -m overpressure dev installed        # after a later reboot: connect again
```

The session runs only on Valve's kernel, so `dev check` runs on the installed system. A changed
stage-1 image needs `dev image` and `dev flash`; the installed system's boot image holds no
stage 1. A new kernel goes through `dev boot-image`, `dev recovery` and `dev install --boot PATH`.
When a device deadline has returned the headset to fastboot, `dev reboot` starts what `misc` and
`boot` select, without a write. `hwtest flash-run` and `cycle` leave the stock boot image in `boot`
and so end development mode; with the system installed they cannot start PICO OS, whose system is
gone, so `dev restore` comes first.

### Back to stock

Only when PICO OS is needed, or to verify the way back, for example before a release:

```sh
python -m overpressure dev restore          # the backup's system and boot image, then PICO OS; the engineering ABL stays
python -m overpressure cycle --restore-only # EDL readback of boot, stock ABL, PICO OS
```

To verify stage 1 and the way back and end on stock, run `python -m overpressure cycle --skip-stage`
after `dev restore` instead. The cycle starts from PICO OS, hence `dev restore` before it.

## Acceptance runner

`python -m overpressure hwtest preflight|flash-run` ([tooling/hardware_test.py](../tooling/hardware_test.py)).
A pass establishes a Linux shell with USB-network diagnostics, read-only persistent storage and a
verified return to the same stock PICO OS. `flash-run` runs on the stock kernel and checks stage 1;
the session's checks run through `dev check` on the installed system. Audio is
not tested. `flash-run` never unlocks the bootloader, resets user data or modifies the source image.

```sh
python -m overpressure hwtest flash-run --serial ADB_SERIAL --fastboot-serial FASTBOOT_SERIAL \
    --confirm-device ADB_SERIAL --backup backup/stock \
    --image out/pico4-stage1-5.13.7-global-seko.img \
    --windows-ncm-python python   # Windows: a Python with pyusb and libusb-package
```

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--serial` | required; `preflight` takes the only authorized ADB device | ADB serial |
| `--fastboot-serial` | `--serial` | fastboot serial if it differs; also stage 1 gadget's USB serial |
| `--image` | required for `flash-run` | stage-1 image |
| `--rootfs` | `preflight`: the newest `out/pico4-stage1-*-rootfs.tar` | `out/pico4-stage1-<firmware>-rootfs.tar` |
| `--backup` | required for `flash-run` | verified EDL backup |
| `--confirm-device` | required for `flash-run` | repeats `--serial` to authorize the boot write |
| `--out` | `out/hardware-<random>` | new [result directory](#result-directory) |
| `--host` | `172.16.42.1` | the headset's USB IPv4 address |
| `--adb`, `--fastboot`, `--ssh`, `--ssh-keygen` | found on `PATH` | executables, checked before anything runs |
| `--windows-ncm-python` | off | Windows only: [NCM initialization](#windows-ncm-initialization) |
| `--boot-timeout`, `--report-timeout`, `--recovery-timeout` | 180, 180, 420 | [limits](#failure-and-timeout-behavior) in seconds |

### Phases

`result.json` records each phase under `checks`, with these names:

1. **prepare test boot image.** With `--windows-ncm-python`, the NCM helper's `--check`. A fresh
   ed25519 key for the run. The source image must pass its header checksum, be a stage-1 build, keep
   read-only mode and the QEMU self-test off (`pico4.rw`, `pico4.selftest`) and start the
   independent `recovery-watch` service (rebuild older images). The runner derives `test-boot.img`: root's `authorized_keys` with the run's
   public key, a random run identifier (in `/etc/pico4/test-run` and as
   `pico4.test_run=`), the [device deadlines](#device-deadlines) and no boot signature.
   Kernel, DTBs and boot layout are kept; the runner parses the packed image again and compares
   kernel and DTB. Both images' SHA-256 are recorded. The preflight uses `rootfs.tar`, the
   userspace of the same source image, so it cannot be paired with another rootfs.
2. **identify stock headset.** After PICO OS reports boot completion: the fingerprint, which must
   be an original PICO 4's (`Pico/Phoenix.../PICOA8110:10/...`), the OEM state and the SoC ID.
   They are the baseline for every later identity check.
3. **verify and pin stock boot restoration image** (`flash-run`, see
   [below](#explicit-boot-partition-test)).
4. **userspace preflight.** Pushes `rootfs.tar` and
   [scripts/preflight-device.sh](../scripts/preflight-device.sh) to
   `/data/local/tmp/pico4-stage1-<run>`; the script runs the image's Alpine binaries on the stock
   kernel as the unprivileged ADB shell user. It must exit 0 and print exactly one summary
   `PREFLIGHT: N passed, 0 failed` with N > 0 (`preflight.txt`). The files are removed after a
   failure too.
5. **enter unlocked fastboot.** `adb reboot bootloader`; within `--boot-timeout` the fastboot
   serial must appear, and `fastboot oem device-info` (or `getvar unlocked`) must report a known
   unlocked state.
6. **flash test boot image**, bounded by `--boot-timeout`.
7. **SSH identity.** Within `--boot-timeout`, after the NCM initialization if enabled, SSH with
   the run's key must return this run's identifier and the baseline SoC ID before the endpoint
   counts as the headset. Only then does the runner clear the device's connection deadline.
8. **Linux shell and read-only storage.** The running build metadata equals the image's, the
   kernel release matches it, the machine is `aarch64`, and the shell runs as UID 0 and executes
   commands. `/sys/class/block/*/ro` is 1 for every disk and partition, device-mapper devices
   included and loop, ram, zram and nbd devices excluded; root is RAM-backed, and no `/dev/*`
   file system is mounted writable. These checks observe: nothing
   tries to write to a real partition. For `flash-run`, `boot` read back over the test image's
   length must hash to the test image's SHA-256.
9. **HTTP and diagnostic report.** `cgi-bin/status` must be stage 1's and `cgi-bin/log?boot` must
   name this run. The runner waits up to `--report-timeout` for the report that init's probe
   writes instead of starting another probe; `cgi-bin/report` must hold `summary.txt`,
   `system.txt` (naming this run) and `dmesg.txt`, each 1 B to 16 MiB, inspected without
   extracting device-controlled paths. HTTP bypasses proxies, rejects redirects and limits each
   response to 32 MiB and 15 s. Mounting the USB log drive is not part of the test.
10. **return to original PICO OS**, whenever the headset left PICO OS, after a pass or a failure
    ([recovery](#failure-and-timeout-behavior)). The headset must come back under the original
    ADB serial with boot completed and the baseline fingerprint, OEM state and SoC ID; reaching
    fastboot alone does not count.

`test-boot.img` tests the supplied stage-1 image with authentication and identification instrumentation;
it is not a claim that the unmodified source image booted byte for byte. The run's private key is
deleted when the runner finishes. Host keys are accepted on the first connection into the run's
own `known_hosts` and pinned from then on; no password prompts, personal SSH configuration or
global known-hosts updates are involved.

### Windows NCM initialization

The stock kernel's NCM function (PICO OS 5.13.7) rejects the packets of Windows' NCM driver with
`Wrong NDP SIGN` in its kernel log, so Windows gets no DHCP, SSH or HTTP. This concerns stage 1 on
the stock kernel: the maintenance stage 1 and the runner's images. Valve's kernel's NCM function,
under the installed system, takes Windows 11's in-box driver as it is. Sending the CDC request
`SET_CRC_MODE(0)` through UsbDk and then releasing the device to Windows 11's in-box UsbNcm driver
makes all three work without changing the kernel. [overpressure/windows_ncm.py](../../../overpressure/windows_ncm.py)
does this; `--windows-ncm-python` (runner) or, for the cycle and the dev loop, `PICO4_NCM_PYTHON`
(else the Python that runs overpressure, which has pyusb and libusb-package) enables it.

The request works because the 4.19 kernel's `f_ncm` recomputes the NDP signature it checks on
every CDC class request to its control interface (and resets it with the data interface);
Windows' UsbNcm sends none, and nothing on the gadget side triggers the recompute. The stock
kernel has no RNDIS, ECM or EEM function to use instead.

Install [UsbDk](https://github.com/daynix/UsbDk/releases) (`winget install daynix.UsbDk`, as
administrator; the helper loads `UsbDkHelper.dll` from `%ProgramFiles%\UsbDk Runtime Library`),
and run overpressure on a Python with pyusb and libusb-package (the start scripts' and `uv sync`'s have them):

```powershell
python -m overpressure doctor        # "NCM Python and UsbDk": the packages and the UsbDk backend
```

`--check` tests the dependencies and the UsbDk backend without opening a device;
`--serial FASTBOOT_SERIAL` initializes a running stage 1, also by hand.

- The runner runs `--check` before the first reboot.
- After Linux enumerates, the helper requires exactly one stage-1 gadget (USB ID `1d6b:0104`) with the
  fastboot serial (`--fastboot-serial`, else `--serial`), exactly one configuration and exactly
  one CDC NCM control interface. It selects that configuration, sends the request and always
  releases the USB handle. A missing, ambiguous or mismatched gadget receives no request and
  cannot produce a pass.
- The runner retries it within the SSH readiness deadline (`--boot-timeout`) until it succeeds
  once; SSH gets the rest of that deadline. `result.json` records it under `windows_ncm`. Every
  call is bounded by its caller, because a Windows driver open or close can stall.
- It runs once per stage-1 boot on the stock kernel (`dev connect`, `dev recovery`, the runner) and
  never on the installed system: running it on a working link stalls UsbDk and leaves the host's
  NCM and storage drivers in an error state. `dev installed` does not send it; `dev reboot` sends it
  once when nothing has answered for 60 s, since the maintenance stage 1 may have started.
- The Windows adapter stays on DHCP. The helper changes no IP, DNS, route or firewall settings;
  driver installation and any earlier manual IP configuration have to be settled beforehand.
  UsbDk briefly disconnects the composite gadget, including its log drive; leave that drive alone
  during the initialization.
- [libusb's Windows documentation](https://github.com/libusb/libusb/wiki/Windows) describes its
  UsbDk backend. The workaround is tested with Windows 11, UsbDk 1.0.22, PyUSB 1.3.1 and
  libusb-package 1.0.30.0 on PICO OS 5.13.7; other combinations need their own acceptance run.

### Explicit boot-partition test

`flash-run` replaces the headset's single `boot` partition with the test image for the run and
then flashes the original back through fastboot. It writes no other partition (not ABL,
recovery, userdata or calibration) and neither unlocks nor relocks the bootloader. The
engineering ABL has to stay installed until the restore is complete.

Before the first reboot, so that any mismatch fails with the headset still in PICO OS, it checks
that:

- `--confirm-device` repeats `--serial`, and the result directory lies outside the backup;
- every file of the backup manifest verifies, and the backup's identity (serial, fingerprint, OEM
  state, SoC ID) matches the live headset;
- the backup holds exactly one, non-slotted boot image, the test image is no larger, and it keeps
  the stock kernel, second stage, recovery DTBO, DTB, header version, page size, name, OS version
  and load addresses;
- `stock-boot.img`, a copy of the backup's boot image pinned in the result directory, matches the
  backup's hash.

Before writing, in fastboot, it checks the pinned image again, requires a known unlocked state,
requires `getvar all` to list exactly `boot`, with the backed-up size and no `boot_a`/`boot_b`
(this ABL has no `getvar has-slot`), and sends the engineering ABL's per-session OEM
authorization: `fastboot oem <token> unlock`, with the token derived from the SoC ID
(`python -m overpressure device token`). That command is not `flashing unlock` and resets nothing.

`result.json` records `boot_restore_required: true` and `manual_restore_commands` before the
write, so a failed, timed-out or interrupted write is restored too. A running stage 1 is asked to
reboot to the bootloader; a headset that comes up in Android with the original identity instead
(a partial write can leave a bootable stock image) is sent there through ADB. The pinned image is
checked again and flashed (`boot_restore: flash_acknowledged`); after a normal reboot the
original Android identity must complete boot (`android_boot_verified`). That is no byte readback
of the stock partition; the [cycle](#acceptance-cycle) adds a full EDL readback. A failed restore
leaves the headset in fastboot when possible and keeps `boot_restore_required: true`.

Killing the host process or losing power can leave stage 1 in `boot`; a normal reboot then starts
stage 1 again, never PICO OS. Once the headset is in fastboot (its deadlines bring it there), the
saved `manual_restore_commands` flash `stock-boot.img` from the result directory; the EDL backup
is the other way back.

### Userspace checks

`dev check` runs these checks on the installed system on Valve's kernel
([userspace.md](userspace.md), [xr.md](xr.md), [session.md](session.md)). What runs in them depends
on the archive the system image was built from, found by the hash the image names, with
`pico4-userspace.manifest.json` beside it.

In this order:

| Check | Runs when | Must hold |
| --- | --- | --- |
| Userspace check | always | `pico4-userspace-check --wait 240` passes: systemd 257 as PID 1, state `running`, no failed units; `/dev` the kernel's devtmpfs with every node; the image read-only and `userdata` the only writable partition; the USB link up as `pico4-usb-gadget` recorded it; logind's `seat0`; the `steamos` user's session, `pico4-session.service` and Valve's session target active; the session user can open its devices; `pico4-fan.service` active and writing its state ([cooling.md](cooling.md#checks)); with GPU firmware in the archive, Turnip's Vulkan readback. The shell and storage assertions (phase 8) then hold on the installed system too. |
| Head IMU | the manifest has sensor data | `pico4-imu stream --rate 500 --seconds 10 --check --orientation`: accelerometer and gyroscope samples with increasing timestamps at the requested 500 Hz |
| Valve's session | the archive has `deckard-steamvr-rel` | after the cool-down, `pico4-valve-session-check --seconds 30`: `gamescope-session.target`, SteamVR and gamescope active and the configured session restored afterwards; SteamVR's compositor runs as a gamescope client without asserts and presents, measured from msm's events, at ≥ 95 % of 90 Hz with at most 1 % late, no stalls, 99 % of its jobs at least 1 ms before their vblank; SteamVR reports the headset and both controllers with finite poses, the controllers with positions |

Each check runs only after the previous one passed and only when the archive contains what it
tests. The thresholds are in [tooling/userspace.py](../tooling/userspace.py). Before Valve's session
check the runner stops the session and waits until SteamVR and the Steam client have exited;
afterwards the session runs again, also after a failed check.

Before Valve's session, the runner reads the hottest thermal zone every 5 s (ignoring readings of 200 °C
and above) and starts once it is below 70 °C, or after 180 s regardless; `valve_session_cooldown`
in `result.json` records the wait and the temperature. The session check waits up to 180 s for
SteamVR's compositor to present, runs 30 s, records the hottest zone (`max_temp_c`) and the fan's
state (`fan`) before and after, and is bounded to 400 s.

### Suspend check

`pico4-suspend-check [--seconds SECONDS]` on the installed system plays a tone on the speakers and records
it on the microphone, suspends through the RTC (20 s by default), and repeats the tone. It passes when PipeWire lives through the suspend, the
kernel counts it, the tone stands 10 dB above the room at its frequency both times, and the two differ by
at most 6 dB. It needs someone near: the tones are audible, and a failed resume needs a hand on the headset.
SSH over USB drops at the wake, so start it detached and read the result afterwards:

```sh
python -m overpressure dev ssh "systemd-run --unit=pico4-suspend-test --no-block pico4-suspend-check"
python -m overpressure dev ssh "cat /run/pico4-suspend-check.json"
```

### Device deadlines

Every test image carries deadlines that its independent `recovery-watch` service enforces
([recovery-watch](../initramfs/usr/libexec/pico4/recovery-watch), policy in
[recovery-policy.sh](../initramfs/usr/libexec/pico4/recovery-policy.sh)), from inittab. It checks every 2 s and reboots to
fastboot when a deadline passes.

- **Connection**, from Linux start: `--boot-timeout` + 30 s (210 s by default); 600 s in the dev
  image. Only the host's verified SSH identity check clears it
  (`/run/pico4/test-authenticated`); USB enumeration, carrier and DHCP traffic do not.
- **Maximum runtime**, from Linux start: `--boot-timeout` + `--report-timeout` + 300 s; 660 s by
  default. 3600 s in the dev image. Nothing clears it, so it also
  covers a host runner that died after authentication. The dev loop renews it instead: each of
  its commands writes the uptime to `/run/pico4/host-contact`, and the deadline then counts
  from that contact ("maximum runtime: no host contact for 3600s").

These deadlines apply even when the image turns off the interactive auto-reboot
(`pico4.autoreboot`, [stage1.md](stage1.md)). A switch that fails before systemd runs also
returns to fastboot. A kernel panic reboots the headset instead of leaving it in Qualcomm
crash-dump mode (stage 1 turns download mode off unless `pico4.ramdump=1`), so a flashed stage 1
starts again and its deadlines apply.

### Failure and timeout behavior

Exit status 0 means every automated check and the return to the original PICO OS passed. Any
failed check or failed recovery gives 1; a successful recovery never erases an earlier failure.
A command counts as failed when it exits nonzero or prints the word `FAILED`.

| Limit | Default | Bounds |
| --- | --- | --- |
| `--boot-timeout` | 180 s | reaching fastboot; each flash; SSH readiness, including the NCM initialization |
| `--report-timeout` | 180 s | the hardware report |
| `--recovery-timeout` | 420 s | the wait for PICO OS; for `flash-run` also the wait for fastboot before the restore |

Every subprocess and HTTP transfer has its own timeout, so a phase can overrun its limit by the
timeout of the command in flight. Preflight's push, run and cleanup are bounded separately (180,
300 and 30 s), as are the stage-2 checks (for example 300 s for the userspace check and 400 s for
Valve's session).

Once the headset has left PICO OS, a failure leads into the recovery phase (a `finally` path):

- With this run's SSH identity verified, the runner saves the boot log (`recovery-boot.log`, best
  effort) and asks stage 1 to reboot, to the bootloader when `boot` must be restored.
- `flash-run` restores stock boot as [described above](#explicit-boot-partition-test).
- If the fastboot serial appears, including after stage 1's own fallback to fastboot, the runner
  requests a normal boot, then waits for the original Android identity and boot completion.
- If neither ADB nor fastboot returns in time, recovery is marked failed and the logs stay for
  manual recovery.

A wrong SSH endpoint is never sent a reboot. The runner does no EDL recovery and no power
cycling. Ctrl+C starts the recovery; a second Ctrl+C can end it, and closing or killing the host
process skips it. A configured but unusable USB link ends in fastboot through the device
deadlines, after which the runner restores PICO OS without a button press. A kernel hard hang
needs a person. Missing hardware cannot produce a pass.

### Result directory

`--out` (default `out/hardware-<random>`) must not exist yet; results are never overwritten. Keep
it private: it holds device identifiers, hardware details and, for `flash-run`, the stock boot
image.

| File | Content |
| --- | --- |
| `result.json` | status; every phase with times and errors; identity, image hashes, device deadlines, recovery and boot-restore state, `manual_restore_commands`, NCM data; rewritten atomically after each change |
| `command-NNNN.json`, `command-NNNN.log` | every subprocess: arguments, timeout and start; its output, partial on timeout |
| `test-boot.img`, `rootfs.tar` | the instrumented image; the source image's userspace for the preflight |
| `ssh_config`, `known_hosts`, `id_ed25519.pub` | the run's SSH settings and pinned host keys; the private key is deleted at the end |
| `preflight.txt` | preflight output |
| `stock-boot.img` | `flash-run`: the pinned stock boot image |
| `storage.txt`, `mounts.txt`, `boot-readback.txt` | block-device inventory, mounts, `flash-run` readback hash; `-userspace` variants from `dev check` |
| `http-status.txt`, `boot.log`, `report.tar.gz`, `dmesg.txt` | HTTP diagnostics and the kernel log |
| `userspace-*` | `dev check`: stage-2 results (`.json`) and logs; the logs are best effort, the results decide |
| `recovery-boot.log` | boot log saved during recovery |

### Preflight only

```sh
python -m overpressure hwtest preflight --serial ADB_SERIAL --rootfs out/pico4-stage1-5.13.7-global-seko-rootfs.tar
```

Runs phases 2 and 4 only: PICO OS keeps running, and no unlock, fastboot or SSH is involved.
`--serial` may be left out when exactly one authorized ADB device is attached, and `--rootfs`
when the newest `out/pico4-stage1-*-rootfs.tar` is the one to check (the window's "Preflight on
PICO OS" step runs it so). The recorded fingerprint and OEM
state select the firmware entry: `python -m overpressure match --fingerprint ... --oem-state ...`.

## Acceptance cycle

`python -m overpressure cycle [--skip-stage] [--keep-abl | --restore-only]`
([tooling/cycle.py](../tooling/cycle.py)) verifies stage 1 and the way back unattended and ends on
stock boot and stock ABL. It takes the headset out of development mode, so tasks do not run it
([Back to stock](#back-to-stock)). Settings come from [`.env`](#settings-for-cycle-and-dev).

1. **Engineering ABL** (skipped with `--skip-stage`): into EDL; a read-only first session
   (`printgpt`) must report `PICO4_EDL_SERIAL`; then
   `python -m overpressure device stage --partition abl --execute` with `PICO4_ENGINEERING_ABL` and
   `PICO4_ENGINEERING_ABL_SHA256`, which checks the hash, writes and reads back
   ([device-preparation.md](device-preparation.md)); EDL reset; PICO OS must finish booting.
2. **Acceptance run**: `python -m overpressure hwtest flash-run` with the `.env` serials and backup,
   `out/pico4-stage1-<PICO4_FIRMWARE>.img` (built beforehand) and `--windows-ncm-python` when
   there is an NCM Python (`PICO4_NCM_PYTHON`, else this one on Windows); results in
   `run/`, bounded to 4 hours. If `result.json` still requires a boot restore, the cycle waits up
   to 45 minutes for the fastboot serial (the device deadlines bring the headset there), checks
   the pinned `stock-boot.img` against its recorded size and SHA-256, and runs the recorded
   `manual_restore_commands`. PICO OS must then finish booting.
3. **Stock boot and stock ABL** (skipped with `--keep-abl`): into EDL; edl-ng reads the whole
   `boot` partition (96 MiB) back. Its output must report `PICO4_EDL_SERIAL`, and the readback
   must match the backup's boot image. Then `python -m overpressure device restore-abl --execute`, with
   readback.
4. **EDL reset**; PICO OS must finish booting.

`--restore-only` runs steps 3 and 4, for example after `python -m overpressure dev restore`.
`--skip-stage` is for a headset whose engineering ABL is still staged, as after the dev loop. To
enter EDL, the cycle uses the attached EDL device (`05c6:9008`) if there is exactly one;
otherwise it sends `adb reboot edl` and waits up to 30 s for exactly one. EDL reads and resets
are bounded to 600 s, staging and ABL restore to 900 s, and each wait for PICO OS to 300 s.

| Outcome | Exit | Meaning |
| --- | --- | --- |
| `PASSED` | 0 | the run passed and every later step succeeded. After a full cycle, `boot` and `abl` match the backup by EDL readback and PICO OS runs with SELinux enforcing; the bootloader stays unlocked. |
| `FAILED` | 1 | the run failed; the cycle's own steps still succeeded, so stock boot and, without `--keep-abl`, stock ABL are restored and verified |
| `STOPPED: <reason>` | 1 | one of the cycle's own steps failed. The cycle stops at once, never repeats a step and prints the evidence directory; the reason says where the headset is when that is known. A boot readback that differs from the backup, or an EDL device that is not `PICO4_EDL_SERIAL`, stops it in EDL with the engineering ABL in place; a changed pinned stock image stops it in fastboot. |

**If PICO OS does not come back.** After the final EDL reset the headset can stay off although
stock boot and stock ABL are already verified. The resets use edl-ng's plain `Reset` mode, the
only one that restarts the device (`Off` powers it down, `Edl` returns to EDL). When PICO OS does
not finish booting within 300 s of a reset, the cycle stops with `hard reset needed` and says
whether the headset is still in EDL or on neither ADB nor EDL. A person has to look at the
headset: a hardware reset (power button held for more than 10 s) starts PICO OS. Do not retry the
cycle or any EDL or fastboot step blindly.

The evidence goes to `out/cycle-<YYYYmmdd-HHMMSS>/`. The readbacks are full partition images;
keep the directory private.

| File | Step |
| --- | --- |
| `edl-identify.log`, `stage-abl.log`, `engineering-abl-readback.bin`, `reset-stage.log` | 1 |
| `run/` (the runner's result directory), `run.log` | 2 |
| `manual-restore.log` | 2, when the cycle restored `boot` |
| `boot-readback.log`, `boot-readback.bin`, `restore-abl.log`, `stock-abl-readback.bin` | 3 |
| `reset-final.log` | 4 |

## Development loop

`python -m overpressure dev COMMAND` ([tooling/devloop.py](../tooling/devloop.py)) works on a headset in
[development mode](#development-mode), whose `recovery` partition holds the dev image. The
everyday attempt installs a system image from the maintenance stage 1 and runs the runner's stage-2
checks on the installed system; `install`, `recovery`, `normal`, `installed` and `restore-partition`
are in [install.md](install.md#install-and-update). Settings come from [`.env`](#settings-for-cycle-and-dev).

| Command | Effect |
| --- | --- |
| `stage-abl` | engineering ABL through EDL with readback, then PICO OS, as in cycle step 1; logs in `out/dev/stage-abl-<time>/` |
| `image` | builds `out/dev/dev-boot.img` and `dev-recovery.img` from `out/pico4-stage1-<PICO4_FIRMWARE>.img`: instrumented like a runner image, with the run identifier `dev-loop`, the dev key and long deadlines ([install.md](install.md#boot-images)) |
| `boot-image` | builds `out/kernel/valve-boot.img`, the installed system's boot image, from Valve's kernel and the header of `dev-recovery.img` (`recovery_image.py --direct --boot`, [install.md](install.md#install-and-update)) |
| `flash` | goes to fastboot (from PICO OS through ADB, from stage 1 or 2 through SSH; up to 180 s), checks the bootloader and partition as `flash-run` does (unlocked, exactly one non-slotted partition of the backed-up size, an image that fits), sends the OEM authorization, writes the dev image to `recovery` and starts it |
| `connect` | forgets the previous host key (stage 1 makes new ones every boot), runs the NCM initialization when there is an NCM Python (`PICO4_NCM_PYTHON`, else this one on Windows; retried for up to 120 s) and logs in to stage 1 within 120 s; the endpoint must report `dev-loop` and the SoC ID of `PICO4_IDENTITY`, and the login clears the connection deadline. It then reads back the partition the running stage 1 came from (`pico4-install hash`): its hash must match `out/dev/dev-boot.img` or `dev-recovery.img` |
| `check` | runs the runner's stage-2 checks, from the userspace check to Valve's session, on the installed system; results in `out/dev/check-<time>/`; exit 0 only if all pass |
| `ssh COMMAND...` | runs the words as one remote command (up to 600 s) and prints the output as text; the remote exit status is not passed on |
| `reboot` | reboots the headset: over SSH from stage 1 or 2, with `fastboot reboot` when it waits in fastboot (no write); then waits for another boot than the one it rebooted (its boot ID, no fixed wait) and connects to what started, the maintenance stage 1 while `misc` holds its request, else the installed system; it sends the NCM initialization once when nothing has answered for 60 s |
| `restore` | leaves development mode. With the system installed, as in [install.md](install.md#back-to-stock). Otherwise it checks the backup's stock boot image against its manifest and the partitions as `flash` does, writes it to `boot` through fastboot and waits up to 300 s for PICO OS. The ABL stays engineering |

- `flash` and `restore` need the engineering ABL. Unlike `flash-run`
  they do not compare the backup's identity record with the live headset; the serials in `.env`
  and the SoC ID checked at login tie them to it.
- The dev image's connection deadline is 600 s and its maximum runtime 3600 s from the last
  dev command (each one renews `/run/pico4/host-contact`): stage 1 waits for the next
  attempt instead of rebooting, a headset left alone returns to fastboot an hour after the last
  command, a session in use with commands in between keeps running. From fastboot, `dev reboot`
  starts what `misc` and `boot` select.
- Files for the running system go over `ssh` or `scp` directly, as do binary files from the
  headset, which `dev ssh` would decode as text:

  ```sh
  ssh -i out/dev/id_ed25519 -o UserKnownHostsFile=out/dev/known_hosts root@172.16.42.1 "cat /run/FILE" > FILE
  ```

`out/dev/` holds the loop's state:

| File | Content |
| --- | --- |
| `id_ed25519`, `id_ed25519.pub` | the dev key, created once; the dev image trusts it, so a new key needs `image` and `flash` again |
| `known_hosts` | stage 1's host key, replaced at each `connect` |
| `dev-boot.img`, `dev-recovery.img`, `dev-image.json` | the dev image and its whole-partition form for `recovery`; their hashes, build metadata and device deadlines |
| `installed.json` | written by `install`: PICO OS's system is gone until `restore` |
| `rootfs.tar` | the image's userspace, a by-product of the instrumentation |
| `state.json` | the archive the installed system was built from (hash, features, boot time); written when `install`, `normal`, `installed` or `reboot` reach the system, removed by `recovery`, `reboot` and `restore` |
| `check-<time>/` | `check` results: `result.json`, command logs and the `userspace-*` files |
| `stage-abl-<time>/` | `stage-abl` logs and readback |

## Host tests

`python -m unittest`, or `make test`, which also runs
[tests/test_recovery_policy.sh](../tests/test_recovery_policy.sh) for the deadline policy, needs no
hardware ([development.md](development.md)). With simulated devices,
[tests/test_hardware.py](../tests/test_hardware.py) covers the runner: success, failed preflight,
locked and unsupported bootloaders, a wrong SSH identity, a wrong build, writable partitions,
broken HTTP and failed recovery; for `flash-run` wrong or tampered backups, mismatched kernels and
partition layouts, partial and interrupted writes, a changed restore image, readback mismatches
and failed restores; the stage-2 checks, the order and
failures of the NCM initialization and artifact preservation. What the runner is built on has its
own tests: [tests/test_evidence.py](../../../tests/test_evidence.py) (recorded commands, their
failures and timeouts, steps, bounded waits and fetches) and
[tests/test_storage.py](../../../tests/test_storage.py) (the read-only storage policy).
[tests/test_headset.py](../tests/test_headset.py) covers the cycle's order and refusals (a boot
partition that differs from the backup, another EDL device, a changed pinned image, no EDL device)
and the dev loop's login, install, reboot and restore; [tests/test_windows_ncm.py](../../../tests/test_windows_ncm.py)
covers the NCM helper. They do not replace a run on the headset; the QEMU test covers stage 1 only.

## Support bundle

`python -m overpressure dev support-bundle` (or `pico4-support-bundle` on the headset) collects what a bug report
needs into one archive in `out/support`: the version, the journals of this boot and the last, the kernel log,
pstore, failed units, coredumps' list, Steam's and SteamVR's logs, packages, power, temperatures and network state.
It works with the session down. Serials (the SoC's also in hex, as FastRPC names its test signature), MAC and IP
addresses, Steam IDs, account and network names are masked;
Steam's config and login data and the network connections are not read into it. It is plain text, and the command
lists its files: read it before sending it.
