# Native graphics

Roadmap step 3 ([roadmap.md](roadmap.md)): Vulkan through Mesa's Turnip driver on the stock
kernel's KGSL GPU interface, and presentation on both panels through the stock `msm_drm` display
driver, with the stock kernel and device trees unchanged. Two native tests cover the two halves
separately: an offscreen Vulkan render with pixel readback, and CPU-drawn frames on both panels
through atomic KMS with the vendor display fences. Both pass on the headset, the KMS test at
90 Hz and 72 Hz. They come with the userspace package `pico4-graphics-tests`
([userspace.md](userspace.md)). Monado's KMS compositor target ([xr.md](xr.md)) and SteamVR's
display layer ([session.md](session.md)) present GPU-rendered frames over the same display path.

## Interfaces on PICO OS 5.13.7

The GPU is an Adreno 650 behind Qualcomm's KGSL driver (`/dev/kgsl-3d0`, `CONFIG_QCOM_KGSL`).
The upstream `msm` GPU driver is not built (`CONFIG_DRM_MSM` is unset), so the GPU is not a DRM
device. The display driver is Qualcomm's `msm_drm` from the kernel's techpack, at
`/dev/dri/card0`. Its single DSI connector advertises a combined **4320 x 2160** surface. The
selected Sharp LS026B3SA device-tree node describes two 2160 x 2160 panels, two DSI controllers
and DSC compression, with 90 Hz and 72 Hz modes.

Stock Android's live DRM properties show how PICO OS drives the panels:

- **Eye routing.** Each plane's `sspp_layout` property (`left` or `right`) selects the eye.
  Destination coordinates are local to each eye and start at x = 0; the 4320-wide mode is not a
  generic side-by-side CRTC layout.
- **Layers.** Six planes, three per eye: four VIG planes and two DMA planes. Each carries one
  colour component (`layer_color_component` red, green or blue) and blends at its own stage
  (`blend_op` `layer_color`).
- **Framebuffer.** One 4320 x 2160 framebuffer serves both eyes; the right eye's planes select
  its right half through the source crop.
- **Fences.** Every commit requests the CRTC's release fence (`output_fence`) and the
  connector's retire fence (`RETIRE_FENCE`). These vendor properties return file descriptors
  through userspace pointers. The display userspace waits for the retire fence, then closes
  both.

Plane capabilities, property enum values and object IDs are read at run time, not assumed.
`pico4-kms-test --inspect` prints every plane's properties without becoming DRM master or
changing the display.

Driver behaviour the tests account for:

- **Mode timing.** The mode names say 90 and 72 Hz, but generic tools (`drm_info`, `modetest`)
  compute about 32.83 and 26.27 Hz from the modes' clock and totals. Neither a mode name nor a
  successful modeset proves the physical refresh, so the KMS test measures it.
- **Events.** The driver takes no vblank reference for page-flip completions. Without a separate
  vblank request, page-flip events carry zero timestamps and sequence numbers, which cannot
  establish the refresh rate or dropped frames.
- **First frame.** A first commit at 72 Hz fails with `DMS not supported on first frame`. The
  switch to 72 Hz works after frames were presented at the boot-time 90 Hz mode, in the same DRM
  session.
- **Panel supply.** The kernel's cleanup of unused regulators disables `display_panel_avdd`
  after boot. Stock PICO OS runs with the same supply disabled while the display is active, so
  this alone does not indicate a power fault. No regulator or device-tree change is made.

## GPU firmware

