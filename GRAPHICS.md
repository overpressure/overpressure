# Native graphics bring-up

Roadmap step 3 builds on the verified Linux-shell and recovery path. The stock kernel and
device trees remain unchanged. Graphics tools and privately extracted firmware are added
through an optional overlay; the normal shell image stays independent of these experiments.

## Interfaces observed on PICO OS 5.13.7

The original PICO 4 exposes `msm_drm` for display and KGSL for its Adreno 650 GPU. The DSI
connector advertises a combined **4320 x 2160** surface. The selected Sharp LS026B3SA device-tree
node describes two 2160 x 2160 panels, two DSI controllers and DSC compression, with 90 Hz and
72 Hz modes. Object IDs are discovered by the native test, rather than assumed stable.

Stock Android's live DRM properties show that PICO selects eyes through `sspp_layout`
(`left`/`right`). Destination coordinates are local to each eye and start at x=0. The default
test shares one 4320 x 2160 framebuffer across both eyes, selecting the right half through
the source crop. It uses four VIG planes and two DMA planes, with separate red, green and blue
components and blend stages for each eye. `pico4-kms-test --inspect` reads plane
properties without acquiring DRM master or changing the display; it also runs under Android
when invoked with the matching musl loader and libdrm from the POC userspace.

Plane capabilities and property enum names are discovered at runtime. Every frame requests
the vendor CRTC release fence and connector retire fence, waits for the retire fence, then
closes both descriptors. This matches the stock display userspace contract. Using page-flip
events alone produced visually corrupted output despite apparently correct timing; the
fence-based path produced steady images with continuous framebuffer swaps.

The mode names contain 90/72 Hz, but clock/total arithmetic in generic `drm_info`/`modetest`
reports approximately 32.83/26.27 Hz. Acceptance explicitly subscribes to kernel vblank events
and separately measures page-flip completion throughput with the monotonic clock. The first
experiment's page-flip events contained zero timestamps and sequence numbers: those fields
cannot establish refresh or dropped frames. Schema 2 reports unavailable measurements as
null. Neither a mode name nor a successful modeset ioctl proves physical output.

An initial 72 Hz experiment hit the stock driver's `DMS not supported on first frame` error.
The native test therefore starts at the advertised boot-time 90 Hz mode, presents frames,
then attempts 72 Hz in the same DRM session.

## Build the optional graphics overlay

