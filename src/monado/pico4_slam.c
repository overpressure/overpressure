// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 6DoF head tracking through Monado's SLAM tracker (see pico4_slam.h).
 *
 * The four OV7251s (pico4_cam.h) only send frames while the controller radio
 * pulses their shared FSIN line (pico4_station.h), so all cameras expose
 * together. The station alternates two exposures: every other frame is a
 * short one in which only the controllers' infrared LEDs show (for
 * controller tracking). Those are skipped; the scene frames are told apart
 * by brightness and their place in the alternation, which survives dropped
 * frames. Frames of one pulse are grouped by their start-of-frame time,
 * converted from 10 to 8 bits (black level removed, slowly adapting digital
 * gain) and pushed to the tracker in camera order with one timestamp, as
 * Basalt requires. The station also sets the exposure (the sensors' own
 * registers are ignored in this mode); its default of about 3 ms leaves
 * indoor scenes nearly black, so a slow auto-exposure loop raises it.
 *
 * Basalt's queues are bounded and block their producers when its optical flow
 * falls behind. Two threads therefore feed the tracker: one takes the newest
 * frame set from the camera thread (an older one still waiting is dropped),
 * the other drains a ring buffer of IMU samples from the HMD's IMU thread.
 * Neither capture nor the 3DoF and controller paths ever wait for Basalt, and
 * IMU samples keep flowing while a frame push blocks: Basalt's estimator waits
 * for them before its optical flow can take the next frame, so one thread
 * feeding both deadlocked. IMU samples arrive from
 * the HMD's sensor thread in the same body frame as the factory extrinsics.
 *
 * @ingroup drv_pico4
 */

#include "pico4_slam.h"

#include "xrt/xrt_config_build.h"

#ifdef XRT_FEATURE_SLAM

#include "pico4_cam.h"
#include "pico4_station.h"
#include "pico4_tcam.h"

#include "xrt/xrt_frame.h"
#include "xrt/xrt_tracking.h"

#include "math/m_api.h"
#include "os/os_threading.h"
#include "os/os_time.h"
#include "tracking/t_tracking.h"
#include "util/u_debug.h"
#include "util/u_frame.h"
#include "util/u_misc.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sched.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

DEBUG_GET_ONCE_BOOL_OPTION(pico4_slam, "PICO4_SLAM", true)
/*!
 * Factory cameras (cam0..cam3) given to the tracker, in tracker order. Basalt matches the
 * first camera against the others; all four keep up on the XR2 and see the most.
 */
DEBUG_GET_ONCE_OPTION(pico4_slam_cams, "PICO4_SLAM_CAMS", "0,1,2,3")
/*!
 * CSL sensor slot of each factory camera cam0..cam3 (top left, top right,
 * bottom right, bottom left as worn), found by covering one lens at a time.
 */
DEBUG_GET_ONCE_OPTION(pico4_tcam_slots, "PICO4_TCAM_SLOTS", "0,2,1,3")
//! Turn images that are upside down relative to the first camera's (the lower pair is mounted 180 degrees apart).
DEBUG_GET_ONCE_BOOL_OPTION(pico4_slam_upright, "PICO4_SLAM_UPRIGHT", true)
/*!
 * Use every Nth scene frame set (25 Hz: the station's 50 Hz alternates scene
 * and LED exposures) and IMU sample (512 Hz). Basalt's optical flow keeps up
 * with 25 Hz on the XR2; it does not need the full IMU rate.
 */
DEBUG_GET_ONCE_NUM_OPTION(pico4_slam_divisor, "PICO4_SLAM_DIVISOR", 1)
DEBUG_GET_ONCE_NUM_OPTION(pico4_slam_imu_divisor, "PICO4_SLAM_IMU_DIVISOR", 2)
//! Directory to save the frames of every 500th set as PGM (diagnostics).
DEBUG_GET_ONCE_OPTION(pico4_slam_dump, "PICO4_SLAM_DUMP", NULL)
//! Mean 8-bit brightness the digital gain aims for.
DEBUG_GET_ONCE_NUM_OPTION(pico4_slam_brightness, "PICO4_SLAM_BRIGHTNESS", 70)
//! Longest exposure in sensor lines (19.3 us each): with the 9 ms readout a frame must fit
//! the 20 ms sync period, and longer exposures smear more when moving.
DEBUG_GET_ONCE_NUM_OPTION(pico4_slam_max_exposure, "PICO4_SLAM_MAX_EXPOSURE", 400)
//! CPUs (bit mask) Basalt may use; 0 = no restriction. Default: all but cores 6 and 7.
DEBUG_GET_ONCE_NUM_OPTION(pico4_slam_cpus, "PICO4_SLAM_CPUS", 0x3f)

