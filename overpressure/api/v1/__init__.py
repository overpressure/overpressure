# SPDX-License-Identifier: Unlicense
"""Headset interface 1 (overpressure.headset.SUPPORTED names its newest minor): what a headset may use.

    from overpressure.api.v1 import REPO, qualcomm
    from overpressure.api.v1.headset import Headset, Step

  android      adb and fastboot, fail-closed, each command naming the serial
  assets       a headset's assets out of its stock folder
  avb          Android Verified Boot footers and vbmeta
  backup       verified backups, their archives, and the window's words for them
  bootimg      Android boot images
  compress     the compressions images use
  cpio         cpio archives (ramdisks)
  doctor       dependency checks
  evidence     runs on the headset that record every step
  ext4         ext4 images, read without mounting
  fastboot     flashing through fastboot, the RAM boot
  fat          FAT images, read without mounting
  headset      the headset, its steps, workflows and finished page
  install      the installer: stages, tasks, asks and the context a task runs in
  kernelinfo   a kernel image's version and configuration
  localconfig  a headset's settings in .env
  ota          a vendor's OTA: boot images and partitions, pinned members
  payload      Android A/B OTA payloads
  process      bounded waits and running tools
  progress     progress lines
  qualcomm     Qualcomm: SoC ID, EDL, partition maps, verified writes
  runner       a step's command line
  ssh          SSH with the project's key
  stage1       an Alpine stage 1 in a stock boot image
  storage      the read-only storage policy
  tools        the pinned host tools
  usbaccess    USB access on Linux, a headset's USB modes
  windows_ncm  Windows' USB network link

Each module is today's own module (the same object at run time, so a patch in a test reaches every caller).
Once interface 2 changes one, its module here becomes an adapter that keeps interface 1's behaviour.
"""

from overpressure import REPO

__all__ = ["REPO"]
