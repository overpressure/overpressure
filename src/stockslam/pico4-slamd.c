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
 * it: every IMU sample {gyro, accel, temperature} with its time; each frame set, once the IMU is
 * past it (the camera's latency does that on PICO OS), as four 640x480 8-bit images after the
 * stock image processing (isp.h), with the earliest exposure middle for all four; then
 * start6DOF. Every pose goes back. Times are CLOCK_MONOTONIC ns throughout.
 * PICO4_SLAMD_LSC names another lens-shading table (default: config_lscLH.txt, camera modules
 * of vendor 9).
 */
#include "bionic.h"
#include "isp.h"
#include "pico4_slam_feed.h"

#include <stdbool.h>

#define CONFIG_DIR "/system/etc/pvr/config/"
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

static struct isp_tables g_tables;
static uint16_t g_raw[PICO4_SLAM_FEED_CAMS][PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H];
static uint8_t g_images[PICO4_SLAM_FEED_CAMS][PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H];

static void
on_result(void)
{
}

static int
load_table(const char *path, uint32_t *out, int count)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		return -1;
	}
	static char text[4 << 20];
	long len = 0;
	ssize_t r;
	while (len < (long)sizeof(text) && (r = read(fd, text + len, sizeof(text) - (size_t)len)) > 0) {
		len += r;
	}
	close(fd);
	return isp_parse_numbers(text, len, out, count) == count ? 0 : -1;
}