#define SLAM_TRACE(s, ...) U_LOG_IFL_T(s->log_level, __VA_ARGS__)
#define SLAM_DEBUG(s, ...) U_LOG_IFL_D(s->log_level, __VA_ARGS__)
#define SLAM_INFO(s, ...) U_LOG_IFL_I(s->log_level, __VA_ARGS__)
#define SLAM_ERROR(s, ...) U_LOG_IFL_E(s->log_level, __VA_ARGS__)

#define STATION_HZ 50.0
#define SCENE_HZ (STATION_HZ / 2) //!< Every other frame is a controller-LED exposure.
#define FRAME_NS (1000000000.0 / STATION_HZ)
//! First-camera frames per exposure phase before the scene phase is trusted.
#define PHASE_FRAMES 8
//! OV7251 10-bit black level.
#define BLACK_LEVEL 16
//! Auto exposure: mean signal (10-bit, above black) it aims for, and its limits.
#define AE_TARGET 250.0f
#define AE_MIN_LINES 10u
#define AE_MAX_GAIN 64u //!< 4x; more mostly adds noise for the optical flow
#define AE_SETS 25        //!< Sets per adjustment (half a second).
#define IMU_HZ 512.0 //!< What the DSP delivers for the 1 kHz request.
#define IMU_RING 2048
#define TRACKER_NICE 19 //!< Basalt and its feeders, below the compositor and the app (0).
//! Time Basalt gets at shutdown to process the frame sets still queued (10 at most).
#define TRACKER_DRAIN_NS (1000 * U_TIME_1MS_IN_NS)

//! Frames of one FSIN pulse start within this window.
#define GROUP_WINDOW_NS (4 * U_TIME_1MS_IN_NS)

/*!
 * Origin of the HMD frame (between the eyes) in the body frame (x up, y left,
 * z back): centred between the cameras, a little below and behind them.
 * Only affects how position follows head rotation.
 */
static const struct xrt_vec3 P_BODY_HMD = {-0.025f, 0.052f, 0.04f};

struct pico4_slam
{
	enum u_logging_level log_level;

	struct xrt_frame_context xfctx;
	struct xrt_tracked_slam *tracker;
	struct xrt_slam_sinks *sinks;
	struct t_slam_calibration calib;

	struct pico4_station *station;
	struct pico4_cam *cam;
	struct os_thread_helper oth;

	int cam_count;           //!< Cameras in the tracker.
	int index_of_slot[PICO4_CAM_NUM]; //!< Tracker index of each sensor slot, -1 if unused.
	bool rotated[PICO4_TCAM_COUNT];   //!< Image turned by 180 degrees (pico4_tcam_upside_down).
	unsigned divisor;

	//! The frame set being collected.
	struct
	{
		int64_t ts;
		int have;
		struct xrt_frame *frames[PICO4_TCAM_COUNT];
	} group;
	uint64_t sets_pushed, sets_dropped, sets_busy, sequence;

	//! Frame sets for the tracker, guarded by push_oth's lock.
	struct os_thread_helper push_oth;
	struct xrt_frame *mail[PICO4_TCAM_COUNT];
	bool mail_full;
	//! IMU samples for the tracker, guarded by imu_oth's lock.
	struct os_thread_helper imu_oth;
	struct xrt_imu_sample imu_ring[IMU_RING];
	unsigned imu_head, imu_len;
	uint64_t imu_overflow;

	//! Auto exposure: current station settings and the signal collected since.
	unsigned exposure_lines, exposure_gain, max_exposure;
	double ae_signal;
	unsigned ae_count;
	//! Newest IMU sample, to report the camera-IMU time skew.
	int64_t last_imu_ns;
	unsigned imu_divisor;
	uint64_t imu_count;
	float gain, brightness;

	//! Exposure alternation: start of the newest pulse, its phase and each phase's mean signal.
	int64_t phase_ts;
	int phase;
	float phase_signal[2];
	unsigned phase_frames[2];
};

//! Comma-separated indices below @p limit; returns the count, 0 on a parse error.
static int
parse_index_list(const char *text, int *out, int max, int limit)
{
	int n = 0;
	for (const char *p = text; *p != '\0' && n < max;) {
		char *end;
		long v = strtol(p, &end, 10);
		if (end == p || v < 0 || v >= limit) {
			return 0;
		}
		out[n++] = (int)v;
		p = *end == ',' ? end + 1 : end;
	}
	return n;
}

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

