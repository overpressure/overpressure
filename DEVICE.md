# Preparing a PICO 4 for the Linux POC

`python device.py --help` provides a staged, Python-standard-library workflow on Windows,
Linux and macOS. This is an independent implementation informed by
[PicoUnlock's documented process](https://github.com/chaixshot/more-picohaxx-tool) and
[the serial encoding protocol](https://github.com/chaixshot/more-picohaxx-tool/blob/main/more-picohaxx.py).
We did not import its scripts, repository, drivers, engineering images or Magisk packages.
The protocol masks the 32-bit SoC ID with `0xF7F3F37F` and maps hexadecimal digits through
`0XD9J6FB3ATQIHNM`. Credits for the underlying discovery belong to typlo and the upstream contributors.

The integration covers the access/recovery prerequisite for Linux experiments. Android rooting,
Magisk, OS downgrades, relocking and automated userdata restoration are outside this tool's scope.
Host tests use simulated tools; **this implementation has not been validated on a real headset**.

## Tools and device scope

Install Android platform-tools and, separately, [bkerler/edl](https://github.com/bkerler/edl)
or [edl-ng](https://github.com/strongtz/edl-ng). The default adapter uses bkerler's `rl`,
`r`, `w`, `--genxml`, `--memory=ufs` and `--loader` interface. Select the native client with
`--edl-backend edl-ng --edl PATH_TO_EDL_NG`; its different syntax is handled explicitly.
QFIL command syntax is not supported. Supply a compatible
signed Firehose loader and the engineering ABL/devinfo images yourself, with hashes from a
trusted source. No automatic download or driver installation takes place.

If Windows materializes the EDL package's entrypoint symlink as a text file, use
`--edl-script cache/edl-client/edl.py --edl-python cache/edl-venv/Scripts/python.exe`
instead of `--edl`. This invokes the separately installed client directly without a shell wrapper.

On Windows, edl-ng can use the Qualcomm QDLoader COM interface directly. Device Manager must
show the EDL device as healthy, not an unknown `QUSB_BULK` device with error 28. A matching
signed driver for `USB\VID_05C6&PID_9008` is available from the
[Microsoft Update Catalog](https://www.catalog.update.microsoft.com/ScopedViewInline.aspx?updateid=bcdb99c1-fd4a-4ded-a97f-5bcf6e57fb58).
Install only its serial driver (`qcser.inf`); verify the catalog signature and hardware ID.
UsbDk is not needed for the native COM connection. If an interrupted EDL session stops
responding, restart the headset into stock OS, verify ADB identity, and enter EDL afresh.

Preparation accepts original PICO 4 A8110/phoenix fingerprints through 5.13.8, matching the
upstream reported version ceiling. It deliberately rejects Neo 3 and other PICO generations.
An accepted version does not prove that your loader/images or device variant are compatible.

Keep only the intended Qualcomm device connected during EDL operations. ADB and fastboot can
select a serial, but this EDL adapter cannot authenticate that the connected device matches
the saved identity. `--confirm-device` is your acknowledgment of that physical check, not
an automatic EDL identity verification. Mode changes are manual, so no operation silently
switches from inspection to flashing. The command log on stderr includes device identifiers;
keep identity files, logs and backups private.

## 1. Capture identity in stock PICO OS

Enable USB debugging and authorize the PC in the headset:

```sh
python device.py probe --output out/device.json
python device.py token 1234567
```

`probe` only reads ADB properties and `/sys/devices/soc0/serial_number`. Use `--serial` if more
than one ADB device is attached. The SoC ID is a decimal integer (or `0x` hexadecimal), **not**
the USB/ADB serial. `token` only prints the derived fastboot command. It does not unlock.
Identity files are never overwritten. Use the saved fingerprint and OEM state with
`python build.py match --fingerprint "..." --oem-state false` to select a firmware build.

## 2. Capture recovery files before modifying anything

Enter EDL using the device's physical controls (on PICO 4, hold both volume buttons while
connecting USB to the powered-off headset). Verify the intended device appears in EDL 9008.
The following command previews the external command; it does not contact EDL:

```sh
python device.py backup --identity out/device.json --backup backup/stock --loader assets/firehose.elf
```

Add `--execute --confirm-device YOUR_ADB_SERIAL` to perform the dump. A fresh directory is
required. It requests all partitions and GPT/rawprogram metadata, excluding userdata by
default. Add `--include-userdata` to capture the full userdata partition too; allow enough
space for the headset's full storage capacity. There is no experimental used-sector mode.
Do not proceed without a separate backup of personal data you need to retain.

For edl-ng, first inspect `edl-ng --loader assets/firehose.elf --memory Ufs printgpt`.
Pass **all LUNs reported by that device**, for example:

```sh
python device.py backup --identity out/device.json --backup backup/stock --loader assets/firehose.elf --edl-backend edl-ng --edl cache/edl-ng/edl-ng.exe --edl-luns 4 1 2 3 5 0 --include-userdata
```

This still previews by default. Execution captures each LUN in a separate directory,
preserving duplicate partition names across LUNs. It requires every non-skipped file in each
rawprogram map to have exactly the advertised sector count before creating a manifest.
The LUN list is explicit; the adapter does not prove that a manually supplied list is complete.
The example captures the smaller firmware LUNs before LUN 0, which commonly contains userdata.
Use `--edl-backend edl-ng --edl PATH_TO_EDL_NG` for later stage/readback/restore operations too.

```sh
python device.py verify-backup --backup backup/stock
```

The manifest records the identity, file sizes and SHA-256 hashes. Verification detects missing,
empty and modified files; it does **not** prove that a restore works or that EDL dumped every
partition correctly. Inspect the GPT/rawprogram files and dump log, confirm boot, ABL, devinfo
and device-specific calibration partitions are present, and preserve an independent copy.
A failed dump has no complete manifest and cannot be used for subsequent stages.

The dumped stock `boot` image also works with `python build.py build --stock-boot PATH`.
Find its actual filename in the dump; do not assume an A/B layout or active slot.

## 3. Stage engineering bootloader assets

Use exact partition names from the captured GPT. The following example assumes an unsuffixed
`abl` partition; use `abl_a`/`abl_b` only if the device's partition table says so. Stage any
required devinfo image separately with the same command and `--partition devinfo`.

```sh
python device.py stage --identity out/device.json --backup backup/stock --loader assets/firehose.elf --partition abl --image assets/engineering-abl.elf --sha256 EXPECTED_SHA256 --readback out/engineering-abl-readback.bin
```

Preview is the default. Add `--execute --confirm-device YOUR_ADB_SERIAL` only when ready to
write. Each selected partition must have exactly one hash-verified original backup. The
engineering image must fit its original partition and match the supplied hash. This checks
integrity and size, not provenance or hardware compatibility. Writes are followed by a fresh
EDL readback comparison of every supplied image byte; unused partition padding is not compared.
Readback paths must be new and outside the backup directory. No reboot happens automatically.
If a write/readback fails, remain in EDL and recover using the original image.

## 4. Unlock and verify persistence

Manually enter fastboot with the engineering ABL active:

```sh
python device.py unlock --identity out/device.json --backup backup/stock
```

This previews the OEM code, SELinux permissive command, normal unlock, critical unlock and
bootloader reboot, in that order. Execution requires all three flags:
`--execute --confirm-device YOUR_ADB_SERIAL --accept-data-loss`.
**Unlocking wipes user data and the upstream sequence enables permissive SELinux.**
If fastboot exposes a different USB serial, pass it with `--serial FASTBOOT_SERIAL` after
checking the physical device. Every command must succeed; failures stop the sequence.

The tool checks both normal and critical unlock state **after** rebooting the bootloader.
On the tested 5.13.7 headset, issuing unlock before `oem setenforce 0` returned
`Set device unlocked! failed: 00000050` and changed only the reported in-memory flags;
they reverted after reboot. The corrected order persisted both flags across a reboot.
Locked, missing or contradictory state is failure. Upstream reports that unlock persistence
can require another attempt; there is no automatic retry loop. Inspect the failure and rerun
explicitly if appropriate. You can also inspect without modifying the device:

```sh
python device.py check-unlocked --critical
```

## 5. Test with engineering ABL, then restore stock ABL

Boot PICO OS and follow its recovery prompts for the required factory reset. Complete setup
and authorize USB debugging again before using the [hardware acceptance runner](HARDWARE-TEST.md).
The original PICO 4's volume rocker is on the top edge of the rigid right-hand strap, just
behind the headset body. In recovery, use Volume +/- to move and a short Power press to select
the reset and confirm it; select `Reboot system now` afterward if shown. See
[PICO's recovery instructions and button diagrams](https://www.picoxr.com/uk/support/faq/recovery-restore-factory-settings).
Keep engineering ABL active for RAM-boot experiments: restoring production ABL can remove
usable fastboot commands even when the unlock bits persist. A successful unlock alone does
not establish that `fastboot boot` works; the runner checks that on the device and has no
automatic flashing fallback.

After the experiments, re-enter EDL. Restore each ABL partition you staged from its original
verified backup for normal boot reliability:

```sh
python device.py restore-abl --identity out/device.json --backup backup/stock --loader assets/firehose.elf --partition abl --readback out/stock-abl-readback.bin
```

Add `--execute --confirm-device YOUR_ADB_SERIAL` to write and verify. Restoration deliberately
does not require a successful unlock state: it must also work when recovering a failed attempt.
It restores ABL only; devinfo is not automatically reverted because that may undo the workflow.
Do not automatically restore encrypted userdata across resets or firmware changes.

The runner verifies return to the original Android build while engineering ABL is still
installed. It does not restore ABL or relock the device; verify another normal boot after
restoring ABL. The flashing fallback in [README.md](README.md) remains a separate, persistent
operation requiring working recovery.

## Host verification

```sh
python -m unittest discover -s test -p "test_*.py" -v
```

Tests exercise protocol vectors, tool failures, ambiguous/unauthorized devices, backup tampering,
preview behavior, write/readback, data-loss gating and unlock persistence without USB hardware.
