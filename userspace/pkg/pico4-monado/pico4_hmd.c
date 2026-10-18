// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 HMD: 3DoF head orientation from the IMU behind the sensor DSP.
 *
 * Accelerometer and gyroscope samples of the ICM-42688 come from the Qualcomm
 * Sensor Core (see pico4_ssc.c) with QTimer timestamps. They are rotated into
 * the HMD frame, fused with m_imu_3dof and pushed into a relation history in
 * the CLOCK_MONOTONIC timeline that Monado uses.
 *
 * @ingroup drv_pico4
 */

#include "pico4_interface.h"
#include "pico4_ssc.h"

#include "xrt/xrt_defines.h"
#include "xrt/xrt_device.h"
#include "xrt/xrt_prober.h"

#include "os/os_threading.h"
#include "os/os_time.h"

#include "math/m_api.h"
#include "math/m_imu_3dof.h"
#include "math/m_mathinclude.h" // IWYU pragma: keep
#include "math/m_relation_history.h"
#include "math/m_vec3.h"

#include "util/u_debug.h"
#include "util/u_device.h"
#include "util/u_distortion_mesh.h"
#include "util/u_logging.h"
#include "util/u_misc.h"
#include "util/u_time.h"
#include "util/u_var.h"
#include "util/u_visibility_mask.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

DEBUG_GET_ONCE_LOG_OPTION(pico4_log, "PICO4_LOG", U_LOGGING_INFO)
DEBUG_GET_ONCE_NUM_OPTION(pico4_imu_rate, "PICO4_IMU_RATE", 1000)

#define P4_TRACE(h, ...) U_LOG_XDEV_IFL_T(&h->base, h->log_level, __VA_ARGS__)
#define P4_DEBUG(h, ...) U_LOG_XDEV_IFL_D(&h->base, h->log_level, __VA_ARGS__)
#define P4_INFO(h, ...) U_LOG_XDEV_IFL_I(&h->base, h->log_level, __VA_ARGS__)
#define P4_ERROR(h, ...) U_LOG_XDEV_IFL_E(&h->base, h->log_level, __VA_ARGS__)

//! QTimer rate of the SSC timestamps; also the AP's arch counter on this SoC.
#define QTIMER_HZ 19200000ULL

//! Gyro samples ignored after enabling (the accelerometer settles), then averaged for the start pose.
#define PICO4_WARMUP_SKIP 100
#define PICO4_WARMUP_AVERAGE 100

//! Samples per stillness window of the gyro bias estimate (a quarter second at 1 kHz).
#define PICO4_STILL_WINDOW 256

/*!
 * The PICO 4 HMD.
 *
 * @implements xrt_device
 */
struct pico4_hmd
{
	struct xrt_device base;

	struct pico4_ssc *ssc;
	struct pico4_ssc_suid accel_suid, gyro_suid;
	struct os_thread_helper oth;

	//! CLOCK_MONOTONIC ns minus the QTimer time in ns.
	int64_t qtimer_offset_ns;

	struct m_imu_3dof fusion;
	bool fusion_started;
	uint32_t warmup_samples;
	struct xrt_vec3 warmup_accel;

	//! Gyro bias, learned while the headset is still (see update_bias()).
	struct xrt_vec3 gyro_bias;
	bool gyro_bias_valid;
	struct
	{
		struct xrt_vec3 gyro_sum, gyro_sq, accel_sum, accel_sq;
		uint32_t n;
	} still;
	struct xrt_vec3 last_accel;
	bool have_accel;
	struct m_relation_history *relation_hist;

	// Statistics, also shown in the debug GUI.
	uint64_t gyro_samples;
	uint64_t accel_samples;
	int64_t last_latency_ns;

	enum u_logging_level log_level;
};

static inline struct pico4_hmd *
pico4_hmd(struct xrt_device *xdev)
{
	return (struct pico4_hmd *)xdev;
}

static uint64_t
qtimer_to_ns(uint64_t ticks)
{
	// 1e9 / 19.2e6 = 625 / 12, split to stay exact and avoid overflow.
	return (ticks / 12) * 625 + (ticks % 12) * 625 / 12;
}

#if defined(__aarch64__)
static uint64_t
read_counter(void)
{
	uint64_t value;
	__asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(value)::"memory");
	return value;
}

static uint64_t
counter_frequency(void)
{
	uint64_t value;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(value));
	return value;
}
#endif

