# Background: the PICO 4, the Steam Frame and public projects

Background to the project on the original PICO 4 (2022, codename phoenix), not the PICO 4 Ultra: how it
compares with Valve's Steam Frame, which public projects help, and Valve's licensing. The plan and its
status are in [roadmap.md](roadmap.md).

## Hardware comparison

| Component | Original PICO 4 | Steam Frame |
| --- | --- | --- |
| Released | 2022 | 2026 |
| Architecture | ARM64 | ARM64 |
| SoC | Snapdragon XR2 Gen 1 | Snapdragon 8 Gen 3 |
| GPU | Adreno 650 family | Adreno 750 |
| RAM | 8 GB | 16 GB |
| Display resolution | 2160 × 2160 per eye | 2160 × 2160 per eye |
| Refresh rate | 72/90 Hz | 72–144 Hz |
| Tracking | Four monochrome cameras | Four monochrome cameras |
| Controllers | IR-tracked, 6DoF | Camera-tracked, 6DoF |
| Operating system | Android-based PICO OS | ARM64 SteamOS 3 |

Sources: [PICO developer specifications](https://developer.picoxr.com/document/web/introduce-xr-hardware/)
and [Valve's Steam Frame specifications](https://store.steampowered.com/hardware/steamframe).

Both devices use Qualcomm ARM SoCs, pancake optics, inside-out tracking and high-resolution
displays. They differ in SoC generation and in their display, camera, wireless and peripheral
implementations, so the Steam Frame's kernel and hardware configuration cannot simply be
flashed onto a PICO 4.

## Relevant open-source projects

Six public repositories are relevant to the project.

### steam-frame-nixos: works on the Frame

[y0usaf/steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos) runs NixOS with
Valve's SteamVR session, Steam client and gamescope on real Steam Frame hardware. Its author
reports working inside-out tracking, controller support, Wi-Fi, audio and the VR dashboard;
game execution and suspend are not verified in its documented results.

Relevance: **high**. Probably the best reference for separating the Steam Frame's Linux
userspace from its operating system and for understanding its dependencies.

### steamac: ARM64 image reuse

[fxgl/steamac](https://github.com/fxgl/steamac) runs Valve's ARM64 SteamOS image in a
lightweight virtual machine on Apple Silicon, using libkrun and graphics translation. It
explores bootstrapping the SteamOS environment and choosing between Steam client branches,
including the Frame client.

Relevance: **high** for SteamOS's ARM64 packaging and dependencies; none for PICO-specific
bootloaders or hardware drivers.

### more-picohaxx-tool: original PICO 4 confirmed

[chaixshot/more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool) unlocks the
original PICO 4's bootloader, obtains root access and backs up device data. Its developer
explicitly reports original PICO 4 support.

Relevance: **critical**. It provides the initial access needed to investigate a non-Android
boot chain. Unlocking wipes user data, so recovery preparations are essential.

### PICO-documentation

[thoricelli/PICO-documentation](https://github.com/thoricelli/PICO-documentation) documents
PICO 4 internals: the kernel, Android components, services, recovery, partitions, hardware and
tracking interfaces.

Relevance: **critical** for building a device-specific kernel, extracting the hardware
configuration and investigating PICO's tracking interfaces. It is documentation, not a Linux
port.

### Monado: open-source OpenXR runtime

[Monado](https://gitlab.freedesktop.org/monado/monado) ([documentation](https://monado.freedesktop.org/)),
a freedesktop.org project, provides an open-source XR runtime, compositor and hardware-driver
framework for Linux and Android, including experimental camera-based positional tracking.

Relevance: **high** for a native VR implementation.

### frame-control

[saphid/frame-control](https://github.com/saphid/frame-control) is a management and
development tool for the Steam Frame. It documents SSH, device configuration, recovery images,
Steam client behaviour and running parts of the Frame environment without the headset.

Relevance: **medium to high**. Useful for reverse engineering the userspace and automating
experiments; it does not provide a PICO 4 hardware port.

## Steam Frame recovery images and licensing

Valve publishes Steam Frame recovery images and ARM64 SteamOS packages. Its recovery image index
(`steamdeck-images.steamos.cloud`) lists USB boot images and Qualcomm download-mode recovery packages: a
concrete reference for the Steam Frame's kernel, root filesystem, startup configuration and device
dependencies. Valve also publishes SteamOS source packages on its
[SteamOS GitLab](https://gitlab.steamos.cloud/).

SteamOS contains both open-source and proprietary components, and Valve's recovery-image
license restricts modification and redistribution of the image. A public port should
therefore be built from independently built, legally redistributable packages, with
proprietary components obtained separately where permitted. This project builds its userspace
privately from Valve's public package repositories and does not redistribute the result
([userspace.md](userspace.md)).

## Related projects worth monitoring

| Resource | Why it matters |
| --- | --- |
| [pico4.wiki](https://pico4.wiki/) | Community hardware, rooting and recovery knowledge |
| [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool) | Original PICO 4 bootloader unlock and backup |
| [PICO-documentation](https://github.com/thoricelli/PICO-documentation) | Device-tree, kernel and hardware research |
| [steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos) | Steam Frame's VR software on alternative Linux |
| [steamac](https://github.com/fxgl/steamac) | ARM64 SteamOS image reuse |
| [Monado](https://gitlab.freedesktop.org/monado/monado) | Native open-source XR runtime |

**Naming caution.** The `phoenix` codename alone does not identify matching code. ByteDance's
publicly indexed [`phoenix-kernel`](https://github.com/bytedance/phoenix-kernel) repository, for
example, describes a different SXR2130P-based VR device; its compatibility with the original
PICO 4 should not be assumed.

