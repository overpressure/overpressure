// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 passthrough frames (see pico4_passthrough.h).
 * @ingroup drv_pico4
 */

#include "pico4_passthrough.h"
#include "pico4_roomview.h"
#include "pico4_tcam.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define BLACK_LEVEL 16
//! Factory cameras of the forward pair: bottom left, bottom right.
static const int FACTORY[PICO4_PASSTHROUGH_CAMS] = {3, 2};
//! Who wants frames (pico4_passthrough::streaming).
#define STREAM_CLIENT 1u
#define STREAM_ROOMVIEW 2u

struct pico4_passthrough
{
	struct pico4_passthrough_info info;
	pthread_mutex_t lock;
	pthread_cond_t cond;
	unsigned streaming; //!< STREAM_* bits.
	//! The newest set, guarded by lock.
	uint16_t *raw[PICO4_PASSTHROUGH_CAMS];
	struct pico4_passthrough_frame frame;
	float gain;

	//! The room view's export (pico4_passthrough_export).
	struct pico4_roomview *roomview;
	pthread_t export_thread;
	bool exporting, export_stop;
	pico4_passthrough_pose_fn pose;
	void *pose_ctx;
};

static char *
read_file(const char *path)
{
	FILE *f = fopen(path, "rb");
	if (f == NULL) {
		return NULL;
	}
	char *text = calloc(1, 1 << 16);
	if (text != NULL && fread(text, 1, (1 << 16) - 1, f) == 0) {
		free(text);
		text = NULL;
	}
	fclose(f);
	return text;
}

//! a = b * c for row-major 4x4 rigid transforms.
static void
mul44(double a[16], const double b[16], const double c[16])
{
	for (int r = 0; r < 4; r++) {
		for (int col = 0; col < 4; col++) {
			double v = 0;
			for (int i = 0; i < 4; i++) {
				v += b[r * 4 + i] * c[i * 4 + col];
			}
			a[r * 4 + col] = v;
		}
	}
}

//! The inverse of a row-major rigid transform.
static void
invert_rigid(double out[16], const double t[16])
{
	memset(out, 0, 16 * sizeof(double));
	for (int r = 0; r < 3; r++) {
		for (int c = 0; c < 3; c++) {
			out[r * 4 + c] = t[c * 4 + r];
		}
		out[r * 4 + 3] = -(t[0 * 4 + r] * t[3] + t[1 * 4 + r] * t[7] + t[2 * 4 + r] * t[11]);
	}
	out[15] = 1;
}

struct pico4_passthrough *
pico4_passthrough_create(const char *calibration_dir, const double down_body[3], const double T_body_head[16])
{
	struct pico4_passthrough *p = calloc(1, sizeof(*p));
	if (p == NULL) {
		return NULL;
	}
	double T_head_body[16];
	invert_rigid(T_head_body, T_body_head);
	// OpenVR's camera axes (x right, y up, looking along -z) from the computer-vision ones.
	static const double CV_GL[16] = {1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1};
	for (int i = 0; i < PICO4_PASSTHROUGH_CAMS; i++) {
		char path[512];
		snprintf(path, sizeof(path), "%s/pvr/mav0/cam%d/sensor.yaml", calibration_dir, FACTORY[i]);
		char *text = read_file(path);
		struct pico4_tcam_omni omni;
		struct pico4_tcam_kb4 kb4;
		bool ok = text != NULL && pico4_tcam_parse_yaml(text, &omni) && pico4_tcam_fit_kb4(&omni, &kb4) &&
		          omni.width == PICO4_PASSTHROUGH_SRC_W && omni.height == PICO4_PASSTHROUGH_SRC_H;
		free(text);
		if (!ok) {
			free(p->raw[0]);
			free(p);
			return NULL;
		}
		int w, h;
		p->info.turn[i] = pico4_tcam_upright(&kb4, omni.T_BS, omni.width, omni.height, down_body, &w, &h);
		p->info.width = (uint32_t)w;
		p->info.height = (uint32_t)h;
		p->info.fx[i] = kb4.fx;
		p->info.fy[i] = kb4.fy;
		p->info.cx[i] = kb4.cx;
		p->info.cy[i] = kb4.cy;
		memcpy(p->info.k[i], kb4.k, sizeof(kb4.k));
		double T_head_cam[16], T_gl[16];
		mul44(T_head_cam, T_head_body, omni.T_BS);
		mul44(T_gl, T_head_cam, CV_GL);
		memcpy(p->info.camera_to_head[i], T_gl, 12 * sizeof(double));
		p->raw[i] = calloc(PICO4_PASSTHROUGH_SRC_W * PICO4_PASSTHROUGH_SRC_H, sizeof(uint16_t));
		if (p->raw[i] == NULL) {
			free(p->raw[0]);
			free(p);
			return NULL;
		}
	}
	pthread_mutex_init(&p->lock, NULL);
	pthread_condattr_t attr;
	pthread_condattr_init(&attr);
	pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
	pthread_cond_init(&p->cond, &attr);
	pthread_condattr_destroy(&attr);
	return p;
}

