import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTFS = ROOT / "userspace/rootfs"
UNITS = ROOTFS / "usr/lib/systemd/system"
LIBEXEC = ROOTFS / "usr/libexec/pico4-poc"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class CdspBootTests(unittest.TestCase):
    """an issue: the Compute DSP for PICO OS's head tracker (docs/xr.md#head-tracking)."""

    def test_restart_level_before_boot(self):
        # The kernel default SYSTEM reboots the whole headset on any DSP crash (CLAUDE.md).
        script = read(LIBEXEC / "cdsp-boot")
        related = script.index("write RELATED")
        check = script.index('= RELATED ] || {')
        boot = script.index("echo 1 > /sys/kernel/boot_cdsp/boot")
        self.assertLess(related, check)
        self.assertLess(check, boot)

    def test_unit(self):
        unit = read(UNITS / "pico4-cdsp.service")
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/libexec/pico4-poc/cdsp-boot$")
        self.assertRegex(unit, r"(?m)^ConditionPathExists=/usr/lib/firmware/cdsp\.mdt$")
        self.assertRegex(unit, r"(?m)^RemainAfterExit=yes$")


class TrackerUnitTests(unittest.TestCase):
    def test_cdsprpcd_runs_in_the_root_with_vendor_libraries(self):
        unit = read(UNITS / "pico4-cdsprpcd.service")
        self.assertRegex(unit, r"(?m)^ExecStart=/usr/libexec/pico4-poc/stock-slam --vendor /vendor/bin/cdsprpcd$")
        self.assertRegex(unit, r"(?m)^Requires=pico4-cdsp\.service$")

    def test_slamd_restarts_for_each_driver(self):
        unit = read(UNITS / "pico4-slamd.service")
        self.assertIn("/usr/libexec/pico4-poc/stock-slam /pico4/pico4-slamd", unit)
        self.assertIn("getent group video", unit)
        self.assertRegex(unit, r"(?m)^Restart=always$")
        self.assertRegex(unit, r"(?m)^Requires=pico4-cdsprpcd\.service$")
        for name in ("pico4-slamd", "pico4-cdsprpcd", "pico4-cdsp"):
            self.assertNotRegex(read(UNITS / f"{name}.service"), r"(?m)^(CPUWeight|CPUQuota)=", name)

    def test_session_and_monado_want_the_tracker(self):
        for name in ("pico4-session", "pico4-monado"):
            self.assertRegex(read(UNITS / f"{name}.service"), r"(?m)^Wants=pico4-slamd\.service$", name)


class RootRunnerTests(unittest.TestCase):
    def test_root_layout(self):
        script = read(LIBEXEC / "stock-slam")
        self.assertIn("root=/usr/lib/pico4-stock-slam", script)
        # The factory calibration where libSlam reads it, the gyro bias table included.
        self.assertIn('mount --bind "$calibration" "$root/mnt/vendor/persist/pvr"', script)
        self.assertIn('mount --bind "$calibration" "$root/mnt/vendor/pvr"', script)
        self.assertIn("unshare -m --propagation private", script)
        self.assertIn("exec env -i", script)
        self.assertIn("LD_PRELOAD=/pico4/libpico4-slam-stubs.so", script)
        self.assertIn('/usr/bin/chroot "$root" /system/bin/bootstrap/linker64 "$@"', script)
        self.assertIn("ADSP_LIBRARY_PATH=/vendor/lib/rfsa/adsp;/vendor/dsp/cdsp", script)

    def test_files_stay_lf(self):
        for path in (LIBEXEC / "stock-slam", LIBEXEC / "cdsp-boot", UNITS / "pico4-slamd.service",
                     ROOT / "scripts/extract-stock-slam.sh", ROOT / "src/stockslam/build.sh"):
            self.assertNotIn(b"\r", path.read_bytes(), path.name)


