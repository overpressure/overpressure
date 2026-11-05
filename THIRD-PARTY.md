# Third-party components

The project's own code is under the Unlicense ([LICENSE](LICENSE)). This list names what comes from
elsewhere: every upstream the repository builds, patches, fetches or includes, its license and its
pinned version. Licenses are read from each upstream's own files at the pinned version; "Not stated"
means that no license file of the entry has been read yet.

The repository holds recipes, pins and patches, not the upstreams' sources. The build downloads them.
The one exception is Alpine's signing keys, under [Stage 1](#stage-1).

## Kernel

| Upstream | License | Pinned version | Used by |
| --- | --- | --- | --- |
| Linux, Valve's kernel (`linux-valve`, source package `linux-72-deckard`) | GPL-2.0-only WITH Linux-syscall-note | 7.2.0, commit `509b71a1bdcdc1055eec7426a5e25633b07db12d`, package sha256 `871f6abbc167a360368b0d2b19f1e0b2aed25d835f388873d3698b0b3a170252` | `devices/phoenix/kernel/` |
| Linux, mainline (QEMU smoke test only) | GPL-2.0-only WITH Linux-syscall-note | 6.18.56 by default, no checksum | `devices/phoenix/tests/qemu/build-test-kernel.sh` |

## Userspace packages

The recipes are in `devices/phoenix/userspace/pkgbuilds/`. A license here is the recipe's `license=`.

