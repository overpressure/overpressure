# SPDX-License-Identifier: Unlicense
"""devices.phoenix.tooling.stockfiles: PICO OS's files for the data partition, and the image's links to them."""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tooling import stockfiles


def assets_folders(root: Path) -> dict[str, Path]:
    """Return synthetic assets folders with a file at each path the tree takes, and one the dsp partition holds."""
    files = {
        **{f"graphics-firmware/{n}": n.encode() for n in stockfiles.assets.GPU_FIRMWARE},
        "graphics-firmware/venus.mdt": b"modem",
        "sensor-data/camera/imx471-mode.bin": b"camera",
        "sensor-data/sensors/config/a.json": b"{}",
        "sensor-data/calibration/controllers/led-models.json": b"[]",
        "sensor-data/rendermodels/pico4_controller_left/model.obj": b"o",
        "audio/calibration/MTP_base_Wsa8810_Global_cal.acdb": b"acdb",
        "audio/audiopd/acdb/adsp_avs_config.acdb": b"avs",
        "stock-slam/root/system/bin/bootstrap/linker64": b"linker",
        "stock-slam/root/system/lib64/libSlam.so": b"slam",
        "stock-slam/root/vendor/bin/cdsprpcd": b"listener",
        "stock-slam/root/vendor/dsp/cdsp/fastrpc_shell_3": b"shell",
    }
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    return {kind: root / kind for kind in ("graphics-firmware", "sensor-data", "audio", "stock-slam")}


class ArchiveTests(unittest.TestCase):
    def test_the_tree_is_laid_out_as_the_system_expects_it(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = io.BytesIO(), io.BytesIO()
            manifest = stockfiles.write_archive(assets_folders(Path(folder)), "5.13.7", first)
            stockfiles.write_archive(assets_folders(Path(folder)), "5.13.7", second)
        self.assertEqual(first.getvalue(), second.getvalue())  # the same assets, the same archive
        with tarfile.open(fileobj=io.BytesIO(first.getvalue()), mode="r:gz") as tar:
            names = tar.getnames()
            members = {m.name: m for m in tar.getmembers()}
            sums_file = tar.extractfile("SHA256SUMS")
            sums = sums_file.read().decode() if sums_file else ""
        self.assertEqual(names[:2], ["manifest.json", "SHA256SUMS"])
        self.assertIn("usr/lib/firmware/a650_sqe.fw", manifest["files"])
        self.assertIn("usr/lib/pico4-stock-slam/system/lib64/libSlam.so", manifest["files"])
        # The dsp partition's shells and the modem's images are mounted at boot, not copied.
        self.assertFalse([n for n in members if "fastrpc_shell" in n or "venus" in n])
        self.assertTrue(members["usr/lib/pico4-stock-slam/vendor/dsp/cdsp"].isdir())
        self.assertEqual(members["usr/lib/pico4-stock-slam/system/lib64/libandroid.so"].size, 0)
        self.assertEqual(members["usr/share/qcom/pico4/calibration/pvr"].linkname, "/mnt/vendor/persist/pvr")
        self.assertEqual(members["usr/lib/pico4-stock-slam/vendor/bin/cdsprpcd"].mode, 0o755)
        self.assertEqual({(m.uid, m.gid, m.mtime) for m in members.values()}, {(0, 0, 0)})
        self.assertIn(f"{hashlib.sha256(b'slam').hexdigest()}  usr/lib/pico4-stock-slam/system/lib64/libSlam.so", sums)


class LinkTests(unittest.TestCase):
    def test_every_link_points_to_the_data_partitions_copy_or_a_runtime_mount(self):
        links = stockfiles.image_links()
        self.assertIn("usr/lib/firmware/a650_sqe.fw", links)
        for link, target in links.items():
            self.assertTrue(target.startswith((stockfiles.STOCK + "/", "/run/pico4/")), link)
            if target.startswith(stockfiles.STOCK + "/"):
                self.assertEqual(target, f"{stockfiles.STOCK}/{link}")  # the copy mirrors the image's paths


if __name__ == "__main__":
    unittest.main()
