# The logo

**TL;DR.** The logo's drawings and every rendering of them live in
[overpressure/brand](https://github.com/overpressure/brand), by where they are used (GitHub, Matrix, Reddit,
the installer's window), with its colours and the rules for using the name and the logo. This repository keeps
copies of what it needs without the internet, unchanged from brand at commit `b3f245a`.

## The copies here

| File | Copy of brand's | Used by |
| --- | --- | --- |
| [overpressure.svg](overpressure.svg), [overpressure-light.svg](overpressure-light.svg) | `logo/` | This repository's README, dark and light (a README's picture can't come from another repository), and the organization's profile |
| [overpressure-mark.svg](overpressure-mark.svg) | `logo/` | The PICO 4's boot screen, whose tests check its drawing against this one |
| [`overpressure/window/icons/`](../../overpressure/window/icons/) | `window/` | The installer's window: Tk draws no SVG and scales no image, so [logo.py](../../overpressure/window/logo.py) picks the rendering nearest to the display's scaling |
| [`app/src/assets/headsets/`](../../app/src/assets/headsets/) | `matrix/<codename>.svg`, commit `a080b96` | The app's picture of each headset: its tiles, its page, the list to add one from |

## Changing the logo

The change goes to overpressure/brand first, by pull request, with its renderings
(`python scripts/render.py` there). Then the copies here are replaced by brand's files of that commit, unchanged,
and this page names the commit.
