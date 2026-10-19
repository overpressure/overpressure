# Research: SteamOS 3 on the original PICO 4

Research as of October 2026. Target: the original PICO 4 (2022, codename phoenix), not the
PICO 4 Ultra.

This is the background research behind the project: how the PICO 4 compares with Valve's
Steam Frame, which public projects help, what stands in the way and how feasible each
milestone looked. It is a snapshot and does not follow the project's results; the
implementation plan and its current status are in [roadmap.md](roadmap.md).

## Summary

Running SteamOS 3 on the original PICO 4 is technically plausible. The research found no
existing, working open-source port for this headset, but several open-source projects make a
port considerably more realistic than it was before the Steam Frame's release:

- **Unlocking.** The original PICO 4's bootloader can be unlocked; community tools and
  documented procedures exist.
- **ARM64 SteamOS.** Valve's ARM64 SteamOS distribution ships on the Steam Frame. Community
  projects examine, modify and run its software on other systems.
- **Another distribution.** The Steam Frame's VR software runs on a different Linux
  distribution: a NixOS project demonstrated SteamVR, tracking and controllers on actual Steam
  Frame hardware.
- **Different hardware.** Similar hardware does not mean compatible kernels or drivers. The
  PICO 4 has a Snapdragon XR2 Gen 1, the Steam Frame a Snapdragon 8 Gen 3. Device-specific
  drivers, camera tracking and display initialization remain substantial obstacles.

The research's assessment: a headless Linux boot or an experimental SteamOS-derived desktop is
a much nearer-term goal than a fully functional standalone SteamVR headset.

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

Six repositories are particularly relevant. None demonstrates a completed SteamOS-on-PICO-4
port, but several solve important parts of the problem.

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

Relevance: **high** for a native VR implementation. Monado's existence does not mean that the
PICO 4's cameras, IMU and controllers have working native Linux drivers.

### frame-control

