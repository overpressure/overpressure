# Installed system

The Linux system can be installed on the headset, so that it starts without the PC, keeps its
data and has room for Steam and its library. The layout follows PICO OS's own: a boot image, a
read-only system that updates replace whole, a writable data partition, and a separate
maintenance environment in `recovery`. Installing replaces PICO OS's system and data; the way
back writes them from the EDL backup (PICO OS then starts with fresh data, as after a factory
reset, because the backup does not hold its userdata). The installed system runs on Valve's kernel
([kernel.md](kernel.md)); the maintenance environment stays on the stock
kernel.

## Layout

| Partition | Size | PICO OS | Linux |
| --- | --- | --- | --- |
| `boot` | 96 MiB | kernel and first-stage ramdisk | Valve's kernel, its device tree and the command line that starts the system image; no ramdisk ([the boot image](#install-and-update)) |
| `recovery` | 100 MiB | recovery | the maintenance stage 1 on the stock kernel: development, installs, rescue |
| `super` | 8 GiB | the read-only system (system, vendor, product, odm) | the system image: read-only ext4, about 4.6 GiB |
| `userdata` | 106 GiB | apps and data (f2fs, file-based encryption) | the data: ext4 `pico4-data`, mounted at `/data` |
| `misc` | 1 MiB | the bootloader message | the same: it selects the maintenance mode |
| `metadata` | 16 MiB | userdata's encryption metadata | unused; wiped on the way back |

Every other partition stays read-only; the calibration and firmware the system needs
come from the image ([stage1.md](stage1.md), [xr.md](xr.md)).

## The installed system

