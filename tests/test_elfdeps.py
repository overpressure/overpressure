# SPDX-License-Identifier: Unlicense
"""overpressure.elfdeps: an image's ELF dependencies resolved inside its root, as the dynamic loader would."""

from __future__ import annotations

import io
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING

from overpressure import elfdeps

if TYPE_CHECKING:
    from collections.abc import Sequence

AARCH64, X86_64 = 183, 62


def make_elf(  # noqa: PLR0913  # one keyword per dynamic linking fact
    *,
    machine: int = AARCH64,
    soname: str = "",
    needed: Sequence[str] = (),
    rpath: str = "",
    runpath: str = "",
    interp: str = "",
    verdef: Sequence[str] = (),
    verneed: dict[str, list[tuple[str, bool]]] | None = None,
) -> bytes:
    """Build a minimal 64-bit little-endian ELF object with the given dynamic linking facts.

    verneed maps a library name to [(version, weak)], like elfdeps.Elf.verneed.
    """
    verneed = verneed or {}
    strtab = bytearray(b"\0")

    def add(text: str) -> int:
        offset = len(strtab)
        strtab.extend(text.encode() + b"\0")
        return offset

    tags = [(elfdeps.DT_NEEDED, add(name)) for name in needed]
    for tag, text in ((elfdeps.DT_SONAME, soname), (elfdeps.DT_RPATH, rpath), (elfdeps.DT_RUNPATH, runpath)):
        if text:
            tags.append((tag, add(text)))
    definitions = [(elfdeps.VER_FLG_BASE, add(soname or "base"))] + [(0, add(v)) for v in verdef]
    requirements = [(add(lib), [(add(v), weak) for v, weak in versions]) for lib, versions in verneed.items()]

    phnum = 3 if interp else 2
    layout = 64 + 56 * phnum
    interp_offset = layout
    interp_bytes = interp.encode() + b"\0" if interp else b""
    strtab_offset = interp_offset + len(interp_bytes)
    verdef_offset = strtab_offset + len(strtab)
    verdef_bytes = bytearray()
    for i, (flags, name) in enumerate(definitions):
        following = 28 if i < len(definitions) - 1 else 0
        verdef_bytes += struct.pack("<HHHHIII", 1, flags, i + 1, 1, 0, 20, following)
        verdef_bytes += struct.pack("<II", name, 0)
    verneed_offset = verdef_offset + len(verdef_bytes)
    verneed_bytes = bytearray()
    for i, (lib, versions) in enumerate(requirements):
        size = 16 + 16 * len(versions)
        following = size if i < len(requirements) - 1 else 0
        verneed_bytes += struct.pack("<HHIII", 1, len(versions), lib, 16, following)
        for j, (name, weak) in enumerate(versions):
            after = 16 if j < len(versions) - 1 else 0
            verneed_bytes += struct.pack("<IHHII", 0, elfdeps.VER_FLG_WEAK if weak else 0, 0, name, after)
    dynamic_offset = verneed_offset + len(verneed_bytes)
    tags += [
        (elfdeps.DT_STRTAB, strtab_offset),
        (10, len(strtab)),
        (elfdeps.DT_VERDEF, verdef_offset),
        (elfdeps.DT_VERDEFNUM, len(definitions)),
    ]
    if requirements:
        tags += [(elfdeps.DT_VERNEED, verneed_offset), (elfdeps.DT_VERNEEDNUM, len(requirements))]
    dynamic = b"".join(struct.pack("<qQ", tag, value) for tag, value in [*tags, (0, 0)])
    total = dynamic_offset + len(dynamic)

    header = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    header += struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, 64, 0, 0, 64, 56, phnum, 64, 0, 0)
    phdrs = struct.pack("<IIQQQQQQ", elfdeps.PT_LOAD, 5, 0, 0, 0, total, total, 0x1000)
    phdrs += struct.pack(
        "<IIQQQQQQ",
        elfdeps.PT_DYNAMIC,
        6,
        dynamic_offset,
        dynamic_offset,
        dynamic_offset,
        len(dynamic),
        len(dynamic),
        8,
    )
    if interp:
        phdrs += struct.pack(
            "<IIQQQQQQ",
            elfdeps.PT_INTERP,
            4,
            interp_offset,
            interp_offset,
            interp_offset,
            len(interp_bytes),
            len(interp_bytes),
            1,
        )
    return header + phdrs + interp_bytes + bytes(strtab) + bytes(verdef_bytes) + bytes(verneed_bytes) + dynamic


class ElfDepsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def put(self, path: str, data: bytes | str) -> None:
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())

    def check(self, path: str) -> list[str]:
        return elfdeps.Loader(str(self.root)).check(path)

    def test_parses_dynamic_section_and_versions(self):
        elf = elfdeps.parse(
            make_elf(
                soname="libfoo.so.1",
                needed=["libc.so.6"],
                runpath="$ORIGIN",
                verdef=["FOO_1"],
                verneed={"libc.so.6": [("GLIBC_2.17", False)]},
            ),
            "/usr/lib/libfoo.so.1",
        )
        assert elf is not None
        self.assertEqual(
            (elf.machine, elf.soname, elf.needed, elf.runpath), (AARCH64, "libfoo.so.1", ["libc.so.6"], ["$ORIGIN"])
        )
        self.assertEqual(elf.verdef, {"FOO_1"})
        self.assertEqual(elf.verneed, {"libc.so.6": [("GLIBC_2.17", False)]})
        self.assertIsNone(elfdeps.parse(b"#!/bin/sh\n" + bytes(64), "/usr/bin/script"))

    def test_resolves_default_directory_and_reports_missing(self):
        self.put("/usr/lib/libbar.so.1", make_elf(soname="libbar.so.1"))
        self.put("/usr/lib/libfoo.so.1", make_elf(soname="libfoo.so.1", needed=["libbar.so.1", "libgone.so.2"]))
        self.assertEqual(self.check("/usr/lib/libfoo.so.1"), ["libgone.so.2 => not found"])

    def test_missing_libraries_of_dependencies_are_reported(self):
        self.put("/usr/lib/libbar.so.1", make_elf(soname="libbar.so.1", needed=["libgone.so.2"]))
        self.put("/usr/bin/prog", make_elf(needed=["libbar.so.1"]))
        self.assertEqual(self.check("/usr/bin/prog"), ["libgone.so.2 => not found"])

    def test_runpath_expands_origin(self):
        self.put("/opt/app/lib/libapp.so", make_elf(soname="libapp.so"))
        self.put("/opt/app/bin/with", make_elf(needed=["libapp.so"], runpath="$ORIGIN/../lib"))
        self.put("/opt/app/bin/without", make_elf(needed=["libapp.so"]))
        self.assertEqual(self.check("/opt/app/bin/with"), [])
        self.assertEqual(self.check("/opt/app/bin/without"), ["libapp.so => not found"])

    def test_rpath_of_the_loading_chain_unless_runpath(self):
        self.put("/opt/a/liba.so", make_elf(soname="liba.so", needed=["libb.so"]))
        self.put("/opt/a/libb.so", make_elf(soname="libb.so"))
        self.put("/opt/a/libc2.so", make_elf(soname="libc2.so", needed=["libb.so"], runpath="/nowhere"))
        self.put("/usr/bin/one", make_elf(needed=["liba.so"], rpath="/opt/a"))
        self.put("/usr/bin/two", make_elf(needed=["libc2.so"], rpath="/opt/a"))
        self.assertEqual(self.check("/usr/bin/one"), [])
        # An object with DT_RUNPATH does not use the DT_RPATH of the objects that loaded it.
        self.assertEqual(self.check("/usr/bin/two"), ["libb.so => not found"])

    def test_missing_versions_are_reported_unless_weak(self):
        self.put("/usr/lib/libv.so.1", make_elf(soname="libv.so.1", verdef=["V_1"]))
        self.put("/usr/bin/old", make_elf(needed=["libv.so.1"], verneed={"libv.so.1": [("V_1", False)]}))
        self.put("/usr/bin/new", make_elf(needed=["libv.so.1"], verneed={"libv.so.1": [("V_2", False)]}))
        self.put("/usr/bin/weak", make_elf(needed=["libv.so.1"], verneed={"libv.so.1": [("V_2", True)]}))
        self.assertEqual(self.check("/usr/bin/old"), [])
        self.assertEqual(
            self.check("/usr/bin/new"), ["/usr/lib/libv.so.1: version `V_2' not found (required by /usr/bin/new)"]
        )
        self.assertEqual(self.check("/usr/bin/weak"), [])

    def test_other_architectures_are_skipped_like_the_loader_does(self):
        self.put("/etc/ld.so.conf", "include /etc/ld.so.conf.d/*.conf\n")
        self.put("/etc/ld.so.conf.d/x86.conf", "# a foreign library first\n/opt/x86\n")
        self.put("/opt/x86/libz.so.1", make_elf(machine=X86_64, soname="libz.so.1"))
        self.put("/usr/bin/prog", make_elf(needed=["libz.so.1"]))
        self.assertEqual(self.check("/usr/bin/prog"), ["libz.so.1 => not found"])
        self.put("/usr/lib/libz.so.1", make_elf(soname="libz.so.1"))
        self.assertEqual(self.check("/usr/bin/prog"), [])
        self.assertEqual(elfdeps.conf_dirs(str(self.root)), ["/opt/x86"])

    def test_missing_interpreter_and_relative_names(self):
        self.put("/usr/bin/prog", make_elf(interp="/lib/ld-linux-aarch64.so.1", needed=["./libsteam_api.so"]))
        self.assertEqual(
            self.check("/usr/bin/prog"), ["/lib/ld-linux-aarch64.so.1 => not found", "./libsteam_api.so => not found"]
        )

    def test_symlinks_resolve_inside_the_root(self):
        self.put("/usr/lib/libreal.so.1.2", make_elf(soname="libreal.so.1"))
        try:
            (self.root / "usr/lib/libreal.so.1").symlink_to("libreal.so.1.2")
            (self.root / "lib").symlink_to("/usr/lib")
        except OSError:
            self.skipTest("symlinks need more privileges on this host")
        self.put("/usr/bin/prog", make_elf(needed=["libreal.so.1"], rpath="/lib"))
        self.assertEqual(elfdeps.resolve(str(self.root), "/lib/libreal.so.1"), "/usr/lib/libreal.so.1.2")
        self.assertEqual(self.check("/usr/bin/prog"), [])

    def test_packages_from_the_pacman_database_and_ignored_lines(self):
        self.put("/var/lib/pacman/local/foo-1.0-1/desc", "%NAME%\nfoo\n\n%VERSION%\n1.0-1\n")
        self.put(
            "/var/lib/pacman/local/foo-1.0-1/files",
            "%FILES%\nusr/\nusr/lib/\nusr/lib/libfoo.so.1\nusr/share/doc/foo.txt\n\n%BACKUP%\n",
        )
        self.put("/usr/lib/libfoo.so.1", make_elf(soname="libfoo.so.1", needed=["libgone.so"]))
        self.put("/usr/share/doc/foo.txt", "not checked")
        self.assertEqual(
            elfdeps.problems(str(self.root), ["foo"]), ["foo /usr/lib/libfoo.so.1:\tlibgone.so => not found"]
        )
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(elfdeps.main([str(self.root), "foo"]), 1)
        self.assertIn("libgone.so", out.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(elfdeps.main([str(self.root), "foo", "--ignore", r"^foo /usr/lib/libfoo\.so\.1:"]), 0)
        with self.assertRaisesRegex(ValueError, "not installed: bar"):
            elfdeps.problems(str(self.root), ["foo", "bar"])


if __name__ == "__main__":
    unittest.main()
