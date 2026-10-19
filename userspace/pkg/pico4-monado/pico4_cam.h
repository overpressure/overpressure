// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Capture from the four OV7251 tracking cameras through the Spectra CSL UAPI.
 *
 * The stock 4.19 kernel exposes Qualcomm's camera stack (CSL: request manager,
 * sync objects, CCI sensor driver, CSIPHY, CSID-lite + IFE-lite) and nothing
 * V4L2-capture shaped. This module drives that stack the way PICO OS does for
 * its tracking cameras: one CSL session per camera, only the ISP on its
 * request-manager link (the sensor follows the station's FSIN pulses, not
 * requests), and a single RDI output (raw sensor data, no pixel pipeline, no
 * firmware) into cached buffers that are invalidated before each frame is read. Sensor slots 0-3 are the OV7251s (left, right,
 * "slave", "master" modules); devices are found at run time from the media
 * graph and CAM_QUERY_CAP, not from fixed node numbers.
 *
 * Plain C11 on Linux headers plus the Spectra UAPI headers (media/cam_*.h,
 * fetched at build time, see pico4_cam.c). Not thread-safe: one thread owns a
 * pico4_cam. Functions return 0 or a negative errno and log the failing ioctl.
 *
 * @ingroup drv_pico4
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PICO4_CAM_NUM 4      //!< OV7251 tracking cameras, CSL sensor slots 0-3.
#define PICO4_CAM_WIDTH 640  //!< Image width in pixels.
#define PICO4_CAM_HEIGHT 480 //!< Image height reported to callers.
#define PICO4_CAM_LINES 480  //!< Lines the RDI writes per frame.
#define PICO4_CAM_RING_MIN 4
#define PICO4_CAM_RING_MAX 8
#define PICO4_CAM_MAX_SLOTS 8   //!< CSL sensor slots in the device tree (0-5 used on PICO 4).
#define PICO4_CAM_MAX_PHYS 8    //!< CSIPHY instances (0-5 used).
#define PICO4_CAM_MAX_ENTITIES 64

enum pico4_cam_log_level
{
	PICO4_CAM_LOG_ERROR = 0,
	PICO4_CAM_LOG_INFO = 1,
	PICO4_CAM_LOG_DEBUG = 2, //!< Every ioctl.
};

typedef void (*pico4_cam_log_fn)(void *user, enum pico4_cam_log_level level, const char *msg);

struct pico4_cam_config
{
	unsigned slots;               //!< Bitmask of sensor slots 0-3.
	unsigned fps;                 //!< 60 or 30 (frame length 862 or 1724 lines).
	unsigned ring;                //!< In-flight requests per camera, 4-8.
	int lane_assign[PICO4_CAM_NUM]; //!< CSIPHY data lane per slot, -1 = automatic (see pico4_cam.c).
	bool hold_frames;             //!< Frames stay owned until pico4_cam_release(); else the next call releases.
	bool probe_only;              //!< pico4_cam_open() stops after the sensor probes (no sessions).
	int verbose;                  //!< Highest pico4_cam_log_level that is logged.
	pico4_cam_log_fn log;         //!< NULL logs to stderr.
	void *log_user;
};

struct pico4_cam_frame
{
	int slot;                  //!< Sensor slot 0-3.
	uint64_t request_id;       //!< CSL request that carried the buffer.
	uint64_t frame_id;         //!< ISP start-of-frame counter of this session.
	int64_t sof_boottime_ns;   //!< CLOCK_BOOTTIME at the start-of-frame interrupt; 0 if no event arrived.
	int64_t sof_monotonic_ns;  //!< The same instant on CLOCK_MONOTONIC; 0 if unknown.
	uint32_t width;            //!< 640.
	uint32_t height;           //!< 480.
	uint32_t lines;            //!< Lines in the buffer (480).
	uint32_t stride;           //!< Bytes per line.
	uint32_t bytes_per_sample; //!< 2: RAW10, LSB-aligned in 16 bits.
	const void *data;          //!< First line; valid until the frame is released.
	size_t size;               //!< stride * lines.
	unsigned buffer;           //!< Ring index (internal, used by pico4_cam_release()).
};

//! Media entity found while discovering the camera devices.
struct pico4_cam_entity
{
	char name[32];
	uint32_t function;
	uint32_t major, minor;
	char devnode[64]; //!< Empty if the entity has no node.
};

//! What pico4_cam_discover() found. Integer fields are -1 where nothing was found.
struct pico4_cam_devices
{
	char crm[64];  //!< cam-req-mgr video node.
	char sync[64]; //!< cam_sync video node.
	char isp[64];  //!< cam-isp subdev.
	char sensor[PICO4_CAM_MAX_SLOTS][64];  //!< Sensor subdev by slot (cell-index).
	int sensor_csiphy[PICO4_CAM_MAX_SLOTS]; //!< csiphy_slot_id reported by each sensor.
	char csiphy[PICO4_CAM_MAX_PHYS][64];   //!< CSIPHY subdev by PHY index.
	int32_t isp_iommu[2]; //!< Image IOMMU handle: non-secure, secure.
	int32_t cdm_iommu[2]; //!< CDM IOMMU handle: non-secure, secure.
	unsigned num_entities;
	struct pico4_cam_entity entities[PICO4_CAM_MAX_ENTITIES];
};

//! Per-slot state for diagnostics.
struct pico4_cam_slot_info
{
	bool enabled;
	char sensor[64];
	char csiphy[64];
	int phy;            //!< CSIPHY index.
	bool combo;         //!< PHY shared with another slot (combo mode).
	unsigned lane_assign;
	int probe_rc;       //!< 0 = probed, negative errno, 1 = not attempted.
	int32_t session, sensor_dev, csiphy_dev, isp_dev, link;
	uint64_t frames, errors, missing_ts;
};

struct pico4_cam;

//! Defaults: all four slots, 60 fps, RAW10, free-running, ring of 6, automatic lanes.
void
pico4_cam_config_init(struct pico4_cam_config *cfg);

//! Finds the CSL nodes and queries sensors, CSIPHYs and the ISP. Powers nothing.
int
pico4_cam_discover(struct pico4_cam_devices *out, const struct pico4_cam_config *cfg);

/*!
 * Opens the request manager, probes the selected sensors and sets up one
 * session per camera: sensor acquire (power-up) and register tables, CSIPHY,
 * IFE-lite RDI and buffers. Streaming starts with pico4_cam_start().
 */
int
pico4_cam_open(struct pico4_cam **out, const struct pico4_cam_config *cfg);

//! Links, starts ISP, CSIPHY and sensor (stream on) and queues the request ring.
int
pico4_cam_start(struct pico4_cam *cam);

/*!
 * Waits up to @p timeout_ms (negative: forever) for the next frame from any
 * camera. Returns 0, -ETIMEDOUT, -EINTR (signal) or another negative errno.
 */
int
pico4_cam_next(struct pico4_cam *cam, struct pico4_cam_frame *out, int timeout_ms);

//! Gives a frame's buffer back to its camera (only needed with hold_frames).
int
pico4_cam_release(struct pico4_cam *cam, const struct pico4_cam_frame *frame);

//! Stream off, flush, stop and unlink. Safe to call twice.
int
pico4_cam_stop(struct pico4_cam *cam);

//! Stops if needed, releases every device, session and buffer, closes all nodes.
void
pico4_cam_close(struct pico4_cam *cam);

//! Copies the per-slot state of @p slot (0-3).
int
pico4_cam_get_slot_info(const struct pico4_cam *cam, int slot, struct pico4_cam_slot_info *out);

#ifdef __cplusplus
}
#endif