static bool
load_calibration(struct pico4_slam *s, const char *dir, const int *cams)
{
	double first_T_BS[16];
	s->calib.cam_count = s->cam_count;
	for (int i = 0; i < s->cam_count; i++) {
		char path[512];
		struct pico4_tcam_omni omni;
		struct pico4_tcam_kb4 kb4;
		snprintf(path, sizeof(path), "%s/pvr/mav0/cam%d/sensor.yaml", dir, cams[i]);
		char *text = read_file(path);
		bool ok = text != NULL && pico4_tcam_parse_yaml(text, &omni) && pico4_tcam_fit_kb4(&omni, &kb4);
		free(text);
		if (!ok) {
			SLAM_ERROR(s, "No usable camera calibration in %s", path);
			return false;
		}
		SLAM_INFO(s, "cam%d: KB4 refit of the factory omni model, %.2f px RMS (max %.2f) over %d rays", cams[i],
		          kb4.rms_px, kb4.max_px, kb4.samples);
		if (i == 0) {
			memcpy(first_T_BS, omni.T_BS, sizeof(first_T_BS));
		} else if (debug_get_bool_option_pico4_slam_upright() && pico4_tcam_upside_down(first_T_BS, omni.T_BS)) {
			// The lower pair is mounted 180 degrees apart; turn this image upright for stereo matching.
			pico4_tcam_rotate_180(&kb4, omni.T_BS, omni.width, omni.height);
			s->rotated[i] = true;
			SLAM_INFO(s, "cam%d: image turned by 180 degrees to match cam%d", cams[i], cams[0]);
		}

		struct t_slam_camera_calibration *c = &s->calib.cams[i];
		c->base.image_size_pixels.w = (uint32_t)omni.width;
		c->base.image_size_pixels.h = (uint32_t)omni.height;
		c->base.intrinsics[0][0] = kb4.fx;
		c->base.intrinsics[0][2] = kb4.cx;
		c->base.intrinsics[1][1] = kb4.fy;
		c->base.intrinsics[1][2] = kb4.cy;
		c->base.intrinsics[2][2] = 1;
		c->base.distortion_model = T_DISTORTION_FISHEYE_KB4;
		c->base.kb4.k1 = kb4.k[0];
		c->base.kb4.k2 = kb4.k[1];
		c->base.kb4.k3 = kb4.k[2];
		c->base.kb4.k4 = kb4.k[3];
		c->frequency = SCENE_HZ / s->divisor;

		// T_BS is the camera's pose in the body (IMU) frame: Basalt's T_i_c.
		const double *t = omni.T_BS;
		struct xrt_matrix_3x3 rot = {{(float)t[0], (float)t[1], (float)t[2], (float)t[4], (float)t[5], (float)t[6],
		                              (float)t[8], (float)t[9], (float)t[10]}};
		struct xrt_pose pose = {.position = {(float)t[3], (float)t[7], (float)t[11]}};
		math_quat_from_matrix_3x3(&rot, &pose.orientation);
		math_quat_normalize(&pose.orientation);
		math_matrix_4x4_isometry_from_pose(&pose, &c->T_imu_cam);
	}

	// ICM-42688 class noise; Basalt estimates the biases.
	struct t_inertial_calibration accel = {
	    .transform = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}},
	    .bias_std = {0.001, 0.001, 0.001},
	    .noise_std = {0.016, 0.016, 0.016},
	};
	struct t_inertial_calibration gyro = {
	    .transform = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}},
	    .bias_std = {0.0001, 0.0001, 0.0001},
	    .noise_std = {0.0016, 0.0016, 0.0016},
	};
	s->calib.imu.base.accel = accel;
	s->calib.imu.base.gyro = gyro;
	s->calib.imu.frequency = IMU_HZ / s->imu_divisor;
	return true;
}

static void
drop_group(struct pico4_slam *s)
{
	for (int i = 0; i < PICO4_TCAM_COUNT; i++) {
		xrt_frame_reference(&s->group.frames[i], NULL);
	}
	s->group.have = 0;
}

//! Mean of a sparse grid of 10-bit samples.
static float
sample_mean(const struct pico4_cam_frame *f)
{
	uint64_t sum = 0, n = 0;
	for (uint32_t y = 8; y < f->height; y += 16) {
		const uint16_t *line = (const uint16_t *)((const uint8_t *)f->data + (size_t)y * f->stride);
		for (uint32_t x = 8; x < f->width; x += 16) {
			sum += line[x] & 0x3ff;
			n++;
		}
	}
	return n > 0 ? (float)sum / (float)n : 0;
}

