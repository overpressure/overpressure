# Releasing

**TL;DR.** overpressure and each headset are released apart, each from its own repository by a pushed,
annotated tag `vMAJOR.MINOR[-beta[.N]]`: a GitHub release with the source zip and its SHA-256, nothing
uploaded by hand. An owner downloads overpressure's zip; its installer downloads the headset's release
([tool.md](tool.md#headsets-in-devices)). This page is overpressure's release; a headset's is in its
repository (the PICO 4's: [docs/releasing.md](https://github.com/overpressure/phoenix/blob/main/docs/releasing.md)).

## Versions

`MAJOR.MINOR[-beta[.N]]`, as a Git tag `vMAJOR.MINOR...`. `pyproject.toml` holds the same version in Python's
spelling (`1.0-beta` is `1.0b1`, `1.0-beta.2` is `1.0b2`). A headset's version is its own, in its
`headset.json`; the image it builds reports both (the PICO 4's `/usr/share/pico4/version`: the headset's
version and commit, and `overpressure=` this version).

A release that changes the headset interface raises `API` in
[overpressure/headset.py](../overpressure/headset.py): headset releases made for the old one are then
refused, so each headset needs a release for the new one first.

## Changelog

What changed is in each release's notes and in the `CHANGELOG.md` inside its zip, both generated from the
conventional commits by [git-cliff](https://git-cliff.org/) as [cliff.toml](../cliff.toml) sets it up: features
and fixes by their subjects, everything else as a count. What the next release brings:

```sh
uvx git-cliff@2.14.2 --unreleased
```

## A release

A pushed tag makes the release; nothing is uploaded by hand.

1. **Ready.** Everything is on `main`, CI is green (each headset's `main` too, which the `headsets` job runs
   against it), and no issue the release depends on is open.
2. **The version.** Set it in `pyproject.toml`, by pull request like every change to `main`.
3. **The headsets.** Each supported headset's current release runs its checklist with this commit (the
   PICO 4's: [release-checklist.md](https://github.com/overpressure/phoenix/blob/main/docs/release-checklist.md)).
4. **The tag.** An annotated tag on that commit of `main`: its first line the title, the rest the release's own
   words, the checklist's results and what is known not to work. Then push it.

   ```sh
   git tag -a v1.0-beta -F tag-message.md
   git push origin v1.0-beta
   ```

5. **CI** ([.github/workflows/release.yml](../.github/workflows/release.yml), on GitHub):
   - runs every check of [ci.yml](../.github/workflows/ci.yml) on the tagged commit, the private-data scan over
     the whole history among them, unless the same tree passed already (on its pull request or on `main`): CI runs
     once per content;
   - refuses a tag that is not annotated, not on `main`, or names another version than `pyproject.toml`
     (`python scripts/release.py check TAG`);
   - writes the notes: the tag's own words, then the changelog's section of this release (`git-cliff --latest`);
   - makes the zip with `git archive`: the tagged tree under `overpressure-VERSION/`, with `CHANGELOG.md`, the
     start scripts executable and `Install.cmd` with Windows line endings; and its SHA-256;
   - creates the GitHub release with the notes, the zip and its checksum, as a pre-release for a beta.

   No headset, image, package or vendor file is attached: the installer downloads the headset's release, and
   everything is built from public downloads on the owner's computer or the headset.

A failed check stops the release before anything is published. Fix it on `main`, then delete the tag
(`git push origin :refs/tags/v1.0-beta` and `git tag -d v1.0-beta`) and tag the fixed commit.
