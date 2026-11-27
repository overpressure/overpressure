# The headset runtime

**TL;DR.** The headset runtime is the part of a headset's Linux that every headset shares. It covers the
maintenance system in `recovery`, where the installed system is built, written and rescued. It also covers the
installed system's first process and policies. The runtime keeps every disk read-only, sets up the same USB link
to the PC in both systems, and returns to the bootloader on its own when no PC uses it. The installed system gets
two more jobs: counting boots that never finish and laying the data partition onto the read-only image.

A headset gives the runtime its values in one file and brings its own programs: its writer, its reboot
program, its probe and the hooks of its first process. The runtime comes from three places:

- [overpressure/runtime/](../overpressure/runtime/): the maintenance system;
- [`common/overpressure-base`](https://github.com/overpressure/packages/tree/main/common/overpressure-base) in
  overpressure/packages: the installed system's share;
- [overpressure/contract.py](../overpressure/contract.py): the PC's side of it.

It is part of the headset interface since 1.8. A headset made for 1.8 or newer (`api` in its `headset.json`) runs on it
without saying so; one made for an older interface brings its own `/init`, PID 1 and preinit, as phoenix's releases
before 1.8 do. A headset may still decide either way: `stage1.Spec(runtime=...)` for the maintenance system, `RUNTIME=1`
or `RUNTIME=0` in its `userspace/build.sh` for the installed system.

| | |
| --- | --- |
| Maintenance system | [overpressure/runtime/](../overpressure/runtime/) under the headset's stage-1 overlay |
| Installed system | `overpressure-base` and the runtime's library and recovery files |
| Headset's values | `/etc/overpressure/headset` in both systems, from the headset's stage-1 overlay |
| PC's side | `overpressure.contract` (`overpressure.api.v1.contract`) |

## The names

| What | Where |
| --- | --- |
| State (tmpfs): logs, the USB link's files, the deadline's marks | `/run/overpressure` |
| Programs | `/usr/libexec/overpressure` |
| Settings: the headset's values, a test run's identity and deadlines, the maintenance system's build | `/etc/overpressure` |
| Kernel command line words | `overpressure.NAME=VALUE`; a bare `overpressure.NAME` means `=1` |
| Shell variables of the runtime's library | `OP_*` |
| Log lines | `overpressure:` in the kernel log, `overpressure[UPTIME]:` in pstore's `pmsg` |

A headset's own programs, units and files keep its own names (the PICO 4's `pico4-*`).

## The headset's values

The runtime's library, [lib.sh](../overpressure/runtime/usr/libexec/overpressure/lib.sh), sources
`/etc/overpressure/headset`. The headset's stage-1 overlay carries the file. The installed system's image build
installs the same file at the same path. A value the file leaves out keeps the library's default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OP_NAME` | `headset` | the headset's name in messages |
| `OP_STOCK` | `the stock system` | the stock system's name in messages |
| `OP_IP`, `OP_HOST_IP`, `OP_PREFIX` | `172.16.42.1`, `172.16.42.2`, `24` | the USB link's addresses (overridden by `overpressure.ip=` and `overpressure.host_ip=`) |
| `OP_USB_MANUFACTURER`, `OP_USB_PRODUCT` | `overpressure`, `overpressure Linux` | the gadget's strings, which the PC's tooling finds the headset by |
| `OP_REBOOT` | `reboot` | the reboot program; takes `bootloader`, `recovery`, `poweroff` or nothing |
| `OP_PROBE` | `true` | the hardware probe the maintenance system runs after the USB setup (`--boot`) |
| `OP_DATA_PART`, `OP_DATA_LABEL`, `OP_DATA_MARKER` | `userdata`, `overpressure-data`, `.overpressure-initialized` | the installed system's data partition, its ext4 label, the marker of its first boot |

## The maintenance system

The maintenance system installs, upgrades and restores the installed system, and nothing else. It does that without
anyone at the headset: the PC's tooling drives it. On the headset it offers no more than its state and a small
recovery menu: restart, the bootloader, a staged update, a reset of the owner's data, power off. Any other use of
the headset belongs to the installed system or the stock system.

