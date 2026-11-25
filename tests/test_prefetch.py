# SPDX-License-Identifier: Unlicense
"""overpressure.prefetch: a headset's downloads on this computer before an install writes it, against a fake server."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import unittest.mock
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from overpressure import prefetch, web

REPOSITORY, TAG, COMMIT = "overpressure/packages", "2026.11.27", "c" * 40
ROOTFS_SHA = "a" * 64  # replaced by the fake rootfs's hash
HOLO = "https://holo.invalid/holo-core-aarch64-preview"
HOST_SERVER = f"{HOLO}/mash-20251118/$repo/os/$arch"


def sha(data: bytes) -> str:
    """Return @p data's SHA-256."""
    return hashlib.sha256(data).hexdigest()


def database(packages: dict[str, dict[str, Any]]) -> bytes:
    """Return a pacman database (tar with xz) of @p packages: name -> desc fields."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:xz") as tar:
        for name, fields in packages.items():
            text = "".join(
                f"%{key}%\n" + "\n".join(value if isinstance(value, list) else [str(value)]) + "\n\n"
                for key, value in {"NAME": name, **fields}.items()
            )
            data = text.encode()
            info = tarfile.TarInfo(f"{name}-{fields['VERSION']}/desc")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def package(name: str, version: str, data: bytes, **more: Any) -> dict[str, Any]:  # noqa: ANN401  # desc fields
    """Return a package's desc fields for its file @p data."""
    return {
        "VERSION": version,
        "FILENAME": f"{name}-{version}-aarch64.pkg.tar.zst",
        "SHA256SUM": sha(data),
        "CSIZE": len(data),
        **more,
    }


