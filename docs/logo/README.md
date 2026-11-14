# The logo

A headset whose two lenses are pressure gauges, each needle through the red zone: overpressure. The
drawings are the SVG files here; the PNG and ICO files are rendered from them.

[mockups.html](mockups.html) shows the logo in use (the README, the social card, the boot indication, the
window, docks, app grids, a browser tab), in light and dark.

## Drawings

| File | What it is | Where it is used |
| --- | --- | --- |
| [overpressure.svg](overpressure.svg) | The app icon: the headset with strap mounts, lens rims and dials on a dark tile | The window icon from 64 px, the README in dark mode, the headset page in the dark appearance, docks and app grids |
| [overpressure-light.svg](overpressure-light.svg) | The same on a light tile | The README in light mode, the headset page in the light appearance |
| [overpressure-small.svg](overpressure-small.svg) | The app icon below 64 px: a larger headset without strap mounts, rims or sheen, which blur at that size | The window icon from 16 to 48 px (title bar, taskbar), favicons |
| [overpressure-light-small.svg](overpressure-light-small.svg) | The same on a light tile | Small sizes on light surfaces |
| [overpressure-mark.svg](overpressure-mark.svg) | The app icon's headset without its tile | The window's header, the social card's watermark, the boot indication |
| [overpressure-mono.svg](overpressure-mono.svg) | One colour (`currentColor`), traced from the app icon: its blue and dark parts are ink, its white and orange parts cut out, the lens rims a thin gap | Menu bars and trays that take a template icon, print |

The colours: the visor runs from `#5a92ff` through `#2a66ec` to `#1a43b8` (the window's accent family),
the lenses are `#0b0d12`, the red zone `#ff7849`, the needle and hub white. The dark tile runs from
`#2b2e35` to `#0f1013`, the light one from `#ffffff` to `#eef0f3`.

## Renderings

| File | Size | Use |
| --- | --- | --- |
| [overpressure-social.png](overpressure-social.png), [overpressure-social-light.png](overpressure-social-light.png) | 1280 &times; 640 | The repository's social preview (the image a link to it unfurls into) |
| [overpressure-social-packages.png](overpressure-social-packages.png), [overpressure-social-linux.png](overpressure-social-linux.png), [overpressure-social-builder.png](overpressure-social-builder.png) | 1280 &times; 640 | The social previews of [packages](https://github.com/overpressure/packages), [linux](https://github.com/overpressure/linux) and [builder](https://github.com/overpressure/builder) |
| [overpressure-avatar.png](overpressure-avatar.png) | 512 &times; 512 | The GitHub organization's avatar |
| [`overpressure/window/icons/`](../../overpressure/window/icons/) | `app-*.png` 16 to 256 px, `app-light-*.png` 64 to 192 px, `mark-*.png` 24 to 72 px high, `overpressure.ico` | The window: Tk draws no SVG and scales no image, so [logo.py](../../overpressure/window/logo.py) picks the rendering nearest to the display's scaling |

A browser (Chromium) renders them from the drawings, each at the size it is used. GitHub takes the avatar and the social previews only through its settings pages: they are uploaded by hand. After a drawing changes,
render them again rather than editing them. The headset's boot indication draws the mark itself, from the
same geometry
([pico4-splash.c](https://github.com/overpressure/phoenix/blob/main/src/splash/pico4-splash.c)).