void
pico4_passthrough_info(const struct pico4_passthrough *p, struct pico4_passthrough_info *out)
{
	*out = p->info;
}

static void
set_stream_bit(struct pico4_passthrough *p, unsigned bit, bool on)
{
	if (on) {
		__atomic_fetch_or(&p->streaming, bit, __ATOMIC_RELEASE);
	} else {
		__atomic_fetch_and(&p->streaming, ~bit, __ATOMIC_RELEASE);
	}
}

void
pico4_passthrough_set_streaming(struct pico4_passthrough *p, bool on)
{
	set_stream_bit(p, STREAM_CLIENT, on);
}

bool
pico4_passthrough_streaming(const struct pico4_passthrough *p)
{
	return p != NULL && __atomic_load_n(&p->streaming, __ATOMIC_ACQUIRE) != 0;
}

void
pico4_passthrough_push(struct pico4_passthrough *p,
                       const uint16_t *left,
                       const uint16_t *right,
                       uint32_t stride,
                       int64_t timestamp_ns,
                       uint32_t exposure_lines,
                       float gain)
{
	if (!pico4_passthrough_streaming(p) || pthread_mutex_trylock(&p->lock) != 0) {
		return; // nobody streams, or the client is converting the set before: never wait here
	}
	const uint16_t *src[PICO4_PASSTHROUGH_CAMS] = {left, right};
	for (int i = 0; i < PICO4_PASSTHROUGH_CAMS; i++) {
		for (uint32_t row = 0; row < PICO4_PASSTHROUGH_SRC_H; row++) {
			memcpy(p->raw[i] + row * PICO4_PASSTHROUGH_SRC_W, src[i] + (size_t)row * stride,
			       PICO4_PASSTHROUGH_SRC_W * sizeof(uint16_t));
		}
	}
	p->frame.sequence++;
	p->frame.timestamp_ns = timestamp_ns;
	p->frame.exposure_us = (uint32_t)((uint64_t)exposure_lines * 19069 / 1000); // 19.07 us lines
	p->gain = gain;
	pthread_cond_broadcast(&p->cond);
	pthread_mutex_unlock(&p->lock);
}

void
pico4_passthrough_convert(const uint16_t *src, uint32_t src_stride, int turn, float gain, uint8_t *dst,
                          uint32_t dst_stride)
{
	const int w = PICO4_PASSTHROUGH_SRC_W, h = PICO4_PASSTHROUGH_SRC_H;
	const int dw = turn % 2 == 0 ? w : h, dh = turn % 2 == 0 ? h : w;
	const uint32_t g = (uint32_t)(gain * 256.0f);
	for (int v = 0; v < dh; v++) {
		uint8_t *out = dst + (size_t)v * dst_stride;
		for (int u = 0; u < dw; u++) {
			// The source pixel of (u, v), as pico4_tcam_upright describes the turns.
			int su = turn == 0 ? u : turn == 1 ? v : turn == 2 ? w - 1 - u : w - 1 - v;
			int sv = turn == 0 ? v : turn == 1 ? h - 1 - u : turn == 2 ? h - 1 - v : u;
			uint32_t s = src[(size_t)sv * src_stride + (size_t)su] & 0x3ff;
			s = s > BLACK_LEVEL ? s - BLACK_LEVEL : 0;
			s = (s * g) >> 10; // 10 bits to 8, times the gain
			out[u] = (uint8_t)(s > 255 ? 255 : s);
		}
	}
}

