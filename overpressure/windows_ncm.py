# SPDX-License-Identifier: Unlicense
"""Initialize the stock 4.19 NCM gadget for Windows, then release it to UsbNcm.

Requires PyUSB, libusb-package and the separately installed UsbDk driver.
Only the headset's stage-1 gadget, by its exact USB serial, may receive configuration changes
or NCM setup.
The caller must bound this process: a Windows driver open/close can stall.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Callable

SCRIPT = Path(__file__).resolve()
USB_CLASS_CDC = 2
CDC_SUBCLASS_NCM = 0x0D


def command(python: str | os.PathLike[str], serial: str | None = None) -> list:
    """Return this script's command line for @p python, the Python with the windows-ncm extra.

    It initializes the gadget with @p serial, or (without) checks the dependencies.
    """
    return [python, SCRIPT, *(["--serial", serial] if serial else ["--check"])]


def initialize(devices: list, serial: str, dispose: Callable[..., object]) -> dict:
    """Initialize the one gadget in DEVICES (PyUSB devices) if its serial is SERIAL; release it after."""
    if len(devices) != 1:
        msg = f"Expected exactly one stage-1 gadget; found {len(devices)}"
        raise ValueError(msg)
    dev = devices[0]
    try:
        if dev.serial_number != serial:
            msg = "Stage-1 gadget serial mismatch; refusing control requests"
            raise ValueError(msg)
        if dev.bNumConfigurations != 1:
            msg = "Unexpected stage-1 gadget USB configuration count"
            raise ValueError(msg)
        cfg = dev[0]
        controls = [i for i in cfg if i.bInterfaceClass == USB_CLASS_CDC and i.bInterfaceSubClass == CDC_SUBCLASS_NCM]
        if len(controls) != 1:
            msg = "Expected exactly one CDC NCM control interface"
            raise ValueError(msg)
        iface = controls[0].bInterfaceNumber
        # UsbDk takes the gadget out of Windows' active configuration. Select
        # its sole configuration, initialize CRC mode and relinquish ownership.
        # No gadget UDC unbind occurs, so the initialized NDP signature survives.
        dev.set_configuration(cfg.bConfigurationValue)
        if dev.ctrl_transfer(0x21, 0x8A, 0, iface, b"", timeout=3000) != 0:
            msg = "SET_CRC_MODE did not complete"
            raise ValueError(msg)
        return {"status": "initialized", "serial": serial, "interface": iface, "request": "CDC SET_CRC_MODE(0)"}
    finally:
        dispose(dev)


def backend() -> tuple:
    """Return libusb's backend on UsbDk, the loaded UsbDk helper, and PyUSB's core and util."""
    if sys.platform != "win32":
        msg = "The UsbDk NCM helper requires Windows"
        raise ValueError(msg)
    import libusb_package  # noqa: PLC0415  # only the NCM venv has it
    import usb.backend.libusb1  # noqa: PLC0415  # only the NCM venv has it
    import usb.core  # noqa: PLC0415  # only the NCM venv has it
    import usb.util  # noqa: PLC0415  # only the NCM venv has it

    helper = ctypes.WinDLL(
        str(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "UsbDk Runtime Library" / "UsbDkHelper.dll")
    )
    library = libusb_package.find_library("libusb-1.0")
    api = usb.backend.libusb1.get_backend(find_library=lambda _: library)
    if api is None:
        msg = "libusb backend is unavailable"
        raise ValueError(msg)
    # Select UsbDk before enumeration. The tested libusb 1.0.30 Windows build
    # rejects this option during context creation, but accepts it immediately
    # afterwards. Keep the helper DLL loaded for the entire context lifetime.
    api.lib.libusb_set_option.argtypes = [ctypes.c_void_p, ctypes.c_int]
    api.lib.libusb_set_option.restype = ctypes.c_int
    if api.lib.libusb_set_option(api.ctx, 1) != 0:
        msg = "UsbDk backend is unavailable; install UsbDk first"
        raise ValueError(msg)
    return api, helper, usb.core, usb.util


def main(argv: list[str] | None = None) -> int:
    """Initialize the gadget or check the dependencies; print the result as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check dependencies without opening a device")
    mode.add_argument("--serial", help="exact USB serial of the stage-1 gadget, normally the fastboot serial")
    args = parser.parse_args(argv)
    try:
        api, _helper, core, util = backend()
        versions = {name: importlib.metadata.version(name) for name in ("pyusb", "libusb-package")}
        if args.check:
            result = {"status": "ready", "dependencies": versions}
        else:
            devices = list(core.find(find_all=True, idVendor=0x1D6B, idProduct=0x0104, backend=api))
            result = initialize(devices, args.serial, util.dispose_resources)
            result["dependencies"] = versions
        print(json.dumps(result), flush=True)
    except Exception as exc:  # noqa: BLE001  # any failure is one error line and exit 1 for the caller
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
