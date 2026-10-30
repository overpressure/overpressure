"""The PICO 4's tree and configuration for Valve's kernel: battery, audio and the cameras' clocks."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "kernel"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def tree() -> str:
    return text(KERNEL / "sm8250-pico4-phoenix.dts")


def config() -> str:
    return text(KERNEL / "pico4.config")


class BatteryTests(unittest.TestCase):
    DRIVER = KERNEL / "src/drivers/power/supply/pico4-pm8150b-battery.c"

    def test_driver_is_built_in_and_matches_the_tree(self):
        self.assertIn("CONFIG_BATTERY_PICO4_PM8150B=y", config())
        self.assertIn('#include "pm8150b.dtsi"', tree())
        compatible = re.search(r'\.compatible = "([^"]+)"', text(self.DRIVER)).group(1)
        node = re.search(r"battery@4000 \{(.*?)\};", tree(), re.S).group(1)
        self.assertIn(f'compatible = "{compatible}";', node)
        # The fuel gauge's SOC block, the charger and the USB input, in the driver's order.
        self.assertIn("reg = <0x4000>, <0x1000>, <0x1300>;", node)

    def test_patch_builds_the_drivers_object(self):
        patch = text(KERNEL / "patches/0008-power-supply-build-the-pico4-pm8150b-battery-driver.patch")
        self.assertIn("+config BATTERY_PICO4_PM8150B", patch)
        self.assertIn("+obj-$(CONFIG_BATTERY_PICO4_PM8150B)\t+= pico4-pm8150b-battery.o", patch)

    def test_supplies_have_the_stock_kernels_names(self):
        # Monado's driver and UPower read /sys/class/power_supply/battery, as on the stock kernel.
        source = text(self.DRIVER)
        self.assertIn('.name = "battery"', source)
        self.assertIn('.name = "usb"', source)
        monado = text(ROOT / "src/monado/pico4_hmd.c")
        self.assertIn('"/sys/class/power_supply/battery"', monado)

    def test_capacity_scales_as_the_stock_fuel_gauge(self):
        # Full and empty as they are, everything between over 1-99 (raw values 1-254).
        def capacity(raw: int) -> int:
            if raw >= 255:
                return 100
            if raw == 0:
                return 0
            return ((raw - 1) * 98 + (253 // 2)) // 253 + 1

        self.assertEqual([capacity(r) for r in (0, 1, 128, 254, 255)], [0, 1, 50, 99, 100])
        self.assertIn("DIV_ROUND_CLOSEST((raw - 1) * 98, FULL_SOC_RAW - 2) + 1", text(self.DRIVER))


class AudioTreeTests(unittest.TestCase):
    def test_kernel_has_the_cards_drivers(self):
        for option in ("CONFIG_SND_SOC_SM8250=y", "CONFIG_SND_SOC_WSA881X=y", "CONFIG_SND_SOC_WCD938X_SDW=y",
                       "CONFIG_PINCTRL_SM8250_LPASS_LPI=y"):
            self.assertIn(option, config())

    def test_speakers_are_the_stock_units(self):
        t = tree()
        # The stock 0x1020170211 (right, shutdown GPIO 127) and 0x1020170212 (left, GPIO 129).
        right = re.search(r"right_spkr: speaker@0,1 \{(.*?)\};", t, re.S).group(1)
        left = re.search(r"left_spkr: speaker@0,2 \{(.*?)\};", t, re.S).group(1)
        for node, gpio, prefix in ((right, 127, "SpkrRight"), (left, 129, "SpkrLeft")):
            self.assertIn('compatible = "sdw10217201000";', node)
            self.assertIn(f"powerdown-gpios = <&tlmm {gpio} GPIO_ACTIVE_LOW>;", node)
            self.assertIn(f'sound-name-prefix = "{prefix}";', node)

    def test_codec_has_the_stock_supplies_and_bias(self):
        t = tree()
        codec = re.search(r"wcd938x: audio-codec \{(.*?)\n\t\};", t, re.S).group(1)
        self.assertIn("reset-gpios = <&tlmm 32 GPIO_ACTIVE_LOW>;", codec)
        self.assertIn("vdd-mic-bias-supply = <&vreg_bob>;", codec)
        self.assertEqual(codec.count("-microvolt = <2700000>;"), 4)
        self.assertIn("wcd938x_rx: codec@0,4", t)
        self.assertIn("wcd938x_tx: codec@0,3", t)

    def test_audio_process_gets_the_stock_remote_heap(self):
        t = tree()
        heap = re.search(r"adsp_rpc_heap: adsp-rpc-heap \{(.*?)\};", t, re.S).group(1)
        self.assertIn("size = <0x0 0xc00000>;", heap)
        self.assertIn("reusable;", heap)
        fastrpc = re.search(r"&\{/soc@0/remoteproc@17300000/glink-edge/fastrpc\} \{(.*?)\};", t, re.S).group(1)
        self.assertIn("memory-region = <&adsp_rpc_heap>;", fastrpc)
        # The stock tree's qcom,adsp-remoteheap-vmid = <0x16 0x25>.
        self.assertIn("qcom,vmids = <QCOM_SCM_VMID_LPASS QCOM_SCM_VMID_ADSP_HEAP>;", fastrpc)


class CameraClockTests(unittest.TestCase):
    def test_sensors_hold_the_camera_gdsc(self):
        t = tree()
        sensors = re.findall(r"camera@7[1-4] \{(.*?)\n\t\};", t, re.S)
        self.assertEqual(len(sensors), 4)
        for sensor in sensors:
            self.assertIn("power-domains = <&camcc TITAN_TOP_GDSC>;", sensor)

    def test_ccis_run_at_the_stock_rate(self):
        t = tree()
        for n in (0, 1):
            cci = re.search(rf"&cci{n} \{{(.*?)\}};", t, re.S).group(1)
            self.assertIn(f"assigned-clocks = <&camcc CAM_CC_CCI_{n}_CLK_SRC>;", cci)
            self.assertIn("assigned-clock-rates = <37500000>;", cci)


class FastRpcTests(unittest.TestCase):
    PATCH = KERNEL / "patches/0011-misc-fastrpc-resume-an-interrupted-invocation.patch"

    def test_interrupted_call_is_resumed_not_sent_again(self):
        patch = text(self.PATCH)
        added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+"))
        # Marked on -ERESTARTSYS, found again by the same thread, handle and scalars.
        self.assertIn("ctx->interrupted = true;", added)
        self.assertIn("ctx->pid != current->pid", added)
        self.assertIn("ctx->msg.handle == handle && ctx->sc == sc", added)
        # The resumed context waits without a second send, on the restarted call's arguments.
        resume = added[added.index("ctx = fastrpc_context_resume"):]
        self.assertLess(resume.index("ctx->args = args;"), resume.index("goto wait;"))
        self.assertIn("wait:\n\tif (kernel) {", patch.replace("\n+", "\n").replace("\n ", "\n"))

    def test_client_buffer_mmap_uses_the_remote_mmap_method(self):
        patch = text(KERNEL / "patches/0012-misc-fastrpc-map-a-clients-buffer-through-mmap.patch")
        added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+"))
        # The client's DMA-BUF at its address, with its flags, as the vendor driver sends it.
        self.assertIn("fastrpc_map_create(fl, req->fd, req->size, 0, &map);", added)
        self.assertIn("req_msg.flags = req->flags;\n\treq_msg.vaddr = req->vaddrin;", added)
        self.assertIn("FASTRPC_SCALARS(FASTRPC_RMID_INIT_MMAP, 2, 1)", added)
        self.assertIn("FASTRPC_SCALARS(FASTRPC_RMID_INIT_MUNMAP, 1, 0)", added)
        # Only a request with a descriptor; the driver's own allocations stay as they were.
        self.assertIn("if (req.fd < 0) {", added)


if __name__ == "__main__":
    unittest.main()
