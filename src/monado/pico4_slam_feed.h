// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Shared memory between the PICO 4 driver and pico4-slamd, which runs PICO OS's head
 *         tracker (libSlam) in its bionic root (docs/xr.md#head-tracking).
 *
 * pico4-slamd creates the file (root:video, 0660) and keeps it; the driver maps it and is the
 * only writer of the frame and IMU rings, pico4-slamd the only writer of the pose ring. Each
 * ring entry is written first and its count released after it; a reader that falls behind by
 * a whole ring skips ahead. Times are CLOCK_MONOTONIC nanoseconds. The driver bumps
 * @ref pico4_slam_feed::generation when it (re)starts; pico4-slamd then starts a new tracker.
 * Plain C with fixed-size types: the same header builds against glibc and bionic.
 * @ingroup drv_pico4
 */

#pragma once

#include <stdint.h>

#define PICO4_SLAM_FEED_PATH "/dev/shm/pico4-slam"
#define PICO4_SLAM_FEED_MAGIC 0x32444546344f4350ull //!< "PCO4FED2"
#define PICO4_SLAM_FEED_W 640
#define PICO4_SLAM_FEED_H 480
#define PICO4_SLAM_FEED_CAMS 4
#define PICO4_SLAM_FEED_IMU 8192
#define PICO4_SLAM_FEED_SETS 4
#define PICO4_SLAM_FEED_POSES 4096

//! One head IMU sample: the sensor DSP's raw axes (the factory body frame), m/s^2 and rad/s.
struct pico4_slam_feed_imu
{
	int64_t ts_ns;
	float gyro[3];
	float accel[3];
	float temperature; //!< Degrees Celsius; NaN if unknown.
	uint32_t pad;
};

//! One scene exposure of the four tracking cameras, in factory camera order (cam0..cam3).
struct pico4_slam_feed_set
{
	int64_t ts_ns;                               //!< Middle of the exposure, the earliest of the four.
	int64_t sof_ns[PICO4_SLAM_FEED_CAMS];        //!< Each frame's start of frame.
	uint32_t exposure_lines[PICO4_SLAM_FEED_CAMS];
	uint32_t gain[PICO4_SLAM_FEED_CAMS];         //!< Analog gain, 16 = 1x.
	uint64_t frame_id[PICO4_SLAM_FEED_CAMS];
	//! 10-bit samples in 16 bits, the sensor's rows as read out (no rotation), metadata row excluded.
	uint16_t pixels[PICO4_SLAM_FEED_CAMS][PICO4_SLAM_FEED_W * PICO4_SLAM_FEED_H];
};

//! One pose of the tracker, at an IMU sample's time, in its own world (body frame at the start).
struct pico4_slam_feed_pose
{
	int64_t ts_ns;
	double position[3];      //!< The IMU's position, metres.
	double orientation[4];   //!< World from body, w x y z.
	double velocity[3];      //!< World frame, m/s.
	double angular[3];       //!< Body rates without bias, rad/s.
	uint32_t confidence;     //!< 0: lost or not started; above: tracked.
	int32_t error;           //!< The tracker's error code.
	uint32_t type;
	uint32_t pad;
};

struct pico4_slam_feed
{
	uint64_t magic;
	uint32_t version, generation; //!< generation: bumped by the driver at each start.
	uint64_t imu_written, sets_written, poses_written;
	uint64_t slamd_heartbeat_ns; //!< pico4-slamd's last activity.
	uint64_t pad[8];
	struct pico4_slam_feed_imu imu[PICO4_SLAM_FEED_IMU];
	struct pico4_slam_feed_pose poses[PICO4_SLAM_FEED_POSES];
	struct pico4_slam_feed_set sets[PICO4_SLAM_FEED_SETS];
};