bool
pico4_passthrough_wait(struct pico4_passthrough *p,
                       uint64_t after,
                       int timeout_ms,
                       uint8_t *y,
                       uint32_t stride,
                       struct pico4_passthrough_frame *out)
{
	struct timespec until;
	clock_gettime(CLOCK_MONOTONIC, &until);
	until.tv_sec += timeout_ms / 1000;
	until.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
	if (until.tv_nsec >= 1000000000L) {
		until.tv_sec++;
		until.tv_nsec -= 1000000000L;
	}
	pthread_mutex_lock(&p->lock);
	while (p->frame.sequence <= after) {
		if (pthread_cond_timedwait(&p->cond, &p->lock, &until) == ETIMEDOUT) {
			pthread_mutex_unlock(&p->lock);
			return false;
		}
	}
	for (int i = 0; i < PICO4_PASSTHROUGH_CAMS; i++) {
		pico4_passthrough_convert(p->raw[i], PICO4_PASSTHROUGH_SRC_W, p->info.turn[i], p->gain,
		                          y + (size_t)i * p->info.height * stride, stride);
	}
	*out = p->frame;
	pthread_mutex_unlock(&p->lock);
	return true;
}

static int64_t
monotonic_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000000000ll + ts.tv_nsec;
}

//! Streams the forward pair into the room view's ring while its producer asks for frames.
static void *
export_run(void *arg)
{
	struct pico4_passthrough *p = arg;
	struct pico4_roomview *rv = p->roomview;
	uint64_t after = 0;
	while (!__atomic_load_n(&p->export_stop, __ATOMIC_ACQUIRE)) {
		int64_t seen = __atomic_load_n(&rv->producer_ns, __ATOMIC_ACQUIRE);
		bool want = __atomic_load_n(&rv->requested, __ATOMIC_ACQUIRE) != 0 &&
		            monotonic_ns() - seen < PICO4_ROOMVIEW_PRODUCER_TIMEOUT_NS;
		set_stream_bit(p, STREAM_ROOMVIEW, want);
		if (!want) {
			usleep(50 * 1000);
			continue;
		}
		uint64_t n = __atomic_load_n(&rv->frames_written, __ATOMIC_RELAXED);
		struct pico4_roomview_frame *f = &rv->frames[n % PICO4_ROOMVIEW_FRAMES];
		__atomic_store_n(&f->sequence, 0, __ATOMIC_RELEASE);
		__atomic_thread_fence(__ATOMIC_SEQ_CST);
		struct pico4_passthrough_frame frame;
		if (!pico4_passthrough_wait(p, after, 200, f->pixels[0], p->info.width, &frame)) {
			continue;
		}
		after = frame.sequence;
		f->timestamp_ns = frame.timestamp_ns;
		f->exposure_us = frame.exposure_us;
		f->pose_valid = p->pose != NULL && p->pose(p->pose_ctx, frame.timestamp_ns, f->tracking_from_head);
		__atomic_store_n(&f->sequence, frame.sequence, __ATOMIC_RELEASE);
		__atomic_store_n(&rv->frames_written, n + 1, __ATOMIC_RELEASE);
	}
	set_stream_bit(p, STREAM_ROOMVIEW, false);
	return NULL;
}

