// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Compositor target: the PICO 4 panels through the stock msm_drm KMS driver.
 *
 * Turnip's KGSL backend has no Vulkan display path on the stock kernel, so this
 * target allocates linear, dma-buf exportable images, imports them into msm_drm
 * as framebuffers and shows them with atomic commits. Plane layout and fence
 * contract are the ones pico4-kms-test verified (GRAPHICS.md): six colour
 * component layers routed to the eyes by sspp_layout, one 4320x2160 image for
 * both eyes, and per commit the CRTC release and connector retire fences.
 * Unfenced page flips showed corrupted output on this kernel.
 *
 * While it drives the panels, the target keeps the GPU at PICO4_KMS_GPU_MIN_MHZ
 * or faster (default 587, 0 leaves the governor alone) and restores the previous
 * floor afterwards. The stock msm-adreno-tz governor stays at 305 MHz under the
 * compositor's load, where frames with several layers take longer than a
 * refresh period.
 *
 * PICO4_KMS=0 disables the target. PICO4_KMS_STATS=path writes the presentation
 * statistics as JSON when the target is destroyed. PICO4_KMS_DUMP=path saves the
 * image of commit PICO4_KMS_DUMP_FRAME (default 300) as a PPM, downscaled by
 * four, and adds the mean colour of each eye to the statistics.
 *
 * @ingroup comp_main
 */

#include "util/u_debug.h"
#include "util/u_misc.h"
#include "util/u_pacing.h"

#include "main/comp_window.h"

#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>
#include <linux/dma-buf.h>

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

DEBUG_GET_ONCE_BOOL_OPTION(pico4_kms, "PICO4_KMS", true)
DEBUG_GET_ONCE_OPTION(pico4_kms_stats, "PICO4_KMS_STATS", NULL)
DEBUG_GET_ONCE_OPTION(pico4_kms_dump, "PICO4_KMS_DUMP", NULL)
DEBUG_GET_ONCE_NUM_OPTION(pico4_kms_dump_frame, "PICO4_KMS_DUMP_FRAME", 300)
DEBUG_GET_ONCE_NUM_OPTION(pico4_kms_gpu_min_mhz, "PICO4_KMS_GPU_MIN_MHZ", 587)

#define P4K_DEVICE "/dev/dri/card0"
#define P4K_KGSL "/sys/class/kgsl/kgsl-3d0/"
#define P4K_WIDTH 4320
#define P4K_HEIGHT 2160
#define P4K_EYE 2160
#define P4K_HZ 90
#define P4K_IMAGES 3
#define P4K_LAYERS 6
#define P4K_STATE 14

struct p4k_setting
{
	uint32_t property;
	uint64_t value;
};

struct p4k_image
{
	VkImage image;
	VkDeviceMemory memory;
	VkImageView view;
	int dmabuf;
	uint32_t gem;
	uint32_t fb;
	VkDeviceSize offset, pitch, size;
};

struct p4k_stats
{
	uint64_t commits, busy_retries, retire_waits, retire_timeouts, late;
	int64_t first_ns, last_ns, max_interval_ns;
	int64_t gpu_wait_ns, max_gpu_wait_ns, retire_wait_ns, max_retire_wait_ns;
	double eye_mean[2][3]; //!< Mean RGB per eye of the dumped image, -1 without a dump.
	uint64_t vblanks;
	double first_vblank, last_vblank;
	//! Each commit asks for the next vblank event: a skipped sequence is a vblank that repeated a frame.
	unsigned first_sequence, last_sequence, repeated_vblanks;
};

struct pico4_kms_target
{
	struct comp_target base;

	int fd;
	uint32_t connector, crtc;
	unsigned crtc_index;
	drmModeModeInfo mode;
	uint32_t planes[P4K_LAYERS];
	uint32_t fb_property[P4K_LAYERS];
	struct p4k_setting state[P4K_LAYERS][P4K_STATE];
	uint32_t release_property, retire_property;
	bool xrgb, xbgr; //!< Every layer takes this DRM format.
	bool active;     //!< Mode set and layers enabled.
	bool vblank_pending;

	uint32_t fourcc;
	uint32_t pitch;
	struct p4k_image imgs[P4K_IMAGES];
	struct comp_target_image images[P4K_IMAGES];
	uint32_t next;
	int64_t acquired;
	int64_t shown; //!< Image of the last commit, -1 if none.
	int release_fd, retire_fd;

	struct u_pacing_compositor *upc;
	struct p4k_stats stats;
	bool has_vulkan;

	int saved_min_pwrlevel; //!< KGSL power level floor to restore, -1 if untouched.
};


/*
 *
 * DRM helpers.
 *
 */

static uint32_t
p4k_property(int fd, uint32_t object, uint32_t type, const char *name, uint64_t *out_value)
{
	drmModeObjectProperties *props = drmModeObjectGetProperties(fd, object, type);
	if (props == NULL) {
		return 0;
	}
	uint32_t id = 0;
	for (uint32_t i = 0; i < props->count_props && id == 0; i++) {
		drmModePropertyRes *p = drmModeGetProperty(fd, props->props[i]);
		if (p == NULL) {
			continue;
		}
		if (strcmp(p->name, name) == 0) {
			id = p->prop_id;
			if (out_value != NULL) {
				*out_value = props->prop_values[i];
			}
		}
		drmModeFreeProperty(p);
	}
	drmModeFreeObjectProperties(props);
	return id;
}

static bool
p4k_enum(int fd, uint32_t plane, const char *name, const char *option, uint64_t *out_value)
{
	drmModePropertyRes *p = drmModeGetProperty(fd, p4k_property(fd, plane, DRM_MODE_OBJECT_PLANE, name, NULL));
	if (p == NULL) {
		return false;
	}
	bool found = false;
	for (int i = 0; i < p->count_enums && !found; i++) {
		if (strcmp(p->enums[i].name, option) == 0) {
			*out_value = p->enums[i].value;
			found = true;
		}
	}
	drmModeFreeProperty(p);
	return found;
}

static bool
p4k_add(drmModeAtomicReq *req, int fd, uint32_t object, uint32_t type, const char *name, uint64_t value)
{
	uint32_t id = p4k_property(fd, object, type, name, NULL);
	return id != 0 && drmModeAtomicAddProperty(req, object, id, value) >= 0;
}

