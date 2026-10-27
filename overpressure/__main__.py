"""overpressure's command line: python -m overpressure [--device CODENAME] COMMAND [ARGS...]

  devices              the headsets in devices/
  gui                  the graphical tool (image creation and flashing)
  steps                the headset's workflow steps and their command lines
  run STEP [NAME=VALUE...]
                       run a step as the GUI does, with parameters
  ram-boot [IMAGE]     boot an image from RAM with fastboot boot (writes nothing), where the
                       headset's bootloader allows it
  COMMAND [ARGS...]    a command of the headset (listed below once a headset is known)

The headset is --device, else OVERPRESSURE_DEVICE (environment or .env), else the only one in
devices/. "python -m overpressure COMMAND --help" shows a command's options.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from . import localconfig, runner
from .headset import Headset, Registry, discover

GENERIC = ("devices", "gui", "steps", "run", "ram-boot")


def select(registry: Registry, codename: Optional[str]) -> Optional[Headset]:
    """The headset to work on, or None when the choice is open."""
    if codename is None:
        codename = os.environ.get("OVERPRESSURE_DEVICE") or None
    if codename is None:
        env = localconfig.ROOT / ".env"
        values = localconfig.parse(env.read_text(encoding="utf-8")) if env.exists() else {}
        codename = values.get("OVERPRESSURE_DEVICE") or None
    if codename:
        return registry.get(codename)
    if len(registry.headsets) == 1:
        return next(iter(registry.headsets.values()))
    return None


def usage(headset: Optional[Headset]) -> str:
    text = __doc__.strip()
    if headset is not None:
        lines = [f"\nCommands of the {headset.info.name} ({headset.info.codename}):"]
        lines += [f"  {word:<20} {cmd.help}" for word, cmd in headset.commands().items()]
        text += "\n" + "\n".join(lines)
    return text


def ram_boot(headset: Headset, argv: List[str]) -> int:
    from . import fastboot
    target = headset.ram_boot()
    if target is None:
        print(f"error: the {headset.info.name} has no RAM boot in overpressure", file=sys.stderr)
        return 2
    if argv[:1] in (["-h"], ["--help"]):
        print("usage: python -m overpressure ram-boot [IMAGE]")
        print(f"default image: {target.image()}")
        return 0
    try:
        fastboot.ram_boot(target, Path(argv[0]) if argv else None)
    except fastboot.Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    codename = None
    if argv[:1] == ["--device"] and len(argv) > 1:
        codename, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--device="):
        codename, argv = argv[0].split("=", 1)[1], argv[1:]
    registry = discover()
    try:
        headset = select(registry, codename)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(headset))
        return 0 if argv else 2
    word = argv[0]
    if word == "devices":
        for name, h in sorted(registry.headsets.items()):
            print(f"{name:<12} {h.info.name}" + (f" ({h.info.soc})" if h.info.soc else ""))
        return 0
    if word == "gui":
        try:
            from . import gui
        except ImportError as exc:  # Python without Tk (as in WSL's)
            print(f"error: no Tk for the window ({exc}); the command line does everything it does",
                  file=sys.stderr)
            return 2
        return gui.main(registry, headset)
    if headset is None:
        print("error: choose a headset with --device (python -m overpressure devices)", file=sys.stderr)
        return 2
    if word == "steps":
        for step in headset.steps():
            argv_ = runner.process(headset, step, {})
            print(f"{step.group:<8} {step.key:<20} {step.title}\n{'':<30}{runner.display(argv_)}")
        return 0
    if word == "ram-boot":
        return ram_boot(headset, argv[1:])
    if word == "run":
        steps = {s.key: s for s in headset.steps()}
        if len(argv) < 2 or argv[1] not in steps:
            print("usage: run STEP [NAME=VALUE...]; steps: " + ", ".join(steps), file=sys.stderr)
            return 2
        values = dict(item.split("=", 1) for item in argv[2:] if "=" in item)
        command = runner.process(headset, steps[argv[1]], values)
        print("$ " + runner.display(command), flush=True)
        return runner.run(command, print)
    if word not in headset.commands():
        print(usage(headset))
        return 2
    return headset.main(argv)


def gui() -> int:
    """The overpressure-gui launcher."""
    return main(["gui"])


if __name__ == "__main__":
    sys.exit(main())