static int
load_tables(void)
{
	static uint32_t tmp[ISP_W * ISP_H];
	const char *lsc = getenv("PICO4_SLAMD_LSC");
	char path[256];
	snprintf(path, sizeof(path), CONFIG_DIR "%s", lsc != NULL && *lsc != '\0' ? lsc : "config_lscLH.txt");
	if (load_table(path, tmp, ISP_W * ISP_H) < 0) {
		fprintf(stderr, "pico4-slamd: cannot read the lens-shading table %s\n", path);
		return -1;
	}
	for (int i = 0; i < ISP_W * ISP_H; i++) {
		g_tables.lsc[i] = (uint16_t)tmp[i];
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
	if (!create || !set || !start || !image || !imu || !result || !reg) {
		fprintf(stderr, "pico4-slamd: libSlam.so lacks a function of its interface\n");
		return 1;
	}
	// As PICO OS's service: the IMU's model, start, the result callback, then 6DoF.
	void *h = create("");
	if (h == 0) {
		fprintf(stderr, "pico4-slamd: createHandle failed\n");
		return 1;
	}
	set(h, "headSensorModel", "icm4x6xx");
	start(h, "");
	reg(on_result);
	set(h, "start6DOF", "6");
	fprintf(stderr, "pico4-slamd: tracking for driver generation %u\n", generation);

	uint8_t items[4 * 0x48], res[0x170];
	struct vec frames = {items, items + 4 * 0x48, items + 4 * 0x48};
	double sample[7];
	struct vec samples = {sample, sample + 7, sample + 7};
	uint64_t ir = __atomic_load_n(&feed->imu_written, __ATOMIC_ACQUIRE);
	uint64_t sr = __atomic_load_n(&feed->sets_written, __ATOMIC_ACQUIRE);
	uint64_t sets_fed = 0, imu_fed = 0, skipped = 0, tracked = 0;
	int64_t last_imu = 0, report = now_ns(CLOCK_MONOTONIC) + 10000000000LL;
	int64_t cpu0 = now_ns(CLOCK_PROCESS_CPUTIME_ID), wall0 = now_ns(CLOCK_MONOTONIC);
	for (;;) {
		int64_t now = now_ns(CLOCK_MONOTONIC);
		feed->slamd_heartbeat_ns = (uint64_t)now;
		if (__atomic_load_n(&feed->generation, __ATOMIC_ACQUIRE) != generation) {
			fprintf(stderr, "pico4-slamd: the driver started again; a new tracker follows\n");
			fflush(stderr);
			exit(0); // the tracker's own teardown is not needed for a new one
		}
		uint64_t iw = __atomic_load_n(&feed->imu_written, __ATOMIC_ACQUIRE);
		uint64_t sw = __atomic_load_n(&feed->sets_written, __ATOMIC_ACQUIRE);
		if (iw - ir > PICO4_SLAM_FEED_IMU) {
			skipped += iw - ir - PICO4_SLAM_FEED_IMU;
			ir = iw - PICO4_SLAM_FEED_IMU;
		}
		if (sw - sr > PICO4_SLAM_FEED_SETS - 1) {
			skipped += sw - sr - (PICO4_SLAM_FEED_SETS - 1);
			sr = sw - (PICO4_SLAM_FEED_SETS - 1);
		}
		if (ir == iw) {
			usleep(500);
			continue;
		}
		const struct pico4_slam_feed_imu e = feed->imu[ir % PICO4_SLAM_FEED_IMU];
		ir++;
		// Frame sets the IMU is far enough past go in first.
		while (sr < sw && feed->sets[sr % PICO4_SLAM_FEED_SETS].ts_ns + SET_DELAY_NS <= e.ts_ns) {
			const struct pico4_slam_feed_set *s = &feed->sets[sr % PICO4_SLAM_FEED_SETS];
			int64_t ts = s->ts_ns;
			uint32_t lines[4], gain[4];
			int64_t sof[4];
			memcpy(g_raw, s->pixels, sizeof(g_raw));
			memcpy(lines, s->exposure_lines, sizeof(lines));
			memcpy(gain, s->gain, sizeof(gain));
			memcpy(sof, s->sof_ns, sizeof(sof));
			// The driver may have reused the slot while it was copied.
			if (__atomic_load_n(&feed->sets_written, __ATOMIC_ACQUIRE) - sr >= PICO4_SLAM_FEED_SETS) {
				skipped++;
				sr++;
				continue;
			}
			for (int k = 0; k < 4; k++) {
				isp_process(&g_tables, g_raw[k], PICO4_SLAM_FEED_W, g_images[k]);
				uint8_t *it = items + k * 0x48;
				memset(it, 0, 0x48);
				it[0] = (uint8_t)k;
				put32(it + 0x04, PICO4_SLAM_FEED_W);
				put32(it + 0x08, PICO4_SLAM_FEED_H);
				put32(it + 0x14, lines[k]);
				put32(it + 0x18, gain[k]);
				put64(it + 0x20, (uint64_t)sof[k]);
				put64(it + 0x28, (uint64_t)ts);
				put64(it + 0x30, 13 + sets_fed);
				put32(it + 0x38, PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H);
				const uint8_t *p = g_images[k];
				memcpy(it + 0x40, &p, 8);
			}
			image(h, 0, &frames);
			sets_fed++;
			sr++;
		}
		if (e.ts_ns <= last_imu) {
			continue; // the tracker needs increasing times
		}
		last_imu = e.ts_ns;
		for (int k = 0; k < 3; k++) {
			sample[k] = e.gyro[k];
			sample[3 + k] = e.accel[k];
		}
		// The IMU's own temperature, as PICO OS's sensor DSP inserts it (NaN: none yet).
		bool known = e.temperature >= TEMPERATURE_MIN && e.temperature <= TEMPERATURE_MAX;
		sample[6] = known ? e.temperature : DEFAULT_TEMPERATURE;
		imu(h, &samples, e.ts_ns);
		imu_fed++;
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
		}
		if (now >= report) {
			double cpu = (double)(now_ns(CLOCK_PROCESS_CPUTIME_ID) - cpu0) / (double)(now - wall0);
			fprintf(stderr,
			        "pico4-slamd: %llu frame sets, %llu IMU samples, %llu tracked poses, %llu skipped; IMU at "
			        "%.1f C%s; CPU %.0f %% of one core\n",
			        (unsigned long long)sets_fed, (unsigned long long)imu_fed, (unsigned long long)tracked,
			        (unsigned long long)skipped, sample[6], known ? "" : " (none reported, default)", 100.0 * cpu);
			fflush(stderr);
			report = now + 10000000000LL;
		}
	}
}