static bool
p4k_is_display(int fd)
{
	drmVersion *version = drmGetVersion(fd);
	bool msm = version != NULL && strcmp(version->name, "msm_drm") == 0;
	drmFreeVersion(version);
	return msm;
}

//! The connected DSI connector and its native mode at @ref P4K_HZ.
static bool
p4k_find_output(int fd, drmModeRes *res, uint32_t *out_connector, drmModeModeInfo *out_mode, uint32_t *out_crtcs)
{
	char prefix[32];
	snprintf(prefix, sizeof(prefix), "%dx%dx%dx", P4K_WIDTH, P4K_HEIGHT, P4K_HZ);
	bool found = false;
	for (int i = 0; i < res->count_connectors && !found; i++) {
		drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
		if (c == NULL) {
			continue;
		}
		if (c->connector_type == DRM_MODE_CONNECTOR_DSI && c->connection == DRM_MODE_CONNECTED) {
			for (int m = 0; m < c->count_modes && !found; m++) {
				const drmModeModeInfo *mode = &c->modes[m];
				if (mode->hdisplay == P4K_WIDTH && mode->vdisplay == P4K_HEIGHT &&
				    strncmp(mode->name, prefix, strlen(prefix)) == 0) {
					*out_connector = c->connector_id;
					*out_mode = *mode;
					found = true;
				}
			}
			*out_crtcs = 0;
			for (int e = 0; e < c->count_encoders && found; e++) {
				drmModeEncoder *enc = drmModeGetEncoder(fd, c->encoders[e]);
				if (enc != NULL) {
					*out_crtcs |= enc->possible_crtcs;
					drmModeFreeEncoder(enc);
				}
			}
		}
		drmModeFreeConnector(c);
	}
	return found;
}

static bool
p4k_plane_has_format(drmModePlane *p, uint32_t fourcc)
{
	for (uint32_t i = 0; i < p->count_formats; i++) {
		if (p->formats[i] == fourcc) {
			return true;
		}
	}
	return false;
}

/*!
 * The stock layer arrangement: four VIG (scaler) and two DMA layers, one per
 * colour component and eye, blended per component. Destination coordinates are
 * eye-local; the right eye reads the right half of the shared image.
 */
static bool
p4k_setup_layers(struct pico4_kms_target *kt)
{
	struct comp_compositor *c = kt->base.c;
	int fd = kt->fd;
	static const int eye[P4K_LAYERS] = {1, 0, 1, 0, 1, 0};
	static const int stage[P4K_LAYERS] = {3, 2, 1, 0, 5, 4};
	static const char *color[P4K_LAYERS] = {"blue", "blue", "red", "red", "green", "green"};

	drmModePlaneRes *pr = drmModeGetPlaneResources(fd);
	if (pr == NULL) {
		COMP_ERROR(c, "pico4_kms: no plane resources: %s", strerror(errno));
		return false;
	}
	unsigned nvig = 0, ndma = 0;
	kt->xrgb = kt->xbgr = true;
	for (uint32_t i = 0; i < pr->count_planes && nvig + ndma < P4K_LAYERS; i++) {
		drmModePlane *p = drmModeGetPlane(fd, pr->planes[i]);
		if (p == NULL) {
			continue;
		}
		uint64_t type = 0;
		p4k_property(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "type", &type);
		bool usable = p4k_plane_has_format(p, DRM_FORMAT_XRGB8888) && type != DRM_PLANE_TYPE_CURSOR &&
		              (p->possible_crtcs & (1u << kt->crtc_index)) != 0;
		bool scaler = p4k_property(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "scaler_v2", NULL) != 0;
		bool multirect = p4k_property(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "multirect_mode", NULL) != 0;
		int slot = -1;
		if (usable && scaler && nvig < 4) {
			slot = (int)nvig++;
		} else if (usable && !scaler && !multirect && type == DRM_PLANE_TYPE_OVERLAY && ndma < 2) {
			slot = 4 + (int)ndma++;
		}
		if (slot >= 0) {
			kt->planes[slot] = p->plane_id;
			kt->xrgb = kt->xrgb && p4k_plane_has_format(p, DRM_FORMAT_XRGB8888);
			kt->xbgr = kt->xbgr && p4k_plane_has_format(p, DRM_FORMAT_XBGR8888);
		}
		drmModeFreePlane(p);
	}
	drmModeFreePlaneResources(pr);
	if (nvig != 4 || ndma != 2) {
		COMP_ERROR(c, "pico4_kms: found %u VIG and %u DMA layers, need 4 and 2", nvig, ndma);
		return false;
	}

	static const char *names[P4K_STATE] = {
	    "CRTC_ID", "sspp_layout", "layer_color_component", "blend_op", "rotation", "zpos", "CRTC_X",
	    "CRTC_Y",  "CRTC_W",      "CRTC_H",                "SRC_X",    "SRC_Y",    "SRC_W", "SRC_H",
	};
	for (unsigned i = 0; i < P4K_LAYERS; i++) {
		uint32_t p = kt->planes[i];
		uint64_t layout, component, blend;
		if (!p4k_enum(fd, p, "sspp_layout", eye[i] ? "right" : "left", &layout) ||
		    !p4k_enum(fd, p, "layer_color_component", color[i], &component) ||
		    !p4k_enum(fd, p, "blend_op", "layer_color", &blend)) {
			COMP_ERROR(c, "pico4_kms: plane %u lacks the stock layer properties", p);
			return false;
		}
		uint64_t values[P4K_STATE] = {
		    kt->crtc, layout, component, blend, DRM_MODE_ROTATE_0, (uint64_t)stage[i], 0, 0, P4K_EYE, P4K_EYE,
		    (uint64_t)eye[i] * P4K_EYE << 16, 0, (uint64_t)P4K_EYE << 16, (uint64_t)P4K_EYE << 16,
		};
		for (unsigned j = 0; j < P4K_STATE; j++) {
			kt->state[i][j].property = p4k_property(fd, p, DRM_MODE_OBJECT_PLANE, names[j], NULL);
			kt->state[i][j].value = values[j];
			if (kt->state[i][j].property == 0) {
				COMP_ERROR(c, "pico4_kms: plane %u has no %s", p, names[j]);
				return false;
			}
		}
		kt->fb_property[i] = p4k_property(fd, p, DRM_MODE_OBJECT_PLANE, "FB_ID", NULL);
		if (kt->fb_property[i] == 0) {
			return false;
		}
	}
	return true;
}