class Response(io.BytesIO):
    """A fake HTTP response: the body and its headers."""

    def __init__(self, data: bytes) -> None:
        """Hold @p data."""
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class World:
    """The servers: GitHub's raw files, archive and release, Holo's rootfs and repositories, the Frame's hotfixes."""

    def __init__(self, root: Path) -> None:
        """Make every file and the pins in @p root."""
        self.files: dict[str, bytes] = {}
        self.failures: dict[str, int] = {}  # url -> how many answers fail with 503 first
        self.gets: list[str] = []
        self.rootfs = b"the rootfs"
        packages_sh = (
            f"HOLO_SNAPSHOT=mash-20251118.3\nHOLO_URL={HOLO}/$HOLO_SNAPSHOT/system.rootfs.zst\n"
            f"HOLO_SHA256={sha(self.rootfs)}\n"
        )
        raw = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}"
        self.files[f"{raw}/build/packages.sh"] = packages_sh.encode()
        self.files[f"{raw}/example/fan"] = b"#!/usr/bin/python3\n"
        self.files[f"{HOLO}/mash-20251118.3/system.rootfs.zst"] = self.rootfs
        # The image's repositories and its lock.
        self.bodies = {
            "glibc": b"glibc package",
            "alsa": b"alsa with an epoch",
            "steamvr": b"the hotfix's steamvr",
            "protobuf": b"protobuf 25",
        }
        core = {"glibc": package("glibc", "2.40-1", self.bodies["glibc"])}
        extra = {"alsa-card-profiles": package("alsa-card-profiles", "1:1.6.8-1", self.bodies["alsa"])}
        hotfixes = {
            "steamvr": package("steamvr", "2.17-1", self.bodies["steamvr"]),
            "protobuf": package("protobuf", "25.3-5", self.bodies["protobuf"]),
        }
        image = f"{HOLO}/mash-20251118.3"
        self.files[f"{image}/core/os/aarch64/core.db"] = database(core)
        self.files[f"{image}/extra/os/aarch64/extra.db"] = database(extra)
        hot = "https://hotfixes.invalid/0.3.x"
        self.files[f"{hot}/deckard-arch-hotfixes-release-0.3.db"] = database(hotfixes)
        for url, fields in (
            (f"{image}/core/os/aarch64", core["glibc"]),
            (f"{image}/extra/os/aarch64", extra["alsa-card-profiles"]),
            (hot, hotfixes["steamvr"]),
            (hot, hotfixes["protobuf"]),
        ):
            name = fields["FILENAME"]
            self.files[f"{url}/{name}"] = next(b for b in self.bodies.values() if sha(b) == fields["SHA256SUM"])
        pacman_conf = (
            "[options]\nArchitecture = aarch64\n\n[overpressure-example]\nServer = file:///var/cache/overpressure/example\n"
            f"\n[core]\nServer = {image}/$repo/os/$arch\n\n[extra]\nServer = {image}/$repo/os/$arch\n"
            f"\n[deckard-arch-hotfixes-release-0.3]\nServer = {hot}\n"
        )
        self.lock = [
            (sha(self.bodies["glibc"]), core["glibc"]["FILENAME"]),
            (sha(self.bodies["alsa"]), extra["alsa-card-profiles"]["FILENAME"]),
            (sha(self.bodies["steamvr"]), hotfixes["steamvr"]["FILENAME"]),
        ]
        self.pacman_conf = pacman_conf
        self.write_archive()
        # The release.
        self.release = {"example-session-1-1-any.pkg.tar.zst": b"session", "overpressure-example.db": b"layer db"}
        sums = "".join(f"{sha(d)}  {n}\n" for n, d in self.release.items()) + f"{sha(b'sources')}  sources.tar.gz\n"
        release = f"https://github.com/{REPOSITORY}/releases/download/{TAG}"
        self.files[f"{release}/SHA256SUMS"] = sums.encode()
        for name, data in self.release.items():
            self.files[f"{release}/{name}"] = data
        # The build host's repository: python, its dependency, a provider of a virtual one.
        self.host_bodies = {"python": b"python 3.13", "mpdecimal": b"mpdecimal", "libssl": b"openssl"}
        host_core = {
            "python": package("python", "3.13.7-1", self.host_bodies["python"], DEPENDS=["mpdecimal>=4", "libssl.so"]),
            "mpdecimal": package("mpdecimal", "4.0.1-1", self.host_bodies["mpdecimal"]),
            "openssl": package("openssl", "3.5-1", self.host_bodies["libssl"], PROVIDES=["libssl.so=3-64"]),
        }
        host = f"{HOLO}/mash-20251118"
        self.files[f"{host}/core/os/aarch64/core.db"] = database(host_core)
        self.files[f"{host}/extra/os/aarch64/extra.db"] = database({})
        for fields in host_core.values():
            self.files[f"{host}/core/os/aarch64/{fields['FILENAME']}"] = next(
                b for b in self.host_bodies.values() if sha(b) == fields["SHA256SUM"]
            )
        # The pins.
        self.pin = root / "packages.json"
        self.pin.write_text(
            json.dumps({"repository": REPOSITORY, "tag": TAG, "commit": COMMIT, "sha256sums": sha(sums.encode())})
        )
        self.build_host = root / "build-host.json"
        self.build_host.write_text(
            json.dumps(
                {
                    "rootfs": sha(self.rootfs),
                    "server": HOST_SERVER,
                    "repositories": ["core", "extra"],
                    "packages": ["python"],
                }
            )
        )

    def write_archive(self) -> None:
        """Write GitHub's archive of the pinned commit with pacman.conf and the lock."""
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as tar:
            for path, text in (
                ("headsets/example/pacman.conf", self.pacman_conf),
                ("headsets/example/packages.lock", "".join(f"{s}  {n}\n" for s, n in self.lock)),
            ):
                data = text.encode()
                info = tarfile.TarInfo(f"packages-{COMMIT}/{path}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.files[f"https://github.com/{REPOSITORY}/archive/{COMMIT}.tar.gz"] = out.getvalue()

    def open(self, request: str | urllib.request.Request, timeout: float = 60) -> Response:  # noqa: ARG002  # urlopen's
        """Answer @p request from the files: 404 for none, 503 while a failure is pending."""
        url = request if isinstance(request, str) else request.full_url
        if isinstance(request, str) or request.get_method() == "GET":
            self.gets.append(url)
        if self.failures.get(url):
            self.failures[url] -= 1
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)  # ty: ignore[invalid-argument-type]  # urllib's headers
        if url not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # ty: ignore[invalid-argument-type]  # as above
        return Response(self.files[url])


