// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-cam: the four OV7251 tracking cameras through the Spectra CSL UAPI
 * (pico4_cam.c in userspace/pkg/pico4-monado).
 *
 *   pico4-cam list
 *       Request manager, sync, ISP, sensor and CSIPHY nodes with the slot and
 *       PHY each one reports, the ISP's IOMMU handles and every media entity.
 *       Opens subdevs for CAM_QUERY_CAP only; powers nothing.
 *   pico4-cam probe [SLOT...]
 *       CAM_SENSOR_PROBE_CMD on the given slots (default 0-3): power up, read
 *       the chip ID, power down. The kernel forgets the probe again when this
 *       process closes the sensor node.
 *   pico4-cam capture [--slots 0,1,2,3] [--frames N] [--fps 60|30] [--out DIR]
 *                     [--ring N] [--timeout MS] [--lane-assign SLOT=LANE]
 *                     [--no-station] [--no-station-reset] [--stats-log FILE]
 *                     [--exposure LINES [--gain G]]
 *       Streams until every selected camera delivered N frames (default 30),
 *       saves an early scene frame of each camera as DIR/camS.pgm (8 bits, 640x480) and
 *       DIR/camS.raw16 (640x480 little-endian 16-bit samples), and prints a
 *       JSON summary: per slot the frame count, the frame rate from
 *       start-of-frame timestamps, whether those increase, and min/mean/max
 *       sample values. The station (controller radio) triggers the frames: it
 *       is power-cycled first (unless --no-station-reset) and told to start
 *       once the sensors stream. It refuses unless all four sensors are on, so
 *       --slots only selects which cameras are reported. --stats-log writes
 *       "slot sof_boottime_ns mean latency_ns request" per frame (mean of a
 *       sparse grid; latency from start of frame to delivery), for example to
 *       find which slot goes dark when a lens is covered.
 *       --exposure (sensor lines, at most about 900) and --gain (16 = 1x)
 *       ask the station for that exposure; the saved frame is then the
 *       tenth or eleventh, after the setting took effect. Every other frame
 *       is the station's short controller-LED exposure; of two consecutive
 *       frames the brighter one is saved.
 *
 * -v and -vv (before or after the command) log progress and every ioctl to
 * stderr. Exit status 0 means success: every node found (list), every probe
 * answered (probe), or every camera delivered N frames with increasing
 * timestamps (capture).
 */

#define _GNU_SOURCE // sigaction, mkdir under -std=c11

#include "pico4_cam.h"
#include "pico4_station.h"

#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

static volatile sig_atomic_t g_stop;
static volatile int g_station_stop;
static unsigned g_save_frame = 2; //!< Which frame of each camera to save (or the next, if brighter).

static void
on_signal(int sig)
{
	(void)sig;
	g_stop = 1;
}

static void
usage(FILE *f)
{
	fputs("usage: pico4-cam [-v|-vv] list\n"
	      "       pico4-cam [-v|-vv] probe [SLOT...]\n"
	      "       pico4-cam [-v|-vv] capture [--slots 0,1,2,3] [--frames N] [--fps 60|30] [--out DIR]\n"
	      "                 [--ring N] [--timeout MS] [--lane-assign SLOT=LANE] [--no-station]\n"
	      "                 [--no-station-reset] [--stats-log FILE] [--exposure LINES [--gain G]]\n",
	      f);
}

static void
json_str(const char *s)
{
	putchar('"');
	for (; *s != '\0'; s++) {
		unsigned char ch = (unsigned char)*s;
		if (ch == '"' || ch == '\\') {
			printf("\\%c", ch);
		} else if (ch < 0x20) {
			printf("\\u%04x", ch);
		} else {
			putchar(ch);
		}
	}
	putchar('"');
}

static bool
parse_uint(const char *s, unsigned long max, unsigned long *out)
{
	char *end = NULL;
	unsigned long v;
	errno = 0;
	v = strtoul(s, &end, 0);
	if (errno != 0 || end == s || *end != '\0' || v > max) {
		return false;
	}
	*out = v;
	return true;
}

