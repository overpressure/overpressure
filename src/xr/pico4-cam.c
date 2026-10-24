// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-cam: the four OV7251 tracking cameras through the Spectra CSL UAPI
 * (src/monado/pico4_cam.c).
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
 *                     [--exposure LINES [--gain G]] [--led-exposure LINES [--led-gain G]]
 *                     [--lines N] [--sensor-lines N] [--record-leds FILE] [--record-scene FILE]
 *                     [--record-imu FILE]
 *       Streams until every selected camera delivered N frames (default 30),
 *       saves an early scene frame of each camera as DIR/camS.pgm (8 bits, the
 *       640x480 scene rows) and DIR/camS.raw16 (the whole buffer, little-endian
 *       16-bit samples: the sensor's metadata line, then the scene rows), and
 *       prints a JSON summary: per slot the frame count, the frame rate from
 *       start-of-frame timestamps, whether those increase, min/mean/max sample
 *       values and the frames of each exposure phase from the metadata line. The station (controller radio) triggers the frames: it
 *       is power-cycled first (unless --no-station-reset) and told to start
 *       once the sensors stream. It refuses unless all four sensors are on, so
 *       --slots only selects which cameras are reported. --stats-log writes
 *       "slot sof_boottime_ns mean latency_ns request phase lines gain peak
 *       saturated" per frame (mean of a sparse grid; latency from start of
 *       frame to delivery; the brightest sample and the samples at 1023;
 *       the applied exposure from the metadata line, phase 0 if it does not
 *       decode), for example to find which slot goes dark when a lens is
 *       covered. --lines and --sensor-lines (480-482) override the lines the
 *       ISP writes and the height programmed into the sensor.
 *       --exposure (sensor lines, at most about 570) and --gain (16 = 1x)
 *       ask the station for that exposure; the saved frame is then the
 *       tenth or eleventh, after the setting took effect. Every other frame
 *       is the station's short controller-LED exposure; the saved frame is a
 *       scene frame by its metadata (or the brighter of two if it has none).
 *       --led-exposure and --led-gain (default 4 and 26, at most 10 and 32)
 *       fill the command's second set, which leaves both phases alone: the
 *       station keeps the LED phase at 4 lines and gain 26.
 *       --record-leds appends every controller-LED exposure of the selected
 *       cameras to FILE, for the controller tracking work: per frame a 48-byte
 *       header ("P4LF", version 1, slot, the ISP frame counter, start of frame
 *       on CLOCK_BOOTTIME and CLOCK_MONOTONIC in ns, the applied exposure lines
 *       and gain, width, height; little-endian) and the scene rows as 8 bits
 *       (the 10-bit samples shifted right by two). --record-scene writes the
 *       scene exposures (the head-tracking frames) the same way.
 *       --record-imu appends the controllers' radio packets (the sensor
 *       service's ble_mcu events, with their IMU and input records) and the
 *       head IMU to FILE: records of an 8-byte header ("P4L", kind I, H or C,
 *       version 1, a pad byte, the length that follows as u16), I = the event's
 *       QTimer ticks (u64) and the raw packet, H = a head IMU event's QTimer
 *       ticks (u64), the sensor (u8: 0 accelerometer, 1 gyroscope) and its three
 *       values (float, m/s^2 or rad/s, raw axes = the factory body frame), C
 *       every 100 ms = a CLOCK_MONOTONIC time in ns and the QTimer at that
 *       moment (u64 each), to put both on one clock. The head IMU runs at 1 kHz
 *       as in the driver: without another sensor streaming, the sensor DSP slows
 *       ble_mcu to about 90 events per second a few minutes after it started.
 *
 * -v and -vv (before or after the command) log progress and every ioctl to
 * stderr. Exit status 0 means success: every node found (list), every probe
 * answered (probe), or every camera delivered N frames with increasing
 * timestamps (capture).
 */

#define _GNU_SOURCE // sigaction, mkdir under -std=c11

#include "pico4_cam.h"
#include "pico4_ssc.h"
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
	      "                 [--no-station-reset] [--stats-log FILE] [--exposure LINES [--gain G]]\n"
	      "                 [--led-exposure LINES [--led-gain G]] [--lines N] [--sensor-lines N]\n"
	      "                 [--record-leds FILE] [--record-scene FILE] [--record-imu FILE]\n",
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
	unsigned phases[3]; //!< Frames by metadata: scene, controller LEDs, other or undecodable.
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
		fwrite((const uint8_t *)f->meta + (size_t)y * f->stride, f->bytes_per_sample, f->width, raw);
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

