// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: MIT
/*
 * pico4-slamd: PICO OS's head tracker (libSlam.so, its front end on the Compute DSP) for the
 * PICO 4 driver, in the tracker's bionic root (docs/xr.md#head-tracking).
 *
 *   pico4-slamd [GID]   (in the root, through /usr/libexec/pico4-poc/stock-slam)
 *
 * Creates the shared memory of pico4_slam_feed.h (group GID, mode 0660) and waits for the
 * driver. Each driver generation gets its own tracker: when the driver starts again, the process
 * exits and systemd starts a fresh one. The tracker is fed as PICO OS's tracking service feeds
 * it, from two threads as there: every IMU sample {gyro, accel, temperature} with its time, and
 * the pose after it; each frame set, once the IMU is past it (the camera's latency does that on
 * PICO OS), as four 640x480 8-bit images after the stock image processing (isp.h), with the
 * earliest exposure middle for all four; then start6DOF. Times are CLOCK_MONOTONIC ns throughout.
 * The lens-shading table follows the camera modules' vendor, as on PICO OS (lsc_table);
 * PICO4_SLAMD_LSC names another.
 */
#include "bionic.h"
#include "isp.h"
#include "room.h"
#include "pico4_slam_feed.h"

#include <stdbool.h>

#define CONFIG_DIR "/system/etc/pvr/config/"
//! The camera modules' vendor ids, as PICO OS's station service saved them (extract-stock-slam.sh).
#define OTP_PATH "/pico4/camera_otp.json"
//! A set goes in once the IMU is this far past it.
#define SET_DELAY_NS 30000000
//! Temperature for samples without one: the gyro bias table's middle.
#define DEFAULT_TEMPERATURE 30.0
//! The range libSlam accepts as a temperature (°C); outside it the tracker ignores the value.
#define TEMPERATURE_MIN -30.0
#define TEMPERATURE_MAX 100.0

struct vec
{
	void *begin, *end, *cap; // libc++ std::vector
};
typedef void *(*create_fn)(const char *);
typedef int (*set_fn)(void *, const char *, const char *);
typedef int (*start_fn)(void *, const char *);
typedef void (*image_fn)(void *, int, const struct vec *);
typedef void (*imu_fn)(void *, const struct vec *, int64_t);
typedef int (*result_fn)(void *, void *);
typedef void (*register_fn)(void (*)(void));
typedef void (*prestop_fn)(void *);
typedef int (*stop_fn)(void *, const char *);
typedef void (*destroy_fn)(void *);
typedef int (*relocinfo_fn)(void *, void *);

/*
 * The room (docs/xr.md#room-setup), where PICO OS's tracker keeps it: /data/misc/user/0, bound
 * from the data partition by stock-slam. The tracker saves its map at the stop only with a
 * boundary in stdata.txt (in this session's world), and after relocalising in a saved room it
 * writes that room's boundary in this session's world to stdataforalgorithm.txt. Every map here
 * is saved with the same reference square in its own world (ROOM_SQUARE): the tracker's copy of
 * it after a relocalisation then gives the transform from this session's world to the room's,
 * whichever saved map it found. The play area itself is SteamVR's, kept per room's universe.
 */
#define BOUNDARY_DIR "/data/misc/user/0/boundary/"
#define SESSION_BOUNDARY BOUNDARY_DIR "stdata.txt"
#define RELOCALISED_BOUNDARY BOUNDARY_DIR "stdataforalgorithm.txt"
//! PICO OS's tracker tries to relocalise for 8 s after start6DOF.
#define RELOCALISE_NS 10000000000LL
//! The reference square, 2 m wide, 1.5 m below the start (tracker world: x up).
static const double ROOM_SQUARE[4][3] = {{-1.5, -1, -1}, {-1.5, 1, -1}, {-1.5, 1, 1}, {-1.5, -1, 1}};

static volatile int g_stop;
static char g_text[4 << 20];
static long
read_text(const char *path);

static void
on_stop_signal(int sig)
{
	(void)sig;
	g_stop = 1;
}

static double g_seen[ROOM_MAX_POINTS][3];