static bool
parse_slots(const char *s, unsigned *mask)
{
	char buf[64], *save = NULL, *tok;
	unsigned m = 0;
	snprintf(buf, sizeof(buf), "%s", s);
	for (tok = strtok_r(buf, ",", &save); tok != NULL; tok = strtok_r(NULL, ",", &save)) {
		unsigned long v;
		if (!parse_uint(tok, PICO4_CAM_NUM - 1, &v)) {
			return false;
		}
		m |= 1u << v;
	}
	*mask = m;
	return m != 0;
}


/*
 *
 * list
 *
 */

static int
cmd_list(const struct pico4_cam_config *cfg)
{
	struct pico4_cam_devices *d = calloc(1, sizeof(*d));
	int r, sensors = 0;
	bool first;

	if (d == NULL) {
		return 1;
	}
	r = pico4_cam_discover(d, cfg);
	printf("{\n  \"crm\": ");
	json_str(d->crm);
	printf(",\n  \"sync\": ");
	json_str(d->sync);
	printf(",\n  \"isp\": ");
	json_str(d->isp);
	printf(",\n  \"isp_iommu\": {\"non_secure\": %d, \"secure\": %d},\n", d->isp_iommu[0], d->isp_iommu[1]);
	printf("  \"cdm_iommu\": {\"non_secure\": %d, \"secure\": %d},\n", d->cdm_iommu[0], d->cdm_iommu[1]);
	printf("  \"sensors\": [");
	first = true;
	for (int s = 0; s < PICO4_CAM_MAX_SLOTS; s++) {
		if (d->sensor[s][0] == '\0') {
			continue;
		}
		printf("%s\n    {\"slot\": %d, \"node\": ", first ? "" : ",", s);
		json_str(d->sensor[s]);
		printf(", \"csiphy\": %d, \"tracking_camera\": %s}", d->sensor_csiphy[s], s < PICO4_CAM_NUM ? "true" : "false");
		first = false;
		sensors += s < PICO4_CAM_NUM;
	}
	printf("\n  ],\n  \"csiphys\": [");
	first = true;
	for (int p = 0; p < PICO4_CAM_MAX_PHYS; p++) {
		if (d->csiphy[p][0] == '\0') {
			continue;
		}
		printf("%s\n    {\"phy\": %d, \"node\": ", first ? "" : ",", p);
		json_str(d->csiphy[p]);
		printf("}");
		first = false;
	}
	printf("\n  ],\n  \"entities\": [");
	for (unsigned i = 0; i < d->num_entities; i++) {
		const struct pico4_cam_entity *e = &d->entities[i];
		printf("%s\n    {\"name\": ", i == 0 ? "" : ",");
		json_str(e->name);
		printf(", \"function\": \"0x%x\", \"dev\": \"%u:%u\", \"node\": ", e->function, e->major, e->minor);
		json_str(e->devnode);
		printf("}");
	}
	printf("\n  ]\n}\n");
	free(d);
	return r == 0 && sensors == PICO4_CAM_NUM ? 0 : 1;
}


/*
 *
 * probe
 *
 */

static int
cmd_probe(struct pico4_cam_config *cfg)
{
	struct pico4_cam *cam = NULL;
	bool ok = true, first = true;
	int r;

	cfg->probe_only = true;
	r = pico4_cam_open(&cam, cfg);
	if (r < 0) {
		printf("{\"ok\": false, \"error\": ");
		json_str(strerror(-r));
		printf("}\n");
		return 1;
	}
	printf("{\n  \"slots\": [");
	for (int s = 0; s < PICO4_CAM_NUM; s++) {
		struct pico4_cam_slot_info si;
		if (!((cfg->slots >> s) & 1) || pico4_cam_get_slot_info(cam, s, &si) < 0) {
			continue;
		}
		printf("%s\n    {\"slot\": %d, \"sensor\": ", first ? "" : ",", s);
		json_str(si.sensor);
		printf(", \"csiphy\": %d, \"combo\": %s, \"lane_assign\": %u, \"probe\": ", si.phy,
		       si.combo ? "true" : "false", si.lane_assign);
		json_str(si.probe_rc == 0 ? "ok" : si.probe_rc > 0 ? "not attempted" : strerror(-si.probe_rc));
		printf("}");
		ok = ok && si.probe_rc == 0;
		first = false;
	}
	printf("\n  ],\n  \"ok\": %s\n}\n", ok ? "true" : "false");
	pico4_cam_close(cam);
	return ok ? 0 : 1;
}