The tested Alpine 3.24 Mesa package enables MSM/virtio backends, but omits KGSL. This build
uses unmodified Mesa 26.1.6 with `-Dfreedreno-kmds=kgsl`. Its release archive is checked against
the SHA-512 in [Alpine's package recipe](https://github.com/alpinelinux/aports/blob/3.24-stable/main/mesa/APKBUILD).
The native test programs and shaders under `src/graphics` are project code.

On Linux/WSL, install QEMU user emulation with an enabled aarch64 binfmt handler. First run
the normal POC build to cache the verified Alpine minirootfs. Then, as root:

```sh
bash scripts/build-graphics.sh /var/tmp/pico4-graphics-build "$PWD/out/graphics-overlay"
```

Use a fresh, dedicated build directory on a Linux filesystem. The script installs build
dependencies inside it, builds Mesa and the test tools, and writes an overlay. It does not
access the headset. Keep build output private; it is excluded from Git.

Supply the matching GPU firmware from **your own verified stock backup's vendor partition**
in `out/graphics-overlay/lib/firmware/`: `a650_gmu.bin`, `a650_sqe.fw`, `a650_zap.mdt`,
`a650_zap.b00`, `a650_zap.b01` and `a650_zap.b02`. Record hashes and the originating firmware.
Do not substitute firmware from an unrelated headset. No vendor blobs are distributed here.

```sh
python build.py build --firmware 5.13.7-global-seko --name pico4-poc-graphics --extra-overlay out/graphics-overlay --extra-packages vulkan-loader libstdc++ spirv-tools libexpat zlib zstd-libs
```

The complete image SHA-256 covers the overlay. `--extra-overlay` can override rootfs files,
so use only reviewed local files. `build-info` records the resolved Alpine packages.

## Run and interpret the tests

Use the preparation and stock-backup requirements from [HARDWARE-TEST.md](HARDWARE-TEST.md).
Add `--graphics all --graphics-seconds 45` to an explicit `flash-run` invocation using the
graphics image. `--graphics kms` and `--graphics vulkan` isolate either subsystem. The existing
identity checks, storage checks, watchdog, failure cleanup and stock-boot restoration apply.

* **Vulkan:** select the explicit KGSL Turnip ICD; require the hardware Turnip driver and an
  integrated GPU. Render a triangle covering a 256 x 256 image with four known color regions,
  submit it, wait for the fence, copy it back and verify all 65,536 pixels. Software fallback
  cannot pass. This is an offscreen rendering check, not a performance or conformance claim.
* **KMS:** use six color-component planes with explicit eye routing, a shared double-buffered
  image and exact driver-advertised modes. Show a dim grid with distinct L/R markers. Request
  and wait for display fences. Measure kernel vblank timing and commit
  throughput at 90 Hz then 72 Hz. Require both rates within 5%, enough samples/completed frames
  for the requested duration and no gaps in the vblank samples. Sample gaps indicate an
  incomplete measurement; they do not by themselves prove dropped physical scanouts. The
  validator also requires the tested layer arrangement, buffer layout and fence contract.
* **Physical output:** inspect both lenses for stable, complete images. The runner explicitly
  records visual confirmation as pending; it cannot infer that from DRM events.

Results include `graphics-vulkan.json`, `graphics-kms.json`, separate stderr logs, post-test
kernel logs and the usual recovery evidence. A failed graphics phase fails the run even when
stock restoration succeeds. Readback evidence and all hardware reports remain private.

For several experiments in one Linux boot, use the bounded development session documented in
[HARDWARE-TEST.md](HARDWARE-TEST.md#iterate-within-one-linux-boot). Upload replacement
test binaries to `/run` through the run's pinned SSH configuration. Useful diagnostic flags
after the duration argument are `--static`, `--static-buffer-one`, `--repeat-buffer`,
`--blocking`, `--full-state`, `--per-eye-buffers`, `--opaque-layers` and `--no-retire-fence`.
Static, same-buffer and unvalidated layout/fence variants report `diagnostic`, never graphics
acceptance. They separate scanout stability, individual buffers and commit timing.
Confirm that a mode is still active before requesting a visual observation: the test disables
the display when it ends, so black output after completion is expected.

Passing the independent KMS and offscreen Vulkan tests does not yet demonstrate a compositor,
direct GPU-buffer scanout, lens correction or an XR session. These require their own integration
and visual checks before broader native-graphics claims.

## Hardware evidence, October 17, 2026

The first native run on PICO OS 5.13.7's kernel passed the Turnip hardware test: Mesa 26.1.6
identified Adreno 650 and verified all 65,536 rendered pixels. Its KMS test failed. The user
briefly saw the grid, followed by gray and then corrupted output. Automatic stock-boot
restoration completed, and the user confirmed normal output in both lenses.

The kernel's unused-regulator cleanup disabled `display_panel_avdd` shortly after the grid
started. A subsequent stock Android comparison found the same supply disabled with the
display active, so this correlation does not establish a power fault. No regulator override
or device-tree modification was applied. The corrected experiment instead adds explicit eye
routing and a continuous vblank subscription based on the observed DRM interfaces.

In the first persistent development session, six-layer static scanout was confirmed visually
at both refresh rates: a steady red L grid on the left and blue R grid on the right. A
70-second measurement per mode recorded **89.9979 Hz** and **72.0048 Hz**, with zero vblank
sequence gaps. A repeated swapping test still visibly flickered/corrupted at 90 Hz, even
after full panel power-off/on. This narrows the problem to frame updates; it does not establish
that bootloader splash handoff is the cause. The session reached its configured deadline and
automatically restored and verified PICO OS. Static evidence cannot close step 3.

The second session isolated the frame-update behavior. Repeated commits of one buffer,
blocking commits, resubmission of plane properties and a shared framebuffer all still
flickered. Adding the release/retire fence contract produced stable physical output at both
refresh rates. A **120-second run per mode** passed: 10,801 flips at **90.0021 Hz**, then 8,642
flips at **72.0082 Hz**, with zero vblank sequence gaps. The user confirmed steady red L and
blue R grids during each mode. This establishes the working userspace sequence; it does not
prove which internal vendor-driver behavior caused the unfenced failure.

The rebuilt default image then passed the complete acceptance run from a fresh Linux boot:

| Check | Result |
| --- | --- |
| Hardware Vulkan render/readback | Turnip Adreno 650; all 65,536 pixels verified |
| 90 Hz, 90 seconds | 8,100 flips; 89.9976 Hz; zero vblank sequence gaps |
| 72 Hz, 90 seconds | 6,481 flips; 72.0045 Hz; zero vblank sequence gaps |
| Physical output | User confirmed steady, correct L/R grids during each active mode |
| Recovery | Automatic stock-boot restoration; full boot and original ABL readbacks matched the backup |
| Final Android state | Matching PICO OS 5.13.7; boot complete; SELinux Enforcing; bootloader remains unlocked |
| Local checks | 72 unit tests; native tools compiled with warnings treated as errors |

**Roadmap step 3 is complete on this tested original PICO 4.** The fresh run required no
preliminary panel power cycle. Offscreen GPU rendering and CPU-buffer KMS presentation are
verified separately; direct GPU-buffer scanout, compositor integration, lens correction and
XR operation remain separate work. These bounded tests are not a long-duration reliability
or Vulkan conformance claim.

Private completion evidence is indexed by `out/stage3-completion-20261017.json`. Automated
runner results and user visual observations are retained separately and joined by run ID.

Primary references: [Mesa Freedreno/Turnip](https://docs.mesa3d.org/drivers/freedreno.html),
[Mesa KGSL backend](https://gitlab.freedesktop.org/mesa/mesa/-/blob/mesa-26.1.6/src/freedreno/vulkan/tu_knl_kgsl.cc),
[Qualcomm display fence contract](https://android.googlesource.com/platform/hardware/qcom/sm7250/display/+/202e8c36/sdm/libs/core/drm/hw_device_drm.cpp),
[Linux DRM userspace interface](https://www.kernel.org/doc/html/latest/gpu/drm-uapi.html),
[Android logical partition format](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fs_mgr/liblp/include/liblp/metadata_format.h).