//! Writes a boundary file (tracker-world points), atomically; 0 or -1.
static int
write_boundary(const char *path, const double (*pts)[3], int n)
{
	static char text[ROOM_MAX_POINTS * 64 + 64];
	int len = snprintf(text, sizeof(text), "isSaveMap 1\nvertexcount %d\n", 2 * n);
	for (int i = 0; i < n && len < (int)sizeof(text) - 64; i++) {
		double abc[3];
		room_to_file(pts[i], abc);
		len += snprintf(text + len, sizeof(text) - (size_t)len, "(%f, %f, %f)\n", abc[0], abc[1], abc[2]);
	}
	char tmp[128];
	snprintf(tmp, sizeof(tmp), "%s.new", path);
	int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
	if (fd < 0) {
		return -1;
	}
	int ok = write(fd, text, (size_t)len) == len;
	close(fd);
	return ok && rename(tmp, path) == 0 ? 0 : -1;
}

//! A boundary file's points (tracker world); their count, or -1.
static int
read_boundary(const char *path, double (*out)[3])
{
	long len = read_text(path);
	return len > 0 ? room_parse(g_text, len, out, ROOM_MAX_POINTS) : -1;
}

/*!
 * The room after start6DOF, once PICO OS's tracker has had its time to relocalise: in a saved room
 * the transform to its world, and the square again in this session's world so the stop saves the
 * map with it; else a new room, whose map is saved with the square as it is.
 */
static void
settle_room(struct pico4_slam_feed *feed, uint32_t generation, void *h, relocinfo_fn relocinfo)
{
	int seen_n = read_boundary(RELOCALISED_BOUNDARY, g_seen);
	uint8_t info[0x48];
	memset(info, 0, sizeof(info));
	int info_ret = relocinfo != 0 ? relocinfo(h, info) : -99;
	int32_t info_status;
	memcpy(&info_status, info + 0x1c, 4);
	memcpy(feed->room_uuid, info + 0x20, sizeof(feed->room_uuid) - 1);
	feed->room_uuid[sizeof(feed->room_uuid) - 1] = '\0';
	fprintf(stderr, "pico4-slamd: map %s (getRelocMapInfo %d, status %d); relocalised boundary %d points\n",
	        feed->room_uuid, info_ret, info_status, seen_n);
	uint32_t state = 2;
	if (seen_n == 4) {
		double yaw, t[3];
		double rms = room_fit((const double(*)[3])g_seen, ROOM_SQUARE, 4, &yaw, t, atan2, sin, cos, sqrt);
		fprintf(stderr, "pico4-slamd: relocalised in a saved room: turn %.2f deg, shift %.3f %.3f %.3f m, residual %.4f m\n",
		        yaw * 57.29577951308232, t[0], t[1], t[2], rms);
		if (rms >= 0 && rms < 0.05) {
			feed->room_yaw = yaw;
			memcpy(feed->room_t, t, sizeof(t));
			state = 1;
			write_boundary(SESSION_BOUNDARY, (const double(*)[3])g_seen, seen_n);
		}
	}
	if (state != 1) {
		fprintf(stderr, "pico4-slamd: a new room; its map is saved at the stop\n");
		write_boundary(SESSION_BOUNDARY, ROOM_SQUARE, 4);
	}
	feed->room_generation = generation;
	__atomic_store_n(&feed->room_state, state, __ATOMIC_RELEASE);
}

static struct isp_tables g_tables;
static uint16_t g_raw[PICO4_SLAM_FEED_CAMS][PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H];
static uint8_t g_images[PICO4_SLAM_FEED_CAMS][PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H];

/*
 * PICO OS's auto exposure of the head cameras (docs/xr.md#auto-exposure): its
 * libExposureImageProcess.so, called as its tracking service's calcExpo thread calls it, on a
 * set's four images after black level and lens shading, before the gamma table, with the
 * exposure they were taken with. Cameras 2 and 3 share one setting. The result goes to the driver,
 * which owns the station, unless it is what the set was taken with or what was sent last.
 */
typedef int (*expo_fn)(const void *, const void *, const void *, const void *, uint16_t *, uint8_t *, void *, int,
                       int, int);
#define AE_FPS 50       //!< The cameras' frame rate as PICO OS passes it (scene sets come at half of it).
#define AE_BIT_WIDTH 16 //!< 10-bit samples in 16 bits.
#define AE_FLAG_DETECT 1 //!< Start the mains-flicker detection, as PICO OS does once at the start.