The stage-1 build puts [overpressure/runtime/](../overpressure/runtime/) into the ramdisk under the headset's
overlay, and the headset's files replace the runtime's where both have one. The headset's overlay holds
at least `etc/overpressure/headset`. It also holds its writer (the PICO 4's `pico4-install`), its probe, its
status page (`/srv/www`) and its own login profile.

| Step | What happens |
| --- | --- |
| 1 | [/init](../overpressure/runtime/init) mounts the pseudo file systems and fills `/dev` (devtmpfs, or `mdev` on a stock kernel without it) |
| 2 | every disk and partition becomes read-only as it appears ([block-hotplug](../overpressure/runtime/usr/libexec/overpressure/block-hotplug)), with `/dev/block/by-name/*` links as on Android |
| 3 | the stock ramdisk's modules (`modules.load`) |
| 4 | the USB gadget ([usb-gadget](../overpressure/runtime/usr/libexec/overpressure/usb-gadget)): NCM (RNDIS or ECM where the kernel lacks it), ACM; `OP_IP` on it, `udhcpd` gives the PC `OP_HOST_IP` |
| 5 | CPU power management as a stock post-boot script does ([power](../overpressure/runtime/usr/libexec/overpressure/power)): `schedutil`, deep idle states after 30 s, Qualcomm's crash-dump mode off |
| 6 | [overpressure-init](../overpressure/runtime/usr/libexec/overpressure/overpressure-init) becomes PID 1 and runs [/etc/inittab](../overpressure/runtime/etc/inittab): `udhcpd`, `telnetd`, `dropbear`, `httpd`, the serial getty, the USB link watcher and the recovery deadline |
| 7 | `OP_PROBE --boot` 15 s after the USB setup |

`overpressure-init` runs inittab's `::respawn:` and `::shutdown:` entries like busybox init. Its stop signals reach
only user processes: busybox init's `kill(-1)` also reaches kernel threads that accept signals, and the USB link
must outlive every stop. Each service runs through [svc](../overpressure/runtime/usr/libexec/overpressure/svc),
which waits for its prerequisites and rate-limits restarts. SIGTERM to PID 1 stops and reboots; SIGUSR1 and
SIGUSR2 stop, then halt or power off. `overpressure-mount-ro PARTITION` mounts a stock partition read-only for
inspection.

The maintenance system logs every step to the kernel log, to `/run/overpressure/boot.log` and to pstore's `pmsg`,
which survives a warm reboot. Its services log to `/run/overpressure/svc-NAME.log`.

## The recovery deadline

[recovery-watch](../overpressure/runtime/usr/libexec/overpressure/recovery-watch) runs in both systems. It checks
every 2 s and calls `OP_REBOOT bootloader` when
[recovery-policy.sh](../overpressure/runtime/usr/libexec/overpressure/recovery-policy.sh) gives a reason:

- **Unused.** Without a test run, it reboots after `overpressure.autoreboot` seconds (300; `0` never). It never
  reboots once a packet from the PC arrived on the USB network or a login shell started (the runtime's
  `/etc/profile.d/overpressure.sh` writes `/run/overpressure/no-autoreboot`).
- **Test run.** An image with `/etc/overpressure/test-run` takes its own deadlines instead:
  - a connection deadline (`test-connect-timeout`), which ends once the PC marks
    `/run/overpressure/test-authenticated` after proving the run's endpoint;
  - a maximum runtime (`test-timeout`), which counts from the PC's last contact (`/run/overpressure/host-contact`,
    the uptime of its last command).

The installed system's boot image passes `overpressure.autoreboot=0`, so there the deadline only covers test runs.

A hang that stops every process stops `recovery-watch` too. For that the maintenance system arms the SoC's
watchdog as the installed system's systemd does (`RuntimeWatchdogSec=30s`): its `watchdog` service runs busybox's
`watchdog -T 30 -t 10 /dev/watchdog`, pinging every 10 s, and the kernel resets the headset when the pings stop. A
kernel without `/dev/watchdog` leaves the service idle.

