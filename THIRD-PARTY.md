# Third-party material and other licenses

The project's own code is under the Unlicense ([LICENSE](LICENSE)). This file lists what in the
repository is not: material from other projects, and the project's own code that is built into
another project and takes its license. Each source file names its license in its
`SPDX-License-Identifier` header; `tests/test_licenses.py` checks the headers.

## From other projects

| Path | What it is | From | Pinned | License |
| --- | --- | --- | --- | --- |
| `overpressure/apksign.py`: `ALPINE_KEYS` | Alpine's two aarch64 signing public keys, to check its package index | Alpine Linux (`alpine-keys`) | the two keys as in the file | public keys, published at alpinelinux.org/keys |

## The project's own code under another license

| Path | License | Note |
| --- | --- | --- |
| `devices/phoenix/src/splash/` | BSL-1.0 | |
| `devices/phoenix/prebuilt/aarch64/pico4-splash` | BSL-1.0 | built from `src/splash/` |

`devices/phoenix/prebuilt/aarch64/pico4-reboot` is built from `devices/phoenix/src/reboot/` (Unlicense). Both
prebuilt programs are freestanding and link no library.

## Not in the repository

The build downloads and builds other projects (Valve's packages, Alpine's packages and others) and the host
tools fetch adb, fastboot and edl-ng. None of them is in the repository or published by the project. Each
names its upstream, pinned version and license: `overpressure/tools.py` and the build image's recipe
([overpressure/builder](https://github.com/overpressure/builder)). Where each vendor file comes from is in
[devices/phoenix/docs/distribution.md](devices/phoenix/docs/distribution.md).

## The packages

The project's packages (Monado with the PICO 4's driver, Basalt, gamescope, hexagonrpc, PipeWire's ALSA
plugin, GTK 2, MangoHud and others) and the PICO 4's image definition are their own repository,
[overpressure/packages](https://github.com/overpressure/packages), whose THIRD-PARTY.md lists the material
from other projects and the project's own code under their licenses. `devices/phoenix/userspace/packages.json`
pins its release `2026.11.13.1`, which carries the complete sources of its GPL and LGPL packages.

## The kernel

Valve's Linux kernel with the project's changes is its own repository,
[overpressure/linux](https://github.com/overpressure/linux) (GPL-2.0-only, unless a file states another
license). `devices/phoenix/kernel/release.json` pins its release `phoenix/7.2-1`, whose binaries the project
publishes there with their source: Valve's commit `509b71a1bdcd` and the project's commits on it.