/*!
 * Offset between the SSC timestamps and CLOCK_MONOTONIC. On the XR2 the arch
 * counter is the QTimer, so the narrowest of a few (counter, monotonic) pairs
 * gives it directly; elsewhere it is set from the first sample's arrival.
 */
static bool
calibrate_clock(struct pico4_hmd *h)
{
#if defined(__aarch64__)
	if (counter_frequency() != QTIMER_HZ) {
		P4_ERROR(h, "Arch counter runs at %llu Hz, not the QTimer rate", (unsigned long long)counter_frequency());
		return false;
	}
	int64_t best_window = INT64_MAX;
	for (int i = 0; i < 16; i++) {
		int64_t before = os_monotonic_get_ns();
		uint64_t ticks = read_counter();
		int64_t after = os_monotonic_get_ns();
		if (after - before < best_window) {
			best_window = after - before;
			h->qtimer_offset_ns = before + (after - before) / 2 - (int64_t)qtimer_to_ns(ticks);
		}
	}
	return true;
#else
	return false;
#endif
}

//! IMU axes into the HMD frame (x right, y up, z back): from the factory camera extrinsics.
static struct xrt_vec3
imu_to_hmd(const float v[3])
{
	return (struct xrt_vec3){-v[1], v[0], v[2]};
}

/*!
 * Orientation (world from HMD) that takes the measured gravity reaction @p a,
 * which points up when still, to world +Y; no rotation about the vertical.
 */
static struct xrt_quat
rotation_to_up(const struct xrt_vec3 *a)
{
	float len = sqrtf(a->x * a->x + a->y * a->y + a->z * a->z);
	if (len < 1e-3f) {
		return (struct xrt_quat)XRT_QUAT_IDENTITY;
	}
	struct xrt_vec3 n = {a->x / len, a->y / len, a->z / len};
	// Half-way quaternion between n and up: axis n x up, w = 1 + n . up.
	struct xrt_quat q = {-n.z, 0.0f, n.x, 1.0f + n.y};
	if (q.w < 1e-6f) {
		return (struct xrt_quat){1.0f, 0.0f, 0.0f, 0.0f}; // upside down: 180 degrees about X
	}
	math_quat_normalize(&q);
	return q;
}

static float
window_max_std(const struct xrt_vec3 *sum, const struct xrt_vec3 *sq, uint32_t n)
{
	float mx = sum->x / n, my = sum->y / n, mz = sum->z / n;
	float vx = sq->x / n - mx * mx, vy = sq->y / n - my * my, vz = sq->z / n - mz * mz;
	return sqrtf(fmaxf(fmaxf(vx, vy), fmaxf(vz, 0.0f)));
}

/*!
 * The SSC's "gyro" stream keeps a bias of a few mrad/s, enough to tilt the
 * fusion away from gravity faster than m_imu_3dof pulls it back. Learn it over
 * windows in which gyro and accelerometer are both quiet and the rate is small:
 * a still headset, not a slow deliberate turn.
 */
static void
update_bias(struct pico4_hmd *h, const struct xrt_vec3 *gyro, const struct xrt_vec3 *accel)
{
	struct xrt_vec3 g2 = {gyro->x * gyro->x, gyro->y * gyro->y, gyro->z * gyro->z};
	struct xrt_vec3 a2 = {accel->x * accel->x, accel->y * accel->y, accel->z * accel->z};
	math_vec3_accum(gyro, &h->still.gyro_sum);
	math_vec3_accum(&g2, &h->still.gyro_sq);
	math_vec3_accum(accel, &h->still.accel_sum);
	math_vec3_accum(&a2, &h->still.accel_sq);
	if (++h->still.n < PICO4_STILL_WINDOW) {
		return;
	}

	uint32_t n = h->still.n;
	struct xrt_vec3 mean = h->still.gyro_sum;
	math_vec3_scalar_mul(1.0f / n, &mean);
	struct xrt_vec3 a_mean = h->still.accel_sum;
	math_vec3_scalar_mul(1.0f / n, &a_mean);
	float g_std = window_max_std(&h->still.gyro_sum, &h->still.gyro_sq, n);
	float a_std = window_max_std(&h->still.accel_sum, &h->still.accel_sq, n);
	float rate = m_vec3_len(mean);
	float g = m_vec3_len(a_mean);
	memset(&h->still, 0, sizeof(h->still));

	if (g_std > 0.01f || a_std > 0.05f || rate > 0.02f || fabsf(g - (float)MATH_GRAVITY_M_S2) > 0.3f) {
		return;
	}
	if (!h->gyro_bias_valid) {
		h->gyro_bias = mean;
		h->gyro_bias_valid = true;
	} else {
		h->gyro_bias.x += 0.2f * (mean.x - h->gyro_bias.x);
		h->gyro_bias.y += 0.2f * (mean.y - h->gyro_bias.y);
		h->gyro_bias.z += 0.2f * (mean.z - h->gyro_bias.z);
	}
}