static struct xrt_frame *
convert(struct pico4_slam *s, const struct pico4_cam_frame *f, bool rotate)
{
	struct xrt_frame *xf = NULL;
	u_frame_create_one_off(XRT_FORMAT_L8, f->width, f->height, &xf);
	if (xf == NULL) {
		return NULL;
	}
	// 10 -> 8 bits above the black level, with the gain in 1/256 steps.
	uint32_t g = (uint32_t)(s->gain * 256.0f);
	for (uint32_t y = 0; y < f->height; y++) {
		const uint16_t *in = (const uint16_t *)((const uint8_t *)f->data + (size_t)y * f->stride);
		uint8_t *out = xf->data + (size_t)(rotate ? f->height - 1 - y : y) * xf->stride;
		for (uint32_t x = 0; x < f->width; x++) {
			uint32_t raw = in[x] & 0x3ff;
			uint32_t v = raw > BLACK_LEVEL ? ((raw - BLACK_LEVEL) * g) >> 10 : 0;
			out[rotate ? f->width - 1 - x : x] = (uint8_t)(v > 255 ? 255 : v);
		}
	}
	return xf;
}

static void
dump_frame(const struct xrt_frame *xf, const char *dir, uint64_t set, int index)
{
	char path[512];
	snprintf(path, sizeof(path), "%s/set%llu-cam%d.pgm", dir, (unsigned long long)set, index);
	FILE *f = fopen(path, "wb");
	if (f == NULL) {
		return;
	}
	fprintf(f, "P5\n%u %u\n255\n", xf->width, xf->height);
	for (uint32_t y = 0; y < xf->height; y++) {
		fwrite(xf->data + (size_t)y * xf->stride, 1, xf->width, f);
	}
	fclose(f);
}

static void
push_group(struct pico4_slam *s)
{
	const char *dump = debug_get_option_pico4_slam_dump();
	s->sequence++;
	if (dump != NULL && s->sets_pushed % 500 == 100) {
		for (int i = 0; i < s->cam_count; i++) {
			dump_frame(s->group.frames[i], dump, s->sets_pushed, i);
		}
	}
	os_thread_helper_lock(&s->push_oth);
	if (s->mail_full) {
		s->sets_busy++; // the tracker has not taken the previous set yet
		for (int i = 0; i < s->cam_count; i++) {
			xrt_frame_reference(&s->mail[i], NULL);
		}
	}
	for (int i = 0; i < s->cam_count; i++) {
		struct xrt_frame *xf = s->group.frames[i];
		xf->timestamp = s->group.ts;
		xf->source_timestamp = s->group.ts;
		xf->source_sequence = s->sequence;
		xf->source_id = i;
		s->mail[i] = xf; // the reference moves to the mailbox
		s->group.frames[i] = NULL;
	}
	s->mail_full = true;
	os_thread_helper_signal_locked(&s->push_oth);
	os_thread_helper_unlock(&s->push_oth);
	s->sets_pushed++;
	if (s->sets_pushed % 250 == 1) {
		int64_t imu = __atomic_load_n(&s->last_imu_ns, __ATOMIC_RELAXED);
		SLAM_INFO(s,
		          "frame set %llu: frame - newest IMU %.1f ms, latency %.1f ms, exposure %u lines gain %u, "
		          "digital gain %.1f",
		          (unsigned long long)s->sets_pushed, (s->group.ts - imu) / 1e6,
		          (os_monotonic_get_ns() - s->group.ts) / 1e6, s->exposure_lines, s->exposure_gain, s->gain);
	}
	drop_group(s);
}

//! Station exposure from the mean signal of the first camera, exposure first, then gain.
static void
auto_exposure(struct pico4_slam *s, float signal)
{
	s->ae_signal += signal;
	if (++s->ae_count < AE_SETS) {
		return;
	}
	float mean = (float)(s->ae_signal / s->ae_count);
	s->ae_signal = 0;
	s->ae_count = 0;

	float total = (float)s->exposure_lines * (float)s->exposure_gain;
	float want = total * AE_TARGET / (mean > 1.0f ? mean : 1.0f);
	if (want > total * 4) {
		want = total * 4; // the response is not quite linear; approach in steps
	}
	unsigned lines = (unsigned)(want / 16.0f), gain = 16;
	if (lines > s->max_exposure) {
		lines = s->max_exposure;
		gain = (unsigned)(want / (float)lines);
		gain = gain > AE_MAX_GAIN ? AE_MAX_GAIN : gain < 16 ? 16 : gain;
	}
	lines = lines < AE_MIN_LINES ? AE_MIN_LINES : lines;
	float change = (float)lines * (float)gain / total;
	if (change > 0.9f && change < 1.1f) {
		return;
	}
	if (pico4_station_camera_set_exposure(s->station, lines, gain) == 0) {
		SLAM_DEBUG(s, "exposure %u lines, gain %u (signal %.0f)", lines, gain, mean);
		s->exposure_lines = lines;
		s->exposure_gain = gain;
	}
}