class ExtractionTests(unittest.TestCase):
    def test_extracts_what_the_tracker_needs(self):
        script = read(ROOT / "scripts/extract-stock-slam.sh")
        # libion: without it the tracker's DSP optical flow cannot allocate and 6DoF never starts.
        for name in ("libSlam.so", "libslamDSP.so", "libceres.so", "libminiglog.so", "libcdsprpc.so", "libion.so",
                     "libslamDSP_skel.so", "libpico_cv_dsp_skel.so", "libfastcvadsp.so", "libdspCV_skel.so",
                     "cdsprpcd", "libcdsp_default_listener.so", "linker64", "fastrpc_shell_3", "cdsp.mdt",
                     "cdspr.jsn", "libExposureImageProcess.so", "libtianti_shm_dynamic.so", "libc++_shared.so"):
            self.assertIn(name, script, name)
        self.assertIn("sha256 does not match the backup manifest", script)
        # an issue: the camera modules' vendor ids as PICO OS saved them, only values it trusts.
        self.assertIn("/data/stationservice/camera_otp.json", script)
        self.assertIn("all(v in (8, 9) for v in ids)", script)
        self.assertIn('"$root/pico4/camera_otp.json"', script)
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn('#define OTP_PATH "/pico4/camera_otp.json"', slamd)
        self.assertIn("isp_lsc_file(vendor)", slamd)
        # debugfs's rdump creates the directory inside its target: dump into the parent.
        self.assertIn('dump "$work/system.img" /system/etc/pvr/config "$root/system/etc/pvr"\n', script)
        self.assertIn('dump "$backup/lun4/dsp.bin" /cdsp "$root/vendor/dsp"\n', script)


class BuildTests(unittest.TestCase):
    def test_build_installs_the_root(self):
        build = read(ROOT / "scripts/build-userspace.sh")
        self.assertIn('--stock-slam) stock_slam=$(realpath "${2:?}"); shift 2 ;;', build)
        self.assertIn('install -m 0644 "$stock_slam"/firmware/cdsp* "$T/usr/lib/firmware/"', build)
        self.assertIn('bash "$project/src/stockslam/build.sh" "$island" "$island/pico4" "$llvm"', build)
        self.assertIn('mv "$island/pico4/libandroid.so" "$island/system/lib64/libandroid.so"', build)
        self.assertRegex(build, r"mkdir -p \"\$island\"/\{dev,proc,sys,data,tmp,mnt/vendor/persist/pvr,mnt/vendor/pvr\}")
        self.assertIn('"stock_slam": json.loads(stock)', build)
        # The manifest's script gets the list as its last argument.
        self.assertIn('"$wlan_json" "$audio_json" "$stock_json" >', build)
        self.assertIn("audio, stock = sys.argv[1:11]", build)

    def test_monado_recipe_ships_the_feed_header(self):
        recipe = read(ROOT / "userspace/pkgbuilds/pico4-monado/PKGBUILD")
        self.assertEqual(recipe.count("pico4_slam_feed.h"), 2)


class DriverAttachTests(unittest.TestCase):
    def test_only_a_driver_with_frames_starts_a_generation(self):
        # A second driver whose cameras fail (another one has them) must not restart the
        # working driver's tracker: the generation is bumped from the first frame only.
        source = read(ROOT / "src/monado/pico4_slam.c")
        calls = [m.start() for m in re.finditer(r"\bstock_attach\(s\);", source)]
        self.assertEqual(len(calls), 1)
        copy = source.index("stock_copy_frame(struct pico4_slam *s")
        self.assertLess(copy, calls[0])
        self.assertLess(calls[0], source.index("\n}\n", copy))


