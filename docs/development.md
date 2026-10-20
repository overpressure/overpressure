# Development

How the project is laid out, built and tested, what a build host needs, and the fastest way
from a change to a result on the headset. The headset-side workflows (acceptance runner,
acceptance cycle, development loop) are in [hardware-testing.md](hardware-testing.md).

## Repository layout

```text
pico4/            host tooling, a standard-library Python package: python -m pico4 COMMAND
  image.py          stage-1 boot image (build, inspect, match); firmware.json: PICO's OTA builds
  bootimg.py, cpio.py, compress.py, alpine.py, apksign.py, firmware.py, payload.py, kernelinfo.py, superimg.py
  device.py         identity, EDL backups, engineering ABL, unlock and restore
  hardware_test.py  the bounded acceptance runner (preflight, run, flash-run)
  cycle.py          the unattended acceptance cycle that ends on stock boot and ABL
  devloop.py        the development loop on the flashed dev image
  localconfig.py    the headset's settings from .env
  userspace.py, graphics.py   validators of the on-device checks
  elfdeps.py        native ELF dependency check of the userspace root
  windows_ncm.py    Windows NCM initialization through UsbDk
initramfs/        stage 1: files added to the Alpine ramdisk (pico4-init, services, handoff)
userspace/        stage 2: the SteamOS-compatible userspace
  packages.txt, pacman.conf   package list and repositories
  rootfs/           the PICO 4 adaptation layer (units, udev rules, PAM, session, device tools, checks)
  session/          the session user's SteamVR configuration
  pkgbuilds/        the project's packages: pico4-turnip-kgsl, pico4-sensors, pico4-basalt,
                    pico4-monado, pico4-vrdisplay, pico4-sysvsem
src/              C sources: graphics/ (Vulkan and KMS tests), monado/ (Monado's PICO 4 driver and
                  KMS target), xr/ (OpenXR test clients), vrdisplay/ (SteamVR's display layer),
                  sysvsem/ (System V semaphores), reboot/ (pico4-reboot)
prebuilt/         the static aarch64 pico4-reboot, so stage 1 builds without a compiler
scripts/          build-userspace.sh, build-graphics.sh, extract-sensor-data.sh, boot and preflight helpers
tests/            host tests (unittest), test_recovery_policy.sh, qemu/ (test kernels and boot tests)
docs/             documentation
```