//! Exposure phase (0 or 1) of the pulse at @p ts, counted in sync periods from the newest pulse.
static int
frame_phase(struct pico4_slam *s, int64_t ts)
{
	if (s->phase_ts == 0) {
		s->phase_ts = ts;
		return s->phase;
	}
	int64_t periods = llround((double)(ts - s->phase_ts) / FRAME_NS);
	int phase = (int)((s->phase + periods) & 1); // also right for an older pulse (periods < 0)
	if (periods > 0) {
		s->phase_ts = ts;
		s->phase = phase;
	}
	return phase;
}

static void
handle_frame(struct pico4_slam *s, const struct pico4_cam_frame *f)
{
	int index = s->index_of_slot[f->slot];
	if (index < 0 || f->sof_monotonic_ns == 0) {
		return;
	}
	int64_t ts = f->sof_monotonic_ns;
	int phase = frame_phase(s, ts);
	float signal = -1.0f;
	if (index == 0) {
		signal = sample_mean(f) - BLACK_LEVEL;
		float *avg = &s->phase_signal[phase];
		*avg = s->phase_frames[phase]++ == 0 ? signal : *avg + 0.1f * (signal - *avg);
	}
	if (s->phase_frames[0] < PHASE_FRAMES || s->phase_frames[1] < PHASE_FRAMES ||
	    s->phase_signal[phase] < s->phase_signal[phase ^ 1]) {
		return; // a controller-LED exposure, or not known yet
	}
	if (s->group.have != 0 && ts > s->group.ts + GROUP_WINDOW_NS) {
		s->sets_dropped++; // a camera missed this pulse
		drop_group(s);
	}
	if (s->group.have != 0 && ts < s->group.ts - GROUP_WINDOW_NS) {
		return; // late frame of a set already given up
	}
	if (s->group.have == 0) {
		s->group.ts = ts;
	}
	if (index == 0) {
		// The first camera steers the exposure and the digital gain of the next sets.
		auto_exposure(s, signal);
		float mean8 = signal / 4.0f;
		float want = mean8 > 0.5f ? s->brightness / mean8 : 8.0f;
		want = want < 1.0f ? 1.0f : want > 8.0f ? 8.0f : want;
		s->gain += 0.05f * (want - s->gain);
	}
	if (s->group.frames[index] != NULL) {
		return; // duplicate
	}
	s->group.frames[index] = convert(s, f, s->rotated[index]);
	if (s->group.frames[index] == NULL) {
		drop_group(s);
		return;
	}
	s->group.have++;
	if (s->group.have == s->cam_count) {
		if (s->sequence % s->divisor == 0) {
			push_group(s);
		} else {
			s->sequence++;
			drop_group(s);
		}
	}
}

/*!
 * Set the calling thread's nice value; returns the previous one. Basalt's
 * threads run below the compositor and the IMU thread: without a config file
 * its TBB pool takes every core, and at equal priority the compositor missed
 * frames.
 */
static int
thread_nice(int value)
{
	id_t tid = (id_t)syscall(SYS_gettid);
	errno = 0;
	int before = getpriority(PRIO_PROCESS, tid);
	if (errno != 0) {
		before = 0;
	}
	setpriority(PRIO_PROCESS, tid, value); // lowering it back needs CAP_SYS_NICE (root here)
	return before;
}

/*!
 * Restrict the calling thread to the CPUs in PICO4_SLAM_CPUS (a bit mask,
 * default cores 0-5: the XR2's four small and two of its big cores), saving
 * the previous set. TBB sizes its pool from the CPU set it starts on, so
 * Basalt then leaves the two fastest cores to the compositor and the app.
 */
static bool
tracker_cpus(cpu_set_t *before)
{
	unsigned long mask = (unsigned long)debug_get_num_option_pico4_slam_cpus();
	cpu_set_t set;
	CPU_ZERO(&set);
	for (int cpu = 0; cpu < (int)(8 * sizeof(mask)) && cpu < CPU_SETSIZE; cpu++) {
		if (mask & (1ul << cpu)) {
			CPU_SET(cpu, &set);
		}
	}
	if (mask == 0 || sched_getaffinity(0, sizeof(*before), before) != 0) {
		return false;
	}
	CPU_AND(&set, &set, before);
	return CPU_COUNT(&set) > 0 && sched_setaffinity(0, sizeof(set), &set) == 0;
}

