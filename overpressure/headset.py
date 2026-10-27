"""What a headset gives overpressure: its identity, its commands and its workflow steps.

Each headset lives in devices/<codename>/ with its own sources, image recipes, scripts, tests
and documents. Its Python tooling, the package devices/<codename>/tooling, exports HEADSET, an
instance of a Headset subclass. The command line passes each of its commands on to the module
that implements it, and the GUI shows its steps: each step is a command line with parameters, so
everything the GUI does can be repeated in a terminal word for word.

A new headset needs: devices/<codename>/tooling/__init__.py with HEADSET, the command modules
that build its images and talk to it (any transport: fastboot, ADB, EDL, SSH, ...), and the
steps that string them together. docs/adding-a-headset.md walks through it.
"""
from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence, Tuple

from . import REPO

if TYPE_CHECKING:
    from .fastboot import RamBootTarget


@dataclass(frozen=True)
class Info:
    codename: str   #: devices/<codename>, also the --device name
    name: str       #: the product's name
    soc: str = ""
    summary: str = ""


@dataclass(frozen=True)
class Command:
    """A command of the command line: a module whose main(argv) takes the rest of the words.

    With keep, the command word itself is passed on first (one module, several commands)."""

    module: str
    help: str
    keep: bool = False


@dataclass(frozen=True)
class Param:
    """A parameter of a step: its value goes into the command line in place of {name}."""

    name: str
    label: str
    default: str = ""
    kind: str = "text"            #: text, path (a file or folder), choice or flag
    choices: Tuple[str, ...] = ()
    help: str = ""
    option: str = ""              #: a flag's word, given when the flag is on ("1")


@dataclass(frozen=True)
class Step:
    """One action of the workflow, as the GUI offers it.

    argv is the command line after "overpressure --device CODENAME" (a command of the
    headset), or a program relative to the headset's folder when script is set. A word that is
    a parameter in braces takes its value (dropped when empty; a flag gives its option when on),
    and braces inside a word are filled in the same way."""

    key: str
    title: str
    group: str                    #: "Setup", "Image" or "Headset"
    argv: Tuple[str, ...]
    help: str = ""
    params: Tuple[Param, ...] = ()
    script: bool = False          #: argv[0] is a program in the headset's folder
    linux_root: bool = False      #: needs Linux as root (on Windows: WSL)
    writes: bool = False          #: writes to the headset: the GUI asks first

    def command(self, values: Dict[str, str]) -> List[str]:
        """The command line with @p values in place of the parameters."""
        by_name = {p.name: p for p in self.params}
        words = []
        for word in self.argv:
            name = word[1:-1] if word.startswith("{") and word.endswith("}") else None
            if name in by_name:
                param, value = by_name[name], values.get(name, by_name[name].default)
                if param.kind == "flag":
                    if value == "1":
                        words.append(param.option)
                    continue
                if value == "":
                    continue
                words.append(str(value))
            else:
                words.append(word.format(**{k: values.get(k, by_name[k].default) for k in by_name})
                             if "{" in word else word)
        return words


def tools_step() -> Step:
    """The window's step for overpressure's tool fetcher."""
    return Step("tools", "Fetch the host tools", "Setup", ("tools",),
                "Downloads the pinned platform-tools (adb, fastboot) and edl-ng, and on Windows the NCM "
                "venv (pyusb, libusb-package), each checked by SHA-256, into cache/tools. Settings that "
                "name your own copies still take precedence.")


def ram_boot_step(image: str, help: str = "") -> Step:
    """The window's step for overpressure's RAM boot, with the headset's default image."""
    return Step("ram-boot", "RAM boot (writes nothing)", "Headset", ("ram-boot", "{image}"),
                help or "fastboot boot: the image runs from RAM and the next normal boot starts what the "
                        "partitions hold. A bootloader without fastboot boot refuses it.",
                (Param("image", "Boot image", image, "path"),))


class Headset(ABC):
    """A supported headset."""

    info: Info

    @property
    def root(self) -> Path:
        """Its folder, devices/<codename>."""
        return REPO / "devices" / self.info.codename

    @abstractmethod
    def commands(self) -> Dict[str, Command]:
        """Its command line: command word -> module of its tooling package."""

    def steps(self) -> Sequence[Step]:
        """Its workflow for the GUI, in order: building its images, then working on it."""
        return ()

    def dependencies(self) -> Sequence[Callable]:
        """Its checks for python -m overpressure doctor: callables that take an
        overpressure.doctor.Host and return overpressure.doctor.Check objects."""
        return ()

    def ram_boot(self) -> Optional["RamBootTarget"]:
        """How it boots an image from RAM (python -m overpressure ram-boot), or None."""
        return None

    def main(self, argv: List[str]) -> int:
        """Runs a command of its command line (argv[0] is the command word)."""
        cmd = self.commands()[argv[0]]
        module = importlib.import_module(f"devices.{self.info.codename}.tooling.{cmd.module}")
        entry: Callable[[List[str]], Optional[int]] = module.main
        return entry(argv if cmd.keep else argv[1:]) or 0


@dataclass
class Registry:
    """The headsets in devices/, by codename."""

    headsets: Dict[str, Headset] = field(default_factory=dict)

    def get(self, codename: str) -> Headset:
        try:
            return self.headsets[codename]
        except KeyError:
            known = ", ".join(sorted(self.headsets)) or "none"
            raise KeyError(f"no headset {codename!r} in devices/ (known: {known})") from None


def discover(root: Path = REPO) -> Registry:
    """Every devices/<codename>/tooling package that exports HEADSET."""
    registry = Registry()
    for folder in sorted((root / "devices").glob("*/tooling/__init__.py")):
        codename = folder.parent.parent.name
        module = importlib.import_module(f"devices.{codename}.tooling")
        headset = getattr(module, "HEADSET", None)
        if isinstance(headset, Headset):
            if headset.info.codename != codename:
                raise ValueError(f"devices/{codename} declares codename {headset.info.codename!r}")
            registry.headsets[codename] = headset
    return registry
