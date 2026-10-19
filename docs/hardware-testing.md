# Hardware testing

Three commands run the POC on a prepared headset ([device-preparation.md](device-preparation.md)).
The acceptance runner, `python -m pico4 hwtest`, boots one instrumented stage-1 image, checks it
and, on request, the native graphics or the SteamOS-compatible userspace up to Valve's VR
session, then returns the headset to its original PICO OS. The acceptance cycle,
`python -m pico4 cycle`, runs one `flash-run` unattended between staging the engineering ABL and
restoring stock boot and stock ABL, both verified by EDL readback. The development loop,
`python -m pico4 dev`, keeps a dev image in the `boot` partition, so that each attempt only
streams a userspace archive and runs the stage-2 checks. Iterate with the dev loop and finish
with one cycle. Every ADB and fastboot action names the headset's serial and every wait is
bounded; Linux never writes the headset's storage, and device-side deadlines return the headset
to fastboot when the host stops answering.

## Overview

| Command | Writes | Ends in | Use |
| --- | --- | --- | --- |
| `hwtest preflight` | temporary files under `/data/local/tmp`, removed again | PICO OS, never left | the image's userspace binaries on the stock kernel |
| `hwtest run` | nothing: RAM boot | the original PICO OS | acceptance where `fastboot boot` works |
| `hwtest flash-run` | `boot`, restored afterwards | the original PICO OS | acceptance on the PICO 4 |
| `cycle` | `abl` and `boot`, both restored | PICO OS on stock boot and stock ABL | final acceptance, unattended |
| `dev` | `boot` until `dev restore`; `abl` with `dev stage-abl` | stage 1 or 2; PICO OS after `dev restore` | iteration |

On the PICO 4, `flash-run` with the engineering ABL is the route that works. The signed Neo3
engineering ABL answers `fastboot boot` with `unknown command` after transferring the image, and
the stock ABL reports its unlock state as `DeviceUnlocked=true`, a form the runner's unlock check
does not accept. `run` then fails and returns the headset to PICO OS; it never falls back to
flashing.

[scripts/boot-poc.sh](../scripts/boot-poc.sh) and `boot-poc.ps1` boot an image from RAM for
interactive use and leave Linux running ([stage1.md](stage1.md)); their reachability check is not
an acceptance result.

## Requirements

- **Headset**, prepared as in [device-preparation.md](device-preparation.md): identity record,
  verified EDL backup, unlocked bootloader, engineering ABL and Firehose loader. Commands start
  from PICO OS with USB debugging authorized unless noted. `adb reboot edl` works from PICO OS and
  EDL needs no buttons, so the cycle and the dev loop need nobody at the headset.
