# Installed system

| | |
| --- | --- |
| What | Linux installed on the headset, in PICO OS's layout: Valve's kernel in `boot`, a read-only system image in `super`, data in `userdata`, the maintenance stage 1 (stock kernel) in `recovery`. It starts without the PC and keeps its data across reboots and updates |
| Install, upgrade, back to stock | `python -m overpressure install`, `upgrade` or `restore`, or the window's installer ([the installer](#the-installer)) |
| By hand | `dev recovery`, then `dev install` ([install and update](#install-and-update)); back with `dev restore` ([back to stock](#back-to-stock)) |
| Code | [`tooling/install.py`](../tooling/install.py), [`tooling/devloop.py`](../tooling/devloop.py), [`scripts/build-system-image.sh`](../scripts/build-system-image.sh), [`preinit`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/preinit), [`pico4-install`](../initramfs/usr/bin/pico4-install) |

Installing replaces PICO OS's system and data. The way back writes them from the EDL backup; PICO OS then
starts with fresh data, as after a factory reset, because the backup does not hold its userdata. The
installed system runs on Valve's kernel ([kernel.md](kernel.md)).

## Layout

| Partition | Size | PICO OS | Linux |
| --- | --- | --- | --- |
| `boot` | 96 MiB | kernel and first-stage ramdisk | Valve's kernel, its device tree and the command line that starts the system image; no ramdisk ([the boot image](#install-and-update)) |
| `recovery` | 100 MiB | recovery | the maintenance stage 1 on the stock kernel: development, installs, rescue |
| `super` | 8 GiB | the read-only system (system, vendor, product, odm) | the system image: read-only ext4, about 4.6 GiB |
| `userdata` | 106 GiB | apps and data (f2fs, file-based encryption) | the data: ext4 `pico4-data`, mounted at `/data` |
| `misc` | 1 MiB | the bootloader message | the same: it selects the maintenance mode |
| `metadata` | 16 MiB | userdata's encryption metadata | unused; wiped on the way back |

Every other partition stays read-only; the calibration and firmware the system needs come from the image
([stage1.md](stage1.md), [xr.md](xr.md)).

## The installed system

- **System image.** `build-system-image.sh` turns the userspace archive into ext4 for `super`, as root in the
  build container: no journal (never written on the headset), without `orphan_file` and `metadata_csum_seed`
  (the stock kernel under the maintenance stage 1 cannot mount them), file capabilities kept (gamescope's
  `cap_sys_nice`). Its size is the content plus 10 % and 128 MiB. It names its archive in
  `/usr/share/pico4/system-image`. Beside it: `pico4-system.img.zst` (zstd level 3, about 38 % of the image)
  and the image's SHA-256 in `pico4-system.img.zst.sha256`; `dev install` sends the compressed copy when that
  hash is the image's.
- **Data.** preinit mounts `userdata` at `/data` and lays the changing parts onto the read-only image: `/etc`
  as an overlay (upper: `/data/etc/upper`, without the inodes index), `/var`, `/home` and `/root` as binds of
  `/data/var`, `/data/home` and `/data/root`, copied from the image on the first boot. SteamOS's offload units
  bind `/srv`, `/var/log`, `/var/tmp`, the pacman cache, flatpak and container storage from
  `/home/.steamos/offload`, so they land on the data partition too.
- **Updates.** A new system image replaces `super` whole; the data stays. On an image it has not booted
  before, preinit takes `/var/lib/pacman` (which describes the image) from the new image.
- **Boot indication.** [`pico4-splash`](../src/splash/pico4-splash.c) draws the logo's headset
  ([docs/logo](../../../docs/logo/README.md)), the name and a moving bar in the middle of both lenses while the
  system starts ([pico4-splash-draw.h](../src/splash/pico4-splash-draw.h)). preinit starts it before it mounts
  the data; `pico4-splash.service` takes the display over and the bar carries on (its place follows the
  clock). It sets the panels' 90 Hz mode on msm's primary plane and gives up DRM master, so the next compositor
  (Monado, SteamVR's) becomes master; that compositor's commit ends the indication, four minutes at the latest.
- **SSH.** `dev install` writes the enrolled keys to the data partition ([security.md](security.md#logins)).
  The installed system makes its host keys on the first boot (`sshdgenkeys.service`) and keeps them in the
  `/etc` overlay; the maintenance stage 1 makes new ones on every boot, so the two answer with different keys.

### How it starts

`boot` holds Valve's kernel, its device tree and a command line, no ramdisk. The command line adds
`root=PARTLABEL=super rootfstype=ext4 rootwait=20 ro init=/usr/libexec/pico4/preinit pico4.root=installed
pico4.autoreboot=0` to the kernel's own words (`clk_ignore_unused`, `pd_ignore_unused`,
`regulator_ignore_unused`, `panic=5`). The kernel mounts `super` read-only as the root, mounts devtmpfs on
`/dev` and starts preinit, a bash script in the image:

1. It mounts proc, sysfs and `/run`, and starts the boot indication (not in charger mode). It sets every CPU's
   governor to `schedutil`, as PICO OS runs (Valve's kernel starts with `performance`; `pico4.cpugov=keep`
   leaves it).
2. `devnode sweep` switches every disk and partition read-only and makes the `/dev/block/by-name` links before
   anything is mounted writable: one `blockdev` call, 0.03 s for about 120 devices (`pico4-storage-ro.service`
   runs it again before `local-fs-pre.target`).
3. It makes `userdata` writable, and its disk too (a mainline kernel counts a disk's read-only flag for its
   partitions; the other partitions keep their own flags), and mounts it at `/data`. Outside charger mode it
   counts the boot ([boot attempts](#boot-attempts)).
4. On the first boot it copies `/var`, `/root` and `/home` from the image (`cp -a -n`); after a new system
   image it takes the package database from the image.
5. It mounts the `/etc` overlay, binds `/var`, `/home` and `/root`, and writes `/etc/machine-id` from the
   headset serial, so that journals of different boots belong together.
6. It executes systemd, in charger mode with `--unit=pico4-charger.target` ([charger mode](#charger-mode)).

Any failure on the way reboots into `recovery` (`pico4-reboot recovery`). If `super` cannot be mounted at
all, the kernel waits 20 s, panics and reboots from `boot` again; the way out is fastboot and `dev recovery`,
or EDL by hand (both volume buttons held while connecting USB to the powered-off headset,
[device preparation](device-preparation.md)). Only `pico4-install` writes `super`, and it reads it back.

### Boot attempts

A boot that started systemd can still fail: a kernel panic reboots after 5 s (`panic=5`), and the kernel
panics on soft lockups and hung tasks (120 s). systemd arms the SoC's watchdog (`qcom_wdt`,
`RuntimeWatchdogSec=30s` in `system.conf.d/50-pico4.conf`). Its bite alone does not reset the headset on this
firmware (it locks one CPU up), so the bark, 1 s before it, panics (pretimeout governor `panic` in
the kernel's `pico4.config`):

| A stopped PID 1 (ptrace, no pings) | After |
| --- | --- |
| the bark: `Kernel panic - not syncing: watchdog pretimeout event` | 28.3 s |
| `panic=5`'s reset | 33 s |
| SSH answers again; the graphical target reached, the count cleared | 55 s |

A suspend does not count against the watchdog (90 s asleep with it armed woke into the same boot). A system
that only crawls (every CPU busy at real-time priority) keeps pinging: the fair server leaves normal tasks 5 %
of each CPU, and the hung-task panic restarted it after about 7 minutes.

[`boot-attempts`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/boot-attempts) counts boots in
`/data/.pico4-boot-attempts`:

| When | What happens |
| --- | --- |
| preinit, before systemd (not in charger mode) | the count goes up by one |
| the graphical target is reached (`pico4-boot-ok.service`) | the count is cleared |
| preinit finds 2 unfinished boots | it clears the count and reboots into `recovery`; a boot from there has two tries again |

A failed unit does not hold the graphical target back; a system that never reaches it still answers SSH.

Under systemd:

- **USB link.** `pico4-usb-gadget.service` runs [`usb-gadget`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/usb-gadget)
  before `sysinit.target`: the maintenance stage 1's composite gadget ([stage1.md](stage1.md)), recorded in
  `/run/pico4/usb-link.env`. systemd-networkd gives `usb0` `172.16.42.1/24` and one DHCP lease, `172.16.42.2`,
  without router, DNS or NTP, so the PC keeps its own internet route
  ([`50-pico4-usb.network`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/50-pico4-usb.network)). The Frame's own
  gadget units stay masked. Windows 11's in-box NCM driver works as it is: only the maintenance stage 1 needs the
  [NCM initialization](hardware-testing.md#windows-ncm-initialization).
- **Wi-Fi.** ath11k probes the QCA6390 before the system image is mounted and fails without its firmware;
  `pico4-wlan-probe.service` binds it again once `/usr/lib/firmware` holds it, before NetworkManager, and waits
  up to 15 s for `wlan0`.
- **Display and GPU.** They bind without a ramdisk; the GPU loads its firmware from the image at first use.

## Boot modes

The ABL starts `recovery` instead of `boot` while `misc` holds the bootloader message `boot-recovery`:

| Mode | Starts | Entered by | Left by |
| --- | --- | --- | --- |
| Installed | `boot`: the kernel mounts `super`, preinit mounts `userdata` and executes systemd, with or without a PC; a failure reboots into recovery | a boot with an empty bootloader message | `dev recovery` |
| Maintenance | `recovery`: the dev image's stage 1 on the stock kernel with its deadlines; `dev install` writes from here | `fastboot reboot recovery` (the ABL writes `boot-recovery`), or a reboot with reason `recovery` (once) | `pico4-install bootmode normal` (`dev normal`) |

The maintenance stage 1's deadlines return the headset to fastboot; with `boot-recovery` in `misc`, every boot
from there starts the maintenance stage 1 again.

### Charger mode

With the USB cable attached, a powered-off headset starts again with `androidboot.mode=charger`. PICO OS then
runs AOSP's `charger`; preinit starts systemd with `pico4-charger.target`: `basic.target`,
`pico4-charger.service` ([`charger`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/charger)), the USB link,
systemd-networkd and sshd. The boot indication does not run, so the lenses stay dark. Holding the power key 2 s
starts the system (a plain reboot); 10 s without USB power turn the headset off, as AOSP's charger does.
[test_charger.sh](https://github.com/overpressure/packages/blob/main/phoenix/pico4-base/test_charger.sh) tests these decisions in `pico4-base`'s build.

### Power key

[`pico4-powerkey.service`](https://github.com/overpressure/packages/blob/main/phoenix/pico4-power/pico4-powerkey.service) (overpressure/packages' `pico4-power`) does what PICO
OS's window manager does with the PMIC's key (`pm8941_pwrkey`): a press released within 2 s runs
`systemctl suspend` (s2idle, the only sleep state this kernel offers; the key wakes the headset, and the waking
press is ignored); held 2 s, `systemctl poweroff`, off in about 7 s. logind ignores the key. Held 8.7 s, the PMIC
resets the headset.

After a resume Valve's session goes on with 6DoF, both controllers, the cameras, USB and sound. PICO OS
suspends a headset taken off (screen off after 10 s, suspend after 15 s); here SteamVR's standby follows the
wear sensor ([session.md](session.md)). Steam can suspend again seconds after a wake
([roadmap.md](roadmap.md#open-work)).

Across a suspend the QTimer, which stamps IMU samples and camera frames, runs on while CLOCK_MONOTONIC stands.
The driver sees that the system slept (CLOCK_BOOTTIME less CLOCK_MONOTONIC grew), maps both clocks anew, drops
IMU samples that do not move forward, and restarts the camera capture.

## Boot images

`python -m overpressure dev image` writes two images from the stage-1 image to `out/dev/` (private: the recovery
image carries stock recovery's overlay and vbmeta, from the public OTA of the headset's firmware, cached in
`cache/stock`):

- `dev-boot.img`: the dev image for `boot`.
- `dev-recovery.img`: the same stage 1 for `recovery`, marked `pico4.part=recovery` on its command line, as a
  whole-partition image:
  - the ABL loads the device-tree overlay from the image itself (`recovery_dtbo`), taken from stock recovery;
  - `recovery` is a chained AVB partition: stock's own signed vbmeta (RSA-4096) follows the image, with a
    footer in the partition's last 64 bytes. The ABL checks that chain on every boot; an image that overwrites
    the vbmeta leaves every boot in fastboot, while a hash mismatch alone is tolerated on the unlocked headset.
    The image therefore carries stock's vbmeta unchanged and a footer pointing to it
    ([`overpressure/avb.py`](../../../overpressure/avb.py)).

The installed system's boot image comes from `dev boot-image` ([below](#install-and-update)).

Large raw downloads crash the ABL's fastboot (the 100 MiB stock recovery left it in crash-dump mode), so
whole-partition images go sparse ([`overpressure/sparse.py`](../../../overpressure/sparse.py)), zero runs as fill
chunks.

## The installer

Once the PICO 4 is chosen, the window asks what to do: *Install*, *Upgrade* or *Restore to stock*
([below](#the-upgrade-and-the-way-back)); its start page also exports and imports the backup
([below](#the-backup-as-one-file)). On the command line these are `python -m overpressure install`, `upgrade`
and `restore` ([`tooling/install.py`](../tooling/install.py): `Plan.stages`, `upgrade_stages`,
`restore_stages`; the engine: [docs/tool.md](../../../docs/tool.md#the-installer); the owner's steps:
[README](../README.md#install)).

Every command that touches the headset first asks the disclaimer (y/n); `--accept-disclaimer` or
`OVERPRESSURE_ACCEPT_DISCLAIMER=1` accepts it for unattended runs
([overpressure/disclaimer.py](../../../overpressure/disclaimer.py)). A computer on its battery below 30 % is
refused, and the installer checks again before each task that writes
([overpressure/power.py](../../../overpressure/power.py)).

A started workflow goes on where it stopped; after an earlier run the window opens at *Continue where you left
off* and *Start over from…* a stage (starting over at Prepare your headset or Install repeats the backup and the
writes). A stop (*Stop*, closing the window, Ctrl-C) ends any wait at once, such as the up to ten minutes
Connect your headset waits for USB; a task that writes (`dev stage-abl`, the unlock, `dev flash`, `dev install`,
the Wi-Fi profile) finishes its write first.

The install takes a headset from PICO OS to the installed system in five stages, with the commands of this
document and of [device-preparation.md](device-preparation.md):

| Stage | Tasks | Headset |
| --- | --- | --- |
| Check this computer | `tools`, `device fetch-unlock-files`, then the doctor's checks: a failure stops it with the fixes, apart from what the installer finds out (serials, identity, backup) or builds (the assets) | not needed |
| Build the system | each skipped while its output is there and not older than its input: assets, stage-1 image, the build environment (the container image), userspace archive, system image, dev image, Valve's kernel, boot image | not needed |
| Connect your headset | an action ask (developer mode, USB debugging, the cable, the prompt in the headset); exactly one `adb devices` entry in state `device`, written to `PICO4_ADB_SERIAL`; `device probe` unless `PICO4_IDENTITY` already holds this headset (another headset's record stops it: records are never overwritten); the firmware (`check_firmware`) and panel (`check_panel` on `/proc/cmdline` over adb). An unverified one ends the run | PICO OS |
| Prepare your headset | one consent (it erases the headset; the backup is the way back); `adb reboot edl`, `edl-ng printgpt` for the LUNs (LUN 0 last) and the serial (`PICO4_EDL_SERIAL`), `device backup --execute` without userdata, `device verify-backup` (a new backup goes to `backup/stock-TIME`, written to `PICO4_BACKUP`; an unfinished one stays); `dev stage-abl`; `adb reboot bootloader`, exactly one fastboot device (`PICO4_FASTBOOT_SERIAL`), `device unlock --execute --accept-data-loss`; `dev flash`, `dev connect` | EDL, fastboot, maintenance stage 1 |
| Install | a form for Wi-Fi (both fields optional, kept in memory only); `dev install`, `dev installed`; the Wi-Fi profile as a NetworkManager keyfile (WPA-PSK, or open) through SSH's stdin: `install -D -m 600 /dev/stdin /etc/NetworkManager/system-connections/overpressure-wifi.nmconnection && nmcli connection reload`, so neither name nor password is in a command line, a log or `.env`; `dev check`; then "Unplug it and put it on" | on the cable |

After the backup the installer suggests exporting it. It goes from the unlock straight to fastboot and
`dev flash`, without PICO OS's factory reset of
[device-preparation.md](device-preparation.md#5-factory-reset-and-stage-1-work): the install formats `userdata`
anyway. `Plan(factory_reset=True)` adds the reset as an action ask after the unlock.

### The upgrade and the way back

*Upgrade* builds this version of the system and installs it over the one this computer installed, keeping the
data; it is offered only when `out/dev/installed.json` exists. *Restore to stock* brings back PICO OS and its
bootloader; it needs a backup on this computer, made by the install or imported. A finished workflow forgets
the others' recorded progress (the install starts afresh after a restore).

| Workflow | Stage | Tasks | Headset |
| --- | --- | --- | --- |
| Upgrade | Check this computer | `tools`, the doctor's checks | not needed |
| Upgrade | Build the system | as the install's, but the userspace archive, system image, Valve's kernel and boot image are built every time (each build reuses what it can) | not needed |
| Upgrade | Install the upgrade | an action ask (switched on, on the cable); `dev recovery`, `dev install` (data kept), `dev installed`, `dev check` | installed system, maintenance stage 1 |
| Restore | Check the backup | `tools`; `device verify-backup` of `PICO4_BACKUP`, which must be the headset `PICO4_IDENTITY` describes | not needed |
| Restore | Restore PICO OS | one consent (it erases the system and its data); an action ask; a headset that answers `adb devices` with its serial runs PICO OS already and is left alone, any other gets `dev restore --installed` ([back to stock](#back-to-stock)) | maintenance stage 1, fastboot |
| Restore | Set PICO OS up | an action ask: PICO OS's first-time setup, developer mode and USB debugging; exactly one `adb devices` entry, written to `PICO4_ADB_SERIAL` | PICO OS |
| Restore | Stock bootloader | `cycle --restore-only`: stock boot verified, stock ABL written back; then "Back on PICO OS". The bootloader stays unlocked | PICO OS |

### The backup as one file

`python -m overpressure backup export FILE` (*Export backup…*) writes one checked file
([docs/tool.md](../../../docs/tool.md#the-installer)): the EDL backup `PICO4_BACKUP` names, the identity, this
computer's SSH key for the maintenance stage 1 (`out/dev/id_ed25519`, the only key the dev image in `recovery`
trusts), `out/dev/installed.json` once the system is installed, and the headset's serials. `backup import FILE`
(*Import backup…*) puts the backup under `backup/`, the key and the record into `out/dev` where it has none, and
fills the backup, identity and serials `.env` lacks, so another computer can restore the headset. It refuses
another headset's backup and a key different from this computer's own. The file is as private as the backup: it
reaches the headset's maintenance system.

## Install and update

```sh
# Valve's kernel: the pinned release of overpressure/linux into out/kernel, checked by hash (the window's
# "Valve's kernel" step); kernel build builds the same commit here instead
python -m overpressure --device phoenix kernel fetch
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

`dev install` writes through [`pico4-install`](../initramfs/usr/bin/pico4-install) in the maintenance stage 1,
which writes only these:

| Command | Writes | Checks |
| --- | --- | --- |
| `pico4-install write boot\|super --size --bytes --sha256 [--zstd]` | the image to the start of the partition, through `dd` in 4 MiB blocks; `--zstd` decompresses the stream first | the partition's size equals the backup's, the image fits, a readback against the image's SHA-256 |
| `pico4-install hash NAME BYTES` | nothing | the SHA-256 of the partition's first BYTES (whether it holds its image already) |
| `pico4-install format-data --size` | ext4 `pico4-data` on `userdata` | the backup's size; the label afterwards |
| `pico4-install keys --size --id` (stdin: `authorized_keys`) | `/root/.ssh/authorized_keys` and `/etc/pico4/install-id` in the `/etc` overlay's upper layer on the data partition | the backup's size, the `pico4-data` label, at least one public key |
| `pico4-install wipe userdata\|metadata --size` | zeros over the first 64 MiB | the backup's size; a readback |
| `pico4-install bootmode recovery\|normal` | the 2 KiB bootloader message in `misc` | a readback |

Each partition is writable for its write only. `dev install` skips a partition that holds its image already (by
readback hash), formats `userdata` only when it holds no `pico4-data` file system (or with `--format-data`), and
writes the keys and install ID ([security.md](security.md#logins)). An update is the same command with a new
system image: the data stays, `boot` and `super` are written only when they differ, the keys again.

Before the first write `dev install` and `dev restore` read the headset's battery: below 20 % they start only
while it charges (`Charging` or `Full`); an unreadable battery gets a warning. A headset that switches off
halfway, or whose cable is pulled, starts the maintenance stage 1 again (`misc` requests it until the install
ends); the write is lost, and `dev install` again (or the installer's Upgrade) writes `boot` and `super` anew
while `userdata` keeps its data. This follows from the order and `misc`; it was not tried with a pulled cable.

**The boot image.** `dev install` writes `out/kernel/valve-boot.img` to `boot`: Valve's kernel and tree
([kernel.md](kernel.md)). `dev boot-image` makes it with `kernel/recovery_image.py --direct --boot KERNEL_OUT
STOCK_RECOVERY DEV_RECOVERY OUT`, the header from the dev recovery image: no ramdisk, the command line of
[how it starts](#how-it-starts), no AVB footer (the bootloader applies the `dtbo` partition's overlay, as on every
normal boot), no keys and no firmware. A new boot image alone goes through `dev recovery`, then
`dev install --boot PATH`.

| Command | Purpose |
| --- | --- |
| `python -m overpressure dev recovery` | into the maintenance stage 1 (through fastboot), which stays until `dev normal` |
| `python -m overpressure dev normal` | from maintenance into the installed system |
| `python -m overpressure dev installed` | connect to the running installed system (after a reboot) |
| `python -m overpressure dev restore-partition misc\|recovery\|boot` | one partition back from the backup through fastboot (sparse, size- and hash-checked) |

## Back to stock

`python -m overpressure dev restore` knows from `out/dev/installed.json` (written by `dev install`) that the
system is installed, or from `--installed` where another computer installed it (the installer's restore passes
it unless PICO OS runs). From the maintenance stage 1 it writes the backup's `super` and `boot` (checked against
the manifest, read back), wipes the start of `userdata` and `metadata` (both `formattable` in PICO OS's fstab,
so PICO OS makes new file systems and keys), clears `misc`, then writes stock `recovery` through fastboot and
starts PICO OS. PICO OS's first-time setup follows; ADB stays off until developer mode is on again, so
`cycle --restore-only` (stock ABL) and `dev stage-abl` (development mode again) wait for that.

### An interrupted restore

A restore that stops part way (cable pulled, battery empty, host gone) leaves the headset where its order put
it. This follows from the order and the boot rules; it was not tried with a pulled cable.

| Stopped while | Where the headset is then | Way out |
| --- | --- | --- |
| `super` or `boot` is written | every boot starts the maintenance stage 1 (`misc` keeps the request) | `dev restore` again: it writes everything again |
| `userdata` and `metadata` are wiped, or `misc` is cleared | the maintenance stage 1, or, once `misc` is clear, PICO OS on stock `super` and `boot`, making its data anew | `dev restore` again from the maintenance stage 1; from PICO OS: `adb reboot bootloader`, then `dev restore-partition recovery` |
| stock `recovery` is written through fastboot | every boot ends in fastboot: the ABL checks recovery's chain on every boot | `dev restore-partition recovery` |
| any of these, and nothing answers | — | EDL, which the chip's ROM always answers ([device-preparation.md](device-preparation.md#entering-and-leaving-edl)), and the backup written back from there |

## Verified results

| Check | Result |
| --- | --- |
| Recovery boot | the ABL starts the maintenance stage 1 from `recovery` (`fastboot reboot recovery`), readback identical; `boot-recovery` stays in `misc` until cleared |
| `misc` and `recovery` written back from the backup | identical to the backup by readback; `boot` starts again |
| System image from the 1,701 MiB archive | 4,707 MiB ext4; capabilities of gamescope, newuidmap and newgidmap kept |
| Install | `boot` (24 MiB) and `super` (4,706 MiB) written and read back, `userdata` formatted (104 GiB for data); keys and install ID written, and `dev installed` logs in with them |
| Install time | `dev install` of a new system image: 2 min 22 s from the maintenance stage 1 to the running system; `super` from the 1,808 MiB compressed image written and read back in 101 s (the USB link carries 26 to 28 MB/s with any SSH cipher); unchanged `boot` skipped after its first MiB's hash |
| Direct boot | the ABL boots a boot image without a ramdisk; `root=PARTLABEL=super` mounts the system image (`sda7`) read-only 1.43 s after the kernel starts; the display and the GPU bind |
| Windows NCM on Valve's kernel | after a reboot the installed system answers SSH without the NCM initialization; no `Wrong NDP SIGN` |
| Start without stage 1 | preinit at 1.4 s, systemd at 1.9 s (storage sweep 0.03 s); USB link at about 4 s, `wlan0` (re-probed) at about 9 s; systemd `running`, no failed units; `/` read-only from `sda7`, `/data` writable, `/etc` an overlay; governor `schedutil` |
| Boot time | the graphical target 9.7 s and 11.0 s after the kernel starts, `pico4-session.service` started at 5.3 to 5.7 s; the audio DSP's chain (`pico4-lpass-macros`, 5 to 6 s) ends it |
| Charger mode | a power-off with the cable in starts `pico4-charger.target` (10.7 s): charger service, `sshd` and USB link run, boot indication and session do not; a plain reboot from it starts the system |
| Persistence | files in `/home`, `/var`, `/etc` and `/root` survive a reboot and a system update (new image in `super`, its package database taken over) |
| `dev check` on the installed system | userspace check passes (USB link, storage, systemd, no unexpected listener); head IMU passes; Valve's session: 90.03 commits/s, 0.0 % late, 0 stalls, hottest zone 67.1 °C after 30 s; SteamVR saw the headset (the controllers were not connected) |
| Development loop | `dev recovery`, `dev reboot` (stays in maintenance), `dev normal` (installed system again); from fastboot without the recovery request, `dev reboot` connects to the installed system |
| Boot indication | Monado and SteamVR's compositor take the display from `pico4-splash.service` (it exits within 0.5 s) |
| Power-off | stays off without the cable; with the cable attached it starts again with `androidboot.mode=charger` |
| Installer | host tests with fakes: the stages, done checks, serials written to `.env`, refusals before any write, the Wi-Fi password on stdin only. On the headset, on Windows, from PICO OS: the headset's half in about 11 minutes, from finding it through the panel check over adb, the engineering ABL, the unlock (also of a headset unlocked before) and the maintenance stage 1 without a factory reset, to the install and `dev check` (backup, identity and builds there already, no Wi-Fi given) |
| Restore to stock in the window | on the headset, on Windows: from the installed system through all four stages to PICO OS with its stock bootloader; the install's recorded progress and `out/dev/installed.json` gone afterwards |
| The backup as one file | a 12.5 GiB backup: exported with the readback in 57 s, imported elsewhere and verified in 81 s, with the SSH key and the serials |

## Open work

1. **The way back.** Not run on the headset: a restore interrupted part way ([above](#an-interrupted-restore)),
   the upgrade, and a restore from an imported backup on another computer.
2. **Integrity.** The system image is checked when written, not at boot (no dm-verity).
3. **The direct boot.** Still to run: the power key held 2 s in charger mode, the lenses dark there (by eye), a
   boot without the PC, and `dev check` with both controllers connected.
4. **The installer.** The whole path with its builds and a new backup: the LUNs from `printgpt`, and the Wi-Fi
   keyfile on the installed system (WPA2; a WPA3-only network may need `key-mgmt=sae`).
5. **The boot indication in the lenses.** Tested on the host (the drawing also on aarch64 under QEMU); still to
   see: the picture in both lenses and the bar carrying on when `pico4-splash.service` takes over.
