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

    def test_cvp_related_before_the_depth_mesh(self):
        # The kernel starts the CVP's firmware when libphotoid opens /dev/cvp.
        script = read(LIBEXEC / "cdsp-boot")
        self.assertIn('if cvp=$(subsys_dir cvpss); then\n\twrite RELATED "$cvp/restart_level"', script)
        vst = read(ROOT / "src/stockslam/vst.c")
        start = vst[vst.index("\nvst_start(struct pico4_slam_feed *feed)"):]
        self.assertLess(start.index("if (!cvp_restart_related())"), start.index('dlopen("libphotoid.so"'))

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
        # From the stock firmware (PICO's public OTA), checked against its manifest first.
        self.assertIn('stock_dir "${1:-}" system.img vendor.img modem.bin dsp.bin', script)
        self.assertIn("sha256 does not match the stock manifest", read(ROOT / "scripts/stock-lib.sh"))
        # an issue: as a fresh PICO OS install, nothing of the camera modules' ids from the backup.
        self.assertNotIn("camera_otp", script)
        self.assertNotIn("userdata", script)
        # debugfs's rdump creates the directory inside its target: dump into the parent.
        self.assertIn('dump "$stock/system.img" /system/etc/pvr/config "$root/system/etc/pvr"\n', script)
        self.assertIn('dump "$stock/dsp.bin" /cdsp "$root/vendor/dsp"\n', script)


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
        self.assertIn("if (now - s->qtimer_measured_ns >= U_TIME_1S_IN_NS ||", body)
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


