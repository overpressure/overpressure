# Native graphics

Roadmap step 3 ([roadmap.md](roadmap.md)), on Valve's kernel ([kernel.md](kernel.md)):

| | |
| --- | --- |
| Works | Vulkan through Valve's Turnip on msm, OpenGL through zink, both panels through mainline's DPU and DSI drivers with compressed scanout, 90 Hz and 72 Hz |
| Test | `pico4-vulkan-test` (package `pico4-graphics-tests`, [src/graphics](../src/graphics)): an offscreen render with pixel readback, run by `dev check` |
| Panel driver | [panel-pico4-ls026b3sa.c](https://github.com/overpressure/linux/blob/phoenix/7.2/drivers/gpu/drm/panel/panel-pico4-ls026b3sa.c) |
| Presentation in VR | SteamVR's compositor through Turnip's display WSI ([session.md](session.md)) |
| Results | [kernel.md](kernel.md#results) |

## Interfaces on PICO OS 5.13.7

The hardware reference. PICO OS drives the Adreno 650 through Qualcomm's KGSL (`/dev/kgsl-3d0`;
`CONFIG_DRM_MSM` unset, so the GPU is no DRM device) and the display through the techpack's `msm_drm` at
`/dev/dri/card0`. Its single DSI connector advertises one **4320 x 2160** surface; the Sharp LS026B3SA
node describes two 2160 x 2160 panels, two DSI controllers and DSC, with 90 Hz and 72 Hz modes. Its live
DRM properties show:

- **Eye routing.** Each plane's `sspp_layout` (`left` or `right`) selects the eye; destination
  coordinates are local to the eye and start at x = 0.
- **Layers.** Six planes, three per eye (four VIG, two DMA), each one colour component
  (`layer_color_component`) blending at its own stage (`blend_op` `layer_color`).
- **Framebuffer.** One 4320 x 2160 framebuffer for both eyes; the right eye's planes crop its right half.
- **Fences.** Every commit requests the CRTC's `output_fence` and the connector's `RETIRE_FENCE`
  (file descriptors returned through userspace pointers); the composer waits for the retire fence.

## GPU firmware

Six files from `/firmware` of the stock `vendor` partition: `a650_gmu.bin`, `a650_sqe.fw`, `a650_zap.mdt`
and `a650_zap.b00` to `.b02`, out of PICO's public OTA ([development.md](development.md#stock-firmware)):

```sh
python -m overpressure assets --firmware 5.13.7-global-seko graphics-firmware
```

It checks the stock folder's manifest first and writes each file's SHA-256 and the firmware build to
`manifest.json`. The userspace build takes the directory (`--gpu-firmware`, [userspace.md](userspace.md)).
The project distributes no vendor files; `assets/` and the build output stay private.

## Run the test

In [development mode](hardware-testing.md#development-mode) `python -m overpressure dev check` runs it
in stage 2, from `pico4-userspace-check` with the system's Turnip ICD selected (`VK_DRIVER_FILES`).
`pico4-vulkan-test` requires the Turnip driver and an integrated GPU, renders a full-screen triangle into
a 256 x 256 image with four colour quadrants, copies it back and verifies all 65,536 pixels. It is no
performance or conformance test.

## OpenGL

Valve's Mesa has Turnip and zink, no freedreno gallium driver, as on the Frame. gamescope's session and
the Steam client set `MESA_LOADER_DRIVER_OVERRIDE=zink`; Xwayland's glamor, and with it DRI3 for X11
clients, needs it.

## Refresh rates

The panel driver offers 90 Hz and 72 Hz; SteamVR sees both (`Prop_DisplayAvailableFrameRates_Float_Array`)
and starts apps at 72 Hz, the Frame's default ([session.md](session.md#steamvr-configuration)). 72 Hz is
the stock tree's dynamic fps by vertical front porch (`dfps_immediate_porch_mode_vfp`): the same pixel
clock and lines, vtotal 2781 instead of 2225, no panel commands. PICO's `post-72-nt57900-on-command` is
not sent: with it both lenses show a white, blueish, inverted-looking picture. A change of rate is a full
mode set (the kernel commit “drm/msm/dpu: a refresh change is a mode set on DPU 6.0”).

| Fact | Result |
| --- | --- |
| vblanks at 72 Hz, on a table | 72.007 Hz, 72 commits/s, 0 late, margin 7.8 ms |
| Picture at 72 Hz in the lenses | normal in both lenses, in the session and in Beat Saber |
| A change of rate, worn | a short black fade; SteamVR's settings and a game's start both switch it |

## Verified results

Current results are in [kernel.md](kernel.md#results).

## References

- [Mesa Freedreno/Turnip](https://docs.mesa3d.org/drivers/freedreno.html)
- [Qualcomm display fence contract](https://android.googlesource.com/platform/hardware/qcom/sm7250/display/+/202e8c36/sdm/libs/core/drm/hw_device_drm.cpp)
- [Linux DRM userspace interface](https://www.kernel.org/doc/html/latest/gpu/drm-uapi.html)