| Upstream | License | Pinned version | Recipe |
| --- | --- | --- | --- |
| Monado | BSL-1.0 | 25.1.0, commit `3b450e37b624f5edea366492ee2d267d673a35aa` | `pico4-monado` |
| cJSON, from Monado's `src/external` | MIT | Monado's commit | `pico4-monado` |
| OpenVR headers, from Monado's `src/external` | BSD-3-Clause | Monado's commit | `pico4-monado` |
| Basalt, Monado's fork | BSD-3-Clause | 2.0.1, commit `df6e970c8da7636eb401a09e3317fbeaaf829b9a` | `pico4-basalt` |
| basalt-headers | BSD-3-Clause | commit `e6db0fb84c69614bc4923fde6c52154c221c1768` | `pico4-basalt` |
| CLI11 | BSD-3-Clause | commit `37bb6edc5317e99af72ef48405e65d9ca5218861` | `pico4-basalt` |
| OpenGV | BSD-3-Clause | commit `91f4b19c73450833a40e463ad3648aae80b3a7f3` | `pico4-basalt` |
| magic_enum | MIT | commit `e046b69a3736d314fad813e159b1c192eaef92cd` | `pico4-basalt` |
| Eigen | MPL-2.0; some files BSD-3-Clause, Apache-2.0 or Minpack | commit `bc3b39870ecb690a623a3f49149a358b95c5781d` | `pico4-basalt` |
| Sophus | MIT | commit `d0b7315a0d90fc6143defa54596a3a95d9fa10ec` | `pico4-basalt` |
| cereal | BSD-3-Clause | commit `ebef1e929807629befafbb2918ea1a08c7194554` | `pico4-basalt` |
| gamescope | BSD-2-Clause | 3.16.28, commit `ea3579ed612151eaa005d526a2533e8932854f58` | `pico4-gamescope` |
| wlroots | MIT | the submodule commit of gamescope's commit | `pico4-gamescope` |
| libliftoff | MIT | the submodule commit of gamescope's commit | `pico4-gamescope` |
| vkroots | Apache-2.0 OR MIT; LGPL-2.1 for the generator scripts `gen/make_vkroots` and `gen/vulkan_helpers.py` | the submodule commit of gamescope's commit | `pico4-gamescope` |
| libdisplay-info (gamescope's submodule) | MIT | the submodule commit of gamescope's commit | `pico4-gamescope` |
| OpenVR | BSD-3-Clause | the submodule commit of gamescope's commit | `pico4-gamescope` |
| ReShade (Joshua-Ashton's fork) | BSD-3-Clause | the submodule commit of gamescope's commit | `pico4-gamescope` |
| SPIRV-Headers | MIT (Khronos wording) | the submodule commit of gamescope's commit | `pico4-gamescope` |
| glm | MIT or the Happy Bunny License (a modified MIT) | commit `0af55ccecd98d4e5a8d1fad7de25ba429d60e863` | `pico4-gamescope` |
| stb | MIT or Unlicense | commit `5736b15f7ea0ffb08dd38af21067c314d6a3aae9` | `pico4-gamescope` |
| wayland-protocols | MIT | 1.47, commit `88223018d1b578d0d8869866da66d9608e05f928` | `pico4-gamescope` |
| hexagonrpc | GPL-3.0-or-later | commit `da7a3742ab8d5273a281b70ee11f16fb2cb6a690` | `pico4-sensors` |
| PipeWire | MIT | 1.4.9, sha256 `8066a7b220069e4c6e3b02bd2b6ea303bba66df255023c07c99323449ba8fe3c` | `pico4-spa-alsa` |
| GTK+ | LGPL-2.1-or-later | 2.24.33, sha256 `ac2ac757f5942d318a311a54b0c80b5ef295f299c2a73c632f6bfb1ff49cc6da` | `pico4-gtk2` |
| libdisplay-info | MIT | 0.1.1, sha256 `a5aeef57817916286526292ec816a5338c4d3c0094ce91e584fc82b57070a44f` | `pico4-libdisplay-info` |
| MangoHud | MIT | 0.8.4, sha256 `bbe5a2b976313c53f21dc24b98bbed03226589460a0cce20d923cb2c36c9d8b0` | `pico4-mangohud` |
| Dear ImGui | MIT | 1.91.6, bundled in MangoHud's source archive | `pico4-mangohud` |
| ImPlot | MIT | 0.16, bundled in MangoHud's source archive | `pico4-mangohud` |
| spdlog | MIT | 1.14.1, bundled in MangoHud's source archive | `pico4-mangohud` |
| Vulkan headers | Apache-2.0 OR MIT | 1.4.346, bundled in MangoHud's source archive | `pico4-mangohud` |

The recipes `pico4-graphics-tests`, `pico4-swapchain-usage` and `pico4-vrlink` build only the
project's sources in `devices/phoenix/src/`.

## Stage 1

| Upstream | License | Pinned version | Used by |
| --- | --- | --- | --- |
| Alpine Linux minirootfs (aarch64) | Not stated | 3.24.2, branch v3.24 | `overpressure/alpine.py`, `overpressure/stage1.py` |
| Alpine packages: busybox-extras, dropbear, dropbear-scp, dropbear-convert, openssh-sftp-server, mtools, kmod, dtc, evtest, i2c-tools, libgpiod, tinyalsa, strace, htop, drm_info, libdrm-tests, usbutils, pciutils, lsblk, device-mapper, e2fsprogs, and their dependencies | Not stated | the branch's versions at build time, checked against its signed index | `devices/phoenix/tooling/image.py` |
| Alpine's signing public keys (`58199dcc`, `616ae350`), included in the source | Not stated | the keys themselves | `overpressure/apksign.py` |

## Host tools

| Upstream | License | Pinned version | Used by |
| --- | --- | --- | --- |
| Android platform-tools (adb, fastboot) | Not stated | 36.0.2, sha256 per host | `overpressure/tools.py` |
| edl-ng | MIT, as `tools.py` states | 1.6.0, sha256 per host | `overpressure/tools.py` |
| pyusb | BSD-3-Clause | 1.3.1, file hashes | `overpressure/tools.py`, `pyproject.toml` |
| libusb-package | Apache-2.0; it bundles libusb, whose `COPYING` is the LGPL 2.1 | 1.0.30.0, file hashes | `overpressure/tools.py`, `pyproject.toml` |
| importlib-resources | Apache-2.0 | 7.1.0 (Python 3.10 and later), 6.5.2 (older), file hashes | `overpressure/tools.py` |
| zipp | MIT, as its `pyproject.toml` states | 3.23.0 (Python before 3.10), file hash | `overpressure/tools.py` |
| Ubuntu base image | Not stated | 24.04, digest `sha256:24737cef17f4e22b1977df7d9a05e77c4e28b7da5b8911f8d8ccda136bf3573c` | `overpressure/container/Dockerfile` |
| Ubuntu packages: bash, ca-certificates, coreutils, curl, tar, zstd, xz-utils, python3, brotli, e2fsprogs, util-linux, clang-20, lld-20 | Not stated | not pinned, the release's versions at build time | `overpressure/container/Dockerfile` |

The tooling itself uses Python's standard library only.

## Under another license in this repository

These paths are not under the Unlicense. Each source file names its license in its
`SPDX-License-Identifier` header, and `tests/test_licenses.py` checks the headers.

| Path | License |
| --- | --- |
| `devices/phoenix/kernel/src/` | GPL-2.0-only (headers: `GPL-2.0`, `GPL-2.0-only`) |
| `devices/phoenix/kernel/patches/` | GPL-2.0-only, unless the changed file states another license |
| `devices/phoenix/kernel/sm8250-pico4-phoenix.dts` | BSD-3-Clause |
| `devices/phoenix/src/monado/`, `src/xr/`, `src/vrlink/`, `src/splash/` | BSL-1.0 |
| `devices/phoenix/prebuilt/aarch64/pico4-splash`, built from `src/splash/` | BSL-1.0 |
| `devices/phoenix/src/stockslam/`, except `build.sh` | MIT |
| `devices/phoenix/src/swapchain/` | MIT |
| `devices/phoenix/userspace/rootfs/usr/bin/`: `pico4-fan`, `pico4-lens`, `pico4-station` | MIT |
| `devices/phoenix/userspace/rootfs/usr/libexec/pico4/`: `bt-address`, `msm-frame-stats`, `session-performance`, `usb-internet` | MIT |
| `devices/phoenix/userspace/pkgbuilds/`: the patches and `pico4-vio.json` | the upstream's ([note](devices/phoenix/userspace/pkgbuilds/LICENSE.md)) |

`devices/phoenix/prebuilt/aarch64/pico4-reboot` is built from `devices/phoenix/src/reboot/`. Both
prebuilt programs are freestanding and link no library.

## Not included

None of this is in the repository. Users obtain it themselves.

- **Valve's packages.** The Holo Core aarch64 preview (snapshot `mash-20251118.3`, root file system
  sha256 `7e3fb88454e1ac633b7488abb72d3ca0cc7d2578a38146fdd7d58b50fcbd60bf`) and the Steam Frame's
  repository `deckard-arch-hotfixes-release-0.3`: the base system, systemd, Valve's Mesa and the rest
  of `devices/phoenix/userspace/packages.txt`. The userspace build downloads them from Valve's servers.
- **Steam and SteamVR.** The Steam client and SteamVR come from those packages, Lepton with Steam.
- **PICO's firmware.** PICO OS's OTA packages, listed in `devices/phoenix/tooling/firmware.json`, from
  PICO's servers. `devices/phoenix/scripts/extract-assets.sh` takes the GPU firmware, sensor data,
  WLAN and audio files from them, and PICO OS's `libSlam` with its bionic libraries.
- **Engineering ABL and Firehose loader.** The user supplies both, named in `.env` (`assets/` by
  default).
- **Other host programs.** Python, WSL or Docker, a cross compiler, UsbDk on Windows and, optionally,
  bkerler/edl as the EDL client.