//! Feeds frame sets to the tracker; may block on it.
static void *
push_thread(void *ptr)
{
	struct pico4_slam *s = ptr;
	struct xrt_frame *frames[PICO4_TCAM_COUNT] = {0};

	thread_nice(TRACKER_NICE);
	os_thread_helper_name(&s->push_oth, "PICO 4 SLAM frames");
	os_thread_helper_lock(&s->push_oth);
	while (os_thread_helper_is_running_locked(&s->push_oth)) {
		if (!s->mail_full) {
			os_thread_helper_wait_locked(&s->push_oth);
			continue;
		}
		for (int i = 0; i < s->cam_count; i++) {
			frames[i] = s->mail[i];
			s->mail[i] = NULL;
		}
		s->mail_full = false;
		os_thread_helper_unlock(&s->push_oth);

		for (int i = 0; i < s->cam_count; i++) {
			xrt_sink_push_frame(s->sinks->cams[i], frames[i]);
			xrt_frame_reference(&frames[i], NULL);
		}
		os_thread_helper_lock(&s->push_oth);
	}
	os_thread_helper_unlock(&s->push_oth);
	return NULL;
}

//! Feeds IMU samples to the tracker, independently of the frames.
static void *
imu_push_thread(void *ptr)
{
	struct pico4_slam *s = ptr;
	struct xrt_imu_sample imu[IMU_RING];

	thread_nice(TRACKER_NICE);
	os_thread_helper_name(&s->imu_oth, "PICO 4 SLAM IMU");
	os_thread_helper_lock(&s->imu_oth);
	while (os_thread_helper_is_running_locked(&s->imu_oth)) {
		if (s->imu_len == 0) {
			os_thread_helper_wait_locked(&s->imu_oth);
			continue;
		}
		unsigned n = s->imu_len;
		for (unsigned i = 0; i < n; i++) {
			imu[i] = s->imu_ring[(s->imu_head + i) % IMU_RING];
		}
		s->imu_head = (s->imu_head + n) % IMU_RING;
		s->imu_len = 0;
		os_thread_helper_unlock(&s->imu_oth);

		for (unsigned i = 0; i < n; i++) {
			xrt_sink_push_imu(s->sinks->imu, &imu[i]);
		}
		os_thread_helper_lock(&s->imu_oth);
	}
	os_thread_helper_unlock(&s->imu_oth);
	return NULL;
}

static void *
camera_thread(void *ptr)
{
	struct pico4_slam *s = ptr;
	struct pico4_cam_frame f;
	uint64_t timeouts = 0;
	bool failed = false;

	os_thread_helper_name(&s->oth, "PICO 4 cameras");
	os_thread_helper_lock(&s->oth);
	while (os_thread_helper_is_running_locked(&s->oth)) {
		os_thread_helper_unlock(&s->oth);
		int r = pico4_cam_next(s->cam, &f, 200);
		if (r == 0) {
			handle_frame(s, &f);
		} else if (r == -ETIMEDOUT) {
			if (timeouts++ % 25 == 0) {
				SLAM_ERROR(s, "No camera frames for 200 ms");
			}
		} else if (r != -EINTR) {
			// Every camera failed; nothing recovers that short of a restart.
			if (!failed) {
				SLAM_ERROR(s, "Camera capture failed: %s; 6DoF stops here", strerror(-r));
				failed = true;
			}
			os_nanosleep(U_TIME_1MS_IN_NS * 100);
		}
		os_thread_helper_lock(&s->oth);
	}
	os_thread_helper_unlock(&s->oth);
	return NULL;
}

static void
cam_log(void *user, enum pico4_cam_log_level level, const char *msg)
{
	struct pico4_slam *s = user;
	if (level == PICO4_CAM_LOG_ERROR) {
		SLAM_ERROR(s, "%s", msg);
	} else {
		SLAM_DEBUG(s, "%s", msg);
	}
}

