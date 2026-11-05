# Security

What the headset exposes, who can reach it and what protects it. The system runs as a device on a home
network, holding the owner's Steam login and Wi-Fi password.

## Who it protects against

| Who | Protected | How |
| --- | --- | --- |
| Someone on the same network | yes | SSH takes only the keys enrolled at install; no account has a password; nothing else of ours listens |
| A program of the session user | as on a desktop | `steamos` has no way to root: no password, and `sudoers` grants only root. It can read everything the session user owns, such as Steam's login data |
| Someone holding the headset | no | The bootloader is unlocked by design ([device-preparation.md](device-preparation.md)): anyone with the headset and a cable can boot their own image and read the data partition |

## Logins

| Where | Who | How |
| --- | --- | --- |
| Installed system, USB link and Wi-Fi | root | SSH with an enrolled key (`sshd_config.d/50-pico4.conf`); no password, no empty password |
| Installed system | `steamos` | no remote login; the session logs in by itself |
| Stage 1 of the installed system, for its seconds before the switch and in charger mode | root | dropbear on the USB link with an enrolled key; no telnet, web server or serial shell |
| Maintenance stage 1 in `recovery` | root | dropbear on the USB link with a key or the empty password, telnet, a web page and a serial shell, all without a password |

Neither root nor `steamos` has a password in the image (`*` in `/etc/shadow`), so `su` gives nothing either.

**The keys.** `python -m overpressure dev image` makes this PC's own key once, `out/dev/id_ed25519`, and
enrolls it in the boot images; `PICO4_SSH_PUBLIC_KEY` in `.env` adds the owner's own public key.
Stage 1 copies them to `/root/.ssh/authorized_keys` at every boot. They are never in the system image or
in anything the project publishes, and no two headsets share one. A lost key means a new `dev image` and
`dev install --boot` from the maintenance stage 1, which needs the cable and no key.

**Why the maintenance stage 1 stays open.** It runs only when asked for (`dev recovery`, or a failed
boot), only on the USB cable, and it is the rescue when a key is lost. Whoever has the cable and the
headset can use the unlocked bootloader anyway, so a password there would protect nothing.

## Listening services

Read from the configuration; `pico4-listeners` reports what really listens beyond loopback, and
`pico4-userspace-check` fails on anything it doesn't expect.

| Stage | Port | Program | Reachable from | Why |
| --- | --- | --- | --- | --- |
| Maintenance stage 1 | tcp 22 | dropbear | USB cable | the development loop, installs, rescue |
| Maintenance stage 1 | tcp 23, tcp 80 | telnetd, httpd | USB cable | rescue, status page |
| Maintenance and installed stage 1 | udp 67 | udhcpd | USB cable | the PC's address on the link |
| Installed stage 1 | tcp 22 | dropbear, keys only | USB cable | `dev` commands, charger mode |
| Installed system | tcp 22 | sshd, keys only | USB cable, Wi-Fi | `dev` commands, the owner's access |
| Installed system | udp 68, udp 546 | NetworkManager, systemd-networkd | the link's network | DHCP clients |
| Installed system | tcp and udp 5355, udp 5353 | systemd-resolved | the link's network | name resolution |
| Installed system | Steam's ports | steam, steamwebhelper, vrlink, vrserver | Wi-Fi | Steam's local discovery, Remote Play and Steam Link, as on any Steam machine |

There are no firewall rules: nothing listens that one would have to hide. The headset forwards nothing.
The USB link serves one PC and gives it no route.

## Data at rest

The data partition is plain ext4: Steam's login, game data and Wi-Fi passwords are not encrypted. Encryption
would need a key from somewhere: the headset has no way to type a passphrase at boot, and a key on the
headset itself protects nothing while the bootloader is unlocked. An owner who sells or returns the headset
goes back to PICO OS ([install.md](install.md#back-to-stock)), which wipes the data partition's start and
makes a new file system. That is no secure erase.

## Integrity

The system image is checked when it is written, not at boot (no dm-verity). Valve's repositories publish no
signatures; the build pins their packages by hash ([userspace.md](userspace.md#package-lock)) and Valve's
kernel source by hash and commit.

## Not verified

- The list above against a running system: `pico4-listeners` has not run on the headset yet.
- That a login without a key is refused, tested from another machine on USB and Wi-Fi.
