"""Check that installed programs and libraries find their shared libraries.

The userspace root is aarch64. Running ldd in it means one QEMU process per file, so
this reads the ELF dynamic sections directly and follows the dynamic loader's search
rules: DT_RPATH of the loading chain (unless the object has DT_RUNPATH), DT_RUNPATH,
the ld.so.conf directories and the default directory. Unlike ldd it also reports
symbol versions that no loaded library defines.

    python3 -m overpressure.elfdeps ROOT PACKAGE... [--ignore REGEX]

Prints one line per problem, "PACKAGE FILE:<TAB>NEEDED => not found" or
"PACKAGE FILE:<TAB>LIBRARY: version `NAME' not found (required by OBJECT)", and exits 1
if any line does not match --ignore.
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

PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
DT_NEEDED, DT_STRTAB, DT_SONAME, DT_RPATH, DT_RUNPATH = 1, 5, 14, 15, 29
DT_VERDEF, DT_VERDEFNUM, DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFC, 0x6FFFFFFD, 0x6FFFFFFE, 0x6FFFFFFF
VER_FLG_BASE, VER_FLG_WEAK = 0x1, 0x2
DEFAULT_DIRS = ("/usr/lib", "/lib", "/usr/lib64", "/lib64")


@dataclass
class Elf:
    path: str  # real path inside the root
    elfclass: int
    machine: int
    soname: str = ""
    needed: list = field(default_factory=list)
    rpath: list = field(default_factory=list)
    runpath: list = field(default_factory=list)
    interp: str = ""
    verneed: dict = field(default_factory=dict)  # library name -> [(version, weak)]
    verdef: set = field(default_factory=set)


def parse(data, path: str) -> Elf | None:
    """The dynamic linking facts of an ELF file (None for other files)."""
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[5] != 1:
        return None
    wide = data[4] == 2
    if wide:
        _, machine, _, _, phoff, _, _, _, phentsize, phnum = struct.unpack_from("<HHIQQQIHHH", data, 16)
        ph, dyn = "<IIQQQQQQ", "<qQ"
    else:
        _, machine, _, _, phoff, _, _, _, phentsize, phnum = struct.unpack_from("<HHIIIIIHHH", data, 16)
        ph, dyn = "<IIIIIIII", "<iI"
    elf = Elf(path, data[4], machine)
    loads, dynamic = [], None
    for i in range(phnum):
        fields = struct.unpack_from(ph, data, phoff + i * phentsize)
        if wide:
            p_type, _, offset, vaddr, _, filesz = fields[:6]
        else:
            p_type, offset, vaddr, _, filesz = fields[:5]
        if p_type == PT_LOAD:
            loads.append((vaddr, offset, filesz))
        elif p_type == PT_DYNAMIC:
            dynamic = (offset, filesz)
        elif p_type == PT_INTERP:
            elf.interp = bytes(data[offset:offset + filesz]).split(b"\0", 1)[0].decode()
    if dynamic is None:
        return elf

    def at(vaddr: int) -> int:
        for start, offset, size in loads:
            if start <= vaddr < start + size:
                return offset + vaddr - start
        raise ValueError(f"{path}: address {vaddr:#x} is outside the file")

    tags = []
    step = struct.calcsize(dyn)
    for offset in range(dynamic[0], dynamic[0] + dynamic[1] - step + 1, step):
        tag, value = struct.unpack_from(dyn, data, offset)
        if tag == 0:
            break
        tags.append((tag, value))
    values = dict(tags)
    strtab = at(values[DT_STRTAB])

    def string(offset: int) -> str:
        end = data.find(b"\0", strtab + offset)
        return bytes(data[strtab + offset:end]).decode("utf-8", "replace")

    for tag, value in tags:
        if tag == DT_NEEDED:
            elf.needed.append(string(value))
        elif tag == DT_SONAME:
            elf.soname = string(value)
        elif tag == DT_RPATH:
            elf.rpath = [d for d in string(value).split(":") if d]
        elif tag == DT_RUNPATH:
            elf.runpath = [d for d in string(value).split(":") if d]
    if DT_VERNEED in values:
        offset = at(values[DT_VERNEED])
        for _ in range(values.get(DT_VERNEEDNUM, 0)):
            _, count, name, aux, following = struct.unpack_from("<HHIII", data, offset)
            versions, entry = [], offset + aux
            for _ in range(count):
                _, flags, _, version, after = struct.unpack_from("<IHHII", data, entry)
                versions.append((string(version), bool(flags & VER_FLG_WEAK)))
                entry += after
            elf.verneed[string(name)] = versions
            if not following:
                break
            offset += following
    if DT_VERDEF in values:
        offset = at(values[DT_VERDEF])
        for _ in range(values.get(DT_VERDEFNUM, 0)):
            _, flags, _, count, _, aux, following = struct.unpack_from("<HHHHIII", data, offset)
            if count and not flags & VER_FLG_BASE:
                elf.verdef.add(string(struct.unpack_from("<I", data, offset + aux)[0]))
            if not following:
                break
            offset += following
    return elf


def resolve(root: str, path: str) -> str | None:
    """The real path of PATH inside ROOT, following symlinks as a chroot would."""
    todo, done, hops = [p for p in path.split("/") if p], [], 0
    while todo:
        name = todo.pop(0)
        if name == ".":
            continue
        if name == "..":
            done = done[:-1]
            continue
        full = os.path.join(root, *done, name)
        try:
            mode = os.lstat(full).st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            hops += 1
            if hops > 40:
                return None
            target = os.readlink(full)
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
        with open(os.path.join(root, conf.lstrip("/")), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return dirs
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line.startswith("include ") and depth < 8:
            pattern = line.split(None, 1)[1]
            if not pattern.startswith("/"):
                pattern = os.path.join(os.path.dirname(conf), pattern)
            for match in sorted(glob.glob(os.path.join(root, pattern.lstrip("/")))):
                inside = "/" + os.path.relpath(match, root).replace(os.sep, "/")
                dirs += conf_dirs(root, inside, depth + 1)
        elif line.startswith("/"):
            dirs.append(line)
    return dirs


class Loader:
    """The dynamic loader's view of a root file system."""

    def __init__(self, root: str):
        self.root = root
        self.files = {}
        self.system = conf_dirs(root) + list(DEFAULT_DIRS)

    def load(self, path: str) -> Elf | None:
        real = resolve(self.root, path)
        if real is None:
            return None
        if real not in self.files:
            elf = None
            try:
                with open(os.path.join(self.root, real.lstrip("/")), "rb") as f:
                    if os.fstat(f.fileno()).st_size >= 52:
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
                            elf = parse(data, real)
            except (OSError, ValueError, struct.error, KeyError):
                elf = None
            self.files[real] = elf
        return self.files[real]

    def find(self, name: str, chain: list, like: Elf) -> Elf | None:
        """Search NAME for the last object of CHAIN (the objects that loaded it, in order)."""
        def usable(path):
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
        for directory, owner in dirs:
            if owner is not None:
                origin = os.path.dirname(owner.path)
                directory = directory.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)
            elf = usable(directory.rstrip("/") + "/" + name)
            if elf:
                return elf
        return None

    def check(self, path: str) -> list:
        """ldd-style problems of one program or library."""
        top = self.load(path)
        problems = []
        if top is None:
            return problems
        if top.interp and resolve(self.root, top.interp) is None:
            problems.append(f"{top.interp} => not found")
        loaded = [top]
        names = {top.soname or os.path.basename(top.path): top}
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
                    queue.append((dep, chain + [dep]))
        for obj in loaded:
            for library, versions in obj.verneed.items():
                dep = names.get(library)
                if dep is None or not dep.verdef:
                    continue
                for version, weak in versions:
                    if not weak and version not in dep.verdef:
                        problems.append(f"{dep.path}: version `{version}' not found (required by {obj.path})")
        return problems


