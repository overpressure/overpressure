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
not supported. `python -m overpressure device fetch-unlock-files` fetches the signed Firehose loader and the
engineering ABL from PICO's public downloads for the Neo 3 and checks them by hash
([distribution.md](distribution.md)). No driver is installed automatically.

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

## Verified hardware

The tools run only on what the whole path has run on, or on PICO OS builds that run the same code before the
kernel, and refuse the rest before they change anything:

| | Verified | Checked by |
| --- | --- | --- |
| Headset | original PICO 4, A8110, *phoenix*, with LPDDR4X | the fingerprint, PICO OS's model (`pxr.vendorhw.product.model` `PICO 4`) and memory (`ro.vendor.ddr_type` `DDR4`), in `device probe` and every later command |
| PICO OS | 5.13.7 `global-seko` (`ro.oem.state` true), where the whole path has run; 5.13.3 and 5.13.8 `global-seko`, which run the same code before the kernel ([other builds](#other-pico-os-builds)) | the same, with the fingerprint's region |
| Panel | Sharp LS026B3SA | `dev install`, from the kernel command line in the maintenance stage 1: the stock tree also knows an Innolux NT57900, which our driver and tree do not |

Neo 3 and Ultra have other fingerprints and are refused. The PICO 4 Pro and Enterprise take the same packages
(device `PICOA8110`) and report the same fingerprint, but they have LPDDR5 memory (the PICO 4 LPDDR4X), which the
firehose loader the tools fetch and the kernel's memory bus table are not made for. PICO OS tells them apart itself,
and the tools read what it found: `pxr.vendorhw.product.model`, which PICO OS's `pxr.vendorhw.check.sh` sets from the
serial number to `PICO 4`, `PICO 4 Pro` or `PICO 4 Enterprise`, and `ro.vendor.ddr_type`, which its
`init.customize.config.sh` sets from the bootloader's memory report to `DDR4` or `DDR5`. Anything but `PICO 4` and
`DDR4`, or no answer, is refused; an identity saved before the probe read these keeps working. All
this ran on one headset, so a second unit can still differ in what these checks don't see, such as the RGB camera's
module (PICO OS knows two makers' with nearly the same settings).

Keep only the intended Qualcomm device connected during EDL operations. ADB and fastboot select
a device by serial, but the EDL adapter cannot verify that the connected device matches the
saved identity: `--confirm-device` (the saved ADB serial) is the user's acknowledgment of that
physical check. `python -m overpressure cycle` additionally compares the serial number that edl-ng
reports with `PICO4_EDL_SERIAL` before it restores the ABL. The `device` commands do not switch
the headset between PICO OS, fastboot and EDL, so no operation silently moves from inspection to
writing; `dev stage-abl` and the cycle do switch modes. The command log on stderr contains device
identifiers. Identity files, logs and backups stay private; `backup/` and `assets/` are ignored
by Git.

## Other PICO OS builds

`tooling/firmware.json` lists PICO's public full packages for the PICO 4, all of them still on PICO's servers; 5.13.8
is the newest build for the PICO 4. `seko` and `sek` are secure-boot user builds, `seko` with `ro.oem.state` true; the
region is the fingerprint's product (`Phoenix_ovs` global, `Phoenix` China).

**What the headset's own build still decides.** The installed system takes everything of PICO OS's it runs from the
5.13.7 `global-seko` package, whatever the headset runs: the DSP images, the Wi-Fi and Bluetooth firmware, the GPU's
firmware, PICO's tracker and its libraries, the sensor and audio configuration (`python -m overpressure assets`), and
the stock recovery under the maintenance stage 1. Only `persist`, this headset's own data, is read from the headset.
What stays the headset's own build is what runs before the kernel and is never written: the boot chain (XBL and its
configuration, AOP, TrustZone and its libraries, the hypervisor, devcfg, the serial engines' firmware, the UEFI
volume) and the board overlay in `dtbo`. The ABL is not among them: the install replaces it with the engineering ABL.

`python -m overpressure bootchain` compares those members of each package with 5.13.7 `global-seko`'s: an image runs
the same code when every segment but its hash segment (hashes, signature, certificates) is byte-identical, and `dtbo`
is compared overlay by overlay, by each overlay's `compatible` and `qcom,board-id` (kona MTP and kona-iot MTP share
a board-id; the order changes from build to build). PICO signs every build anew, so no image is byte-identical. The
recovery the install writes is checked against the headset's `vbmeta`: its key and rollback index are read too.

| Build | `ro.oem.state` | Date | Before the kernel | `vbmeta` and recovery key | Here |
| --- | --- | --- | --- | --- | --- |
| 5.13.7 `global-seko` (b9665) | true | 2025-10-30 | the reference | the reference | verified: the whole path has run |
| 5.13.3 `global-seko` (b9480) | true | 2025-07-03 | the same code, re-signed; `dtbo` the same | the same, recovery rollback index 1 | accepted: the same code |
| 5.13.8 `global-seko` (b10102) | true | 2026-09-02 | the same code, re-signed; `dtbo` the same | the same, recovery rollback index 1 | accepted: the same code |
| 5.13.3, 5.13.7, 5.13.8 `global-sek` (b9479, b9666, b10113) | empty | | the same code, re-signed; `dtbo` the same | another key | refused: the install's recovery is signed with the `seko` key |
| 5.13.7 `cn-seko`, `cn-sek` (b9650, b9651) | true, empty | 2025-10-30 | XBL and AOP differ | | refused: other code before the kernel |

The tools refuse every build not in the table as well (`tooling/device.py`), naming the reason, before anything is
written. An accepted build that has not run on a headset is accepted on this comparison: the engineering ABL, the
unlock and the whole path have run on 5.13.7 only; the unlock's upstream reports the unlock working through 5.13.8.

What the packages also say (read from PICO's servers, their `updater-script` and firmware):

- **No way back through PICO's updater.** Every package refuses a headset whose build is newer
  (`E3003: Can't install this package ... over newer build`) and one of the other `ro.oem.state`
  (`E3009`). A headset on 5.13.8 cannot get 5.13.7 from PICO, nor move between `seko` and `sek`; the install does not
  need it to.
- **No anti-rollback.** None of the boot chain's images carries an anti-rollback version in its signing header or
  certificates, and the engineering ABL chains to the same Pico root CA as every 5.13.7 and 5.13.8 ABL and XBL. In all
  eight packages `vbmeta` has rollback index 0 and `vbmeta_system` 1617580800; recovery's is 1 in every `global` one.
- **Who can install today.** A headset on a `global-seko` build: 5.13.3, 5.13.7 or 5.13.8.

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
  written to a partition: the development loop keeps it in `recovery` as the maintenance stage 1,
  with the installed system in `boot`, until `dev restore`, and the runner restores stock boot
  after its run ([stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md)).
* Writing `boot` needs the engineering ABL's per-session command authorization first:
  `fastboot oem <token> unlock`, the command that `python -m overpressure device token` prints. The
  hardware runner and `python -m overpressure dev restore` send it.

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
it describes one particular headset; copy [.env.example](../../../.env.example) to start. The keys
are in [hardware-testing.md](hardware-testing.md#settings-for-cycle-and-dev).

## Host tests

`python -m unittest` (or `make test`) runs
[tests/test_device.py](../tests/test_device.py) and the cycle and development-loop tests with
simulated tools, without USB hardware. They cover protocol vectors, tool failures, ambiguous or
unauthorized devices, backup tampering, preview behaviour, write and readback, data-loss gating
and unlock persistence.