static bool
p4k_open(struct pico4_kms_target *kt)
{
	struct comp_compositor *c = kt->base.c;

	kt->fd = open(P4K_DEVICE, O_RDWR | O_CLOEXEC);
	if (kt->fd < 0 || !p4k_is_display(kt->fd)) {
		COMP_ERROR(c, "pico4_kms: %s is not the stock msm_drm device", P4K_DEVICE);
		return false;
	}
	if (drmSetClientCap(kt->fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) != 0 ||
	    drmSetClientCap(kt->fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0) {
		COMP_ERROR(c, "pico4_kms: atomic modesetting unavailable: %s", strerror(errno));
		return false;
	}
	if (drmSetMaster(kt->fd) != 0) {
		COMP_ERROR(c, "pico4_kms: cannot become DRM master (is another compositor running?): %s",
		           strerror(errno));
		return false;
	}

	drmModeRes *res = drmModeGetResources(kt->fd);
	if (res == NULL) {
		return false;
	}
	uint32_t crtcs = 0;
	bool ok = p4k_find_output(kt->fd, res, &kt->connector, &kt->mode, &crtcs);
	if (!ok) {
		COMP_ERROR(c, "pico4_kms: no connected DSI output with a %dx%d %d Hz mode", P4K_WIDTH, P4K_HEIGHT,
		           P4K_HZ);
	}
	kt->crtc_index = 0;
	while (ok && kt->crtc_index < (unsigned)res->count_crtcs && (crtcs & (1u << kt->crtc_index)) == 0) {
		kt->crtc_index++;
	}
	ok = ok && kt->crtc_index < (unsigned)res->count_crtcs;
	if (ok) {
		kt->crtc = res->crtcs[kt->crtc_index];
	}
	drmModeFreeResources(res);

	ok = ok && p4k_setup_layers(kt);
	if (ok) {
		kt->release_property = p4k_property(kt->fd, kt->crtc, DRM_MODE_OBJECT_CRTC, "output_fence", NULL);
		kt->retire_property =
		    p4k_property(kt->fd, kt->connector, DRM_MODE_OBJECT_CONNECTOR, "RETIRE_FENCE", NULL);
		ok = kt->release_property != 0 && kt->retire_property != 0;
		if (!ok) {
			COMP_ERROR(c, "pico4_kms: the vendor release/retire fence properties are missing");
		}
	}
	if (ok) {
		COMP_INFO(c, "pico4_kms: connector %u, CRTC %u, mode %s, layers %u %u %u %u %u %u", kt->connector,
		          kt->crtc, kt->mode.name, kt->planes[0], kt->planes[1], kt->planes[2], kt->planes[3],
		          kt->planes[4], kt->planes[5]);
	}
	return ok;
}

/*
 *
 * GPU clock floor.
 *
 */

static bool
p4k_read_text(const char *path, char *buf, size_t size)
{
	FILE *f = fopen(path, "r");
	if (f == NULL) {
		return false;
	}
	size_t n = fread(buf, 1, size - 1, f);
	fclose(f);
	buf[n] = '\0';
	return n > 0;
}

static bool
p4k_write_int(const char *path, int value)
{
	FILE *f = fopen(path, "w");
	if (f == NULL) {
		return false;
	}
	bool ok = fprintf(f, "%d\n", value) > 0;
	return fclose(f) == 0 && ok;
}

/*!
 * KGSL power levels index gpu_available_frequencies (fastest first): pick the
 * slowest level at or above the wanted clock as min_pwrlevel.
 */
static void
p4k_gpu_floor(struct pico4_kms_target *kt)
{
	struct comp_compositor *c = kt->base.c;
	int64_t mhz = debug_get_num_option_pico4_kms_gpu_min_mhz();
	char freqs[256], current[16];
	if (mhz <= 0 || !p4k_read_text(P4K_KGSL "gpu_available_frequencies", freqs, sizeof(freqs)) ||
	    !p4k_read_text(P4K_KGSL "min_pwrlevel", current, sizeof(current))) {
		return;
	}
	int level = -1, index = 0;
	for (char *tok = strtok(freqs, " \n"); tok != NULL; tok = strtok(NULL, " \n"), index++) {
		if (strtoll(tok, NULL, 10) >= mhz * 1000000) {
			level = index;
		}
	}
	int old = atoi(current);
	if (level < 0 || level >= old) {
		return; // Already at or above the floor.
	}
	if (!p4k_write_int(P4K_KGSL "min_pwrlevel", level)) {
		COMP_WARN(c, "pico4_kms: cannot raise the GPU clock floor: %s", strerror(errno));
		return;
	}
	kt->saved_min_pwrlevel = old;
	COMP_INFO(c, "pico4_kms: GPU power level floor %d -> %d (%" PRId64 " MHz or faster)", old, level, mhz);
}

static void
p4k_gpu_floor_restore(struct pico4_kms_target *kt)
{
	if (kt->saved_min_pwrlevel >= 0) {
		p4k_write_int(P4K_KGSL "min_pwrlevel", kt->saved_min_pwrlevel);
		kt->saved_min_pwrlevel = -1;
	}
}

static double
p4k_seconds(unsigned sec, unsigned usec)
{
	return sec + usec * 1e-6;
}

static void
p4k_vblank_handler(int fd, unsigned sequence, unsigned sec, unsigned usec, void *data)
{
	struct pico4_kms_target *kt = data;
	struct p4k_stats *s = &kt->stats;
	double t = p4k_seconds(sec, usec);
	if (s->vblanks == 0) {
		s->first_vblank = t;
		s->first_sequence = sequence;
	} else if (sequence - s->last_sequence != 1) {
		s->repeated_vblanks += sequence - s->last_sequence - 1;
	}
	s->last_vblank = t;
	s->last_sequence = sequence;
	s->vblanks++;
	kt->vblank_pending = false;
}

/*!
 * Keep one vblank event queued, as pico4-kms-test does: this vendor driver
 * holds no vblank reference for commits on its own.
 */
static void
p4k_poll_vblank(struct pico4_kms_target *kt)
{
	struct pollfd p = {.fd = kt->fd, .events = POLLIN};
	while (poll(&p, 1, 0) > 0 && (p.revents & POLLIN) != 0) {
		drmEventContext ctx = {.version = 2, .vblank_handler = p4k_vblank_handler};
		if (drmHandleEvent(kt->fd, &ctx) != 0) {
			break;
		}
	}
	if (kt->active && !kt->vblank_pending) {
		drmVBlank request = {0};
		request.request.type = DRM_VBLANK_RELATIVE | DRM_VBLANK_EVENT |
		                       ((kt->crtc_index << DRM_VBLANK_HIGH_CRTC_SHIFT) & DRM_VBLANK_HIGH_CRTC_MASK);
		request.request.sequence = 1;
		request.request.signal = (unsigned long)kt;
		kt->vblank_pending = drmWaitVBlank(kt->fd, &request) == 0;
	}
}

//! Wait until the previous commit is on screen, which also frees the image before it.
static void
p4k_wait_retired(struct pico4_kms_target *kt)
{
	if (kt->retire_fd < 0) {
		return;
	}
	struct pollfd p = {.fd = kt->retire_fd, .events = POLLIN};
	if (poll(&p, 1, 0) == 0) {
		int64_t before_ns = os_monotonic_get_ns();
		kt->stats.retire_waits++;
		if (poll(&p, 1, 500) == 0) {
			kt->stats.retire_timeouts++;
			COMP_ERROR(kt->base.c, "pico4_kms: retire fence not signalled after 500 ms");
		}
		int64_t waited_ns = os_monotonic_get_ns() - before_ns;
		kt->stats.retire_wait_ns += waited_ns;
		if (waited_ns > kt->stats.max_retire_wait_ns) {
			kt->stats.max_retire_wait_ns = waited_ns;
		}
	}
	close(kt->retire_fd);
	close(kt->release_fd);
	kt->retire_fd = kt->release_fd = -1;
}

static bool
p4k_commit(struct pico4_kms_target *kt, uint32_t index)
{
	struct comp_compositor *c = kt->base.c;
	uint32_t fb = kt->imgs[index].fb;
	uint64_t release_fd = UINT64_MAX, retire_fd = UINT64_MAX;
	bool modeset = !kt->active;
	uint32_t blob = 0;

	drmModeAtomicReq *req = drmModeAtomicAlloc();
	bool ok = req != NULL;
	if (ok && modeset) {
		ok = drmModeCreatePropertyBlob(kt->fd, &kt->mode, sizeof(kt->mode), &blob) == 0 &&
		     p4k_add(req, kt->fd, kt->connector, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", kt->crtc) &&
		     p4k_add(req, kt->fd, kt->crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID", blob) &&
		     p4k_add(req, kt->fd, kt->crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE", 1);
		for (unsigned i = 0; ok && i < P4K_LAYERS; i++) {
			for (unsigned j = 0; ok && j < P4K_STATE; j++) {
				ok = drmModeAtomicAddProperty(req, kt->planes[i], kt->state[i][j].property,
				                              kt->state[i][j].value) >= 0;
			}
		}
	} else if (ok) {
		// The vendor fence properties return 64-bit fds through user pointers.
		ok = drmModeAtomicAddProperty(req, kt->crtc, kt->release_property, (uint64_t)(uintptr_t)&release_fd) >=
		         0 &&
		     drmModeAtomicAddProperty(req, kt->connector, kt->retire_property,
		                              (uint64_t)(uintptr_t)&retire_fd) >= 0;
	}
	for (unsigned i = 0; ok && i < P4K_LAYERS; i++) {
		ok = drmModeAtomicAddProperty(req, kt->planes[i], kt->fb_property[i], fb) >= 0;
	}

	int ret = -1;
	if (ok) {
		// The first commit sets the mode and blocks; later ones flip without blocking.
		uint32_t flags = modeset ? DRM_MODE_ATOMIC_ALLOW_MODESET : DRM_MODE_ATOMIC_NONBLOCK;
		for (int attempt = 0; attempt < 20; attempt++) {
			ret = drmModeAtomicCommit(kt->fd, req, flags, kt);
			if (ret == 0 || errno != EBUSY) {
				break;
			}
			kt->stats.busy_retries++;
			usleep(1000);
		}
	}
	int err = errno;
	drmModeAtomicFree(req);
	if (blob != 0) {
		drmModeDestroyPropertyBlob(kt->fd, blob);
	}
	if (!ok || ret != 0) {
		COMP_ERROR(c, "pico4_kms: %s commit failed: %s", modeset ? "mode set" : "flip", strerror(err));
		return false;
	}
	if (!modeset && (release_fd > INT_MAX || retire_fd > INT_MAX)) {
		COMP_ERROR(c, "pico4_kms: the driver returned no display fences");
		return false;
	}
	kt->release_fd = modeset ? -1 : (int)release_fd;
	kt->retire_fd = modeset ? -1 : (int)retire_fd;
	if (modeset) {
		COMP_INFO(c, "pico4_kms: %s active", kt->mode.name);
		p4k_gpu_floor(kt);
	}
	kt->active = true;
	kt->shown = index;
	return true;
}

static void
p4k_disable(struct pico4_kms_target *kt)
{
	if (!kt->active) {
		return;
	}
	p4k_wait_retired(kt);
	drmModeAtomicReq *req = drmModeAtomicAlloc();
	bool ok = req != NULL;
	for (unsigned i = 0; ok && i < P4K_LAYERS; i++) {
		ok = p4k_add(req, kt->fd, kt->planes[i], DRM_MODE_OBJECT_PLANE, "FB_ID", 0) &&
		     p4k_add(req, kt->fd, kt->planes[i], DRM_MODE_OBJECT_PLANE, "CRTC_ID", 0);
	}
	ok = ok && p4k_add(req, kt->fd, kt->connector, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", 0) &&
	     p4k_add(req, kt->fd, kt->crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID", 0) &&
	     p4k_add(req, kt->fd, kt->crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE", 0);
	if (!ok || drmModeAtomicCommit(kt->fd, req, DRM_MODE_ATOMIC_ALLOW_MODESET, NULL) != 0) {
		COMP_ERROR(kt->base.c, "pico4_kms: disabling the display failed: %s", strerror(errno));
	}
	drmModeAtomicFree(req);
	kt->active = false;
	kt->shown = -1;
}


/*
 *
 * Images.
 *
 */

static uint32_t
p4k_fourcc(VkFormat format)
{
	switch (format) {
	case VK_FORMAT_B8G8R8A8_UNORM:
	case VK_FORMAT_B8G8R8A8_SRGB: return DRM_FORMAT_XRGB8888;
	case VK_FORMAT_R8G8B8A8_UNORM:
	case VK_FORMAT_R8G8B8A8_SRGB:
	case VK_FORMAT_A8B8G8R8_UNORM_PACK32:
	case VK_FORMAT_A8B8G8R8_SRGB_PACK32: return DRM_FORMAT_XBGR8888;
	default: return 0;
	}
}

//! Linear, dma-buf exportable and usable as asked for by the renderer.
static bool
p4k_format_usable(struct vk_bundle *vk, VkFormat format, VkImageUsageFlags usage)
{
	VkPhysicalDeviceExternalImageFormatInfo external = {
	    .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
	    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	VkPhysicalDeviceImageFormatInfo2 info = {
	    .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
	    .pNext = &external,
	    .format = format,
	    .type = VK_IMAGE_TYPE_2D,
	    .tiling = VK_IMAGE_TILING_LINEAR,
	    .usage = usage,
	};
	VkExternalImageFormatProperties external_props = {
	    .sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES,
	};
	VkImageFormatProperties2 props = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
	    .pNext = &external_props,
	};
	VkResult ret = vk->vkGetPhysicalDeviceImageFormatProperties2(vk->physical_device, &info, &props);
	return ret == VK_SUCCESS && props.imageFormatProperties.maxExtent.width >= P4K_WIDTH &&
	       props.imageFormatProperties.maxExtent.height >= P4K_HEIGHT &&
	       (external_props.externalMemoryProperties.externalMemoryFeatures &
	        VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT) != 0;
}

static void
p4k_free_image(struct pico4_kms_target *kt, struct p4k_image *img)
{
	struct vk_bundle *vk = &kt->base.c->base.vk;
	if (img->fb != 0) {
		drmModeRmFB(kt->fd, img->fb);
	}
	if (img->gem != 0) {
		drmCloseBufferHandle(kt->fd, img->gem);
	}
	if (img->dmabuf >= 0) {
		close(img->dmabuf);
	}
	if (img->view != VK_NULL_HANDLE) {
		vk->vkDestroyImageView(vk->device, img->view, NULL);
	}
	if (img->image != VK_NULL_HANDLE) {
		vk->vkDestroyImage(vk->device, img->image, NULL);
	}
	if (img->memory != VK_NULL_HANDLE) {
		vk->vkFreeMemory(vk->device, img->memory, NULL);
	}
	*img = (struct p4k_image){.dmabuf = -1};
}

static void
p4k_free_images(struct pico4_kms_target *kt)
{
	// Nothing may scan out an image that is about to go away.
	p4k_disable(kt);
	for (uint32_t i = 0; i < P4K_IMAGES; i++) {
		p4k_free_image(kt, &kt->imgs[i]);
	}
	kt->base.image_count = 0;
	kt->base.images = NULL;
}

static bool
p4k_create_image(struct pico4_kms_target *kt, VkFormat format, VkImageUsageFlags usage, struct p4k_image *img)
{
	struct comp_compositor *c = kt->base.c;
	struct vk_bundle *vk = &c->base.vk;
	VkResult ret;

	VkExternalMemoryImageCreateInfo external = {
	    .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
	    .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	VkImageCreateInfo info = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
	    .pNext = &external,
	    .imageType = VK_IMAGE_TYPE_2D,
	    .format = format,
	    .extent = {P4K_WIDTH, P4K_HEIGHT, 1},
	    .mipLevels = 1,
	    .arrayLayers = 1,
	    .samples = VK_SAMPLE_COUNT_1_BIT,
	    .tiling = VK_IMAGE_TILING_LINEAR,
	    .usage = usage,
	    .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
	    .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
	};
	ret = vk->vkCreateImage(vk->device, &info, NULL, &img->image);
	if (ret != VK_SUCCESS) {
		COMP_ERROR(c, "pico4_kms: vkCreateImage: %s", vk_result_string(ret));
		return false;
	}

	VkMemoryRequirements reqs;
	vk->vkGetImageMemoryRequirements(vk->device, img->image, &reqs);
	uint32_t type = 0;
	if (!vk_get_memory_type(vk, reqs.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, &type)) {
		COMP_ERROR(c, "pico4_kms: no device-local memory type");
		return false;
	}
	VkMemoryDedicatedAllocateInfo dedicated = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
	    .image = img->image,
	};
	VkExportMemoryAllocateInfo export_info = {
	    .sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
	    .pNext = &dedicated,
	    .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	VkMemoryAllocateInfo alloc = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
	    .pNext = &export_info,
	    .allocationSize = reqs.size,
	    .memoryTypeIndex = type,
	};
	ret = vk->vkAllocateMemory(vk->device, &alloc, NULL, &img->memory);
	if (ret == VK_SUCCESS) {
		ret = vk->vkBindImageMemory(vk->device, img->image, img->memory, 0);
	}
	if (ret != VK_SUCCESS) {
		COMP_ERROR(c, "pico4_kms: image memory: %s", vk_result_string(ret));
		return false;
	}
	img->size = reqs.size;

	VkMemoryGetFdInfoKHR fd_info = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
	    .memory = img->memory,
	    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	ret = vk->vkGetMemoryFdKHR(vk->device, &fd_info, &img->dmabuf);
	if (ret != VK_SUCCESS) {
		img->dmabuf = -1;
		COMP_ERROR(c, "pico4_kms: vkGetMemoryFdKHR: %s", vk_result_string(ret));
		return false;
	}

	VkImageSubresource sub = {.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT};
	VkSubresourceLayout layout;
	vk->vkGetImageSubresourceLayout(vk->device, img->image, &sub, &layout);
	img->offset = layout.offset;
	img->pitch = layout.rowPitch;

	uint32_t handles[4] = {0}, pitches[4] = {(uint32_t)img->pitch}, offsets[4] = {(uint32_t)img->offset};
	if (drmPrimeFDToHandle(kt->fd, img->dmabuf, &img->gem) != 0) {
		COMP_ERROR(c, "pico4_kms: importing the dma-buf into msm_drm: %s", strerror(errno));
		return false;
	}
	handles[0] = img->gem;
	if (drmModeAddFB2(kt->fd, P4K_WIDTH, P4K_HEIGHT, kt->fourcc, handles, pitches, offsets, &img->fb, 0) != 0) {
		COMP_ERROR(c, "pico4_kms: drmModeAddFB2 (pitch %u, offset %u): %s", pitches[0], offsets[0],
		           strerror(errno));
		return false;
	}

	VkImageSubresourceRange range = {
	    .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
	    .levelCount = 1,
	    .layerCount = 1,
	};
	ret = vk_create_view(vk, img->image, VK_IMAGE_VIEW_TYPE_2D, format, range, &img->view);
	if (ret != VK_SUCCESS) {
		COMP_ERROR(c, "pico4_kms: vk_create_view: %s", vk_result_string(ret));
		return false;
	}
	return true;
}

//! An image on screen as a PPM, downscaled by four, and its mean colour per eye (debugging only).
static void
p4k_dump(struct pico4_kms_target *kt, uint32_t index, const char *path)
{
	struct p4k_image *img = &kt->imgs[index];
	uint8_t *map = mmap(NULL, img->size, PROT_READ, MAP_SHARED, img->dmabuf, 0);
	if (map == MAP_FAILED) {
		COMP_ERROR(kt->base.c, "pico4_kms: mmap of the scanout image: %s", strerror(errno));
		return;
	}
	struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
	ioctl(img->dmabuf, DMA_BUF_IOCTL_SYNC, &sync);
	FILE *f = fopen(path, "wb");
	bool bgr = kt->fourcc == DRM_FORMAT_XRGB8888;
	double sum[2][3] = {{0}};
	if (f != NULL) {
		fprintf(f, "P6\n%d %d\n255\n", P4K_WIDTH / 4, P4K_HEIGHT / 4);
	}
	for (int y = 0; y < P4K_HEIGHT; y += 4) {
		for (int x = 0; x < P4K_WIDTH; x += 4) {
			const uint8_t *px = map + img->offset + (size_t)y * img->pitch + (size_t)x * 4;
			uint8_t rgb[3] = {bgr ? px[2] : px[0], px[1], bgr ? px[0] : px[2]};
			for (int c = 0; c < 3; c++) {
				sum[x / P4K_EYE][c] += rgb[c];
			}
			if (f != NULL) {
				fwrite(rgb, 1, 3, f);
			}
		}
	}
	if (f != NULL) {
		fclose(f);
	}
	for (int e = 0; e < 2; e++) {
		for (int c = 0; c < 3; c++) {
			kt->stats.eye_mean[e][c] = sum[e][c] / ((P4K_EYE / 4) * (P4K_HEIGHT / 4));
		}
	}
	sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
	ioctl(img->dmabuf, DMA_BUF_IOCTL_SYNC, &sync);
	munmap(map, img->size);
}

static void
p4k_write_stats(struct pico4_kms_target *kt, const char *path)
{
	const struct p4k_stats *s = &kt->stats;
	double seconds = (s->last_ns - s->first_ns) / 1e9;
	double vblank_seconds = s->last_vblank - s->first_vblank;
	FILE *f = fopen(path, "w");
	if (f == NULL) {
		return;
	}
	fprintf(f,
	        "{\"target\":\"pico4_kms\",\"mode\":\"%s\",\"format\":\"%s\",\"pitch\":%u,\"commits\":%" PRIu64
	        ",\"commit_hz\":%.4f,\"max_interval_ms\":%.3f,\"late\":%" PRIu64 ",\"busy_retries\":%" PRIu64
	        ",\"retire_waits\":%" PRIu64 ",\"retire_timeouts\":%" PRIu64 ",\"vblanks\":%" PRIu64
	        ",\"vblank_hz\":%.4f,\"repeated_vblanks\":%u,\"gpu_wait_ms\":{\"mean\":%.3f,\"max\":%.3f}"
	        ",\"retire_wait_ms\":{\"mean\":%.3f,\"max\":%.3f}"
	        ",\"eye_mean_rgb\":",
	        kt->mode.name, kt->fourcc == DRM_FORMAT_XRGB8888 ? "XRGB8888" : "XBGR8888", kt->pitch, s->commits,
	        s->commits > 1 && seconds > 0 ? (s->commits - 1) / seconds : 0.0, s->max_interval_ns / 1e6, s->late,
	        s->busy_retries, s->retire_waits, s->retire_timeouts, s->vblanks,
	        s->vblanks > 1 && vblank_seconds > 0 ? (s->last_sequence - s->first_sequence) / vblank_seconds : 0.0,
	        s->repeated_vblanks, s->commits ? s->gpu_wait_ns / 1e6 / s->commits : 0.0, s->max_gpu_wait_ns / 1e6,
	        s->retire_waits ? s->retire_wait_ns / 1e6 / s->retire_waits : 0.0, s->max_retire_wait_ns / 1e6);
	if (s->eye_mean[0][0] < 0) {
		fprintf(f, "null}\n");
	} else {
		fprintf(f, "[[%.1f,%.1f,%.1f],[%.1f,%.1f,%.1f]]}\n", s->eye_mean[0][0], s->eye_mean[0][1],
		        s->eye_mean[0][2], s->eye_mean[1][0], s->eye_mean[1][1], s->eye_mean[1][2]);
	}
	fclose(f);
}


/*
 *
 * Target members.
 *
 */

static bool
target_init_pre_vulkan(struct comp_target *ct)
{
	return true; // The display was opened when the target was created.
}

static bool
target_init_post_vulkan(struct comp_target *ct, uint32_t preferred_width, uint32_t preferred_height)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;
	struct vk_bundle *vk = &ct->c->base.vk;

	if (!vk->has_EXT_external_memory_dma_buf) {
		COMP_ERROR(ct->c, "pico4_kms: the Vulkan driver lacks VK_EXT_external_memory_dma_buf");
		return false;
	}
	kt->has_vulkan = true;
	return true;
}

static bool
target_check_ready(struct comp_target *ct)
{
	return true;
}

static void
target_create_images(struct comp_target *ct, const struct comp_target_create_images_info *create_info)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;
	struct vk_bundle *vk = &ct->c->base.vk;

	assert(kt->has_vulkan);
	p4k_free_images(kt);

	VkFormat format = VK_FORMAT_UNDEFINED;
	for (uint32_t i = 0; i < create_info->format_count && format == VK_FORMAT_UNDEFINED; i++) {
		VkFormat f = create_info->formats[i];
		uint32_t fourcc = p4k_fourcc(f);
		bool layers = (fourcc == DRM_FORMAT_XRGB8888 && kt->xrgb) || (fourcc == DRM_FORMAT_XBGR8888 && kt->xbgr);
		if (layers && p4k_format_usable(vk, f, create_info->image_usage)) {
			format = f;
			kt->fourcc = fourcc;
		}
	}
	if (format == VK_FORMAT_UNDEFINED) {
		COMP_ERROR(ct->c,
		           "pico4_kms: no requested format is linear, exportable and scanout capable "
		           "(usage 0x%x; XRT_COMPOSITOR_COMPUTE=0 selects the graphics path)",
		           create_info->image_usage);
		return;
	}
	if (create_info->extent.width != P4K_WIDTH || create_info->extent.height != P4K_HEIGHT) {
		COMP_WARN(ct->c, "pico4_kms: ignoring the preferred extent %ux%u", create_info->extent.width,
		          create_info->extent.height);
	}

	for (uint32_t i = 0; i < P4K_IMAGES; i++) {
		if (!p4k_create_image(kt, format, create_info->image_usage, &kt->imgs[i])) {
			for (uint32_t j = 0; j <= i; j++) {
				p4k_free_image(kt, &kt->imgs[j]);
			}
			return;
		}
		kt->images[i].handle = kt->imgs[i].image;
		kt->images[i].view = kt->imgs[i].view;
	}
	kt->pitch = (uint32_t)kt->imgs[0].pitch;
	COMP_INFO(ct->c, "pico4_kms: %u images, %s, pitch %u, offset %u", P4K_IMAGES, vk_format_string(format),
	          kt->pitch, (unsigned)kt->imgs[0].offset);

	kt->base.image_count = P4K_IMAGES;
	kt->base.images = kt->images;
	kt->base.width = P4K_WIDTH;
	kt->base.height = P4K_HEIGHT;
	kt->base.format = format;
	// Linear images are not compressed; the display reads them in any layout.
	kt->base.final_layout = VK_IMAGE_LAYOUT_GENERAL;
	kt->next = 0;
}

static bool
target_has_images(struct comp_target *ct)
{
	return ct->images != NULL;
}

static VkResult
target_acquire(struct comp_target *ct, uint32_t *out_index)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;

	assert(kt->acquired == -1);
	/*
	 * Three images: the one on screen, the one committed last (on screen
	 * from the next vblank) and this one. Each present waits for the
	 * previous commit to retire, so the image acquired here was replaced
	 * on screen before its turn comes again.
	 */
	kt->acquired = kt->next;
	kt->next = (kt->next + 1) % P4K_IMAGES;
	*out_index = (uint32_t)kt->acquired;
	return VK_SUCCESS;
}

static VkResult
target_present(struct comp_target *ct,
               VkQueue queue,
               uint32_t index,
               uint64_t timeline_semaphore_value,
               int64_t desired_present_time_ns,
               int64_t present_slop_ns)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;
	struct vk_bundle *vk = &ct->c->base.vk;

	assert((int64_t)index == kt->acquired);
	kt->acquired = -1;

	// The renderer submitted without a semaphore for us; scanout must follow its work.
	int64_t before_ns = os_monotonic_get_ns();
	vk_queue_lock(vk->main_queue);
	VkResult ret = vk->vkQueueWaitIdle(queue);
	vk_queue_unlock(vk->main_queue);
	if (ret != VK_SUCCESS) {
		return ret;
	}
	int64_t gpu_wait_ns = os_monotonic_get_ns() - before_ns;
	kt->stats.gpu_wait_ns += gpu_wait_ns;
	if (gpu_wait_ns > kt->stats.max_gpu_wait_ns) {
		kt->stats.max_gpu_wait_ns = gpu_wait_ns;
	}

	p4k_poll_vblank(kt);
	p4k_wait_retired(kt);
	if (!p4k_commit(kt, index)) {
		return VK_ERROR_SURFACE_LOST_KHR;
	}
	p4k_poll_vblank(kt);

	struct p4k_stats *s = &kt->stats;
	int64_t now_ns = os_monotonic_get_ns();
	if (s->commits > 0) {
		int64_t interval = now_ns - s->last_ns;
		if (interval > s->max_interval_ns) {
			s->max_interval_ns = interval;
		}
		if (interval * 2 > (int64_t)ct->c->settings.nominal_frame_interval_ns * 3) {
			s->late++;
		}
	} else {
		s->first_ns = now_ns;
	}
	s->last_ns = now_ns;
	s->commits++;

	const char *dump = debug_get_option_pico4_kms_dump();
	if (dump != NULL && s->commits == (uint64_t)debug_get_num_option_pico4_kms_dump_frame()) {
		p4k_dump(kt, index, dump);
	}
	return VK_SUCCESS;
}

static VkResult
target_wait_for_present(struct comp_target *ct, time_duration_ns timeout_ns)
{
	return VK_ERROR_EXTENSION_NOT_PRESENT;
}

static void
target_flush(struct comp_target *ct)
{
	// No-op
}

static void
target_calc_frame_pacing(struct comp_target *ct,
                         int64_t *out_frame_id,
                         int64_t *out_wake_up_time_ns,
                         int64_t *out_desired_present_time_ns,
                         int64_t *out_present_slop_ns,
                         int64_t *out_predicted_display_time_ns)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;
	int64_t predicted_display_period_ns = 0;
	int64_t min_display_period_ns = 0;

	u_pc_predict(kt->upc, os_monotonic_get_ns(), out_frame_id, out_wake_up_time_ns, out_desired_present_time_ns,
	             out_present_slop_ns, out_predicted_display_time_ns, &predicted_display_period_ns,
	             &min_display_period_ns);
}

static void
target_mark_timing_point(struct comp_target *ct, enum comp_target_timing_point point, int64_t frame_id, int64_t when_ns)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;

	switch (point) {
	case COMP_TARGET_TIMING_POINT_WAKE_UP: u_pc_mark_point(kt->upc, U_TIMING_POINT_WAKE_UP, frame_id, when_ns); break;
	case COMP_TARGET_TIMING_POINT_BEGIN: u_pc_mark_point(kt->upc, U_TIMING_POINT_BEGIN, frame_id, when_ns); break;
	case COMP_TARGET_TIMING_POINT_SUBMIT_BEGIN:
		u_pc_mark_point(kt->upc, U_TIMING_POINT_SUBMIT_BEGIN, frame_id, when_ns);
		break;
	case COMP_TARGET_TIMING_POINT_SUBMIT_END:
		u_pc_mark_point(kt->upc, U_TIMING_POINT_SUBMIT_END, frame_id, when_ns);
		break;
	default: assert(false);
	}
}