static bool
suid_equal(const struct pico4_ssc_suid *a, const struct pico4_ssc_suid *b)
{
	return a->low == b->low && a->high == b->high;
}

static void
handle_sample(struct pico4_hmd *h, const struct pico4_ssc_sample *s)
{
	if (s->count < 3) {
		return;
	}
	int64_t ts = (int64_t)qtimer_to_ns(s->ticks) + h->qtimer_offset_ns;
	struct xrt_vec3 v = imu_to_hmd(s->values);

	if (suid_equal(&s->suid, &h->accel_suid)) {
		h->last_accel = v;
		h->have_accel = true;
		h->accel_samples++;
		return;
	}
	if (!suid_equal(&s->suid, &h->gyro_suid) || !h->have_accel) {
		return;
	}
	h->gyro_samples++;
	h->last_latency_ns = os_monotonic_get_ns() - ts;

	update_bias(h, &v, &h->last_accel);
	if (h->gyro_bias_valid) {
		math_vec3_subtract(&h->gyro_bias, &v);
	}

	if (!h->fusion_started) {
		/*
		 * m_imu_3dof starts at identity and only slowly pulls towards gravity,
		 * and the first accelerometer samples after enabling are not settled
		 * yet (observed: 2 degrees off). Skip those, average the next ones and
		 * start the fusion level with that gravity mapped to +Y.
		 */
		h->warmup_samples++;
		if (h->warmup_samples > PICO4_WARMUP_SKIP) {
			math_vec3_accum(&h->last_accel, &h->warmup_accel);
		}
		if (h->warmup_samples < PICO4_WARMUP_SKIP + PICO4_WARMUP_AVERAGE) {
			return;
		}
		h->fusion.rot = rotation_to_up(&h->warmup_accel);
		h->fusion_started = true;
		P4_DEBUG(h, "fusion started after %u samples", h->warmup_samples);
	}
	m_imu_3dof_update(&h->fusion, (uint64_t)ts, &h->last_accel, &v);

	struct xrt_space_relation rel = XRT_SPACE_RELATION_ZERO;
	rel.relation_flags = (enum xrt_space_relation_flags)(XRT_SPACE_RELATION_ORIENTATION_VALID_BIT |
	                                                      XRT_SPACE_RELATION_ORIENTATION_TRACKED_BIT |
	                                                      XRT_SPACE_RELATION_ANGULAR_VELOCITY_VALID_BIT);
	rel.pose.orientation = h->fusion.rot;
	math_quat_rotate_derivative(&h->fusion.rot, &v, &rel.angular_velocity);
	m_relation_history_push(h->relation_hist, &rel, ts);

	if (h->gyro_samples % 5000 == 1 && h->log_level <= U_LOGGING_DEBUG) {
		// Tilt of the head's up axis in the world, and of the measured gravity in the head.
		struct xrt_vec3 up = {0, 1, 0}, head_up;
		math_quat_rotate_vec3(&h->fusion.rot, &up, &head_up);
		float a = sqrtf(h->last_accel.x * h->last_accel.x + h->last_accel.y * h->last_accel.y +
		                h->last_accel.z * h->last_accel.z);
		P4_DEBUG(h, "samples %llu, latency %.2f ms, fusion tilt %.3f deg, accel tilt %.3f deg, bias %s (%.4f %.4f %.4f) rad/s",
		         (unsigned long long)h->gyro_samples, h->last_latency_ns / 1e6,
		         acosf(fminf(1.0f, head_up.y)) * 180.0f / (float)M_PI,
		         acosf(fminf(1.0f, h->last_accel.y / a)) * 180.0f / (float)M_PI, h->gyro_bias_valid ? "learned" : "none",
         h->gyro_bias.x, h->gyro_bias.y, h->gyro_bias.z);
	}
}

