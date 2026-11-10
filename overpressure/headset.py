# SPDX-License-Identifier: Unlicense
"""What a headset gives overpressure: its identity, its commands and its workflow steps.

Each headset lives in devices/<codename>/ with its own sources, image recipes, scripts, tests
and documents. Its Python tooling, the package devices/<codename>/tooling, exports HEADSET, an
instance of a Headset subclass. The command line passes each of its commands on to the module
that implements it, and the window shows its steps: each step is a command line with parameters, so
everything the window does can be repeated in a terminal word for word. A headset may also give an
installer's plan (install_plan: stages of tasks built on those commands, overpressure/install.py)
and what the window's finished page says and offers after it (after_install); with more than the
install (workflows and plan: an upgrade, the way back), the window asks which right after the
headset. backup_set and adopt_backup let the installer export and import its backup.

A new headset needs: devices/<codename>/tooling/__init__.py with HEADSET, the command modules
that build its images and talk to it (any transport: fastboot, ADB, EDL, SSH, ...), and the
steps that string them together. docs/adding-a-headset.md walks through it.
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from . import REPO

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .fastboot import RamBootTarget
    from .install import Stage


@dataclass(frozen=True)
class Info:
    """What names and describes a headset."""

    codename: str  #: devices/<codename>, also the --device name
    name: str  #: the product's name
    soc: str = ""
    summary: str = ""


@dataclass(frozen=True)
class Command:
    """A command of the command line: a module whose main(argv) takes the rest of the words.

    With keep, the command word itself is passed on first (one module, several commands).
    """

    module: str
    help: str
    keep: bool = False


@dataclass(frozen=True)
class Param:
    """A parameter of a step: its value goes into the command line in place of {name}."""

    name: str
    label: str
    default: str = ""
    kind: str = "text"  #: text, path (a file or folder), choice or flag
    choices: tuple[str, ...] = ()
    help: str = ""
    option: str = ""  #: a flag's word, given when the flag is on ("1")


@dataclass(frozen=True)
class Step:
    """One action of the workflow, as the window offers it.

    argv is the command line after "overpressure --device CODENAME" (a command of the
    headset), or a program relative to the headset's folder when script is set. A word that is
    a parameter in braces takes its value (dropped when empty; a flag gives its option when on),
    and braces inside a word are filled in the same way.
    """

    key: str
    title: str
    group: str  #: "Setup", "Image" or "Headset"
    argv: tuple[str, ...]
    help: str = ""
    params: tuple[Param, ...] = ()
    script: bool = False  #: argv[0] is a program in the headset's folder
    linux_root: bool = False  #: runs as root in the build container (overpressure/linux.py)
    writes: bool = False  #: writes to the headset: the window asks first

    def command(self, values: dict[str, str]) -> list[str]:
        """Return the command line with @p values in place of the parameters."""
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
                words.append(
                    word.format(**{k: values.get(k, by_name[k].default) for k in by_name}) if "{" in word else word
                )
        return words


#: The installer's workflow every headset with an installer has; its state stays where it always was.
INSTALL = "install"


@dataclass(frozen=True)
class Workflow:
    """Something the installer does with a headset, chosen right after the headset: install, upgrade, restore.

    Each has its own plan of stages (Headset.plan) and its own resumable state; the window offers
    them in this order, each with its summary.
    """

    key: str  #: the plan's name: install --workflow KEY
    title: str  #: the window's card and the stepper's caption
    summary: str  #: what it does and what it keeps or erases, in one or two sentences
    erases: bool = False  #: it erases the headset's data


@dataclass(frozen=True)
class Action:
    """A choice on the installer's finished page: another workflow, or Advanced mode at one of the headset's steps."""

    label: str
    step: str = ""  #: the key of the step Advanced mode opens at
    workflow: str = ""  #: the workflow it starts instead (its start page)


@dataclass(frozen=True)
class BackupSet:
    """A headset's backup on this computer: what an export carries and where an import puts it.

    The folder is a verified backup (manifest.json, overpressure/backup.py) whose manifest names the
    headset's identity; files are the other files the way back needs on another computer, by their
    name in the archive; settings are the .env values it needs (the headset's serials).
    """

    folder: Path
    identity: Path
    files: dict[str, Path] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AfterInstall:
    """What the installer's finished page says and offers: the generic window knows no headset's OS or store."""

    title: str = "Installed"
    text: str = "Your headset is ready."
    actions: tuple[Action, ...] = ()


def tools_step() -> Step:
    """Return the window's step for overpressure's tool fetcher."""
    return Step(
        "tools",
        "Fetch the host tools",
        "Setup",
        ("tools",),
        "Downloads the pinned platform-tools (adb, fastboot) and edl-ng, each checked by SHA-256, "
        "into cache/tools. Settings that name your own copies still take precedence.",
    )


def ram_boot_step(image: str, help_text: str = "") -> Step:
    """Return the window's step for overpressure's RAM boot, with the headset's default image."""
    return Step(
        "ram-boot",
        "RAM boot (writes nothing)",
        "Headset",
        ("ram-boot", "{image}"),
        help_text
        or "fastboot boot: the image runs from RAM and the next normal boot starts what the "
        "partitions hold. A bootloader without fastboot boot refuses it.",
        (Param("image", "Boot image", image, "path"),),
    )


class Headset(ABC):
    """A supported headset."""

    info: Info

    @property
    def root(self) -> Path:
        """Its folder, devices/<codename>."""
        return REPO / "devices" / self.info.codename

    @abstractmethod
    def commands(self) -> dict[str, Command]:
        """Its command line: command word -> module of its tooling package."""

    def steps(self) -> Sequence[Step]:
        """Its workflow for the window, in order: building its images, then working on it."""
        return ()

    def dependencies(self) -> Sequence[Callable]:
        """Return its checks for python -m overpressure doctor.

        They are callables that take an overpressure.doctor.Host and return overpressure.doctor.Check
        objects.
        """
        return ()

    def ram_boot(self) -> RamBootTarget | None:
        """How it boots an image from RAM (python -m overpressure ram-boot), or None."""
        return None

    def install_plan(self) -> Sequence[Stage]:
        """Return its installer's stages (python -m overpressure install); empty: it has no installer."""
        return ()

    def workflows(self) -> Sequence[Workflow]:
        """Return what its installer does, in the window's order: the install alone unless it has more."""
        if not tuple(self.install_plan()):
            return ()
        return (Workflow(INSTALL, "Install", f"Installs overpressure's system on the {self.info.name}."),)

    def plan(self, workflow: str) -> Sequence[Stage]:
        """Return the stages of WORKFLOW (python -m overpressure install --workflow WORKFLOW)."""
        return self.install_plan() if workflow == INSTALL else ()

    def workflow_problem(self, workflow: str) -> str:  # noqa: ARG002  # a headset's own rule may use it
        """Return why WORKFLOW cannot run now, in plain words, or "" when it can."""
        return ""

    def after_install(self) -> AfterInstall:
        """Return what the window's finished page says and offers once its installer has run."""
        return AfterInstall()

    def finished_page(self, workflow: str) -> AfterInstall:
        """Return what the window's finished page says and offers once WORKFLOW has run (the install: after_install)."""
        if workflow == INSTALL:
            return self.after_install()
        title = next((w.title for w in self.workflows() if w.key == workflow), workflow)
        return AfterInstall(f"{title}: done", f"The {self.info.name} is ready.")

    def backup_set(self) -> BackupSet | None:
        """Return its backup on this computer as .env names it (python -m overpressure backup), or None."""
        return None

    def adopt_backup(self, imported: BackupSet) -> None:  # noqa: ARG002  # a headset's own keeps it
        """Make an imported backup this computer's: the .env settings that name it and its serials."""
        msg = f"the {self.info.name} keeps no backup in overpressure"
        raise ValueError(msg)

    def main(self, argv: list[str]) -> int:
        """Run a command of its command line (argv[0] is the command word)."""
        cmd = self.commands()[argv[0]]
        module = importlib.import_module(f"devices.{self.info.codename}.tooling.{cmd.module}")
        entry: Callable[[list[str]], int | None] = module.main
        return entry(argv if cmd.keep else argv[1:]) or 0


@dataclass
class Registry:
    """The headsets in devices/, by codename."""

    headsets: dict[str, Headset] = field(default_factory=dict)

    def get(self, codename: str) -> Headset:
        """Return the headset CODENAME; KeyError names the known ones."""
        try:
            return self.headsets[codename]
        except KeyError:
            known = ", ".join(sorted(self.headsets)) or "none"
            msg = f"no headset {codename!r} in devices/ (known: {known})"
            raise KeyError(msg) from None


def discover() -> Registry:
    """Every devices/<codename>/tooling package that exports HEADSET."""
    registry = Registry()
    for folder in sorted((REPO / "devices").glob("*/tooling/__init__.py")):
        codename = folder.parent.parent.name
        module = importlib.import_module(f"devices.{codename}.tooling")
        headset = getattr(module, "HEADSET", None)
        if isinstance(headset, Headset):
            if headset.info.codename != codename:
                msg = f"devices/{codename} declares codename {headset.info.codename!r}"
                raise ValueError(msg)
            registry.headsets[codename] = headset
    return registry
