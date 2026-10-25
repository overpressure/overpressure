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
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PICO4_CAM_NUM 4      //!< OV7251 tracking cameras, CSL sensor slots 0-3.
#define PICO4_CAM_WIDTH 640  //!< Image width in pixels.
#define PICO4_CAM_HEIGHT 480 //!< Scene rows reported to callers.
/*!
 * Lines the RDI writes per frame by default: the sensor's metadata line
 * (pico4_cam_meta_decode) and the 480 scene rows, as PICO OS captures them.
 */
#define PICO4_CAM_LINES 481
#define PICO4_CAM_SENSOR_LINES 480 //!< Default output height programmed into the sensor.
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
	unsigned lines;               //!< Lines the RDI writes (480-482), 0: PICO4_CAM_LINES.
	unsigned sensor_lines;        //!< Output height programmed into the sensor, 0: PICO4_CAM_SENSOR_LINES.
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
	int64_t sof_qtimer_ns;     //!< The camera hardware's SOF stamp, QTimer in ns (the IMU's clock); 0 if unknown.
	uint32_t width;            //!< 640.
	uint32_t height;           //!< Scene rows from data on (480, fewer if the buffer is shorter).
	uint32_t lines;            //!< Lines in the buffer, the metadata line included (481).
	uint32_t stride;           //!< Bytes per line.
	uint32_t bytes_per_sample; //!< 2: RAW10, LSB-aligned in 16 bits.
	const void *meta;          //!< The buffer's first line: the sensor's metadata (pico4_cam_meta_decode).
	const void *data;          //!< First scene row, after the metadata line; valid until the frame is released.
	size_t size;               //!< stride * lines, from meta.
	unsigned buffer;           //!< Ring index (internal, used by pico4_cam_release()).
};

/*!
 * What the sensor applied to a frame, from its metadata line: OV7251 register
 * values carried as 10-bit samples (shifted up by two bits). PICO OS reads the
 * same words. The exposure phase follows the station's schedule.
 */
struct pico4_cam_meta
{
	unsigned gain;           //!< Analog gain, 16 = 1x.
	unsigned exposure_lines; //!< Exposure in sensor lines.
	unsigned phase;          //!< 1: scene (head tracking), 2: controller LEDs, 4: other.
	unsigned tag;            //!< The phase register's upper bits; their meaning is not known.
};

#define PICO4_CAM_PHASE_SCENE 1u
#define PICO4_CAM_PHASE_LEDS 2u

//! Decodes a metadata line of @p bytes; false if it is too short or holds no known phase.
static inline bool
pico4_cam_meta_decode(const void *line, size_t bytes, struct pico4_cam_meta *out)
{
	const uint8_t *b = (const uint8_t *)line;
	if (line == NULL || bytes < 83 * 2) {
		return false;
	}
#define PICO4_CAM_META_WORD(i) ((unsigned)b[2 * (i)] | ((unsigned)b[2 * (i) + 1] << 8))
	unsigned gain = PICO4_CAM_META_WORD(4), exp_hi = PICO4_CAM_META_WORD(6), exp_lo = PICO4_CAM_META_WORD(7);
	unsigned phase = PICO4_CAM_META_WORD(82);
#undef PICO4_CAM_META_WORD
	if ((gain | exp_hi | exp_lo | phase) > 0x3ff) {
		return false; // not 10-bit samples
	}
	out->gain = gain >> 2;
	out->exposure_lines = ((exp_hi << 6) & 0xf00) | ((exp_lo >> 2) & 0xff);
	out->phase = (phase >> 2) & 0xf;
	out->tag = (phase >> 2) >> 4;
	return out->phase == 1 || out->phase == 2 || out->phase == 4;
}

/*!
 * The median of @p n signals (n <= 8): what the cameras of one set see as a group. One
 * covered or saturated camera does not move it, unlike the mean or the first camera alone.
 */
static inline float
pico4_cam_median(const float *v, int n)
{
	float s[8];
	if (n <= 0) {
		return 0.0f;
	}
	n = n > 8 ? 8 : n;
	for (int i = 0; i < n; i++) {
		int j = i;
		for (; j > 0 && s[j - 1] > v[i]; j--) {
			s[j] = s[j - 1];
		}
		s[j] = v[i];
	}
	return n % 2 ? s[n / 2] : 0.5f * (s[n / 2 - 1] + s[n / 2]);
}

/*!
 * Assembly of one set of scene frames, one per camera, by start of frame: all sensors start
 * on the same FSIN pulse, so a set's frames start within @p window_ns of its first one. A
 * frame after that window starts the next set and drops the incomplete one (a camera missed
 * the pulse); one before it belongs to a set already given up; a second frame of a camera
 * in the same set is a duplicate.
 */
struct pico4_cam_assembly
{
	int64_t ts;   //!< Start of frame of the set's first frame.
	int have;     //!< Frames in the set.
	bool slot[8]; //!< Which cameras the set has.
};