static void *
imu_thread(void *ptr)
{
	struct pico4_hmd *h = (struct pico4_hmd *)ptr;
	struct pico4_ssc_sample samples[64];

	os_thread_helper_name(&h->oth, "PICO 4 IMU");
	os_thread_helper_lock(&h->oth);
	while (os_thread_helper_is_running_locked(&h->oth)) {
		os_thread_helper_unlock(&h->oth);
		int n = pico4_ssc_read(h->ssc, 100, samples, (int)ARRAY_SIZE(samples));
		if (n < 0) {
			P4_ERROR(h, "Reading the sensor service failed: %s", strerror(-n));
			os_nanosleep(U_TIME_1MS_IN_NS * 100);
		}
		for (int i = 0; i < n; i++) {
			handle_sample(h, &samples[i]);
		}
		os_thread_helper_lock(&h->oth);
	}
	os_thread_helper_unlock(&h->oth);
	return NULL;
}

static void
pico4_hmd_destroy(struct xrt_device *xdev)
{
	struct pico4_hmd *h = pico4_hmd(xdev);

	os_thread_helper_destroy(&h->oth);
	if (h->ssc != NULL) {
		pico4_ssc_disable(h->ssc, &h->accel_suid);
		pico4_ssc_disable(h->ssc, &h->gyro_suid);
		pico4_ssc_close(&h->ssc);
	}
	u_var_remove_root(h);
	m_imu_3dof_close(&h->fusion);
	m_relation_history_destroy(&h->relation_hist);
	u_device_free(&h->base);
}

static xrt_result_t
pico4_hmd_get_tracked_pose(struct xrt_device *xdev,
                           enum xrt_input_name name,
                           int64_t at_timestamp_ns,
                           struct xrt_space_relation *out_relation)
{
	struct pico4_hmd *h = pico4_hmd(xdev);

	if (name != XRT_INPUT_GENERIC_HEAD_POSE) {
		U_LOG_XDEV_UNSUPPORTED_INPUT(&h->base, h->log_level, name);
		return XRT_ERROR_INPUT_UNSUPPORTED;
	}

	struct xrt_space_relation rel = XRT_SPACE_RELATION_ZERO;
	m_relation_history_get(h->relation_hist, at_timestamp_ns, &rel);
	if (rel.relation_flags & XRT_SPACE_RELATION_ORIENTATION_VALID_BIT) {
		math_quat_normalize(&rel.pose.orientation);
	}
	*out_relation = rel;
	return XRT_SUCCESS;
}

static xrt_result_t
pico4_hmd_get_visibility_mask(struct xrt_device *xdev,
                              enum xrt_visibility_mask_type type,
                              uint32_t view_index,
                              struct xrt_visibility_mask **out_mask)
{
	struct xrt_fov fov = xdev->hmd->distortion.fov[view_index];
	u_visibility_mask_get_default(type, &fov, out_mask);
	return XRT_SUCCESS;
}

//! Display description: two 2160x2160 panels side by side, as the step-3 KMS path drives them.
static bool
setup_display(struct pico4_hmd *h)
{
	const int panel = 2160;
	// Approximate; the per-unit lens data (XR.md) replaces this with the distortion work.
	const double fov = 104.0 * (M_PI / 180.0);

	h->base.hmd->blend_modes[0] = XRT_BLEND_MODE_OPAQUE;
	h->base.hmd->blend_mode_count = 1;
	h->base.hmd->screens[0].w_pixels = panel * 2;
	h->base.hmd->screens[0].h_pixels = panel;
	h->base.hmd->screens[0].nominal_frame_interval_ns = time_s_to_ns(1.0f / 90.0f);
	for (int eye = 0; eye < 2; eye++) {
		h->base.hmd->views[eye].display.w_pixels = panel;
		h->base.hmd->views[eye].display.h_pixels = panel;
		h->base.hmd->views[eye].viewport.x_pixels = eye * panel;
		h->base.hmd->views[eye].viewport.y_pixels = 0;
		h->base.hmd->views[eye].viewport.w_pixels = panel;
		h->base.hmd->views[eye].viewport.h_pixels = panel;
		h->base.hmd->views[eye].rot = u_device_rotation_ident;
		if (!math_compute_fovs(1, 0.5, fov, 1, 0.5, fov, &h->base.hmd->distortion.fov[eye])) {
			return false;
		}
	}
	u_distortion_mesh_set_none(&h->base);
	return true;
}

