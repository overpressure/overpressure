# Native graphics

Roadmap step 3 ([roadmap.md](roadmap.md)): Vulkan through Mesa's Turnip driver on the stock
kernel's KGSL GPU interface, and presentation on both panels through the stock `msm_drm` display
driver, with the stock kernel and device trees unchanged. Two native tests cover the two halves
separately: an offscreen Vulkan render with pixel readback, and CPU-drawn frames on both panels
through atomic KMS with the vendor display fences. Both pass on the headset, the KMS test at
90 Hz and 72 Hz. They run from an optional Alpine overlay in the stage-1 image
([stage1.md](stage1.md)) or from the userspace package `pico4-graphics-tests`
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
changing the display. It also runs under PICO OS, with the musl loader and libdrm of the stage-1
root file system.

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

## Graphics overlay for the stage-1 image

Alpine 3.24's Mesa package builds Turnip for the `msm` and virtio kernel interfaces, not KGSL.
[scripts/build-graphics.sh](../scripts/build-graphics.sh) builds unmodified Mesa 26.1.6 with
`-Dvulkan-drivers=freedreno -Dfreedreno-kmds=kgsl` and nothing else (no OpenGL, no window
systems). It checks the release archive against the SHA-512 in
[Alpine's package recipe](https://github.com/alpinelinux/aports/blob/3.24-stable/main/mesa/APKBUILD)
and compiles the project's test programs and shaders from [src/graphics/](../src/graphics/),
with warnings treated as errors.

It runs as root on Linux or WSL, with QEMU user emulation and an enabled aarch64 binfmt handler.
It starts from the verified Alpine minirootfs that the stage-1 build caches, so run
`python -m pico4 build` once first:

```sh
bash scripts/build-graphics.sh /var/tmp/pico4-graphics-build "$PWD/out/graphics-overlay"
```

Use a dedicated build directory on a Linux file system. The script installs the build
dependencies inside it and writes the overlay; it does not access the headset. The overlay holds
Turnip (`usr/lib/libvulkan_freedreno.so`) with its ICD file
(`usr/share/vulkan/icd.d/pico4-turnip.json`), the two tests in `usr/bin`, and in
`usr/share/pico4-poc/graphics/` the SPIR-V shaders, the list of Alpine build packages and the
Mesa provenance.

The GPU firmware is not part of it. The kernel loads six files: `a650_gmu.bin`, `a650_sqe.fw`,
`a650_zap.mdt`, `a650_zap.b00`, `a650_zap.b01` and `a650_zap.b02`. Take them from **your own**
verified stock backup ([device-preparation.md](device-preparation.md)), never from another
headset. They are in `/firmware` of the `vendor` logical partition inside the backup's `super`
image. [pico4/superimg.py](../pico4/superimg.py) extracts the partition after verifying its
metadata checksums, and `debugfs` reads it without writing:

```sh
python -m pico4 device verify-backup --backup BACKUP
python -m pico4.superimg extract BACKUP/lun0/super.bin vendor /var/tmp/vendor.img
mkdir -p assets/graphics-firmware-5.13.7 out/graphics-overlay/lib/firmware
for f in a650_gmu.bin a650_sqe.fw a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
    debugfs -R "dump /firmware/$f assets/graphics-firmware-5.13.7/$f" /var/tmp/vendor.img
    cp "assets/graphics-firmware-5.13.7/$f" out/graphics-overlay/lib/firmware/
done
```

Record the files' SHA-256 and the firmware build they come from. The project distributes no
vendor files; `assets/` and the build output stay private. The userspace build takes the same
directory (`--gpu-firmware`, [userspace.md](userspace.md)).

A stage-1 image with the overlay and the libraries the tests need:

```sh
python -m pico4 build --firmware 5.13.7-global-seko --name pico4-poc-graphics \
    --extra-overlay out/graphics-overlay \
    --extra-packages vulkan-loader libstdc++ spirv-tools libexpat zlib zstd-libs
```

The image's SHA-256 covers the overlay. `--extra-overlay` can replace any file of the root file
system, so use only reviewed local files. `/etc/pico4-poc/build-info` in the image records the
resolved Alpine packages.

## Run the tests

Prepare the headset and its backup as in [device-preparation.md](device-preparation.md) and
[hardware-testing.md](hardware-testing.md), then add `--graphics` to a `flash-run` with the
graphics image:

```sh
python -m pico4 hwtest flash-run --serial SERIAL --confirm-device SERIAL --backup BACKUP \
    --image out/pico4-poc-graphics.img --graphics all --graphics-seconds 45
```

`--graphics vulkan` or `--graphics kms` runs one test. `--graphics-seconds` is the KMS
measurement per mode (3 to 120 s, default 10). The run's identity and storage checks, the
device watchdog, failure cleanup and stock-boot restoration apply as in every hardware run.
`--graphics` cannot be combined with `--debug-session-seconds`. In
[development mode](hardware-testing.md#development-mode), `python -m pico4 dev check` runs the
Vulkan test and the display checks in stage 2 instead; a `flash-run` leaves the stock boot image
in `boot` and so ends development mode.

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
- **Physical output:** stable, complete images in both lenses can only be confirmed by eye; the
  runner records `graphics_visual_confirmation: not_recorded`. The test disables the display
  when it ends, so black output afterwards is expected: check that a mode is still active before
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
not by themselves prove a dropped scanout. The host validator
([pico4/graphics.py](../pico4/graphics.py)) also requires the stock arrangement: `rgb_layers`
composition, a `combined` buffer, `sspp_layout` routing, `page_flip` presentation with the
retire fence, and both modes, 90 Hz first.

The run directory receives `graphics-vulkan.json` and `graphics-kms.json` (stdout), the matching
`.log` files (stderr), the kernel log and the display state after the tests. A failed graphics
phase fails the run even when stock restoration succeeds.

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

For several experiments in one Linux boot, keep the boot with a development session
(`--debug-session-seconds`, [hardware-testing.md](hardware-testing.md)) and upload replacement
test binaries to `/run` through the run's SSH configuration.

In the userspace, `pico4-turnip-kgsl` provides Turnip under its own ICD file
(`/usr/share/vulkan/icd.d/pico4_turnip_kgsl.aarch64.json`) and `pico4-graphics-tests` the same
two tests, built for glibc. `pico4-userspace-check` runs the Vulkan test there
([userspace.md](userspace.md)).

## Verified results

Original PICO 4, PICO OS 5.13.7, stock kernel `4.19.81-perf+`, tests from the stage-1 overlay:

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
