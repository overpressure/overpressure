# SPDX-License-Identifier: Unlicense
"""pacman's repositories on any host: their databases, what a package depends on, pacman.conf and packages.lock.

Valve's package databases are tar archives with xz, which Python reads on every host: they say which repository
holds each locked file, its SHA-256 and size, and what a package depends on (closure). A headset's prefetch takes
its downloads from them (overpressure.prefetch). Since interface 1.7.
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Download:
    """One file: where it lies in the build folder, where it comes from, and how it is checked."""

    path: str
    url: str
    sha256: str = ""  # empty: kept as downloaded (a database, a file of the pinned commit)
    size: int = 0  # for the progress; 0: unknown


@dataclass
class Database:
    """A pacman repository's database: its packages by name, what each provides, and its server."""

    name: str
    server: str
    packages: dict[str, dict[str, list[str]]]
    arch: str = "aarch64"

    @classmethod
    def parse(cls, name: str, server: str, data: bytes, arch: str = "aarch64") -> Database:
        """Read a database: a tar archive (xz, gzip, zstd aside) of NAME-VERSION/desc files."""
        packages: dict[str, dict[str, list[str]]] = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for member in tar:
                if not member.name.endswith("/desc"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                fields: dict[str, list[str]] = {}
                for block in handle.read().decode("utf-8").strip().split("\n\n"):
                    lines = block.strip().split("\n")
                    fields[lines[0].strip("%")] = lines[1:]
                packages[fields["NAME"][0]] = fields
        return cls(name, server, packages, arch)

    def by_file(self) -> dict[str, dict[str, list[str]]]:
        """Return the packages by their file name."""
        return {fields["FILENAME"][0]: fields for fields in self.packages.values()}

    def url(self, filename: str) -> str:
        """Return where the server has @p filename."""
        return f"{self.server.replace('$repo', self.name).replace('$arch', self.arch)}/{filename}"

    def download(self, fields: dict[str, list[str]], path: str) -> Download:
        """Return the download of a package of this database into @p path, checked by the database's SHA-256."""
        filename = fields["FILENAME"][0]
        return Download(
            f"{path}/{filename}", self.url(filename), fields["SHA256SUM"][0], int(fields.get("CSIZE", ["0"])[0])
        )


def bare(dependency: str) -> str:
    """Return a dependency's name without its version: "glibc>=2.40" -> "glibc"."""
    return re.split(r"[<>=]", dependency, maxsplit=1)[0]


class Packages:
    """The packages of @p databases by name or by what they provide, the first database's where several have one."""

    def __init__(self, databases: list[Database]) -> None:
        """Index @p databases."""
        self.databases = databases
        self.provides: dict[str, str] = {}
        for database in reversed(databases):
            for name, fields in database.packages.items():
                self.provides.update(dict.fromkeys((bare(p) for p in fields.get("PROVIDES", [])), name))

    def find(self, wanted: str) -> tuple[str, tuple[Database, dict[str, list[str]]] | None]:
        """Return the package that satisfies @p wanted (its name, or a provider's) and where it is."""
        for name in (wanted, self.provides.get(wanted, "")):
            for database in self.databases:
                if name and name in database.packages:
                    return name, (database, database.packages[name])
        return wanted, None


def closure(names: list[str], databases: list[Database]) -> list[tuple[Database, dict[str, list[str]]]]:
    """Return @p names and everything they depend on, from @p databases (the first that has one)."""
    found: dict[str, tuple[Database, dict[str, list[str]]]] = {}
    index = Packages(databases)
    todo = list(names)
    while todo:
        wanted = bare(todo.pop())
        name, entry = index.find(wanted)
        if entry is None:
            if wanted in names:
                msg = f"no repository has {wanted}"
                raise ValueError(msg)
            continue  # a virtual dependency nobody provides here: pacman's problem, not this list's
        if name in found:
            continue
        found[name] = entry
        todo += entry[1].get("DEPENDS", [])
    return [found[name] for name in sorted(found)]


def repositories(pacman_conf: str) -> list[tuple[str, str]]:
    """Return pacman.conf's repositories that a server outside this build serves: (name, server)."""
    found: list[tuple[str, str]] = []
    section = ""
    for raw in pacman_conf.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section != "options" and line.startswith("Server") and "=" in line:
            server = line.split("=", 1)[1].strip()
            if server.startswith(("http://", "https://")) and all(name != section for name, _ in found):
                found.append((section, server))
    return found


def lock(text: str) -> list[tuple[str, str]]:
    """Return packages.lock's entries (and SHA256SUMS'): (SHA-256, file name)."""
    return [(sum_, name) for sum_, name in (line.split() for line in text.splitlines() if line.strip())]


def latest(database: Database, name: str, version_prefix: str) -> dict[str, list[str]]:
    """Return @p database's @p name if its version starts with @p version_prefix (protobuf 25), else fail."""
    fields = database.packages.get(name)
    if fields is None or not fields["VERSION"][0].startswith(version_prefix):
        msg = f"{database.name} has no {name} {version_prefix}*"
        raise ValueError(msg)
    return fields
