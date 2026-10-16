# Automated hardware acceptance test

The acceptance criterion is a Linux shell on the original PICO 4, with working USB-network
diagnostics, protected persistent storage and a verified return to the same stock PICO OS.
It does not establish working VR, displays, tracking, audio or GPU acceleration.

## Run from RAM

First complete [device preparation](DEVICE.md), including recovery backups. Start in PICO OS
on an already-unlocked headset, with USB debugging authorized. Install Python 3.9+, Android
platform-tools and OpenSSH (`ssh` and `ssh-keygen`), all accessible from the host. The host must
have a working USB network driver and route to the headset's USB IP address. Initial host
driver setup and physical recovery from a hard hang remain supervised tasks.

```sh
python hardware_test.py run --serial YOUR_ADB_SERIAL --image out/pico4-poc-5.13.8-global-sek.img
```

This command **operates on the headset**: it writes temporary preflight files under
`/data/local/tmp`, reboots to fastboot, RAM-boots a test image and reboots back. It never
unlocks, flashes, resets user data or automatically falls back to flashing if RAM boot is
unsupported. The source image is not modified.

Use `--fastboot-serial SERIAL` if the same headset exposes a different serial in fastboot.
All ADB/fastboot operations target explicit serials. Use `--host 172.16.42.1` for the USB IP
configured in the image, and `--adb`, `--fastboot`, `--ssh`, `--ssh-keygen` for executable paths.
The runner does not change host networking or install drivers.

Use `--out out/my-test` to choose a **new** output directory. Existing results are never
overwritten. Run one hardware test at a time against a headset; concurrent runs are unsupported.

## Explicit boot-partition test

On the tested PICO 4 with the signed Neo3 engineering ABL, `fastboot boot` returned
`unknown command` after transferring the image. The RAM test correctly failed and verified
return to PICO OS. It never falls back to writing storage.

Use the separate `flash-run` command only when deliberately choosing a persistent boot write:

```sh
python hardware_test.py flash-run --serial YOUR_ADB_SERIAL --fastboot-serial YOUR_FASTBOOT_SERIAL --image out/pico4-poc-5.13.7-global-seko.img --backup backup/stock --confirm-device YOUR_ADB_SERIAL --out out/flash-test
```

This temporarily replaces the headset's single `boot` partition, runs the same Linux checks,
then restores the original boot image through fastboot. It does not write ABL, recovery,
userdata, calibration or any other partition, and it does not unlock or relock the bootloader.
Keep engineering ABL installed until restoration is complete.

Before rebooting, it verifies every backup manifest entry, matches the backup identity to the
live headset/build, checks that the POC retains the stock kernel, device trees and boot layout,
and pins a verified copy of the original boot image in the result directory. Before writing,
it requires an unlocked, non-slotted boot partition with the backed-up size and sends the
engineering ABL's per-session OEM authorization command. `--confirm-device` must match `--serial`.
The partition check uses `getvar all`: this older ABL does not implement `has-slot:boot`.
The inventory must list exactly `boot` and no `boot_a`/`boot_b` partitions.

The result records `boot_restore_required: true` and manual restore commands **before** the
first flash. Even a failed, timed-out or interrupted write triggers restoration. A running
POC is asked to reboot to the bootloader, where the pinned stock image is checked again and
flashed. A failed restoration leaves the headset in fastboot when possible and keeps the
restoration requirement in the result. A hard hang can still require physical recovery.

In Linux, the runner hashes the written test-image bytes read back from `boot`. Stock recovery
requires fastboot to acknowledge the original-image write and the original Android identity
to complete boot. `boot_restore: android_boot_verified` denotes those checks; it is **not**
a full stock-partition byte readback. An independent EDL readback can establish that separately.

Killing the host process or losing power can leave the POC in `boot`. In that case, use the
saved `manual_restore_commands` and `stock-boot.img` once the headset is in fastboot, or use
the original EDL backup. A normal reboot alone cannot restore PICO OS while the POC is flashed.
The result directory deliberately retains the stock restoration image.

## What is checked in both boot modes

1. Validate the source image and record its SHA-256. Derive a RAM-only test variant with
   a fresh SSH public key and run identifier; record its distinct SHA-256. Kernel, DTBs
   and boot layout are retained. Preflight uses userspace extracted from the same source
   image, preventing accidental pairing with another rootfs tar.
2. Record the selected headset's fingerprint, OEM state and SoC ID, after PICO OS reports
   boot completion. Run all userspace preflight checks and require a successful exit code
   and a complete, zero-failure summary. Clean up temporary preflight files on failure too.
3. Enter fastboot and require a known unlocked state. `run` uses `fastboot boot`;
   `flash-run` performs the additional backup/partition checks above and flashes only `boot`.