struct xrt_device *
pico4_hmd_create(void)
{
	enum u_device_alloc_flags flags =
	    (enum u_device_alloc_flags)(U_DEVICE_ALLOC_HMD | U_DEVICE_ALLOC_TRACKING_NONE);
	struct pico4_hmd *h = U_DEVICE_ALLOCATE(struct pico4_hmd, flags, 1, 0);
	h->log_level = debug_get_log_option_pico4_log();

	h->base.update_inputs = u_device_noop_update_inputs;
	h->base.get_tracked_pose = pico4_hmd_get_tracked_pose;
	h->base.get_view_poses = u_device_get_view_poses;
	h->base.get_visibility_mask = pico4_hmd_get_visibility_mask;
	h->base.destroy = pico4_hmd_destroy;
	h->base.name = XRT_DEVICE_GENERIC_HMD;
	h->base.device_type = XRT_DEVICE_TYPE_HMD;
	h->base.inputs[0].name = XRT_INPUT_GENERIC_HEAD_POSE;
	h->base.supported.orientation_tracking = true;
	h->base.supported.position_tracking = false;
	snprintf(h->base.str, XRT_DEVICE_NAME_LEN, "PICO 4");
	snprintf(h->base.serial, XRT_DEVICE_NAME_LEN, "PICO 4 (SSC IMU)");

	m_relation_history_create(&h->relation_hist);
	m_imu_3dof_init(&h->fusion, M_IMU_3DOF_USE_GRAVITY_DUR_20MS);
	if (os_thread_helper_init(&h->oth) != 0 || !setup_display(h)) {
		P4_ERROR(h, "Failed to set up the device");
		goto fail;
	}

	int ret = pico4_ssc_open(&h->ssc, 3000);
	if (ret < 0) {
		P4_ERROR(h, "No SNS client service (is the SLPI up and pico4-sensorspd running?): %s", strerror(-ret));
		goto fail;
	}
	if (pico4_ssc_find(h->ssc, "accel", &h->accel_suid, 3000) < 0 ||
	    pico4_ssc_find(h->ssc, "gyro", &h->gyro_suid, 3000) < 0) {
		P4_ERROR(h, "The sensor service reports no accelerometer/gyroscope");
		goto fail;
	}
	if (!calibrate_clock(h)) {
		P4_ERROR(h, "Cannot map sensor timestamps to CLOCK_MONOTONIC");
		goto fail;
	}

	float rate = (float)debug_get_num_option_pico4_imu_rate();
	if (pico4_ssc_enable(h->ssc, &h->accel_suid, rate) < 0 || pico4_ssc_enable(h->ssc, &h->gyro_suid, rate) < 0 ||
	    os_thread_helper_start(&h->oth, imu_thread, h) != 0) {
		P4_ERROR(h, "Failed to start the IMU stream");
		goto fail;
	}
	P4_INFO(h, "IMU streaming at %.0f Hz requested, QTimer offset %lld ns", rate,
	        (long long)h->qtimer_offset_ns);

	u_var_add_root(h, "PICO 4", true);
	u_var_add_log_level(h, &h->log_level, "log_level");
	u_var_add_ro_u64(h, &h->gyro_samples, "gyro_samples");
	u_var_add_ro_u64(h, &h->accel_samples, "accel_samples");
	u_var_add_ro_i64_ns(h, &h->last_latency_ns, "last_latency");
	m_imu_3dof_add_vars(&h->fusion, h, "");
	return &h->base;

fail:
	pico4_hmd_destroy(&h->base);
	return NULL;
}


/*
 *
 * Auto-prober.
 *
 */

static int
pico4_autoprobe(struct xrt_auto_prober *xap,
                cJSON *attached_data,
                bool no_hmds,
                struct xrt_prober *xp,
                struct xrt_device **out_xdevs)
{
	// Only on a headset with a sensor DSP; the device create checks the service.
	if (no_hmds || access("/sys/kernel/boot_slpi", F_OK) != 0) {
		return 0;
	}
	struct xrt_device *xdev = pico4_hmd_create();
	if (xdev == NULL) {
		return 0;
	}
	out_xdevs[0] = xdev;
	return 1;
}

static void
pico4_auto_prober_destroy(struct xrt_auto_prober *xap)
{
	free(xap);
}

struct xrt_auto_prober *
pico4_create_auto_prober(void)
{
	struct xrt_auto_prober *xap = U_TYPED_CALLOC(struct xrt_auto_prober);
	xap->name = "PICO 4";
	xap->destroy = pico4_auto_prober_destroy;
	xap->lelo_dallas_autoprobe = pico4_autoprobe;
	return xap;
}
