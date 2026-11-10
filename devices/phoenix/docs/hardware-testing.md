# Hardware testing

**TL;DR.** Three commands (`python -m overpressure ...`) test a prepared headset
([device-preparation.md](device-preparation.md)). `dev` is the everyday route: the headset stays in
[development mode](#development-mode), each attempt installs a system image and runs the stage-2 checks. `cycle`
is the way back to stock, not a step of every task.

| Command | Code | Writes | Ends in |
| --- | --- | --- | --- |
| `dev ...` | [tooling/devloop.py](../tooling/devloop.py) | `abl` (`stage-abl`), `recovery` (`flash`), `boot`, `super`, `userdata` (`install`), until `dev restore` | development mode, kept between tasks; PICO OS after `dev restore` |
| `hwtest preflight` | [tooling/hardware_test.py](../tooling/hardware_test.py) | temporary files under `/data/local/tmp`, removed again | PICO OS, never left |
| `hwtest flash-run` | the same | `boot` (stage 1 on the stock kernel), restored afterwards | the original PICO OS |
| `cycle` | [tooling/cycle.py](../tooling/cycle.py) | `abl` and `boot`, both restored and verified by EDL readback; unattended | PICO OS on stock boot and stock ABL |

Every command that touches the headset first shows the disclaimer and asks y or n; `--accept-disclaimer`
(`python -m overpressure --accept-disclaimer dev ...`) or `OVERPRESSURE_ACCEPT_DISCLAIMER=1` accepts it for
unattended runs. Every ADB and fastboot action names the headset's serial and every wait is bounded. Linux
writes the headset's storage only through `pico4-install` and the installed system's data partition;
device-side deadlines return the headset to fastboot when the host stops answering.

`flash-run` needs the engineering ABL: the stock ABL reports its unlock state as `DeviceUnlocked=true`, which
the unlock check refuses. The engineering ABL has no `fastboot boot` (`unknown command` after the transfer),
so every acceptance boot writes `boot` and restores it.

## Requirements

- **Headset** prepared as in [device-preparation.md](device-preparation.md): identity record, verified EDL
  backup, unlocked bootloader, engineering ABL, Firehose loader. `hwtest`, `cycle` and `dev stage-abl`
  start from PICO OS with USB debugging authorized, `dev flash` from PICO OS or development mode, the other
  `dev` commands from development mode. `adb reboot edl` works from PICO OS and EDL needs no buttons.
- **Host**: Python 3.9+, `adb`, `fastboot` and edl-ng (`python -m overpressure tools` fetches them), OpenSSH
  (`ssh`, `ssh-keygen`), a USB network driver for the NCM link and a route to 172.16.42.1; on Windows the
  [NCM initialization](#windows-ncm-initialization) for stage 1. The tools install no drivers and change no
  host network settings.
- **Images** ([development.md](development.md)): `out/pico4-stage1-<firmware>.img` from
  `python -m overpressure build --firmware <firmware>` (the entry `python -m overpressure match` finds); for
  the installed system, the images in [install.md](install.md#install-and-update).
- **One run at a time** against one headset.

## Settings for cycle and dev

`cycle` and `dev` read `.env` at the repository root ([tooling/localconfig.py](../tooling/localconfig.py));
[.env.example](../../../.env.example) lists the keys. Environment variables of the same names override it.
Lines are `KEY=VALUE`, `#` starts a comment, quotes around a value are removed, relative paths are relative
to the repository root. A command that needs an empty key stops with `set PICO4_... in .env`. `hwtest` takes
command-line options instead; the cycle passes these settings to it.

| Key | Default | Meaning |
| --- | --- | --- |
| `PICO4_ADB_SERIAL` | required | serial in `adb devices` |
| `PICO4_FASTBOOT_SERIAL` | required | serial in `fastboot devices`; also the USB gadget's serial in stage 1 and the installed system |
| `PICO4_EDL_SERIAL` | required for EDL writes | `Serial Number` in edl-ng's output; no ABL is written to another device |
| `PICO4_IDENTITY` | `backup/device.json` | identity record (`device probe`); its SoC ID derives the OEM authorization and is checked at login |
| `PICO4_BACKUP` | `backup/stock` | verified EDL backup with the stock `boot` and `abl` |
| `PICO4_LOADER` | `assets/firehose.elf` | signed Firehose loader |
| `PICO4_ENGINEERING_ABL` | `assets/engineering-abl.elf` | engineering ABL image |
| `PICO4_ENGINEERING_ABL_SHA256` | the hash of the file `device fetch-unlock-files` fetches | its expected SHA-256; the cycle refuses an empty one |
| `PICO4_EDL` | the fetched edl-ng, else `PATH` | edl-ng executable |
| `PICO4_FIRMWARE` | `5.13.7-global-seko` | entry of [tooling/firmware.json](../tooling/firmware.json); selects `out/pico4-stage1-<firmware>.img` |
| `PICO4_SSH_PUBLIC_KEY` | empty | the owner's own public key file, enrolled on the installed system besides this PC's key |
| `PICO4_NCM_PYTHON` | on Windows the Python that runs overpressure when it has pyusb and libusb-package | Python for the NCM initialization |
| `PICO4_PLATFORM_TOOLS` | the fetched platform-tools, else `PATH` | folder with `adb` and `fastboot` |
| `PICO4_SSH`, `PICO4_SSH_KEYGEN` | `ssh`, `ssh-keygen` | OpenSSH executables |

## Development mode

Between tasks the headset stays in development mode: the engineering ABL in `abl`, the dev image as the
maintenance stage 1 (stock kernel) in `recovery`, and the installed system on Valve's kernel in `boot`,
`super` and `userdata` ([install.md](install.md)). `dev recovery` keeps every boot in the maintenance stage 1
until `dev normal`. Nothing is restored when a task ends. Enter it once, from PICO OS, with the images of
[install.md](install.md#install-and-update) built:

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

- The session runs only on Valve's kernel, so `dev check` runs on the installed system.
- A changed stage-1 image needs `dev image` and `dev flash`; the installed system's boot image holds no stage 1.
- A new kernel: `dev boot-image`, `dev recovery`, `dev install --boot PATH`.
- After a device deadline left the headset in fastboot, `dev reboot` starts what `misc` and `boot` select,
  without a write.
- `hwtest flash-run` and `cycle` leave the stock boot image in `boot` and so end development mode; with the
  system installed they cannot start PICO OS (its system is gone), so `dev restore` comes first.

### Back to stock

Only when PICO OS is needed, or to verify the way back (for example before a release). The installer's
restore workflow (`python -m overpressure restore`, [install.md](install.md#back-to-stock)) runs the same:

```sh
python -m overpressure dev restore          # PICO OS's system, boot and recovery from the backup; the ABL stays engineering
python -m overpressure cycle --restore-only # EDL readback of boot, stock ABL, PICO OS
```

`python -m overpressure cycle --skip-stage` after `dev restore` also verifies stage 1 and ends on stock.

## Acceptance runner

`python -m overpressure hwtest preflight|flash-run`. A pass establishes a Linux shell on the stock kernel with
USB-network diagnostics, read-only storage and a verified return to the same stock PICO OS. Audio is not
tested; the session's checks run through `dev check`. `flash-run` never unlocks the bootloader, resets user
data or modifies the source image.

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
| `--out` | `out/hardware-<random>` | new [result directory](#result-directory) |
| `--adb` | `adb` | executable, checked before anything runs |
| `--rootfs` | `preflight` only: the newest `out/pico4-stage1-*-rootfs.tar` | the image's userspace |
| `--image`, `--backup` | required for `flash-run` | stage-1 image; verified EDL backup |
| `--confirm-device` | required for `flash-run` | repeats `--serial` to authorize the boot write |
| `--fastboot-serial` | `--serial` | fastboot serial if it differs; also the stage-1 gadget's USB serial |
| `--host` | `172.16.42.1` | the headset's USB IPv4 address |
| `--fastboot`, `--ssh`, `--ssh-keygen` | the names on `PATH` | executables, checked before anything runs |
| `--windows-ncm-python` | off | Windows only: [NCM initialization](#windows-ncm-initialization) |
| `--boot-timeout`, `--report-timeout`, `--recovery-timeout` | 180, 180, 420 | [limits](#failure-and-timeout-behavior) in seconds |

### Phases

`result.json` records each phase under `checks`:

1. **prepare test boot image.** With `--windows-ncm-python`, the NCM helper's `--check`. A fresh ed25519 key.
   The source image must pass its header checksum, be a stage-1 build, keep read-only mode and the QEMU
   self-test off (`pico4.rw`, `pico4.selftest`) and start `recovery-watch`. The runner derives
   `test-boot.img`: root's `authorized_keys` with the run's key, a random run identifier (in
   `/etc/pico4/test-run` and `pico4.test_run=`), the [device deadlines](#device-deadlines), no boot
   signature. Kernel, DTBs and boot layout are kept and compared after repacking; both SHA-256 are recorded.
2. **identify stock headset.** After boot completion: the fingerprint (an original PICO 4's,
   `Pico/Phoenix.../PICOA8110:10/...`), OEM state and SoC ID, the baseline for every later identity check.
3. **verify and pin stock boot restoration image** (`flash-run`, [below](#explicit-boot-partition-test)).
4. **userspace preflight.** Pushes `rootfs.tar` and [scripts/preflight-device.sh](../scripts/preflight-device.sh)
   to `/data/local/tmp/pico4-stage1-<run>`, runs the image's Alpine binaries on the stock kernel as the ADB
   shell user. It must exit 0 and print exactly one `PREFLIGHT: N passed, 0 failed` with N > 0
   (`preflight.txt`). The files are removed after a failure too.
5. **enter unlocked fastboot.** `adb reboot bootloader`; within `--boot-timeout` the fastboot serial appears
   and `fastboot oem device-info` (or `getvar unlocked`) reports a known unlocked state.
6. **flash test boot image**, bounded by `--boot-timeout`.
7. **SSH identity.** Within `--boot-timeout` (after the NCM initialization if enabled), SSH with the run's
   key must return this run's identifier and the baseline SoC ID. Only then is the connection deadline
   cleared.
8. **Linux shell and read-only storage.** Build metadata and kernel release match the image, machine
   `aarch64`, UID 0. `/sys/class/block/*/ro` is 1 for every disk and partition (device-mapper included;
   loop, ram, zram, nbd excluded); root is RAM-backed; no `/dev/*` file system is mounted writable. These
   checks only observe. For `flash-run`, `boot` read back over the test image's length hashes to its SHA-256.
9. **HTTP and diagnostic report.** `cgi-bin/status` is stage 1's, `cgi-bin/log?boot` names this run. The
   runner waits up to `--report-timeout` for init's report; `cgi-bin/report` must hold `summary.txt`,
   `system.txt` (naming this run) and `dmesg.txt`, each 1 B to 16 MiB, inspected without extracting
   device-controlled paths. HTTP bypasses proxies, rejects redirects, limits each response to 32 MiB and 15 s.
10. **return to original PICO OS**, after a pass or a failure ([recovery](#failure-and-timeout-behavior)):
    the original ADB serial with boot completed and the baseline identity; fastboot alone does not count.

`test-boot.img` is the supplied image plus authentication and identification; a pass is not a claim that the
unmodified image booted byte for byte. The run's private key is deleted at the end. Host keys are pinned into
the run's own `known_hosts` at the first connection; no passwords, personal SSH configuration or global
known-hosts changes are involved.

### Windows NCM initialization

On the stock kernel (PICO OS 5.13.7: the maintenance stage 1 and the runner's images), the NCM function
rejects Windows' NCM packets with `Wrong NDP SIGN` in its kernel log: no DHCP, SSH or HTTP. Sending the CDC
request `SET_CRC_MODE(0)` through UsbDk, then releasing the device to Windows 11's in-box UsbNcm driver, fixes
all three without a kernel change: the 4.19 `f_ncm` recomputes the NDP signature on every CDC class request
to its control interface, and Windows' driver sends none. The stock kernel has no RNDIS, ECM or EEM function
to use instead. Valve's kernel (the installed system) takes Windows 11's driver as it is.

[overpressure/windows_ncm.py](../../../overpressure/windows_ncm.py) does it; `--windows-ncm-python` (runner) or
`PICO4_NCM_PYTHON` (cycle, dev loop) enables it. Install [UsbDk](https://github.com/daynix/UsbDk/releases)
(`winget install daynix.UsbDk`, as administrator; the helper loads `UsbDkHelper.dll` from
`%ProgramFiles%\UsbDk Runtime Library`) and run overpressure on a Python with pyusb and libusb-package (the
start scripts' and `uv sync`'s have them). `python -m overpressure doctor` checks both ("NCM Python and
UsbDk"). The helper's `--check` tests them without a device; `--serial FASTBOOT_SERIAL` initializes a running
stage 1 by hand.

- It requires exactly one stage-1 gadget (`1d6b:0104`) with the fastboot serial, one configuration and one
  CDC NCM control interface; anything else gets no request and cannot pass. It always releases the handle.
- The runner retries it within `--boot-timeout` until it succeeds once (`windows_ncm` in `result.json`).
  Every call is bounded: a Windows driver open or close can stall.
- It runs once per stage-1 boot (`dev connect`, `dev recovery`, the runner) and **never on the installed
  system**: on a working link it stalls UsbDk and leaves the host's NCM and storage drivers in an error
  state. `dev installed` never sends it; `dev reboot` sends it once when nothing has answered for 60 s.
- The Windows adapter stays on DHCP; the helper changes no IP, DNS, route or firewall settings. UsbDk briefly
  disconnects the composite gadget, log drive included: leave that drive alone meanwhile.
- Tested with Windows 11, UsbDk 1.0.22, PyUSB 1.3.1 and libusb-package 1.0.30.0 on PICO OS 5.13.7
  ([libusb's UsbDk backend](https://github.com/libusb/libusb/wiki/Windows)).

### Explicit boot-partition test

`flash-run` writes the test image to the single `boot` partition and flashes the original back through
fastboot. It writes no other partition and neither unlocks nor relocks. The engineering ABL stays installed
until the restore is complete.

Before the first reboot (so a mismatch fails in PICO OS) it checks: `--confirm-device` repeats `--serial`
and the result directory lies outside the backup; every file of the backup manifest verifies and the backup's
identity (serial, fingerprint, OEM state, SoC ID) matches the live headset; the backup holds exactly one
non-slotted boot image, the test image is no larger and keeps the stock kernel, second stage, recovery DTBO,
DTB, header version, page size, name, OS version and load addresses; `stock-boot.img`, pinned in the result
directory, matches the backup's hash.

In fastboot it checks the pinned image again, requires a known unlocked state and `getvar all` listing exactly
`boot` with the backed-up size and no `boot_a`/`boot_b` (this ABL has no `getvar has-slot`), and sends the
engineering ABL's per-session authorization `fastboot oem <token> unlock` (token from the SoC ID,
`python -m overpressure device token`; not `flashing unlock`, resets nothing).

`result.json` records `boot_restore_required: true` and `manual_restore_commands` before the write, so a
failed or interrupted write is restored too. A running stage 1 is asked to reboot to the bootloader; a
headset that comes up in Android with the original identity is sent there through ADB. The pinned image is
checked and flashed (`boot_restore: flash_acknowledged`), then the original Android must complete boot
(`android_boot_verified`). This is no byte readback; the [cycle](#acceptance-cycle) adds a full EDL readback.
A failed restore leaves the headset in fastboot when possible and keeps `boot_restore_required: true`.

Killing the host process or losing power can leave stage 1 in `boot`: every normal boot then starts stage 1.
Once the deadlines bring the headset to fastboot, the saved `manual_restore_commands` flash `stock-boot.img`;
the EDL backup is the other way back.

### Userspace checks

`dev check` runs these on the installed system ([userspace.md](userspace.md), [xr.md](xr.md),
[session.md](session.md)), in order, each only after the previous one passed and only when the archive the
image was built from (found by the hash the image names, `pico4-userspace.manifest.json` beside it) contains
what it tests:

| Check | Runs when | Must hold |
| --- | --- | --- |
| Userspace check | always | `pico4-userspace-check --wait 240`: systemd 257 as PID 1, state `running`, no failed units; `/dev` the kernel's devtmpfs; the image read-only and `userdata` the only writable partition; the USB link up as `pico4-usb-gadget` recorded it; logind's `seat0`; the `steamos` user's session, `pico4-session.service` and Valve's session target active; the session user can open its devices; `pico4-fan.service` active and writing its state ([cooling.md](cooling.md#checks)); with GPU firmware, Turnip's Vulkan readback. Phase 8's shell and storage assertions hold too. |
| Head IMU | the manifest has sensor data | `pico4-imu stream --rate 500 --seconds 10 --check --orientation`: accelerometer and gyroscope samples with increasing timestamps at 500 Hz |
| Valve's session | the archive has `deckard-steamvr-rel` | `pico4-valve-session-check --seconds 30`: `gamescope-session.target`, SteamVR and gamescope active, the configured session restored; SteamVR's compositor runs as a gamescope client without asserts and presents (msm's events) at ≥ 95 % of 90 Hz, at most 1 % late, no stalls, 99 % of its jobs at least 1 ms before their vblank; the headset and both controllers with finite poses, the controllers with positions |

Thresholds: [tooling/userspace.py](../tooling/userspace.py) and the headset's
[session-performance](../userspace/rootfs/usr/libexec/pico4/session-performance). Before Valve's session the
runner stops the session and waits for SteamVR and the Steam client to exit; afterwards the session runs
again, also after a failure. The cool-down reads the hottest thermal zone every 5 s (ignoring ≥ 200 °C) and
starts below 70 °C or after 180 s (`valve_session_cooldown`). The check waits up to 180 s for the compositor
to present, runs 30 s, records `max_temp_c` and the fan's state before and after, and is bounded to 400 s.

### Suspend check

`pico4-suspend-check [--seconds SECONDS]` plays a tone on the speakers and records it on the microphone,
suspends through the RTC (20 s by default), and repeats. It passes when PipeWire survives, the kernel counts
the suspend, the tone is 10 dB above the room at its frequency both times and the two differ by at most
6 dB. It needs someone near (audible tones; a failed resume needs a hand). SSH over USB drops at the wake, so
start it detached:

```sh
python -m overpressure dev ssh "systemd-run --unit=pico4-suspend-test --no-block pico4-suspend-check"
python -m overpressure dev ssh "cat /run/pico4-suspend-check.json"
```

### Device deadlines

Every test image's `recovery-watch` ([recovery-watch](../initramfs/usr/libexec/pico4/recovery-watch), policy
in [recovery-policy.sh](../initramfs/usr/libexec/pico4/recovery-policy.sh)) checks every 2 s and reboots to
fastboot when a deadline passes:

| Deadline | Runner image | Dev image | Cleared or renewed by |
| --- | --- | --- | --- |
| Connection, from Linux start | `--boot-timeout` + 30 s (210 s) | 600 s | only the host's verified SSH identity check (`/run/pico4/test-authenticated`); USB enumeration, carrier and DHCP do not count |
| Maximum runtime, from Linux start | `--boot-timeout` + `--report-timeout` + 300 s (660 s) | 3600 s from the last `dev` command | nothing in the runner image (covers a runner that died after login); each `dev` command writes `/run/pico4/host-contact` |

They apply even when the image turns off the interactive auto-reboot (`pico4.autoreboot`,
[stage1.md](stage1.md)). A kernel panic reboots instead of entering Qualcomm crash-dump mode (unless
`pico4.ramdump=1`), so a flashed stage 1 starts again and its deadlines apply.

### Failure and timeout behavior

Exit 0 means every check and the return to the original PICO OS passed; any failed check or recovery gives 1,
and a successful recovery never erases an earlier failure. A command fails when it exits nonzero or prints
`FAILED`.

| Limit | Default | Bounds |
| --- | --- | --- |
| `--boot-timeout` | 180 s | reaching fastboot; each flash; SSH readiness, NCM initialization included |
| `--report-timeout` | 180 s | the hardware report |
| `--recovery-timeout` | 420 s | the wait for PICO OS; for `flash-run` also for fastboot before the restore |

Every subprocess and HTTP transfer has its own timeout, so a phase can overrun by the command in flight.
Preflight's push, run and cleanup are bounded to 180, 300 and 30 s; the userspace check to 300 s, Valve's
session to 400 s.

Once the headset left PICO OS, a failure enters recovery (a `finally` path): with this run's SSH identity
verified, the runner saves `recovery-boot.log` and asks stage 1 to reboot (to the bootloader when `boot` must
be restored); `flash-run` restores stock boot as [above](#explicit-boot-partition-test); when the fastboot
serial appears (also after stage 1's own fallback), it requests a normal boot and waits for the original
Android; if neither ADB nor fastboot returns in time, recovery fails and the logs stay.

A wrong SSH endpoint is never sent a reboot. No EDL recovery, no power cycling. Ctrl+C starts the recovery, a
second Ctrl+C can end it; killing the host process skips it. An unusable USB link ends in fastboot through the
deadlines, after which the runner restores PICO OS without a button press. A kernel hard hang needs a person.

### Result directory

`--out` must not exist yet. Keep it private: it holds device identifiers and, for `flash-run`, the stock boot
image.

| File | Content |
| --- | --- |
| `result.json` | status; every phase with times and errors; identity, image hashes, deadlines, recovery and boot-restore state, `manual_restore_commands`, NCM data; rewritten atomically |
| `command-NNNN.json`, `command-NNNN.log` | every subprocess: arguments, timeout, start; output, partial on timeout |
| `test-boot.img`, `rootfs.tar` | the instrumented image; the source image's userspace |
| `ssh_config`, `known_hosts`, `id_ed25519.pub` | the run's SSH settings and pinned host keys |
| `preflight.txt`, `stock-boot.img` | preflight output; the pinned stock boot image (`flash-run`) |
| `storage.txt`, `mounts.txt`, `boot-readback.txt` | block devices, mounts, readback hash; `-userspace` variants from `dev check` |
| `http-status.txt`, `boot.log`, `report.tar.gz`, `dmesg.txt` | HTTP diagnostics and the kernel log |
| `userspace-*` | `dev check`: stage-2 results (`.json`, which decide) and logs (best effort) |
| `recovery-boot.log` | boot log saved during recovery |

### Preflight only

```sh
python -m overpressure hwtest preflight --serial ADB_SERIAL --rootfs out/pico4-stage1-5.13.7-global-seko-rootfs.tar
```

Phases 2 and 4 only: PICO OS keeps running, no unlock, fastboot or SSH. `--serial` may be left out with one
authorized ADB device, `--rootfs` for the newest tar (the window's "Preflight on PICO OS" step does so). The
recorded fingerprint and OEM state select the firmware entry (`python -m overpressure match`).

## Acceptance cycle

`python -m overpressure cycle [--skip-stage] [--keep-abl | --restore-only]` verifies stage 1 and the way back
unattended and ends on stock boot and stock ABL, so tasks do not run it ([Back to stock](#back-to-stock)).
Settings come from [`.env`](#settings-for-cycle-and-dev).

1. **Engineering ABL** (skipped with `--skip-stage`, as after the dev loop): into EDL; a read-only `printgpt`
   must report `PICO4_EDL_SERIAL`; `device stage --partition abl --execute` with hash check, write and
   readback; EDL reset; PICO OS must finish booting.
2. **Acceptance run**: `hwtest flash-run` with the `.env` serials and backup,
   `out/pico4-stage1-<PICO4_FIRMWARE>.img` and the NCM Python if there is one; results in `run/`, bounded to
   4 hours. If `result.json` still requires a boot restore, the cycle waits up to 45 min for the fastboot
   serial, checks the pinned `stock-boot.img` against its recorded size and SHA-256 and runs
   `manual_restore_commands`. PICO OS must then finish booting.
3. **Stock boot and stock ABL** (skipped with `--keep-abl`): into EDL; edl-ng reads all of `boot` (96 MiB),
   which must come from `PICO4_EDL_SERIAL` and match the backup; then `device restore-abl --execute` with
   readback.
4. **EDL reset** (edl-ng's `Reset` mode, the only one that restarts: `Off` powers down, `Edl` returns to
   EDL); PICO OS must finish booting.

`--restore-only` runs steps 3 and 4, for example after `dev restore`. To enter EDL the cycle uses the one
attached EDL device (`05c6:9008`), else sends `adb reboot edl` and waits up to 30 s for exactly one. EDL reads
and resets are bounded to 600 s, staging and ABL restore to 900 s, each wait for PICO OS to 300 s.

| Outcome | Exit | Meaning |
| --- | --- | --- |
| `PASSED` | 0 | the run and every later step passed. After a full cycle `boot` and `abl` match the backup by EDL readback and PICO OS runs with SELinux enforcing; the bootloader stays unlocked |
| `FAILED` | 1 | the run failed, the cycle's own steps succeeded: stock boot and (without `--keep-abl`) stock ABL are restored and verified |
| `STOPPED: <reason>` | 1 | one of the cycle's own steps failed; it stops at once, never repeats a step, prints the evidence directory and says where the headset is. A boot readback that differs, or another EDL device, stops it in EDL with the engineering ABL in place; a changed pinned image stops it in fastboot |

**If PICO OS does not come back** within 300 s of a reset, the cycle stops with `hard reset needed` and says
whether the headset is in EDL or on neither ADB nor EDL. A person looks at the headset: holding the power
button for more than 10 s starts PICO OS. Do not retry the cycle or any EDL or fastboot step blindly.

Evidence goes to `out/cycle-<YYYYmmdd-HHMMSS>/` (full partition readbacks: keep it private):

| File | Step |
| --- | --- |
| `edl-identify.log`, `stage-abl.log`, `engineering-abl-readback.bin`, `reset-stage.log` | 1 |
| `run/` (the runner's result directory), `run.log`; `manual-restore.log` when the cycle restored `boot` | 2 |
| `boot-readback.log`, `boot-readback.bin`, `restore-abl.log`, `stock-abl-readback.bin` | 3 |
| `reset-final.log` | 4 |

## Development loop

`python -m overpressure dev COMMAND` works on a headset in [development mode](#development-mode). `install`,
`recovery`, `normal`, `installed` and `restore-partition` are in [install.md](install.md#install-and-update).
Settings come from [`.env`](#settings-for-cycle-and-dev). All but `image` and `boot-image` touch the headset.

| Command | Effect |
| --- | --- |
| `stage-abl` | engineering ABL through EDL with readback, then PICO OS (cycle step 1); logs in `out/dev/stage-abl-<time>/` |
| `image` | builds `out/dev/dev-boot.img` and `dev-recovery.img` from `out/pico4-stage1-<PICO4_FIRMWARE>.img`: a runner image with the run identifier `dev-loop`, the dev key and long deadlines ([install.md](install.md#boot-images)) |
| `boot-image` | builds `out/kernel/valve-boot.img`, the installed system's boot image, from Valve's kernel and the header of `dev-recovery.img` (`recovery_image.py --direct --boot`) |
| `flash` | to fastboot (from PICO OS by ADB, from stage 1 or 2 by SSH; up to 180 s), the partition checks of `flash-run` (unlocked, one non-slotted partition of the backed-up size, an image that fits), the OEM authorization, the dev image into `recovery`, then starts it |
| `connect` | forgets the old host key, runs the NCM initialization when there is an NCM Python (up to 120 s) and logs in within 120 s; the endpoint must report `dev-loop` and the SoC ID of `PICO4_IDENTITY` (this clears the connection deadline); the partition stage 1 came from must hash (`pico4-install hash`) to `dev-boot.img` or `dev-recovery.img` |
| `check` | the runner's [stage-2 checks](#userspace-checks) on the installed system; results in `out/dev/check-<time>/`; exit 0 only if all pass |
| `ssh COMMAND...` | the words as one remote command (up to 600 s), output as text; the remote exit status is not passed on |
| `reboot` | reboots over SSH, or with `fastboot reboot` from fastboot (no write); waits for another boot ID and connects to what started (the maintenance stage 1 while `misc` holds its request, else the installed system); NCM initialization once after 60 s of silence |
| `restore` | leaves development mode: with the system installed as in [install.md](install.md#back-to-stock) (`--installed` when this PC did not record the install); otherwise the backup's stock boot image, checked as in `flash`, into `boot`, and up to 300 s for PICO OS. The ABL stays engineering |
| `release-check [--attended]` | the automated part of [release-checklist.md](release-checklist.md); `--attended` adds what needs someone near |
| `support-bundle [--output PATH]` | the [support bundle](#support-bundle) |

- `flash` and `restore` need the engineering ABL. Unlike `flash-run` they do not compare the backup's
  identity with the live headset; the `.env` serials and the SoC ID checked at login tie them to it.
- The dev image waits for the next command instead of rebooting; left alone, it returns to fastboot an hour
  after the last `dev` command (plain `ssh` does not renew it).
- Files to the system, and binary files from it (`dev ssh` decodes text), go over `ssh`/`scp` directly:

  ```sh
  ssh -i out/dev/id_ed25519 -o UserKnownHostsFile=out/dev/known_hosts root@172.16.42.1 "cat /run/FILE" > FILE
  ```

`out/dev/` holds the loop's state:

| File | Content |
| --- | --- |
| `id_ed25519`, `id_ed25519.pub` | the dev key, created once; a new key needs `image` and `flash` again |
| `known_hosts` | stage 1's host key, replaced at each `connect` |
| `dev-boot.img`, `dev-recovery.img`, `dev-image.json` | the dev image, its whole-partition form for `recovery`; hashes, build metadata, deadlines |
| `installed.json` | written by `install`: PICO OS's system is gone until `restore` |
| `rootfs.tar` | the image's userspace, a by-product of the instrumentation |
| `state.json` | the archive the installed system was built from (hash, features, boot time); written when `install`, `normal`, `installed` or `reboot` reach the system, removed by `recovery`, `reboot` and `restore` |
| `check-<time>/`, `stage-abl-<time>/` | `check` results; `stage-abl` logs and readback |

## Host tests

`python -m unittest` (or `make test`, which adds [tests/test_recovery_policy.sh](../tests/test_recovery_policy.sh))
needs no hardware ([development.md](development.md)). With simulated devices,
[tests/test_hardware.py](../tests/test_hardware.py) covers the runner (preflight, bootloader states, SSH
identity, storage, HTTP, tampered backups, partial writes, restores, the stage-2 checks, the NCM order) and
[tests/test_headset.py](../tests/test_headset.py) the cycle's order and refusals and the dev loop.
[tests/test_evidence.py](../../../tests/test_evidence.py), [tests/test_storage.py](../../../tests/test_storage.py)
and [tests/test_windows_ncm.py](../../../tests/test_windows_ncm.py) cover the recorded runs, the read-only
storage policy and the NCM helper. They do not replace a run on the headset.

## Support bundle

`python -m overpressure dev support-bundle` (or `pico4-support-bundle` on the headset) collects what a bug
report needs into one archive in `out/support`: version, the journals of this boot and the last, kernel log,
pstore, failed units, the coredumps' list, Steam's and SteamVR's logs, packages, power, temperatures and
network state. It works with the session down. Serials (the SoC's also in hex), MAC and IP addresses, Steam
IDs, account and network names are masked; Steam's config and login data and the network connections are not
read. It is plain text and the command lists its files: read it before sending it.