struct ae_ctx
{
	struct pico4_slam_feed *feed;
	expo_fn predict;
	int ready; //!< 1: g_linear and lines/gain hold a set for the auto exposure (frame thread -> AE thread).
	uint32_t lines[PICO4_SLAM_FEED_CAMS], gain[PICO4_SLAM_FEED_CAMS];
	int stop;
	uint64_t runs, sent;
};
static uint16_t g_linear[PICO4_SLAM_FEED_CAMS][PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H];

static void *
ae_thread(void *arg)
{
	struct ae_ctx *c = arg;
	struct pico4_slam_feed *feed = c->feed;
	int flags = AE_FLAG_DETECT, last_ret = 2, have_sent = 0;
	uint16_t sent_lines[4] = {0};
	uint8_t sent_gains[4] = {0};
	while (!__atomic_load_n(&c->stop, __ATOMIC_ACQUIRE)) {
		if (!__atomic_load_n(&c->ready, __ATOMIC_ACQUIRE)) {
			usleep(2000);
			continue;
		}
		uint16_t lines[8] = {0};
		uint8_t gains[8] = {0};
		for (int k = 0; k < 4; k++) {
			lines[k] = (uint16_t)c->lines[k];
			gains[k] = (uint8_t)(c->gain[k] > 255 ? 255 : c->gain[k]);
		}
		struct vec unused = {0, 0, 0};
		int ret = c->predict(g_linear[0], g_linear[1], g_linear[2], g_linear[3], lines, gains, &unused, AE_FPS,
		                     AE_BIT_WIDTH, flags);
		flags = 0;
		c->runs++;
		if (ret != last_ret) {
			fprintf(stderr, "pico4-slamd: auto exposure %s\n",
			        ret == 0   ? "detecting the mains frequency"
			        : ret == 1 ? "found the frame rate off the mains frequency (flicker); the station stays at 50 Hz"
			                   : "running");
			last_ret = ret;
		}
		int own = 1, same = have_sent;
		for (int k = 0; k < 4; k++) {
			own = own && lines[k] == c->lines[k] && gains[k] == c->gain[k];
			same = same && lines[k] == sent_lines[k] && gains[k] == sent_gains[k];
		}
		if (!own && !same) {
			for (int k = 0; k < 4; k++) {
				feed->ae_lines[k] = lines[k];
				feed->ae_gain[k] = gains[k];
				sent_lines[k] = lines[k];
				sent_gains[k] = gains[k];
			}
			have_sent = 1;
			__atomic_fetch_add(&feed->ae_written, 1, __ATOMIC_RELEASE);
			if (c->sent++ % 50 == 0) {
				fprintf(stderr, "pico4-slamd: exposure %u/%u %u/%u %u/%u %u/%u (lines/gain per camera), %llu of %llu sets\n",
				        lines[0], gains[0], lines[1], gains[1], lines[2], gains[2], lines[3], gains[3],
				        (unsigned long long)c->sent, (unsigned long long)c->runs);
			}
		}
		__atomic_store_n(&c->ready, 0, __ATOMIC_RELEASE);
	}
	return 0;
}

//! PICO OS's auto exposure, unless PICO4_SLAMD_AE=0 leaves it to the driver; 0 when it cannot load.
static expo_fn
load_ae(void)
{
	const char *env = getenv("PICO4_SLAMD_AE");
	if (env != NULL && strcmp(env, "0") == 0) {
		fprintf(stderr, "pico4-slamd: auto exposure left to the driver (PICO4_SLAMD_AE=0)\n");
		return 0;
	}
	void *lib = dlopen("libExposureImageProcess.so", RTLD_NOW);
	expo_fn predict = lib != 0 ? (expo_fn)dlsym(lib, "ExposurePredict") : 0;
	if (predict == 0) {
		fprintf(stderr, "pico4-slamd: no auto exposure (%s); the driver's runs\n", lib != 0 ? "ExposurePredict" : dlerror());
	}
	return predict;
}

static void
on_result(void)
{
}

static char g_text[4 << 20];

//! The whole file into g_text; its length, or -1.
static long
read_text(const char *path)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		return -1;
	}
	long len = 0;
	ssize_t r;
	while (len < (long)sizeof(g_text) && (r = read(fd, g_text + len, sizeof(g_text) - (size_t)len)) > 0) {
		len += r;
	}
	close(fd);
	return len;
}