static VkResult
target_update_timings(struct comp_target *ct)
{
	return VK_SUCCESS;
}

static void
target_info_gpu(struct comp_target *ct, int64_t frame_id, int64_t gpu_start_ns, int64_t gpu_end_ns, int64_t when_ns)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;

	u_pc_info_gpu(kt->upc, frame_id, gpu_start_ns, gpu_end_ns, when_ns);
}

static void
target_set_title(struct comp_target *ct, const char *title)
{
	// No-op
}

static xrt_result_t
target_get_refresh_rates(struct comp_target *ct, uint32_t *out_count, float *out_rates)
{
	*out_count = 1;
	out_rates[0] = P4K_HZ;
	return XRT_SUCCESS;
}

static xrt_result_t
target_get_current_refresh_rate(struct comp_target *ct, float *out_rate)
{
	*out_rate = P4K_HZ;
	return XRT_SUCCESS;
}

static void
target_destroy(struct comp_target *ct)
{
	struct pico4_kms_target *kt = (struct pico4_kms_target *)ct;
	const char *stats = debug_get_option_pico4_kms_stats();

	if (kt->has_vulkan) {
		// The renderer's last frame must finish before its image is freed.
		struct vk_bundle *vk = &ct->c->base.vk;
		vk_queue_lock(vk->main_queue);
		vk->vkQueueWaitIdle(vk->main_queue->queue);
		vk_queue_unlock(vk->main_queue);
		p4k_free_images(kt);
	}
	if (stats != NULL && kt->fd >= 0) {
		p4k_write_stats(kt, stats);
	}
	p4k_gpu_floor_restore(kt);
	if (kt->fd >= 0) {
		COMP_INFO(ct->c, "pico4_kms: %" PRIu64 " commits, %" PRIu64 " vblanks, %" PRIu64 " late",
		          kt->stats.commits, kt->stats.vblanks, kt->stats.late);
		drmDropMaster(kt->fd);
		close(kt->fd);
	}
	u_pc_destroy(&kt->upc);
	free(kt);
}

