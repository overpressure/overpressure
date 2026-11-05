# License

The PKGBUILDs are the project's own, under the Unlicense at the repository's root; a recipe's
`license=` names what the package it builds is under. What a recipe's folder holds beside its
PKGBUILD changes or derives from its upstream, and is under that upstream's license:

| Recipe | Upstream | License | Files |
| --- | --- | --- | --- |
| `pico4-basalt` | Basalt | `BSD-3-Clause` | the patches; `pico4-vio.json` (Basalt's defaults with the PICO 4's changes) |
| `pico4-gamescope` | gamescope | `BSD-2-Clause` | the patch |
| `pico4-monado` | Monado | `BSL-1.0` | the patches |
| `pico4-sensors` | hexagonrpc | `GPL-3.0-or-later` | the patches |
| `pico4-spa-alsa` | PipeWire | `MIT` | the patch |

`pico4-monado`'s JSON files are SteamVR bindings the project wrote. The list of upstreams and their
pinned versions is in [THIRD-PARTY.md](../../../../THIRD-PARTY.md).
