# Research: Porting SteamOS 3 from Steam Frame to the original PICO 4

Research date: October 14, 2026 · Target: PICO 4 (2022, codename Phoenix), not PICO 4 Ultra

Implementation update, October 17, 2026: **technical roadmap step 2 is complete on the local
original PICO 4 running 5.13.7**. A fully automated test verified an ARM64 Linux root shell,
USB networking, read-only storage, diagnostics and return to stock PICO OS. See the
[measured acceptance results](README.md#verification) and [reproduction procedure](HARDWARE-TEST.md).
The research findings below retain their original date; this local result does not establish
graphics, tracking or SteamOS/SteamVR support.

## Executive summary

Running SteamOS 3 on the original PICO 4 is technically plausible, but I could not verify an existing, working open-source port specifically targeting this headset.

There are, however, several relevant open-source projects that make such a port considerably more realistic than it would have been before the Steam Frame's release.

The most important findings are:

- PICO 4 bootloader unlocking is possible. Community tools and documented procedures already exist for the original PICO 4.
- Valve's ARM64 SteamOS distribution is now available on the Steam Frame. Community projects have begun examining, modifying and running its software on other systems.
- Steam Frame's VR software can run with a different Linux distribution. A NixOS project has demonstrated SteamVR, tracking and controller operation on actual Steam Frame hardware.
- Hardware similarity does not imply kernel or driver compatibility. The original PICO 4 has a Snapdragon XR2 Gen 1, while the Steam Frame uses a Snapdragon 8 Gen 3. Device-specific drivers, camera tracking and display initialization remain substantial obstacles.

My assessment is that a headless Linux boot or an experimental SteamOS-derived desktop is a much nearer-term goal than a fully functional standalone SteamVR headset.

## 1. Hardware comparison

[PICO 4 VR Headset - Chinese Version](https://images.openai.com/static-rsc-4/-mBV01ADoW_NXv7z48w3_T6pQnxvy7-q2_nhUMZ95Sj9IKQ2W9hljJ6JAkovoSlS7IuHPG0zehzuEd6tZBzFa1xFqabyMjpUdDdT3mUdoWsC6KG1nV7qvLtxiILCUL95aMFezLT2yt7TTiFB9c05RwxTH6BOfRju4MLZFichdRo?purpose=inline)

PICO 4

2022 · Snapdragon XR2 Gen 1

[Valve Officially Announces Steam Frame, A "Streaming-First" Standalone VR Headset](https://images.openai.com/static-rsc-4/BtN1eHgl5A_E8bDWpUXKun1VePmmOS6-Tsd7Ec5dRIePvYaiXVdP07g5Lzt3baLECia1V_GBUNibL1vJYd3clOo4gQBAcN6dpIDHWU3IdoTwfv9VfPdsUQSC1dKrNpkrEdlwq6cyHo5rsLp0yFYFu3n1lANlZt9sqv3orBI8X90?purpose=inline)

Valve Steam Frame

2026 · Snapdragon 8 Gen 3

| Component          | Original PICO 4         | Steam Frame             |
| ------------------ | ----------------------- | ----------------------- |
| Architecture       | ARM64                   | ARM64                   |
| SoC                | Snapdragon XR2 Gen 1    | Snapdragon 8 Gen 3      |
| GPU                | Adreno 650 family       | Adreno 750              |
| RAM                | 8 GB                    | 16 GB                   |
| Display resolution | 2160 × 2160 per eye     | 2160 × 2160 per eye     |
| Refresh rate       | 72/90 Hz                | 72–144 Hz               |
| Tracking           | Four monochrome cameras | Four monochrome cameras |
| Controllers        | IR-tracked, 6DoF        | Camera-tracked, 6DoF    |
| Operating system   | Android-based PICO OS   | ARM64 SteamOS 3         |

Sources: [PICO developer specifications](https://developer.picoxr.com/document/web/introduce-xr-hardware/) and [Valve's Steam Frame specifications](https://store.steampowered.com/hardware/steamframe).&#x20;

[image](https://www.google.com/s2/favicons?domain=https://developer.picoxr.com\&sz=32)

PICO Developer

+1

Both devices use Qualcomm's ARM architecture, pancake optics, inside-out tracking and high-resolution displays. That is encouraging.

The important distinction is that they use different generations of Qualcomm SoCs, along with different display, camera, wireless and peripheral implementations. The Steam Frame kernel and hardware configuration therefore cannot simply be flashed onto a PICO 4.

## 2. Relevant open-source projects

I found six particularly relevant repositories. None demonstrates a completed SteamOS-on-PICO-4 port, but several solve important parts of the problem.

1\. steam-frame-nixos

Working on Frame

Maintainer: y0usaf · GitHub

Runs NixOS with Valve's SteamVR session, Steam client and gamescope on real Steam Frame hardware. The author reports functioning inside-out tracking, controller support, Wi-Fi, audio and the VR dashboard. Game execution and suspend were not verified in the project's documented results.

Relevance: High. Probably the best reference for separating the Steam Frame's Linux userspace from its operating system and understanding dependencies.

[GitHub repository](https://github.com/y0usaf/steam-frame-nixos)&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



2\. steamac

ARM64 image reuse

Maintainer: fxgl · GitHub

Runs Valve's ARM64 SteamOS image in a lightweight virtual machine on Apple Silicon, using libkrun and graphics translation technologies. Its implementation explores bootstrapping the SteamOS environment and choosing different Steam client branches, including the Frame client.

Relevance: High for understanding SteamOS ARM64 packaging and dependencies, but not for PICO-specific bootloaders or hardware drivers.

[GitHub repository](https://github.com/fxgl/steamac)&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



3\. more-picohaxx-tool

PICO 4 confirmed

Maintainer: chaixshot · GitHub

Tools for unlocking the original PICO 4 bootloader, obtaining root access and backing up device data. The developer explicitly reports original PICO 4 support.

Relevance: Critical. Provides the initial access needed to investigate a non-Android boot chain. Unlocking wipes user data, and recovery preparations are essential.

[GitHub repository](https://github.com/chaixshot/more-picohaxx-tool)&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



4\. PICO-documentation

Maintainer: thoricelli · GitHub

Documents PICO 4 internals: the kernel, Android components, services, recovery, partitions, hardware and tracking interfaces.

Relevance: Critical for constructing a device-specific kernel, extracting hardware configuration and investigating PICO tracking interfaces. This is documentation, not a Linux port.

[GitHub repository](https://github.com/thoricelli/PICO-documentation)&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



5\. Monado

Open-source OpenXR runtime · freedesktop.org

Monado provides an open-source XR runtime, compositor and hardware-driver framework. It supports Linux and Android, including experimental work with camera-based positional tracking.

Relevance: High for a future native VR implementation. However, the existence of Monado does not imply that the original PICO 4 cameras, IMU and controllers already have working native Linux drivers.

[Source repository](https://gitlab.freedesktop.org/monado/monado) · [Documentation](https://monado.freedesktop.org/)&#x20;

[image](https://www.google.com/s2/favicons?domain=https://monado.freedesktop.org\&sz=32)

Getting Started

+1



6\. frame-control

Maintainer: saphid · GitHub

A management and development tool for the Steam Frame, with documentation on SSH, device configuration, recovery images, Steam client behavior and running parts of the Frame environment without physical headset hardware.

Relevance: Medium to high. Useful for reverse engineering the userspace and automating experiments, but it does not provide a PICO 4 hardware port.

[GitHub repository](https://github.com/saphid/frame-control)&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



### What I did not find

I could not confirm any of the following as of October 14, 2026:

- A public `SteamOS 3 → PICO 4 Phoenix` port with verified boot results.
- A maintained original-PICO-4 postmarketOS device package with documented functional status.
- A native Linux implementation of the complete PICO 4 tracking and controller pipeline.
- A working Steam Frame SteamVR session on PICO 4 hardware.

These are negative search findings, not proof that no private or newly published experiments exist. My searches covered public GitHub results, device documentation, Linux XR projects and publicly indexed porting resources.

## 3. The most important recent development: Steam Frame recovery images

Valve now publishes Steam Frame recovery images and ARM64 SteamOS packages.

Official Steam Frame recovery

Build 20260922.5153644 · September 22, 2026

3.8 GiB compressed

The publicly listed recovery files include a USB boot image and Qualcomm download-mode recovery packages. They provide a concrete reference for examining the Steam Frame kernel, root filesystem, startup configuration and device dependencies.

&#x20;View official recovery images

Source: Valve's recovery image index.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://steamdeck-images.steamos.cloud\&sz=32)

steamdeck-images.steamos.cloud



Valve also publishes SteamOS source packages through its [SteamOS GitLab](https://gitlab.steamos.cloud/).

This matters because porting no longer needs to start from an undocumented idea of how Valve's ARM-based SteamOS might work. There is now an actual operating system distribution to examine.

One licensing limitation: SteamOS contains both open-source and proprietary components. Valve's recovery-image license restricts modification and redistribution of the image. A public port should therefore be designed around independently built, legally redistributable packages and separately obtained proprietary components where permitted.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://store.steampowered.com\&sz=32)

Steam Store



## 4. Technical obstacles to running SteamOS on PICO 4

The key question is not whether SteamOS userspace can execute on the Snapdragon XR2. It is whether the hardware can be initialized and operated by Linux outside PICO OS.

Bootloader and Linux boot

Access available

PicoUnlock and related tools make custom boot experiments possible. However, an unlocked bootloader is not equivalent to a working Linux port. A compatible kernel, device tree, boot image and root filesystem are still required.

Adreno GPU acceleration

Hardware rendering verified; compositor integration remains

Mesa Turnip's KGSL backend has rendered and verified an offscreen Vulkan image on this headset.
An accelerated desktop or SteamVR compositor still requires integration and its own validation.

Display initialization

Native scanout verified

Both eye displays have shown stable native-resolution output with continuous framebuffer swaps
at 90 and 72 Hz using the stock PICO kernel. Lens correction and compositor presentation remain
separate integration tasks; the Steam Frame's display driver is not interchangeable.

6DoF headset tracking

Major blocker

PICO OS provides its own camera and IMU processing pipeline. Native Linux operation would need a compatible interface to that system or an alternative tracking implementation, including calibration, synchronization and pose prediction.

Motion controllers

Major blocker

The original PICO controllers require their own communication and tracking integration. Valve's Steam Frame controller software cannot be assumed to recognize them. Input decoding alone would not provide controller position tracking.

Wi-Fi, sound, USB-C, battery and power management

Device-specific

These components need PICO-specific firmware, kernel support and configuration. Some Qualcomm upstream drivers may be reusable, but each subsystem would require hardware testing.

The practical consequence is that successful Linux booting and successful VR operation are two very different milestones.

## 5. Most promising development strategy

I would approach this as a Linux-on-PICO-4 project first, followed by a SteamOS userspace port.

Suggested technical roadmap

1. Establish a recoverable development device

   Back up the stock firmware and device-specific partitions, validate the recovery method, and unlock the original PICO 4 bootloader. Avoid replacing the stock operating system until recovery has been tested.
2. Boot a minimal ARM64 Linux environment

   Use the PICO kernel and device-tree information to prepare a minimal initramfs with serial or USB debugging where available. The first measurable success is a Linux shell, not a VR interface.

   **Completed locally on October 17, 2026:** stock kernel/DTBs with Alpine ARM64 userspace,
   authenticated root SSH, USB HTTP diagnostics, read-only storage checks and automated
   stock-boot restoration passed. Full EDL readbacks of restored boot and ABL matched the
   original backup. The validated route uses temporary boot flashing because the tested
   engineering bootloader rejects RAM boot.

3. Bring up native graphics

   Investigate DRM/KMS, display initialization, Mesa Turnip and Vulkan. Verify whether the two eye displays can be driven reliably at their native resolution and refresh rates.

   **Completed locally on October 17, 2026:** hardware Turnip rendered and verified all
   65,536 pixels of an offscreen Vulkan test. Both lenses showed stable native-resolution
   grids with continuous framebuffer swaps at measured 90/72 Hz. The tested path uses
   vendor release/retire fences; page-flip events alone produced corrupted output. Extended
   and fresh-boot runs passed, followed by byte-verified original boot/ABL restoration and
   matching Android startup. See [GRAPHICS.md](GRAPHICS.md) for evidence and scope.
4. Build a SteamOS-compatible ARM64 userspace

   Study the publicly available Steam Frame packages and the `steamac` and `steam-frame-nixos` projects. Adapt system services, input and graphics dependencies to the PICO kernel instead of attempting to use the Frame kernel.
5. Integrate native XR hardware

   Establish IMU access, camera acquisition, 6DoF tracking, controller connectivity and lens calibration. Monado offers a possible foundation for developing and testing this layer.
6. Attempt Valve's VR session

   Only after tracking and graphics are operational would I attempt to bring up the Frame-style gamescope/SteamVR session. Compatibility with Valve's proprietary headset-specific components remains unproven.

An intermediate alternative would be to experiment with SteamOS components on top of the existing Android kernel and firmware. That could preserve some working low-level device support, but would not automatically make PICO's Android XR services accessible to a native Linux SteamVR runtime.

## 6. Related projects worth monitoring

| Resource                                                                       | Why it matters                                               |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| [pico4.wiki](https://pico4.wiki/)                                              | Community hardware, rooting and recovery knowledge           |
| [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool)          | Original PICO 4 bootloader unlock and backup                 |
| [PICO-documentation](https://github.com/thoricelli/PICO-documentation)         | Device-tree, kernel and hardware research                    |
| [steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos)               | Steam Frame's VR software on alternative Linux               |
| [steamac](https://github.com/fxgl/steamac)                                     | ARM64 SteamOS image reuse                                    |
| [Monado](https://gitlab.freedesktop.org/monado/monado)                         | Native open-source XR runtime                                |
| [postmarketOS pmaports](https://gitlab.postmarketos.org/postmarketOS/pmaports) | A potential starting point for a dedicated PICO 4 Linux port |

A naming caution: the headset's `phoenix` codename is not sufficient to identify matching code. For example, ByteDance's publicly indexed `phoenix-kernel` repository describes a different SXR2130P-based VR device. Its compatibility with the original PICO 4 should not be assumed.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



## 7. Feasibility assessment

These are engineering estimates based on the publicly documented work, not confirmed development schedules.

| Milestone                                | Assessment                               |
| ---------------------------------------- | ---------------------------------------- |
| Unlock PICO 4 bootloader                 | Already demonstrated                     |
| Boot minimal Linux                       | Demonstrated locally on PICO 4 / 5.13.7; step 2 complete |
| Obtain accelerated Linux graphics        | Hardware Vulkan readback and native 90/72 Hz display presentation verified separately; step 3 complete |
| Run ARM64 SteamOS userspace              | Plausible after Linux bring-up           |
| Display a Steam-style desktop in-headset | Challenging                              |
| Full 6DoF and controllers                | Major research effort                    |
| Complete standalone SteamVR experience   | Highly uncertain                         |

The strongest supporting evidence is the September 2026 Steam Frame NixOS experiment. It shows that Valve's VR session is not fundamentally tied to the Arch-based SteamOS distribution. However, that experiment preserves the actual Frame's hardware-specific environment, including calibration and device configuration.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



### Conclusion

I found the building blocks for a PICO 4 SteamOS port, but not an existing project that has combined them into a functioning implementation.

The most productive initial objective would be a documented, reproducible Linux boot on the original PICO 4 with working Adreno graphics. That milestone would enable serious experiments with Valve's ARM64 SteamOS userspace.

If your ultimate goal is a fully functional Steam Frame-like experience, native tracking and controller support are likely to require more engineering than booting SteamOS itself.

The two repositories I would investigate first are [PICO-documentation](https://github.com/thoricelli/PICO-documentation) for the headset's low-level implementation and [steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos) for the Steam Frame software environment. Together, they address the two sides of the proposed port, although the substantial integration work between them remains to be done.
