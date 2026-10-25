# Installed system

The Linux system can be installed on the headset, so that it starts without the PC, keeps its
data and has room for Steam and its library. The layout follows PICO OS's own: a boot image, a
read-only system that updates replace whole, a writable data partition, and a separate
maintenance environment in `recovery`. Installing replaces PICO OS's system and data; the way
back writes them from the EDL backup (PICO OS then starts with fresh data, as after a factory
reset, because the backup does not hold its userdata).

## Layout

| Partition | Size | PICO OS | Linux |
| --- | --- | --- | --- |
| `boot` | 96 MiB | kernel and first-stage ramdisk | stage 1 in installed mode ([install boot image](#boot-images)) |
| `recovery` | 100 MiB | recovery | the maintenance stage 1: development, installs, rescue |
| `super` | 8 GiB | the read-only system (system, vendor, product, odm) | the system image: read-only ext4, about 3.1 GiB |
| `userdata` | 106 GiB | apps and data (f2fs, file-based encryption) | the data: ext4 `pico4-data`, mounted at `/data` |
| `misc` | 1 MiB | the bootloader message | the same: it selects the maintenance mode |
| `metadata` | 16 MiB | userdata's encryption metadata | unused; wiped on the way back |

Every other partition stays read-only as before; the calibration and firmware the system needs
come from the image ([stage1.md](stage1.md), [xr.md](xr.md)).

## The installed system

- **System image.** [`scripts/build-system-image.sh`](../scripts/build-system-image.sh) turns the
  userspace archive into ext4 for `super`, as root in Linux or WSL: no journal (it is never
  written on the headset), none of the features the 4.19 kernel cannot mount (`orphan_file`,
  `metadata_csum_seed`), file capabilities kept (gamescope's `cap_sys_nice`). Its size is the
  content plus 10 % and 128 MiB. It names the archive it came from in
  `/usr/share/pico4-poc/system-image`.
- **Data.** Stage 1 mounts the system image read-only at the new root, then `userdata` at
  `/data`, and lays the changing parts onto it: `/etc` as an overlay (lower: the image, upper:
  `/data/etc/upper`), `/var`, `/home` and `/root` as binds of `/data/var`, `/data/home` and
  `/data/root`. On the first boot these start as copies of the image's. SteamOS's offload units
  bind `/srv`, `/var/log`, `/var/tmp`, the pacman cache, flatpak and container storage from
  `/home/.steamos/offload`, so they land on the data partition too.
- **Updates.** A new system image replaces `super` whole; the data stays. When stage 1 sees an
  image it has not booted before, `/var/lib/pacman` (the package database, which describes the
  image) is taken from the new image.
- **Boot indication.** [`pico4-splash`](../src/splash/pico4-splash.c) shows a turning ring of
  dots in the middle of both lenses while the system starts. Stage 1 starts it before it mounts
  the system, about 15 s after the kernel; it survives the switch to systemd, whose
  `pico4-splash.service` takes the display from it at about 29 s. It sets the panels' 90 Hz
  mode on the six stock layers and then gives up DRM master, so the next compositor (Monado,
  SteamVR's) becomes master as usual; its commit ends the indication. Monado's session shows
  nothing until an OpenXR application starts, so its start ends the indication there
  (`splash-done`, from `pico4-session.service`); after four minutes it ends in any case.
- **SSH host keys.** The installed system keeps the keys of its first boot (in the `/etc`
  overlay); stage 1 makes new ones on every boot, so its few seconds before the switch answer
  with another key.

## Boot modes

The ABL starts `recovery` instead of `boot` while `misc` holds the bootloader message
`boot-recovery`, as on Android:

| Mode | Starts | Entered by | Left by |
| --- | --- | --- | --- |
| Installed | `boot`: stage 1 mounts `super` and `userdata` and switches to systemd as soon as its USB setup is done, with or without a PC; if that fails, it reboots into recovery | a boot with an empty bootloader message | `dev recovery` |
| Maintenance | `recovery`: the dev image's stage 1 with its deadlines; `dev boot` streams a system into RAM as before | `fastboot reboot recovery` (the ABL writes `boot-recovery`), or a reboot with reason `recovery` (once) | `pico4-install bootmode normal` (`dev normal`) |

The deadlines of the maintenance stage 1 return the headset to fastboot as before; with
`boot-recovery` in `misc`, every boot from there starts the maintenance stage 1 again.

### Charger mode

With the USB cable attached, a powered-off headset starts again: the PMIC powers up on USB,
and the bootloader boots with `androidboot.mode=charger`. PICO OS then runs AOSP's `charger`
instead of its system (`init.qcom.rc`). Stage 1 does the same for the installed system
([`charger`](../initramfs/usr/libexec/pico4-poc/charger)): it does not switch, leaves the lenses
dark and the status LED amber, and keeps the USB link and SSH as every stage 1 does. Holding the
power key for 2 s starts the system (a plain reboot, which the bootloader starts normally), and
10 s without USB power turn the headset off, as AOSP's charger does.

### Power key

PICO OS's window manager puts the headset to sleep on a short press (screen off, then the
kernel's suspend) and shuts it down without asking once the key is held 2 s (its long-press
timeout; `factory.long_press_power_off=1` makes the long press a power-off instead of the
menu). [`pico4-powerkey.service`](../userspace/rootfs/usr/lib/systemd/system/pico4-powerkey.service)
does the same from the PMIC's key (`qpnp_pon`): a press released within 2 s runs
`systemctl suspend` (deep suspend; the key wakes the headset, and the press that woke it is
ignored), a press held 2 s runs `systemctl poweroff`. logind ignores the key. Held 8.7 s the
PMIC resets the headset.

## Boot images

`python -m pico4 dev image` writes three images from the stage-1 image to `out/dev/` (private:
the recovery image carries vendor data from the backup):

- `dev-boot.img`: the dev image for `boot`, as before.
- `dev-recovery.img`: the same stage 1 for `recovery`, marked `pico4poc.part=recovery` on its
  command line. It is a whole-partition image, because the ABL treats `recovery` differently:
  - it loads the device-tree overlay from the image itself (`recovery_dtbo`), taken from the
    backup's stock recovery;
  - `recovery` is a chained AVB partition: the stock image is followed by its own signed vbmeta
    (RSA-4096) and a footer in the partition's last 64 bytes. The ABL checks that chain on every
    boot, not only on recovery boots. An image that overwrites the vbmeta leaves every boot in
    fastboot; a hash mismatch alone is tolerated on the unlocked headset. The image therefore
    carries stock's vbmeta unchanged behind the stage 1 and a footer that points to it
    ([`pico4/avb.py`](../pico4/avb.py)).
- `install-boot.img`: stage 1 for `boot` in installed mode (`pico4poc.root=installed
  pico4poc.autoreboot=0`, no test files, so no deadlines), with the dev loop's SSH key and
  `/etc/pico4-poc/install-id`.

Large raw downloads crash the ABL's fastboot (the 100 MiB stock recovery left the headset in
crash-dump mode); whole-partition images therefore go as sparse images
([`pico4/sparse.py`](../pico4/sparse.py)), whose zero runs are written as fill chunks.

## Install and update

```sh
scripts/build-system-image.sh out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img  # as root
python -m pico4 dev image
python -m pico4 dev flash recovery     # the maintenance stage 1; starts it
python -m pico4 dev install            # boot, super, userdata (if new), then the installed system
python -m pico4 dev check              # the acceptance checks, on the installed system
```

`dev install` runs from the maintenance stage 1 and writes through
[`pico4-install`](../initramfs/usr/bin/pico4-install) there, which writes only these:

| Command | Writes | Checks |
| --- | --- | --- |
| `pico4-install write boot\|super --size --bytes --sha256` | the image to the start of the partition | the partition's size equals the backup's, the image fits, the stream's SHA-256, a readback of the written bytes |
| `pico4-install format-data --size` | ext4 `pico4-data` on `userdata` | the backup's size; the label afterwards |
| `pico4-install wipe userdata\|metadata --size` | zeros over the first 64 MiB | the backup's size; a readback |
| `pico4-install bootmode recovery\|normal` | the 2 KiB bootloader message in `misc` | a readback |

Each partition is made writable for its write only. `dev install` skips a partition that
already holds its image (by a readback hash), and formats `userdata` only when it holds no
`pico4-data` file system (or with `--format-data`). An update is the same command with a new
system image: `boot` and the data stay.

| Command | Purpose |
| --- | --- |
| `python -m pico4 dev recovery` | into the maintenance stage 1 (through fastboot), which stays until `dev normal` |
| `python -m pico4 dev normal` | from maintenance into the installed system |
| `python -m pico4 dev installed` | connect to the running installed system (after a reboot) |
| `python -m pico4 dev restore-partition misc\|recovery\|boot` | write one partition back from the backup through fastboot (sparse, size- and hash-checked) |

## Back to stock

`python -m pico4 dev restore` knows from `out/dev/installed.json` (written by `dev install`)
that the system is installed. From the maintenance stage 1 it writes the backup's `super` and
`boot` (checked against the manifest, read back), wipes the start of `userdata` and `metadata`
(both `formattable` in PICO OS's fstab, so PICO OS makes new file systems and keys), clears
`misc`, then writes stock `recovery` through fastboot and starts PICO OS. PICO OS's first-time
setup follows on the headset; ADB stays off until developer mode is enabled again, so returning
the ABL to stock (`python -m pico4 cycle --restore-only`) and entering development mode again
(`dev stage-abl`) wait for that.

## Verified results

| Check | Result |
| --- | --- |
| Recovery boot | the ABL starts the maintenance stage 1 from `recovery` (`fastboot reboot recovery`), readback identical; `boot-recovery` stays in `misc` until cleared |
| `misc` and `recovery` written back from the backup | identical to the backup by readback; `boot` starts again |
| System image from the 810 MiB archive | 3,105 MiB ext4, 2.6 GiB used; capabilities of gamescope, newuidmap and newgidmap kept |
| Install | `boot` (46 MiB) and `super` (3,105 MiB) written and read back, `userdata` formatted: 104 GiB for data |
| Installed boot | systemd `running`, no failed units; `/` read-only from `super`, `/data` and its binds writable from `userdata`, writing `/` refused |
| Persistence | files in `/home`, `/var`, `/etc` and `/root` survive a reboot and a system update (a new image in `super`, its package database taken over) |
| `dev check` on the installed system | the userspace, IMU, pose and display checks pass (the display check failed once on the first, cold run with 51 late frames, then passed with 21); Valve's session passes its integration checks, its performance verdict fails on one of the known one-second stalls (as in RAM) |
| Development loop | `dev recovery`, `dev boot` (root on zram again), `dev reboot` (stays in maintenance), `dev normal` (installed system again) |
| Boot without the PC (cable unplugged, power button) | stage 1 switches without waiting for a PC; systemd reaches the graphical target 1 min 53 s after power-on with no failed units; the data is there; the USB link comes up when the cable is plugged in later |
| Boot indication | stage 1 shows it 15.3 s after the kernel started, stage 2's service takes over at 29.2 s without a gap, Monado's session ends it at 68 s; Monado and SteamVR's compositor take the display from it (it exits within 0.5 s) |
| Power-off | stays off without the cable; with the cable attached the headset starts again in charger mode (`androidboot.mode=charger`), and stage 1 stays there: no system, lenses dark, amber LED, waiting on the power key; `pico4-reboot` from there starts the installed system |

## Open work

1. **The way back.** `dev restore` for an installed system is implemented and tested on the
   host, not yet on the headset; it ends in PICO OS's first-time setup.
2. **Feedback and power.** The lenses show the boot indication until the session starts; the
   default session (Monado) then shows nothing until an OpenXR application starts. The
   [power key](#power-key)'s short press and 2 s hold are not yet tried on the headset; nor are,
   in charger mode, holding the power key and unplugging the cable. PICO OS also sleeps when
   the headset is taken off (screen off after 10 s, suspend after 15 s); here nothing follows
   the wear sensor but the microphone ([session.md](session.md)).
3. **Integrity.** The system image is checked when written, not at boot (no dm-verity).