struct pico4_slam *
pico4_slam_create(const char *calibration_dir, struct pico4_station *station, enum u_logging_level log_level)
{
	if (!debug_get_bool_option_pico4_slam() || station == NULL) {
		return NULL;
	}
	struct pico4_slam *s = U_TYPED_CALLOC(struct pico4_slam);
	int cams[PICO4_TCAM_COUNT], slots[PICO4_TCAM_COUNT];
	s->log_level = log_level;
	s->gain = 2.0f;
	s->exposure_lines = 300;
	s->exposure_gain = 16;
	s->max_exposure = (unsigned)debug_get_num_option_pico4_slam_max_exposure();
	s->brightness = (float)debug_get_num_option_pico4_slam_brightness();
	long divisor = debug_get_num_option_pico4_slam_divisor();
	s->divisor = divisor < 1 ? 1 : (unsigned)divisor;
	long imu_divisor = debug_get_num_option_pico4_slam_imu_divisor();
	s->imu_divisor = imu_divisor < 1 ? 1 : (unsigned)imu_divisor;
	s->cam_count = parse_index_list(debug_get_option_pico4_slam_cams(), cams, PICO4_TCAM_COUNT, PICO4_TCAM_COUNT);
	if (s->cam_count < 2 ||
	    parse_index_list(debug_get_option_pico4_tcam_slots(), slots, PICO4_TCAM_COUNT, PICO4_CAM_NUM) !=
	        PICO4_TCAM_COUNT) {
		SLAM_ERROR(s, "PICO4_SLAM_CAMS needs two or more cameras 0-3, PICO4_TCAM_SLOTS four slots");
		free(s);
		return NULL;
	}
	for (int i = 0; i < PICO4_CAM_NUM; i++) {
		s->index_of_slot[i] = -1;
	}
	for (int i = 0; i < s->cam_count; i++) {
		s->index_of_slot[slots[cams[i]]] = i;
	}
	if (!load_calibration(s, calibration_dir, cams)) {
		free(s);
		return NULL;
	}

	struct t_slam_tracker_config config;
	t_slam_fill_default_config(&config);
	config.cam_count = s->cam_count;
	config.slam_calib = &s->calib;
	config.submit_from_start = true;
	// Basalt's threads, its TBB workers included, inherit this thread's nice value.
	// Basalt's threads, its TBB workers included, inherit this thread's nice value and CPU set.
	int nice_before = thread_nice(TRACKER_NICE);
	cpu_set_t cpus_before;
	bool pinned = tracker_cpus(&cpus_before);
	bool started = t_slam_create(&s->xfctx, &config, &s->tracker, &s->sinks) == 0 && t_slam_start(s->tracker) == 0;
	if (pinned) {
		sched_setaffinity(0, sizeof(cpus_before), &cpus_before);
	}
	thread_nice(nice_before);
	if (!started) {
		SLAM_ERROR(s, "Could not start the SLAM tracker (is Basalt's libbasalt.so installed?)");
		goto fail;
	}

	s->station = station;
	int r;
	struct pico4_cam_config cc;
	pico4_cam_config_init(&cc);
	cc.verbose = log_level <= U_LOGGING_DEBUG ? PICO4_CAM_LOG_INFO : PICO4_CAM_LOG_ERROR;
	cc.log = cam_log;
	cc.log_user = s;
	if ((r = pico4_cam_open(&s->cam, &cc)) < 0 || (r = pico4_cam_start(s->cam)) < 0) {
		SLAM_ERROR(s, "Tracking cameras failed to start: %s", strerror(-r));
		goto fail;
	}
	if ((r = pico4_station_camera_start(s->station)) < 0) {
		SLAM_ERROR(s, "The controller radio refused camera sync: %s", strerror(-r));
		goto fail;
	}
	pico4_station_camera_set_exposure(s->station, s->exposure_lines, s->exposure_gain);
	if (os_thread_helper_init(&s->imu_oth) != 0 || os_thread_helper_start(&s->imu_oth, imu_push_thread, s) != 0 ||
	    os_thread_helper_init(&s->push_oth) != 0 || os_thread_helper_start(&s->push_oth, push_thread, s) != 0 ||
	    os_thread_helper_init(&s->oth) != 0 || os_thread_helper_start(&s->oth, camera_thread, s) != 0) {
		SLAM_ERROR(s, "Could not start the camera threads");
		goto fail;
	}
	SLAM_INFO(s, "SLAM tracking with %d cameras at %.1f Hz", s->cam_count, SCENE_HZ / s->divisor);
	return s;

fail:
	pico4_slam_destroy(&s);
	return NULL;
}

void
pico4_slam_push_imu(struct pico4_slam *s, int64_t timestamp_ns, const float accel[3], const float gyro[3])
{
	if (s->imu_count++ % s->imu_divisor != 0) {
		return;
	}
	struct xrt_imu_sample sample = {
	    .timestamp_ns = timestamp_ns,
	    .accel_m_s2 = {accel[0], accel[1], accel[2]},
	    .gyro_rad_secs = {gyro[0], gyro[1], gyro[2]},
	};
	os_thread_helper_lock(&s->imu_oth);
	if (s->imu_len == IMU_RING) {
		// The tracker is seconds behind; drop the oldest sample.
		s->imu_head = (s->imu_head + 1) % IMU_RING;
		s->imu_len--;
		s->imu_overflow++;
	}
	s->imu_ring[(s->imu_head + s->imu_len) % IMU_RING] = sample;
	s->imu_len++;
	os_thread_helper_signal_locked(&s->imu_oth);
	os_thread_helper_unlock(&s->imu_oth);
	__atomic_store_n(&s->last_imu_ns, timestamp_ns, __ATOMIC_RELAXED);
}