class ClockTests(unittest.TestCase):
    """timesyncd steers CLOCK_MONOTONIC against the QTimer: both mappings are taken again each second."""

    def test_offsets_follow_the_clock(self):
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        thread = hmd.index("imu_thread(void *ptr)")
        refresh = hmd.index("calibrate_clock(h);", thread)
        self.assertLess(refresh, hmd.index("\n}\n", thread))
        self.assertIn("if (now - clock_ns >= U_TIME_1S_IN_NS) {", hmd[thread:refresh])
        slam = read(ROOT / "src/monado/pico4_slam.c")
        sof = slam.index("frame_sof_ns(struct pico4_slam *s")
        body = slam[sof:slam.index("\n}\n", sof)]
        self.assertIn("if (now - s->qtimer_measured_ns >= U_TIME_1S_IN_NS) {", body)
        self.assertIn("s->qtimer_offset_ns = qtimer_offset();", body)

    def test_controller_tracker_gets_the_head_pose_of_its_exposure(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        head = slam.index("pico4_slam_get_head_pose(struct pico4_slam *s")
        self.assertIn("if (at_ns < p.ts_ns) {", slam[head:head + 900])
        self.assertIn("stock_pose_at(s, at_ns, &then)", slam[head:head + 900])


class SlamdThreadTests(unittest.TestCase):
    def test_images_on_their_own_thread(self):
        # As PICO OS: frames (checkIsSync) apart from the IMU (threadReader), so a set never holds
        # up the samples and their poses.
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        frame = slamd.index("frame_thread(void *arg)")
        self.assertIn("c->image(c->h, 0, &frames);", slamd[frame:slamd.index("\n}\n", frame)])
        self.assertIn("pthread_create(&thread, 0, frame_thread, &frames)", slamd)
        main = slamd.index("main(int argc")
        self.assertNotIn("image(h,", slamd[main:])
        self.assertIn("__atomic_store_n(&frames.imu_fed_ts, e.ts_ns, __ATOMIC_RELEASE);", slamd)


class TemperatureTests(unittest.TestCase):
    """an issue: the IMU's die temperature with every sample, as PICO OS's sensor DSP inserts it."""

    def test_driver_streams_it_into_the_feed(self):
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        # Looked up before the IMU thread owns the client, enabled with the other streams.
        lookup = hmd.index('pico4_ssc_find(h->ssc, "sensor_temperature", &h->temperature_suid, 3000)')
        self.assertLess(lookup, hmd.index("os_thread_helper_start(&h->oth, imu_thread, h)"))
        self.assertIn("pico4_ssc_enable(h->ssc, &h->temperature_suid, PICO4_TEMPERATURE_HZ)", hmd)
        # Single-value samples are taken before the three-axis check drops them.
        handle = hmd.index("handle_sample(struct pico4_hmd *h")
        self.assertLess(hmd.index("suid_equal(&s->suid, &h->temperature_suid)", handle),
                        hmd.index("if (s->count < 3)", handle))
        self.assertIn("pico4_slam_push_imu(h->slam, ts, h->last_accel_raw, s->values, h->temperature_c);", hmd)
        # Found again after a sensor DSP restart.
        recover = hmd.index("recover_imu(struct pico4_hmd *h")
        self.assertGreater(hmd.index("setup_temperature(h, 1000);", recover), recover)
        slam = read(ROOT / "src/monado/pico4_slam.c")
        self.assertIn("e->temperature = temperature_c;", slam)

    def test_slamd_passes_only_what_libslam_accepts(self):
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn("#define TEMPERATURE_MIN -30.0", slamd)
        self.assertIn("#define TEMPERATURE_MAX 100.0", slamd)
        self.assertIn("sample[6] = known ? e.temperature : DEFAULT_TEMPERATURE;", slamd)


class AutoExposureTests(unittest.TestCase):
    """an issue: PICO OS's auto exposure in pico4-slamd, the station commands in the driver."""

    def test_slamd_calls_it_as_pico_os(self):
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn('dlopen("libExposureImageProcess.so", RTLD_NOW)', slamd)
        self.assertIn('dlsym(lib, "ExposurePredict")', slamd)
        # Images after black level and lens shading, before gamma; 16-bit at the 50 Hz rate.
        self.assertIn("to_ae ? g_linear[k] : 0", slamd)
        self.assertIn("#define AE_FPS 50", slamd)
        self.assertIn("#define AE_BIT_WIDTH 16", slamd)
        # Nothing sent when it is what the set was taken with, or what was sent last.
        self.assertIn("if (!own && !same)", slamd)
        self.assertIn("__atomic_fetch_add(&feed->ae_written, 1, __ATOMIC_RELEASE);", slamd)
        self.assertIn("__atomic_store_n(&feed->ae_generation, 0, __ATOMIC_RELEASE);", slamd)
        runner = read(LIBEXEC / "stock-slam")
        self.assertIn("${PICO4_SLAMD_AE:+PICO4_SLAMD_AE=$PICO4_SLAMD_AE}", runner)
        android = read(ROOT / "src/stockslam/android.c")
        for name in ("allocate", "lock", "unlock", "release", "sendHandleToUnixSocket", "recvHandleFromUnixSocket"):
            self.assertIn(f"AHardwareBuffer_{name}(", android)

    def test_driver_sends_it_per_camera(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        self.assertIn("pico4_station_camera_set_each(s->station, scene)", slam)
        self.assertIn("s->identity.per_camera_exposure = stock_ae;", slam)
        apply = slam.index("if (stock_ae) {\n\t\t\tstock_ae_apply(s);\n\t\t} else {\n\t\t\tauto_exposure(")
        self.assertGreater(apply, 0)


class RoomTests(unittest.TestCase):
    """an issue: PICO OS's map persistence and a SteamVR universe per room."""

    def test_maps_on_the_data_partition(self):
        runner = read(LIBEXEC / "stock-slam")
        self.assertIn('mount --bind "$state/maps" "$root/data/misc/user/0/maps"', runner)
        self.assertIn('mount --bind "$state/boundary" "$root/data/misc/user/0/boundary"', runner)

    def test_slamd_saves_every_map_with_the_reference_square(self):
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn("static const double ROOM_SQUARE[4][3]", slamd)
        self.assertIn("write_boundary(SESSION_BOUNDARY, ROOM_SQUARE, 4);", slamd)
        self.assertIn("room_fit((const double(*)[3])g_seen, ROOM_SQUARE, 4,", slamd)
        # A copy left from an earlier session must not count as this one's relocalisation.
        self.assertLess(slamd.index("unlink(RELOCALISED_BOUNDARY);"), slamd.index('void *h = create("");'))
        # The clean stop that saves the map, as PICO OS's service stops the tracker.
        stop = slamd.index("prestop(h);")
        self.assertLess(stop, slamd.index('stop(h, "non_sleep");'))
        self.assertLess(slamd.index('stop(h, "non_sleep");'), slamd.index("destroy(h);"))

    def test_steamvr_gets_a_universe_per_map(self):
        patch = read(ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-room.patch")
        self.assertIn("pico4_hmd_get_universe(struct xrt_device *xdev, uint64_t *out_universe) __attribute__((weak));",
                      patch)
        self.assertIn("vr::Prop_CurrentUniverseId_Uint64", patch)
        recipe = read(ROOT / "userspace/pkgbuilds/pico4-monado/PKGBUILD")
        self.assertLess(recipe.index("< monado-steamvr-camera.patch"), recipe.index("< monado-steamvr-room.patch"))
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        self.assertIn("*out_universe = h > 1 ? h : h + 2;", hmd)  # 0 and 1 are OpenVR's


class FeedHeaderTests(unittest.TestCase):
    def test_layout_is_libc_neutral(self):
        # The same header builds against glibc (the driver) and bionic (pico4-slamd).
        header = read(ROOT / "src/monado/pico4_slam_feed.h")
        includes = re.findall(r"(?m)^#include <([^>]+)>", header)
        self.assertEqual(includes, ["stdint.h"])
        self.assertNotRegex(header, r"\b(size_t|long|int|unsigned)\b(?![0-9_])")
        self.assertIn('#define PICO4_SLAM_FEED_PATH "/dev/shm/pico4-slam"', header)


if __name__ == "__main__":
    unittest.main()
