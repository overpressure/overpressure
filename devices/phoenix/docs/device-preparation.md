# Device preparation

`python -m overpressure device` prepares an original PICO 4 for this project and returns it to stock
afterwards: it records the headset's identity, captures and verifies a complete EDL backup,
writes the engineering ABL, unlocks the bootloader and restores the stock ABL. It is
standard-library Python for Windows, Linux and macOS. `backup`, `stage`, `unlock` and
`restore-abl` only print their external commands unless `--execute` is given. On the tested headset
(original PICO 4, PICO OS 5.13.7), `backup`, `stage`, `unlock` and `restore-abl` have been used,
and `python -m overpressure dev stage-abl` and `python -m overpressure cycle` run `stage` and `restore-abl`
unattended when the headset enters and leaves development mode
([Development mode](#development-mode)).

The implementation is independent, informed by
[PicoUnlock's documented process](https://github.com/chaixshot/more-picohaxx-tool) and
[its serial encoding protocol](https://github.com/chaixshot/more-picohaxx-tool/blob/main/more-picohaxx.py).
None of its scripts, drivers, engineering images or Magisk packages are imported. The protocol
masks the 32-bit SoC ID with `0xF7F3F37F` and maps the hexadecimal digits through
`0XD9J6FB3ATQIHNM`. Credit for the underlying discovery belongs to typlo and the upstream
contributors. The workflow covers access and recovery for the Linux work; Android rooting,
Magisk, OS downgrades, relocking and automated userdata restoration are out of scope.

| Step | Command | Headset in |
| --- | --- | --- |
| 1. Identity | `device probe` | PICO OS, USB debugging authorized |
| 2. Backup | `device backup`, `device verify-backup` | EDL |
| 3. Engineering ABL | `device stage` | EDL |
| 4. Unlock | `device unlock` | fastboot, engineering ABL |
| 5. Factory reset, development | PICO OS recovery; [stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md) | PICO OS, then development mode |
| 6. Stock ABL, when leaving development | `device restore-abl` | EDL |

## Tools and device scope

Install Android platform-tools and, separately, [bkerler/edl](https://github.com/bkerler/edl)
or [edl-ng](https://github.com/strongtz/edl-ng). The default adapter uses bkerler's `rl`, `r`,
`w`, `--genxml`, `--memory=ufs` and `--loader` interface. `--edl-backend edl-ng --edl PATH`
selects the native client, whose different syntax (`--memory Ufs`, `read-part`, `write-part`,
`dump-rawprogram`) is handled explicitly; `cycle` and `dev` use edl-ng. QFIL command syntax is
not supported. Supply a compatible signed Firehose loader and the engineering ABL (and a devinfo
image, if needed) yourself, with hashes from a trusted source. Nothing is downloaded and no
driver is installed automatically.

If Windows materializes the bkerler package's entrypoint symlink as a text file, use
`--edl-script cache/edl-client/edl.py --edl-python cache/edl-venv/Scripts/python.exe` instead of
`--edl`. This runs the separately installed client directly, without a shell wrapper.

On Windows, edl-ng uses the Qualcomm QDLoader COM interface directly. Device Manager must show
the EDL device as healthy, not as an unknown `QUSB_BULK` device with error 28. A matching signed
driver for `USB\VID_05C6&PID_9008` is available from the
[Microsoft Update Catalog](https://www.catalog.update.microsoft.com/ScopedViewInline.aspx?updateid=bcdb99c1-fd4a-4ded-a97f-5bcf6e57fb58).
Install only its serial driver (`qcser.inf`), after verifying the catalog signature and the
hardware ID. UsbDk is not needed for the native COM connection. If an interrupted EDL session
stops responding, restart the headset into PICO OS, check its ADB identity and enter EDL again.

The identity check accepts original PICO 4 (A8110, *phoenix*) fingerprints through PICO OS
5.13.8, the upstream reported range, and rejects Neo 3 and other PICO generations. An accepted
version does not prove that a loader, an image or a device variant is compatible.

Keep only the intended Qualcomm device connected during EDL operations. ADB and fastboot select
a device by serial, but the EDL adapter cannot verify that the connected device matches the
saved identity: `--confirm-device` (the saved ADB serial) is the user's acknowledgment of that
physical check. `python -m overpressure cycle` additionally compares the serial number that edl-ng
reports with `PICO4_EDL_SERIAL` before it restores the ABL. The `device` commands do not switch
the headset between PICO OS, fastboot and EDL, so no operation silently moves from inspection to
writing; `dev stage-abl` and the cycle do switch modes. The command log on stderr contains device
identifiers. Identity files, logs and backups stay private; `backup/` and `assets/` are ignored
by Git.

## Entering and leaving EDL

* From PICO OS with USB debugging authorized: `adb reboot edl`. No buttons are needed.
* By hand: hold both volume buttons while connecting USB to the powered-off headset.
* In EDL the headset appears as Qualcomm HS-USB QDLoader 9008 (`05c6:9008`).
* `edl-ng --loader assets/firehose.elf --memory Ufs reset` leaves EDL and starts the headset. If
  it stays off after a reset, a hard reset (power button held for more than 10 s) starts it.

## 1. Identity

With PICO OS running and USB debugging authorized:

```sh
python -m overpressure device probe
python -m overpressure device token CHIP_ID
```

`probe` only reads `ro.build.fingerprint`, `ro.oem.state` and `/sys/devices/soc0/serial_number`
through ADB and writes them with the ADB serial to `backup/device.json` (`--output` chooses
another path; an existing file is never overwritten). Use `--serial` when more than one ADB
device is attached. The SoC ID (chip ID) is a decimal integer or `0x` hexadecimal, **not** the
USB or ADB serial. `token` only prints the derived fastboot command; it does not unlock. The
saved fingerprint and OEM state select the firmware for the stage-1 build:
`python -m overpressure match --fingerprint "..." --oem-state false` ([stage1.md](stage1.md)).

## 2. Backup

Capture the recovery files before modifying anything. In EDL, check that the intended device
appears as 9008. This previews the external command and does not contact EDL:

```sh
python -m overpressure device backup --identity backup/device.json --backup backup/stock --loader assets/firehose.elf
```

Add `--execute --confirm-device ADB_SERIAL` to perform the dump. The backup directory must not
exist yet. The dump requests all partitions and the GPT and rawprogram metadata, excluding
userdata by default. `--include-userdata` also captures the full userdata partition; allow
space for the headset's full storage capacity. There is no used-sector mode. Back up personal
data you need separately.

With edl-ng, first list the LUNs: `edl-ng --loader assets/firehose.elf --memory Ufs printgpt`.
Pass **all LUNs reported by that device**, for example:

```sh
python -m overpressure device backup --identity backup/device.json --backup backup/stock --loader assets/firehose.elf --edl-backend edl-ng --edl cache/tools/edl-ng-1.6.0/edl-ng.exe --edl-luns 4 1 2 3 5 0 --include-userdata
```

This still previews by default. Execution captures each LUN in its own directory (`lun<N>/`
with `rawprogram<N>.xml`), which keeps duplicate partition names of different LUNs apart.
Before a manifest is written, every non-skipped file of each rawprogram map must have exactly
the advertised sector count. The LUN list is explicit; the adapter does not prove that a
manually supplied list is complete. The example captures the smaller firmware LUNs before LUN 0,
which commonly holds userdata. The way back to stock and the write checks read this layout (the
partition maps, sizes and hashes); building an image does not need the backup
([development.md](development.md#stock-firmware)). Pass `--edl-backend edl-ng --edl PATH` to
`stage` and `restore-abl` as well.

A manifest is written only when the dump contains `abl`, `boot` and `devinfo` images, the GPT
(`gpt_main*`), the rawprogram maps and, when requested, userdata. A failed dump has no manifest
and cannot be used by the later steps. `edl.log` in the backup keeps the client's output.

```sh
python -m overpressure device verify-backup --backup backup/stock
```

`manifest.json` records the identity, the EDL backend, the LUNs and the size and SHA-256 of
every image and XML file. Verification detects missing, empty and modified files; it does
**not** prove that a restore works or that EDL dumped every partition correctly. Inspect the
GPT and rawprogram files and `edl.log`, confirm that `boot`, `abl`, `devinfo` and the
device-specific calibration partitions (`persist`, `fsg`) are present, and keep an independent
copy. `stage`, `unlock`, `restore-abl` and the hardware runner's `flash-run` verify the backup
against its manifest, and its identity against the identity file, before they act.

The dumped stock `boot` image also works with `python -m overpressure build --stock-boot PATH`
([stage1.md](stage1.md)). Find its actual file name in the dump; do not assume an A/B layout or
an active slot.

## 3. Engineering ABL

Use the exact partition names from the captured GPT. The example assumes an unsuffixed `abl`
partition; use `abl_a` or `abl_b` only if the partition table says so. Stage a required devinfo
image separately with the same command and `--partition devinfo`.

```sh
python -m overpressure device stage --identity backup/device.json --backup backup/stock --loader assets/firehose.elf --partition abl --image assets/engineering-abl.elf --sha256 EXPECTED_SHA256 --readback out/engineering-abl-readback.bin
```

Preview is the default; `--execute --confirm-device ADB_SERIAL` writes. The partition needs
exactly one hash-verified original image in the backup, and the engineering image must fit that
partition and match the given SHA-256. This checks integrity and size, not provenance or
hardware compatibility. After the write, a fresh EDL readback is compared with every byte of
the image; the partition's padding beyond the image is not compared. The readback path must be
new and outside the backup directory. Nothing reboots automatically. If the write or the
readback fails, stay in EDL and recover with the original image.

The engineering ABL on the tested headset is a signed PICO Neo3 engineering ABL, and PICO OS
boots normally with it. `dev stage-abl` and the cycle stage only `abl`.

## 4. Unlock

Enter fastboot with the engineering ABL active (`adb reboot bootloader`), then:

```sh
python -m overpressure device unlock --identity backup/device.json --backup backup/stock
```

This previews, in order: the OEM authorization (`oem <token> unlock`), `oem setenforce 0`,
`flashing unlock`, `flashing unlock_critical` and `reboot bootloader`. Execution needs all
three of `--execute --confirm-device ADB_SERIAL --accept-data-loss`. **Unlocking wipes user
data, and the upstream sequence sets SELinux to permissive.** If fastboot shows a different USB
serial than ADB, pass it with `--serial FASTBOOT_SERIAL` after checking the physical device.
Every command must succeed; a failure stops the sequence.

After the bootloader reboot, the tool requires both the normal and the critical unlock state.
With the engineering ABL on 5.13.7, `flashing unlock` before `oem setenforce 0` fails with
`Set device unlocked! failed: 00000050` and changes only the reported in-memory flags, which
revert after a reboot. In the tool's order, both flags persist across reboots. A locked, missing
or contradictory state is a failure. Upstream reports that unlock persistence can need another
attempt; there is no automatic retry, so inspect the failure and rerun explicitly if
appropriate. To inspect without changing the device:

```sh
python -m overpressure device check-unlocked --critical
```

`check-unlocked` reads `fastboot oem device-info` (without `--critical` it falls back to
`getvar unlocked`) and fails closed.

## 5. Factory reset and stage-1 work

Boot PICO OS and follow its recovery prompts for the required factory reset, then complete the
setup and authorize USB debugging again. The original PICO 4's volume rocker is on the top edge
of the rigid right-hand strap, just behind the headset body. In recovery, Volume +/- moves and a
short Power press selects: select the reset, confirm it, and select `Reboot system now`
afterwards if shown
([PICO's recovery instructions and button diagrams](https://www.picoxr.com/uk/support/faq/recovery-restore-factory-settings)).

Keep the engineering ABL active while working with stage 1:

* Fastboot then reports the unlock state in the form that `check-unlocked` and the hardware
  runner accept. The stock ABL reports `DeviceUnlocked=true`, which they refuse, and restoring
  the stock ABL can remove usable fastboot commands even when the unlock bits persist.
* The engineering ABL rejects `fastboot boot` with an unknown-command error, so stage 1 is
  written to `boot`: the development loop keeps it there until `dev restore`, and the runner
  restores stock boot after its run ([stage1.md](stage1.md),
  [hardware-testing.md](hardware-testing.md)).
* Writing `boot` needs the engineering ABL's per-session command authorization first:
  `fastboot oem <token> unlock`, the command that `python -m overpressure device token` prints. The
  hardware runner and `python -m overpressure dev flash` send it.

## 6. Stock ABL

When the headset leaves development mode, enter EDL again and restore each ABL partition that
was staged from its verified original; after `python -m overpressure dev restore`,
`python -m overpressure cycle --restore-only` does this unattended:

```sh
python -m overpressure device restore-abl --identity backup/device.json --backup backup/stock --loader assets/firehose.elf --partition abl --readback out/stock-abl-readback.bin
```

`--execute --confirm-device ADB_SERIAL` writes and verifies by EDL readback. Restoration
deliberately does not require an unlocked state: it must also work when recovering a failed
attempt. It restores ABL only; devinfo is not reverted automatically, because that may undo the
workflow. Do not restore encrypted userdata automatically across resets or firmware changes.

With the stock ABL restored, PICO OS boots with SELinux enforcing, and the bootloader stays
unlocked. The hardware runner verifies the return to the original Android build while the
engineering ABL is still installed; it neither restores the ABL nor relocks. Check a normal
PICO OS boot after restoring the ABL; `python -m overpressure cycle` waits for it. The manual
boot-partition sequence in [stage1.md](stage1.md#writing-boot-by-hand) is a
separate, persistent operation that needs a working recovery path.

## Development mode

`python -m overpressure dev stage-abl` and `python -m overpressure cycle` wrap `stage` and `restore-abl` with
the edl-ng backend and without buttons. The first stages the engineering ABL for development
mode, in which the headset stays between tasks; the second returns it to stock
([hardware-testing.md](hardware-testing.md#development-mode)):

| Command | Steps |
| --- | --- |
| `python -m overpressure dev stage-abl` | `adb reboot edl` (unless exactly one EDL device is attached already), `device stage --partition abl` with hash check and readback, EDL reset, wait for PICO OS |
| `python -m overpressure cycle` | the same staging (`--skip-stage` skips it), `hwtest flash-run`, then `adb reboot edl`, an EDL readback of `boot` that must match the backup and come from the device with the configured EDL serial number, `device restore-abl` with readback, EDL reset, and PICO OS must finish booting. `--keep-abl` stops before the ABL restore; `--restore-only` runs only the readback, the restore and the reset |

Both read the headset's settings from `.env` in the repository root, which Git ignores because
it describes one particular headset; copy [.env.example](../../../.env.example) to start. Environment
variables of the same names take precedence, and relative paths are relative to the repository
root. The keys used here:

| Key | Default | Meaning |
| --- | --- | --- |
| `PICO4_ADB_SERIAL`, `PICO4_FASTBOOT_SERIAL`, `PICO4_EDL_SERIAL` | | the headset's serials in `adb devices`, `fastboot devices` and edl-ng's output ("Serial Number") |
| `PICO4_IDENTITY` | `backup/device.json` | identity file |
| `PICO4_BACKUP` | `backup/stock` | verified EDL backup |
| `PICO4_LOADER` | `assets/firehose.elf` | signed Firehose loader |
| `PICO4_ENGINEERING_ABL`, `PICO4_ENGINEERING_ABL_SHA256` | `assets/engineering-abl.elf` | engineering ABL and its expected SHA-256 |
| `PICO4_EDL` | empty: the fetched edl-ng (`python -m overpressure tools`), else PATH | edl-ng executable |
| `PICO4_PLATFORM_TOOLS` | empty: the fetched platform-tools, else PATH | folder with `adb` and `fastboot` |

The other keys (`PICO4_FIRMWARE`, `PICO4_NCM_PYTHON`, `PICO4_SSH`, `PICO4_SSH_KEYGEN`) serve the
hardware runs ([hardware-testing.md](hardware-testing.md)).

## Host tests

`python -m unittest` (or `make test`) runs
[tests/test_device.py](../tests/test_device.py) and the cycle and development-loop tests with
simulated tools, without USB hardware. They cover protocol vectors, tool failures, ambiguous or
unauthorized devices, backup tampering, preview behaviour, write and readback, data-loss gating
and unlock persistence.
