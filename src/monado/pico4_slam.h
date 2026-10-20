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

/*!
 * Load the factory camera calibration from @p calibration_dir, start the SLAM
 * tracker, the cameras and their frame sync through @p station (freshly
 * reset; borrowed, it must outlive the tracker). Returns NULL if SLAM is not
 * built in, disabled (PICO4_SLAM=0), there is no station or any part fails;
 * the caller then stays on 3DoF. @p imu_hz is the rate the head IMU's samples
 * arrive at (pico4_slam_push_imu), declared to the tracker.
 */
struct pico4_slam *
pico4_slam_create(const char *calibration_dir,
                  struct pico4_station *station,
                  double imu_hz,
                  enum u_logging_level log_level);

/*!
 * One IMU sample in the sensor DSP's raw axes (the factory "body" frame):
 * CLOCK_MONOTONIC nanoseconds, m/s^2 and rad/s, without bias correction.
 */
void
pico4_slam_push_imu(struct pico4_slam *s, int64_t timestamp_ns, const float accel[3], const float gyro[3]);

/*!
 * Head pose at @p at_ns in the OpenXR convention (world Y up; HMD x right, y
 * up, z back). Returns false until the tracker has a pose.
 */
bool
pico4_slam_get_head_pose(struct pico4_slam *s, int64_t at_ns, struct xrt_space_relation *out);

void
pico4_slam_destroy(struct pico4_slam **s);

#ifdef __cplusplus
}
#endif
