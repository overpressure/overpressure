# SPDX-License-Identifier: Unlicense
"""PICO OS's head tracker: what the assets take for it (pico4-slamd is overpressure/packages' pico4-slamd)."""

import unittest
from pathlib import Path

from devices.phoenix.tooling import assets

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    """Return a file's text."""
    return path.read_text(encoding="utf-8")


class ExtractionTests(unittest.TestCase):
    def test_extracts_what_the_tracker_needs(self):
        taken = {
            *assets.SLAM_LIBRARIES,
            *assets.SLAM_DSP_MODULES,
            *assets.DSP_LISTENER_LIBRARIES,
            *assets.SLAM_BOOTSTRAP,
            *assets.CDSP_FIRMWARE,
        }
        source = read(ROOT / "tooling/assets.py")
        # libion: without it the tracker's DSP optical flow cannot allocate and 6DoF never starts.
        for name in (
            "libSlam.so",
            "libslamDSP.so",
            "libceres.so",
            "libminiglog.so",
            "libcdsprpc.so",
            "libion.so",
            "libslamDSP_skel.so",
            "libpico_cv_dsp_skel.so",
            "libfastcvadsp.so",
            "libdspCV_skel.so",
            "libcdsp_default_listener.so",
            "libExposureImageProcess.so",
            "libtianti_shm_dynamic.so",
            "libc++_shared.so",
            "cdsp.*",
            "cdspr.jsn",
        ):
            self.assertIn(name, taken, name)
        for path in ("/bin/cdsprpcd", "/system/bin/bootstrap/linker64", '"/cdsp"', "fastrpc_shell_3"):
            self.assertIn(path, source, path)
        # From the stock firmware (PICO's public OTA), checked against its manifest first.
        self.assertEqual(assets.KINDS["stock-slam"].images, ("system.img", "vendor.img", "modem.bin", "dsp.bin"))
        # As a fresh PICO OS install, nothing of the camera modules' ids from the backup.
        self.assertNotIn("camera_otp", source)
        self.assertNotIn("userdata", source)
        # The trees come in whole, as debugfs's rdump took them: into their parent folder.
        self.assertIn('dump(system, "/system/etc/pvr/config", root / "system/etc/pvr")', source)
        self.assertIn('dump(s.ext4("dsp.bin"), "/cdsp", root / "vendor/dsp")', source)


class DepthMeshExtractionTests(unittest.TestCase):
    def test_extracts_libphotoid_and_its_backends(self):
        self.assertEqual(
            assets.DEPTH_MESH_LIBRARIES,
            (
                "libphotoid.so",
                "libSNPE.so",
                "libhexagon_runtime.so",
                "libcvp2.so",
                "libcvp2_hfi.so",
                "libcvp_common.so",
            ),
        )
        self.assertEqual(assets.DEPTH_MESH_CONFIG, ("vst_releaseA.json.cpt", "vst_releaseB.json.cpt"))
        self.assertIn("libcvpdsp_skel.so", assets.SLAM_DSP_MODULES)
        self.assertIn("libhexagon_nn_skel.so", assets.SLAM_DSP_MODULES)
        source = read(ROOT / "tooling/assets.py")
        self.assertIn('dump(system, "/system/etc/pxr/vst", root / "system/etc/pxr")', source)
        # SNPE has the DSP load its skel by the system path, as on PICO OS.
        self.assertEqual(assets.SNPE_DSP, ("libsnpe_dsp_v66_domains_v2_skel.so", "libsnpe_dsp_domains_v2.so"))
        self.assertIn('dump(system, f"/system/lib64/{name}", root / "system/lib64" / name)', source)


if __name__ == "__main__":
    unittest.main()
