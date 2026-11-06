# SPDX-License-Identifier: Unlicense
"""overpressure.storage: the read-only storage policy, from the block devices' flags and the mounts."""

import unittest

from overpressure import storage


class StoragePolicyTests(unittest.TestCase):
    def test_storage_checks_partitions_not_just_disks(self):
        storage.validate_storage("BLOCK sda 1\nBLOCK sda1 1\nBLOCK loop0 0\nBLOCK ram0 0")
        for value in ("BLOCK sda 1\nBLOCK sda1 0", "BLOCK sda 0", "BLOCK loop0 1", "", "BLOCK sda ?"):
            with self.assertRaises(ValueError):
                storage.validate_storage(value)

    def test_mounts_cannot_hide_writable_storage_or_disk_root(self):
        storage.validate_mounts("rootfs / rootfs rw 0 0\n/dev/sda1 /mnt rofs ro 0 0")
        for value in (
            "",
            "proc /proc proc rw 0 0",
            "/dev/root / ext4 ro 0 0",
            "rootfs / rootfs rw 0 0\n/dev/sda1 /mnt ext4 rw 0 0",
        ):
            with self.assertRaises(ValueError):
                storage.validate_mounts(value)

    def test_stage_1_root_is_ram_and_a_zram_root_is_not_accepted(self):
        storage.validate_mounts("rootfs / rootfs rw 0 0\ntmpfs /run tmpfs rw 0 0")
        with self.assertRaises(ValueError):
            storage.validate_mounts("/dev/zram0 / ext4 rw,noatime,discard 0 0\ntmpfs /run tmpfs rw 0 0")

    def test_installed_system_writes_only_its_data_partition(self):
        mounts = (
            "/dev/sda7 / ext4 ro,noatime 0 0\n/dev/sda16 /data ext4 rw,noatime 0 0\n"
            "overlay /etc overlay rw,relatime 0 0\n/dev/sda16 /var ext4 rw,noatime 0 0\n"
            "/dev/sda16 /home ext4 rw,noatime 0 0\ntmpfs /run tmpfs rw 0 0"
        )
        storage.validate_mounts(mounts, installed=True)
        storage.validate_mounts(mounts + "\n/dev/sda16 /var/log ext4 rw,noatime 0 0", installed=True)
        for bad in (
            mounts.replace("/ ext4 ro", "/ ext4 rw"),
            mounts + "\n/dev/sda5 /mnt ext4 rw 0 0",
            mounts.replace("/dev/sda16 /data ext4 rw,noatime 0 0\n", ""),
        ):
            with self.assertRaises(ValueError):
                storage.validate_mounts(bad, installed=True)
        with self.assertRaises(ValueError):
            storage.validate_mounts(mounts)

    def test_another_data_mount_point(self):
        mounts = "/dev/sda7 / ext4 ro 0 0\n/dev/sda20 /userdata ext4 rw 0 0"
        self.assertEqual(storage.data_device(mounts, "/userdata"), "sda20")
        self.assertEqual(storage.data_device(mounts), "")
        storage.validate_mounts(mounts, installed=True, data="/userdata")
        with self.assertRaises(ValueError):
            storage.validate_mounts(mounts, installed=True)

    def test_installed_data_partition_must_be_writable(self):
        mounts = "/dev/sda7 / ext4 ro,noatime 0 0\n/dev/sda16 /data ext4 rw,noatime 0 0"
        self.assertEqual(storage.data_device(mounts), "sda16")
        storage.validate_storage("BLOCK sda 1\nBLOCK sda7 1\nBLOCK sda16 0\nBLOCK loop0 0", "sda16")
        # A mainline kernel: the data partition's disk is writable too, its other partitions not.
        storage.validate_storage("BLOCK sda 0\nBLOCK sda7 1\nBLOCK sda16 0\nBLOCK sdb 1", "sda16")
        with self.assertRaises(ValueError):
            storage.validate_storage("BLOCK sda 1\nBLOCK sdb 0\nBLOCK sda16 0", "sda16")
        for bad in (
            "BLOCK sda 1\nBLOCK sda7 1\nBLOCK sda16 1",  # writeback would fail
            "BLOCK sda 1\nBLOCK sda7 0\nBLOCK sda16 0",  # another partition writable
        ):
            with self.assertRaises(ValueError):
                storage.validate_storage(bad, "sda16")
        with self.assertRaises(ValueError):
            storage.validate_storage("BLOCK sda 1\nBLOCK sda16 0")  # not installed: all read-only

    def test_storage_stays_protected_in_stage_two(self):
        for value in ("rootfs / rootfs rw 0 0\n/dev/sda5 /mnt ext4 rw 0 0", "/dev/sda5 / ext4 rw 0 0"):
            with self.assertRaises(ValueError):
                storage.validate_mounts(value)


if __name__ == "__main__":
    unittest.main()
