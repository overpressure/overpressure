// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 6DoF head tracking: tracking cameras and IMU into Monado's SLAM tracker (Basalt).
 * @ingroup drv_pico4
 */

#pragma once

#include "xrt/xrt_defines.h"
#include "util/u_logging.h"

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct pico4_slam;
struct pico4_station;
struct pico4_fusion_slam;
struct pico4_ctrack;

/*!
 * Origin of the HMD frame (between the eyes) in the body frame of the factory
 * calibration (x up, y left, z back), metres: centred between the cameras, a
 * little below and behind them. Only affects how position follows head rotation.
 */
#define PICO4_HMD_IN_BODY {-0.025f, 0.052f, 0.04f}

/*!
 * Load the factory camera calibration from @p calibration_dir, start the SLAM
 * tracker, the cameras and their frame sync through @p station (freshly
 * reset; borrowed, it must outlive the tracker). Returns NULL if SLAM is not
 * built in, disabled (PICO4_SLAM=0), there is no station or any part fails;
 * the caller then stays on 3DoF. @p imu_hz is the rate the head IMU's samples
 * arrive at (pico4_slam_push_imu), declared to the tracker. The controller-LED
 * exposures go to @p ctrack (borrowed, may be NULL; it must outlive the tracker).
 */
struct pico4_slam *
pico4_slam_create(const char *calibration_dir,
                  struct pico4_station *station,
                  double imu_hz,
                  struct pico4_ctrack *ctrack,
                  enum u_logging_level log_level);

/*!
 * One IMU sample in the sensor DSP's raw axes (the factory "body" frame):
 * CLOCK_MONOTONIC nanoseconds, m/s^2 and rad/s, without bias correction, and the IMU's
 * latest temperature in °C (NaN if unknown; PICO OS's tracker uses it, Basalt does not).
 */
void
pico4_slam_push_imu(
    struct pico4_slam *s, int64_t timestamp_ns, const float accel[3], const float gyro[3], float temperature_c);

/*!
 * Head pose at @p at_ns in the OpenXR convention (world Y up; HMD x right, y
 * up, z back). Returns false until the tracker has a pose.
 */
bool
pico4_slam_get_head_pose(struct pico4_slam *s, int64_t at_ns, struct xrt_space_relation *out);

/*!
 * The tracker's newest pose, at the time its images were taken, in the OpenXR
 * world (Y up): the HMD's orientation and the IMU's position and velocity (the
 * HMD origin is PICO4_HMD_IN_BODY away). False until the tracker has a pose.
 */
bool
pico4_slam_get_latest(struct pico4_slam *s, struct pico4_fusion_slam *out);

/*!
 * Whether the tracker's poses already fuse the IMU at its rate (PICO OS's tracker): the HMD
 * then takes pico4_slam_get_head_pose() directly instead of its own fusion (pico4_fusion.h).
 */
bool
pico4_slam_fuses_imu(const struct pico4_slam *s);

//! Tells the tracker to drop its state and start a new map (after it ran away).
void
pico4_slam_reset(struct pico4_slam *s);

void
pico4_slam_destroy(struct pico4_slam **s);

#ifdef __cplusplus
}
#endif