4. Establish noninteractive SSH with the per-run key. Check the run identifier and SoC ID
   before treating the endpoint as the headset. Compare build metadata, kernel release and
   ARM64 architecture, and execute a command in the root shell.
5. Read `/sys/class/block/*/ro` for disks and partitions. Require read-only physical storage
   and device-mapper devices; RAM/loop/zram/nbd devices are excluded from this physical-storage
   assertion. Verify RAM-backed root and no writable `/dev/*` filesystem mounts. These are
   observational checks: the test does not attempt a write to real headset partitions.
6. Fetch HTTP status, this boot's log and a valid hardware-report archive identifying this
   run. Save the report and kernel log locally. HTTP bypasses proxy settings and rejects
   redirects. Host mounting of the USB log drive is not part of this test.
7. For `flash-run`, first return to fastboot and restore the original boot image. Request a
   normal reboot, wait for the original ADB serial, require Android boot completion
   and compare fingerprint, OEM state and SoC ID with the baseline. Returning to fastboot
   alone does not count as recovery.

The derived image tests the supplied POC with authentication/identification instrumentation;
it is not a claim that the unmodified source image was booted byte-for-byte. The temporary SSH
private key is deleted when the runner finishes. SSH host keys are accepted on first connection
into an isolated per-run known-hosts file, then pinned for subsequent connections. No password
prompts, personal SSH configuration or global known-host updates are used.

## Failure and timeout behavior

Exit code `0` means every acceptance check **and stock recovery** passed. Any failed check or
failed recovery returns `1`; a successful recovery never erases an earlier failure.
`result.json` records each phase, timestamps, identities, image hashes, error and recovery status.
Numbered command JSON/log files retain subprocess output, including partial output on timeout.
Keep these artifacts private: they contain device identifiers and hardware details.

Default limits are 180 seconds for fastboot/SSH readiness and RAM boot, 180 seconds for report
readiness, and 420 seconds for recovery. Override with `--boot-timeout`, `--report-timeout` and
`--recovery-timeout`. Each subprocess and HTTP transfer is bounded; phase limits can be exceeded
by a final in-flight command's timeout. Preflight has separate bounded push/run/cleanup commands.
For flash tests, waiting for fastboot restoration and waiting for Android each have their own
recovery limit; each flash uses the boot timeout.

Rebuild old POC images before testing: the runner requires the independent `recovery-watch`
service. Each test image sets a device connection deadline of `boot-timeout + 30` seconds
and a maximum runtime of `boot-timeout + report-timeout + 300` seconds, measured from Linux
startup. Enumeration, carrier and DHCP traffic cannot cancel the test connection deadline;
only the runner's verified SSH identity check acknowledges it. The maximum runtime still
returns to fastboot if the host runner dies after authentication. These limits apply even
when the source image disables the interactive auto-reboot timer.

After a failure following the first reboot, recovery is attempted in a `finally` path.
In RAM mode:

- If this run's SSH identity is verified, collect a best-effort boot log and request PICO OS.
- If the selected serial appears in fastboot (including the POC's automatic fallback), request
  a normal boot and wait for matching Android identity and boot completion.
- If neither interface returns, mark recovery failed and leave logs for manual recovery.

A wrong SSH endpoint is never sent a reboot command. There is no automated EDL recovery or
power cycling. Closing/killing the host process bypasses Python cleanup; Ctrl+C triggers a
recovery attempt, while a second interruption can terminate it. A hard hang still requires
physical intervention. Missing hardware cannot produce a passing result.

## Preflight only

```sh
python hardware_test.py preflight --serial YOUR_ADB_SERIAL --rootfs out/pico4-poc-5.13.8-global-sek-rootfs.tar
```

This mode leaves PICO OS running and needs no unlock, fastboot or SSH. `--serial` may be omitted
only when exactly one authorized ADB device is attached. Existing `scripts/preflight.ps1` and
`scripts/preflight.sh` now delegate to this runner and preserve its exit status. They keep the
latest-rootfs convenience; PowerShell accepts `-Serial`, shell accepts serial as argument two.
The recorded fingerprint/OEM state can be passed to `build.py match` to select firmware.

## Development validation

```sh
python -m unittest discover -s test -p "test_*.py" -v
```

Simulated-device tests cover success, failed preflight, locked/unsupported bootloaders, wrong
SSH identity, wrong build, writable partitions, broken HTTP and unsuccessful stock recovery.
Flash-mode cases additionally cover wrong/tampered backups, mismatched kernels or partition
layouts, partial/interrupted writes, changed restore files, readback failures and failed restores.
They also exercise archive validation, command timeouts and artifact preservation. They do
not replace a real headset run. QEMU tests remain a separate userspace regression check.
