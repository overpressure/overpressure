import copy
import unittest
from devices.phoenix.tooling.graphics import validate_vulkan


class GraphicsEvidenceTests(unittest.TestCase):
    def test_hardware_render_requires_complete_pixel_verification(self):
        evidence=dict(status="passed",driver="turnip",width=256,height=256,verified_pixels=65536,bad_pixels=0)
        validate_vulkan(evidence)
        for key,value in (("driver","lavapipe"),("status","failed"),("verified_pixels",0),("bad_pixels",1)):
            broken=copy.deepcopy(evidence);broken[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_vulkan(broken)

    def test_missing_evidence_never_passes(self):
        for value in ({},None,[],"passed"):
            with self.assertRaises(ValueError):validate_vulkan(value)


if __name__ == "__main__":
    unittest.main()
