# SPDX-License-Identifier: Unlicense
"""Check that installed programs and libraries find their shared libraries.

The userspace root is aarch64. Running ldd in it means one QEMU process per file, so
this reads the ELF dynamic sections directly and follows the dynamic loader's search
rules: DT_RPATH of the loading chain (unless the object has DT_RUNPATH), DT_RUNPATH,
the ld.so.conf directories and the default directory. Unlike ldd it also reports
symbol versions that no loaded library defines, and, as ldd -r does, symbols that no
loaded object defines: a library built against a newer version of another can call a
function the installed one lacks, which only fails when the program starts or calls it.

    python3 -m overpressure.elfdeps ROOT PACKAGE... [--dependents] [--ignore REGEX]

--dependents also checks the packages that depend on PACKAGE..., which load their
libraries. Prints one line per problem, "PACKAGE FILE:<TAB>NEEDED => not found",
"PACKAGE FILE:<TAB>LIBRARY: version `NAME' not found (required by OBJECT)" or
"PACKAGE FILE:<TAB>undefined symbol: NAME (required by OBJECT)", and exits 1 if any line
does not match --ignore.
"""

from __future__ import annotations

import argparse
import glob
import mmap
import os
import re
import stat
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB, DT_SONAME, DT_RPATH, DT_RUNPATH = 1, 4, 5, 6, 14, 15, 29
DT_GNU_HASH = 0x6FFFFEF5
DT_VERDEF, DT_VERDEFNUM, DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFC, 0x6FFFFFFD, 0x6FFFFFFE, 0x6FFFFFFF
VER_FLG_BASE, VER_FLG_WEAK = 0x1, 0x2
STB_GLOBAL, STB_WEAK, STB_GNU_UNIQUE = 1, 2, 10
SHN_UNDEF = 0
SHT_DYNSYM = 11
DEFAULT_DIRS = ("/usr/lib", "/lib", "/usr/lib64", "/lib64")
ELF_HEADER_MIN = 52  # an ELF32 header: a shorter file is no ELF file
ELFCLASS64 = 2
MAX_SYMLINKS = 40  # as Linux follows at most 40 symbolic links in one lookup
MAX_INCLUDE_DEPTH = 8  # ld.so.conf includes, against include loops


@dataclass
class Elf:
    """What the dynamic loader needs to know of one ELF file."""

    path: str  # real path inside the root
    elfclass: int
    machine: int
    soname: str = ""
    needed: list[str] = field(default_factory=list)
    rpath: list[str] = field(default_factory=list)
    runpath: list[str] = field(default_factory=list)
    interp: str = ""
    verneed: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)  # library name -> [(version, weak)]
    verdef: set[str] = field(default_factory=set)
    defined: frozenset[str] = frozenset()  # the dynamic symbols it exports
    undefined: frozenset[str] = frozenset()  # the global symbols another object must define (weak ones may stay)


@dataclass
class _Image:
    """An ELF file's bytes and loadable segments: where an address and a string are in the file."""

    data: bytes | mmap.mmap
    path: str
    loads: list[tuple[int, int, int]]  # (address, file offset, size)
    strtab: int = 0

    def at(self, vaddr: int) -> int:
        for start, offset, size in self.loads:
            if start <= vaddr < start + size:
                return offset + vaddr - start
        msg = f"{self.path}: address {vaddr:#x} is outside the file"
        raise ValueError(msg)

    def string(self, offset: int) -> str:
        end = self.data.find(b"\0", self.strtab + offset)
        return bytes(self.data[self.strtab + offset : end]).decode("utf-8", "replace")


