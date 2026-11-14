# Releasing

## Versions

`MAJOR.MINOR[-beta[.N]]`, as a Git tag `vMAJOR.MINOR...`. `pyproject.toml` holds the same version in Python's
spelling (`1.0-beta` is `1.0b1`, `1.0-beta.2` is `1.0b2`). The image reports its version and Git commit in
`/usr/share/pico4/version`; a release build's commit carries no `-dirty`.

## Changelog

What changed is in each release's notes and in the `CHANGELOG.md` inside its zip, both generated from the
conventional commits by [git-cliff](https://git-cliff.org/) as [cliff.toml](../cliff.toml) sets it up: features
and fixes by their subjects, everything else as a count. What the next release brings:

```sh
uvx git-cliff@2.14.2 --unreleased
```

## A release

A pushed tag makes the release; nothing is uploaded by hand.

1. **Ready.** Everything is on `main`, CI is green, and no issue the release depends on is open.
2. **The version.** Set it in `pyproject.toml`, by pull request like every change to `main`.
3. **The image.** Build from a clean checkout, the whole path of the headset's README, with the package lock
   on, in a new build root: a second such build gives the same archive and system image
   ([devices/phoenix/docs/userspace.md](../devices/phoenix/docs/userspace.md#reproducible-builds)). Run the
   headset's checklist ([devices/phoenix/docs/release-checklist.md](../devices/phoenix/docs/release-checklist.md))
   on that image.
4. **The tag.** An annotated tag on that commit of `main`: its first line the title, the rest the release's own
   words, the checklist's results and what is known not to work. Then push it.

   ```sh
   git tag -a v1.0-beta -F tag-message.md
   git push origin v1.0-beta
   ```

5. **CI** ([.github/workflows/release.yml](../.github/workflows/release.yml), on GitHub):
   - runs every check of [ci.yml](../.github/workflows/ci.yml) on the tagged commit, the private-data scan over
     the whole history among them;
   - refuses a tag that is not annotated, not on `main`, or names another version than `pyproject.toml`
     (`python scripts/release.py check TAG`);
   - writes the notes: the tag's own words, then the changelog's section of this release (`git-cliff --latest`);
   - makes the zip with `git archive`: the tagged tree under `overpressure-VERSION/`, with `CHANGELOG.md`, the
     start scripts executable and `Install.cmd` with Windows line endings; and its SHA-256;
   - creates the GitHub release with the notes, the zip and its checksum, as a pre-release for a beta.

   No image, package or vendor file is attached
   ([devices/phoenix/docs/distribution.md](../devices/phoenix/docs/distribution.md)): everything is built on
   the owner's computer.

A failed check stops the release before anything is published. Fix it on `main`, then delete the tag
(`git push origin :refs/tags/v1.0-beta` and `git tag -d v1.0-beta`) and tag the fixed commit.

## Moving to new upstream versions

Valve's packages, the kernel and Steam's client hold still between releases
([devices/phoenix/docs/userspace.md](../devices/phoenix/docs/userspace.md#package-lock)). The weekly upgrade check
says what Valve has moved and whether the image still builds on it
([devices/phoenix/docs/development.md](../devices/phoenix/docs/development.md#upgrade-check)). To move:

| What | Change | Then |
| --- | --- | --- |
| Holo and Frame packages, SteamVR, Mesa | `build-userspace.sh --refresh --update-lock`, review `OUTPUT_DIR/packages.lock`, copy it to overpressure/packages' `headsets/phoenix/packages.lock`, tag a release there and pin it in `devices/phoenix/userspace/packages.json` | the whole checklist: `driver_monado`, the patches and masks depend on SteamVR's behaviour |
| PipeWire | in overpressure/packages: `pkgver` of `overpressure-spa-alsa` to the new version, its patch rebased (the build stops otherwise), a new tag; then `devices/phoenix/userspace/packages.json` pinned to it | suspend with the microphone capturing |
| A project package | in overpressure/packages: the change, CI green, a new tag `YYYY.MM.DD`, whose release CI builds; then `devices/phoenix/userspace/packages.json` pinned to its tag and commit | the parts of the checklist the package touches |
| Valve's kernel | in overpressure/linux: the Valve workflow's weekly pull request reviewed and merged, and those up the layers after it, a new tag `phoenix/7.2-N` released; then `devices/phoenix/kernel/release.json` pinned to it (CI downloads it against the pin, and the contracts check the names both sides share) | the whole checklist |
| PICO OS firmware | a new entry in `VERIFIED_FIRMWARES`, only after the whole path has run on it | the whole checklist |
