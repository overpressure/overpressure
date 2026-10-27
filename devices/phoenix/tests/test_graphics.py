import copy
import unittest
from devices.phoenix.tooling.graphics import validate_kms, validate_vulkan


class GraphicsEvidenceTests(unittest.TestCase):
    def test_complete_measured_modes(self):
        validate_kms(self.kms(), 10)

    @staticmethod
    def kms():
        return dict(schema=2, status="passed", presentation="page_flip", retire_fence=True,
            composition="rgb_layers", buffer_layout="combined", eye_routing="sspp_layout", width=4320, height=2160, modes=[
            dict(requested_hz=h, vblank_hz=float(h), completion_hz=float(h), flips=h*10,
                 vblank_samples=h*10, vblank_sequence_gaps=0, status="passed") for h in (90, 72)])

    def test_mode_names_or_wrong_clock_math_are_not_refresh_measurements(self):
        for measured in (None, "90", float("nan"), float("inf"), 32.83, 26.27, 60):
            for field in ("vblank_hz", "completion_hz"):
                value=self.kms();value["modes"][0][field]=measured
                with self.subTest(measured=measured,field=field), self.assertRaises(ValueError): validate_kms(value,10)

    def test_short_runs_dropped_flips_and_partial_modes_fail(self):
        for key,value in (("flips",1),("flips","900"),("vblank_samples",1),("vblank_sequence_gaps",1),("vblank_sequence_gaps",None),("status","failed")):
            evidence=self.kms();evidence["modes"][0][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_kms(evidence,10)
        for modes in ([],None,[{}],self.kms()["modes"][::-1]):
            evidence=self.kms();evidence["modes"]=modes
            with self.assertRaises(ValueError): validate_kms(evidence,10)

    def test_zero_vendor_flip_timestamps_are_not_invented_drops(self):
        evidence=self.kms()
        for mode in evidence["modes"]:
            mode.update(page_flip_hz=None,page_flip_sequence_gaps=None)
        validate_kms(evidence,10)  # Separate kernel vblank evidence is still required.
        evidence["modes"][0]["vblank_hz"]=None
        with self.assertRaises(ValueError): validate_kms(evidence,10)

    def test_static_and_repeated_buffer_diagnostics_cannot_pass_acceptance(self):
        for presentation in (None, "static", "repeat_buffer"):
            evidence=self.kms();evidence["presentation"]=presentation
            with self.subTest(presentation=presentation), self.assertRaises(ValueError):validate_kms(evidence,10)

    def test_timing_alone_does_not_accept_the_visually_broken_commit_path(self):
        for field,value in (("retire_fence",False),("retire_fence",None),("retire_fence",1),
                            ("composition","opaque"),("buffer_layout","per_eye"),("eye_routing",None)):
            evidence=self.kms();evidence[field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):validate_kms(evidence,10)

    def test_hardware_render_requires_complete_pixel_verification(self):
        evidence=dict(status="passed",driver="turnip",width=256,height=256,verified_pixels=65536,bad_pixels=0)
        validate_vulkan(evidence)
        for key,value in (("driver","lavapipe"),("status","failed"),("verified_pixels",0),("bad_pixels",1)):
            broken=copy.deepcopy(evidence);broken[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_vulkan(broken)

    def test_missing_evidence_never_passes(self):
        for value in ({},None,[],"passed"):
            with self.assertRaises(ValueError):validate_vulkan(value)
            with self.assertRaises(ValueError):validate_kms(value,10)
