# What is distributed

The project publishes source: its own code, recipes, patches and pins. It publishes no image, no vendor file
and no binary of anyone else's. Each owner builds their own image on their own computer, from files each of
them downloads from where the vendors publish them. This is a description of how the project works, not
legal advice.

| Input | Publisher | In this repository | How an owner gets it | Used for |
| --- | --- | --- | --- | --- |
| PICO OS OTA of the headset's firmware | PICO, public download | URL and hashes (`tooling/firmware.json`) | `python -m overpressure stock`, on their PC | stock kernel, device-tree overlays, GPU, Wi-Fi, audio and DSP firmware, sensor data |
| PICO OS's head tracker (`libSlam` and its libraries) | PICO, in that OTA | no | `python -m overpressure assets`, on their PC | 6DoF head tracking |
| Panel commands, camera sensor settings | PICO, in that OTA | no: generated at each build (`kernel/panel_sequence.py`, `kernel/imx471_mode.py`) | the kernel build, from their OTA | both panels, the RGB camera |
| Engineering ABL | PICO, in a public PICO Neo 3 OTA | URL and hash (`tooling/unlockfiles.py`) | `python -m overpressure device fetch-unlock-files` | unlocking, development mode |
| Firehose loader | PICO, in a public PICO Neo 3 package | URL and hash (`tooling/unlockfiles.py`) | the same command | EDL backups and restores |
| The headset's calibration | the owner's headset | no | read from `persist`, on the headset, at run time | tracking, displays |
| Holo Core and the Frame's packages, Steam's client, SteamVR | Valve, public package repositories without signatures | names and hashes (`userspace/packages.lock`) | the userspace build, on their PC | the userspace, Valve's session |
| Valve's kernel | Valve, its public GPL source package | hash and commit (`kernel/build-kernel.sh`), patches | the kernel build, on their PC | the kernel |
| The Steam client's updates, games | Valve, with the owner's own Steam account | no | Steam, on the headset | Steam |
| Open source projects | their upstreams | recipes, pins and patches (`userspace/pkgbuilds/`) | the userspace build | Monado, gamescope and others |
| `prebuilt/aarch64/pico4-reboot`, `pico4-splash` | this project | yes, with their sources in `src/` | the repository | stage 1 and the userspace build without a cross compiler for them |

## What this means for an owner

- The install needs no file from anyone's private collection: everything comes from public downloads.
- The built images (`out/`) hold PICO's and Valve's files, and are the owner's own copies. They stay private:
  an owner who publishes one redistributes those files.
- The backup (`backup/`) and its exported file (`python -m overpressure backup export`) hold the headset's own
  partitions and the key to its maintenance system: they stay private too.
- A PICO account or a purchase isn't involved; a Steam account is, on the headset, as for any Steam machine.
- The engineering ABL is PICO's own file for another headset, the Neo 3. It boots PICO OS on the tested PICO 4,
  as [device-preparation.md](device-preparation.md) describes.

## What this means for the project

- Nothing built leaves a computer through the project, so no binary of the kernel or of GPL programs is
  published and no GPL source offer arises: every owner builds from the public source.
- `.gitignore` keeps `backup/`, `assets/`, `cache/`, `out/` and `.env` out, and `scripts/check-private.py` stops
  private data, vendor register tables and binaries in every commit.
- PICO's and Valve's terms for these files were not reviewed by a lawyer.
