# Device preparation

**TL;DR.** `python -m overpressure device` ([tooling/device.py](../tooling/device.py)) makes an original PICO 4
recoverable and unlocked, and returns it to stock: identity, a verified EDL backup, the engineering ABL, the
unlock, and later the stock ABL. The installer (`python -m overpressure install`,
[install.md](install.md#the-installer)) runs these steps itself; `dev stage-abl` and `cycle` run `stage` and `restore-abl` unattended
([Development mode](#development-mode)). `backup`, `stage`, `unlock` and `restore-abl` only print their
external commands unless `--execute` is given. Every command that touches the headset (`probe`,
`check-unlocked`, `backup`, `stage`, `unlock`, `restore-abl`) first asks the disclaimer
(`--accept-disclaimer` for unattended runs).

| Step | Command | Headset in |
| --- | --- | --- |
| 1. Identity | `device probe` | PICO OS, USB debugging authorized |
| 2. Backup | `device backup`, `device verify-backup` | EDL |
| 3. Engineering ABL | `device stage` | EDL |
| 4. Unlock | `device unlock` | fastboot, engineering ABL |
| 5. Factory reset (only for PICO OS), stage 1 | PICO OS recovery; `dev flash` ([stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md)) | fastboot, then development mode |
| 6. Stock ABL, when leaving development | `device restore-abl` | EDL |

All of it has run on the tested headset (original PICO 4, PICO OS 5.13.7). The implementation is independent,
informed by [PicoUnlock's documented process](https://github.com/chaixshot/more-picohaxx-tool) and
[its serial encoding protocol](https://github.com/chaixshot/more-picohaxx-tool/blob/main/more-picohaxx.py);
credit for the discovery belongs to typlo and its upstream contributors. None of its scripts, drivers, engineering images
or Magisk packages are used. The token masks the 32-bit SoC ID with `0xF7F3F37F` and maps the hexadecimal digits
through `0XD9J6FB3ATQIHNM`. Android rooting, Magisk, OS downgrades, relocking and automated userdata restoration
are out of scope.

## Tools and device scope

`adb` and `fastboot` come from `python -m overpressure tools`; the EDL client is installed separately:
[bkerler/edl](https://github.com/bkerler/edl) (the default, `--edl-backend bkerler`: its `rl`, `r`, `w`,
`--genxml`, `--memory=ufs` and `--loader` interface) or [edl-ng](https://github.com/strongtz/edl-ng)
(`--edl-backend edl-ng --edl PATH`: `--memory Ufs`, `read-part`, `write-part`, `dump-rawprogram`; also fetched
by `python -m overpressure tools`). `cycle` and `dev` use edl-ng. QFIL syntax is not supported.
`python -m overpressure device fetch-unlock-files` fetches the signed Firehose loader and the engineering ABL
from PICO's public Neo 3 downloads and checks them by hash ([distribution.md](distribution.md)). No driver is
installed automatically.

- If Windows turns bkerler's entrypoint symlink into a text file, use
  `--edl-script cache/edl-client/edl.py --edl-python cache/edl-venv/Scripts/python.exe` instead of `--edl`.
- On Windows, edl-ng uses the Qualcomm QDLoader COM interface: Device Manager must show the EDL device as healthy,
  not as an unknown `QUSB_BULK` device with error 28. A signed driver for `USB\VID_05C6&PID_9008` is in the
  [Microsoft Update Catalog](https://www.catalog.update.microsoft.com/ScopedViewInline.aspx?updateid=bcdb99c1-fd4a-4ded-a97f-5bcf6e57fb58);
  install only its serial driver (`qcser.inf`), after checking the catalog signature and hardware ID. UsbDk is
  not needed for EDL.
- If an interrupted EDL session stops responding, restart into PICO OS, check its ADB identity and enter EDL again.

## Verified hardware

The tools run only on what the whole path has run on, or on PICO OS builds that run the same code before the
kernel, and refuse the rest before they change anything:

| | Verified | Checked by |
| --- | --- | --- |
| Headset | original PICO 4, A8110, *phoenix*, with LPDDR4X | the fingerprint, PICO OS's model (`pxr.vendorhw.product.model` `PICO 4`) and memory (`ro.vendor.ddr_type` `DDR4`), in `device probe` and every later command |
| PICO OS | 5.13.7 `global-seko` (`ro.oem.state` true), where the whole path has run; 5.13.3 and 5.13.8 `global-seko`, which run the same code before the kernel ([other builds](#other-pico-os-builds)) | the same, with the fingerprint's region |
| Panel | Sharp LS026B3SA | `dev install`, from the kernel command line in the maintenance stage 1 (the installer checks it on PICO OS too): the stock tree also knows an Innolux NT57900, which our driver and tree do not |

Neo 3 and Ultra have other fingerprints and are refused. The PICO 4 Pro and Enterprise take the same packages
(`PICOA8110`) and report the same fingerprint, but have LPDDR5, which the fetched Firehose loader and the
kernel's memory bus table are not made for. PICO OS tells them apart: its `pxr.vendorhw.check.sh` sets
`pxr.vendorhw.product.model` from the serial number (`PICO 4`, `PICO 4 Pro`, `PICO 4 Enterprise`), and its
`init.customize.config.sh` sets `ro.vendor.ddr_type` from the bootloader's memory report (`DDR4`, `DDR5`).
Anything but `PICO 4` and `DDR4`, or no answer, is refused; an identity saved before the probe read these keys
keeps working. All this ran on one headset: a second unit can differ in what the checks don't see, such as the
RGB camera's module (PICO OS knows two makers' with nearly the same settings).

**Keep only the intended Qualcomm device connected during EDL operations.** ADB and fastboot select by serial,
but the EDL adapter cannot verify that the device matches the identity: `--confirm-device` (the saved ADB
serial) is the user's acknowledgment of that physical check. `cycle` also compares the serial edl-ng reports
with `PICO4_EDL_SERIAL`. The `device` commands never switch the headset between PICO OS, fastboot and EDL, so
no operation silently moves from inspection to writing; `dev stage-abl` and the cycle do switch. The command
log on stderr contains device identifiers. Identity files, logs and backups stay private (`backup/` and
`assets/` are ignored by Git).

## Other PICO OS builds

`tooling/firmware.json` lists PICO's public full packages for the PICO 4, all still on PICO's servers; 5.13.8
is the newest. `seko` and `sek` are secure-boot user builds, `seko` with `ro.oem.state` true; the region is the
fingerprint's product (`Phoenix_ovs` global, `Phoenix` China).

**What the headset's own build still decides.** The installed system takes everything of PICO OS's it runs from
the 5.13.7 `global-seko` package, whatever the headset runs: DSP images, Wi-Fi and Bluetooth firmware, GPU
firmware, PICO's tracker and libraries, sensor and audio configuration (`python -m overpressure assets`) and the
stock recovery under the maintenance stage 1. Only `persist` is read from the headset. The headset's own build
stays in what runs before the kernel and is never written: the boot chain (XBL and its configuration, AOP,
TrustZone and its libraries, the hypervisor, devcfg, the serial engines' firmware, the UEFI volume) and the board
overlay in `dtbo`. The ABL is not among them: the install replaces it with the engineering ABL.

`python -m overpressure bootchain` compares those members of each package with 5.13.7 `global-seko`'s: an image
runs the same code when every segment but its hash segment (hashes, signature, certificates) is byte-identical;
`dtbo` is compared overlay by overlay, by `compatible` and `qcom,board-id` (kona MTP and kona-iot MTP share a
board-id; the order changes between builds). PICO signs every build anew, so no image is byte-identical. The
recovery the install writes is checked against the headset's `vbmeta`: its key and rollback index are read too.

| Build | `ro.oem.state` | Date | Before the kernel | `vbmeta` and recovery key | Here |
| --- | --- | --- | --- | --- | --- |
| 5.13.7 `global-seko` (b9665) | true | 2025-10-30 | the reference | the reference | verified: the whole path has run |
| 5.13.3 `global-seko` (b9480) | true | 2025-07-03 | the same code, re-signed; `dtbo` the same | the same, recovery rollback index 1 | accepted: the same code |
| 5.13.8 `global-seko` (b10102) | true | 2026-09-02 | the same code, re-signed; `dtbo` the same | the same, recovery rollback index 1 | accepted: the same code |
| 5.13.3, 5.13.7, 5.13.8 `global-sek` (b9479, b9666, b10113) | empty | | the same code, re-signed; `dtbo` the same | another key | refused: the install's recovery is signed with the `seko` key |
| 5.13.7 `cn-seko`, `cn-sek` (b9650, b9651) | true, empty | 2025-10-30 | XBL and AOP differ | | refused: other code before the kernel |

The tools refuse every other build too, naming the reason, before anything is written. An accepted build is
accepted on this comparison: the engineering ABL, the unlock and the whole path have run on 5.13.7 only; the
unlock's upstream reports it working through 5.13.8.

What the packages also say (their `updater-script` and firmware):

- **No way back through PICO's updater.** Every package refuses a headset on a newer build
  (`E3003: Can't install this package ... over newer build`) or the other `ro.oem.state` (`E3009`): a headset on
  5.13.8 cannot get 5.13.7 from PICO, nor move between `seko` and `sek`. The install does not need it to.
- **No anti-rollback.** No boot-chain image carries an anti-rollback version in its signing header or
  certificates, and the engineering ABL chains to the same Pico root CA as every 5.13.7 and 5.13.8 ABL and XBL.
  In all eight packages `vbmeta` has rollback index 0 and `vbmeta_system` 1617580800; recovery's is 1 in every
  `global` one.
- **Who can install today:** a headset on a `global-seko` build, 5.13.3, 5.13.7 or 5.13.8.

## Entering and leaving EDL

* From PICO OS with USB debugging authorized: `adb reboot edl`. No buttons are needed.
* By hand: hold both volume buttons while connecting USB to the powered-off headset.
* In EDL the headset is Qualcomm HS-USB QDLoader 9008 (`05c6:9008`).
* `edl-ng --loader assets/firehose.elf --memory Ufs reset --mode Reset` leaves EDL and starts the headset (the
  other modes power it off or return to EDL). If it stays off, a hard reset (power button held for more than
  10 s) starts it.

## 1. Identity

With PICO OS running and USB debugging authorized:

```sh
python -m overpressure device probe
python -m overpressure device token CHIP_ID
```

`probe` reads `ro.build.fingerprint`, `ro.oem.state`, `pxr.vendorhw.product.model`, `ro.vendor.ddr_type` and
`/sys/devices/soc0/serial_number` through ADB and writes them with the ADB serial to `backup/device.json`
(`--output` for another path; an existing file is never overwritten; `--serial` with several ADB devices). The
SoC ID (chip ID) is a decimal or `0x` hexadecimal integer, **not** the USB or ADB serial. `token` only prints the
derived fastboot command. The fingerprint and OEM state select the stage-1 firmware:
`python -m overpressure match --fingerprint "..." --oem-state false` ([stage1.md](stage1.md)).

## 2. Backup

Capture the recovery files before modifying anything. In EDL, check that the intended device appears as 9008.
This previews the external command and does not contact EDL:

```sh
python -m overpressure device backup --identity backup/device.json --backup backup/stock --loader assets/firehose.elf
```

Add `--execute --confirm-device ADB_SERIAL` to dump. The backup directory must not exist yet. The dump takes
all partitions and the GPT and rawprogram metadata, without userdata by default; `--include-userdata` adds the
full partition (allow space for the whole storage; no used-sector mode). Back up personal data separately.

With edl-ng, list the LUNs first (`edl-ng --loader assets/firehose.elf --memory Ufs printgpt`) and pass **all
LUNs that device reports**:

```sh
python -m overpressure device backup --identity backup/device.json --backup backup/stock --loader assets/firehose.elf --edl-backend edl-ng --edl cache/tools/edl-ng-1.6.0/edl-ng.exe --edl-luns 4 1 2 3 5 0 --include-userdata
```

Each LUN goes to its own `lun<N>/` with `rawprogram<N>.xml`, which keeps duplicate partition names apart. Every
non-skipped file of each rawprogram map must have exactly the advertised sector count. The adapter cannot prove
that a manual LUN list is complete. The example dumps the small firmware LUNs before LUN 0, which commonly holds
userdata. Pass `--edl-backend edl-ng --edl PATH` to `stage` and `restore-abl` as well. The way back and the write
checks read this backup (maps, sizes, hashes); building an image does not need it
([development.md](development.md#stock-firmware)).

A manifest is written only when the dump has `abl`, `boot` and `devinfo` images, the GPT (`gpt_main*`), the
rawprogram maps and, when requested, userdata; a failed dump has none and cannot be used later. `edl.log` keeps
the client's output.

```sh
python -m overpressure device verify-backup --backup backup/stock
```

`manifest.json` records the identity, the EDL backend, the LUNs and the size and SHA-256 of every file.
Verification finds missing, empty and modified files; it does **not** prove that a restore works or that EDL
dumped every partition correctly. Inspect the GPT and rawprogram files and `edl.log`, confirm that `boot`,
`abl`, `devinfo` and the calibration partitions (`persist`, `fsg`) are present, and keep an independent copy.
`stage`, `unlock`, `restore-abl` and `hwtest flash-run` verify the backup against its manifest, and its identity
against the identity file, before they act. The dumped `boot` also works with
`python -m overpressure build --stock-boot PATH`; find its name in the dump (no A/B layout).

## 3. Engineering ABL

Use the exact partition names of the captured GPT (an unsuffixed `abl` on the PICO 4; `abl_a`/`abl_b` only if the
table says so). A devinfo image is staged the same way with `--partition devinfo`.

```sh
python -m overpressure device stage --identity backup/device.json --backup backup/stock --loader assets/firehose.elf --partition abl --image assets/engineering-abl.elf --sha256 EXPECTED_SHA256 --readback out/engineering-abl-readback.bin
```

`--execute --confirm-device ADB_SERIAL` writes. The partition needs exactly one hash-verified original in the
backup; the image must fit it and match the SHA-256 (integrity and size, not provenance or compatibility). A
fresh EDL readback is compared with every byte of the image (not the padding beyond it); the readback path must
be new and outside the backup. Nothing reboots. If the write or readback fails, stay in EDL and recover with
the original image. The engineering ABL is a signed PICO Neo3 engineering ABL; PICO OS boots normally with it.
`dev stage-abl` and the cycle stage only `abl`.

## 4. Unlock

In fastboot with the engineering ABL active (`adb reboot bootloader`):

```sh
python -m overpressure device unlock --identity backup/device.json --backup backup/stock
```

This previews, in order: `oem <token> unlock`, `oem setenforce 0`, `flashing unlock`, `flashing unlock_critical`,
`reboot bootloader`. Execution needs `--execute --confirm-device ADB_SERIAL --accept-data-loss`. **Unlocking
wipes user data, and the sequence sets SELinux to permissive.** If fastboot shows another serial than ADB, pass
`--serial FASTBOOT_SERIAL` after checking the device. Every command must succeed; a `flashing` command refused as
already unlocked counts as done.

After the reboot the tool requires both the normal and the critical unlock state. **The order matters:** with the
engineering ABL on 5.13.7, `flashing unlock` before `oem setenforce 0` fails with
`Set device unlocked! failed: 00000050` and changes only in-memory flags, which revert at the next reboot. A
locked, missing or contradictory state is a failure; there is no automatic retry. To inspect without a change:

```sh
python -m overpressure device check-unlocked --critical
```

`check-unlocked` reads `fastboot oem device-info` (without `--critical` it falls back to `getvar unlocked`) and
fails closed.

## 5. Factory reset and stage-1 work

PICO OS needs a factory reset after the unlock before it starts again. The installer skips it and goes from the
unlock to the maintenance stage 1 (`dev flash`). To use PICO OS: in its recovery (the volume rocker is on the top
edge of the rigid right strap, just behind the body), Volume +/- moves and a short Power press selects: select the
reset, confirm it, then `Reboot system now`
([PICO's instructions](https://www.picoxr.com/uk/support/faq/recovery-restore-factory-settings)). Set PICO OS up
and authorize USB debugging again.

Keep the engineering ABL active while working with stage 1:

* Its fastboot reports the unlock state in the form `check-unlocked` and the runner accept; the stock ABL's
  `DeviceUnlocked=true` is refused, and restoring the stock ABL can remove usable fastboot commands even when the
  unlock bits persist.
* It rejects `fastboot boot`, so stage 1 is written to a partition: the dev loop keeps it in `recovery` as the
  maintenance stage 1, with the installed system in `boot`, until `dev restore`; the runner restores stock boot
  after its run ([stage1.md](stage1.md), [hardware-testing.md](hardware-testing.md)).
* Writing `boot` needs its per-session authorization `fastboot oem <token> unlock` (`device token` prints it);
  the runner, `dev flash` and `dev restore` send it.

## 6. Stock ABL

When the headset leaves development mode, restore each staged ABL partition from its verified original in EDL;
after `python -m overpressure dev restore`, `python -m overpressure cycle --restore-only` does it unattended (the
installer's restore workflow too):

```sh
python -m overpressure device restore-abl --identity backup/device.json --backup backup/stock --loader assets/firehose.elf --partition abl --readback out/stock-abl-readback.bin
```

`--execute --confirm-device ADB_SERIAL` writes and verifies by EDL readback. It does not require an unlocked
state, so it also recovers a failed attempt. It restores ABL only: devinfo is not reverted, because that may undo
the workflow. Do not restore encrypted userdata automatically across resets or firmware changes.

With the stock ABL, PICO OS boots with SELinux enforcing, and the bootloader stays unlocked. The runner verifies
the return to Android with the engineering ABL still installed; it neither restores the ABL nor relocks. Check a
normal PICO OS boot after restoring the ABL (`cycle` waits for it). The manual boot sequence in
[stage1.md](stage1.md#writing-boot-by-hand) is a separate, persistent operation that needs a working recovery path.

## Development mode

`dev stage-abl` and `cycle` wrap `stage` and `restore-abl` with the edl-ng backend, without buttons: the first
enters development mode, in which the headset stays between tasks; the second returns it to stock
([hardware-testing.md](hardware-testing.md#development-mode)).

| Command | Steps |
| --- | --- |
| `python -m overpressure dev stage-abl` | `adb reboot edl` (unless exactly one EDL device is attached), `device stage --partition abl` with hash check and readback, EDL reset, wait for PICO OS |
| `python -m overpressure cycle` | the same staging (`--skip-stage` skips it), `hwtest flash-run`, then EDL, a readback of `boot` that must match the backup and come from `PICO4_EDL_SERIAL`, `device restore-abl` with readback, EDL reset, PICO OS. `--keep-abl` stops before the ABL restore; `--restore-only` runs only the readback, the restore and the reset |

Both read `.env` at the repository root (copy [.env.example](../../../.env.example)); the keys are in
[hardware-testing.md](hardware-testing.md#settings-for-cycle-and-dev).

## Host tests

`python -m unittest` runs [tests/test_device.py](../tests/test_device.py) and the cycle and dev-loop tests with
simulated tools: protocol vectors, tool failures, ambiguous or unauthorized devices, backup tampering, previews,
write and readback, data-loss gating and unlock persistence.