Private and generated directories stay out of Git: `backup/` (device identity and EDL
backups), `assets/` (Firehose loader, engineering ABL, GPU firmware, sensor data), `cache/`
(downloads), `out/` (build output and test evidence; `out/dev/` for the development loop) and
`.env` (the headset's settings, see [.env.example](../.env.example)).

## Build host

| Task | Needs |
| --- | --- |
| Stage-1 image, host tests, headset commands | Python 3.9+ on Windows, Linux or macOS; Android platform-tools and OpenSSH for the headset |
| Userspace archive, QEMU tests | Linux or WSL2 as root: curl, tar, zstd, python3, chroot, overlayfs |
| on x86-64 hosts additionally | an aarch64 binfmt handler with the F flag (qemu-user-static) and LLVM: `apt install clang-20 lld-20` |
| Windows NCM initialization | UsbDk and a Python with the `windows-ncm` extra: `pip install "pyusb==1.3.1" "libusb-package==1.0.30.0"` |

The userspace build runs unchanged on an arm64 Linux host. There the build environment runs
natively, without binfmt or LLVM.

### Native compilers instead of an arm64 build machine

The build environment is an arm64 root file system. On x86-64 its programs run under QEMU user
emulation, roughly ten times slower than natively. The compilers are the exception: the kernel
runs the host's x86-64 clang and lld directly inside the build environment, and they compile for
`aarch64-unknown-linux-gnu` with the environment as their sysroot (its glibc, GCC 15 runtime and
libraries). Only makepkg, the CMake and Meson configuration steps, code generators, pacman and
its hooks still run emulated.

Clean package builds on a 12-core i7-12700KF (20 threads) under WSL2:

| Package | GCC under QEMU | host clang |
| --- | --- | --- |
| pico4-turnip-kgsl (Mesa 26.1.6, Turnip only) | about 30 min | 4.4 min |
| pico4-basalt | 8.7 min (ccache warm) | 2.9 min |
| pico4-monado | 4.6–8.3 min | 2.8 min |
| pico4-sensors | 1.6 min | 1.0 min |
| pico4-vrdisplay, pico4-sysvsem | 45 s | 21–25 s |

A Raspberry Pi 5 (four Cortex-A76 cores) compiles at about the speed of this host's emulation,
so it would not shorten builds. Machines that would, if the host's native compilers are not
enough: Apple silicon (Linux VM), Snapdragon X (WSL2), or Ampere and Graviton cloud instances.
Keep the private data rules in mind before building on someone else's machine.

`--emulated-cc` builds with the environment's own GCC under QEMU, for comparison or when no
LLVM is installed. Packages built by clang use lld and no LTO (LTO's bitcode objects would need
LLVM's archiver in every build system).

## The userspace build

```sh
# Linux or WSL, as root (make userspace does the same with the defaults below):
scripts/build-userspace.sh /var/tmp/pico4-userspace-build out/userspace \
    --gpu-firmware assets/graphics-firmware-5.13.7 --sensor-data assets/sensor-data-5.13.7
```

| Option | Effect |
| --- | --- |
| `--gpu-firmware DIR` | `a650_*` GPU firmware from your own backup; without it Vulkan cannot start |
| `--sensor-data DIR` | sensor DSP firmware, registry and calibration from `scripts/extract-sensor-data.sh` |
| `--refresh` | download current package databases and reinstall the base root |
| `--clean` | rebuild the project packages from scratch |
| `--emulated-cc` | compile under QEMU with the environment's GCC |

Each step is skipped when its inputs did not change:

1. **Snapshot.** The script copies its inputs (`pico4/`, `src/`, `userspace/`, the shared stage-1
   scripts, `prebuilt/`) into the build root first, so the tree can be edited during a build.
2. **Build environment.** Valve's Holo Core aarch64 preview root file system, pinned by SHA-256,
   unpacked once. Package databases are downloaded by the first build and with `--refresh`
   only, so repeated builds install the same package versions.
3. **Project packages.** makepkg builds each recipe in `userspace/pkgbuilds/` when the recipe
   (its directory and the compiler) changes. When only the project's own sources in `src/`
   change, the package is rebuilt incrementally in its last build tree (`makepkg -e`): the
   recipes copy those sources with `install -C`, which leaves unchanged files and their
   timestamps alone, and configure only a new build tree.
4. **Base root.** `userspace/packages.txt` installed by pacman from the Steam Frame hotfix
   repository and the Holo Core preview. It is kept between builds and reinstalled when the
   package list or `userspace/pacman.conf` changes, and with `--refresh`.
5. **Image root.** An overlay on the base root: the project packages that differ from the
   base root's, the adaptation layer (`userspace/rootfs`, shared stage-1 scripts, users, units)
   and the session configuration. [pico4/elfdeps.py](../pico4/elfdeps.py) checks natively that
   the programs and libraries of the Frame's and the project's packages find their libraries and
   symbol versions; a few optional programs are excluded by name in the script.
6. **Archive.** `pico4-userspace.tar.zst` (zstd level 12, multithreaded) and
   `pico4-userspace.manifest.json` with the sources, hashes and installed package versions.

Times on the same host, for an archive of about 800 MB:

| Change | Build time |
| --- | --- |
| nothing, or only the adaptation layer (`userspace/rootfs`, `userspace/session`) | 45 s |
| one source file of a project package (here a Monado driver file, incremental) | 2.3 min |
| the package list or `pacman.conf` (new base root, packages reused) | 2 min |
| everything: `--clean` packages and a new base root | about 16 min |

Writing the archive takes about 25 s of every build (tar, zstd and the copy to the output
directory). Package builds keep emulated overhead outside the compiler: makepkg's stripping,
debug packages and compression take about a minute for Monado.

## Tests

| Level | Command | Takes |
| --- | --- | --- |
| Host tests: protocols, validators, the runner, cycle and dev loop against fakes | `python -m unittest discover -s tests` | about 6 s |
| Stage-1 recovery policy | `bash tests/test_recovery_policy.sh` | seconds |
| Both of the above | `make test` | seconds |
| On the headset: the integration test | `python -m pico4 dev boot` and `dev check`, in development mode | minutes; [hardware-testing.md](hardware-testing.md) |
| Optional: stage 1 on a QEMU test kernel | `tests/qemu/run-selftest.sh` (`make qemu-stage1`) | about 3 min |
| Optional: stage 1 and the userspace on the stock-like 4.19 QEMU kernel | `tests/qemu/run-userspace.sh` (`make qemu-userspace`) | 10–20 min, often fails |

The headset is the integration test: the development loop boots a new archive on the real
kernel and runs the acceptance checks in minutes. The QEMU boot tests remain for work without a
headset. They emulate the whole system under TCG (the test kernels come from
`tests/qemu/build-test-kernel.sh`): unpacking the userspace archive takes about 2.5 minutes, and
the guest's CPUs stall (RCU stall warnings) while systemd runs its generators. A userspace boot
takes 10–20 minutes and often stops with "Failed to start up manager" when those stalls push the
generators past their limit; one vCPU instead of two avoids the stalls but takes twice as long.

New behaviour gets host tests with fakes for hardware and golden data for protocols; the C
protocol tests run inside the package builds, because makepkg runs with `--nocheck`.

## From a change to the headset

The headset stays in development mode, with the engineering ABL and the dev image in `boot`
([hardware-testing.md](hardware-testing.md#development-mode)):

1. Change the code, then build: `make userspace` (or the script above), incremental.
2. `python -m pico4 dev reboot`, `python -m pico4 dev boot`, `python -m pico4 dev check`.

A changed stage-1 image is built with `python -m pico4 build` and written with
`python -m pico4 dev image` and `python -m pico4 dev flash`; `dev connect` then takes the place of
`dev reboot`. A task ends with the headset still in development mode. Returning it to stock is a
separate step, for when PICO OS is needed
([hardware-testing.md](hardware-testing.md#back-to-stock)).

## Windows host and WSL

- `wsl -u root -- bash -c '... $VAR ...'` expands `$VAR` before bash runs, even inside single
  quotes. Put such commands in a script file and run `wsl -u root -- bash /mnt/c/.../script.sh`.
- From Git Bash, set `MSYS_NO_PATHCONV=1`; otherwise `/abs/paths` in arguments and environment
  values become `C:/Program Files/Git/...`.
- Run project PowerShell scripts with `pwsh` (7); Windows PowerShell 5.1 aborts on native stderr.
- `adb` and `fastboot` live in `%LOCALAPPDATA%\Android\Sdk\platform-tools` unless on PATH;
  `PICO4_PLATFORM_TOOLS` in `.env` points the headset commands there.
- Line endings: `.gitattributes` checks out `userspace/`, `initramfs/`, scripts and sources with
  LF; PKGBUILD checksums depend on it. Python on Windows writes CRLF unless told
  `newline="\n"`.
- Windows' real-time scan reads every freshly written boot image once, about a quarter of a
  second each; the host tests share one image for that reason.
- Build on the WSL file system (`/var/tmp/...`), not under `/mnt/c`, which is much slower.

## Conventions

- Python uses the standard library only, dataclasses and type hints, and short docstrings that
  explain why. Shell scripts use `set -euo pipefail`. C sources carry SPDX headers (BSL-1.0 for
  Monado code).
- External sources are pinned by commit or hash; patches live as `.patch` files next to their
  PKGBUILD, whose `sha256sums` are regenerated after a change. The project's own sources stay in
  `src/` and are `SKIP` in the recipes.
- Documentation states facts: what the system is, how it works, how to build, run and verify it,
  verified results and open work. History belongs in Git, not in the documents.
- Commits follow conventional commits (`feat:`, `docs:`, ...) on a step branch that is
  fast-forwarded into `main`.