enum pico4_cam_assembly_result
{
	PICO4_CAM_SET_ADDED,     //!< In the set, which still misses frames.
	PICO4_CAM_SET_COMPLETE,  //!< In the set, which now has all @p count frames.
	PICO4_CAM_SET_LATE,      //!< From a set already given up: skip it.
	PICO4_CAM_SET_DUPLICATE, //!< The camera is in the set already: skip it.
};

static inline void
pico4_cam_assembly_reset(struct pico4_cam_assembly *a)
{
	a->have = 0;
	for (int i = 0; i < 8; i++) {
		a->slot[i] = false;
	}
}

/*!
 * Add camera @p index's frame (start of frame @p ts) to the set of @p count cameras.
 * @p out_dropped is set when the frame dropped an incomplete set before it.
 */
static inline enum pico4_cam_assembly_result
pico4_cam_assembly_add(
    struct pico4_cam_assembly *a, int index, int64_t ts, int64_t window_ns, int count, bool *out_dropped)
{
	*out_dropped = false;
	if (a->have != 0 && ts > a->ts + window_ns) {
		*out_dropped = true;
		pico4_cam_assembly_reset(a);
	}
	if (a->have != 0 && ts < a->ts - window_ns) {
		return PICO4_CAM_SET_LATE;
	}
	if (a->have == 0) {
		a->ts = ts;
	}
	if (a->slot[index]) {
		return PICO4_CAM_SET_DUPLICATE;
	}
	a->slot[index] = true;
	a->have++;
	return a->have == count ? PICO4_CAM_SET_COMPLETE : PICO4_CAM_SET_ADDED;
}

/*!
 * Frame identity across one set of scene frames. All sensors start their frames on the
 * station's FSIN pulse, so each camera's ISP frame counter keeps a fixed offset from the
 * first camera's; a frame at another offset is from another pulse. An offset that changes
 * and then holds for PICO4_CAM_OFFSET_ADOPT sets is taken as the new one (a counter that
 * skipped).
 */
#define PICO4_CAM_OFFSET_ADOPT 2

struct pico4_cam_set_check
{
	bool have;
	int64_t offset[8];  //!< frame_id[i] - frame_id[0] of the accepted sets.
	int64_t pending[8]; //!< A different offset seen in the last sets...
	int pending_sets;   //!< ...this many in a row.
	//! The cameras are exposed each on its own (PICO OS's auto exposure): only the counters count.
	bool per_camera_exposure;
};

/*!
 * Whether @p n frames (n <= 8) form one exposure: the same exposure and analog gain in
 * each frame's metadata, and every camera's frame counter at its usual offset. False
 * with the reason in @p why otherwise; also @p why, with true, when a new offset is adopted.
 */
static inline bool
pico4_cam_set_consistent(struct pico4_cam_set_check *c,
                         const uint64_t *frame_id,
                         const struct pico4_cam_meta *meta,
                         int n,
                         char *why,
                         size_t why_len)
{
	why[0] = '\0';
	n = n > 8 ? 8 : n;
	for (int i = 1; i < n && !c->per_camera_exposure; i++) {
		if (meta[i].exposure_lines != meta[0].exposure_lines || meta[i].gain != meta[0].gain) {
			snprintf(why, why_len, "camera %d exposed %u lines at gain %u, camera 0 %u lines at gain %u", i,
			         meta[i].exposure_lines, meta[i].gain, meta[0].exposure_lines, meta[0].gain);
			return false;
		}
	}
	int64_t offset[8];
	int moved = -1;
	for (int i = 0; i < n; i++) {
		offset[i] = (int64_t)(frame_id[i] - frame_id[0]);
		if (c->have && offset[i] != c->offset[i] && moved < 0) {
			moved = i;
		}
	}
	if (!c->have || moved < 0) {
		for (int i = 0; i < n; i++) {
			c->offset[i] = offset[i];
		}
		c->have = true;
		c->pending_sets = 0;
		return true;
	}
	bool same = c->pending_sets > 0;
	for (int i = 0; same && i < n; i++) {
		same = c->pending[i] == offset[i];
	}
	c->pending_sets = same ? c->pending_sets + 1 : 1;
	for (int i = 0; i < n; i++) {
		c->pending[i] = offset[i];
	}
	if (c->pending_sets >= PICO4_CAM_OFFSET_ADOPT) {
		snprintf(why, why_len, "camera %d frame counter moved from offset %lld to %lld, adopted", moved,
		         (long long)c->offset[moved], (long long)offset[moved]);
		for (int i = 0; i < n; i++) {
			c->offset[i] = offset[i];
		}
		c->pending_sets = 0;
		return true;
	}
	snprintf(why, why_len, "camera %d frame %llu is at offset %lld from camera 0, not %lld", moved,
	         (unsigned long long)frame_id[moved], (long long)offset[moved], (long long)c->offset[moved]);
	return false;
}

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

//! Defaults: all four slots, 60 fps, RAW10, free-running, ring of 8, automatic lanes.
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
