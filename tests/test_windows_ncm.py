import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from pico4.windows_ncm import initialize


class NcmTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Mock(bConfigurationValue=1)
        self.control = SimpleNamespace(bInterfaceClass=2, bInterfaceSubClass=13, bInterfaceNumber=2)
        self.cfg.__iter__ = Mock(return_value=iter([self.control]))
        self.dev = Mock(serial_number="POC123", bNumConfigurations=1)
        self.dev.__getitem__ = Mock(return_value=self.cfg)
        self.dev.ctrl_transfer.return_value = 0
        self.dispose = Mock()

    def test_initializes_selected_control_interface_and_releases_device(self):
        result = initialize([self.dev], "POC123", self.dispose)
        self.assertEqual(result["serial"], "POC123")
        self.assertEqual(result["status"], "initialized")
        self.dev.set_configuration.assert_called_once_with(1)
        self.dev.ctrl_transfer.assert_called_once_with(0x21, 0x8a, 0, 2, b"", timeout=3000)
        self.dispose.assert_called_once_with(self.dev)

    def test_wrong_serial_receives_no_configuration_or_control(self):
        with self.assertRaisesRegex(ValueError, "serial mismatch"):
            initialize([self.dev], "OTHER", self.dispose)
        self.dev.set_configuration.assert_not_called()
        self.dev.ctrl_transfer.assert_not_called()
        self.dispose.assert_called_once_with(self.dev)

    def test_absent_or_ambiguous_device_is_not_opened(self):
        for devices in ([], [self.dev, self.dev]):
            with self.assertRaisesRegex(ValueError, "exactly one POC"):
                initialize(devices, "POC123", self.dispose)
        self.dev.set_configuration.assert_not_called()
        self.dispose.assert_not_called()

    def test_wrong_configuration_count_is_rejected_before_requests(self):
        self.dev.bNumConfigurations = 2
        with self.assertRaisesRegex(ValueError, "configuration count"):
            initialize([self.dev], "POC123", self.dispose)
        self.dev.ctrl_transfer.assert_not_called()
        self.dev.set_configuration.assert_not_called()

    def test_missing_or_ambiguous_ncm_interface_is_rejected(self):
        for interfaces in ([], [self.control, self.control]):
            self.cfg.__iter__.return_value = iter(interfaces)
            with self.assertRaisesRegex(ValueError, "NCM control interface"):
                initialize([self.dev], "POC123", self.dispose)
        self.dev.set_configuration.assert_not_called()

    def test_transfer_failure_still_releases_device(self):
        self.dev.ctrl_transfer.side_effect = OSError("USB timeout")
        with self.assertRaises(OSError):
            initialize([self.dev], "POC123", self.dispose)
        self.dispose.assert_called_once_with(self.dev)

    def test_unexpected_response_is_not_success(self):
        self.dev.ctrl_transfer.return_value = 1
        with self.assertRaisesRegex(ValueError, "did not complete"):
            initialize([self.dev], "POC123", self.dispose)
        self.dispose.assert_called_once_with(self.dev)


if __name__ == "__main__":
    unittest.main()
