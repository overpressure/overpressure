# Releasing

**TL;DR.** overpressure and each headset are released apart, each from its own repository by a pushed,
annotated tag that is the release's calendar version (`2026.12.0`, a beta `2026.12.0b3`): a GitHub release with
the source zip and its SHA-256, nothing uploaded by hand. An owner downloads overpressure's zip; its installer
downloads the headset's release ([tool.md](tool.md#headsets-in-devices)). This page is overpressure's release and
the [versions across the project](#versions-across-the-project); a headset's release is in its repository (the
PICO 4's: [docs/releasing.md](https://github.com/overpressure/phoenix/blob/main/docs/releasing.md)).

## Versions

Calendar versions, as Home Assistant's ([overpressure/version.py](../overpressure/version.py)):

| | Version | Example |
| --- | --- | --- |
| Stable | `YYYY.M.PATCH`: the year, the month without a leading zero, that month's releases counted from 0 | `2026.12.0`, then `2026.12.1` |
| Beta | the coming release with `bN`, from `b0` | `2026.12.0b0`, `2026.12.0b1`, then `2026.12.0` |
| `main` between releases | the next one as a development version | `2026.12.0.dev0`, `2026.12.0b2.dev0` |
| A preview (a download of `main`, a checkout) | that development version with its commit's time (UTC) as the development number | `2026.12.0.dev202612031154` |

These strings are valid Python versions as they stand: `pyproject.toml`, a headset's `headset.json`, the tags, the
logs and the command line use them (the machine spelling). The window, release titles, the README and Save logs…
show them in the display spelling: *2026.12.0*, *2026.12.0 beta 3*, *2026.12.0 preview from 3 December 2026
(4d8f977)*. `python -m overpressure --version` gives both and the commit. A beta series keeps its name until its
release, also across a month's end. The channel follows from the version: `bN` is a beta (a GitHub pre-release), a
development version a preview, anything else stable.

The releases before the switch (`v1.0-beta` to `v1.0-beta.4`) keep their tags; they sort before every calendar
version, so the catalogue and `headset update` take the calendar ones as newer.

A headset's version is its own, in its `headset.json`; the image it builds reports both (the PICO 4's
`/usr/share/pico4/version`). Which overpressure runs a headset release is its `api`, the headset interface
(`MAJOR.MINOR`, [adding-a-headset.md](adding-a-headset.md#the-headset-interface)): a release keeps every headset
release it ran before running; an addition to the interface raises its minor in `SUPPORTED`; a break adds a major
and keeps the old one as adapters; only a major change of the interface retires one (`RETIRED`, with the last
overpressure version that ran it). A headset release's notes name the oldest overpressure release that runs it.

## Versions across the project

| Kind | Repositories | Version | Consumed |
| --- | --- | --- | --- |
| Product | overpressure, each headset | `YYYY.M.PATCH[bN]`, the tag the version itself | downloaded by owners, by channel (stable, beta, preview) |
| Component | [overpressure/packages](https://github.com/overpressure/packages), [overpressure/builder](https://github.com/overpressure/builder) | `YYYY.M.PATCH`, the next free patch of the month, tagged by its release workflow | pinned: a headset pins the packages by tag and commit, overpressure the build image by tag and digest |
| Kernel | [overpressure/linux](https://github.com/overpressure/linux) | `<headset>/<kernel line>-N` (`phoenix/7.2-6`) | pinned by a headset by tag and commit |
| Interface | overpressure's headset interface | `MAJOR.MINOR` (`api` in `headset.json`) | checked by the catalogue |
| Unversioned | headset-template, brand, .github | none | by commit |

A package's own version (`pkgver`, `pkgrel` in overpressure/packages) rises whenever its contents change, which
that repository's CI enforces.

## Changelog

What changed is in each release's notes and in the `CHANGELOG.md` inside its zip, both generated from the
conventional commits by [git-cliff](https://git-cliff.org/) as [cliff.toml](../cliff.toml) sets it up: features
and fixes by their subjects, everything else as a count (`python scripts/release.py notes TAG`). The changelog
starts at the first release, whose entry is its tag's own words. A beta's notes list what changed since the
release before it; a stable release's what changed since the stable release before it (or the first release),
its betas included. What the next release brings:

```sh
uvx git-cliff@2.14.2 --unreleased
```

## A release

A pushed tag makes the release; nothing is uploaded by hand.

1. **Ready.** Everything is on `main`, CI is green (each headset's `main` and its oldest supported release too,
   which the `headsets` job runs against it), and no issue the release depends on is open.
2. **The version.** `python scripts/release.py bump beta` (or `stable`) writes the release's version into
   `pyproject.toml` and prints it: a new beta series takes this month and its first free patch, a running one its
   next `bN`, and `stable` the running series' release. That change goes to `main` by pull request, like every
   change.
3. **The headsets.** Each supported headset has a published release (a beta at least: the window offers a
   headset's newest stable release first, which falls back to its newest beta, and a headset without any
   release can only be downloaded as a preview), and that release runs its checklist with this commit (the
   PICO 4's: [release-checklist.md](https://github.com/overpressure/phoenix/blob/main/docs/release-checklist.md)).
4. **The tag.** An annotated tag on that commit of `main`, named exactly as the version: its first line the
   release's title, the rest the release's own words, the checklist's results and what is known not to work.
   Then push it.

   ```sh
   git tag -a 2026.12.0b0 -F tag-message.md
   git push origin 2026.12.0b0
   ```

5. **CI** ([.github/workflows/release.yml](../.github/workflows/release.yml), on GitHub, for tags of the form
   `YYYY.…`):
   - runs every check of [ci.yml](../.github/workflows/ci.yml) on the tagged commit, the private-data scan over
     the whole history among them, unless the same tree passed already (on its pull request or on `main`): CI runs
     once per content. The headsets' checks run again in any case, against each headset's `main` and oldest
     supported release as they are now;
   - refuses a tag that is not annotated, not on `main`, not a calendar release, or another version than
     `pyproject.toml`'s (`python scripts/release.py check TAG`);
   - writes the notes and the zip's `CHANGELOG.md` (`python scripts/release.py notes TAG`, [Changelog](#changelog)),
     and scans both for private data before anything is published;
   - makes the zip with `git archive`: the tagged tree under `overpressure-VERSION/`, with `CHANGELOG.md`, the
     start scripts executable and `Install.cmd` with Windows line endings; and its SHA-256;
   - creates the GitHub release titled by the tag's first line, with the notes, the zip and its checksum, as a
     pre-release for a beta.
   - then closes the issues the release brings: every open issue whose Stage is Awaiting release gets a comment
     linking the release and is closed as completed (`python scripts/release.py close TAG --repo OWNER/NAME`,
     `--dry-run` lists them; [CLAUDE.md](../CLAUDE.md#backlog)). A failure there leaves the release published.

   No headset, image, package or vendor file is attached: the installer downloads the headset's release, and
   everything is built from public downloads on the owner's computer or the headset.

6. **The next version.** Right after the release, `python scripts/release.py bump next` writes the next
   development version (after `2026.12.0b0`, `2026.12.0b1.dev0`; after `2026.12.0`, `2026.12.1.dev0`), by pull
   request: a preview of `main` then says so (`python -m overpressure --version`, the window) and is never taken
   for the release.

A failed check stops the release before anything is published. Fix it on `main`, then delete the tag
(`git push origin :refs/tags/2026.12.0b0` and `git tag -d 2026.12.0b0`) and tag the fixed commit. Only a tag that
never got a release is deleted: a published version never moves.
