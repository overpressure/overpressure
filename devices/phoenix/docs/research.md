# Background: the PICO 4, the Steam Frame and public projects

Background to the project on the original PICO 4 (2022, codename phoenix), not the PICO 4 Ultra: how it
compares with Valve's Steam Frame, which public projects help, and Valve's licensing. The plan and its status
are in [roadmap.md](roadmap.md).

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

Both use Qualcomm ARM SoCs, pancake optics, inside-out tracking and high-resolution displays. They differ in
SoC generation and in their display, camera, wireless and peripheral hardware, so the Steam Frame's kernel and
hardware configuration cannot simply be flashed onto a PICO 4: the project runs Valve's kernel with the PICO's
own drivers and device tree ([kernel.md](kernel.md)).

## Relevant open-source projects

| Project | What it is | Relevance |
| --- | --- | --- |
| [steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos) | NixOS with Valve's SteamVR session, Steam client and gamescope on real Steam Frame hardware; its author reports inside-out tracking, controllers, Wi-Fi, audio and the VR dashboard working, game execution and suspend not verified | high: separating the Frame's Linux userspace from its operating system, and its dependencies |
| [steamac](https://github.com/fxgl/steamac) | Valve's ARM64 SteamOS image in a libkrun virtual machine on Apple silicon, with graphics translation; bootstraps SteamOS and chooses between Steam client branches, including the Frame's | high for SteamOS's ARM64 packaging and dependencies; none for PICO bootloaders or drivers |
| [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool) | unlocks the original PICO 4's bootloader, obtains root and backs up device data; original PICO 4 support confirmed by its developer | critical: the initial access to a non-Android boot chain (unlocking wipes user data) |
| [PICO-documentation](https://github.com/thoricelli/PICO-documentation) | PICO 4 internals: kernel, Android components, services, recovery, partitions, hardware and tracking interfaces | critical for the device tree, the hardware configuration and the tracking interfaces; documentation, not a port |
| [Monado](https://gitlab.freedesktop.org/monado/monado) ([documentation](https://monado.freedesktop.org/)) | freedesktop.org's open-source OpenXR runtime, compositor and driver framework for Linux and Android, with experimental camera-based tracking | high for a native VR implementation |
| [frame-control](https://github.com/saphid/frame-control) | a management and development tool for the Steam Frame: SSH, configuration, recovery images, Steam client behaviour, parts of the Frame environment without the headset | medium to high for reverse engineering the userspace; no PICO 4 port |
| [pico4.wiki](https://pico4.wiki/) | community hardware, rooting and recovery knowledge | reference |

**Naming caution.** The `phoenix` codename alone does not identify matching code. ByteDance's
[`phoenix-kernel`](https://github.com/bytedance/phoenix-kernel) repository, for example, describes a different
SXR2130P-based VR device; its compatibility with the original PICO 4 should not be assumed.

## Steam Frame recovery images and licensing

Valve publishes Steam Frame recovery images and ARM64 SteamOS packages. Its recovery image index
(`steamdeck-images.steamos.cloud`) lists USB boot images and Qualcomm download-mode recovery packages: a
reference for the Frame's kernel, root file system, startup configuration and device dependencies. Valve also
publishes SteamOS source packages on its [SteamOS GitLab](https://gitlab.steamos.cloud/).

SteamOS contains open-source and proprietary components, and the recovery image's license restricts its
modification and redistribution. The project therefore builds its userspace on each owner's computer from Valve's
public package repositories and distributes no image ([distribution.md](distribution.md),
[userspace.md](userspace.md)).
