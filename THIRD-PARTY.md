# Third-party material and other licenses

The project's own code is under the Unlicense ([LICENSE](LICENSE)). This file lists what in the
repository is not: material from other projects, and the project's own code that is built into
another project and takes its license. Each source file names its license in its
`SPDX-License-Identifier` header; `tests/test_licenses.py` checks the headers.

## From other projects

| Path | What it is | From | Pinned | License |
| --- | --- | --- | --- | --- |
| `devices/phoenix/userspace/pkgbuilds/pico4-basalt/`: the patches, `pico4-vio.json` (Basalt's defaults with the PICO 4's changes) | changes to Basalt | Basalt | 2.0.1, commit `df6e970c8da7` | BSD-3-Clause |
| `devices/phoenix/userspace/pkgbuilds/pico4-gamescope/`: the patch | a change to gamescope | gamescope | 3.16.28, commit `ea3579ed6121` | BSD-2-Clause |
| `devices/phoenix/userspace/pkgbuilds/pico4-monado/`: the patches | changes to Monado | Monado | 25.1.0, commit `3b450e37b624` | BSL-1.0 |
| `devices/phoenix/userspace/pkgbuilds/pico4-sensors/`: the patches | changes to hexagonrpc | hexagonrpc | commit `da7a3742ab8d` | GPL-3.0-or-later |
| `devices/phoenix/userspace/pkgbuilds/pico4-spa-alsa/`: the patch | a change to PipeWire | PipeWire | 1.6.8 (release archive, SHA-256 in the recipe) | MIT |
| `overpressure/apksign.py`: `ALPINE_KEYS` | Alpine's two aarch64 signing public keys, to check its package index | Alpine Linux (`alpine-keys`) | the two keys as in the file | public keys, published at alpinelinux.org/keys |

## The project's own code under another license

| Path | License | Note |
| --- | --- | --- |
| `devices/phoenix/src/monado/`, `src/xr/` | BSL-1.0 | built inside Monado's tree |
| `devices/phoenix/src/vrlink/`, `src/splash/` | BSL-1.0 | |
| `devices/phoenix/prebuilt/aarch64/pico4-splash` | BSL-1.0 | built from `src/splash/` |

`devices/phoenix/prebuilt/aarch64/pico4-reboot` is built from `devices/phoenix/src/reboot/` (Unlicense). Both
prebuilt programs are freestanding and link no library.

## Not in the repository

The build downloads and builds other projects (Valve's packages, Monado, Basalt, gamescope, Alpine's packages
and others) and the host tools fetch adb, fastboot and edl-ng. None of them is in the repository or published
by the project. Each recipe names its upstream, pinned version and license: the PKGBUILDs in
`devices/phoenix/userspace/pkgbuilds/` (`license=`), `overpressure/tools.py` and the build image's recipe
([overpressure/builder](https://github.com/overpressure/builder)). Where each vendor file comes from is in
[devices/phoenix/docs/distribution.md](devices/phoenix/docs/distribution.md).

## The kernel

Valve's Linux kernel with the project's changes is its own repository,
[overpressure/linux](https://github.com/overpressure/linux) (GPL-2.0-only, unless a file states another
license). `devices/phoenix/kernel/release.json` pins its release `phoenix/7.2-1`, whose binaries the project
publishes there with their source: Valve's commit `509b71a1bdcd` and the project's commits on it.