static struct pico4_kms_target *
target_create(struct comp_compositor *c)
{
	struct pico4_kms_target *kt = U_TYPED_CALLOC(struct pico4_kms_target);

	kt->base.name = "pico4_kms";
	kt->base.init_pre_vulkan = target_init_pre_vulkan;
	kt->base.init_post_vulkan = target_init_post_vulkan;
	kt->base.check_ready = target_check_ready;
	kt->base.create_images = target_create_images;
	kt->base.has_images = target_has_images;
	kt->base.acquire = target_acquire;
	kt->base.present = target_present;
	kt->base.wait_for_present = target_wait_for_present;
	kt->base.flush = target_flush;
	kt->base.calc_frame_pacing = target_calc_frame_pacing;
	kt->base.mark_timing_point = target_mark_timing_point;
	kt->base.update_timings = target_update_timings;
	kt->base.info_gpu = target_info_gpu;
	kt->base.set_title = target_set_title;
	kt->base.get_refresh_rates = target_get_refresh_rates;
	kt->base.get_current_refresh_rate = target_get_current_refresh_rate;
	kt->base.destroy = target_destroy;
	kt->base.c = c;
	kt->base.wait_for_present_supported = false;

	kt->fd = -1;
	kt->release_fd = kt->retire_fd = -1;
	kt->acquired = -1;
	kt->shown = -1;
	kt->saved_min_pwrlevel = -1;
	kt->stats.eye_mean[0][0] = -1;
	for (uint32_t i = 0; i < P4K_IMAGES; i++) {
		kt->imgs[i].dmabuf = -1;
	}
	u_pc_fake_create(c->settings.nominal_frame_interval_ns, os_monotonic_get_ns(), &kt->upc);
	return kt;
}