def package_files(root: str) -> dict:
    """Installed package name -> its files, from pacman's local database."""
    packages = {}
    for entry in sorted(glob.glob(os.path.join(root, "var/lib/pacman/local/*/desc"))):
        with open(entry, encoding="utf-8") as f:
            desc = f.read().split("\n")
        files = []
        try:
            with open(os.path.join(os.path.dirname(entry), "files"), encoding="utf-8") as f:
                section = ""
                for line in f.read().split("\n"):
                    if line.startswith("%"):
                        section = line
                    elif section == "%FILES%" and line and not line.endswith("/"):
                        files.append("/" + line)
        except OSError:
            pass
        packages[desc[desc.index("%NAME%") + 1]] = files
    return packages


def checked(root: str, path: str) -> bool:
    """Programs, public libraries, the private driver directory and SteamVR drivers;
    libraries in other private directories are reached through their programs."""
    try:
        mode = os.lstat(os.path.join(root, path.lstrip("/"))).st_mode
    except OSError:
        return False
    if not stat.S_ISREG(mode):
        return False
    program = mode & 0o111 and ".so" not in os.path.basename(path)
    return bool(program or re.match(r"/usr/lib/[^/]+\.so", path) or path.startswith("/usr/lib/pico4/")
                or (path.startswith("/usr/share/steamvr-monado/") and path.endswith(".so")))


def problems(root: str, names: list) -> list:
    loader, files, lines = Loader(root), package_files(root), []
    missing = [n for n in names if n not in files]
    if missing:
        raise ValueError(f"not installed: {' '.join(missing)}")
    for name in names:
        for path in files[name]:
            if checked(root, path) and loader.load(path) is not None:
                lines += [f"{name} {path}:\t{p}" for p in loader.check(path)]
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m overpressure.elfdeps", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("packages", nargs="+")
    ap.add_argument("--ignore", default="", help="regular expression for accepted problem lines")
    args = ap.parse_args(argv)
    ignore = re.compile(args.ignore) if args.ignore else None
    lines = [line for line in problems(args.root, args.packages) if not (ignore and ignore.search(line))]
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