/*
 *
 * capture
 *
 */

struct slot_stats
{
	unsigned frames;
	unsigned with_ts;
	bool monotonic;
	int64_t first_ts, last_ts, prev_ts;
	uint64_t first_frame_id, last_frame_id;
	uint64_t first_request;
	uint64_t sum;
	uint64_t count;
	unsigned min, max;
	bool saved;
	double saved_mean;
	char pgm[256], raw[256];
	int save_error;
};

static unsigned
sample(const struct pico4_cam_frame *f, const uint8_t *line, unsigned x)
{
	if (f->bytes_per_sample == 2) {
		return (unsigned)line[2 * x] | ((unsigned)line[2 * x + 1] << 8);
	}
	return line[x];
}

//! Every frame: all pixels of the first one, every second row and column of the rest.
static void
add_stats(struct slot_stats *st, const struct pico4_cam_frame *f, bool full)
{
	unsigned step = full ? 1 : 2;
	for (unsigned y = 0; y < f->height; y += step) {
		const uint8_t *line = (const uint8_t *)f->data + (size_t)y * f->stride;
		for (unsigned x = 0; x < f->width; x += step) {
			unsigned v = sample(f, line, x);
			st->sum += v;
			st->min = v < st->min ? v : st->min;
			st->max = v > st->max ? v : st->max;
		}
		st->count += (f->width + step - 1) / step;
	}
}

static int
save_frame(struct slot_stats *st, const struct pico4_cam_frame *f, const char *dir)
{
	FILE *pgm, *raw;
	unsigned shift = 0;
	uint8_t row[PICO4_CAM_WIDTH];

	if (f->bytes_per_sample == 2) {
		// 10-bit samples, LSB-aligned; scan for set high bits in case the CSID aligns them up.
		unsigned max = 0;
		for (unsigned y = 0; y < f->height; y++) {
			const uint8_t *line = (const uint8_t *)f->data + (size_t)y * f->stride;
			for (unsigned x = 0; x < f->width; x++) {
				unsigned v = sample(f, line, x);
				max = v > max ? v : max;
			}
		}
		shift = max > 1023 ? 8 : 2;
	}
	snprintf(st->pgm, sizeof(st->pgm), "%s/cam%d.pgm", dir, f->slot);
	snprintf(st->raw, sizeof(st->raw), "%s/cam%d.raw16", dir, f->slot);
	pgm = fopen(st->pgm, "wbe");
	if (pgm == NULL) {
		return -errno;
	}
	fprintf(pgm, "P5\n%u %u\n255\n", f->width, f->height);
	for (unsigned y = 0; y < f->height; y++) {
		const uint8_t *line = (const uint8_t *)f->data + (size_t)y * f->stride;
		for (unsigned x = 0; x < f->width; x++) {
			unsigned v = sample(f, line, x) >> shift;
			row[x] = (uint8_t)(v > 255 ? 255 : v);
		}
		fwrite(row, 1, f->width, pgm);
	}
	if (fclose(pgm) != 0) {
		return -errno;
	}
	raw = fopen(st->raw, "wbe");
	if (raw == NULL) {
		return -errno;
	}
	for (unsigned y = 0; y < f->lines; y++) {
		fwrite((const uint8_t *)f->data + (size_t)y * f->stride, f->bytes_per_sample, f->width, raw);
	}
	if (fclose(raw) != 0) {
		return -errno;
	}
	st->saved = true;
	return 0;
}

//! Mean of a sparse grid of samples.
static double
grid_mean(const struct pico4_cam_frame *f)
{
	uint64_t sum = 0, n = 0;
	for (unsigned y = 8; y < f->height; y += 16) {
		const uint8_t *line = (const uint8_t *)f->data + (size_t)y * f->stride;
		for (unsigned x = 8; x < f->width; x += 16) {
			sum += sample(f, line, x);
			n++;
		}
	}
	return n > 0 ? (double)sum / (double)n : 0.0;
}