bool
pico4_passthrough_export(struct pico4_passthrough *p, const char *path, pico4_passthrough_pose_fn pose, void *ctx)
{
	if (p->exporting) {
		return true;
	}
	// The frames stack the two cameras: pico4_passthrough_wait writes left above right.
	if ((size_t)p->info.width * p->info.height > PICO4_ROOMVIEW_PIXELS) {
		return false;
	}
	int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
	if (fd < 0) {
		return false;
	}
	if (ftruncate(fd, sizeof(struct pico4_roomview)) != 0) {
		close(fd);
		return false;
	}
	struct pico4_roomview *rv = mmap(NULL, sizeof(*rv), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	close(fd);
	if (rv == MAP_FAILED) {
		return false;
	}
	// Kept in place across starts (pico4-roomview keeps its mapping); a new generation each start.
	uint32_t generation = rv->magic == PICO4_ROOMVIEW_MAGIC ? rv->generation + 1 : 1;
	__atomic_store_n(&rv->magic, 0, __ATOMIC_RELEASE);
	rv->version = 1;
	rv->generation = generation;
	rv->width = p->info.width;
	rv->height = p->info.height;
	for (int i = 0; i < PICO4_ROOMVIEW_CAMS; i++) {
		rv->fx[i] = p->info.fx[i];
		rv->fy[i] = p->info.fy[i];
		rv->cx[i] = p->info.cx[i];
		rv->cy[i] = p->info.cy[i];
		memcpy(rv->k[i], p->info.k[i], sizeof(rv->k[i]));
		memcpy(rv->camera_to_head[i], p->info.camera_to_head[i], sizeof(rv->camera_to_head[i]));
	}
	for (int i = 0; i < PICO4_ROOMVIEW_FRAMES; i++) {
		rv->frames[i].sequence = 0;
	}
	__atomic_store_n(&rv->magic, PICO4_ROOMVIEW_MAGIC, __ATOMIC_RELEASE);
	p->roomview = rv;
	p->pose = pose;
	p->pose_ctx = ctx;
	p->export_stop = false;
	if (pthread_create(&p->export_thread, NULL, export_run, p) != 0) {
		munmap(rv, sizeof(*rv));
		p->roomview = NULL;
		return false;
	}
	p->exporting = true;
	return true;
}

bool
pico4_passthrough_newest_head(const struct pico4_passthrough *p,
                              uint64_t *out_sequence,
                              int64_t *out_timestamp_ns,
                              double out_tracking_from_head[12])
{
	const struct pico4_roomview *rv = p != NULL && p->exporting ? p->roomview : NULL;
	if (rv == NULL) {
		return false;
	}
	uint64_t n = __atomic_load_n(&rv->frames_written, __ATOMIC_ACQUIRE);
	if (n == 0) {
		return false;
	}
	const struct pico4_roomview_frame *f = &rv->frames[(n - 1) % PICO4_ROOMVIEW_FRAMES];
	uint64_t seq = __atomic_load_n(&f->sequence, __ATOMIC_ACQUIRE);
	int64_t timestamp_ns = f->timestamp_ns;
	bool valid = f->pose_valid != 0;
	memcpy(out_tracking_from_head, f->tracking_from_head, sizeof(f->tracking_from_head));
	__atomic_thread_fence(__ATOMIC_ACQUIRE);
	// The export writes this slot again only after three more frames; seen as a changed sequence.
	if (seq == 0 || !valid || __atomic_load_n(&f->sequence, __ATOMIC_RELAXED) != seq) {
		return false;
	}
	*out_sequence = seq;
	*out_timestamp_ns = timestamp_ns;
	return true;
}

void
pico4_passthrough_destroy(struct pico4_passthrough **pp)
{
	struct pico4_passthrough *p = *pp;
	if (p == NULL) {
		return;
	}
	if (p->exporting) {
		__atomic_store_n(&p->export_stop, true, __ATOMIC_RELEASE);
		pthread_join(p->export_thread, NULL);
		munmap(p->roomview, sizeof(*p->roomview));
	}
	pthread_mutex_destroy(&p->lock);
	pthread_cond_destroy(&p->cond);
	for (int i = 0; i < PICO4_PASSTHROUGH_CAMS; i++) {
		free(p->raw[i]);
	}
	free(p);
	*pp = NULL;
}