bool
pico4_slam_get_head_pose(struct pico4_slam *s, int64_t at_ns, struct xrt_space_relation *out)
{
	struct xrt_space_relation rel = XRT_SPACE_RELATION_ZERO;
	xrt_tracked_slam_get_tracked_pose(s->tracker, at_ns, &rel);
	if (!(rel.relation_flags & XRT_SPACE_RELATION_POSITION_TRACKED_BIT)) {
		return false;
	}

	// Basalt: body (IMU) pose in a Z-up world. OpenXR: HMD pose in a Y-up world.
	const struct xrt_quat q_xr_w = {-0.70710678f, 0, 0, 0.70710678f}; // -90 degrees about X: Z up -> Y up
	const struct xrt_quat q_b_h = {0, 0, -0.70710678f, 0.70710678f};  // HMD axes in the body (x up, y left)
	struct xrt_quat q;
	struct xrt_vec3 offset, p;

	math_quat_rotate(&q_xr_w, &rel.pose.orientation, &q);
	math_quat_rotate(&q, &q_b_h, &out->pose.orientation);
	math_quat_normalize(&out->pose.orientation);

	math_quat_rotate_vec3(&rel.pose.orientation, &P_BODY_HMD, &offset);
	p = (struct xrt_vec3){rel.pose.position.x + offset.x, rel.pose.position.y + offset.y,
	                      rel.pose.position.z + offset.z};
	math_quat_rotate_vec3(&q_xr_w, &p, &out->pose.position);

	out->relation_flags = (enum xrt_space_relation_flags)(
	    XRT_SPACE_RELATION_ORIENTATION_VALID_BIT | XRT_SPACE_RELATION_POSITION_VALID_BIT |
	    XRT_SPACE_RELATION_ORIENTATION_TRACKED_BIT | XRT_SPACE_RELATION_POSITION_TRACKED_BIT);
	return true;
}

void
pico4_slam_destroy(struct pico4_slam **ptr)
{
	struct pico4_slam *s = *ptr;
	if (s == NULL) {
		return;
	}
	// A failed create gets here with helpers never initialised; Monado asserts on those.
	if (s->oth.initialized) {
		os_thread_helper_destroy(&s->oth);
	}
	// The frame feeder may be inside a push; the IMU feeder keeps Basalt draining until it returns.
	if (s->push_oth.initialized) {
		os_thread_helper_destroy(&s->push_oth);
	}
	/*
	 * Let Basalt work through the frames it has queued before it stops: its
	 * optical flow carries on after the estimator has finished and then
	 * predicts from an invalid state (an abort in Sophus or a crash, seen when
	 * the optical flow lagged behind).
	 */
	if (s->tracker != NULL && s->sets_pushed > 0) {
		os_nanosleep(TRACKER_DRAIN_NS);
	}
	if (s->imu_oth.initialized) {
		os_thread_helper_destroy(&s->imu_oth);
	}
	for (int i = 0; i < PICO4_TCAM_COUNT; i++) {
		xrt_frame_reference(&s->mail[i], NULL);
	}
	if (s->station != NULL && s->cam != NULL) {
		pico4_station_camera_stop(s->station);
	}
	pico4_cam_close(s->cam);
	drop_group(s);
	xrt_frame_context_destroy_nodes(&s->xfctx);
	SLAM_INFO(s,
	          "SLAM: %llu frame sets, %llu incomplete, %llu skipped while the tracker was busy, "
	          "%llu IMU samples dropped",
	          (unsigned long long)s->sets_pushed, (unsigned long long)s->sets_dropped,
	          (unsigned long long)s->sets_busy, (unsigned long long)s->imu_overflow);
	free(s);
	*ptr = NULL;
}

#else // XRT_FEATURE_SLAM

struct pico4_slam *
pico4_slam_create(const char *calibration_dir, struct pico4_station *station, enum u_logging_level log_level)
{
	(void)calibration_dir;
	(void)station;
	(void)log_level;
	return NULL;
}

void
pico4_slam_push_imu(struct pico4_slam *s, int64_t timestamp_ns, const float accel[3], const float gyro[3])
{
	(void)s;
	(void)timestamp_ns;
	(void)accel;
	(void)gyro;
}

bool
pico4_slam_get_head_pose(struct pico4_slam *s, int64_t at_ns, struct xrt_space_relation *out)
{
	(void)s;
	(void)at_ns;
	(void)out;
	return false;
}

void
pico4_slam_destroy(struct pico4_slam **s)
{
	*s = NULL;
}

#endif // XRT_FEATURE_SLAM