[saphid/frame-control](https://github.com/saphid/frame-control) is a management and
development tool for the Steam Frame. It documents SSH, device configuration, recovery images,
Steam client behaviour and running parts of the Frame environment without the headset.

Relevance: **medium to high**. Useful for reverse engineering the userspace and automating
experiments; it does not provide a PICO 4 hardware port.

### Not found

The research could not confirm any of the following:

- a public SteamOS 3 port for the PICO 4 (phoenix) with verified boot results;
- a maintained postmarketOS device package for the original PICO 4 with documented functional
  status;
- a native Linux implementation of the complete PICO 4 tracking and controller pipeline;
- a working Steam Frame SteamVR session on PICO 4 hardware.

These are negative search results, not proof that no private or newly published experiments
exist. The searches covered public GitHub results, device documentation, Linux XR projects and
publicly indexed porting resources.

## Steam Frame recovery images and licensing

Valve publishes Steam Frame recovery images and ARM64 SteamOS packages. The recovery build
listed in Valve's recovery image index (`steamdeck-images.steamos.cloud`) at the time of the
research is 20260922.5153644 of September 22, 2026, 3.8 GiB compressed. Its files include a
USB boot image and Qualcomm download-mode recovery packages: a concrete reference for the
Steam Frame's kernel, root filesystem, startup configuration and device dependencies. Valve
also publishes SteamOS source packages on its [SteamOS GitLab](https://gitlab.steamos.cloud/).

A port therefore does not have to start from guesses about how Valve's ARM-based SteamOS
works: there is an actual distribution to examine.

SteamOS contains both open-source and proprietary components, and Valve's recovery-image
license restricts modification and redistribution of the image. A public port should
therefore be built from independently built, legally redistributable packages, with
proprietary components obtained separately where permitted. This project builds its userspace
privately from Valve's public package repositories and does not redistribute the result
([userspace.md](userspace.md)).

## Technical obstacles

The key question is not whether the SteamOS userspace can run on the Snapdragon XR2, but
whether Linux can initialize and operate the hardware outside PICO OS.

| Area | Assessment | Reason |
| --- | --- | --- |
| Bootloader and Linux boot | access available | PicoUnlock and related tools make custom boot experiments possible. An unlocked bootloader is not a working Linux port: a compatible kernel, device tree, boot image and root filesystem are still required. |
| Adreno GPU acceleration | significant work | The Adreno 650-family GPU has a potential open-source driver route through Mesa's Freedreno/Turnip stack. An accelerated Vulkan desktop or SteamVR compositor on this headset has not been demonstrated. |
| Display initialization | significant work | The display topology, panel initialization, refresh modes and distortion parameters must be understood. The identical per-eye resolution does not make the Steam Frame's display driver interchangeable. |
| 6DoF headset tracking | major blocker | PICO OS has its own camera and IMU processing pipeline. Native Linux operation needs a compatible interface to it or an alternative tracking implementation, including calibration, synchronization and pose prediction. |
| Motion controllers | major blocker | The PICO controllers need their own communication and tracking integration. Valve's Steam Frame controller software cannot be assumed to recognize them, and input decoding alone does not provide controller positions. |
| Wi-Fi, sound, USB-C, battery and power management | device-specific | These need PICO-specific firmware, kernel support and configuration. Some upstream Qualcomm drivers may be reusable, but each subsystem needs hardware testing. |

A successful Linux boot and successful VR operation are therefore very different milestones.

## Development strategy

The research recommends treating the port as a Linux-on-PICO-4 project first, followed by a
SteamOS userspace port, in six steps: establish a recoverable development device, boot a
minimal ARM64 Linux, bring up native graphics, build a SteamOS-compatible ARM64 userspace,
integrate the native XR hardware, and only then attempt Valve's VR session.
[roadmap.md](roadmap.md) holds these steps with their goals and status.

An intermediate alternative is to run SteamOS components on top of the existing Android kernel
and firmware. That could preserve working low-level device support, but would not by itself
make PICO's Android XR services available to a native Linux SteamVR runtime.

## Related projects worth monitoring

| Resource | Why it matters |
| --- | --- |
| [pico4.wiki](https://pico4.wiki/) | Community hardware, rooting and recovery knowledge |
| [more-picohaxx-tool](https://github.com/chaixshot/more-picohaxx-tool) | Original PICO 4 bootloader unlock and backup |
| [PICO-documentation](https://github.com/thoricelli/PICO-documentation) | Device-tree, kernel and hardware research |
| [steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos) | Steam Frame's VR software on alternative Linux |
| [steamac](https://github.com/fxgl/steamac) | ARM64 SteamOS image reuse |
| [Monado](https://gitlab.freedesktop.org/monado/monado) | Native open-source XR runtime |
| [postmarketOS pmaports](https://gitlab.postmarketos.org/postmarketOS/pmaports) | A potential starting point for a dedicated PICO 4 Linux port |

**Naming caution.** The `phoenix` codename alone does not identify matching code. ByteDance's
publicly indexed [`phoenix-kernel`](https://github.com/bytedance/phoenix-kernel) repository, for
example, describes a different SXR2130P-based VR device; its compatibility with the original
PICO 4 should not be assumed.

## Feasibility

Engineering estimates from the publicly documented work at the time of the research, not
development schedules. The measured status of each milestone is in
[roadmap.md](roadmap.md#milestones).

| Milestone | Estimate |
| --- | --- |
| Unlock the PICO 4 bootloader | Already demonstrated |
| Boot minimal Linux | Plausible; no verified PICO 4 port found |
| Obtain accelerated Linux graphics | Plausible but unverified |
| Run the ARM64 SteamOS userspace | Plausible after Linux bring-up |
| Display a Steam-style desktop in the headset | Challenging |
| Full 6DoF and controllers | Major research effort |
| Complete standalone SteamVR experience | Highly uncertain |

The strongest supporting evidence is the September 2026 Steam Frame NixOS experiment. It shows
that Valve's VR session is not fundamentally tied to the Arch-based SteamOS distribution. That
experiment, however, keeps the Frame's hardware-specific environment, including calibration and
device configuration.

## Conclusion

The building blocks for a PICO 4 SteamOS port exist, but no public project had combined them
into a working implementation. The most productive first objective is a documented,
reproducible Linux boot on the original PICO 4 with working Adreno graphics; it enables serious
experiments with Valve's ARM64 SteamOS userspace. For a Steam Frame-like experience, native
tracking and controller support are likely to take more engineering than booting SteamOS
itself.

The two repositories to investigate first are
[PICO-documentation](https://github.com/thoricelli/PICO-documentation), for the headset's
low-level implementation, and [steam-frame-nixos](https://github.com/y0usaf/steam-frame-nixos),
for the Steam Frame's software environment. Together they cover the two sides of the port; the
substantial integration work between them is not covered by either.