class CameraOtpTests(unittest.TestCase):
    """an issue: the modules' ids as PICO OS's station service gets them on a fresh install."""

    def test_saved_on_the_data_partition_where_pico_os_keeps_them(self):
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn('#define OTP_PATH "/data/stationservice/camera_otp.json"', slamd)
        runner = read(LIBEXEC / "stock-slam")
        self.assertIn('mount --bind "$state/stationservice" "$root/data/stationservice"', runner)
        # Saved only when PICO OS would keep them (all 8 or 9), and the saved ones offered first.
        self.assertIn("isp_otp_valid(ids) ? open(OTP_PATH", slamd)
        # Once saved, a driver started again while this pico4-slamd runs reads no OTP.
        self.assertIn("memcpy(feed->otp_saved, ids, sizeof(ids));", slamd)
        self.assertLess(slamd.index("memcpy(feed->otp_saved, saved, sizeof(saved));"),
                        slamd.index("load_tables(camera_vendor(feed, generation))"))
        self.assertIn("isp_lsc_file(vendor)", slamd)

    def test_driver_reads_the_otp_only_without_valid_saved_ids(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        attach = slam[slam.index("stock_attach(struct pico4_slam *s)"):]
        self.assertLess(attach.index("stock_camera_otp(s, feed);"), attach.index("__atomic_add_fetch(&feed->generation"))
        body = slam[slam.index("stock_camera_otp(struct pico4_slam *s"):]
        self.assertLess(body.index("if (otp_valid(saved))"), body.index("pico4_station_camera_otp(s->station, ids)"))


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


class SetAssemblyTests(unittest.TestCase):
    """an issue: the frames of one pulse form a set whatever exposure each camera has."""

    LINE_NS = 19069
    WINDOW_NS = 4_000_000

    def complete(self, sofs, keys):
        """The driver's assembly (pico4_cam_assembly_add) over one pulse's frames, in SOF order."""
        first = None
        have = 0
        for _, key in sorted(zip(sofs, keys)):
            if first is not None and key > first + self.WINDOW_NS:
                first, have = None, 0  # the set before is dropped
            if first is None:
                first = key
            have += 1
        return have == len(sofs)

    def test_measured_spread_forms_a_set(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        self.assertIn("return sof_ns - (((int64_t)LINE_NS * m->exposure_lines) >> 1);", slam)
        lines = [308, 737, 510, 510]
        # Measured on the headset: the sensors centre their exposures on the pulse, so each start
        # of frame comes half its exposure after it (camera 1 +4.14 ms, cameras 2 and 3 +1.95 ms).
        sofs = [l * self.LINE_NS // 2 for l in lines]
        self.assertFalse(self.complete(sofs, sofs))  # by start of frame: never a set (the stall)
        keys = [sof - (self.LINE_NS * l >> 1) for sof, l in zip(sofs, lines)]
        self.assertTrue(self.complete(sofs, keys))

    def test_both_assemblies_group_by_the_pulse(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        self.assertIn("pico4_cam_assembly_add(&s->group.assembly, index, pulse_key(ts, &meta),", slam)
        self.assertIn("pico4_cam_assembly_add(&s->leds.assembly, k, pulse_key(sof, meta),", slam)


class CaptureRecoveryTests(unittest.TestCase):
    """an issue/112: a stall is logged, cannot lock the exposure, and a stuck capture restarts."""

    def test_stall_is_logged_per_camera(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        stall = slam[slam.index("\nscene_stall(struct pico4_slam *s)"):]
        stall = stall[:stall.index("\n}\n")]
        self.assertIn('"no complete scene set for %.1f s (%llu started): %s"', stall)
        # A camera without frames while the others deliver: its link is stuck.
        self.assertIn('s->restart_why = "a camera delivers no frames";', stall)
        self.assertIn('s->restart_why = "no complete scene set for 3 s";', stall)
        # Otherwise PICO OS's per-camera exposure gives way to one for all cameras.
        self.assertIn("stock_ae_active(s)", stall)
        self.assertIn("pico4_station_camera_set_exposure(s->station, lines[n_cams / 2], gains[n_cams / 2])", stall)

    def test_camera_thread_restarts_the_capture(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        thread = slam[slam.index("\ncamera_thread(void *ptr)"):]
        thread = thread[:thread.index("\n}\n")]
        self.assertIn('why = timeouts >= 5 ? "no camera frames for 1 s" : why;', thread)
        self.assertIn("now - restarted_ns >= (int64_t)5 * U_TIME_1S_IN_NS", thread)
        self.assertIn("restart_capture(s, why);", thread)
        restart = slam[slam.index("\nrestart_capture(struct pico4_slam *s, const char *why)"):]
        restart = restart[:restart.index("\n}\n")]
        # As the start: sync off, sessions again, sync on (a second stop when it refuses), exposure.
        order = ["pico4_station_camera_stop(s->station);", "pico4_cam_close(s->cam);",
                 "pico4_cam_open(&s->cam, &s->cam_config)", "pico4_cam_start(s->cam)",
                 "pico4_station_camera_start(s->station)", "pico4_station_camera_set_exposure("]
        at = [restart.index(o) for o in order]
        self.assertEqual(at, sorted(at))
        self.assertIn("s->cam_config = cc;", slam)

    def test_overflow_free_durations(self):
        # 3 and 5 s in ns do not fit an int: the build warned and the comparisons were wrong.
        slam = read(ROOT / "src/monado/pico4_slam.c")
        self.assertNotRegex(slam, r"[^)]\b[2-9] \* U_TIME_1S_IN_NS")


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
        self.assertLess(slamd.index("unlink(RELOCALISED_BOUNDARY);"), slamd.index('void *h = t->create("");'))
        # The clean stop that saves the map, as PICO OS's service stops the tracker.
        stop = slamd.index("t->prestop(h);")
        self.assertLess(stop, slamd.index('t->stop(h, "non_sleep");'))
        self.assertLess(slamd.index('t->stop(h, "non_sleep");'), slamd.index("t->destroy(h);"))

    def test_slamd_restarts_a_lost_tracker_as_pico_os(self):
        # an issue: PICO OS's tracking service restarts a tracker whose confidence fell from 1 to 0
        # and stayed 0 for more than restart_tracking_sec (10).
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn("#define RESTART_DEFAULT_S 10", slamd)
        self.assertIn("if (p->confidence == 0 && last_confidence == 1) {", slamd)
        self.assertIn("lost_since = now_ns(CLOCK_BOOTTIME);", slamd)
        restart = slamd.index("restarting the tracker")
        self.assertLess(slamd.index("pthread_join(thread, 0);", restart), slamd.index("tracker_stop(&t, h);", restart))
        self.assertLess(slamd.index("tracker_stop(&t, h);", restart), slamd.index("h = tracker_start(&t, feed);", restart))
        self.assertIn("PICO4_SLAMD_RESTART_S", read(LIBEXEC / "stock-slam"))

    def test_steamvr_gets_a_universe_per_map(self):
        patch = read(ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-room.patch")
        self.assertIn("pico4_hmd_get_universe(struct xrt_device *xdev, uint64_t *out_universe) __attribute__((weak));",
                      patch)
        self.assertIn("vr::Prop_CurrentUniverseId_Uint64", patch)
        recipe = read(ROOT / "userspace/pkgbuilds/pico4-monado/PKGBUILD")
        self.assertLess(recipe.index("< monado-steamvr-camera.patch"), recipe.index("< monado-steamvr-room.patch"))
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        self.assertIn("*out_universe = h > 1 ? h : h + 2;", hmd)  # 0 and 1 are OpenVR's


class DepthMeshTests(unittest.TestCase):
    """PICO OS's see-through depth mesh (libphotoid) in pico4-slamd, fed as its see-through service."""

    def test_head_and_points_go_in_y_up(self):
        # libphotoid takes the head and the map points in libSlam's world turned (-y, x, z): fed
        # unchanged, a head turned right put the hemisphere below the horizon.
        vst = read(ROOT / "src/stockslam/vst.c")
        turn = vst[vst.index("\nto_y_up("):]
        turn = turn[:turn.index("\n}\n")]
        self.assertIn("v[0] = -v[1];\n\tv[1] = x;", turn)
        feed = vst[vst.index("\nvst_feed("):]
        feed = feed[:feed.index("\n}\n")]
        self.assertIn("to_y_up(position);\n\tto_y_up(orientation + 1);", feed)
        self.assertIn("f->head = head;", feed)
        points = vst[vst.index("\nvst_points("):]
        points = points[:points.index("\n}\n")]
        self.assertIn("p[0] = -p[1];\n\t\t\tp[1] = x;", points)
        self.assertIn("g_vst.points(&turned);", points)
        # The turn keeps a quaternion's rotation consistent: q' = (w, C v) is C R C^T.
        import math
        w, x, y, z = 0.82, -0.56, 0.05, -0.02
        n = math.sqrt(w * w + x * x + y * y + z * z)
        w, x, y, z = w / n, x / n, y / n, z / n

        def rot(w, x, y, z):
            return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                    [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                    [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]
        C = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        R = rot(w, x, y, z)
        CRCt = [[sum(C[i][a] * R[a][b] * C[j][b] for a in range(3) for b in range(3)) for j in range(3)]
                for i in range(3)]
        turned = rot(w, -y, x, z)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(turned[i][j], CRCt[i][j])

    def test_extracts_libphotoid_and_its_backends(self):
        script = read(ROOT / "scripts/extract-stock-slam.sh")
        for name in ("libphotoid.so", "libSNPE.so", "libhexagon_runtime.so", "libcvp2.so", "libcvp2_hfi.so",
                     "libcvp_common.so", "vst_releaseA.json.cpt", "FLNet_lite_v2", "libcvpdsp_skel.so",
                     "libhexagon_nn_skel.so", "'::/image/cvpss.*'"):
            self.assertIn(name, script, name)
        # SNPE has the DSP load its skel by the system path, as on PICO OS.
        self.assertIn("for f in libsnpe_dsp_v66_domains_v2_skel.so libsnpe_dsp_domains_v2.so; do\n"
                      '    dump "$stock/system.img" "/system/lib64/$f" "$root/system/lib64/$f"', script)

    def test_cvp_firmware_in_the_trackers_root(self):
        # The kernel looks for the CVP's image in the root of the process opening /dev/cvp.
        build = read(ROOT / "scripts/build-userspace.sh")
        self.assertIn('install -m 0644 "$stock_slam"/firmware/cvpss* "$island/lib/firmware/"', build)
        self.assertNotIn("usr/lib/firmware/cvpss", build)

    def test_slamd_feeds_it_as_pico_os(self):
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        vst = read(ROOT / "src/stockslam/vst.c")
        # libSlam's ground detection: the floor in every result, which libphotoid takes as its ground.
        self.assertIn('t->set(h, "plane", "1");', slamd)
        # The forward pair (cam3 left, cam2 right) with the tracker's result at the pair's time.
        self.assertIn("vst_feed(g_images[3], g_images[2], PICO4_SLAM_FEED_W, PICO4_SLAM_FEED_H, ts, exposure_ns, head);",
                      slamd)
        # The map points only while the tracker tracks (type 1, confidence 1).
        self.assertIn("head[0x40] == 1 && head[0x41] == 1", slamd)
        self.assertIn('dlsym(lib, "AlgorithmPointCallbackRegister")', slamd)
        # Only while the driver asks for meshes.
        self.assertIn("__atomic_load_n(&feed->mesh_wanted_ns, __ATOMIC_ACQUIRE) <", slamd)
        # The buffers before the start: the pipeline writes its first mesh without a check.
        self.assertLess(vst.index("mesh_buffer(g_vst.vertices"), vst.index("int ok = start();"))
        self.assertIn('"$src/crt.c" "$src/pico4-slamd.c" "$src/vst.c"', read(ROOT / "src/stockslam/build.sh"))

    def test_feed_mesh_matches_the_drivers_limits(self):
        header = read(ROOT / "src/monado/pico4_slam_feed.h")
        passthrough = read(ROOT / "src/monado/pico4_passthrough.h")
        self.assertIn("#define PICO4_SLAM_FEED_MESH_VERTICES 16384", header)
        self.assertIn("#define PICO4_DEPTH_MESH_VERTICES 16384", passthrough)
        self.assertIn("#define PICO4_SLAM_FEED_MESH_INDICES (6 * 127 * 127)", header)
        self.assertIn("#define PICO4_DEPTH_MESH_INDICES (6 * 127 * 127)", passthrough)
        # After the rings: the mesh slots are the largest part of the feed.
        self.assertGreater(header.index("struct pico4_slam_feed_mesh meshes["),
                           header.index("struct pico4_slam_feed_set sets["))


class FloorTests(unittest.TestCase):
    """an issue: the floor libSlam detects, to SteamVR's room setup as the Frame's driver_cv sends its own."""

    def test_slamd_passes_the_ground_with_each_pose(self):
        slamd = read(ROOT / "src/stockslam/pico4-slamd.c")
        self.assertIn("p->floor_status = (uint32_t)plane_status;", slamd)
        self.assertIn("p->floor_height = plane_height;", slamd)
        self.assertLess(slamd.index("p->floor_height = plane_height;"),
                        slamd.index("__atomic_store_n(&feed->poses_written, w + 1, __ATOMIC_RELEASE);"))

    def test_driver_takes_only_a_detected_floor_below_the_origin(self):
        slam = read(ROOT / "src/monado/pico4_slam.c")
        floor = slam[slam.index("\npico4_slam_floor(struct pico4_slam *s, float *out_y)\n{\n\tstruct"):]
        floor = floor[:floor.index("\n}\n")]
        self.assertIn("p.floor_status != 1", floor)  # 2 is the initial estimate (1.13 m on the desk)
        self.assertIn("double y = -p.floor_height;", floor)
        self.assertIn("y += feed->room_t[0];", floor)
        hmd = read(ROOT / "src/monado/pico4_hmd.c")
        self.assertIn("math_pose_transform_point(&offset, &floor, &point);", hmd)

    def test_room_setup_steps_as_driver_cv(self):
        recipe = read(ROOT / "userspace/pkgbuilds/pico4-monado/PKGBUILD")
        self.assertLess(recipe.index("< monado-steamvr-roomview-mesh.patch"), recipe.index("< monado-steamvr-floor.patch"))
        patch = read(ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-floor.patch")
        self.assertIn("static_cast<vr::EVREventType>(809)", patch)
        self.assertIn("SetFloorDetection(step == 3 || step == 6 || step == 7);", patch)
        self.assertIn('GetGenericInterface("IVRServerInternal_XXX", &err)', patch)
        self.assertIn("kFloorDetectionSlot = 40;", patch)
        self.assertIn("ReadPathBatch(kChaperoneRoot, &read, 1)", patch)
        mesh = read(ROOT / "userspace/pkgbuilds/pico4-monado/monado-steamvr-roomview-mesh.patch")
        self.assertIn("kChaperoneRoot = 0x0000000600000003ull;", mesh)
        self.assertIn('"/chaperone/room_setup/transform"', patch)
        self.assertIn("read.tag != vr::k_unHmdMatrix34PropertyTag", patch)
        self.assertIn("m.m[1][1] * y + m.m[1][3]", patch)
        # Off: NaN clears the compositor's value.
        off = patch[patch.index("ovrd_log(\"Room setup: floor detection %s\\n\""):]
        self.assertIn("SendFloor(NAN);", off[:off.index("m_floor_sent = NAN;")])


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