def _segments(data: bytes | mmap.mmap, elf: Elf, *, wide: bool) -> tuple[_Image, tuple[int, int] | None]:
    """Return the loadable segments and the dynamic section's place; note the interpreter in ELF."""
    if wide:
        phoff, _, _, _, phentsize, phnum = struct.unpack_from("<QQIHHH", data, 32)
    else:
        phoff, _, _, _, phentsize, phnum = struct.unpack_from("<IIIHHH", data, 28)
    image, dynamic = _Image(data, elf.path, []), None
    for i in range(phnum):
        fields = struct.unpack_from("<IIQQQQQQ" if wide else "<IIIIIIII", data, phoff + i * phentsize)
        if wide:
            p_type, _, offset, vaddr, _, filesz = fields[:6]
        else:
            p_type, offset, vaddr, _, filesz = fields[:5]
        if p_type == PT_LOAD:
            image.loads.append((vaddr, offset, filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (offset, filesz)
        elif p_type == PT_INTERP:
            elf.interp = bytes(data[offset : offset + filesz]).split(b"\0", 1)[0].decode()
    return image, dynamic


def _versions_needed(image: _Image, offset: int, count: int) -> dict[str, list[tuple[str, bool]]]:
    """Return the symbol versions required of each library: [(version, weak)]."""
    verneed = {}
    for _ in range(count):
        _, entries, name, aux, following = struct.unpack_from("<HHIII", image.data, offset)
        versions, entry = [], offset + aux
        for _ in range(entries):
            _, flags, _, version, after = struct.unpack_from("<IHHII", image.data, entry)
            versions.append((image.string(version), bool(flags & VER_FLG_WEAK)))
            entry += after
        verneed[image.string(name)] = versions
        if not following:
            break
        offset += following
    return verneed


def _versions_defined(image: _Image, offset: int, count: int) -> set[str]:
    """Return the symbol versions an object defines, without its base version."""
    verdef = set()
    for _ in range(count):
        _, flags, _, entries, _, aux, following = struct.unpack_from("<HHHHIII", image.data, offset)
        if entries and not flags & VER_FLG_BASE:
            verdef.add(image.string(struct.unpack_from("<I", image.data, offset + aux)[0]))
        if not following:
            break
        offset += following
    return verdef


def _dynsym_count(data: bytes | mmap.mmap, *, wide: bool) -> int | None:
    """Return the number of dynamic symbols from the .dynsym section header, None without one."""
    if wide:
        shoff, shentsize, shnum = struct.unpack_from("<Q", data, 40)[0], *struct.unpack_from("<HH", data, 58)
    else:
        shoff, shentsize, shnum = struct.unpack_from("<I", data, 32)[0], *struct.unpack_from("<HH", data, 46)
    if not shoff or shoff + shnum * shentsize > len(data):
        return None
    for i in range(shnum):
        header = shoff + i * shentsize
        if wide:
            _, sh_type, _, _, _, size, _, _, _, entsize = struct.unpack_from("<IIQQQQIIQQ", data, header)
        else:
            _, sh_type, _, _, _, size, _, _, _, entsize = struct.unpack_from("<IIIIIIIIII", data, header)
        if sh_type == SHT_DYNSYM and entsize:
            return size // entsize
    return None


def _gnu_hash_count(data: bytes | mmap.mmap, offset: int, *, wide: bool) -> int | None:
    """Return the number of dynamic symbols from a DT_GNU_HASH table: past the last chain's end.

    None when the table hashes no symbol: then it does not tell how many there are.
    """
    nbuckets, symoffset, bloom_size, _ = struct.unpack_from("<IIII", data, offset)
    buckets = offset + 16 + bloom_size * (8 if wide else 4)
    last = max(struct.unpack_from(f"<{nbuckets}I", data, buckets), default=0)
    if last < symoffset:
        return None
    chains = buckets + 4 * nbuckets
    while not struct.unpack_from("<I", data, chains + 4 * (last - symoffset))[0] & 1:
        last += 1
    return last + 1


def _symbols(image: _Image, offset: int, count: int, *, wide: bool) -> tuple[frozenset[str], frozenset[str]]:
    """Return the dynamic symbols an object defines and the global ones it needs defined."""
    layout = "<IBBHQQ" if wide else "<IIIBBH"
    size = struct.calcsize(layout)
    defined, undefined = set(), set()
    for i in range(1, count):  # symbol 0 is the null symbol
        fields = struct.unpack_from(layout, image.data, offset + i * size)
        name, info, shndx = (fields[0], fields[1], fields[3]) if wide else (fields[0], fields[3], fields[5])
        bind = info >> 4
        if shndx == SHN_UNDEF:
            if bind == STB_GLOBAL and name:
                undefined.add(image.string(name))
        elif bind in (STB_GLOBAL, STB_WEAK, STB_GNU_UNIQUE):
            defined.add(image.string(name))
    return frozenset(defined), frozenset(undefined)


def _dynamic_tags(data: bytes | mmap.mmap, dynamic: tuple[int, int], *, wide: bool) -> list[tuple[int, int]]:
    """Return the dynamic section's (tag, value) entries up to its DT_NULL."""
    tags = []
    dyn = "<qQ" if wide else "<iI"
    step = struct.calcsize(dyn)
    for offset in range(dynamic[0], dynamic[0] + dynamic[1] - step + 1, step):
        tag, value = struct.unpack_from(dyn, data, offset)
        if tag == 0:
            break
        tags.append((tag, value))
    return tags


def parse(data: bytes | mmap.mmap, path: str) -> Elf | None:
    """Return the dynamic linking facts of an ELF file (None for other files)."""
    if len(data) < ELF_HEADER_MIN or data[:4] != b"\x7fELF" or data[5] != 1:
        return None
    wide = data[4] == ELFCLASS64
    elf = Elf(path, data[4], struct.unpack_from("<H", data, 18)[0])
    image, dynamic = _segments(data, elf, wide=wide)
    if dynamic is None:
        return elf
    tags = _dynamic_tags(data, dynamic, wide=wide)
    values = dict(tags)
    image.strtab = image.at(values[DT_STRTAB])
    for tag, value in tags:
        if tag == DT_NEEDED:
            elf.needed.append(image.string(value))
        elif tag == DT_SONAME:
            elf.soname = image.string(value)
        elif tag == DT_RPATH:
            elf.rpath = [d for d in image.string(value).split(":") if d]
        elif tag == DT_RUNPATH:
            elf.runpath = [d for d in image.string(value).split(":") if d]
    _read_symbols(elf, image, values, wide=wide)
    return elf


def _read_symbols(elf: Elf, image: _Image, values: dict[int, int], *, wide: bool) -> None:
    """Note an object's symbol versions and dynamic symbols in ELF."""
    if DT_VERNEED in values:
        elf.verneed = _versions_needed(image, image.at(values[DT_VERNEED]), values.get(DT_VERNEEDNUM, 0))
    if DT_VERDEF in values:
        elf.verdef = _versions_defined(image, image.at(values[DT_VERDEF]), values.get(DT_VERDEFNUM, 0))
    if DT_SYMTAB in values:
        count = _symbol_count(image, values, wide=wide)
        elf.defined, elf.undefined = _symbols(image, image.at(values[DT_SYMTAB]), count, wide=wide)


def _symbol_count(image: _Image, values: dict[int, int], *, wide: bool) -> int:
    """Return the number of dynamic symbols, which the dynamic section does not hold.

    The section header has it, else the hash tables, else the string table that the linkers put right
    after the symbol table.
    """
    count = _dynsym_count(image.data, wide=wide)
    if count is None and DT_HASH in values:
        count = struct.unpack_from("<II", image.data, image.at(values[DT_HASH]))[1]
    if count is None and DT_GNU_HASH in values:
        count = _gnu_hash_count(image.data, image.at(values[DT_GNU_HASH]), wide=wide)
    if count is None and values[DT_STRTAB] > values[DT_SYMTAB]:
        count = (values[DT_STRTAB] - values[DT_SYMTAB]) // (24 if wide else 16)
    return count or 0


def resolve(root: str, path: str) -> str | None:
    """Return the real path of PATH inside ROOT, following symlinks as a chroot would."""
    todo, done, hops = [p for p in path.split("/") if p], [], 0
    while todo:
        name = todo.pop(0)
        if name == ".":
            continue
        if name == "..":
            done = done[:-1]
            continue
        full = Path(root, *done, name)
        try:
            mode = full.lstat().st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            hops += 1
            if hops > MAX_SYMLINKS:
                return None
            target = full.readlink().as_posix()
            if target.startswith("/"):
                done = []
            todo = [p for p in target.split("/") if p] + todo
        else:
            done.append(name)
    return "/" + "/".join(done)


def conf_dirs(root: str, conf: str = "/etc/ld.so.conf", depth: int = 0) -> list:
    """Directories listed in ld.so.conf and the files it includes."""
    dirs = []
    try:
        lines = Path(root, conf.lstrip("/")).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return dirs
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if line.startswith("include ") and depth < MAX_INCLUDE_DEPTH:
            pattern = line.split(None, 1)[1]
            if not pattern.startswith("/"):
                pattern = str(PurePosixPath(conf).parent / pattern)
            # glob(3) as ldconfig's: a wildcard skips hidden files, unlike Path.glob.
            for match in sorted(glob.glob(str(Path(root, pattern.lstrip("/"))))):  # noqa: PTH207  # glob(3) semantics
                inside = "/" + os.path.relpath(match, root).replace(os.sep, "/")
                dirs += conf_dirs(root, inside, depth + 1)
        elif line.startswith("/"):
            dirs.append(line)
    return dirs


class Loader:
    """The dynamic loader's view of a root file system."""

    def __init__(self, root: str) -> None:
        """Read the root's ld.so.conf; files are read when first needed."""
        self.root = root
        self.files: dict[str, Elf | None] = {}
        self.system = conf_dirs(root) + list(DEFAULT_DIRS)

    def load(self, path: str) -> Elf | None:
        """Return the ELF file at PATH inside the root, None for a missing or other file."""
        real = resolve(self.root, path)
        if real is None:
            return None
        if real not in self.files:
            elf = None
            try:
                with Path(self.root, real.lstrip("/")).open("rb") as f:
                    if os.fstat(f.fileno()).st_size >= ELF_HEADER_MIN:
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
                            elf = parse(data, real)
            except (OSError, ValueError, struct.error, KeyError):
                elf = None
            self.files[real] = elf
        return self.files[real]

    def find(self, name: str, chain: list[Elf], like: Elf) -> Elf | None:
        """Search NAME for the last object of CHAIN (the objects that loaded it, in order)."""

        def usable(path: str) -> Elf | None:
            elf = self.load(path)
            return elf if elf and (elf.elfclass, elf.machine) == (like.elfclass, like.machine) else None

        if "/" in name:
            # Relative names are relative to the working directory, / for these checks.
            return usable(name if name.startswith("/") else "/" + name)
        requester = chain[-1]
        dirs = []
        if not requester.runpath:
            for obj in reversed(chain):
                dirs += [(d, obj) for d in obj.rpath]
        dirs += [(d, requester) for d in requester.runpath]
        dirs += [(d, None) for d in self.system]
        for entry, owner in dirs:
            directory = entry
            if owner is not None:
                origin = str(PurePosixPath(owner.path).parent)
                directory = entry.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)
            elf = usable(directory.rstrip("/") + "/" + name)
            if elf:
                return elf
        return None

    def check(self, path: str) -> list[str]:
        """Return the ldd-style problems of one program or library."""
        top = self.load(path)
        problems: list[str] = []
        if top is None:
            return problems
        interp = None
        if top.interp:
            interp = self.load(top.interp)
            if resolve(self.root, top.interp) is None:
                problems.append(f"{top.interp} => not found")
        loaded, names = self._load_all(top, problems)
        for obj in loaded:
            for library, versions in obj.verneed.items():
                dep = names.get(library)
                if dep is None or not dep.verdef:
                    continue
                problems += [
                    f"{dep.path}: version `{version}' not found (required by {obj.path})"
                    for version, weak in versions
                    if not weak and version not in dep.verdef
                ]
        if top.interp and not any(p.endswith(" => not found") for p in problems):
            # A program's symbols resolve in its whole load set, the interpreter's included. A library's
            # can also come from whichever program loads it, so libraries are checked through programs.
            scope = [*loaded, interp] if interp else loaded
            for obj in loaded:
                missing = sorted(s for s in obj.undefined if not any(s in o.defined for o in scope))
                problems += [f"undefined symbol: {s} (required by {obj.path})" for s in missing]
        return problems

    def _load_all(self, top: Elf, problems: list[str]) -> tuple[list[Elf], dict[str, Elf | None]]:
        """Load TOP's libraries breadth first as the loader does; return them and the objects by name."""
        loaded = [top]
        names: dict[str, Elf | None] = {top.soname or PurePosixPath(top.path).name: top}
        queue = [(top, [top])]
        while queue:
            obj, chain = queue.pop(0)
            for name in obj.needed:
                if name in names:
                    continue
                dep = self.find(name, chain, top)
                names[name] = dep
                if dep is None:
                    problems.append(f"{name} => not found")
                    continue
                if dep.soname:
                    names.setdefault(dep.soname, dep)
                if dep not in loaded:
                    loaded.append(dep)
                    queue.append((dep, [*chain, dep]))
        return loaded, names


def package_files(root: str) -> dict[str, list[str]]:
    """Return each installed package's files, from pacman's local database."""
    packages = {}
    for entry in sorted(Path(root).glob("var/lib/pacman/local/*/desc")):
        desc = entry.read_text(encoding="utf-8").split("\n")
        files = []
        try:
            lines = (entry.parent / "files").read_text(encoding="utf-8").split("\n")
        except OSError:
            lines = []
        section = ""
        for line in lines:
            if line.startswith("%"):
                section = line
            elif section == "%FILES%" and line and not line.endswith("/"):
                files.append("/" + line)
        packages[desc[desc.index("%NAME%") + 1]] = files
    return packages


def _desc_fields(text: str) -> dict[str, list[str]]:
    """Return the sections of a pacman desc file: %NAME%, %DEPENDS%, %PROVIDES%, ..."""
    fields = {}
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        if lines and lines[0].startswith("%"):
            fields[lines[0]] = lines[1:]
    return fields


def dependents(root: str, names: list[str]) -> list[str]:
    """Return the installed packages other than NAMES that depend on one of NAMES (by name or provision)."""
    provider, depends = {}, {}
    for entry in sorted(Path(root).glob("var/lib/pacman/local/*/desc")):
        fields = _desc_fields(entry.read_text(encoding="utf-8"))
        name = fields["%NAME%"][0]
        for provided in [name, *fields.get("%PROVIDES%", [])]:
            provider.setdefault(re.split(r"[<>=]", provided, maxsplit=1)[0], name)
        depends[name] = [re.split(r"[<>=]", d, maxsplit=1)[0] for d in fields.get("%DEPENDS%", [])]
    wanted = set(names)
    return [n for n, deps in depends.items() if n not in wanted and any(provider.get(d) in wanted for d in deps)]


def checked(root: str, path: str) -> bool:
    """Tell whether PATH is checked: programs, public libraries and SteamVR drivers.

    Libraries in private directories are reached through their programs.
    """
    try:
        mode = Path(root, path.lstrip("/")).lstat().st_mode
    except OSError:
        return False
    if not stat.S_ISREG(mode):
        return False
    program = mode & 0o111 and ".so" not in PurePosixPath(path).name
    return bool(
        program
        or re.match(r"/usr/lib/[^/]+\.so", path)
        or (path.startswith("/usr/share/steamvr-monado/") and path.endswith(".so"))
    )


def problems(root: str, names: list[str]) -> list[str]:
    """Return the problems of the files of packages NAMES, one line each."""
    loader, files, lines = Loader(root), package_files(root), []
    missing = [n for n in names if n not in files]
    if missing:
        msg = f"not installed: {' '.join(missing)}"
        raise ValueError(msg)
    for name in names:
        for path in files[name]:
            if checked(root, path) and loader.load(path) is not None:
                lines += [f"{name} {path}:\t{p}" for p in loader.check(path)]
    return lines


def main(argv: list[str] | None = None) -> int:
    """Print the problems; exit 1 if one is not ignored."""
    ap = argparse.ArgumentParser(
        prog="python3 -m overpressure.elfdeps",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("root")
    ap.add_argument("packages", nargs="+")
    ap.add_argument("--dependents", action="store_true", help="also check the packages that depend on these")
    ap.add_argument("--ignore", default="", help="regular expression for accepted problem lines")
    args = ap.parse_args(argv)
    ignore: re.Pattern[str] | None = re.compile(args.ignore) if args.ignore else None
    names = list(args.packages)
    if args.dependents:
        names += dependents(args.root, names)
    lines = [line for line in problems(args.root, names) if not (ignore and ignore.search(line))]
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