static int
load_table(const char *path, uint32_t *out, int count)
{
	long len = read_text(path);
	return len >= 0 && isp_parse_numbers(g_text, len, out, count) == count ? 0 : -1;
}

/*!
 * The lens-shading table as PICO OS chooses it: by the camera modules' vendor, which its station
 * service saved from their OTP (the extraction takes camera_otp.json from the user's backup).
 * PICO4_SLAMD_LSC names a table instead; NULL: PICO OS calls the vendor illegal and shades nothing.
 */
static const char *
lsc_table(void)
{
	const char *lsc = getenv("PICO4_SLAMD_LSC");
	if (lsc != NULL && *lsc != '\0') {
		return lsc;
	}
	long len = read_text(OTP_PATH);
	int vendor = len >= 0 ? isp_otp_vendor(g_text, len) : -1;
	if (vendor < 0) {
		fprintf(stderr, "pico4-slamd: no camera vendor id (" OTP_PATH "): the vendor-9 table\n");
		return "config_lscLH.txt";
	}
	const char *file = isp_lsc_file(vendor);
	fprintf(stderr, "pico4-slamd: camera vendor %d: %s\n", vendor, file != NULL ? file : "no lens shading");
	return file;
}

static int
load_tables(void)
{
	static uint32_t tmp[ISP_W * ISP_H];
	const char *lsc = lsc_table();
	if (lsc == NULL) {
		for (int i = 0; i < ISP_W * ISP_H; i++) {
			g_tables.lsc[i] = 128; // 1x
		}
	} else {
		char path[256];
		snprintf(path, sizeof(path), CONFIG_DIR "%s", lsc);
		if (load_table(path, tmp, ISP_W * ISP_H) < 0) {
			fprintf(stderr, "pico4-slamd: cannot read the lens-shading table %s\n", path);
			return -1;
		}
		for (int i = 0; i < ISP_W * ISP_H; i++) {
			g_tables.lsc[i] = (uint16_t)tmp[i];
		}
	}
	if (load_table(CONFIG_DIR "config_gma10.txt", tmp, 1024) < 0) {
		fprintf(stderr, "pico4-slamd: cannot read " CONFIG_DIR "config_gma10.txt\n");
		return -1;
	}
	for (int i = 0; i < 1024; i++) {
		g_tables.gamma[i] = (uint8_t)(tmp[i] > 255 ? 255 : tmp[i]);
	}
	return 0;
}

