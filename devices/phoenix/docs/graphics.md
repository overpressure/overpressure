# Native graphics

Roadmap step 3 ([roadmap.md](roadmap.md)): Vulkan through Valve's Turnip on msm, OpenGL through
zink, and presentation on both panels through mainline's DPU and DSI drivers, on Valve's kernel
([kernel.md](kernel.md)). PICO OS's interfaces stay here as the reference for the
hardware. A native test covers it: an offscreen Vulkan render with pixel readback. It comes with the userspace package `pico4-graphics-tests`
([userspace.md](userspace.md)). SteamVR's compositor presents through Turnip's display WSI
([session.md](session.md)).

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
  through userspace pointers. PICO OS's composer waits for the retire fence, then closes
  both.

## GPU firmware

The kernel loads six files: `a650_gmu.bin`, `a650_sqe.fw`, `a650_zap.mdt`, `a650_zap.b00`, `a650_zap.b01` and `a650_zap.b02`, in `/firmware` of the
stock `vendor` partition of the headset's firmware. They come from PICO's public OTA
([development.md](development.md#stock-firmware)):

```sh
python -m overpressure assets --firmware 5.13.7-global-seko graphics-firmware
```

The command checks the stock folder's manifest first and writes the files' SHA-256 and the
firmware build to `manifest.json`. The project distributes no vendor files; `assets/` and the
build output stay private. The userspace build takes the same directory (`--gpu-firmware`,
[userspace.md](userspace.md)).

## Run the test

The test is in the userspace (`pico4-graphics-tests`). In
[development mode](hardware-testing.md#development-mode), `python -m overpressure dev check` runs it in stage 2.

`pico4-vulkan-test` selects the system's Turnip ICD explicitly (`VK_DRIVER_FILES`), requires the Turnip
driver and an integrated GPU, renders a full-screen triangle into a 256 x 256 image with four colour
quadrants, waits for the fence, copies the image back and verifies all 65,536 pixels. It is no
performance or conformance test.

## OpenGL

The GPU's driver is Valve's Mesa: Turnip on msm, OpenGL through zink, as on the Frame; the package has no
freedreno gallium driver. gamescope's session and the Steam client set
`MESA_LOADER_DRIVER_OVERRIDE=zink`; Xwayland's glamor, and with it DRI3 for X11 clients, needs it.

## Refresh rates

The panel driver offers 90 Hz and 72 Hz, and SteamVR sees both (`Prop_DisplayAvailableFrameRates_Float_Array`).
The 72 Hz mode is the stock tree's dynamic fps by vertical front porch
(`dfps_immediate_porch_mode_vfp`): the same pixel clock and lines, vtotal 2781 instead of 2225, and no
panel commands. PICO's `post-72-nt57900-on-command` is not sent: with it both lenses show a white, blueish,
inverted-looking picture. A change between the rates is a full mode set (patch 0030).

| Fact | Result |
| --- | --- |
| vblanks at 72 Hz, on a table | 72.007 Hz, 72 commits/s, 0 late, margin 7.8 ms |
| Picture at 72 Hz in the lenses without the commands | normal in both lenses, in the session and in Beat Saber |
| A change of rate, worn | a short black fade; SteamVR's settings and a game's start both switch it |

## Verified results

Current results are in [kernel.md](kernel.md#results).

## References

- [Mesa Freedreno/Turnip](https://docs.mesa3d.org/drivers/freedreno.html)
- [Qualcomm display fence contract](https://android.googlesource.com/platform/hardware/qcom/sm7250/display/+/202e8c36/sdm/libs/core/drm/hw_device_drm.cpp)
- [Linux DRM userspace interface](https://www.kernel.org/doc/html/latest/gpu/drm-uapi.html)
