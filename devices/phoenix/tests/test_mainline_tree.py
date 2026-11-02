"""The PICO 4's tree and configuration for Valve's kernel: battery, audio and the cameras."""
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


class AudioCalibrationTests(unittest.TestCase):
    PATCH = KERNEL / "patches/0013-asoc-qcom-q6-pico-os-audio-calibration.patch"

    def test_core_commands_are_the_stock_drivers(self):
        # Opcodes as PICO's audio_q6.ko builds them (q6core_set_cal, q6core_map_memory_regions).
        patch = text(self.PATCH)
        for define in ("AVCS_CMD_SHARED_MEM_MAP_REGIONS\t\t0x00012924", "AVCS_CMD_REGISTER_TOPOLOGIES\t\t0x00012923",
                       "AVCS_CMD_DEREGISTER_TOPOLOGIES\t\t0x0001292a", "ADM_CMD_SET_PP_PARAMS_V6\t0x0001035d"):
            self.assertIn(define, patch)

    def test_routing_opens_with_the_paths_topology_and_falls_back(self):
        added = "\n".join(line[1:] for line in text(self.PATCH).splitlines() if line.startswith("+"))
        self.assertIn("topology = q6calibration_topology(afe_port, session->path_type);", added)
        self.assertIn("topology = NULL_COPP_TOPOLOGY;", added)
        self.assertIn("q6calibration_apply(routing_data->dev, copp, afe_port, session->path_type);", added)
        # The driver is built with the routing driver.
        self.assertIn("+obj-$(CONFIG_SND_SOC_QDSP6_ROUTING) += q6routing.o q6calibration-pico4.o", text(self.PATCH))

    def test_tree_gives_the_driver_the_audio_smmu_stream(self):
        node = re.search(r"&q6adm \{\s*calibration \{(.*?)\};", tree(), re.S).group(1)
        driver = text(KERNEL / "src/sound/soc/qcom/qdsp6/q6calibration-pico4.c")
        compatible = re.search(r'\.compatible = "([^"]+)"', driver).group(1)
        self.assertIn(f'compatible = "{compatible}";', node)
        # The stream q6asm-dai's buffers use (sm8250.dtsi), which the ADSP reads them through.
        self.assertIn("iommus = <&apps_smmu 0x1801 0x0>;", node)


class CameraTests(unittest.TestCase):
    DRIVER = KERNEL / "src/drivers/media/i2c/pico4-ov7251.c"

    def sensors(self):
        return re.findall(r"camera-([0-3]) \{(.*?)\n\t\};", tree(), re.S)

    def test_sensors_are_no_i2c_clients(self):
        # The station owns their control port: the stock kernel never talks to them either.
        self.assertNotIn("ovti,ov7251", tree())
        self.assertIn("CONFIG_VIDEO_PICO4_OV7251=y", config())
        self.assertNotIn("CONFIG_VIDEO_OV7251=y", config())
        compatible = re.search(r'\.compatible = "([^"]+)"', text(self.DRIVER)).group(1)
        for slot, sensor in self.sensors():
            self.assertIn(f'compatible = "{compatible}";', sensor)
            self.assertIn(f"pico,camera-slot = <{slot}>;", sensor)

    def test_sensors_hold_the_camera_gdsc(self):
        sensors = self.sensors()
        self.assertEqual(len(sensors), 4)
        for _, sensor in sensors:
            self.assertIn("power-domains = <&camcc TITAN_TOP_GDSC>;", sensor)
            self.assertIn("standby-supply = <&cam_standby>;", sensor)
            self.assertIn("reset-supply = <&cam_reset>;", sensor)

    def test_stock_wiring(self):
        # Each slot's MCLK and CSIPHY endpoint, as the stock overlay's cells 0-3.
        wiring = {"0": ("3", "csiphy5_ep"), "1": ("4", "csiphy3_cam1_ep"), "2": ("2", "csiphy3_cam2_ep"),
                  "3": ("5", "csiphy4_ep")}
        for slot, sensor in self.sensors():
            mclk, remote = wiring[slot]
            self.assertIn(f"clocks = <&camcc CAM_CC_MCLK{mclk}_CLK>;", sensor)
            self.assertIn(f"remote-endpoint = <&{remote}>;", sensor)

    def test_combo_phy_uses_the_stock_lanes(self):
        # On CSIPHY 3: one sensor on data lane 0 with the clock lane, the other on lane 2, lane 3 its clock.
        t = tree()
        first = re.search(r"csiphy3_cam1_ep: endpoint@0 \{(.*?)\};", t, re.S).group(1)
        second = re.search(r"csiphy3_cam2_ep: endpoint@1 \{(.*?)\};", t, re.S).group(1)
        self.assertIn("data-lanes = <0>;", first)
        self.assertIn("clock-lanes = <3>;", second)
        self.assertIn("data-lanes = <2>;", second)

    def test_camss_has_sm8250s_phy_tables(self):
        patch = text(KERNEL / "patches/0015-media-camss-sm8250-phy-settings-and-combo-mode.patch")
        self.assertIn("csiphy_lane_regs lane_regs_sm8250_combo[] = {", patch)
        self.assertIn("version == CAMSS_8250 ? 0x02 : 0x7a", patch)
        self.assertIn("+\t\t} else if (csiphy->combo_subdev == sd) {", patch)

    def test_ccis_run_at_the_stock_rate(self):
        t = tree()
        for n in (0, 1):
            cci = re.search(rf"&cci{n} \{{(.*?)\}};", t, re.S).group(1)
            self.assertIn(f"assigned-clocks = <&camcc CAM_CC_CCI_{n}_CLK_SRC>;", cci)
            self.assertIn("assigned-clock-rates = <37500000>;", cci)


