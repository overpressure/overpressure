"""The image is built without the user's backup.

The stock files come from PICO's public OTA (python -m overpressure stock), the headset's own factory data
from its persist partition at boot (pico4-persist.service).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ["extract-graphics-firmware.sh", "extract-sensor-data.sh", "extract-wlan.sh", "extract-audio.sh",
           "extract-stock-slam.sh"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ExtractionTests(unittest.TestCase):
    def test_no_extraction_reads_the_backup(self):
        for name in SCRIPTS:
            script = read(ROOT / "scripts" / name)
            self.assertNotRegex(script, r"lun\d/|persist\.bin|superimg|backup", name)
            self.assertIn('. "$project/../../scripts/stock-lib.sh"', script, name)
            self.assertRegex(script, r'stock_dir "\$\{1:-\}"( [a-z]+\.(img|bin))+\n', name)
            self.assertNotIn(b"\r", (ROOT / "scripts" / name).read_bytes(), name)

    def test_the_build_ships_no_persist_data(self):
        build = read(ROOT / "scripts/build-userspace.sh")
        self.assertNotIn("wlan_mac.bin; do", build)
        self.assertNotRegex(build, r'"\$sensor_data/sensors/registry"')
        for link in ('/mnt/vendor/persist/sensors/registry/registry "$qcom/sensors/registry"',
                     '/mnt/vendor/persist/sensors/registry/sns_reg_version "$qcom/sensors/sns_reg_version"',
                     '/mnt/vendor/persist/pvr "$qcom/calibration/pvr"'):
            self.assertIn("ln -sfn " + link, build)
        self.assertIn('mkdir -p "$T/mnt/vendor/persist"', build)
        self.assertIn("pico4-persist.service", build[build.index("in_t systemctl enable"):])


class PersistTests(unittest.TestCase):
    def test_mounted_read_only_without_journal_replay(self):
        script = read(ROOT / "userspace/rootfs/usr/libexec/pico4-poc/persist")
        self.assertIn("mount -t ext4 -o ro,noload,nodev,nosuid,noexec", script)
        self.assertIn('blockdev --setro "$dev"', script)
        unit = read(ROOT / "userspace/rootfs/usr/lib/systemd/system/pico4-persist.service")
        self.assertIn("After=pico4-storage-ro.service", unit)
        self.assertIn("Before=local-fs.target", unit)

    def test_session_user_reads_a_copy_of_the_controller_bias_tables(self):
        script = read(ROOT / "userspace/rootfs/usr/libexec/pico4-poc/persist")
        self.assertIn('"$dir"/pvr/?????????????????.txt', script)
        self.assertIn("install -m 644", script)
        self.assertIn("/run/pico4-controllers/", script)
        driver = read(ROOT / "src/monado/pico4_ctrack.cpp")
        self.assertIn('"/run/pico4-controllers/" + sn + ".txt"', driver)

    def test_fan_batch_from_the_serial_at_run_time(self):
        fan = read(ROOT / "userspace/rootfs/usr/bin/pico4-fan")
        self.assertIn('SERIAL_FILE = Path("/mnt/vendor/persist/falcon/sn")', fan)


class DevImageTests(unittest.TestCase):
    def test_recovery_from_the_ota(self):
        import tempfile
        import zipfile
        from overpressure import ota
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "ota.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("boot.img", b"ANDROID!boot")
                zf.writestr("recovery.img", b"ANDROID!recovery")
            self.assertEqual(ota.fetch_boot_image(str(zip_path), log=lambda *a: None), b"ANDROID!boot")
            self.assertEqual(ota.fetch_boot_image(str(zip_path), log=lambda *a: None, partition="recovery"),
                             b"ANDROID!recovery")
        devloop = read(ROOT / "tooling/devloop.py")
        image = devloop[devloop.index("    def image(self):"):devloop.index("    def install_image(")]
        self.assertIn('ota.fetch_boot_image(self.stock_location(), str(ROOT / "cache"), print, "recovery")', image)
        self.assertNotIn("stock_partition", image)


class StockLibTests(unittest.TestCase):
    def test_files_checked_before_use(self):
        lib = read(ROOT.parent.parent / "scripts/stock-lib.sh")
        self.assertIn("sha256 does not match the stock manifest", lib)
        self.assertTrue(re.search(r"stock_dir\(\) \{", lib))


if __name__ == "__main__":
    unittest.main()