The kernel loads six files: `a650_gmu.bin`, `a650_sqe.fw`, `a650_zap.mdt`, `a650_zap.b00`, `a650_zap.b01` and `a650_zap.b02`, in `/firmware` of the
stock `vendor` partition of the headset's firmware. They come from PICO's public OTA
([development.md](development.md#stock-firmware)):

```sh
python3 -m overpressure stock --firmware 5.13.7-global-seko /var/tmp/pico4-stock/5.13.7-global-seko
devices/phoenix/scripts/extract-graphics-firmware.sh /var/tmp/pico4-stock/5.13.7-global-seko assets/graphics-firmware-5.13.7
```

The script checks the stock directory's manifest first and writes the files' SHA-256 and the
firmware build to `manifest.json`. The project distributes no vendor files; `assets/` and the
build output stay private. The userspace build takes the same directory (`--gpu-firmware`,
[userspace.md](userspace.md)).

## Run the tests

Both tests are in the userspace (`pico4-graphics-tests`). In
[development mode](hardware-testing.md#development-mode), `python -m overpressure dev check` runs
the Vulkan test and the display checks in stage 2; `pico4-kms-test` runs by hand on the headset.

- **Vulkan** (`pico4-vulkan-test`): selects the KGSL Turnip ICD explicitly (`VK_DRIVER_FILES`),
  requires the Turnip driver and an integrated GPU, renders a full-screen triangle into a
  256 x 256 image with four colour quadrants, waits for the fence, copies the image back and
  checks every pixel. It prints JSON on stdout with `status`, `driver`, the image size,
  `verified_pixels` and `bad_pixels`, and the GPU and driver names on stderr. A pass needs all
  65,536 pixels; a software fallback cannot pass. This is an offscreen rendering check, not a
  performance or conformance test.
- **KMS** (`pico4-kms-test SECONDS [FLAG...]`): becomes DRM master and takes the connected DSI
  connector, its exact 4320 x 2160 modes at 90 and 72 Hz and six RGB planes in the stock
  arrangement. It shows a dim grid with an L on the left panel (red tint) and an R on the right
  (blue tint). Every frame alternates between two shared framebuffers in a nonblocking atomic
  commit with a page-flip event and both display fences, and waits for the retire fence. One
  vblank event stays queued throughout. The test runs SECONDS at 90 Hz, then SECONDS at 72 Hz in
  the same DRM session, then disables the display. Except with `--inspect` it runs only in the
  POC environment (`/etc/pico4-poc/build-info`).
- **Physical output:** stable, complete images in both lenses can only be confirmed by eye. The
  test disables the display when it ends, so black output afterwards is expected: check that a mode is still active before
  looking.

The KMS test prints one JSON object (schema 2) with the tested arrangement (`composition`,
`buffer_layout`, `eye_routing`, `presentation`, `retire_fence`) and, per mode:

| Field | Meaning |
| --- | --- |
| `vblank_hz`, `vblank_samples`, `vblank_sequence_gaps` | refresh measured from kernel vblank events |
| `completion_hz`, `flips` | page-flip completions per second, timed with the monotonic clock |
| `page_flip_hz`, `page_flip_sequence_gaps` | from the page-flip events' own fields; `null` when these are unusable |
| `status` | `passed`, `failed`, or `diagnostic` for a variant (below) |

A mode passes with both rates within 5% of the mode, at least 90% of the expected flips and
vblank samples, and zero vblank sequence gaps. Gaps mean the measurement is incomplete; they do
not by themselves prove a dropped scanout.

Diagnostic variants, given after the duration, separate scanout stability, individual buffers
and commit timing. They report `diagnostic`, never a pass:

| Flag | Variant |
| --- | --- |
| `--static` | no commits after the modeset; vblank measurement only |
| `--static-buffer-one` | as `--static`, showing the second buffer |
| `--repeat-buffer` | commits the same buffer every frame |
| `--blocking` | blocking instead of nonblocking commits |
| `--full-state` | resubmits every plane property with each commit |
| `--per-eye-buffers` | one 2160 x 2160 framebuffer per eye instead of the shared one |
| `--opaque-layers` | two opaque planes, one per eye, instead of the six colour layers |
| `--no-retire-fence` | page-flip events only, without the release and retire fences |

In the userspace, `pico4-turnip-kgsl` provides Turnip, unmodified Mesa 26.1.6 with
`-Dvulkan-drivers=freedreno -Dfreedreno-kmds=kgsl,msm` (the release archive checked against the
SHA-512 of [Alpine's package recipe](https://github.com/alpinelinux/aports/blob/3.24-stable/main/mesa/APKBUILD)),
under its own ICD file (`/usr/share/vulkan/icd.d/pico4_turnip_kgsl.aarch64.json`), and
`pico4-graphics-tests` the two tests. `pico4-userspace-check` runs the Vulkan test there
([userspace.md](userspace.md)). It builds the `msm` backend beside KGSL
(`-Dfreedreno-kmds=kgsl,msm`) only for libdrm ([OpenGL](#opengl)); KGSL is enumerated first,
so the `msm` backend never runs on the stock kernel. Its two patches:

- [kgsl-context-priority.patch](../userspace/pkgbuilds/pico4-turnip-kgsl/kgsl-context-priority.patch)
  gives Vulkan's queue priorities to the KGSL contexts. Upstream, the KGSL backend reports a
  single priority and creates every context at KGSL's default level 8. The PICO 4's KGSL
  preempts between four ringbuffers, one per four levels (`preemption` is 1), so the patch
  reports four priorities and maps realtime, high, medium and low to levels 1, 4, 8 and 12.
- [kgsl-display.patch](../userspace/pkgbuilds/pico4-turnip-kgsl/kgsl-display.patch): with
  libdrm, Turnip offers `VK_KHR_display`, and the KGSL backend refused every instance that
  enables it (`vkEnumeratePhysicalDevices` returned `VK_ERROR_INITIALIZATION_FAILED`;
  `vrcompositor` aborted). KGSL has no DRM master, so the display WSI now reports no displays;
  the panels stay the display layer's ([session.md](session.md)).

The Stearlight project (Quest 2, the same Mesa 26.1.6 KGSL backend) works around three
defects. One applies here:

- **Holes in the right eye.** Without an extra memory barrier before the compositor's render
  passes, chunks of SteamVR's overlays are missing in the right eye while the left is
  complete. A single frame dumped by the display layer, downscaled by four, did not show it;
  in the lenses it is plain with one eye closed. `steamvr.service` sets
  `PICO4_VRDISPLAY_PASS_BARRIER=1`, and the display layer then records a full memory barrier
  (all writes to all reads and writes) before every render pass of SteamVR's processes. In a
  blind comparison the overlay was complete in both eyes with it and the whole picture looked
  better; the rate stayed the same (57.5 against 58.0 commits/s, GPU wait 10.2 against
  9.9 ms). Which pass reads too early is not narrowed down.
- **Dma-buf allocations keeping their fd** do not occur: in 26.1.6 the DMA-heap and ION paths
  close the allocation's fd after the import; the buffer keeps only its own duplicate, which
  freeing it closes. Over two minutes of Valve's session `vrcompositor` stays at 235–237 open
  fds and 611 MiB of KGSL memory, gamescope and the dashboard's renderer just as flat.
- **A cross-process timeline semaphore** is not needed: the display layer already emulates
  SteamVR's shared timeline and binary semaphores over sync files
  ([session.md](session.md#shared-semaphores)).

## OpenGL

The GPU's only driver is Turnip on KGSL. Holo's Mesa (25.2.7) brings the rest of the OpenGL
stack: EGL, GLX, GBM and zink, Mesa's OpenGL on Vulkan. Mesa's loader picks an OpenGL driver
by the DRM node's kernel driver; `msm_drm`'s render node, the only one, names `msm`, whose
driver needs an msm GPU, so on its own the loader falls back to llvmpipe. Xwayland in gamescope
then refuses glamor ("Refusing to try glamor on llvmpipe"), and the Steam client and its web
helper draw on the CPU, too slowly to use in the headset.

`MESA_LOADER_DRIVER_OVERRIDE=zink` selects zink instead, in gamescope's session (Xwayland) and
`steam.service` (the client, its web helper and the games it starts)
([session.md](session.md)). Zink finds the Vulkan device for the render node through
`VK_EXT_physical_device_drm`, which the display layer reports (`PICO4_VRDISPLAY=1`).
Surfaceless EGL needs neither.

Zink presents through Turnip's window-system code (Wayland, or X11 through gamescope's WSI
layer). A Turnip built with only the KGSL backend builds Mesa without libdrm: Mesa's build drops
libdrm when no kernel interface needs it. Without `HAVE_LIBDRM` the swapchain code has no DRM
image type, and with assertions off its `UNREACHABLE` default falls through to the CPU type.
The Wayland swapchain's DRM parameters were then read as CPU ones: the modifier-list count (1)
landed in the `alloc_shm` function pointer, and every GL window crashed with SIGBUS at address
0x1. `pico4-turnip-kgsl` therefore builds the `msm` backend too, for libdrm; with it
the swapchains take the DRM path. KGSL has no DRM node to share, so a swapchain blits each image
into a linear buffer exported as a dma-buf (the prime path).

## Verified results

Original PICO 4, PICO OS 5.13.7, stock kernel `4.19.81-perf+`:

| Check | Result |
| --- | --- |
| Vulkan render and readback | Mesa 26.1.6 Turnip on the Adreno 650: all 65,536 pixels verified |
| KMS acceptance from a fresh boot, 90 s per mode | 90 Hz: 8,100 flips, vblank 89.9976 Hz; 72 Hz: 6,481 flips, vblank 72.0045 Hz; zero vblank sequence gaps; no panel power cycle needed |
| KMS, 120 s per mode | 90 Hz: 10,801 flips at 90.0021 Hz; 72 Hz: 8,642 flips at 72.0082 Hz; zero vblank sequence gaps |
| Physical output | both panels stable at 2160 x 2160 with continuous framebuffer swaps in both modes; steady red L and blue R grids, confirmed by eye |
| Working display path | the stock six-layer RGB arrangement, eye routing through `sspp_layout`, release and retire fences on every commit |
| Static scanout (no commits after the modeset), 70 s per mode | steady in both modes, confirmed by eye; vblank 89.9979 Hz and 72.0048 Hz, zero gaps |
| Updates without the fences | page-flip events alone run at the right rate but give visibly flickering, corrupted output at 90 Hz, also after a full panel power cycle; repeating one buffer, blocking commits, resubmitting all plane properties and a shared framebuffer each still flicker |
| Recovery after the acceptance run | automatic stock-boot restoration; full readbacks of boot and the original ABL match the backup; PICO OS 5.13.7 completes its boot with SELinux enforcing; the bootloader stays unlocked |

In the userspace (installed system, Valve's session):

| Check | Result |
| --- | --- |
| `vkEnumeratePhysicalDevices` with each set of instance extensions (none, surface, display, display with DRM acquire, Xlib, Wayland) | one device each, `VK_SUCCESS`; before `kgsl-display.patch` every set with `VK_KHR_display` failed with `VK_ERROR_INITIALIZATION_FAILED` |
| OpenGL through zink | Xwayland and the Steam client load zink on the KGSL Turnip; the web helper's GPU report: `ANGLE (Mesa, zink Vulkan 1.3(Turnip Adreno (TM) 650 (MESA_TURNIP)), OpenGL 4.3 (Core Profile) Mesa 25.2.7)`, GPU compositing enabled; the client runs without the SIGBUS of a KGSL-only build |
| Valve's 30 s session check with the client's UI on the GPU | 89.94–90.0 commits/s, 0.0–0.15 % late, no stall in the window and no one-second wait in five runs in a row ([session.md](session.md#one-second-waits)) |

Which behaviour inside the vendor driver corrupts unfenced updates is not known. The tests are
bounded: they make no long-duration reliability or Vulkan conformance claim, and they verify
offscreen GPU rendering and CPU-buffer presentation separately. GPU-rendered frames on the
panels, lens correction and XR are verified in [xr.md](xr.md) and [session.md](session.md).

## References

- [Mesa Freedreno/Turnip](https://docs.mesa3d.org/drivers/freedreno.html)
- [Mesa KGSL backend](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.1.6/src/freedreno/vulkan/tu_knl_kgsl.cc)
- [Qualcomm display fence contract](https://android.googlesource.com/platform/hardware/qcom/sm7250/display/+/202e8c36/sdm/libs/core/drm/hw_device_drm.cpp)
- [Linux DRM userspace interface](https://www.kernel.org/doc/html/latest/gpu/drm-uapi.html)
- [Android logical partition format](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fs_mgr/liblp/include/liblp/metadata_format.h)