## The installed system

The installed system's image takes `overpressure-base`.
[build-userspace.sh](../overpressure/build/build-userspace.sh) also installs the runtime's `lib.sh`,
`recovery-policy.sh` and `recovery-watch` from the same overpressure, at the same paths. It installs the headset's
`/etc/overpressure/headset` (the headset's `headset_files`) and links `/usr/bin/steamos-readonly` to
`overpressure-base`'s.

### How it starts

The kernel mounts the system image from `super` read-only and starts
[preinit](https://github.com/overpressure/packages/blob/main/common/overpressure-base/preinit):
`root=PARTLABEL=super ro init=/usr/libexec/overpressure/preinit overpressure.root=installed`. preinit runs these
steps, and any failure reboots into `recovery` (`OP_REBOOT recovery`), or plainly with
`overpressure.failover=reboot`:

1. `/proc`, `/sys`, `/run`, the runtime's library;
2. the headset's **early hooks**, `/usr/lib/overpressure/preinit/early.d/*.sh`. A hook may set `OP_BOOT_UNIT`, a
   systemd unit to start instead of the default target (a charger mode);
3. the `schedutil` governor (`overpressure.cpugov=`, `keep` leaves it);
4. the storage policy's sweep;
5. the data partition (`OP_DATA_PART` with an ext4 file system labelled `OP_DATA_LABEL`) writable and mounted at
   `/data`;
6. the boot count (below);
7. on the first boot (no `/data/OP_DATA_MARKER`): `/var`, `/root` and `/home` copied from the image;
8. `/etc` as an overlay on `/data/etc` (without the inodes index, so an upgrade's new image takes it);
9. `/var`, `/root` and `/home` bound from `/data`;
10. a machine ID from the serial number;
11. the headset's **mounted hooks**, `/usr/lib/overpressure/preinit/mounted.d/*.sh`;
12. systemd.

Hooks are bash fragments sourced in name order. They can call `log` and `fail`.

### Boot attempts

[boot-attempts](https://github.com/overpressure/packages/blob/main/common/overpressure-base/boot-attempts) counts,
in `/data/.overpressure-boot-attempts`, boots that started systemd but never reached the graphical target: a
panic, or a hang that the watchdog ended (`RuntimeWatchdogSec=30s`). `overpressure-boot-ok.service` clears the
count at the graphical target. After two unfinished boots, the next one goes to the maintenance system, and a
boot from there has the same tries again. A boot into `OP_BOOT_UNIT` neither counts nor clears.

### The storage policy

Every physical disk and partition is read-only in the installed system too. preinit's sweep runs first,
`overpressure-storage-ro.service` runs again before `local-fs-pre.target`, and the udev rule
`00-overpressure-devnodes.rules` handles each device that appears
([devnode](https://github.com/overpressure/packages/blob/main/common/overpressure-base/devnode)).

There are two exceptions:

- the data partition, and its disk's flag, which the kernel counts for the disk's partitions;
- `super`, while `steamos-readonly disable` holds.

`overpressure.rw=1` turns the policy off.

### The USB link

`overpressure-usb-link.service`
([usb-link](https://github.com/overpressure/packages/blob/main/common/overpressure-base/usb-link)) sets up the
gadget the maintenance system sets up: the same IDs, strings, serial number and MAC addresses. The PC sees one
device in both systems. The gadget records its interface, MAC and address in `/run/overpressure/usb-link.env`.

The other parts of the link:

- **The network.** systemd-networkd gives `usb0` `172.16.42.1/24` and one DHCP lease for the PC
  (`50-overpressure-usb.network`). NetworkManager leaves the USB interfaces alone.
- **Internet through the PC.** `overpressure-usb-internet.service` makes the PC's end of the link (`OP_HOST_IP`,
  or `overpressure.host_ip=`) the last-resort gateway and DNS server, behind Wi-Fi's and Ethernet's routes.
- **The Frame's own gadget.** `/tmp/usb_gadget_block` keeps the Frame's own gadget off the controller.

### A writable system

[steamos-readonly](https://github.com/overpressure/packages/blob/main/common/overpressure-base/steamos-readonly)
gives an ext4 image SteamOS's commands, messages, exit codes and `/.ROOTFS_RW` marker. `disable` makes the root
and `super` writable now and at every boot, and grows the file system to the whole partition. The setting is
`/.overpressure-writable` in the image itself, so an upgrade's fresh image is read-only again. `enable` syncs, then
makes both read-only again.

### Policies

`overpressure-base` also carries:

- systemd's crash action (reboot), short stop timeouts and the hardware watchdog;
- journald forwarding warnings to the kernel log;
- SSH by key only;
- `/home` on the data partition;
- the language fonts;
- emergency and rescue mode returning to the bootloader after 30 s (`fail-to-fastboot`) instead of waiting for a
  console.

## Kernel command line words

| Word | Default | Meaning |
| --- | --- | --- |
| `overpressure.rw` | `0` | `1` leaves block devices writable |
| `overpressure.autoreboot` | `300` | seconds without a usable connection before the maintenance system reboots to the bootloader; `0` never |
| `overpressure.usbnet` | `ncm,rndis,ecm` | the network functions to try, in order |
| `overpressure.usbserial` | `acm` | the serial shell's function; `none` disables it |
| `overpressure.usbkick` | `20` | seconds of silence on a configured link before a software replug (at most 2); `0` never |
| `overpressure.udc_timeout` | `60` | seconds to wait for a USB device controller |
| `overpressure.ip`, `overpressure.host_ip` | `OP_IP`, `OP_HOST_IP` | the USB link's addresses |
| `overpressure.led` | `1` | drive a status LED named after a plain colour |
| `overpressure.cpugov` | `schedutil` | the cpufreq governor; `keep` leaves the kernel's |
| `overpressure.lpm`, `overpressure.lpm_delay` | `1`, `30` | the CPU's deep idle states, after that many seconds |
| `overpressure.ramdump` | `0` | `1` keeps Qualcomm's crash-dump mode after a panic |
| `overpressure.modules` | `1` | load the stock ramdisk's modules |
| `overpressure.devtmpfs` | `1` | mount devtmpfs where the kernel has it |
| `overpressure.probe`, `overpressure.probe_delay` | `1`, `15` | run `OP_PROBE --boot`, that many seconds after the USB setup |
| `overpressure.selftest` | `0` | the QEMU self-test (the headset's `/usr/libexec/overpressure/selftest`) |
| `overpressure.root` | | `installed`: the installed system (its storage policy's exceptions) |
| `overpressure.failover` | `recovery` | where a failed start of the installed system reboots to; `reboot`: plainly |
| `overpressure.emergency_delay` | `30` | seconds emergency and rescue mode wait before the bootloader |

A headset adds its own words under its own prefix, or under `overpressure.` for what its stage-1 overlay reads (the
PICO 4's `overpressure.part`, `overpressure.kernel`, `overpressure.test_run`).

## The PC's side

`overpressure.contract` names what the host reads and writes on the headset:

- the paths: `RUN`, `LIB`, `ETC`;
- the recovery deadline's files: `CONTACT`, `TEST_RUN`, `TEST_AUTHENTICATED`, `TEST_CONNECT_TIMEOUT`,
  `TEST_TIMEOUT`;
- `BUILD_INFO`, the maintenance system's build;
- `HEADSET`, the headset's values;
- the words: `WORD` and `word(name, value)`.

## Verification

- `tests/test_runtime.py` (host tests):
  - the recovery policy's decisions;
  - the library's defaults and the headset's values;
  - the names: no headset's left in the runtime.
- `scripts/run-shellcheck.py` checks every runtime script.
- `overpressure-base`'s build runs its tests: boot attempts, `steamos-readonly`, the USB link's internet route,
  SSH and the watchdog.
- A headset's maintenance system and installed system are tested on the headset (the PICO 4's
  [hardware-testing.md](https://github.com/overpressure/phoenix/blob/main/docs/hardware-testing.md)).