class UnalignedAtomicFixupTests(unittest.TestCase):
    PATCH = KERNEL / "patches/0021-arm64-the-unaligned-atomic-fixups-128-bit-load-survives-a-read-only-page.patch"

    def test_the_store_of_the_128_bit_load_has_its_exception_table_entry(self):
        patch = text(self.PATCH)
        self.assertIn('+               "2: stlxp %w[Tmp], %[ResultLower], %[ResultUpper], [%[Addr]]\\n"', patch)
        self.assertIn("+               _ASM_EXTABLE_UACCESS_ERR(2b, 3b, %w[ret])", patch)


class VenusCaptureFormatTests(unittest.TestCase):
    PATCH = KERNEL / "patches/0020-media-venus-compressed-capture-formats-as-iris-reports-them.patch"

    def test_qc10c_reports_its_real_pitch(self):
        self.assertIn("+\t\t\tpfmt[0].bytesperline = ALIGN(stride * 4 / 3, 256);", text(self.PATCH))

    def test_the_sizes_hold_only_the_picture(self):
        """Steam Link's dma-bufs are as large as the picture: a margin makes vb2 refuse them."""
        patch = text(self.PATCH)
        self.assertIn("-\tsize += max(extradata + SZ_8K, y_stride * 48);", patch)
        self.assertIn("+\treturn ALIGN(y_meta_plane + y_plane + uv_meta_plane + uv_plane, SZ_4K);", patch)


class GpuPreemptionTests(unittest.TestCase):
    PATCH = KERNEL / "patches/0019-drm-msm-a6xx-ringbuffer-preemption-on-the-a650.patch"

    def test_the_firmware_gets_kgsls_list_at_its_init(self):
        patch = text(self.PATCH)
        self.assertIn("+\tOUT_PKT7(ring, CP_ME_INIT, 11);", patch)
        self.assertIn("+\tOUT_RING(ring, 0x0000016f);", patch)
        self.assertIn("+\tlock->list_offset = ifpc->count * 2;", patch)

    def test_a_preemption_restores_the_blocks_addressing(self):
        patch = text(self.PATCH)
        list_ = patch[patch.index("+static const u32 a650_pwrup_reglist_regs[] = {"):]
        list_ = list_[:list_.index("+};")]
        for block in ("VSC", "GRAS", "RB", "PC", "HLSQ", "VFD", "VPC", "UCHE", "SP", "TPL1"):
            self.assertIn(f"REG_A6XX_{block}_ADDR_MODE_CNTL,", list_)
        self.assertEqual(list_.count("+\tREG_"), 35)  # KGSL's 28 and the A650's 7

    def test_on_by_default_for_the_a650(self):
        self.assertIn("+\t\t\tADRENO_QUIRK_PREEMPTION,", text(self.PATCH))


