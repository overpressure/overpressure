# SPDX-License-Identifier: Unlicense
"""overpressure's command line: python -m overpressure [--device CODENAME] COMMAND [ARGS...].

  devices              the headsets in devices/
  gui [--advanced] [--demo | --demo-fresh] [--demo-headsets]
                       the window: the installer, and every step of the headset in its Advanced mode;
                       it opens on the page that asks which headset, unless --device names one
  steps                the headset's workflow steps and their command lines
  run STEP [NAME=VALUE...]
                       run a step as the window does, with parameters
  doctor               what this host has and lacks for the headset's workflow, with fixes
  tools [NAME...]      fetch the pinned host tools: platform-tools, edl-ng
  container build|run -- CMD|shell|binfmt
                       the build container, where every Linux-root step (the builds) runs
  ram-boot [IMAGE]     boot an image from RAM with fastboot boot (writes nothing), where the
                       headset's bootloader allows it
  install [--json] [--plan] [--restart STAGE]
                       the installer: the headset's stages in order, resumable (the window's engine)
  COMMAND [ARGS...]    a command of the headset (listed below once a headset is known)

The headset is --device, else OVERPRESSURE_DEVICE (environment or .env), else the only one in
devices/; the window asks instead, unless --device names one. "python -m overpressure COMMAND
--help" shows a command's options.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from . import doctor, fastboot, install, linux, localconfig, runner, tools
from .headset import Headset, Registry, discover


def select(registry: Registry, codename: str | None) -> Headset | None:
    """Return the headset to work on, or None when the choice is open."""
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


def usage(headset: Headset | None) -> str:
    """Return the usage text, with the headset's own commands when one is chosen."""
    text = __doc__.strip()
    if headset is not None:
        lines = [f"\nCommands of the {headset.info.name} ({headset.info.codename}):"]
        lines += [f"  {word:<20} {cmd.help}" for word, cmd in headset.commands().items()]
        text += "\n" + "\n".join(lines)
    return text


def ram_boot(headset: Headset, argv: list[str]) -> int:
    """Boot an image from RAM: ram-boot [IMAGE]."""
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


def _devices(registry: Registry, _headset: Headset | None, _args: list[str]) -> int:
    for name, h in sorted(registry.headsets.items()):
        print(f"{name:<12} {h.info.name}" + (f" ({h.info.soc})" if h.info.soc else ""))
    return 0


GUI_USAGE = """usage: python -m overpressure gui [--advanced] [--demo | --demo-fresh] [--demo-headsets]

  (nothing)       the installer: its stages, one main button each
  --advanced      open Advanced mode first: every step of the headset, one click each
  --demo          a fake installer engine, a fake step program and fixed checks: no headset, nothing runs;
                  its installer starts as if an earlier run had done four stages (the start page)
  --demo-fresh    the same, with nothing done before
  --demo-headsets the demo (--demo unless --demo-fresh) with a fake second headset, which has no installer

The window opens on "Which headset do you have?", also with one headset in devices/; only --device skips
it (OVERPRESSURE_DEVICE does not). The headset chosen last is marked there and kept in out/window.json,
not in .env; the demo keeps it in the window only. The header's Headsets link comes back to it. The window
needs CustomTkinter (the gui extra); the command line does everything it does."""
GUI_FLAGS = ("--advanced", "--demo", "--demo-fresh", "--demo-headsets")


def _gui(registry: Registry, headset: Headset | None, args: list[str]) -> int:
    if args[:1] in (["-h"], ["--help"]):
        print(GUI_USAGE)
        return 0
    if any(a not in GUI_FLAGS for a in args):
        print(GUI_USAGE, file=sys.stderr)
        return 2
    from . import window  # noqa: PLC0415  # only the window needs it

    missing = window.available()
    if missing:
        print(f"error: the window needs CustomTkinter and Tk ({missing}): {window.HOW}.", file=sys.stderr)
        return 1
    fresh = "--demo-fresh" in args
    demo = fresh or "--demo" in args or "--demo-headsets" in args
    return window.main(registry, headset, demo=demo, fresh=fresh, advanced="--advanced" in args)


def demo_headsets(registry: Registry) -> Registry:
    """Return REGISTRY with gui --demo-headsets' fake second headset, which exists in that demo only."""
    from .window.headsets import DemoHeadset  # noqa: PLC0415  # the window's package, only for the demo

    fake = DemoHeadset()
    return Registry({**registry.headsets, fake.info.codename: fake})


def _container(_registry: Registry, _headset: Headset | None, args: list[str]) -> int:
    return linux.main(args)


def _tools(_registry: Registry, _headset: Headset | None, args: list[str]) -> int:
    return tools.main(args)


def _doctor(_registry: Registry, headset: Headset | None, _args: list[str]) -> int:
    checks = doctor.run(headset)
    print(doctor.report(checks))
    return 1 if doctor.failed(checks) else 0


def _steps(headset: Headset, _args: list[str]) -> int:
    for step in headset.steps():
        argv = runner.process(headset, step, {})
        print(f"{step.group:<8} {step.key:<20} {step.title}\n{'':<30}{runner.display(argv)}")
    return 0


def _run(headset: Headset, args: list[str]) -> int:
    steps = {s.key: s for s in headset.steps()}
    if not args or args[0] not in steps:
        print("usage: run STEP [NAME=VALUE...]; steps: " + ", ".join(steps), file=sys.stderr)
        return 2
    values = dict(item.split("=", 1) for item in args[1:] if "=" in item)
    command = runner.process(headset, steps[args[0]], values)
    print("$ " + runner.display(command), flush=True)
    return runner.run(command, print)


#: The commands of overpressure itself, with or without a headset.
GENERIC: dict[str, Callable[[Registry, Headset | None, list[str]], int]] = {
    "devices": _devices,
    "gui": _gui,
    "container": _container,
    "tools": _tools,
    "doctor": _doctor,
}
#: overpressure's commands that work on the chosen headset.
ON_HEADSET: dict[str, Callable[[Headset, list[str]], int]] = {
    "steps": _steps,
    "ram-boot": ram_boot,
    "run": _run,
    "install": install.main,
}


def _dispatch(registry: Registry, headset: Headset | None, argv: list[str]) -> int:
    word = argv[0]
    if word in GENERIC:
        return GENERIC[word](registry, headset, argv[1:])
    if headset is None:
        print("error: choose a headset with --device (python -m overpressure devices)", file=sys.stderr)
        return 2
    if word in ON_HEADSET:
        return ON_HEADSET[word](headset, argv[1:])
    if word not in headset.commands():
        print(usage(headset))
        return 2
    return headset.main(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the command line."""
    argv = list(sys.argv[1:] if argv is None else argv)
    codename = None
    if argv[:1] == ["--device"] and len(argv) > 1:
        codename, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--device="):
        codename, argv = argv[0].split("=", 1)[1], argv[1:]
    registry = discover()
    if argv[:1] == ["gui"] and "--demo-headsets" in argv:
        registry = demo_headsets(registry)  # before the choice, so the window can ask
    try:
        # The window asks which headset on its first page: only --device skips it, not .env's choice.
        headset = None if argv[:1] == ["gui"] and codename is None else select(registry, codename)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(headset))
        return 0 if argv else 2
    return _dispatch(registry, headset, argv)


def gui() -> int:
    """Open the window: the overpressure-gui launcher."""
    return main(["gui"])


if __name__ == "__main__":
    sys.exit(main())