/*
 *
 * Factory.
 *
 */

static bool
factory_detect(const struct comp_target_factory *ctf, struct comp_compositor *c)
{
	if (!debug_get_bool_option_pico4_kms()) {
		return false;
	}
	int fd = open(P4K_DEVICE, O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		return false;
	}
	bool found = false;
	if (p4k_is_display(fd)) {
		drmModeRes *res = drmModeGetResources(fd);
		uint32_t connector, crtcs;
		drmModeModeInfo mode;
		found = res != NULL && p4k_find_output(fd, res, &connector, &mode, &crtcs);
		drmModeFreeResources(res);
	}
	close(fd);
	return found;
}

static bool
factory_create_target(const struct comp_target_factory *ctf, struct comp_compositor *c, struct comp_target **out_ct)
{
	struct pico4_kms_target *kt = target_create(c);
	if (!p4k_open(kt)) {
		target_destroy(&kt->base);
		return false;
	}
	*out_ct = &kt->base;
	return true;
}

static const char *optional_device_extensions[] = {
    VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
};

const struct comp_target_factory comp_target_factory_pico4_kms = {
    .name = "PICO 4 KMS",
    .identifier = "pico4_kms",
    .requires_vulkan_for_create = false,
    .is_deferred = false,
    .required_instance_version = 0,
    .required_instance_extensions = NULL,
    .required_instance_extension_count = 0,
    .optional_device_extensions = optional_device_extensions,
    .optional_device_extension_count = ARRAY_SIZE(optional_device_extensions),
    .detect = factory_detect,
    .create_target = factory_create_target,
};