class RgbCameraTests(unittest.TestCase):
    DRIVER = KERNEL / "src/drivers/media/i2c/pico4-imx471.c"
    CAM = ROOT / "src/monado/pico4_cam.c"

    def node(self) -> str:
        return re.search(r"camera@1a \{(.*?)\n\t\};", tree(), re.S).group(1)

    def test_driver_is_built_in_and_matches_the_tree(self):
        self.assertIn("CONFIG_VIDEO_PICO4_IMX471=y", config())
        compatible = re.search(r'\.compatible = "([^"]+)"', text(self.DRIVER)).group(1)
        self.assertIn(f'compatible = "{compatible}";', self.node())

    def test_wired_as_picos_board_overlay(self):
        node = self.node()
        self.assertIn("&cci0_i2c0 {", tree())
        self.assertIn("reg = <0x1a>;", node)  # PICO's module: 0x34, in 8 bits
        self.assertIn("CAM_CC_MCLK1_CLK", node)
        self.assertIn("reset-gpios = <&tlmm 23 ", node)
        self.assertIn("standby-gpios = <&tlmm 114 ", node)
        self.assertIn("TITAN_TOP_GDSC", node)
        self.assertIn("data-lanes = <0 1 2 3>;", tree())

    def test_supplies_are_steps_of_their_rails(self):
        # A voltage between the 8 mV steps takes the PMIC's whole regulator node down, with Wi-Fi,
        # audio and the station's power.
        for label, microvolts in (("vreg_l14a_1p8", 1800000), ("vreg_l2c_1p08", 1080000)):
            node = re.search(label + r": ldo\d+ \{(.*?)\};", tree(), re.S).group(1)
            self.assertIn(f"regulator-min-microvolt = <{microvolts}>;", node)
            self.assertIn(f"regulator-max-microvolt = <{microvolts}>;", node)
            self.assertEqual(microvolts % 8000, 0)

    def test_the_camera_noc_is_above_its_boot_rate(self):
        # CAMSS never sets it: at 19.2 MHz the RGB camera's IFE-lite overflows on every frame.
        camss = re.search(r"&camss \{(.*?)ports \{", tree(), re.S).group(1)
        self.assertIn("assigned-clocks = <&camcc CAM_CC_CAMNOC_AXI_CLK_SRC>;", camss)
        self.assertIn("assigned-clock-rates = <300000000>;", camss)

    def test_a_missing_sensor_fails_only_its_stream(self):
        # A probe error would keep CAMSS's notifier, and the tracking cameras, from completing.
        driver = text(self.DRIVER)
        probe = driver[driver.index("static int pico4_imx471_probe"):driver.index("static void pico4_imx471_remove")]
        self.assertNotIn("IMX471_REG_CHIP_ID", probe)
        start = driver[driver.index("static int pico4_imx471_start"):driver.index("static int pico4_imx471_s_stream")]
        self.assertIn("IMX471_REG_CHIP_ID", start)

    def test_the_third_lite_pair(self):
        patch = text(KERNEL / "patches/0017-media-camss-the-rgb-cameras-path-on-a-third-ife-lite.patch")
        for line in ('.reg = { "vfe_lite2" }', '.interrupt = { "csid4" }', "0x0acdd400", "GIC_SPI 449", "GIC_SPI 266"):
            self.assertIn(line, patch)

    def test_driver_takes_the_csids_16_bit_samples(self):
        patch = text(KERNEL / "patches/0018-media-camss-16-bit-samples-from-an-rdi.patch")
        self.assertIn("PLAIN_FORMAT_PLAIN16 << RDI_CFG0_PLAIN_FORMAT", patch)
        self.assertIn("{ MEDIA_BUS_FMT_SRGGB16_1X16, 16, V4L2_PIX_FMT_SRGGB10, 1,", patch)
        cam = text(self.CAM)
        self.assertIn("{1, 1, 4, 4}, // the RGB camera", cam)
        self.assertIn("#define V4L_MBUS_SRGGB16 0x3020", cam)
        self.assertIn("rgb ? V4L2_PIX_FMT_SRGGB10 : V4L2_PIX_FMT_Y10P", cam)

    def test_exposure_range_and_paused_control_agree(self):
        driver = text(self.DRIVER)
        self.assertIn("#define V4L2_CID_PICO4_PAUSED\t(V4L2_CID_USER_BASE | 0x1000)", driver)
        self.assertIn("#define V4L_CID_RGB_PAUSED (V4L2_CID_USER_BASE | 0x1000)", text(self.CAM))
        self.assertIn("#define IMX471_MAX_LINES\t4510", driver)
        self.assertIn("#define PICO4_CAM_RGB_MAX_LINES 4510", text(ROOT / "src/monado/pico4_cam.h"))


class FastRpcTests(unittest.TestCase):
    def test_context_banks_are_coherent_as_on_stock(self):
        # The stock tree's banks of all three DSPs; the tracker loses its features without it.
        t = tree()
        for base, banks in (("5c00000", (1, 2, 3)), ("8300000", range(1, 9)), ("17300000", (3, 4, 5))):
            for cb in banks:
                node = rf"&\{{/soc@0/remoteproc@{base}/glink-edge/fastrpc/compute-cb@{cb}\}} \{{\s*dma-coherent;\s*\}};"
                self.assertRegex(t, node)

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
