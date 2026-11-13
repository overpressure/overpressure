# SPDX-License-Identifier: Unlicense
"""devices.phoenix.tooling.assets: every assets folder from a synthetic stock folder, for the userspace build."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from devices.phoenix.tests.test_kernel_poc import camera_module
from devices.phoenix.tooling import HEADSET, assets, doctor
from overpressure import runner
from overpressure.assets import AssetError, StockFolder
from tests.images import Ext4Image, FatImage, stock_folder

ROOT = Path(__file__).resolve().parents[1]


def modem() -> bytes:
    """Return a modem partition with the firmware the kinds take, and one file none takes."""
    img = FatImage(16)
    for name in ("venus.mdt", "venus.b00", "venus.b19", "slpi.mdt", "slpi.b00", "slpir.jsn", "slpius.jsn"):
        img.file(f"/image/{name}", name.encode())
    for name in ("adsp.mdt", "adsp.b00", "adsp.b18", "adspr.jsn", "adspua.jsn", "cdsp.mdt", "cdsp.b11", "cdspr.jsn"):
        img.file(f"/image/{name}", name.encode())
    img.file("/image/cvpss.mdt", b"another DSP")
    for name in ("amss20.bin", "m3.bin", "bdwlanq.elf", "bdwlan.elf"):
        img.file(f"/image/qca6390/{name}", name.encode())
    return img.build()


def bluetooth() -> bytes:
    """Return a bluetooth partition with the QCA6390's files and another chip's."""
    img = FatImage(16)
    for name in ("htbtfw20.tlv", "htnv20.bin", "hpbtfw10.tlv"):
        img.file(f"/image/{name}", name.encode())
    return img.build()


#: The RGB camera's module in the vendor image, taken as a known one (imx471_mode.MODULES).
CAMERA = camera_module()
KNOWN_CAMERA = mock.patch.object(assets.imx471_mode, "MODULES", {hashlib.sha256(CAMERA).hexdigest()})


def vendor() -> bytes:
    """Return a vendor partition with what the kinds take, and a DSP library none takes."""
    img = Ext4Image(inodes_per_group=64)
    for name in assets.GPU_FIRMWARE:
        img.file(f"/firmware/{name}", name.encode())
    img.file("/etc/sensors/config/kona_irq.json", b"{}")
    img.file("/etc/sensors/config/sns_amd.json", b"{}")
    img.file("/etc/sensors/sns_reg_config", b"registry layout")
    img.file(assets.CAMERA_MODULE, CAMERA)
    img.file("/etc/acdbdata/adsp_avs_config.acdb", b"avs")
    for name in assets.AUDIO_CALIBRATION:
        img.file(f"/etc/acdbdata/wsa8810/base/MTP/{name}", name.encode())
    for name in ("capi_v2_a.so", "capi_v2_b.so", "libc++.so.1", "libc++abi.so.1", "libapps_mem_heap.so"):
        img.file(f"/lib/rfsa/adsp/{name}", name.encode())
    img.file("/lib/rfsa/adsp/sami_audio_config.json", b"{}")
    for name in assets.SLAM_DSP_MODULES:
        img.file(f"/lib/rfsa/adsp/{name}", name.encode())
    img.file("/lib/rfsa/adsp/libvision_skel.so", b"not the audio process's")
    img.file("/bin/cdsprpcd", b"listener", 0o755)
    for name in assets.DSP_LISTENER_LIBRARIES:
        img.file(f"/lib64/{name}", name.encode())
    return img.build()


def system() -> bytes:
    """Return a system partition with what the kinds take."""
    img = Ext4Image(inodes_per_group=64)
    img.file(assets.LENS_CONFIG, b"lens")
    for path in (assets.LED_LIBRARY, assets.AIM_GRIP_LIBRARY, assets.LEVER_ARM_LIBRARY):
        img.file(path, b"\x7fELF " + path.encode())
    img.file(assets.CONTROLLER_MODELS + "/60.obj", b"o")
    img.file("/system/bin/bootstrap/linker64", b"linker", 0o755)
    for name in assets.SLAM_BOOTSTRAP:
        img.file(f"/system/lib64/bootstrap/{name}", name.encode())
    for name in (*assets.SLAM_LIBRARIES, *assets.DEPTH_MESH_LIBRARIES, *assets.SNPE_DSP):
        img.file(f"/system/lib64/{name}", name.encode())
    img.file("/system/etc/pvr/config/config_lscLH.txt", b"lsc")
    img.file("/system/etc/pvr/config/mask/cam0/mask.yaml", b"mask")
    for name in assets.DEPTH_MESH_CONFIG:
        img.file(f"/system/etc/pxr/{name}", name.encode())
    img.file("/system/etc/pxr/vst/FLNet_lite_v2/featureNet/feature.json.cpt", b"net")
    return img.build()


def dsp() -> bytes:
    """Return a dsp partition with the DSP's process shells."""
    img = Ext4Image()
    img.file("/cdsp/fastrpc_shell_3", b"shell")
    img.file("/cdsp/libc++.so.1", b"c++")
    return img.build()