- **System image.** [`scripts/build-system-image.sh`](../scripts/build-system-image.sh) turns the
  userspace archive into ext4 for `super`, as root in the build container: no journal (it is never
  written on the headset), without `orphan_file` and `metadata_csum_seed` (the stock kernel under the
  maintenance stage 1 cannot mount them), file capabilities kept
  (gamescope's `cap_sys_nice`). Its size is the
  content plus 10 % and 128 MiB. It names the archive it came from in
  `/usr/share/pico4/system-image`. Beside it the script writes `pico4-system.img.zst` (zstd, level
  3, about 38 % of the image) and the image's SHA-256 in `pico4-system.img.zst.sha256`: `dev install`
  sends the compressed copy when that hash is the image's.
- **Data.** [`preinit`](../userspace/rootfs/usr/libexec/pico4/preinit), the system's first
  process ([how it starts](#how-it-starts)), mounts `userdata` at `/data` and lays the changing
  parts onto the read-only image: `/etc` as an overlay (lower: the image, upper:
  `/data/etc/upper`, without the inodes index), `/var`, `/home` and `/root` as binds of
  `/data/var`, `/data/home` and `/data/root`. On the first boot these start as copies of the
  image's. SteamOS's offload units bind `/srv`, `/var/log`, `/var/tmp`, the pacman cache, flatpak
  and container storage from `/home/.steamos/offload`, so they land on the data partition too.
- **Updates.** A new system image replaces `super` whole; the data stays. When preinit sees an
  image it has not booted before, `/var/lib/pacman` (the package database, which describes the
  image) is taken from the new image.
- **Boot indication.** [`pico4-splash`](../src/splash/pico4-splash.c) shows a turning ring of
  dots in the middle of both lenses while the system starts. preinit starts it before it mounts
  the data partition; systemd's `pico4-splash.service` takes the display from it. It sets the
  panels' 90 Hz mode on msm's primary plane and then gives up DRM master, so the next compositor
  (Monado, SteamVR's) becomes master as usual; its commit ends the indication. After four minutes
  it ends in any case.
- **SSH.** `dev install` writes the enrolled keys to the data partition
  ([security.md](security.md#logins)). The installed system makes its host keys on the first boot
  (`sshdgenkeys.service`) and keeps them in the `/etc` overlay; the maintenance stage 1 makes new
  ones on every boot, so the two answer with different keys.

### How it starts

`boot` holds Valve's kernel, its device tree and a command line, no ramdisk. The command line adds
`root=PARTLABEL=super rootfstype=ext4 rootwait=20 ro init=/usr/libexec/pico4/preinit
pico4.root=installed pico4.autoreboot=0` to the kernel's own words (`clk_ignore_unused`,
`pd_ignore_unused`, `regulator_ignore_unused`, `panic=5`). The kernel mounts the system image in
`super` read-only as the root, mounts its devtmpfs on `/dev` and starts preinit, a bash script in
the image:

1. It mounts proc, sysfs and `/run`, and starts the boot indication (not in charger mode). It sets
   every CPU's cpufreq governor to `schedutil`, as PICO OS runs (Valve's kernel starts with
   `performance`; `pico4.cpugov=keep` leaves it).
2. `devnode sweep` switches every disk and partition read-only and makes the
   `/dev/block/by-name` links, before anything is mounted writable. It reads sysfs with shell
   builtins and switches all devices in one `blockdev` call: 0.03 s for about 120 devices
   (`pico4-storage-ro.service` runs it again before `local-fs-pre.target`).
3. It makes `userdata` (the `pico4-data` file system) writable, and on this kernel its disk too,
   since a mainline kernel counts a disk's read-only flag for its partitions; the disk's other
   partitions keep their own flags. It mounts the data at `/data`. Outside charger mode it counts
   the boot ([boot attempts](#boot-attempts)).
4. On the first boot it copies `/var`, `/root` and `/home` from the image (`cp -a -n`); after a
   new system image it takes the package database from the image.
5. It mounts the `/etc` overlay and binds `/var`, `/home` and `/root`, and writes
   `/etc/machine-id` from the headset serial, so that journals of different boots belong together.
6. It executes systemd, in charger mode with `--unit=pico4-charger.target`
   ([charger mode](#charger-mode)).

Any failure on the way reboots into `recovery` (`pico4-reboot recovery`), where the maintenance
stage 1 waits. If `super` cannot be mounted at all, the kernel waits 20 s, panics, and `panic=5`
reboots it, again from `boot`; the way out is fastboot and `dev recovery`, or EDL by hand (both
volume buttons held while connecting USB to the powered-off headset,
[device preparation](device-preparation.md)). `super` is written only by `pico4-install`, which
checks the stream and reads it back.

### Boot attempts

A boot that started systemd can still fail later: a kernel panic reboots after 5 s (`panic=5`),
and the kernel's detectors panic on soft lockups and hung tasks (120 s). systemd arms the SoC's
watchdog (`qcom_wdt`, `RuntimeWatchdogSec=30s` in `system.conf.d/50-pico4.conf`) and pings it
while PID 1 runs. Its bite alone does not reset the headset on this firmware (it locks one CPU
up), so the bark, 1 s before it, panics (the pretimeout governor `panic` in `pico4.config`):

| A stopped PID 1 (ptrace, no pings) | After |
| --- | --- |
| the bark: `Kernel panic - not syncing: watchdog pretimeout event` | 28.3 s |
| `panic=5`'s reset | 33 s |
| the installed system answers SSH again; the graphical target reached, the count cleared | 55 s |

A suspend does not count against the watchdog: 90 s asleep with it armed (`pico4-suspend-check
--seconds 90`) woke into the same boot. A system that only crawls (every CPU busy at real-time
priority) does not stop the pings: the
kernel's fair server leaves normal tasks 5 % of each CPU, and the hung-task panic restarted it
after about 7 minutes.

[`boot-attempts`](../userspace/rootfs/usr/libexec/pico4/boot-attempts) counts such boots in
`/data/.pico4-boot-attempts`:

| When | What happens |
| --- | --- |
| preinit, before systemd (not in charger mode) | the count goes up by one |
| the graphical target is reached (`pico4-boot-ok.service`) | the count is cleared |
| preinit finds 2 unfinished boots | it clears the count and reboots into `recovery`: the maintenance stage 1 starts, and a boot from there has two tries again |

A failed unit does not hold the graphical target back. A system that runs but never reaches it
answers over SSH, and its next boot counts on.

Under systemd:

- **USB link.** `pico4-usb-gadget.service` runs
  [`usb-gadget`](../userspace/rootfs/usr/libexec/pico4/usb-gadget) early (before
  `sysinit.target`): the composite gadget the maintenance stage 1 sets up, `1d6b:0104` "PICO 4
  Linux" with `androidboot.serialno` as its serial and the MAC addresses derived from it, NCM with
  Microsoft OS descriptors, a read-only removable mass-storage drive that starts without a medium
  and is filled by `pico4-userspace-logdisk`, and ACM without a shell. `/run/pico4/usb-link.env`
  records the interface, MAC and address. systemd-networkd configures `usb0` from
  [`50-pico4-usb.network`](../userspace/rootfs/usr/lib/systemd/network/50-pico4-usb.network):
  `172.16.42.1/24` and a DHCP server with one lease, `172.16.42.2`, without router, DNS or NTP,
  so the PC keeps its own internet route. The Frame's own gadget units stay masked.
- **Windows.** Valve's kernel's NCM function takes Windows 11's in-box driver as it is: the
  installed system needs no [NCM initialization](hardware-testing.md#windows-ncm-initialization),
  which only the maintenance stage 1 on the stock kernel needs.
- **Wi-Fi.** ath11k probes the QCA6390 while the kernel starts, before the system image is
  mounted, and fails without its firmware (`amss.bin`). `pico4-wlan-probe.service`
  ([`wlan-probe`](../userspace/rootfs/usr/libexec/pico4/wlan-probe)) binds it again once
  `/usr/lib/firmware` holds it, before NetworkManager, and waits up to 15 s for `wlan0`.
- **Display and GPU.** They bind without a ramdisk; the GPU loads its firmware from the system
  image at its first use.

## Boot modes

The ABL starts `recovery` instead of `boot` while `misc` holds the bootloader message
`boot-recovery`, as on Android:

| Mode | Starts | Entered by | Left by |
| --- | --- | --- | --- |
| Installed | `boot`: the kernel mounts `super` and starts preinit, which mounts `userdata` and executes systemd, with or without a PC; if that fails, it reboots into recovery ([how it starts](#how-it-starts)) | a boot with an empty bootloader message | `dev recovery` |
| Maintenance | `recovery`: the dev image's stage 1 on the stock kernel with its deadlines; `dev install` writes from here | `fastboot reboot recovery` (the ABL writes `boot-recovery`), or a reboot with reason `recovery` (once) | `pico4-install bootmode normal` (`dev normal`) |

The deadlines of the maintenance stage 1 return the headset to fastboot; with
`boot-recovery` in `misc`, every boot from there starts the maintenance stage 1 again.

### Charger mode

With the USB cable attached, a powered-off headset starts again: the PMIC powers up on USB,
and the bootloader boots with `androidboot.mode=charger`. PICO OS then runs AOSP's `charger`
instead of its system (`init.qcom.rc`). The installed system does the same: preinit starts
systemd with `pico4-charger.target` instead of the default target. It holds `basic.target`,
`pico4-charger.service` ([`charger`](../userspace/rootfs/usr/libexec/pico4/charger)), the USB
link, systemd-networkd and sshd; `pico4-splash.service` does not run in charger mode, so the lenses
stay dark. Holding the power key for 2 s starts the system (a plain reboot, which the bootloader
starts normally), and 10 s without USB power turn the headset off, as AOSP's charger does.
[tests/test_charger.sh](../tests/test_charger.sh) tests the key and power decisions on the host.

### Power key

PICO OS's window manager puts the headset to sleep on a short press (screen off, then the
kernel's suspend) and shuts it down without asking once the key is held 2 s (its long-press
timeout; `factory.long_press_power_off=1` makes the long press a power-off instead of the
menu). [`pico4-powerkey.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-powerkey.service)
does the same from the PMIC's key (`pm8941_pwrkey`): a press released within 2 s runs
`systemctl suspend` (s2idle, the only sleep state this kernel offers; the key wakes the headset,
and the press that woke it is ignored), a press held 2 s runs `systemctl poweroff`, off in about
7 s. logind ignores the key. Held 8.7 s the PMIC resets the headset.

After a resume Valve's session goes on with 6DoF, both controllers, the cameras, USB and sound.
PICO OS also sleeps when the headset is taken off (screen off after 10 s, suspend after 15 s); here
SteamVR's standby follows the wear sensor ([session.md](session.md)). After a wake Steam can suspend again:
seen about 4 s after an RTC wake from 3 h asleep, with the cable in. It left the headset awake through 80
idle minutes in SteamVR's standby, so this is no plain idle timer.

Across a suspend the QTimer, which stamps the IMU samples and the camera frames, runs on while
CLOCK_MONOTONIC stands. The driver sees the system slept (CLOCK_BOOTTIME less CLOCK_MONOTONIC
grew), maps both clocks anew before the next samples, drops IMU samples that do not move forward,
and starts the camera capture again, because it streamed into the suspend.

## Boot images

`python -m overpressure dev image` writes two images from the stage-1 image to `out/dev/` (private:
the recovery image carries vendor data, the stock recovery's device-tree overlay and AVB footer,
from the public OTA of the headset's firmware):

- `dev-boot.img`: the dev image for `boot`.
- `dev-recovery.img`: the same stage 1 for `recovery`, marked `pico4.part=recovery` on its
  command line. It is a whole-partition image, because the ABL treats `recovery` differently:
  - it loads the device-tree overlay from the image itself (`recovery_dtbo`), taken from the
    backup's stock recovery;
  - `recovery` is a chained AVB partition: the stock image is followed by its own signed vbmeta
    (RSA-4096) and a footer in the partition's last 64 bytes. The ABL checks that chain on every
    boot, not only on recovery boots. An image that overwrites the vbmeta leaves every boot in
    fastboot; a hash mismatch alone is tolerated on the unlocked headset. The image therefore
    carries stock's vbmeta unchanged behind the stage 1 and a footer that points to it
    ([`overpressure/avb.py`](../../../overpressure/avb.py)).

The installed system's boot image comes from `python -m overpressure dev boot-image`
([below](#install-and-update)).

Large raw downloads crash the ABL's fastboot (the 100 MiB stock recovery left the headset in
crash-dump mode); whole-partition images therefore go as sparse images
([`overpressure/sparse.py`](../../../overpressure/sparse.py)), whose zero runs are written as fill chunks.

## The installer

The installer does three things, which the window asks for right after the PICO 4 is chosen: the
install, the upgrade and the way back to stock ([below](#the-upgrade-and-the-way-back)); its page also exports and
imports the headset's backup ([below](#the-backup-as-one-file)).

`python -m overpressure install`, and the window's installer on top of it ([README](../README.md#install)), takes a
headset from PICO OS to the installed system in five stages, with the commands of this document and of
[device-preparation.md](device-preparation.md) ([`tooling/install.py`](../tooling/install.py); the engine:
[docs/tool.md](../../../docs/tool.md#the-installer)). It goes on where it stopped when started again;
after an earlier run the window, once the PICO 4 is chosen, opens at a start page with *Continue where you left off* and *Start over
from…* a stage (Prepare your headset and Install write to the headset, so starting over there repeats the
backup and the writes). A stop (*Stop*, closing the window, Ctrl-C) ends any wait at once, such as the up to
ten minutes Connect your headset waits for the headset on USB; a task that writes (`dev stage-abl`, the
unlock, `dev flash`, `dev install`, the Wi-Fi profile) finishes its write first.

| Stage | Tasks | Headset |
| --- | --- | --- |
| Check this computer | `tools`, `device fetch-unlock-files`, then the doctor's checks: what fails, apart from what the installer finds out itself (serials, identity, backup) and builds (the assets), stops it with the fixes | not needed |
| Build the system | the window's build steps, each skipped while its output is there and not older than its input: assets, stage-1 image, userspace archive, system image, dev image, Valve's kernel, boot image | not needed |
| Connect your headset | an action ask (developer mode, USB debugging, the cable, the prompt in the headset); exactly one `adb devices` entry in state `device`, written to `PICO4_ADB_SERIAL`; `device probe` unless `PICO4_IDENTITY` already holds this headset (a record of another headset stops it: records are never overwritten); the firmware (`check_firmware`) and the panel (`check_panel` on PICO OS's `/proc/cmdline` over adb). An unverified one ends the run here | PICO OS |
| Prepare your headset | one consent (it erases the headset; the backup is the way back); `adb reboot edl`, `edl-ng printgpt` for the LUNs (LUN 0 last) and the serial (`PICO4_EDL_SERIAL`), `device backup --execute` without userdata, `device verify-backup` (an unfinished backup folder stays, the new one goes to `backup/stock-TIME`, written to `PICO4_BACKUP`); `dev stage-abl`; `adb reboot bootloader`, exactly one fastboot device (`PICO4_FASTBOOT_SERIAL`), `device unlock --execute --accept-data-loss`; `dev flash`, `dev connect` | EDL, fastboot, maintenance stage 1 |
| Install | a form for Wi-Fi (both fields optional; the answer stays in memory and is asked again after a restart); `dev install`, `dev installed`; the Wi-Fi profile as a NetworkManager keyfile (WPA-PSK, or open without a password) through SSH's stdin: `install -D -m 600 /dev/stdin /etc/NetworkManager/system-connections/overpressure-wifi.nmconnection && nmcli connection reload`, so neither the network name nor the password is in a command line, a log or `.env`; `dev check`; then "Unplug it and put it on" | on the cable |

After the backup the installer suggests exporting it ([below](#the-backup-as-one-file)). From the unlock the
installer goes straight to fastboot and `dev flash`, without PICO OS's factory reset of
[device-preparation.md](device-preparation.md#5-factory-reset-and-stage-1-work): the install formats `userdata`
anyway. Should PICO OS or the bootloader turn out to need it, it is an action ask with the reset's instructions
after the unlock (`Plan(factory_reset=True)`), not a failure.

### The upgrade and the way back

`python -m overpressure upgrade` (the window's *Upgrade*) builds this version of the system and installs it over
the one this computer installed, keeping the data partition; it is offered only when this computer has
installed the system (`out/dev/installed.json`). `python -m overpressure restore` (the window's *Restore to
stock*) brings back PICO OS and its bootloader from the backup; it needs a backup on this computer, made by
the install or imported. A finished workflow forgets the others' recorded progress (the install starts
afresh after a restore).

| Workflow | Stage | Tasks | Headset |
| --- | --- | --- | --- |
| Upgrade | Check this computer | `tools`, the doctor's checks | not needed |
| Upgrade | Build the system | as the install's, but the userspace archive, the system image, Valve's kernel and the boot image are built on every upgrade (each build reuses what it can) | not needed |
| Upgrade | Install the upgrade | an action ask (the headset switched on, on the cable); `dev recovery`, `dev install` (the data partition kept), `dev installed`, `dev check` | installed system, maintenance stage 1 |
| Restore | Check the backup | `tools`; `device verify-backup` of `PICO4_BACKUP`, which must be the headset `PICO4_IDENTITY` describes | not needed |
| Restore | Restore PICO OS | one consent (it erases the system and its data); an action ask (switched on, on the cable); a headset that answers `adb devices` with its serial runs PICO OS already and is left as it is, any other gets `dev restore --installed` ([back to stock](#back-to-stock)) | maintenance stage 1, fastboot |
| Restore | Set PICO OS up | an action ask: PICO OS's first-time setup, developer mode and USB debugging again; exactly one `adb devices` entry, written to `PICO4_ADB_SERIAL` | PICO OS |
| Restore | Stock bootloader | `cycle --restore-only`: the stock boot image verified, the stock ABL written back; then "Back on PICO OS". The bootloader stays unlocked | PICO OS |

### The backup as one file

`python -m overpressure backup export FILE` (the window's *Export backup…*) writes the backup into one checked file
([docs/tool.md](../../../docs/tool.md#the-installer)): the EDL backup `PICO4_BACKUP` names, the identity, this
computer's SSH key for the maintenance stage 1 (`out/dev/id_ed25519`, which the dev image in `recovery` trusts
alone), `out/dev/installed.json` once the system is installed, and the headset's serials. With them another
computer can restore the headset: `backup import FILE` (*Import backup…*) puts the backup under `backup/`, the
key and the record into `out/dev` where it has none, and names the backup, the identity and the serials `.env`
lacks. It refuses another headset's backup and a key different from this computer's own. The file is as private
as the backup: it reaches the headset's maintenance system.

## Install and update

```sh
# In the build container: Valve's kernel, from Valve's source package linux-72-deckard and the stock
# firmware's recovery and vendor images, into out/kernel (the window's "Valve's kernel" step)
python -m overpressure container run -- bash devices/phoenix/scripts/build-kernel-step.sh \
    cache/stock cache/pico4-stock/5.13.7-global-seko /var/tmp/pico4-kernel-build
python -m overpressure dev image
python -m overpressure dev boot-image         # out/kernel/valve-boot.img
python -m overpressure container run -- bash devices/phoenix/scripts/build-system-image.sh \
    out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img
python -m overpressure dev flash              # a new dev image: the maintenance stage 1; starts it
python -m overpressure dev recovery           # or, with the dev image in recovery already: into it
python -m overpressure dev install            # boot, super, userdata (if new), then the installed system
python -m overpressure dev check              # the acceptance checks, on the installed system
python -m overpressure dev installed          # after a later reboot: connect to it again
```

`dev install` runs from the maintenance stage 1 and writes through
[`pico4-install`](../initramfs/usr/bin/pico4-install) there, which writes only these:

| Command | Writes | Checks |
| --- | --- | --- |
| `pico4-install write boot\|super --size --bytes --sha256 [--zstd]` | the image to the start of the partition, straight into `dd` in 4 MiB blocks; `--zstd` decompresses the stream first | the partition's size equals the backup's, the image fits, a readback of the written bytes with the image's SHA-256 |
| `pico4-install hash NAME BYTES` | nothing | the SHA-256 of the partition's first BYTES (`dev install` checks with it whether a partition holds its image already) |
| `pico4-install format-data --size` | ext4 `pico4-data` on `userdata` | the backup's size; the label afterwards |
| `pico4-install keys --size --id` (standard input: `authorized_keys`) | on the data partition: `/root/.ssh/authorized_keys` and `/etc/pico4/install-id` in the `/etc` overlay's upper layer | the backup's size, the `pico4-data` label, at least one public key |
| `pico4-install wipe userdata\|metadata --size` | zeros over the first 64 MiB | the backup's size; a readback |
| `pico4-install bootmode recovery\|normal` | the 2 KiB bootloader message in `misc` | a readback |

Before the first write `dev install` and `dev restore` read the battery: below 20 % they start
only while it charges (`Charging` or `Full`). A battery they cannot read gets a warning. A headset
that switches off halfway, or whose cable is pulled, starts the maintenance stage 1 again (`misc` still
requests it until the install ends), but the write is lost. For an update that is the whole way out: `dev install`
again (or the installer's Update) writes `boot` and `super` anew, and `userdata`, which an update does not write,
keeps its data. This follows from the order and `misc`; a cable has not been pulled in an update on the headset.

Each partition is made writable for its write only. `dev install` skips a partition that
already holds its image (by a readback hash), formats `userdata` only when it holds no
`pico4-data` file system (or with `--format-data`), and then writes the enrolled keys and the
install ID with `pico4-install keys` ([security.md](security.md#logins)). An update is the same
command with a new system image: the data stays, `boot` and `super` are written only when they
differ, and the keys are written again.

**The boot image.** `dev install` writes `out/kernel/valve-boot.img` to `boot`: the
installed system's boot image with Valve's kernel and tree ([kernel.md](kernel.md)).
`dev boot-image` makes it with `kernel/recovery_image.py --direct --boot KERNEL_OUT
STOCK_RECOVERY DEV_RECOVERY OUT`, taking the header from the dev image's recovery image: no
ramdisk, the command line of [how it starts](#how-it-starts), no AVB footer (the bootloader applies
the `dtbo` partition's overlay, as on every normal boot). It holds no keys and no firmware; the
system image has them. A new boot image alone goes through `dev recovery`, then
`dev install --boot PATH`. The way back to PICO OS is `dev restore`.

| Command | Purpose |
| --- | --- |
| `python -m overpressure dev recovery` | into the maintenance stage 1 (through fastboot), which stays until `dev normal` |
| `python -m overpressure dev normal` | from maintenance into the installed system |
| `python -m overpressure dev installed` | connect to the running installed system (after a reboot) |
| `python -m overpressure dev restore-partition misc\|recovery\|boot` | write one partition back from the backup through fastboot (sparse, size- and hash-checked) |

## Back to stock

`python -m overpressure dev restore` knows from `out/dev/installed.json` (written by `dev install`)
that the system is installed, or from `--installed` where another computer installed it (the restore
stepper passes it unless PICO OS runs). From the maintenance stage 1 it writes the backup's `super` and
`boot` (checked against the manifest, read back), wipes the start of `userdata` and `metadata`
(both `formattable` in PICO OS's fstab, so PICO OS makes new file systems and keys), clears
`misc`, then writes stock `recovery` through fastboot and starts PICO OS. PICO OS's first-time
setup follows on the headset; ADB stays off until developer mode is enabled again, so returning
the ABL to stock (`python -m overpressure cycle --restore-only`) and entering development mode again
(`dev stage-abl`) wait for that.

### An interrupted restore

A restore that stops part way (the cable pulled, the battery empty, the host gone) leaves the headset where
its order put it, and each place has a way out. This follows from the order above and the boot rules; a
cable has not been pulled in a restore on the headset.

| Stopped while | Where the headset is then | Way out |
| --- | --- | --- |
| `super` or `boot` is written | every boot starts the maintenance stage 1 (`misc` keeps the request until the restore clears it) | `dev restore` again: it starts from the maintenance stage 1 and writes everything again |
| `userdata` and `metadata` are wiped, or `misc` is cleared | the maintenance stage 1, or, once `misc` is clear, PICO OS on the stock `super` and `boot`, making its data partition anew | `dev restore` again from the maintenance stage 1; from PICO OS only recovery is left: `adb reboot bootloader`, then `dev restore-partition recovery` |
| stock `recovery` is written through fastboot | every boot ends in fastboot: the ABL checks recovery's chain on every boot | `dev restore-partition recovery` |
| any of these, and nothing answers | — | EDL, which the chip's ROM always answers ([device-preparation.md](device-preparation.md#entering-and-leaving-edl)), and the backup written back from there |

## Verified results

| Check | Result |
| --- | --- |
| Recovery boot | the ABL starts the maintenance stage 1 from `recovery` (`fastboot reboot recovery`), readback identical; `boot-recovery` stays in `misc` until cleared |
| `misc` and `recovery` written back from the backup | identical to the backup by readback; `boot` starts again |
| System image from the 1,701 MiB archive | 4,707 MiB ext4; capabilities of gamescope, newuidmap and newgidmap kept |
| Install | `boot` (24 MiB) and `super` (4,706 MiB) written and read back, `userdata` formatted: 104 GiB for data; the keys and the install ID written to it (`pico4-install keys`), and `dev installed` logs in with them |
| Install time | `dev install` of a new system image: 2 min 22 s from the maintenance stage 1 to the running system; `super` from the 1,808 MiB compressed image written and read back in 101 s (the USB link carries 26 to 28 MB/s with any SSH cipher); `boot`, unchanged, skipped after its first MiB's hash |
| Direct boot | the ABL boots a boot image without a ramdisk; `root=PARTLABEL=super` mounts the system image (`sda7`) read-only 1.43 s after the kernel starts, and the image's init runs; the display and the GPU bind |
| Windows NCM on Valve's kernel | after a reboot the installed system answers SSH without the NCM initialization; no `Wrong NDP SIGN` |
| Start without stage 1 | preinit runs at 1.4 s and executes systemd at 1.9 s (its storage sweep takes 0.03 s); the USB link is up at about 4 s, `wlan0` (re-probed) at about 9 s; systemd `running`, no failed units; `/` read-only from `sda7` (writing to it refused), `/data` writable, `/etc` an overlay; the governor `schedutil` |
| Boot time | the graphical target 9.7 s and 11.0 s after the kernel starts, the session (`pico4-session.service`) started at 5.3 to 5.7 s; stage 1 in `boot` with an earlier system image: 23.3 s, twice. The audio DSP's chain (`pico4-lpass-macros`, 5 to 6 s) ends it |
| Charger mode | a power-off with the cable in starts `pico4-charger.target` (10.7 s): the charger service, `sshd` and the USB link run, the boot indication and the session do not; a plain reboot from it starts the system |
| Persistence | files in `/home`, `/var`, `/etc` and `/root` survive a reboot and a system update (a new image in `super`, its package database taken over) |
| `dev check` on the installed system | the userspace check passes (USB link up, storage, systemd, no unexpected listener). Head IMU passes. Valve's session: 90.03 commits/s, 0.0 % late, 0 stalls, hottest zone 67.1 °C after 30 s; SteamVR saw the headset, not the controllers, which were not connected |
| Development loop | `dev recovery`, `dev reboot` (stays in maintenance), `dev normal` (installed system again); from fastboot without the recovery request, `dev reboot` connects to the installed system that `boot` starts |
| Boot indication | Monado and SteamVR's compositor take the display from `pico4-splash.service` (it exits within 0.5 s) |
| Power-off | stays off without the cable; with the cable attached the headset starts again with `androidboot.mode=charger` |
| Installer | on the host, with fakes: the stages, the done checks, the serials written to `.env`, the refusals before any write, the Wi-Fi password on stdin only. On the headset, from PICO OS after a `dev restore`, on Windows: the headset's half as one path in about 11 minutes, from finding it through the panel check over adb (PICO OS's shell cannot read `/proc/cmdline`; the check reads what it may), the engineering ABL, the unlock (also of a headset unlocked before) and the maintenance stage 1 without a factory reset, to the install and `dev check`; the backup and identity were there already, the builds skipped, and no Wi-Fi was given. Not run: the LUNs from `printgpt` (the backup's step), the Wi-Fi keyfile in the read-only system's `/etc` overlay, the whole path with its builds |
| Restore to stock in the window | on the headset, on Windows: from the installed system through all four stages (the backup checked, PICO OS's system with fresh data, PICO OS's setup and USB debugging by the owner, the stock bootloader) to PICO OS with its stock bootloader; the install's recorded progress and `out/dev/installed.json` gone afterwards |
| The backup as one file | this computer's backup (116 files, 12.5 GiB): exported with the read-back in 57 s, imported into another place and verified in 81 s, with the SSH key and the serials |

## Open work

1. **The way back.** `dev restore` for an installed system ran on the headset and ends in PICO OS's
   first-time setup. A restore interrupted part way has its ways out ([above](#an-interrupted-restore)), not tried
   with a pulled cable. Not run on the headset: the upgrade, and a restore from an imported backup on another
   computer.
2. **Integrity.** The system image is checked when written, not at boot (no dm-verity).
3. **The direct boot on the headset.** Still to run: the power key held 2 s in charger mode, the
   lenses dark there (by eye), a boot without the PC, and `dev check` with both controllers
   connected on an image with the new check.
4. **The installer on a headset.** The whole path with its builds and a new backup: the LUNs from `printgpt`, and the
   Wi-Fi keyfile on the installed system (WPA2; a WPA3-only network may need `key-mgmt=sae`).