static void
log_frame(FILE *log, const struct pico4_cam_frame *f)
{
	struct timespec ts;
	clock_gettime(CLOCK_BOOTTIME, &ts);
	long long now = (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
	fprintf(log, "%d %lld %.2f %lld %llu\n", f->slot, (long long)f->sof_boottime_ns, grid_mean(f),
	        now - (long long)f->sof_boottime_ns, (unsigned long long)f->request_id);
}

static void
on_frame(struct slot_stats *st, const struct pico4_cam_frame *f, const char *dir)
{
	if (st->frames == 0) {
		st->first_request = f->request_id;
	}
	// The station alternates scene and controller-LED exposures: keep the brighter of two frames.
	if (dir != NULL && (st->frames == g_save_frame || st->frames == g_save_frame + 1)) {
		double mean = grid_mean(f);
		if (st->frames == g_save_frame || mean > st->saved_mean) {
			st->saved_mean = mean;
			st->save_error = save_frame(st, f, dir);
		}
	}
	add_stats(st, f, st->frames == 0);
	if (f->sof_boottime_ns != 0) {
		if (st->with_ts == 0) {
			st->first_ts = f->sof_boottime_ns;
			st->first_frame_id = f->frame_id;
		} else if (f->sof_boottime_ns <= st->prev_ts) {
			st->monotonic = false;
		}
		st->prev_ts = st->last_ts = f->sof_boottime_ns;
		st->last_frame_id = f->frame_id;
		st->with_ts++;
	}
	st->frames++;
}

static bool
parse_lane_assign(const char *s, struct pico4_cam_config *cfg)
{
	unsigned long slot, lane;
	char buf[32], *eq;
	snprintf(buf, sizeof(buf), "%s", s);
	eq = strchr(buf, '=');
	if (eq == NULL) {
		return false;
	}
	*eq = '\0';
	if (!parse_uint(buf, PICO4_CAM_NUM - 1, &slot) || !parse_uint(eq + 1, 3, &lane)) {
		return false;
	}
	cfg->lane_assign[slot] = (int)lane;
	return true;
}

//! Acknowledges the station's frames while the capture runs.
static void *
station_thread(void *arg)
{
	while (!g_station_stop) {
		if (pico4_station_poll(arg, 100) < 0) {
			break;
		}
	}
	return NULL;
}

static int
cmd_capture(struct pico4_cam_config *cfg, int argc, char **argv)
{
	const char *dir = ".";
	FILE *stats_log = NULL;
	unsigned long exposure = 0, gain = 16;
	unsigned long frames = 30, timeout = 3000;
	struct slot_stats st[PICO4_CAM_NUM];
	struct pico4_cam *cam = NULL;
	struct sigaction sa;
	const char *error = NULL;
	bool ok = true, first = true, use_station = true, station_reset = true, station_started = false;
	struct pico4_station *station = NULL;
	pthread_t poller;
	bool polling = false;
	int r;

	for (int i = 0; i < argc; i++) {
		const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
		unsigned long n;
		if (strcmp(a, "--no-station") == 0) {
			use_station = false;
			continue;
		}
		if (strcmp(a, "--no-station-reset") == 0) {
			station_reset = false;
			continue;
		}
		if (v == NULL) {
			usage(stderr);
			return 2;
		}
		i++;
		if (strcmp(a, "--slots") == 0 && parse_slots(v, &cfg->slots)) {
		} else if (strcmp(a, "--frames") == 0 && parse_uint(v, 1000000, &frames) && frames > 0) {
		} else if (strcmp(a, "--fps") == 0 && parse_uint(v, 60, &n) && (n == 60 || n == 30)) {
			cfg->fps = (unsigned)n;
		} else if (strcmp(a, "--out") == 0) {
			dir = v;
		} else if (strcmp(a, "--exposure") == 0 && parse_uint(v, 1700, &exposure)) {
		} else if (strcmp(a, "--gain") == 0 && parse_uint(v, 1023, &gain) && gain >= 16) {
		} else if (strcmp(a, "--stats-log") == 0) {
			if (stats_log != NULL) {
				fclose(stats_log);
			}
			stats_log = fopen(v, "we");
			if (stats_log == NULL) {
				fprintf(stderr, "pico4-cam: cannot write %s: %s\n", v, strerror(errno));
				return 1;
			}
		} else if (strcmp(a, "--ring") == 0 && parse_uint(v, PICO4_CAM_RING_MAX, &n)) {
			cfg->ring = (unsigned)n;
		} else if (strcmp(a, "--timeout") == 0 && parse_uint(v, 600000, &timeout)) {
		} else if (strcmp(a, "--lane-assign") == 0 && parse_lane_assign(v, cfg)) {
		} else {
			fprintf(stderr, "pico4-cam: bad option %s %s\n", a, v);
			usage(stderr);
			return 2;
		}
	}
	if (mkdir(dir, 0755) < 0 && errno != EEXIST) {
		fprintf(stderr, "pico4-cam: cannot create %s: %s\n", dir, strerror(errno));
		return 1;
	}

	memset(st, 0, sizeof(st));
	for (int s = 0; s < PICO4_CAM_NUM; s++) {
		st[s].monotonic = true;
		st[s].min = ~0u;
	}
	// No SA_RESTART: a signal ends the wait in pico4_cam_next() and the capture tears down.
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = on_signal;
	sigemptyset(&sa.sa_mask);
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);

	if (use_station) {
		r = pico4_station_open(&station, NULL);
		if (r == 0 && station_reset) {
			r = pico4_station_reset(station);
		}
		if (r < 0) {
			fprintf(stderr, "pico4-cam: station: %s\n", strerror(-r));
			error = "station unavailable";
		}
	}
	r = error != NULL ? -ENODEV : pico4_cam_open(&cam, cfg);
	if (r == 0) {
		r = pico4_cam_start(cam);
		if (r < 0) {
			error = "start failed";
		}
	} else if (error == NULL) {
		error = "open failed";
	}
	if (r == 0 && station != NULL) {
		r = pico4_station_camera_start(station);
		if (r < 0) {
			fprintf(stderr, "pico4-cam: station camera start: %s\n", strerror(-r));
			error = "station refused frame sync (are all four sensors streaming?)";
		} else {
			station_started = true;
			if (exposure != 0) {
				g_save_frame = 10;
				pico4_station_camera_set_exposure(station, (unsigned)exposure, (unsigned)gain);
			}
			polling = pthread_create(&poller, NULL, station_thread, station) == 0;
		}
	}
	while (r == 0 && !g_stop) {
		struct pico4_cam_frame f;
		bool all = true;
		for (int s = 0; s < PICO4_CAM_NUM; s++) {
			all = all && (!((cfg->slots >> s) & 1) || st[s].frames >= frames);
		}
		if (all) {
			break;
		}
		r = pico4_cam_next(cam, &f, (int)timeout);
		if (r == -EINTR) {
			r = 0;
			continue; // g_stop is set
		}
		if (r < 0) {
			error = r == -ETIMEDOUT ? "timeout waiting for frames" : "capture failed";
			break;
		}
		if (st[f.slot].frames < frames) {
			on_frame(&st[f.slot], &f, dir);
			if (stats_log != NULL) {
				log_frame(stats_log, &f);
			}
		}
	}
	if (g_stop && error == NULL) {
		error = "interrupted";
	}

	if (polling) {
		g_station_stop = 1;
		pthread_join(poller, NULL);
	}
	if (station_started) {
		pico4_station_camera_stop(station);
	}
	printf("{\n  \"fps\": %u,\n  \"station\": %s,\n  \"frames_requested\": %lu,\n", cfg->fps,
	       use_station ? "true" : "false", frames);
	printf("  \"slots\": [");
	for (int s = 0; s < PICO4_CAM_NUM; s++) {
		const struct slot_stats *t = &st[s];
		struct pico4_cam_slot_info si;
		double span, fps = 0.0;
		if (!((cfg->slots >> s) & 1)) {
			continue;
		}
		memset(&si, 0, sizeof(si));
		if (cam != NULL) {
			pico4_cam_get_slot_info(cam, s, &si);
		}
		span = (double)(t->last_ts - t->first_ts) / 1e9;
		if (t->with_ts > 1 && span > 0.0) {
			fps = (t->with_ts - 1) / span;
		}
		printf("%s\n    {\"slot\": %d, \"frames\": %u, \"errors\": %llu, \"with_timestamp\": %u, ", first ? "" : ",",
		       s, t->frames, (unsigned long long)si.errors, t->with_ts);
		printf("\"measured_fps\": %.3f, \"timestamps_monotonic\": %s, ", fps, t->monotonic ? "true" : "false");
		printf("\"first_request\": %llu, \"sof_frames_spanned\": %llu, ", (unsigned long long)t->first_request,
		       (unsigned long long)(t->with_ts > 0 ? t->last_frame_id - t->first_frame_id + 1 : 0));
		printf("\"first_sof_boottime_ns\": %lld, ", (long long)t->first_ts);
		if (t->count > 0) {
			printf("\"min\": %u, \"mean\": %.2f, \"max\": %u, ", t->min, (double)t->sum / (double)t->count, t->max);
		} else {
			printf("\"min\": null, \"mean\": null, \"max\": null, ");
		}
		printf("\"pgm\": ");
		json_str(t->saved ? t->pgm : "");
		printf(", \"raw\": ");
		json_str(t->saved ? t->raw : "");
		if (t->save_error != 0) {
			printf(", \"save_error\": ");
			json_str(strerror(-t->save_error));
		}
		printf(", \"csiphy\": %d, \"lane_assign\": %u}", si.phy, si.lane_assign);
		ok = ok && t->frames >= frames && t->monotonic && t->save_error == 0;
		first = false;
	}
	ok = ok && error == NULL;
	printf("\n  ],\n  \"error\": ");
	if (error != NULL) {
		json_str(error);
	} else {
		printf("null");
	}
	printf(",\n  \"ok\": %s\n}\n", ok ? "true" : "false");
	fflush(stdout);
	pico4_cam_close(cam);
	pico4_station_close(station);
	if (stats_log != NULL) {
		fclose(stats_log);
	}
	return ok ? 0 : 1;
}

