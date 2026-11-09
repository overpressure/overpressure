# Release checklist

Every release image goes through all of this; [docs/releasing.md](../../../docs/releasing.md) says when. Run it
on the release's own image, from a fresh boot. `python -m overpressure dev release-check` runs the automated
part and writes `out/release/<time>/result.json`; `--attended` adds what needs someone near. The worn part is a
person's. Attach the results to the release.

## Automated

| Check | Passes when | Time |
| --- | --- | --- |
| `python -m unittest`, `make test`, CI | all pass | minutes |
| Acceptance (`dev check`) | userspace, IMU and Valve session checks pass, with no failed unit | 2 min |
| Access | sshd keys only, no account has a password, no unexpected listener (part of acceptance) | seconds |
| Boot time | `systemd-analyze time`: the graphical target at most 13 s after the kernel's start | seconds |
| Still headset | `pico4-still-gate 60` on the table: no worse than the previous release, tracked throughout | 1 min, after a session has been up for 2 |
| Suspend and sound (`--attended`, plays tones) | `pico4-suspend-check`: PipeWire lives through the suspend, sound comes back | 2 min |

## On the bench

| Check | Passes when | Time |
| --- | --- | --- |
| A login without a key | refused from another machine, on USB and on Wi-Fi, for root and `steamos` | 2 min |
| Boot without the PC | power button, no cable: session starts, tracked | 1 min |
| Charger mode | power off with the cable in: lenses dark; held 2 s, it starts the system | 1 min |
| Unfinished boots | `systemctl set-default multi-user.target`, two reboots: the third boot starts the maintenance stage 1; `dev normal`, `systemctl set-default graphical.target`, a reboot: the system starts | 3 min |
| A hang | PID 1 stopped with ptrace (no watchdog pings): the headset restarts within 40 s, and the system starts again | 2 min |
| The buttons' way out | from the powered-off headset, both volume buttons while connecting USB: EDL answers | 1 min |
| Replug during a suspend | from a fresh boot, status `Attached`, 4 minutes asleep: replug, it wakes with USB and sound | 6 min |
| Suspend with the microphone capturing | PipeWire survives, Steam's interface still sounds | 1 min |
| Wake loop | 40 screen-off and screen-on cycles: no lens fault, none in the kernel log | 10 min |
| A power press after over an hour asleep | wakes and stays awake | over 1 h |

## Worn

| Check | Passes when | Time |
| --- | --- | --- |
| First-run setup | from a wiped data partition: the setup comes up alone in the room view (no dashboard); the language, the time zone and a Wi-Fi network with a password take effect; Done turns the room view off and Steam starts in the chosen language | 5 min |
| First picture | both lenses right, no corruption, ring of dots at boot | 1 min |
| Room view and IPD | colour room view lines up; the IPD wheel moves the lenses | 2 min |
| Room setup, floor height | floor at the real floor; boundary kept across a restart | 3 min |
| Controllers | found by themselves, tracked, buttons, vibration, battery shown | 2 min |
| A native game | a Linux or Proton game, for example Beat Saber: tracked and in time, with sound | 5 min |
| A PC game over Steam Link | streams, tracks and has sound, controllers work, closing returns to the session | 5 min |
| An Android app through Lepton | starts, controllers work, exits | 3 min |
| 72 and 90 Hz | switching in the settings works, picture right at both | 2 min |
| Standby | off the head: dark; on the head: back in seconds | 1 min |
| Volume and microphone | volume keys and the sound for it, a recording | 1 min |
| Steam's sounds | hover and click sounds in the Steam interface after a suspend | 1 min |
| A long session | an hour or more unplugged in a game: no crash, tracking and frame rate as at the start | over 1 h |

## Before the release

| Check | Passes when |
| --- | --- |
| Clean install | from PICO OS on a wiped data partition, following only the README |
| Update | the release's image over the previous release's installed system: its data, Wi-Fi, room and Steam sign-in kept, the package database the new image's |
| The way back | `dev restore`, `cycle --restore-only`: PICO OS boots, tracks and plays |
| `scripts/check-private.py --history` | no finding |
| Package lock | a clean build passes the lock; the image reports the release's version and a commit without `-dirty` |