def files(folder: Path) -> list[str]:
    """Return every file below @p folder, as POSIX paths."""
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


class AssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.images = {
            "modem.bin": modem(),
            "bluetooth.bin": bluetooth(),
            "vendor.img": vendor(),
            "system.img": system(),
            "dsp.bin": dsp(),
        }

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stock = stock_folder(self.root / "stock", self.images, firmware="5.13.7-global-seko")
        self.folder = StockFolder(self.stock)
        self.addCleanup(self.folder.close)

    def extract(self, kind: str) -> Path:
        out = self.root / f"{kind}-5.13.7"
        assets.extract(kind, self.folder, out, log=lambda _line: None)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["firmware"], "5.13.7-global-seko")
        self.assertEqual(manifest["sources"], assets.KINDS[kind].sources)
        self.assertEqual(sorted(manifest["files"]), [f for f in files(out) if f != "manifest.json"])
        return out

    def test_graphics_firmware(self):
        out = self.extract("graphics-firmware")
        self.assertEqual(
            files(out), sorted([*assets.GPU_FIRMWARE, "venus.mdt", "venus.b00", "venus.b19", "manifest.json"])
        )
        self.assertEqual((out / "a650_sqe.fw").read_bytes(), b"a650_sqe.fw")

    def test_wlan(self):
        out = self.extract("wlan")
        self.assertEqual(
            files(out),
            [
                "firmware/qca/htbtfw20.tlv",
                "firmware/qca/htnv20.bin",
                "firmware/qca6390/amss20.bin",
                "firmware/qca6390/bdwlanq.elf",
                "firmware/qca6390/m3.bin",
                "manifest.json",
            ],
        )
        self.assertEqual((out / "firmware/qca/htnv20.bin").read_bytes(), b"htnv20.bin")

    def test_audio_takes_only_the_audio_process_libraries(self):
        out = self.extract("audio")
        self.assertEqual(
            sorted(p.name for p in (out / "audiopd/dsp/adsp").iterdir()),
            [
                "capi_v2_a.so",
                "capi_v2_b.so",
                "libapps_mem_heap.so",
                "libc++.so.1",
                "libc++abi.so.1",
                "sami_audio_config.json",
            ],
        )
        self.assertEqual(
            sorted(p.name for p in (out / "firmware").iterdir()),
            ["adsp.b00", "adsp.b18", "adsp.mdt", "adspr.jsn", "adspua.jsn"],
        )
        self.assertTrue((out / "calibration/MTP_base_Wsa8810_Global_cal.acdb").is_file())
        self.assertTrue((out / "audiopd/acdb/adsp_avs_config.acdb").is_file())

    def test_stock_slam_root(self):
        out = self.extract("stock-slam")
        found = files(out)
        for path in (
            "firmware/cdsp.mdt",
            "firmware/cdsp.b11",
            "firmware/cdspr.jsn",
            "root/vendor/dsp/cdsp/fastrpc_shell_3",
            "root/system/bin/bootstrap/linker64",
            "root/system/lib64/bootstrap/libc.so",
            "root/system/lib64/libSlam.so",
            "root/system/lib64/libphotoid.so",
            "root/system/lib64/libsnpe_dsp_domains_v2.so",
            "root/system/etc/pvr/config/mask/cam0/mask.yaml",
            "root/system/etc/pxr/vst_releaseA.json.cpt",
            "root/system/etc/pxr/vst/FLNet_lite_v2/featureNet/feature.json.cpt",
            "root/vendor/lib/rfsa/adsp/libslamDSP_skel.so",
            "root/vendor/bin/cdsprpcd",
            "root/vendor/lib64/libcdsp_default_listener.so",
        ):
            self.assertIn(path, found)
        self.assertNotIn("firmware/cvpss.mdt", found)
        self.assertNotIn("root/vendor/lib/rfsa/adsp/libvision_skel.so", found)

    def test_sensor_data_with_the_controller_data(self):
        def convert(model_dir: Path, output: Path, aim: dict, leds: dict, levers: dict) -> dict[str, float]:
            self.assertEqual(model_dir.name, "Neo3Phx")
            self.assertTrue((model_dir / "60.obj").is_file())
            self.assertEqual((aim, leds, levers), ({"aim": 1}, {"left": [[0.0]]}, {"left": [1.0]}))
            (output / "pico4_controller_left").mkdir(parents=True)
            (output / "pico4_controller_left" / "pico4_controller_left.obj").write_bytes(b"model")
            return {"left": 0.001}

        libraries = []

        def read(name: str) -> Callable[[bytes], dict]:
            def parse(elf: bytes) -> dict:
                libraries.append((name, elf))
                return {}

            return parse

        with (
            mock.patch.multiple(
                assets.ledmodel,
                models=read("leds"),
                normals=lambda _e, _r: {},
                to_json=lambda *_a: '{"left": [[0.0]]}\n',
            ),
            mock.patch.multiple(assets.aimgrip, transforms=read("aim"), to_json=lambda *_a: '{"aim": 1}\n'),
            mock.patch.multiple(assets.leverarm, lever_arms=read("lever"), to_json=lambda *_a: '{"left": [1.0]}\n'),
            mock.patch.object(assets.controllermodel, "convert", convert),
            KNOWN_CAMERA,
        ):
            out = self.extract("sensor-data")
        self.assertEqual([n for n, _elf in libraries], ["leds", "aim", "lever"])
        self.assertEqual(libraries[0][1], b"\x7fELF " + assets.LED_LIBRARY.encode())
        found = files(out)
        for path in (
            "firmware/slpi.mdt",
            "firmware/slpius.jsn",
            "sensors/config/kona_irq.json",
            "sensors/sns_reg.conf",
            "calibration/pxrconfig/SDK_Lens_ConfigFile.txt",
            "calibration/controllers/led-models.json",
            "calibration/controllers/aim-to-grip.json",
            "calibration/controllers/lever-arm.json",
            "rendermodels/pico4_controller_left/pico4_controller_left.obj",
            "camera/imx471-mode.bin",
        ):
            self.assertIn(path, found)
        # The camera module's mode, without the driver's controls, as the driver loads it.
        table = assets.imx471_mode.table([(0x1000, 0x11), (0x1001, 0x66)])
        self.assertEqual((out / "camera/imx471-mode.bin").read_bytes(), table)
        self.assertNotIn(b"\r", (out / "calibration/controllers/aim-to-grip.json").read_bytes())

    def test_an_unknown_camera_module_is_refused(self):
        with (
            mock.patch.multiple(
                assets.ledmodel, models=lambda _e: {}, normals=lambda _e, _r: {}, to_json=lambda *_a: "{}\n"
            ),
            mock.patch.multiple(assets.aimgrip, transforms=lambda _e: {}, to_json=lambda *_a: "{}\n"),
            mock.patch.multiple(assets.leverarm, lever_arms=lambda _e: {}, to_json=lambda *_a: "{}\n"),
            self.assertRaisesRegex(AssetError, "unknown camera module"),
        ):
            self.extract("sensor-data")

    def test_a_changed_image_and_an_existing_output_are_refused(self):
        (self.stock / "dsp.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(AssetError, "dsp.bin: sha256 does not match"):
            assets.extract("stock-slam", self.folder, self.root / "slam", log=lambda _line: None)
        (self.root / "taken").mkdir()
        with self.assertRaisesRegex(AssetError, "exists"):
            assets.extract("wlan", self.folder, self.root / "taken", log=lambda _line: None)

    def test_build_replaces_each_folder_only_when_complete(self):
        out = self.root / "assets"
        (out / "wlan-5.13.7").mkdir(parents=True)
        (out / "wlan-5.13.7" / "old").write_bytes(b"old")
        fetched = []

        def fetch(firmware: str, stock: Path, _log: Callable[[str], None]) -> dict:
            fetched.append((firmware, stock))
            return {}

        done = assets.build(
            "5.13.7-global-seko", self.stock, out, ("wlan", "graphics-firmware"), fetch=fetch, log=lambda _l: None
        )
        self.assertEqual(fetched, [("5.13.7-global-seko", self.stock)])
        self.assertEqual([p.name for p in done], ["wlan-5.13.7", "graphics-firmware-5.13.7"])
        self.assertFalse((out / "wlan-5.13.7" / "old").exists())
        self.assertFalse((out / "wlan-5.13.7.new").exists())
        (self.stock / "modem.bin").write_bytes(b"broken")
        with self.assertRaises(AssetError):
            assets.build("5.13.7-global-seko", self.stock, out, ("wlan",), fetch=fetch, log=lambda _l: None)
        self.assertTrue((out / "wlan-5.13.7" / "manifest.json").is_file())  # the old folder stays


class WiringTests(unittest.TestCase):
    def test_kinds_are_the_userspace_builds_folders(self):
        self.assertEqual(doctor.ASSET_KINDS, ("graphics-firmware", "sensor-data", "wlan", "audio", "stock-slam"))
        build = (ROOT / "scripts/build-userspace.sh").read_text(encoding="utf-8")
        for option in ("--gpu-firmware", "--sensor-data", "--wlan", "--audio", "--stock-slam"):
            self.assertIn(f"    {option}) ", build)

    def test_the_step_is_python_on_this_computer(self):
        step = next(s for s in HEADSET.steps() if s.key == "assets")
        self.assertFalse(step.script or step.linux_root)
        argv = runner.process(HEADSET, step, {"firmware": "5.13.7-global-seko"}, python="python")
        self.assertEqual(
            argv,
            [
                "python",
                "-m",
                "overpressure",
                "--device",
                "phoenix",
                "assets",
                "--firmware",
                "5.13.7-global-seko",
                "--stock",
                "cache/pico4-stock/5.13.7-global-seko",
            ],
        )
        # The kernel is a download of the pinned release: no stock folder, no Linux.
        kernel = next(s for s in HEADSET.steps() if s.key == "kernel")
        self.assertEqual(kernel.argv, ("kernel", "fetch"))
        self.assertFalse(kernel.linux_root)

    def test_extraction_reads_only_pico_os_s_partitions(self):
        for kind in assets.KINDS.values():
            self.assertTrue(set(kind.images) <= {"system.img", "vendor.img", "modem.bin", "dsp.bin", "bluetooth.bin"})
        self.assertFalse(list((ROOT / "scripts").glob("extract-*.sh")))


if __name__ == "__main__":
    unittest.main()