int
main(int argc, char **argv)
{
	struct pico4_cam_config cfg;
	int i = 1;

	pico4_cam_config_init(&cfg);
	for (; i < argc && argv[i][0] == '-'; i++) {
		if (strcmp(argv[i], "-v") == 0) {
			cfg.verbose = PICO4_CAM_LOG_INFO;
		} else if (strcmp(argv[i], "-vv") == 0) {
			cfg.verbose = PICO4_CAM_LOG_DEBUG;
		} else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
			usage(stdout);
			return 0;
		} else {
			usage(stderr);
			return 2;
		}
	}
	if (i >= argc) {
		usage(stderr);
		return 2;
	}
	// Verbosity flags may also follow the command.
	for (int j = i + 1; j < argc; j++) {
		if (strcmp(argv[j], "-v") == 0 || strcmp(argv[j], "-vv") == 0) {
			cfg.verbose = argv[j][2] == 'v' ? PICO4_CAM_LOG_DEBUG : PICO4_CAM_LOG_INFO;
			memmove(&argv[j], &argv[j + 1], (size_t)(argc - j - 1) * sizeof(argv[0]));
			argc--;
			j--;
		}
	}

	if (strcmp(argv[i], "list") == 0 && argc == i + 1) {
		return cmd_list(&cfg);
	}
	if (strcmp(argv[i], "probe") == 0) {
		if (argc > i + 1) {
			unsigned mask = 0;
			for (int j = i + 1; j < argc; j++) {
				unsigned long v;
				if (!parse_uint(argv[j], PICO4_CAM_NUM - 1, &v)) {
					usage(stderr);
					return 2;
				}
				mask |= 1u << v;
			}
			cfg.slots = mask;
		}
		return cmd_probe(&cfg);
	}
	if (strcmp(argv[i], "capture") == 0) {
		return cmd_capture(&cfg, argc - i - 1, argv + i + 1);
	}
	usage(stderr);
	return 2;
}