- **Host**: Python 3.9+, Android platform-tools (`adb`, `fastboot`), OpenSSH (`ssh`, `ssh-keygen`)
  and, for the EDL steps of `cycle` and `dev stage-abl`, edl-ng. A USB network driver for the
  headset's NCM link and a route to 172.16.42.1; on Windows also the
  [NCM initialization](#windows-ncm-initialization). The tools change no host network settings and
  install no drivers; driver setup and physical recovery from a hard hang need a person.
- **Images**, built from the repository root ([development.md](development.md)):
  `out/pico4-poc-<firmware>.img` from `python -m pico4 build --firmware <firmware>`, for the
  firmware entry that `python -m pico4 match` finds for the headset; for stage 2,
  `out/userspace/pico4-userspace.tar.zst` with `pico4-userspace.manifest.json` beside it.
- **One at a time**: concurrent runs against one headset are unsupported.

## Settings for cycle and dev

`cycle` and `dev` read their settings from `.env` at the repository root
([pico4/localconfig.py](../pico4/localconfig.py)). The file describes one headset, so Git ignores
it; [.env.example](../.env.example) lists the keys. Environment variables of the same names
override it. Lines are `KEY=VALUE`, `#` starts a comment, and quotes around a value are removed.
Relative paths are relative to the repository root. A command that needs an empty key stops with
`set PICO4_... in .env`. `hwtest` takes command-line options instead; the cycle passes these
settings to it.

| Key | Default | Meaning |
| --- | --- | --- |
| `PICO4_ADB_SERIAL` | required | serial in `adb devices` |
| `PICO4_FASTBOOT_SERIAL` | required | serial in `fastboot devices`, also the POC gadget's USB serial |
| `PICO4_EDL_SERIAL` | required for EDL writes | `Serial Number` in edl-ng's output; no ABL is written to another device |
| `PICO4_IDENTITY` | `backup/device.json` | identity record (`python -m pico4 device probe`); its SoC ID derives the OEM authorization and is checked at login |
| `PICO4_BACKUP` | `backup/stock` | verified EDL backup with the stock `boot` and `abl` |
| `PICO4_LOADER` | `assets/firehose.elf` | signed Firehose loader |
| `PICO4_ENGINEERING_ABL` | `assets/engineering-abl.elf` | engineering ABL image |
| `PICO4_ENGINEERING_ABL_SHA256` | required for staging | its expected SHA-256 |
| `PICO4_EDL` | `cache/edl-ng/edl-ng`, `.exe` on Windows | edl-ng executable |
| `PICO4_FIRMWARE` | `5.13.8-global-sek` | entry of [pico4/firmware.json](../pico4/firmware.json); selects `out/pico4-poc-<firmware>.img` |
| `PICO4_NCM_PYTHON` | empty: no NCM initialization | Python with the `windows-ncm` extra |
| `PICO4_PLATFORM_TOOLS` | empty: `adb` and `fastboot` from `PATH` | folder with `adb` and `fastboot` |
| `PICO4_SSH`, `PICO4_SSH_KEYGEN` | `ssh`, `ssh-keygen` | OpenSSH executables |

## Recommended workflow

Iterate in the development loop, with no flashing or restoring between attempts, then run one
acceptance cycle:

```sh
python -m pico4 dev stage-abl        # once, while the stock ABL is installed
python -m pico4 dev image
python -m pico4 dev flash
python -m pico4 dev connect
python -m pico4 dev boot             # each attempt: stream, switch, wait for stage 2
python -m pico4 dev check            #   the runner's stage-2 checks
python -m pico4 dev reboot           #   back to stage 1 for the next attempt
python -m pico4 dev restore          # stock boot and PICO OS; the engineering ABL stays
python -m pico4 cycle --skip-stage --userspace out/userspace/pico4-userspace.tar.zst
```

A changed stage-1 image needs `dev image` and `dev flash` again. The cycle starts from PICO OS,
hence `dev restore` before it.

## Acceptance runner

`python -m pico4 hwtest preflight|run|flash-run` ([pico4/hardware_test.py](../pico4/hardware_test.py)).
Without options, a pass establishes a Linux shell with USB-network diagnostics, read-only
persistent storage and a verified return to the same stock PICO OS; `--graphics` and
`--userspace` add graphics, XR and session checks. Audio is not tested. `run` and `flash-run`
never unlock the bootloader or reset user data, and they never modify the source image. The full
acceptance on the PICO 4:

```sh
python -m pico4 hwtest flash-run --serial ADB_SERIAL --fastboot-serial FASTBOOT_SERIAL \
    --confirm-device ADB_SERIAL --backup backup/stock \
    --image out/pico4-poc-5.13.7-global-seko.img \
    --userspace out/userspace/pico4-userspace.tar.zst \
    --windows-ncm-python cache/ncm-venv/Scripts/python.exe
```

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--serial` | required; `preflight` takes the only authorized ADB device | ADB serial |
| `--fastboot-serial` | `--serial` | fastboot serial if it differs; also the POC gadget's USB serial |
| `--image` | required for `run`, `flash-run` | stage-1 image |
| `--rootfs` | required for `preflight` | `out/pico4-poc-<firmware>-rootfs.tar` |
| `--backup` | required for `flash-run` | verified EDL backup |
| `--confirm-device` | required for `flash-run` | repeats `--serial` to authorize the boot write |
| `--out` | `out/hardware-<random>` | new [result directory](#result-directory) |
| `--host` | `172.16.42.1` | the headset's USB IPv4 address |
| `--adb`, `--fastboot`, `--ssh`, `--ssh-keygen` | found on `PATH` | executables, checked before anything runs |
| `--windows-ncm-python` | off | Windows only: [NCM initialization](#windows-ncm-initialization) |
| `--userspace ARCHIVE` | off | [handoff and stage-2 checks](#userspace-checks) |
| `--userspace-timeout` | 600 (60–1800) | seconds for the archive transfer and for stage 2's SSH, each |
| `--graphics MODE` | off | [native graphics tests](#graphics): `kms`, `vulkan` or `all` |
| `--graphics-seconds` | 10 (3–120) | KMS measurement per mode |
| `--debug-session-seconds` | 0 (60–1800) | [development session](#iterate-within-one-linux-boot); excludes `--graphics` |
| `--boot-timeout`, `--report-timeout`, `--recovery-timeout` | 180, 180, 420 | [limits](#failure-and-timeout-behavior) in seconds |

### Phases

`result.json` records each phase under `checks`, with these names:

1. **prepare test boot image.** With `--windows-ncm-python`, the NCM helper's `--check`. A fresh
   ed25519 key for the run. The source image must pass its header checksum, be a POC build, keep
   read-only mode and the QEMU self-test off (`pico4poc.rw`, `pico4poc.selftest`) and start the
   independent `recovery-watch` service (rebuild older images); with `--userspace` it must also
   contain the handoff (`pico4-userspace`, `zstd`, `pico4-init` and the `switch-userspace`
   restart entry). The runner derives `test-boot.img`: root's `authorized_keys` with the run's
   public key, a random run identifier (in `/etc/pico4-poc/test-run` and as
   `pico4poc.test_run=`), the [device deadlines](#device-deadlines) and no boot signature.
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
   `/data/local/tmp/pico4-poc-<run>`; the script runs the image's Alpine binaries on the stock
   kernel as the unprivileged ADB shell user. It must exit 0 and print exactly one summary
   `PREFLIGHT: N passed, 0 failed` with N > 0 (`preflight.txt`). The files are removed after a
   failure too.
5. **enter unlocked fastboot.** `adb reboot bootloader`; within `--boot-timeout` the fastboot
   serial must appear, and `fastboot oem device-info` (or `getvar unlocked`) must report a known
   unlocked state.
6. **RAM boot** (`fastboot boot`) or **flash test boot image** (`flash-run`), bounded by
   `--boot-timeout`.
7. **SSH identity.** Within `--boot-timeout`, after the NCM initialization if enabled, SSH with
   the run's key must return this run's identifier and the baseline SoC ID before the endpoint
   counts as the headset. Only then does the runner clear the device's connection deadline.
8. **Linux shell and read-only storage.** The running build metadata equals the image's, the
   kernel release matches it, the machine is `aarch64`, and the shell runs as UID 0 and executes
   commands. `/sys/class/block/*/ro` is 1 for every disk and partition, device-mapper devices
   included and loop, ram, zram and nbd devices excluded; root is RAM-backed (zram in stage 2),
   and no `/dev/*` file system other than zram is mounted writable. These checks observe: nothing
   tries to write to a real partition. For `flash-run`, `boot` read back over the test image's
   length must hash to the test image's SHA-256.
9. **HTTP and diagnostic report.** `cgi-bin/status` must be the POC's and `cgi-bin/log?boot` must
   name this run. The runner waits up to `--report-timeout` for the report that init's probe
   writes instead of starting another probe; `cgi-bin/report` must hold `summary.txt`,
   `system.txt` (naming this run) and `dmesg.txt`, each 1 B to 16 MiB, inspected without
   extracting device-controlled paths. HTTP bypasses proxies, rejects redirects and limits each
   response to 32 MiB and 15 s. Mounting the USB log drive is not part of the test.
10. **native graphics**, **SteamOS-compatible userspace**, **bounded development session**: with
    [`--graphics`](#graphics), [`--userspace`](#userspace-checks) and
    [`--debug-session-seconds`](#iterate-within-one-linux-boot), in this order.
11. **return to original PICO OS**, whenever the headset left PICO OS, after a pass or a failure
    ([recovery](#failure-and-timeout-behavior)). The headset must come back under the original
    ADB serial with boot completed and the baseline fingerprint, OEM state and SoC ID; reaching
    fastboot alone does not count.

`test-boot.img` tests the supplied POC with authentication and identification instrumentation;
it is not a claim that the unmodified source image booted byte for byte. The run's private key is
deleted when the runner finishes. Host keys are accepted on the first connection into the run's
own `known_hosts` and pinned from then on; no password prompts, personal SSH configuration or
global known-hosts updates are involved.

### Windows NCM initialization

The stock kernel's NCM function (PICO OS 5.13.7) rejects the packets of Windows' NCM driver with
`Wrong NDP SIGN` in its kernel log, so Windows gets no DHCP, SSH or HTTP. Sending the CDC request
`SET_CRC_MODE(0)` through UsbDk and then releasing the device to Windows 11's in-box UsbNcm driver
makes all three work without changing the kernel. [pico4/windows_ncm.py](../pico4/windows_ncm.py)
does this; `--windows-ncm-python` (runner) or `PICO4_NCM_PYTHON` (cycle, dev) enables it.

Install [UsbDk](https://github.com/daynix/UsbDk/releases) separately (the helper loads
`UsbDkHelper.dll` from `%ProgramFiles%\UsbDk Runtime Library`), then a Python environment with
the `windows-ncm` extra of [pyproject.toml](../pyproject.toml):

```powershell
python -m venv cache/ncm-venv
cache/ncm-venv/Scripts/python.exe -m pip install "pyusb==1.3.1" "libusb-package==1.0.30.0"
cache/ncm-venv/Scripts/python.exe pico4/windows_ncm.py --check
```

`--check` tests the dependencies and the UsbDk backend without opening a device;
`--serial FASTBOOT_SERIAL` initializes a running stage 1, also by hand.

- The runner runs `--check` before the first reboot.
- After Linux enumerates, the helper requires exactly one POC gadget (USB ID `1d6b:0104`) with the
  fastboot serial (`--fastboot-serial`, else `--serial`), exactly one configuration and exactly
  one CDC NCM control interface. It selects that configuration, sends the request and always
  releases the USB handle. A missing, ambiguous or mismatched gadget receives no request and
  cannot produce a pass.
- The runner retries it within the SSH readiness deadline (`--boot-timeout`) until it succeeds
  once; SSH gets the rest of that deadline. `result.json` records it under `windows_ncm`. Every
  call is bounded by its caller, because a Windows driver open or close can stall.
- It runs once per stage-1 boot and never after the switch to stage 2: the handoff keeps the USB
  link (interface, MAC, address), and running the initialization again in stage 2 stalls UsbDk
  and leaves the host's NCM and storage drivers in an error state.
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
(`python -m pico4 device token`). That command is not `flashing unlock` and resets nothing.

`result.json` records `boot_restore_required: true` and `manual_restore_commands` before the
write, so a failed, timed-out or interrupted write is restored too. A running POC is asked to
reboot to the bootloader; a headset that comes up in Android with the original identity instead
(a partial write can leave a bootable stock image) is sent there through ADB. The pinned image is
checked again and flashed (`boot_restore: flash_acknowledged`); after a normal reboot the
original Android identity must complete boot (`android_boot_verified`). That is no byte readback
of the stock partition; the [cycle](#acceptance-cycle) adds a full EDL readback. A failed restore
leaves the headset in fastboot when possible and keeps `boot_restore_required: true`.

Killing the host process or losing power can leave the POC in `boot`; a normal reboot then starts
the POC again, never PICO OS. Once the headset is in fastboot (its deadlines bring it there), the
saved `manual_restore_commands` flash `stock-boot.img` from the result directory; the EDL backup
is the other way back.

### Userspace checks

`--userspace ARCHIVE` hands stage 1 over to the archive's systemd and checks stage 2
([userspace.md](userspace.md), [xr.md](xr.md), [session.md](session.md)). The manifest
`pico4-userspace.manifest.json` must lie beside the archive and name its SHA-256, and the image
must contain the handoff.

The runner streams the archive over the pinned SSH connection into
`pico4-userspace receive --sha256 HASH` (ext4 on zram, mounted at `/newroot`), which must confirm
the hash; `pico4-userspace switch` must answer `switching`. Within `--userspace-timeout`, stage 2
must answer SSH with the same pinned host key (stage 1's dropbear keys are converted for OpenSSH),
the same run identifier and SoC ID, and systemd as PID 1. `result.json` records the archive, its
features, `receive_seconds` and `ssh_seconds`. Stage 1's dropbear runs with a 4 MiB receive window
(`-W` in [svc](../initramfs/usr/libexec/pico4-poc/svc)): with the 24 KiB default, the upload
stopped at about 4.4 MB/s; with the 4 MiB window the 801 MiB archive arrives in 39 s
(21.6 MB/s). `dev boot` prints the rate.

Then, in this order:

| Check | Runs when | Must hold |
| --- | --- | --- |
| Userspace check | always | `pico4-userspace-check --wait 240` passes: systemd 257 as PID 1, state `running`, no failed units; `/dev` on tmpfs with every node; root on zram and no writable block device; the USB link unchanged; logind's `seat0`; the `steamos` user's session, `pico4-session.service` and its target active; Monado's socket listening; the session user can open its devices; `pico4-fan.service` active and writing its state ([cooling.md](cooling.md#checks)); with GPU firmware in the archive, the Turnip/KGSL Vulkan readback. The stage-1 shell and storage assertions (phase 8) then hold in stage 2 too. |
| Head IMU | the manifest has sensor data | `pico4-imu stream --rate 500 --seconds 10 --check --orientation`: accelerometer and gyroscope samples with increasing timestamps at the requested 500 Hz; the accelerometer's mean is the tilt reference for the next check |
| OpenXR pose | the archive has `pico4-monado` | `pico4-openxr-pose 10 10`: the PICO 4 system with orientation tracking, tracked throughout after the warm-up, tilt within 2° of the accelerometer's, heading drift at most 0.5°/s |
| OpenXR display | the archive has `pico4-monado` | `pico4-openxr-display 30` (projection and quad layer): frames at ≥ 95 % of 90 Hz, late frames at most 1 % (or 1); Monado's `pico4_kms` target commits at ≥ 95 % of 90 Hz with vblank at 90 Hz ± 1 % and no fence timeouts; the scanned-out image is red on the left panel and blue on the right |
| SteamVR display layer | the archive has `pico4-vrdisplay` | `pico4-vrdisplay-test 15`: display `PICO 4` acquired through DRM; frames at ≥ 95 % of 90 Hz; the swapchain's vblank counter advancing at 90 Hz ± 1 %, vblank events at ≥ 90 % of 90 Hz; the real `pico4_vrdisplay` layer commits at ≥ 95 % of 90 Hz without fence or GPU timeouts; red left, blue right |
| Shared semaphores | the archive has `pico4-vrdisplay` | `pico4-vrsync-test` as `steamos`: one timeline semaphore shared by two processes (OPAQUE_FD, emulated over sync files) carries a GPU wait, host waits and signals, an early wait and a same-submission wait; the waiting GPU work starts after the signalling work ends |
| Valve's session | the archive has `deckard-steamvr-rel` | after the cool-down, `pico4-valve-session-check --seconds 30`: `gamescope-session.target`, SteamVR and gamescope active and the configured session restored afterwards; SteamVR's compositor runs as a gamescope client without asserts and presents through `pico4_vrdisplay` at ≥ 36 commits/s (40 % of 90 Hz), vblank at 90 Hz ± 1 %, no fence or GPU timeouts, a non-black image; SteamVR reports the headset and both controllers with finite poses, the controllers with positions |

Each check runs only after the previous one passed and only when the archive contains what it
tests. The thresholds are in [pico4/userspace.py](../pico4/userspace.py). Each OpenXR check stops
`pico4-monado.service` first, so its client starts a fresh service through the socket; the KMS
target writes its statistics when the service stops. The display-layer test runs with Monado
stopped, so the layer drives the panels.

The fan control ([cooling.md](cooling.md)) has not been measured with Valve's session. Before
Valve's session, the runner reads the hottest thermal zone every 5 s (ignoring readings of 200 °C
and above) and starts once it is below 70 °C, or after 180 s regardless; `valve_session_cooldown`
in `result.json` records the wait and the temperature. The session check waits up to 180 s for
SteamVR's compositor to present, runs 30 s, records the hottest zone (`max_temp_c`) and the fan's
state (`fan`) before and after, and is bounded to 400 s.

### Graphics

`--graphics MODE` (`kms`, `vulkan` or `all`) runs the native graphics tests in stage 1 and needs a
graphics-enabled image ([graphics.md](graphics.md)). `pico4-vulkan-test` is bounded to 45 s;
`pico4-kms-test` measures `--graphics-seconds` per mode and is bounded to twice that plus 30 s.
[pico4/graphics.py](../pico4/graphics.py) validates the JSON results (`graphics-vulkan.json`,
`graphics-kms.json`, stderr in `.log` files); `graphics-dmesg.txt` and
`graphics-display-state.txt` hold the kernel log and the panel and backlight state. The phase-8
assertions run again afterwards. A failed graphics phase fails the run even when the restore
succeeds. The runner cannot see the panels: `graphics_visual_confirmation` stays `not_recorded`,
and a person has to look at both of them.

### Iterate within one Linux boot

`--debug-session-seconds N` (60–1800) on `run` or `flash-run` keeps the authenticated boot
available for N seconds after the checks, with `--userspace` after the stage-2 checks. The run is
recorded as a development run (`run_kind: development`, `graphics_acceptance: not_evaluated`);
its success does not certify anything launched by hand. When the runner prints
`development session ready`, use the SSH configuration in the result directory `OUT`:

```sh
ssh -F OUT/ssh_config pico4
scp -F OUT/ssh_config PROGRAM pico4:/run/PROGRAM
ssh -F OUT/ssh_config pico4 'chmod +x /run/PROGRAM; timeout 100 /run/PROGRAM'
```

`/run` is in RAM: copy logs to the host before the session ends, and give each experiment its own
short timeout. New programs need no reflash or restore; a new kernel or boot image, or a driver
failure that cannot be recovered, does.

Creating `OUT/end-session` (`New-Item` in PowerShell, `touch` elsewhere) ends the session early.
The runner re-checks the SSH identity every 15 s. When the session ends by the file or the
deadline, the phase-8 assertions run again; the final kernel log is saved in any case
(`development-dmesg.txt`). Every end, including an identity or connection failure and an
interruption, continues into the normal recovery, which restores stock boot once for `flash-run`,
verifies Android and deletes the session key. Deadline expiry and an explicit early finish both
restore and verify PICO OS. The device's maximum runtime includes the session; without a running
host it cannot restore a flashed `boot` partition.

For userspace work the [development loop](#development-loop) is faster: its image stays in `boot`
across attempts.

### Device deadlines

Every test image carries deadlines that its independent `recovery-watch` service enforces
([recovery-watch](../initramfs/usr/libexec/pico4-poc/recovery-watch), policy in
[recovery-policy.sh](../initramfs/usr/libexec/pico4-poc/recovery-policy.sh)): from inittab in
stage 1 and as `pico4-recovery-watch.service` in stage 2. It checks every 2 s and reboots to
fastboot when a deadline passes.

- **Connection**, from Linux start: `--boot-timeout` + 30 s (210 s by default); 600 s in the dev
  image. Only the host's verified SSH identity check clears it
  (`/run/pico4-poc/test-authenticated`); USB enumeration, carrier and DHCP traffic do not.
- **Stage 2 reached**, from the switch: the connection deadline + 120 s (330 s by default). The
  host clears it on reaching stage 2 (`/run/pico4-poc/userspace-authenticated`). `dev boot` lowers
  the connection value to 60 s before switching, so a dev stage 2 that never answers returns to
  fastboot after 180 s instead of 720 s.
- **Maximum runtime**, from Linux start: `--boot-timeout` + `--report-timeout` + 300 s, plus
  `--debug-session-seconds`, plus 2 × `--userspace-timeout` + 300 s with `--userspace`; 660 s by
  default, 2160 s with `--userspace`. 3600 s in the dev image. Nothing clears it, so it also
  covers a host runner that died after authentication.

These deadlines apply even when the image turns off the interactive auto-reboot
(`pico4poc.autoreboot`, [stage1.md](stage1.md)). A switch that fails before systemd runs also
returns to fastboot. A kernel panic reboots the headset instead of leaving it in Qualcomm
crash-dump mode (stage 1 turns download mode off unless `pico4poc.ramdump=1`), so a flashed POC
starts again and its deadlines apply.

### Failure and timeout behavior

Exit status 0 means every automated check and the return to the original PICO OS passed. Any
failed check or failed recovery gives 1; a successful recovery never erases an earlier failure.
A command counts as failed when it exits nonzero or prints the word `FAILED`.

| Limit | Default | Bounds |
| --- | --- | --- |
| `--boot-timeout` | 180 s | reaching fastboot; the RAM boot or each flash; SSH readiness, including the NCM initialization |
| `--report-timeout` | 180 s | the hardware report |
| `--recovery-timeout` | 420 s | the wait for PICO OS; for `flash-run` also the wait for fastboot before the restore |
| `--userspace-timeout` | 600 s | the archive transfer; the wait for stage 2's SSH |

Every subprocess and HTTP transfer has its own timeout, so a phase can overrun its limit by the
timeout of the command in flight. Preflight's push, run and cleanup are bounded separately (180,
300 and 30 s), as are the stage-2 checks (for example 300 s for the userspace check and 400 s for
Valve's session).

Once the headset has left PICO OS, a failure leads into the recovery phase (a `finally` path):

- With this run's SSH identity verified, the runner saves the boot log (`recovery-boot.log`, best
  effort) and asks the POC to reboot, to the bootloader when `boot` must be restored.
- `flash-run` restores stock boot as [described above](#explicit-boot-partition-test).
- If the fastboot serial appears, including after the POC's own fallback to fastboot, the runner
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
| `result.json` | status; every phase with times and errors; identity, image hashes, device deadlines, recovery and boot-restore state, `manual_restore_commands`, NCM, graphics, userspace and session data; rewritten atomically after each change |
| `command-NNNN.json`, `command-NNNN.log` | every subprocess: arguments, timeout and start; its output, partial on timeout |
| `test-boot.img`, `rootfs.tar` | the instrumented image; the source image's userspace for the preflight |
| `ssh_config`, `known_hosts`, `id_ed25519.pub` | the run's SSH settings and pinned host keys; the private key is deleted at the end |
| `preflight.txt` | preflight output |
| `stock-boot.img` | `flash-run`: the pinned stock boot image |
| `storage.txt`, `mounts.txt`, `boot-readback.txt` | block-device inventory, mounts, `flash-run` readback hash; `-userspace` variants from stage 2 |
| `http-status.txt`, `boot.log`, `report.tar.gz`, `dmesg.txt` | HTTP diagnostics and the kernel log |
| `graphics-*` | graphics results and logs |
| `userspace-*` | stage-2 results (`.json`) and logs; the logs are best effort, the results decide |
| `development-dmesg.txt` | kernel log at the end of a development session |
| `recovery-boot.log` | boot log saved during recovery |

### Preflight only

```sh
python -m pico4 hwtest preflight --serial ADB_SERIAL --rootfs out/pico4-poc-5.13.7-global-seko-rootfs.tar
```

Runs phases 2 and 4 only: PICO OS keeps running, and no unlock, fastboot or SSH is involved.
`--serial` may be left out when exactly one authorized ADB device is attached.
[scripts/preflight.ps1](../scripts/preflight.ps1) (`-RootfsTar`, `-Serial`; run it with
PowerShell 7, since Windows PowerShell 5.1 aborts on native stderr) and
[scripts/preflight.sh](../scripts/preflight.sh) (`[rootfs.tar] [serial]`) call it, default to the
newest `out/pico4-poc-*-rootfs.tar` and return its exit status. The recorded fingerprint and OEM
state select the firmware entry: `python -m pico4 match --fingerprint ... --oem-state ...`.

## Acceptance cycle

`python -m pico4 cycle [--userspace ARCHIVE] [--skip-stage] [--keep-abl | --restore-only]`
([pico4/cycle.py](../pico4/cycle.py)) runs the acceptance unattended and ends on stock boot and
stock ABL. Settings come from [`.env`](#settings-for-cycle-and-dev). A full cycle with the
userspace archive, from PICO OS back to PICO OS on stock boot and ABL, takes about 8 minutes.

1. **Engineering ABL** (skipped with `--skip-stage`): into EDL; a read-only first session
   (`printgpt`) must report `PICO4_EDL_SERIAL`; then
   `python -m pico4 device stage --partition abl --execute` with `PICO4_ENGINEERING_ABL` and
   `PICO4_ENGINEERING_ABL_SHA256`, which checks the hash, writes and reads back
   ([device-preparation.md](device-preparation.md)); EDL reset; PICO OS must finish booting.
2. **Acceptance run**: `python -m pico4 hwtest flash-run` with the `.env` serials and backup,
   `out/pico4-poc-<PICO4_FIRMWARE>.img` (built beforehand), `--userspace ARCHIVE` when given
   (stage 1 only otherwise) and `--windows-ncm-python` when `PICO4_NCM_PYTHON` is set; results in
   `run/`, bounded to 4 hours. If `result.json` still requires a boot restore, the cycle waits up
   to 45 minutes for the fastboot serial (the device deadlines bring the headset there), checks
   the pinned `stock-boot.img` against its recorded size and SHA-256, and runs the recorded
   `manual_restore_commands`. PICO OS must then finish booting.
3. **Stock boot and stock ABL** (skipped with `--keep-abl`): into EDL; edl-ng reads the whole
   `boot` partition (96 MiB) back. Its output must report `PICO4_EDL_SERIAL`, and the readback
   must match the backup's boot image. Then `python -m pico4 device restore-abl --execute`, with
   readback.
4. **EDL reset**; PICO OS must finish booting.

`--restore-only` runs steps 3 and 4, for example after `python -m pico4 dev restore`.
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
stock boot and stock ABL are already verified; the cycle then stops waiting for PICO OS. A person
has to look at the headset: a hardware reset (power button held for more than 10 s) starts PICO
OS. Do not retry the cycle or any EDL or fastboot step blindly.

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

`python -m pico4 dev COMMAND` ([pico4/devloop.py](../pico4/devloop.py)) works on a headset whose
`boot` partition holds the dev image. Each attempt reboots into stage 1, streams a userspace
archive, switches to it and runs the runner's stage-2 checks; nothing is flashed or restored in
between. While the dev image is in `boot`, every boot starts stage 1, never PICO OS. Settings
come from [`.env`](#settings-for-cycle-and-dev).

| Command | Effect |
| --- | --- |
| `stage-abl` | engineering ABL through EDL with readback, then PICO OS, as in cycle step 1; logs in `out/dev/stage-abl-<time>/` |
| `image` | builds `out/dev/dev-boot.img` from `out/pico4-poc-<PICO4_FIRMWARE>.img`, which must contain the handoff: instrumented like a runner image, with the run identifier `dev-loop`, the dev key and long deadlines |
| `flash` | goes to fastboot (from PICO OS through ADB, from stage 1 or 2 through SSH; up to 180 s), checks the bootloader and partitions as `flash-run` does (unlocked, exactly one non-slotted `boot` of the backed-up size, an image that fits), sends the OEM authorization, writes the dev image to `boot` and reboots into stage 1 |
| `connect` | forgets the previous host key (stage 1 makes new ones every boot), runs the NCM initialization when `PICO4_NCM_PYTHON` is set (retried for up to 120 s) and logs in to stage 1 within 120 s; the endpoint must report `dev-loop` and the SoC ID of `PICO4_IDENTITY`, and the login clears the connection deadline. It then reads `boot` back from the running stage 1: its hash must match `out/dev/dev-boot.img` |
| `boot [ARCHIVE] [--push LOCAL=REMOTE]` | streams the archive (default `out/userspace/pico4-userspace.tar.zst`, manifest beside it) into `pico4-userspace receive` within 900 s, requires the hash confirmation and prints the rate; writes each `--push` file to its absolute path `REMOTE` in the received root; lowers stage 2's connection deadline; switches; waits up to 170 s for systemd as PID 1 and clears the stage-2 deadline; records the archive in `out/dev/state.json` |
| `check` | runs the runner's stage-2 checks, from the userspace check to Valve's session, against the stage 2 that `boot` started; results in `out/dev/check-<time>/`; exit 0 only if all pass |
| `ssh COMMAND...` | runs the words as one remote command (up to 600 s) and prints the output as text; the remote exit status is not passed on |
| `reboot` | reboots the headset into stage 1, waits 20 s and runs `connect` |
| `restore` | checks the backup's stock boot image against its manifest and the partitions as `flash` does, writes it to `boot` through fastboot and waits up to 300 s for PICO OS; the ABL stays engineering |

- `flash` and `restore` need the engineering ABL and write only `boot`. Unlike `flash-run`
  they do not compare the backup's identity record with the live headset; the serials in `.env`
  and the SoC ID checked at login tie them to it.
- The dev image's connection deadline is 600 s and its maximum runtime 3600 s from Linux start:
  stage 1 waits for the next attempt instead of rebooting, every boot still ends in fastboot
  within an hour, and a stage 2 that never answers returns to fastboot 180 s after the switch.
  From fastboot, `dev flash` starts stage 1 again.
- Once `pico4-userspace receive` has started, a failed `boot` (for example an unconfirmed hash)
  never switches, and the next `boot` needs a `reboot` first, because `/newroot` and zram are in
  use.
- `--push LOCAL=/path` writes LOCAL to `/newroot/path` with `cat`: an existing file keeps its
  permissions, a new one is not executable, and its directory must exist. Files for a running
  stage 2 go over `ssh` or `scp` directly, as do binary files from the headset, which `dev ssh`
  would decode as text:

  ```sh
  ssh -i out/dev/id_ed25519 -o UserKnownHostsFile=out/dev/known_hosts root@172.16.42.1 "cat /run/FILE" > FILE
  ```

`out/dev/` holds the loop's state:

| File | Content |
| --- | --- |
| `id_ed25519`, `id_ed25519.pub` | the dev key, created once; the dev image trusts it, so a new key needs `image` and `flash` again |
| `known_hosts` | stage 1's host key, replaced at each `connect` |
| `dev-boot.img`, `dev-image.json` | the dev image; its hashes, build metadata and device deadlines |
| `rootfs.tar` | the image's userspace, a by-product of the instrumentation |
| `state.json` | the archive running in stage 2 (hash, features, transfer and boot times); written by `boot`, removed by `reboot` and `restore` |
| `check-<time>/` | `check` results: `result.json`, command logs and the `userspace-*` files |
| `stage-abl-<time>/` | `stage-abl` logs and readback |

## Host tests

`python -m unittest discover -s tests`, or `make test`, which also runs
[tests/test_recovery_policy.sh](../tests/test_recovery_policy.sh) for the deadline policy, needs no
hardware ([development.md](development.md)). With simulated devices,
[tests/test_hardware.py](../tests/test_hardware.py) covers the runner: success, failed preflight,
locked and unsupported bootloaders, a wrong SSH identity, a wrong build, writable partitions,
broken HTTP and failed recovery; for `flash-run` wrong or tampered backups, mismatched kernels and
partition layouts, partial and interrupted writes, a changed restore image, readback mismatches
and failed restores; the graphics, userspace and development-session phases, the order and
failures of the NCM initialization, command timeouts and artifact preservation.
[tests/test_headset.py](../tests/test_headset.py) covers the cycle's order and refusals (a boot
partition that differs from the backup, another EDL device, a changed pinned image, no EDL device)
and the dev loop's handoff, login and restore; [tests/test_windows_ncm.py](../tests/test_windows_ncm.py)
covers the NCM helper. They do not replace a run on the headset; the QEMU tests are a separate
userspace regression check.