//! The brightest sample and how many reach the 10-bit maximum (saturated controller LEDs).
static unsigned
frame_peak(const struct pico4_cam_frame *f, unsigned *out_saturated)
{
	unsigned peak = 0, saturated = 0;
	for (unsigned y = 0; y < f->height; y++) {
		const uint8_t *line = (const uint8_t *)f->data + (size_t)y * f->stride;
		for (unsigned x = 0; x < f->width; x++) {
			unsigned v = sample(f, line, x);
			peak = v > peak ? v : peak;
			saturated += v >= 1023;
		}
	}
	*out_saturated = saturated;
	return peak;
}

static void
log_frame(FILE *log, const struct pico4_cam_frame *f)
{
	struct timespec ts;
	clock_gettime(CLOCK_BOOTTIME, &ts);
	long long now = (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
	struct pico4_cam_meta m = {0};
	if (!pico4_cam_meta_decode(f->meta, f->stride, &m)) {
		m.phase = 0;
	}
	unsigned saturated, peak = frame_peak(f, &saturated);
	fprintf(log, "%d %lld %.2f %lld %llu %u %u %u %u %u\n", f->slot, (long long)f->sof_boottime_ns, grid_mean(f),
	        now - (long long)f->sof_boottime_ns, (unsigned long long)f->request_id, m.phase, m.exposure_lines,
	        m.gain, peak, saturated);
}

//! One exposure for --record-leds or --record-scene (format in the header comment); false on a write error.
static bool
record_frame(FILE *out, const struct pico4_cam_frame *f, const struct pico4_cam_meta *m)
{
	uint8_t head[48] = {'P', '4', 'L', 'F', 1, (uint8_t)f->slot};
	uint64_t id = f->frame_id, boot = (uint64_t)f->sof_boottime_ns, mono = (uint64_t)f->sof_monotonic_ns;
	uint16_t lines = (uint16_t)m->exposure_lines, gain = (uint16_t)m->gain;
	uint16_t width = (uint16_t)f->width, height = (uint16_t)f->height;
	memcpy(head + 8, &id, 8);
	memcpy(head + 16, &boot, 8);
	memcpy(head + 24, &mono, 8);
	memcpy(head + 32, &lines, 2);
	memcpy(head + 34, &gain, 2);
	memcpy(head + 36, &width, 2);
	memcpy(head + 38, &height, 2);
	if (fwrite(head, sizeof(head), 1, out) != 1) {
		return false;
	}
	uint8_t row[PICO4_CAM_WIDTH];
	for (unsigned y = 0; y < f->height; y++) {
		const uint8_t *line = (const uint8_t *)f->data + (size_t)y * f->stride;
		for (unsigned x = 0; x < f->width && x < PICO4_CAM_WIDTH; x++) {
			row[x] = (uint8_t)(sample(f, line, x) >> 2);
		}
		if (fwrite(row, 1, f->width, out) != f->width) {
			return false;
		}
	}
	return true;
}

static void
on_frame(struct slot_stats *st, const struct pico4_cam_frame *f, const char *dir)
{
	if (st->frames == 0) {
		st->first_request = f->request_id;
	}
	// The station alternates scene and controller-LED exposures; the metadata says which this is.
	struct pico4_cam_meta m;
	bool decoded = pico4_cam_meta_decode(f->meta, f->stride, &m);
	st->phases[!decoded ? 2 : m.phase == PICO4_CAM_PHASE_SCENE ? 0 : m.phase == PICO4_CAM_PHASE_LEDS ? 1 : 2]++;
	if (dir != NULL && (st->frames == g_save_frame || st->frames == g_save_frame + 1)) {
		double mean = grid_mean(f);
		bool scene = decoded ? m.phase == PICO4_CAM_PHASE_SCENE : st->frames == g_save_frame || mean > st->saved_mean;
		if (scene) {
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

//! The AP's counter, the QTimer that also stamps the sensor service's events (aarch64 only).
static uint64_t
qtimer_ticks(void)
{
#if defined(__aarch64__)
	uint64_t value;
	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(value)::"memory");
	return value;
#else
	return 0;
#endif
}

//! Appends one --record-imu record: "P4LI", "P4LH" or "P4LC", version 1, a pad byte, the length of what follows (u16).
static bool
record_imu_item(FILE *out, char kind, const void *data, uint16_t len)
{
	uint8_t head[8] = {'P', '4', 'L', (uint8_t)kind, 1, 0};
	memcpy(head + 6, &len, 2);
	return fwrite(head, sizeof(head), 1, out) == 1 && (len == 0 || fwrite(data, len, 1, out) == 1);
}

struct imu_recorder
{
	FILE *out;
	int error; //!< 0, or the negative errno that ended the recording.
	uint64_t packets;
};

static bool
suid_is(const struct pico4_ssc_sample *s, const struct pico4_ssc_suid *suid)
{
	return s->suid.low == suid->low && s->suid.high == suid->high;
}

/*!
 * --record-imu: the controllers' ble_mcu packets and the head IMU through the sensor
 * service, while the station runs. Each packet record (I) holds its event time in QTimer
 * ticks (u64) and the raw packet; each head record (H) its event time, the sensor and its
 * three values; every 100 ms a clock record (C) holds a CLOCK_MONOTONIC time in ns and the
 * QTimer read between two such readings (u64 each, the pair with the shortest window).
 */
static void *
imu_record_thread(void *arg)
{
	struct imu_recorder *rec = arg;
	struct pico4_ssc *ssc = NULL;
	// ble_mcu, accel, gyro: the head IMU keeps the DSP from slowing ble_mcu down.
	struct pico4_ssc_suid suids[3];
	static const char *const types[3] = {"ble_mcu", "accel", "gyro"};
	int r = -ETIMEDOUT;
	// The station's controllers show up in the sensor service once it runs.
	for (int attempt = 0; attempt < 10 && !g_station_stop && r < 0; attempt++) {
		r = pico4_ssc_subscribe(&ssc, types, suids, 3, 1000.0f, 1000);
	}
	if (r < 0) {
		rec->error = r;
		return NULL;
	}
	struct pico4_ssc_sample samples[32];
	struct timespec last_clock = {0};
	while (!g_station_stop && rec->error == 0) {
		struct timespec now;
		clock_gettime(CLOCK_MONOTONIC, &now);
		if ((now.tv_sec - last_clock.tv_sec) * 1000000000LL + (now.tv_nsec - last_clock.tv_nsec) > 100000000LL) {
			uint64_t pair[2] = {0, 0};
			int64_t best = INT64_MAX;
			for (int i = 0; i < 8; i++) {
				struct timespec a, b;
				clock_gettime(CLOCK_MONOTONIC, &a);
				uint64_t ticks = qtimer_ticks();
				clock_gettime(CLOCK_MONOTONIC, &b);
				int64_t ta = a.tv_sec * 1000000000LL + a.tv_nsec, tb = b.tv_sec * 1000000000LL + b.tv_nsec;
				if (tb - ta < best) {
					best = tb - ta;
					pair[0] = (uint64_t)(ta + (tb - ta) / 2);
					pair[1] = ticks;
				}
			}
			if (!record_imu_item(rec->out, 'C', pair, sizeof(pair))) {
				rec->error = -EIO;
			}
			last_clock = now;
		}
		int n = pico4_ssc_read(ssc, 100, samples, (int)(sizeof(samples) / sizeof(samples[0])));
		if (n < 0) {
			rec->error = n;
			break;
		}
		for (int i = 0; i < n && rec->error == 0; i++) {
			const struct pico4_ssc_sample *s = &samples[i];
			if (suid_is(s, &suids[0])) {
				uint8_t item[8 + PICO4_SSC_RAW_MAX];
				memcpy(item, &s->ticks, 8);
				memcpy(item + 8, s->raw, s->raw_len);
				if (!record_imu_item(rec->out, 'I', item, (uint16_t)(8 + s->raw_len))) {
					rec->error = -EIO;
				}
				rec->packets++;
			} else if ((suid_is(s, &suids[1]) || suid_is(s, &suids[2])) && s->count >= 3) {
				uint8_t item[8 + 1 + 12];
				memcpy(item, &s->ticks, 8);
				item[8] = suid_is(s, &suids[1]) ? 0 : 1;
				memcpy(item + 9, s->values, 12);
				if (!record_imu_item(rec->out, 'H', item, sizeof(item))) {
					rec->error = -EIO;
				}
			}
		}
	}
	for (int i = 0; i < 3; i++) {
		pico4_ssc_disable(ssc, &suids[i]);
	}
	pico4_ssc_close(&ssc);
	return NULL;
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
	FILE *stats_log = NULL, *led_record = NULL, *scene_record = NULL;
	struct imu_recorder imu = {0};
	pthread_t imu_thread;
	bool imu_running = false;
	unsigned long exposure = 0, gain = 16, led_exposure = 4, led_gain = 26;
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
		} else if (strcmp(a, "--led-exposure") == 0 && parse_uint(v, 1700, &led_exposure)) {
		} else if (strcmp(a, "--led-gain") == 0 && parse_uint(v, 1023, &led_gain)) {
		} else if (strcmp(a, "--stats-log") == 0) {
			if (stats_log != NULL) {
				fclose(stats_log);
			}
			stats_log = fopen(v, "we");
			if (stats_log == NULL) {
				fprintf(stderr, "pico4-cam: cannot write %s: %s\n", v, strerror(errno));
				return 1;
			}
		} else if (strcmp(a, "--record-imu") == 0) {
			if (imu.out != NULL) {
				fclose(imu.out);
			}
			imu.out = fopen(v, "we");
			if (imu.out == NULL) {
				fprintf(stderr, "pico4-cam: cannot write %s: %s\n", v, strerror(errno));
				return 1;
			}
		} else if (strcmp(a, "--record-leds") == 0 || strcmp(a, "--record-scene") == 0) {
			FILE **out = a[9] == 'l' ? &led_record : &scene_record;
			if (*out != NULL) {
				fclose(*out);
			}
			*out = fopen(v, "we");
			if (*out == NULL) {
				fprintf(stderr, "pico4-cam: cannot write %s: %s\n", v, strerror(errno));
				return 1;
			}
		} else if (strcmp(a, "--lines") == 0 && parse_uint(v, PICO4_CAM_HEIGHT + 2, &n) && n >= PICO4_CAM_HEIGHT) {
			cfg->lines = (unsigned)n;
		} else if (strcmp(a, "--sensor-lines") == 0 && parse_uint(v, PICO4_CAM_HEIGHT + 2, &n) &&
		           n >= PICO4_CAM_HEIGHT) {
			cfg->sensor_lines = (unsigned)n;
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
				struct pico4_station_exposure scene = {(unsigned)exposure, (unsigned)gain};
				struct pico4_station_exposure leds = {(unsigned)led_exposure, (unsigned)led_gain};
				pico4_station_camera_set_exposures(station, scene, leds);
			}
			polling = pthread_create(&poller, NULL, station_thread, station) == 0;
			imu_running = imu.out != NULL && pthread_create(&imu_thread, NULL, imu_record_thread, &imu) == 0;
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
			struct pico4_cam_meta m;
			bool decoded = (led_record != NULL || scene_record != NULL) && pico4_cam_meta_decode(f.meta, f.stride, &m);
			if (decoded && led_record != NULL && m.phase == PICO4_CAM_PHASE_LEDS && !record_frame(led_record, &f, &m)) {
				error = "writing the LED record failed";
				r = -EIO;
			}
			if (decoded && scene_record != NULL && m.phase == PICO4_CAM_PHASE_SCENE &&
			    !record_frame(scene_record, &f, &m)) {
				error = "writing the scene record failed";
				r = -EIO;
			}
		}
	}
	if (g_stop && error == NULL) {
		error = "interrupted";
	}

	g_station_stop = 1;
	if (polling) {
		pthread_join(poller, NULL);
	}
	if (imu_running) {
		pthread_join(imu_thread, NULL);
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
		printf(", \"phases\": {\"scene\": %u, \"leds\": %u, \"other\": %u}", t->phases[0], t->phases[1],
		       t->phases[2]);
		printf(", \"csiphy\": %d, \"lane_assign\": %u}", si.phy, si.lane_assign);
		ok = ok && t->frames >= frames && t->monotonic && t->save_error == 0;
		first = false;
	}
	ok = ok && error == NULL;
	printf("\n  ],\n");
	if (imu.out != NULL) {
		printf("  \"imu_packets\": %llu,\n  \"imu_error\": ", (unsigned long long)imu.packets);
		if (imu.error != 0 || !imu_running) {
			json_str(imu_running ? strerror(-imu.error) : "not started");
		} else {
			printf("null");
		}
		printf(",\n");
		ok = ok && imu_running && imu.error == 0 && imu.packets > 0;
	}
	printf("  \"error\": ");
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
	if (led_record != NULL && fclose(led_record) != 0) {
		ok = false;
	}
	if (scene_record != NULL && fclose(scene_record) != 0) {
		ok = false;
	}
	if (imu.out != NULL && fclose(imu.out) != 0) {
		ok = false;
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
