"""Initialize the stock 4.19 NCM gadget for Windows, then release it to UsbNcm.

Requires PyUSB, libusb-package and the separately installed UsbDk driver.
Only the explicit POC USB serial may receive a configuration/control request.
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


def initialize(devices, serial, dispose):
    if len(devices) != 1:
        raise ValueError(f"Expected exactly one POC gadget; found {len(devices)}")
    dev = devices[0]
    try:
        if dev.serial_number != serial:
            raise ValueError("POC gadget serial mismatch; refusing control requests")
        if dev.bNumConfigurations != 1:
            raise ValueError("Unexpected POC USB configuration count")
        cfg = dev[0]
        controls = [i for i in cfg if i.bInterfaceClass == 2 and i.bInterfaceSubClass == 13]
        if len(controls) != 1:
            raise ValueError("Expected exactly one CDC NCM control interface")
        iface = controls[0].bInterfaceNumber
        # UsbDk takes the gadget out of Windows' active configuration. Select
        # its sole configuration, initialize CRC mode and relinquish ownership.
        # No gadget UDC unbind occurs, so the initialized NDP signature survives.
        dev.set_configuration(cfg.bConfigurationValue)
        if dev.ctrl_transfer(0x21, 0x8a, 0, iface, b"", timeout=3000) != 0:
            raise ValueError("SET_CRC_MODE did not complete")
        return {"status": "initialized", "serial": serial, "interface": iface,
                "request": "CDC SET_CRC_MODE(0)"}
    finally:
        dispose(dev)


def backend():
    if sys.platform != "win32":
        raise ValueError("The UsbDk NCM helper requires Windows")
    import libusb_package
    import usb.backend.libusb1
    import usb.core
    import usb.util

    helper = ctypes.WinDLL(str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
                              "UsbDk Runtime Library" / "UsbDkHelper.dll"))
    library = libusb_package.find_library("libusb-1.0")
    api = usb.backend.libusb1.get_backend(find_library=lambda _: library)
    if api is None:
        raise ValueError("libusb backend is unavailable")
    # Select UsbDk before enumeration. The tested libusb 1.0.30 Windows build
    # rejects this option during context creation, but accepts it immediately
    # afterwards. Keep the helper DLL loaded for the entire context lifetime.
    api.lib.libusb_set_option.argtypes = [ctypes.c_void_p, ctypes.c_int]
    api.lib.libusb_set_option.restype = ctypes.c_int
    if api.lib.libusb_set_option(api.ctx, 1) != 0:
        raise ValueError("UsbDk backend is unavailable; install UsbDk first")
    return api, helper, usb.core, usb.util


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="check dependencies without opening a device")
    mode.add_argument("--serial", help="exact POC USB serial, normally the fastboot serial")
    args = parser.parse_args(argv)
    try:
        api, helper, core, util = backend()
        versions = {name: importlib.metadata.version(name) for name in ("pyusb", "libusb-package")}
        if args.check:
            result = {"status": "ready", "dependencies": versions}
        else:
            devices = list(core.find(find_all=True, idVendor=0x1d6b, idProduct=0x0104, backend=api))
            result = initialize(devices, args.serial, util.dispose_resources)
            result["dependencies"] = versions
        print(json.dumps(result), flush=True)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
