# Third-party material and other licenses

The project's own code is under the Unlicense ([LICENSE](LICENSE)). This file lists what in the
repository is not: material from other projects, and the project's own code that is built into
another project and takes its license. Each source file names its license in its
`SPDX-License-Identifier` header; `tests/test_licenses.py` checks the headers.

## From other projects

| Path | What it is | From | Pinned | License |
| --- | --- | --- | --- | --- |
| `devices/phoenix/kernel/patches/` | changes to Valve's Linux kernel; patch 0009 moves and adds Venus files of the kernel (Linaro) | Linux | Valve's kernel 7.2.0, commit `509b71a1bdcd` (`build-kernel.sh`) | GPL-2.0-only, unless the file a patch changes states another license ([note](devices/phoenix/kernel/patches/LICENSE.md)) |
| `devices/phoenix/userspace/pkgbuilds/pico4-basalt/`: the patches, `pico4-vio.json` (Basalt's defaults with the PICO 4's changes) | changes to Basalt | Basalt | 2.0.1, commit `df6e970c8da7` | BSD-3-Clause |
| `devices/phoenix/userspace/pkgbuilds/pico4-gamescope/`: the patch | a change to gamescope | gamescope | 3.16.28, commit `ea3579ed6121` | BSD-2-Clause |
| `devices/phoenix/userspace/pkgbuilds/pico4-monado/`: the patches | changes to Monado | Monado | 25.1.0, commit `3b450e37b624` | BSL-1.0 |
| `devices/phoenix/userspace/pkgbuilds/pico4-sensors/`: the patches | changes to hexagonrpc | hexagonrpc | commit `da7a3742ab8d` | GPL-3.0-or-later |
| `devices/phoenix/userspace/pkgbuilds/pico4-spa-alsa/`: the patch | a change to PipeWire | PipeWire | 1.6.8 (release archive, SHA-256 in the recipe) | MIT |
| `overpressure/apksign.py`: `ALPINE_KEYS` | Alpine's two aarch64 signing public keys, to check its package index | Alpine Linux (`alpine-keys`) | the two keys as in the file | public keys, published at alpinelinux.org/keys |

## The project's own code under another license

| Path | License | Note |
| --- | --- | --- |
| `devices/phoenix/kernel/src/` | GPL-2.0-only (headers: `GPL-2.0`, `GPL-2.0-only`) | drivers built into the kernel |
| `devices/phoenix/kernel/sm8250-pico4-phoenix.dts` | BSD-3-Clause | as the kernel's `sm8250.dtsi`, which it includes |
| `devices/phoenix/src/monado/`, `src/xr/` | BSL-1.0 | built inside Monado's tree |
| `devices/phoenix/src/vrlink/`, `src/splash/` | BSL-1.0 | |
| `devices/phoenix/prebuilt/aarch64/pico4-splash` | BSL-1.0 | built from `src/splash/` |

`devices/phoenix/prebuilt/aarch64/pico4-reboot` is built from `devices/phoenix/src/reboot/` (Unlicense). Both
prebuilt programs are freestanding and link no library.

## Not in the repository

The build downloads and builds other projects (Valve's kernel and packages, Monado, Basalt, gamescope, Alpine's
packages and others) and the host tools fetch adb, fastboot and edl-ng. None of them is in the repository or
published by the project. Each recipe names its upstream, pinned version and license: the PKGBUILDs in
`devices/phoenix/userspace/pkgbuilds/` (`license=`), `devices/phoenix/kernel/build-kernel.sh`,
`overpressure/tools.py` and `overpressure/container/Dockerfile`. Where each vendor file comes from is in
[devices/phoenix/docs/distribution.md](devices/phoenix/docs/distribution.md).
