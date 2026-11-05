# Releasing

## Versions

`MAJOR.MINOR[-beta[.N]]`, as a Git tag `vMAJOR.MINOR...`. `pyproject.toml` holds the same version in Python's
spelling (`1.0-beta` is `1.0b1`). The image reports its version and Git commit in `/usr/share/pico4/version`. A
release build's commit carries no `-dirty`.

## Changelog

The documents state what is true now; what changed is in `CHANGELOG.md` and on each release's page, generated
from the conventional commits:

```sh
python scripts/changelog.py --since v1.0-beta --version 1.0
```

## A release

1. Everything is on `main`, CI is green, and no issue the release depends on is open.
2. Set the version in `pyproject.toml`.
3. Build from a clean checkout, the whole path of the headset's README, with the package lock on.
4. Run the headset's checklist ([devices/phoenix/docs/release-checklist.md](../devices/phoenix/docs/release-checklist.md))
   on that image, and keep the results.
5. `python scripts/check-private.py --history` finds nothing.
6. Add the changelog's section, tag, push the tag and write the release's page: its changelog, the checklist's
   results and what is known not to work. No image or vendor file is attached
   ([devices/phoenix/docs/distribution.md](../devices/phoenix/docs/distribution.md)).

## Moving to new upstream versions

Valve's packages, the kernel and Steam's client hold still between releases
([devices/phoenix/docs/userspace.md](../devices/phoenix/docs/userspace.md#package-lock)). To move:

| What | Change | Then |
| --- | --- | --- |
| Holo and Frame packages, SteamVR, Mesa | `build-userspace.sh --refresh --update-lock`, review `OUTPUT_DIR/packages.lock` and copy it to `userspace/packages.lock` | the whole checklist: `driver_monado`, the patches and masks depend on SteamVR's behaviour |
| PipeWire | `pkgver` of `pico4-spa-alsa` to the new version, its patch rebased; the build stops otherwise | suspend with the microphone capturing |
| Valve's kernel | the package name and hash in `kernel/build-kernel.sh` and `scripts/build-kernel-step.sh`, the commit, the patches rebased | the whole checklist |
| PICO OS firmware | a new entry in `VERIFIED_FIRMWARES`, only after the whole path has run on it | the whole checklist |