class PrefetchTests(unittest.TestCase):
    """Everything the headset's build downloads lies here first, each file checked; the next run fetches nothing."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "cache"
        self.world = World(self.root)

    def fetcher(self) -> prefetch.Prefetch:
        return prefetch.Prefetch(
            codename="example",
            extra=(("downloads/fan-{commit}", "example/fan"),),
            cache=self.cache,
            pin=self.world.pin,
            build_host=self.world.build_host,
            opener=self.world.open,
            log=lambda _line: None,
            sleep=lambda _seconds: None,
        )

    def test_every_download_lies_where_the_headsets_build_keeps_it(self):
        listed = self.fetcher().run()
        packages = prefetch.PACKAGE_CACHE
        expected = {
            f"downloads/packages-{COMMIT}.sh",
            f"downloads/fan-{COMMIT}",
            f"cache/packages/{COMMIT}.tar.gz",
            f"cache/packages-release/{TAG}/SHA256SUMS",
            f"cache/packages-release/{TAG}/example-session-1-1-any.pkg.tar.zst",
            f"cache/packages-release/{TAG}/overpressure-example.db",
            "root/packages/holo-mash-20251118.3.rootfs.zst",
            "root/downloads/databases/core.db",
            "root/downloads/databases/extra.db",
            "root/downloads/databases/deckard-arch-hotfixes-release-0.3.db",
            f"{packages}/glibc-2.40-1-aarch64.pkg.tar.zst",
            f"{packages}/alsa-card-profiles-1:1.6.8-1-aarch64.pkg.tar.zst",
            f"{packages}/steamvr-2.17-1-aarch64.pkg.tar.zst",
            f"{packages}/protobuf-25.3-5-aarch64.pkg.tar.zst",  # adb's, outside the lock
            "downloads/host/server",
            "downloads/host/sync/core.db",
            "downloads/host/sync/extra.db",
            "downloads/host/pkg/python-3.13.7-1-aarch64.pkg.tar.zst",
            "downloads/host/pkg/mpdecimal-4.0.1-1-aarch64.pkg.tar.zst",
            "downloads/host/pkg/openssl-3.5-1-aarch64.pkg.tar.zst",  # provides python's libssl.so
        }
        self.assertEqual(set(listed), expected)
        self.assertEqual(prefetch.manifest(self.cache), listed)
        # The release's sources are not the build's.
        self.assertNotIn("sources.tar.gz", " ".join(listed))
        # A package's epoch: its file keeps the name pacman gives it, here under one Windows can hold.
        self.assertEqual(
            (self.cache / packages / "alsa-card-profiles-1%3A1.6.8-1-aarch64.pkg.tar.zst").read_bytes(),
            b"alsa with an epoch",
        )
        self.assertEqual((self.cache / "downloads/host/server").read_text().strip(), HOST_SERVER)

    def test_a_second_run_downloads_nothing(self):
        self.fetcher().run()
        self.world.gets.clear()
        self.assertEqual(len(self.fetcher().run()), 20)
        self.assertEqual(self.world.gets, [])

    def test_a_file_that_is_not_the_pinned_one_stops_with_nothing_kept(self):
        name = self.world.lock[0][1]
        url = next(u for u in self.world.files if u.endswith(name))
        self.world.files[url] = b"something else"
        with self.assertRaisesRegex(web.DownloadError, "is not the pinned file"):
            self.fetcher().run()
        folder = self.cache / prefetch.PACKAGE_CACHE
        self.assertFalse((folder / name).exists())
        self.assertEqual(list(folder.glob("*.part")), [])
        self.assertEqual(prefetch.manifest(self.cache), [])  # the install sends nothing it could not check

    def test_a_server_that_fails_for_a_while_is_asked_again(self):
        url = f"{HOLO}/mash-20251118.3/system.rootfs.zst"
        self.world.failures[url] = 2
        self.fetcher().run()
        self.assertEqual((self.cache / "root/packages/holo-mash-20251118.3.rootfs.zst").read_bytes(), self.world.rootfs)

    def test_a_release_that_is_not_the_pinned_one_stops(self):
        sums = f"https://github.com/{REPOSITORY}/releases/download/{TAG}/SHA256SUMS"
        self.world.files[sums] += b"0  extra.pkg.tar.zst\n"
        with self.assertRaisesRegex(ValueError, "SHA256SUMS is not the one userspace/packages.json pins"):
            self.fetcher().run()
        self.assertFalse((self.cache / f"cache/packages-release/{TAG}/SHA256SUMS").exists())

    def test_a_locked_package_valve_no_longer_publishes_stops(self):
        self.world.lock.append(("0" * 64, "gone-1-1-aarch64.pkg.tar.zst"))
        self.world.write_archive()
        with self.assertRaisesRegex(ValueError, "gone-1-1-aarch64.pkg.tar.zst, which Valve's repositories no longer"):
            self.fetcher().run()

    def test_an_earlier_pins_files_go(self):
        old = self.cache / "cache/packages-release/2026.11.26/example-session-1-0-any.pkg.tar.zst"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old")
        (self.cache / "downloads").mkdir(parents=True)
        (self.cache / "downloads/x.part").write_bytes(b"a stopped download")
        self.fetcher().run()
        self.assertFalse(old.exists())
        self.assertFalse(old.parent.exists())
        self.assertFalse((self.cache / "downloads/x.part").exists())

    def test_the_build_hosts_repository_pinned_for_another_rootfs_is_left_to_the_headset(self):
        pin = json.loads(self.world.build_host.read_text())
        self.world.build_host.write_text(json.dumps({**pin, "rootfs": "f" * 64}))
        listed = self.fetcher().run()
        self.assertFalse([path for path in listed if path.startswith(prefetch.HOST)])


if __name__ == "__main__":
    unittest.main()