static struct pico4_slam_feed *
open_feed(int gid)
{
	int fd = open(PICO4_SLAM_FEED_PATH, O_RDWR | O_CREAT | O_CLOEXEC, 0660);
	if (fd < 0 || ftruncate(fd, sizeof(struct pico4_slam_feed)) < 0) {
		fprintf(stderr, "pico4-slamd: cannot create " PICO4_SLAM_FEED_PATH "\n");
		return 0;
	}
	if (gid >= 0) {
		fchown(fd, (uid_t)-1, (gid_t)gid);
	}
	fchmod(fd, 0660);
	struct pico4_slam_feed *feed = mmap(0, sizeof(*feed), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	close(fd);
	if (feed == (void *)-1) {
		return 0;
	}
	if (feed->magic != PICO4_SLAM_FEED_MAGIC) {
		memset(feed, 0, sizeof(*feed));
		feed->version = 1;
		__atomic_store_n(&feed->magic, PICO4_SLAM_FEED_MAGIC, __ATOMIC_RELEASE);
	}
	return feed;
}

static void
put32(uint8_t *p, uint32_t v)
{
	memcpy(p, &v, 4);
}

static void
put64(uint8_t *p, uint64_t v)
{
	memcpy(p, &v, 8);
}

/*
 * The frame thread, as PICO OS's checkIsSync thread: each scene set goes in once the IMU thread
 * has fed samples this far past it. updateImage hands the set to the Compute DSP and waits for
 * it; fed from the IMU's thread, the samples piled up behind each set and the poses came in
 * bursts at 25 Hz, up to 20 ms late.
 */
struct frames_ctx
{
	struct pico4_slam_feed *feed;
	void *h;
	image_fn image;
	uint64_t sr;
	int64_t imu_fed_ts; //!< Written by the IMU thread: the newest sample fed.
	uint64_t sets_fed, skipped;
	int stop; //!< Set by the IMU thread before the tracker's stop.
	struct ae_ctx *ae; //!< Or NULL.
};

static void *
frame_thread(void *arg)
{
	struct frames_ctx *c = arg;
	struct pico4_slam_feed *feed = c->feed;
	static uint8_t items[4 * 0x48];
	struct vec frames = {items, items + 4 * 0x48, items + 4 * 0x48};
	while (!__atomic_load_n(&c->stop, __ATOMIC_ACQUIRE)) {
		uint64_t sw = __atomic_load_n(&feed->sets_written, __ATOMIC_ACQUIRE);
		if (sw - c->sr > PICO4_SLAM_FEED_SETS - 1) {
			__atomic_fetch_add(&c->skipped, sw - c->sr - (PICO4_SLAM_FEED_SETS - 1), __ATOMIC_RELAXED);
			c->sr = sw - (PICO4_SLAM_FEED_SETS - 1);
		}
		const struct pico4_slam_feed_set *s = &feed->sets[c->sr % PICO4_SLAM_FEED_SETS];
		if (c->sr == sw || s->ts_ns + SET_DELAY_NS > __atomic_load_n(&c->imu_fed_ts, __ATOMIC_ACQUIRE)) {
			usleep(1000);
			continue;
		}
		int64_t ts = s->ts_ns;
		uint32_t lines[4], gain[4];
		int64_t sof[4];
		memcpy(g_raw, s->pixels, sizeof(g_raw));
		memcpy(lines, s->exposure_lines, sizeof(lines));
		memcpy(gain, s->gain, sizeof(gain));
		memcpy(sof, s->sof_ns, sizeof(sof));
		// The driver may have reused the slot while it was copied.
		if (__atomic_load_n(&feed->sets_written, __ATOMIC_ACQUIRE) - c->sr >= PICO4_SLAM_FEED_SETS) {
			__atomic_fetch_add(&c->skipped, 1, __ATOMIC_RELAXED);
			c->sr++;
			continue;
		}
		uint64_t n = __atomic_load_n(&c->sets_fed, __ATOMIC_RELAXED);
		// The auto exposure takes the set when it is done with the one before.
		bool to_ae = c->ae != 0 && !__atomic_load_n(&c->ae->ready, __ATOMIC_ACQUIRE);
		for (int k = 0; k < 4; k++) {
			isp_process(&g_tables, g_raw[k], PICO4_SLAM_FEED_W, g_images[k], to_ae ? g_linear[k] : 0);
			uint8_t *it = items + k * 0x48;
			memset(it, 0, 0x48);
			it[0] = (uint8_t)k;
			put32(it + 0x04, PICO4_SLAM_FEED_W);
			put32(it + 0x08, PICO4_SLAM_FEED_H);
			put32(it + 0x14, lines[k]);
			put32(it + 0x18, gain[k]);
			put64(it + 0x20, (uint64_t)sof[k]);
			put64(it + 0x28, (uint64_t)ts);
			put64(it + 0x30, 13 + n);
			put32(it + 0x38, PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H);
			const uint8_t *px = g_images[k];
			memcpy(it + 0x40, &px, 8);
		}
		if (to_ae) {
			memcpy(c->ae->lines, lines, sizeof(lines));
			memcpy(c->ae->gain, gain, sizeof(gain));
			__atomic_store_n(&c->ae->ready, 1, __ATOMIC_RELEASE);
		}
		c->image(c->h, 0, &frames);
		__atomic_store_n(&c->sets_fed, n + 1, __ATOMIC_RELAXED);
		c->sr++;
	}
	return 0;
}

int
main(int argc, char **argv, char **envp)
{
	(void)envp;
	int gid = argc > 1 ? atoi(argv[1]) : -1;
	if (load_tables() < 0) {
		return 1;
	}
	struct pico4_slam_feed *feed = open_feed(gid);
	if (feed == 0) {
		return 1;
	}
	// Wait for a driver, then serve its generation for as long as it lasts.
	uint32_t generation;
	while ((generation = __atomic_load_n(&feed->generation, __ATOMIC_ACQUIRE)) == 0 ||
	       __atomic_load_n(&feed->imu_written, __ATOMIC_ACQUIRE) == 0) {
		feed->slamd_heartbeat_ns = (uint64_t)now_ns(CLOCK_MONOTONIC);
		usleep(100000);
	}

	void *lib = dlopen("libSlam.so", RTLD_NOW);
	if (!lib) {
		fprintf(stderr, "pico4-slamd: %s\n", dlerror());
		return 1;
	}
	create_fn create = (create_fn)dlsym(lib, "createHandle");
	set_fn set = (set_fn)dlsym(lib, "setAlgorithmParameters");
	start_fn start = (start_fn)dlsym(lib, "startAlgorithm");
	image_fn image = (image_fn)dlsym(lib, "updateImage");
	imu_fn imu = (imu_fn)dlsym(lib, "update6AxisIMU");
	result_fn result = (result_fn)dlsym(lib, "getAlgorithmResult");
	register_fn reg = (register_fn)dlsym(lib, "AlgorithmResultCallbackRegister");
	prestop_fn prestop = (prestop_fn)dlsym(lib, "preStopAlgorithm");
	stop_fn stop = (stop_fn)dlsym(lib, "stopAlgorithm");
	destroy_fn destroy = (destroy_fn)dlsym(lib, "destroyHandle");
	relocinfo_fn relocinfo = (relocinfo_fn)dlsym(lib, "getRelocMapInfo");
	if (!create || !set || !start || !image || !imu || !result || !reg || !prestop || !stop || !destroy) {
		fprintf(stderr, "pico4-slamd: libSlam.so lacks a function of its interface\n");
		return 1;
	}
	// As PICO OS's service: the IMU's model, start, the result callback, then 6DoF.
	// The tracker's copy of a saved room's boundary is this session's, if it relocalises.
	unlink(RELOCALISED_BOUNDARY);
	void *h = create("");
	if (h == 0) {
		fprintf(stderr, "pico4-slamd: createHandle failed\n");
		return 1;
	}
	set(h, "headSensorModel", "icm4x6xx");
	start(h, "");
	reg(on_result);
	__atomic_store_n(&feed->room_state, 0, __ATOMIC_RELEASE);
	set(h, "start6DOF", "6");
	set(h, "bigRoom", "0"); // as PICO OS without large-space mode
	int64_t started = now_ns(CLOCK_MONOTONIC);
	bool room_settled = false;
	signal(SIGTERM, on_stop_signal);
	signal(SIGINT, on_stop_signal);
	fprintf(stderr, "pico4-slamd: tracking for driver generation %u\n", generation);

	static struct frames_ctx frames;
	frames.feed = feed;
	frames.h = h;
	frames.image = image;
	frames.sr = __atomic_load_n(&feed->sets_written, __ATOMIC_ACQUIRE);
	static struct ae_ctx ae;
	ae.feed = feed;
	ae.predict = load_ae();
	pthread_t ae_th;
	if (ae.predict != 0 && pthread_create(&ae_th, 0, ae_thread, &ae) == 0) {
		frames.ae = &ae;
		__atomic_store_n(&feed->ae_generation, generation, __ATOMIC_RELEASE);
	}
	pthread_t thread;
	if (pthread_create(&thread, 0, frame_thread, &frames) != 0) {
		fprintf(stderr, "pico4-slamd: cannot start the frame thread\n");
		return 1;
	}

	// The IMU thread, as PICO OS's threadReader: every sample and its pose, as soon as it is there.
	uint8_t res[0x170];
	double sample[7];
	struct vec samples = {sample, sample + 7, sample + 7};
	uint64_t ir = __atomic_load_n(&feed->imu_written, __ATOMIC_ACQUIRE);
	uint64_t imu_fed = 0, skipped = 0, tracked = 0;
	int64_t last_imu = 0, report = now_ns(CLOCK_MONOTONIC) + 10000000000LL;
	int64_t cpu0 = now_ns(CLOCK_PROCESS_CPUTIME_ID), wall0 = now_ns(CLOCK_MONOTONIC);
	bool known = false;
	// The longest time between two poses in each report: bursts show here.
	int64_t last_pose_wall = 0, longest_gap = 0;
	for (;;) {
		int64_t now = now_ns(CLOCK_MONOTONIC);
		feed->slamd_heartbeat_ns = (uint64_t)now;
		if (g_stop || __atomic_load_n(&feed->generation, __ATOMIC_ACQUIRE) != generation) {
			// As PICO OS's service stops it: the map is saved (with a boundary) on the way.
			fprintf(stderr, "pico4-slamd: %s; stopping the tracker\n",
			        g_stop ? "asked to stop" : "the driver started again, a new tracker follows");
			fflush(stderr);
			__atomic_store_n(&frames.stop, 1, __ATOMIC_RELEASE);
			pthread_join(thread, 0);
			if (frames.ae != 0) {
				__atomic_store_n(&feed->ae_generation, 0, __ATOMIC_RELEASE); // the driver's own again
				__atomic_store_n(&ae.stop, 1, __ATOMIC_RELEASE);
				pthread_join(ae_th, 0);
			}
			prestop(h);
			stop(h, "non_sleep");
			destroy(h);
			fprintf(stderr, "pico4-slamd: tracker stopped\n");
			fflush(stderr);
			exit(0);
		}
		if (!room_settled && now - started > RELOCALISE_NS) {
			settle_room(feed, generation, h, relocinfo);
			room_settled = true;
		}
		uint64_t iw = __atomic_load_n(&feed->imu_written, __ATOMIC_ACQUIRE);
		if (iw - ir > PICO4_SLAM_FEED_IMU) {
			skipped += iw - ir - PICO4_SLAM_FEED_IMU;
			ir = iw - PICO4_SLAM_FEED_IMU;
		}
		if (ir == iw) {
			usleep(200);
			continue;
		}
		const struct pico4_slam_feed_imu e = feed->imu[ir % PICO4_SLAM_FEED_IMU];
		ir++;
		if (e.ts_ns <= last_imu) {
			continue; // the tracker needs increasing times
		}
		last_imu = e.ts_ns;
		for (int k = 0; k < 3; k++) {
			sample[k] = e.gyro[k];
			sample[3 + k] = e.accel[k];
		}
		// The IMU's own temperature, as PICO OS's sensor DSP inserts it (NaN: none yet).
		known = e.temperature >= TEMPERATURE_MIN && e.temperature <= TEMPERATURE_MAX;
		sample[6] = known ? e.temperature : DEFAULT_TEMPERATURE;
		imu(h, &samples, e.ts_ns);
		imu_fed++;
		__atomic_store_n(&frames.imu_fed_ts, e.ts_ns, __ATOMIC_RELEASE);
		memset(res, 0, sizeof(res));
		if (result(h, res) == 0) {
			uint64_t w = __atomic_load_n(&feed->poses_written, __ATOMIC_RELAXED);
			struct pico4_slam_feed_pose *p = &feed->poses[w % PICO4_SLAM_FEED_POSES];
			memcpy(&p->ts_ns, res, 8);
			memcpy(p->position, res + 0x08, 24);
			memcpy(p->orientation, res + 0x20, 32);
			memcpy(p->velocity, res + 0xe0, 24);
			memcpy(p->angular, res + 0x110, 24);
			p->type = res[0x40];
			p->confidence = res[0x41];
			memcpy(&p->error, res + 0x44, 4);
			__atomic_store_n(&feed->poses_written, w + 1, __ATOMIC_RELEASE);
			tracked += p->confidence > 0;
			int64_t wall = now_ns(CLOCK_MONOTONIC);
			if (last_pose_wall != 0 && wall - last_pose_wall > longest_gap) {
				longest_gap = wall - last_pose_wall;
			}
			last_pose_wall = wall;
		}
		if (now >= report) {
			double cpu = (double)(now_ns(CLOCK_PROCESS_CPUTIME_ID) - cpu0) / (double)(now - wall0);
			fprintf(stderr,
			        "pico4-slamd: %llu frame sets, %llu IMU samples, %llu tracked poses, %llu skipped; IMU at "
			        "%.1f C%s; pose %.1f ms after its sample, longest gap between poses %.1f ms; CPU %.0f %% of "
			        "one core\n",
			        (unsigned long long)__atomic_load_n(&frames.sets_fed, __ATOMIC_RELAXED),
			        (unsigned long long)imu_fed, (unsigned long long)tracked,
			        (unsigned long long)(skipped + __atomic_load_n(&frames.skipped, __ATOMIC_RELAXED)), sample[6],
			        known ? "" : " (none reported, default)", (double)(now_ns(CLOCK_MONOTONIC) - e.ts_ns) / 1e6,
			        (double)longest_gap / 1e6, 100.0 * cpu);
			longest_gap = 0;
			fflush(stderr);
			report = now + 10000000000LL;
		}
	}
}
