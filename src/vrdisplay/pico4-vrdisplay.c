// SPDX-License-Identifier: BSL-1.0
/*
 * VK_LAYER_PICO4_vrdisplay: the PICO 4 panels as a Vulkan direct-mode display,
 * for SteamVR's compositor (docs/session.md).
 *
 * vrcompositor drives a headset through VK_KHR_display: it looks for the
 * display, acquires it (VK_EXT_acquire_drm_display), presents a swapchain on a
 * display-plane surface and paces itself with VK_EXT_display_control's vblank
 * events and counter. On the stock PICO 4 kernel Turnip's KGSL backend reports
 * no display (the GPU is not a DRM device), and msm_drm shows clean frames only
 * with the vendor fence contract and six colour-component layers (docs/graphics.md,
 * comp_window_pico4_kms.c in pico4-monado). This layer answers those calls for
 * one display, 4320x2160 at 90 Hz: swapchain images are linear dma-bufs imported
 * as framebuffers, a present thread commits them atomically with the release
 * and retire fences, and a vblank thread feeds display events and the counter.
 * The layer also emulates the OPAQUE_FD semaphores SteamVR shares between
 * processes, which the driver cannot export on this kernel (pico4-vrsync.c).
 * Every other call passes through.
 *
 * Environment:
 *   PICO4_VRDISPLAY=1            enables the layer (implicit layer manifest)
 *   PICO4_VRDISPLAY_FAKE=1       no panels: a 90 Hz timer stands in for vblanks
 *                                and flips, to test the handshake on any driver;
 *                                =WxH also sets the display's size
 *   PICO4_VRDISPLAY_STATS=path   presentation statistics as JSON per swapchain,
 *                                every ten seconds and when it is destroyed
 *   PICO4_VRDISPLAY_DUMP=path    the image of present PICO4_VRDISPLAY_DUMP_FRAME
 *                                (default 300) as PPM, downscaled by four; with
 *                                PICO4_VRDISPLAY_DUMP_EVERY=N also every N presents
 *                                after it, as path.COMMIT (compressed scanout)
 *   PICO4_VRDISPLAY_MOTION=path  per commit: commit time, the vblank it reaches
 *                                the panels at (CLOCK_MONOTONIC ns), how far the
 *                                image changed (mean luma difference of a sparse
 *                                grid against the previous commit) and its
 *                                horizontal shift in pixels (a band across the
 *                                left eye's centre); for latency measurements
 *                                (PICO4_LATENCY_PROBE_MS). With
 *                                PICO4_VRDISPLAY_MOTION_BAND=1 each line also
 *                                carries 16 bands over the left eye's height
 *                                (512 values each, every other pixel), in hex
 *   PICO4_VRDISPLAY_GPU_MIN_MHZ  GPU clock floor while presenting (587, 0: off)
 *   PICO4_VRDISPLAY_SYNC=off     no semaphore emulation
 *   PICO4_VRDISPLAY_UBWC=1       compressed (UBWC) scanout: the application renders
 *                                into tiled, compressed images without storage
 *                                usage, converted at present into compressed
 *                                images the planes read; the CPU cannot read
 *                                them: the dumped present copies the
 *                                application's image for it, motion data are off;
 *                                =2 the application renders into the compressed
 *                                images the planes read (R8G8B8A8 views), no conversion
 *   PICO4_VRDISPLAY_FRAMES_AHEAD=N  presents count as shown once the present N before
 *                                them is committed (0: when shown; at most 2): SteamVR,
 *                                which waits for each present to be shown before it
 *                                starts the next frame, keeps the GPU busy meanwhile
 *   PICO4_VRDISPLAY_PASS_BARRIER=1  a full memory barrier before every render pass
 *                                (missing overlay parts in the right eye without)
 *   PICO4_VRDISPLAY_IMAGES=1     log every image the application creates (size,
 *                                format, samples, tiling, usage, flags), to see
 *                                which ones lose tiling or compression
 *   PICO4_VRDISPLAY_LOG=debug    log every display call
 *   PICO4_VRDISPLAY_LOGFILE=path append the log there instead of stderr
 */
#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#include "pico4-vrsync.h"

#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>
#include <linux/dma-buf.h>

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <pthread.h>
#include <sched.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

#define LAYER_NAME "VK_LAYER_PICO4_vrdisplay"
#define EXPORT __attribute__((visibility("default")))

#define KMS_DEVICE "/dev/dri/card0"
#define KGSL_DIR "/sys/class/kgsl/kgsl-3d0/"
#define WIDTH 4320
#define HEIGHT 2160
#define EYE 2160
#define HZ 90
#define PERIOD_NS (1000000000LL / HZ)
#define LAYERS 6
#define STATE 14
#define MAX_IMAGES 4
#define NS_PER_S 1000000000LL
#define STATS_EVERY_NS (10 * NS_PER_S) //!< between statistics updates
#define STALL_NS (NS_PER_S / 10)        //!< a gap between commits counted as a stall

//! The display's size: the panels', or PICO4_VRDISPLAY_FAKE=WxH for tests.
static uint32_t g_width = WIDTH, g_height = HEIGHT;

/*
 *
 * Logging and small helpers.
 *
 */

static FILE *g_log;
static bool g_debug;
static pthread_once_t g_log_once = PTHREAD_ONCE_INIT;

static void
log_init(void)
{
	const char *file = getenv("PICO4_VRDISPLAY_LOGFILE");
	g_log = file != NULL ? fopen(file, "ae") : NULL;
	if (g_log == NULL) {
		g_log = stderr;
	}
	const char *level = getenv("PICO4_VRDISPLAY_LOG");
	g_debug = level != NULL && strcmp(level, "debug") == 0;
}

static void __attribute__((format(printf, 2, 3)))
say(bool debug, const char *fmt, ...)
{
	pthread_once(&g_log_once, log_init);
	if (debug && !g_debug) {
		return;
	}
	va_list ap;
	va_start(ap, fmt);
	flockfile(g_log);
	fprintf(g_log, "pico4-vrdisplay[%d]: ", getpid());
	vfprintf(g_log, fmt, ap);
	fputc('\n', g_log);
	funlockfile(g_log);
	fflush(g_log);
	va_end(ap);
}

#define LOG(...) say(false, __VA_ARGS__)
#define DBG(...) say(true, __VA_ARGS__)

void
vrdisplay_log(bool debug, const char *fmt, ...)
{
	pthread_once(&g_log_once, log_init);
	if (debug && !g_debug) {
		return;
	}
	char line[512];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	say(debug, "%s", line);
}

static int64_t
now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * NS_PER_S + ts.tv_nsec;
}

static long
env_num(const char *name, long fallback)
{
	const char *v = getenv(name);
	return v != NULL && *v != '\0' ? strtol(v, NULL, 10) : fallback;
}

static bool
fake_mode(void)
{
	return env_num("PICO4_VRDISPLAY_FAKE", 0) != 0;
}

//! Absolute CLOCK_MONOTONIC deadline for pthread_cond_timedwait (the condition uses it).
static struct timespec
deadline_after(uint64_t timeout_ns)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	uint64_t ns = timeout_ns > (uint64_t)INT64_MAX / 2 ? (uint64_t)INT64_MAX / 2 : timeout_ns;
	ns += (uint64_t)ts.tv_nsec;
	ts.tv_sec += (time_t)(ns / NS_PER_S);
	ts.tv_nsec = (long)(ns % NS_PER_S);
	return ts;
}

static inline void *
dispatch_key(const void *handle)
{
	return *(void *const *)handle;
}


/*
 *
 * Per-instance and per-device data.
 *
 */

struct instance_data
{
	struct instance_data *next;
	void *key;
	VkInstance instance;
	PFN_vkGetInstanceProcAddr gipa;
	PFN_vkDestroyInstance DestroyInstance;
	PFN_vkCreateDevice CreateDevice;
	PFN_vkEnumerateDeviceExtensionProperties EnumerateDeviceExtensionProperties;
	PFN_vkGetPhysicalDeviceProperties2 GetPhysicalDeviceProperties2;
	PFN_vkGetPhysicalDeviceMemoryProperties GetPhysicalDeviceMemoryProperties;
	PFN_vkGetPhysicalDeviceFormatProperties GetPhysicalDeviceFormatProperties;
	PFN_vkGetPhysicalDeviceImageFormatProperties2 GetPhysicalDeviceImageFormatProperties2;
	PFN_vkGetPhysicalDeviceQueueFamilyProperties GetPhysicalDeviceQueueFamilyProperties;
	PFN_vkDestroySurfaceKHR DestroySurfaceKHR;
	PFN_vkGetPhysicalDeviceSurfaceSupportKHR GetPhysicalDeviceSurfaceSupportKHR;
	PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR GetPhysicalDeviceSurfaceCapabilitiesKHR;
	PFN_vkGetPhysicalDeviceSurfaceCapabilities2KHR GetPhysicalDeviceSurfaceCapabilities2KHR;
	PFN_vkGetPhysicalDeviceSurfaceCapabilities2EXT GetPhysicalDeviceSurfaceCapabilities2EXT;
	PFN_vkGetPhysicalDeviceSurfaceFormatsKHR GetPhysicalDeviceSurfaceFormatsKHR;
	PFN_vkGetPhysicalDeviceSurfaceFormats2KHR GetPhysicalDeviceSurfaceFormats2KHR;
	PFN_vkGetPhysicalDeviceSurfacePresentModesKHR GetPhysicalDeviceSurfacePresentModesKHR;
	PFN_vkGetPhysicalDevicePresentRectanglesKHR GetPhysicalDevicePresentRectanglesKHR;
	PFN_vkReleaseDisplayEXT ReleaseDisplayEXT;
	PFN_vkVoidFunction AcquireXlibDisplayEXT;
};

struct device_data
{
	struct device_data *next;
	void *key;
	VkDevice device;
	VkPhysicalDevice physical;
	struct instance_data *inst;
	PFN_vkGetDeviceProcAddr gdpa;
	VkQueue queue; //!< First queue the application retrieved: fallback for signalling acquires.
	bool has_semaphore_fd, has_fence_fd;
	PFN_vkDestroyDevice DestroyDevice;
	PFN_vkGetDeviceQueue GetDeviceQueue;
	PFN_vkGetDeviceQueue2 GetDeviceQueue2;
	PFN_vkCreateImage CreateImage;
	PFN_vkDestroyImage DestroyImage;
	PFN_vkGetImageDrmFormatModifierPropertiesEXT GetImageDrmFormatModifierPropertiesEXT;
	PFN_vkCreateCommandPool CreateCommandPool;
	PFN_vkDestroyCommandPool DestroyCommandPool;
	PFN_vkAllocateCommandBuffers AllocateCommandBuffers;
	PFN_vkBeginCommandBuffer BeginCommandBuffer;
	PFN_vkEndCommandBuffer EndCommandBuffer;
	PFN_vkCmdPipelineBarrier CmdPipelineBarrier;
	PFN_vkCmdBlitImage CmdBlitImage;
	PFN_vkCmdBeginRenderPass CmdBeginRenderPass;
	PFN_vkCmdBeginRenderPass2 CmdBeginRenderPass2;
	PFN_vkCmdBeginRendering CmdBeginRendering;
	PFN_vkCreateImageView CreateImageView;
	PFN_vkCreateRenderPass CreateRenderPass;
	PFN_vkCreateRenderPass2 CreateRenderPass2;
	PFN_vkCmdCopyImageToBuffer CmdCopyImageToBuffer;
	PFN_vkCreateBuffer CreateBuffer;
	PFN_vkDestroyBuffer DestroyBuffer;
	PFN_vkGetBufferMemoryRequirements GetBufferMemoryRequirements;
	PFN_vkBindBufferMemory BindBufferMemory;
	PFN_vkGetImageMemoryRequirements GetImageMemoryRequirements;
	PFN_vkAllocateMemory AllocateMemory;
	PFN_vkFreeMemory FreeMemory;
	PFN_vkBindImageMemory BindImageMemory;
	PFN_vkMapMemory MapMemory;
	PFN_vkUnmapMemory UnmapMemory;
	PFN_vkGetMemoryFdKHR GetMemoryFdKHR;
	PFN_vkGetImageSubresourceLayout GetImageSubresourceLayout;
	PFN_vkCreateFence CreateFence;
	PFN_vkDestroyFence DestroyFence;
	PFN_vkResetFences ResetFences;
	PFN_vkWaitForFences WaitForFences;
	PFN_vkGetFenceStatus GetFenceStatus;
	PFN_vkQueueSubmit QueueSubmit;
	PFN_vkImportSemaphoreFdKHR ImportSemaphoreFdKHR;
	PFN_vkImportFenceFdKHR ImportFenceFdKHR;
	PFN_vkCreateSwapchainKHR CreateSwapchainKHR;
	PFN_vkDestroySwapchainKHR DestroySwapchainKHR;
	PFN_vkGetSwapchainImagesKHR GetSwapchainImagesKHR;
	PFN_vkAcquireNextImageKHR AcquireNextImageKHR;
	PFN_vkAcquireNextImage2KHR AcquireNextImage2KHR;
	PFN_vkQueuePresentKHR QueuePresentKHR;
	PFN_vkGetSwapchainStatusKHR GetSwapchainStatusKHR;
	PFN_vkReleaseSwapchainImagesEXT ReleaseSwapchainImagesEXT;
	PFN_vkWaitForPresentKHR WaitForPresentKHR;
	PFN_vkWaitForPresent2KHR WaitForPresent2KHR;
	PFN_vkSetHdrMetadataEXT SetHdrMetadataEXT;
	PFN_vkGetDeviceGroupSurfacePresentModesKHR GetDeviceGroupSurfacePresentModesKHR;
	PFN_vkCreateSharedSwapchainsKHR CreateSharedSwapchainsKHR;
	PFN_vkGetSwapchainCounterEXT GetSwapchainCounterEXT;
	PFN_vkRegisterDisplayEventEXT RegisterDisplayEventEXT;
	PFN_vkRegisterDeviceEventEXT RegisterDeviceEventEXT;
	PFN_vkDisplayPowerControlEXT DisplayPowerControlEXT;
};

static pthread_mutex_t g_objects = PTHREAD_MUTEX_INITIALIZER;
static struct instance_data *g_instances;
static struct device_data *g_devices;

static struct instance_data *
find_instance(const void *handle)
{
	void *key = dispatch_key(handle);
	pthread_mutex_lock(&g_objects);
	struct instance_data *d = g_instances;
	while (d != NULL && d->key != key) {
		d = d->next;
	}
	pthread_mutex_unlock(&g_objects);
	return d;
}

static struct device_data *
find_device(const void *handle)
{
	void *key = dispatch_key(handle);
	pthread_mutex_lock(&g_objects);
	struct device_data *d = g_devices;
	while (d != NULL && d->key != key) {
		d = d->next;
	}
	pthread_mutex_unlock(&g_objects);
	return d;
}


/*
 *
 * The display: msm_drm's DSI connector with the stock six-layer arrangement.
 *
 */

struct kms_setting
{
	uint32_t property;
	uint64_t value;
};

struct event_fence
{
	struct event_fence *next;
	VkDevice device;
	VkFence fence;
	bool vblank;   //!< Signals at the next vblank; otherwise never (hotplug).
	bool signaled;
};

struct swapchain;

struct display
{
	pthread_mutex_t lock;
	pthread_cond_t cond; //!< vblanks, flips, acquires and event fences
	bool probed, present, fake;

	int fd; //!< KMS fd used for commits (dup of the acquiring fd), -1 if none
	bool kms_ready;
	uint32_t connector, crtc;
	unsigned crtc_index;
	drmModeModeInfo mode;
	uint32_t planes[LAYERS];
	uint32_t fb_property[LAYERS];
	struct kms_setting state[LAYERS][STATE];
	uint32_t release_property, retire_property;
	bool xrgb, xbgr;
	bool active; //!< Mode set and layers enabled.
	int saved_min_pwrlevel;

	pthread_t vblank_thread, clock_thread;
	bool vblank_started, clock_started, vblank_stop, vblank_pending;
	uint64_t vblank_count, vblank_sequence; //!< As the event fences and the counter see them.
	int64_t vblank_ns;
	int64_t phase_ns;          //!< The clock's vblank time (kernel timestamps), 0 before one.
	uint64_t phase_sequence;   //!< Its sequence.
	int64_t lead_ns;           //!< The clock signals vblanks this much before they happen.
	uint64_t ticked_sequence;  //!< The newest vblank the clock signalled.
	double first_vblank, last_vblank;
	uint64_t first_sequence, last_sequence;
	uint64_t stat_vblanks;
	//! Kernel vblank intervals of one refresh, and those on the 10 ms scheduler tick.
	uint64_t stat_intervals, stat_tick_intervals;
	uint64_t logged_intervals, logged_tick_intervals;

	struct event_fence *events;
	struct swapchain *swapchains;
	int surfaces;
};

static struct display g_display = {
    .lock = PTHREAD_MUTEX_INITIALIZER,
    .fd = -1,
    .saved_min_pwrlevel = -1,
};

/*
 * The present timeline for PICO4_VRDISPLAY_TRACE=path: one line per event (kind, time in ns,
 * present id, argument), written with the statistics. Q present queued (image index), G its
 * rendering done (wait in us), C committed, R retire fence signalled (the present shown), S a
 * later present counted as shown at a commit (PICO4_VRDISPLAY_FRAMES_AHEAD), W and w
 * vkWaitForPresentKHR begins (timeout in us) and returns (VkResult), V vblank tick (sequence),
 * K kernel vblank event (its delay in us), A image acquired (index).
 */
struct trace_event
{
	int64_t t;
	uint64_t id;
	int32_t arg;
	char kind;
};
enum { TRACE_EVENTS = 1 << 16 };
static struct trace_event g_trace[TRACE_EVENTS];
static uint64_t g_trace_n, g_trace_written;
static bool g_trace_on;

static int64_t
now_ns(void);

//! Locked.
static void
trace(char kind, uint64_t id, int64_t arg)
{
	if (!g_trace_on) {
		return;
	}
	struct trace_event *e = &g_trace[g_trace_n++ % TRACE_EVENTS];
	e->t = now_ns();
	e->id = id;
	e->arg = arg > INT32_MAX ? INT32_MAX : arg < INT32_MIN ? INT32_MIN : (int32_t)arg;
	e->kind = kind;
}

//! Not locked: copies the new events under the lock, then writes them.
static void
trace_flush(FILE *f)
{
	static struct trace_event copy[TRACE_EVENTS];
	pthread_mutex_lock(&g_display.lock);
	uint64_t from = g_trace_written, to = g_trace_n;
	if (to - from > TRACE_EVENTS) {
		from = to - TRACE_EVENTS; // overwritten meanwhile
	}
	for (uint64_t i = from; i < to; i++) {
		copy[i - from] = g_trace[i % TRACE_EVENTS];
	}
	g_trace_written = to;
	pthread_mutex_unlock(&g_display.lock);
	for (uint64_t i = 0; i < to - from; i++) {
		fprintf(f, "%c,%" PRId64 ",%" PRIu64 ",%d\n", copy[i].kind, copy[i].t, copy[i].id, copy[i].arg);
	}
	fflush(f);
}

// Handles: the addresses of these objects.
static int g_display_mode_tag;
#define DISPLAY_HANDLE ((VkDisplayKHR)(uintptr_t)&g_display)
#define MODE_HANDLE ((VkDisplayModeKHR)(uintptr_t)&g_display_mode_tag)

static uint32_t
kms_property(int fd, uint32_t object, uint32_t type, const char *name, uint64_t *out_value)
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
kms_enum(int fd, uint32_t plane, const char *name, const char *option, uint64_t *out_value)
{
	drmModePropertyRes *p = drmModeGetProperty(fd, kms_property(fd, plane, DRM_MODE_OBJECT_PLANE, name, NULL));
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
kms_add(drmModeAtomicReq *req, int fd, uint32_t object, uint32_t type, const char *name, uint64_t value)
{
	uint32_t id = kms_property(fd, object, type, name, NULL);
	return id != 0 && drmModeAtomicAddProperty(req, object, id, value) >= 0;
}

static bool
kms_is_msm(int fd)
{
	drmVersion *version = drmGetVersion(fd);
	bool msm = version != NULL && strcmp(version->name, "msm_drm") == 0;
	drmFreeVersion(version);
	return msm;
}

//! The connected DSI connector and its native mode at HZ.
static bool
kms_find_output(int fd, uint32_t *out_connector, drmModeModeInfo *out_mode, uint32_t *out_crtc, unsigned *out_index)
{
	drmModeRes *res = drmModeGetResources(fd);
	if (res == NULL) {
		return false;
	}
	char prefix[32];
	snprintf(prefix, sizeof(prefix), "%dx%dx%dx", WIDTH, HEIGHT, HZ);
	bool found = false;
	uint32_t crtcs = 0;
	for (int i = 0; i < res->count_connectors && !found; i++) {
		drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
		if (c == NULL) {
			continue;
		}
		if (c->connector_type == DRM_MODE_CONNECTOR_DSI && c->connection == DRM_MODE_CONNECTED) {
			for (int m = 0; m < c->count_modes && !found; m++) {
				const drmModeModeInfo *mode = &c->modes[m];
				if (mode->hdisplay == WIDTH && mode->vdisplay == HEIGHT &&
				    strncmp(mode->name, prefix, strlen(prefix)) == 0) {
					*out_connector = c->connector_id;
					*out_mode = *mode;
					found = true;
				}
			}
			for (int e = 0; e < c->count_encoders && found; e++) {
				drmModeEncoder *enc = drmModeGetEncoder(fd, c->encoders[e]);
				if (enc != NULL) {
					crtcs |= enc->possible_crtcs;
					drmModeFreeEncoder(enc);
				}
			}
		}
		drmModeFreeConnector(c);
	}
	unsigned index = 0;
	while (found && index < (unsigned)res->count_crtcs && (crtcs & (1u << index)) == 0) {
		index++;
	}
	found = found && index < (unsigned)res->count_crtcs;
	if (found) {
		*out_crtc = res->crtcs[index];
		*out_index = index;
	}
	drmModeFreeResources(res);
	return found;
}

static bool
kms_plane_has_format(drmModePlane *p, uint32_t fourcc)
{
	for (uint32_t i = 0; i < p->count_formats; i++) {
		if (p->formats[i] == fourcc) {
			return true;
		}
	}
	return false;
}

/*!
 * The stock layer arrangement (as in pico4-monado's KMS target): four VIG
 * (scaler) and two DMA layers, one per colour component and eye, blended per
 * component. Destination coordinates are eye-local; the right eye reads the
 * right half of the shared image.
 */
static bool
kms_setup_layers(struct display *d)
{
	int fd = d->fd;
	static const int eye[LAYERS] = {1, 0, 1, 0, 1, 0};
	static const int stage[LAYERS] = {3, 2, 1, 0, 5, 4};
	static const char *color[LAYERS] = {"blue", "blue", "red", "red", "green", "green"};

	drmModePlaneRes *pr = drmModeGetPlaneResources(fd);
	if (pr == NULL) {
		LOG("no plane resources: %s", strerror(errno));
		return false;
	}
	unsigned nvig = 0, ndma = 0;
	d->xrgb = d->xbgr = true;
	for (uint32_t i = 0; i < pr->count_planes && nvig + ndma < LAYERS; i++) {
		drmModePlane *p = drmModeGetPlane(fd, pr->planes[i]);
		if (p == NULL) {
			continue;
		}
		uint64_t type = 0;
		kms_property(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "type", &type);
		bool usable = kms_plane_has_format(p, DRM_FORMAT_XRGB8888) && type != DRM_PLANE_TYPE_CURSOR &&
		              (p->possible_crtcs & (1u << d->crtc_index)) != 0;
		bool scaler = kms_property(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "scaler_v2", NULL) != 0;
		bool multirect = kms_property(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "multirect_mode", NULL) != 0;
		int slot = -1;
		if (usable && scaler && nvig < 4) {
			slot = (int)nvig++;
		} else if (usable && !scaler && !multirect && type == DRM_PLANE_TYPE_OVERLAY && ndma < 2) {
			slot = 4 + (int)ndma++;
		}
		if (slot >= 0) {
			d->planes[slot] = p->plane_id;
			d->xrgb = d->xrgb && kms_plane_has_format(p, DRM_FORMAT_XRGB8888);
			d->xbgr = d->xbgr && kms_plane_has_format(p, DRM_FORMAT_XBGR8888);
		}
		drmModeFreePlane(p);
	}
	drmModeFreePlaneResources(pr);
	if (nvig != 4 || ndma != 2) {
		LOG("found %u VIG and %u DMA layers, need 4 and 2", nvig, ndma);
		return false;
	}

	static const char *names[STATE] = {
	    "CRTC_ID", "sspp_layout", "layer_color_component", "blend_op", "rotation", "zpos", "CRTC_X",
	    "CRTC_Y",  "CRTC_W",      "CRTC_H",                "SRC_X",    "SRC_Y",    "SRC_W", "SRC_H",
	};
	for (unsigned i = 0; i < LAYERS; i++) {
		uint32_t p = d->planes[i];
		uint64_t layout, component, blend;
		if (!kms_enum(fd, p, "sspp_layout", eye[i] ? "right" : "left", &layout) ||
		    !kms_enum(fd, p, "layer_color_component", color[i], &component) ||
		    !kms_enum(fd, p, "blend_op", "layer_color", &blend)) {
			LOG("plane %u lacks the stock layer properties", p);
			return false;
		}
		uint64_t values[STATE] = {
		    d->crtc, layout, component, blend, DRM_MODE_ROTATE_0, (uint64_t)stage[i], 0, 0, EYE, EYE,
		    (uint64_t)eye[i] * EYE << 16, 0, (uint64_t)EYE << 16, (uint64_t)EYE << 16,
		};
		for (unsigned j = 0; j < STATE; j++) {
			d->state[i][j].property = kms_property(fd, p, DRM_MODE_OBJECT_PLANE, names[j], NULL);
			d->state[i][j].value = values[j];
			if (d->state[i][j].property == 0) {
				LOG("plane %u has no %s", p, names[j]);
				return false;
			}
		}
		d->fb_property[i] = kms_property(fd, p, DRM_MODE_OBJECT_PLANE, "FB_ID", NULL);
		if (d->fb_property[i] == 0) {
			return false;
		}
	}
	return true;
}

/*!
 * The KMS fd for commits. The application normally hands its own fd over with
 * vkAcquireDrmDisplayEXT (as the first opener it is DRM master). Without that,
 * look for a master fd of the device in this process, else open one.
 */
static int
kms_find_master_fd(void)
{
	DIR *dir = opendir("/proc/self/fd");
	if (dir == NULL) {
		return -1;
	}
	struct stat want;
	int found = -1;
	if (stat(KMS_DEVICE, &want) == 0) {
		struct dirent *de;
		while (found < 0 && (de = readdir(dir)) != NULL) {
			int fd = atoi(de->d_name);
			struct stat st;
			if (fd <= 2 || fd == dirfd(dir) || fstat(fd, &st) != 0 || !S_ISCHR(st.st_mode) ||
			    st.st_rdev != want.st_rdev) {
				continue;
			}
			if (drmIsMaster(fd)) {
				found = fcntl(fd, F_DUPFD_CLOEXEC, 3);
			}
		}
	}
	closedir(dir);
	return found;
}

//! Locked. Sets up commits on @p fd (taken over); false if it is not the PICO 4 display.
static bool
kms_take_fd(struct display *d, int fd)
{
	if (d->kms_ready) {
		close(fd);
		return true;
	}
	if (!kms_is_msm(fd)) {
		LOG("%s is not the stock msm_drm device", KMS_DEVICE);
		close(fd);
		return false;
	}
	if (drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) != 0 ||
	    drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1) != 0) {
		LOG("atomic modesetting unavailable: %s", strerror(errno));
		close(fd);
		return false;
	}
	if (!drmIsMaster(fd) && drmSetMaster(fd) != 0) {
		LOG("not DRM master (another compositor or the Monado service holds the display?): %s",
		    strerror(errno));
	}
	d->fd = fd;
	bool ok = kms_find_output(fd, &d->connector, &d->mode, &d->crtc, &d->crtc_index) && kms_setup_layers(d);
	if (ok) {
		d->release_property = kms_property(fd, d->crtc, DRM_MODE_OBJECT_CRTC, "output_fence", NULL);
		d->retire_property = kms_property(fd, d->connector, DRM_MODE_OBJECT_CONNECTOR, "RETIRE_FENCE", NULL);
		ok = d->release_property != 0 && d->retire_property != 0;
		if (!ok) {
			LOG("the vendor release/retire fence properties are missing");
		}
	}
	if (!ok) {
		LOG("no PICO 4 panels on %s", KMS_DEVICE);
		close(fd);
		d->fd = -1;
		return false;
	}
	d->kms_ready = true;
	LOG("connector %u, CRTC %u, mode %s, layers %u %u %u %u %u %u, master %d", d->connector, d->crtc,
	    d->mode.name, d->planes[0], d->planes[1], d->planes[2], d->planes[3], d->planes[4], d->planes[5],
	    drmIsMaster(fd));
	return true;
}

//! Locked. KMS for commits, from whatever fd is available.
static bool
kms_ensure(struct display *d)
{
	if (d->fake || d->kms_ready) {
		return true;
	}
	int fd = kms_find_master_fd();
	if (fd < 0) {
		fd = open(KMS_DEVICE, O_RDWR | O_CLOEXEC);
	}
	return fd >= 0 && kms_take_fd(d, fd);
}

/*!
 * Locked. Does this system have the PICO 4 panels? Probes without keeping the
 * device open: the first opener of a DRM device becomes its master, and the
 * application must stay that.
 */
static bool
display_probe(struct display *d)
{
	if (d->probed) {
		return d->present;
	}
	d->probed = true;
	d->fake = fake_mode();
	if (d->fake) {
		unsigned w, h;
		if (sscanf(getenv("PICO4_VRDISPLAY_FAKE"), "%ux%u", &w, &h) == 2 && w >= 2 && h >= 1) {
			g_width = w & ~1u;
			g_height = h;
		}
		d->present = true;
		d->xrgb = d->xbgr = true;
		LOG("fake display: %ux%u at %d Hz, no panels", g_width, g_height, HZ);
		return true;
	}
	int fd = open(KMS_DEVICE, O_RDWR | O_CLOEXEC);
	if (fd >= 0) {
		uint32_t connector, crtc;
		unsigned index;
		drmModeModeInfo mode;
		d->present = kms_is_msm(fd) && kms_find_output(fd, &connector, &mode, &crtc, &index);
		close(fd);
	}
	LOG("%s", d->present ? "PICO 4 panels found" : "no PICO 4 panels: the layer stays out of the way");
	return d->present;
}

static bool
display_present(void)
{
	pthread_mutex_lock(&g_display.lock);
	bool present = display_probe(&g_display);
	pthread_mutex_unlock(&g_display.lock);
	return present;
}

/*
 * GPU clock floor (as in pico4-monado's KMS target): the stock governor stays
 * at 305 MHz under a compositor's load, where a frame takes longer than a
 * refresh period. KGSL power levels index gpu_available_frequencies, fastest
 * first: pick the slowest level at or above the wanted clock as min_pwrlevel.
 */
static bool
read_text(const char *path, char *buf, size_t size)
{
	FILE *f = fopen(path, "re");
	if (f == NULL) {
		return false;
	}
	size_t n = fread(buf, 1, size - 1, f);
	fclose(f);
	buf[n] = '\0';
	return n > 0;
}

static bool
write_int(const char *path, int value)
{
	FILE *f = fopen(path, "we");
	if (f == NULL) {
		return false;
	}
	bool ok = fprintf(f, "%d\n", value) > 0;
	return fclose(f) == 0 && ok;
}

static void
gpu_floor(struct display *d)
{
	long mhz = env_num("PICO4_VRDISPLAY_GPU_MIN_MHZ", 587);
	char freqs[256], current[16];
	if (mhz <= 0 || !read_text(KGSL_DIR "gpu_available_frequencies", freqs, sizeof(freqs)) ||
	    !read_text(KGSL_DIR "min_pwrlevel", current, sizeof(current))) {
		return;
	}
	int level = -1, index = 0;
	char *save = NULL;
	for (char *tok = strtok_r(freqs, " \n", &save); tok != NULL; tok = strtok_r(NULL, " \n", &save), index++) {
		if (strtoll(tok, NULL, 10) >= mhz * 1000000LL) {
			level = index;
		}
	}
	int old = atoi(current);
	if (level < 0) {
		return;
	}
	/*
	 * Restored to KGSL's default level, not to the one found: a compositor that crashed
	 * leaves its floor behind, and the GPU would stay fast after every later session.
	 */
	char stock[16];
	int restore = read_text(KGSL_DIR "default_pwrlevel", stock, sizeof(stock)) ? atoi(stock) : old;
	if (level < old && !write_int(KGSL_DIR "min_pwrlevel", level)) {
		LOG("cannot raise the GPU clock floor: %s", strerror(errno));
		return;
	}
	d->saved_min_pwrlevel = restore > level ? restore : old;
	LOG("GPU power level floor %d -> %d (%ld MHz or faster), %d afterwards", old, level < old ? level : old, mhz,
	    d->saved_min_pwrlevel);
}

static void
gpu_floor_restore(struct display *d)
{
	if (d->saved_min_pwrlevel >= 0) {
		write_int(KGSL_DIR "min_pwrlevel", d->saved_min_pwrlevel);
		d->saved_min_pwrlevel = -1;
	}
}

/*!
 * One commit with @p fb on all six layers. The first sets the mode and blocks;
 * later ones flip without blocking and return the release and retire fences.
 */
static bool
kms_commit(struct display *d, uint32_t fb, int *out_release, int *out_retire, uint64_t *busy_retries)
{
	uint64_t release_fd = UINT64_MAX, retire_fd = UINT64_MAX;
	bool modeset = !d->active;
	uint32_t blob = 0;

	drmModeAtomicReq *req = drmModeAtomicAlloc();
	bool ok = req != NULL;
	if (ok && modeset) {
		ok = drmModeCreatePropertyBlob(d->fd, &d->mode, sizeof(d->mode), &blob) == 0 &&
		     kms_add(req, d->fd, d->connector, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", d->crtc) &&
		     kms_add(req, d->fd, d->crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID", blob) &&
		     kms_add(req, d->fd, d->crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE", 1);
		for (unsigned i = 0; ok && i < LAYERS; i++) {
			for (unsigned j = 0; ok && j < STATE; j++) {
				ok = drmModeAtomicAddProperty(req, d->planes[i], d->state[i][j].property,
				                              d->state[i][j].value) >= 0;
			}
		}
	} else if (ok) {
		// The vendor fence properties return 64-bit fds through user pointers.
		ok = drmModeAtomicAddProperty(req, d->crtc, d->release_property, (uint64_t)(uintptr_t)&release_fd) >= 0 &&
		     drmModeAtomicAddProperty(req, d->connector, d->retire_property, (uint64_t)(uintptr_t)&retire_fd) >=
		         0;
	}
	for (unsigned i = 0; ok && i < LAYERS; i++) {
		ok = drmModeAtomicAddProperty(req, d->planes[i], d->fb_property[i], fb) >= 0;
	}
	int ret = -1;
	if (ok) {
		uint32_t flags = modeset ? DRM_MODE_ATOMIC_ALLOW_MODESET : DRM_MODE_ATOMIC_NONBLOCK;
		for (int attempt = 0; attempt < 20; attempt++) {
			ret = drmModeAtomicCommit(d->fd, req, flags, NULL);
			if (ret == 0 || errno != EBUSY) {
				break;
			}
			(*busy_retries)++;
			usleep(1000);
		}
	}
	int err = errno;
	drmModeAtomicFree(req);
	if (blob != 0) {
		drmModeDestroyPropertyBlob(d->fd, blob);
	}
	if (!ok || ret != 0) {
		LOG("%s commit failed: %s", modeset ? "mode set" : "flip", strerror(err));
		return false;
	}
	if (!modeset && (release_fd > INT_MAX || retire_fd > INT_MAX)) {
		LOG("the driver returned no display fences");
		return false;
	}
	*out_release = modeset ? -1 : (int)release_fd;
	*out_retire = modeset ? -1 : (int)retire_fd;
	if (modeset) {
		LOG("%s active", d->mode.name);
		gpu_floor(d);
	}
	d->active = true;
	return true;
}

static void
kms_disable(struct display *d)
{
	if (!d->active || d->fake) {
		d->active = false;
		return;
	}
	drmModeAtomicReq *req = drmModeAtomicAlloc();
	bool ok = req != NULL;
	for (unsigned i = 0; ok && i < LAYERS; i++) {
		ok = kms_add(req, d->fd, d->planes[i], DRM_MODE_OBJECT_PLANE, "FB_ID", 0) &&
		     kms_add(req, d->fd, d->planes[i], DRM_MODE_OBJECT_PLANE, "CRTC_ID", 0);
	}
	ok = ok && kms_add(req, d->fd, d->connector, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", 0) &&
	     kms_add(req, d->fd, d->crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID", 0) &&
	     kms_add(req, d->fd, d->crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE", 0);
	if (!ok || drmModeAtomicCommit(d->fd, req, DRM_MODE_ATOMIC_ALLOW_MODESET, NULL) != 0) {
		LOG("disabling the display failed: %s", strerror(errno));
	}
	drmModeAtomicFree(req);
	d->active = false;
	gpu_floor_restore(d);
}


/*
 *
 * Vblanks: display events, the swapchain counter and fake flips.
 *
 */

#define CLOCK_HOLD_NS (250 * 1000 * 1000LL) //!< Ticks continue this long after the newest event.
#define PHASE_SLEW_NS (20 * 1000LL)          //!< Most a late kernel timestamp moves the phase.

//! Locked. Vblank @p sequence has begun: its event fences signal and the counter moves.
static void
vblank_tick(struct display *d, uint64_t sequence)
{
	if (d->vblank_count == 0 || sequence > d->vblank_sequence) {
		d->vblank_sequence = sequence;
	}
	d->vblank_count++;
	trace('V', 0, (int64_t)sequence);
	for (struct event_fence *e = d->events; e != NULL; e = e->next) {
		if (e->vblank) {
			e->signaled = true;
		}
	}
	pthread_cond_broadcast(&d->cond);
}

//! Locked. A vblank as the kernel timestamped it: statistics and the clock's phase.
static void
on_vblank(struct display *d, uint64_t sequence, int64_t when_ns)
{
	double t = when_ns / 1e9;
	if (d->stat_vblanks == 0) {
		d->first_vblank = t;
		d->first_sequence = sequence;
	}
	/*
	 * In some sessions the kernel handles the display interrupt only on the 100 Hz
	 * scheduler tick: the events of a 90 Hz panel then come 10 ms apart, every ninth 20 ms.
	 */
	if (d->stat_vblanks > 0 && sequence == d->last_sequence + 1) {
		int64_t interval = when_ns - d->vblank_ns;
		d->stat_intervals++;
		if (interval > 9600000 && interval < 10400000) {
			d->stat_tick_intervals++;
		}
	}
	d->last_vblank = t;
	d->last_sequence = sequence;
	d->stat_vblanks++;
	d->vblank_ns = when_ns;
	trace('K', sequence, (now_ns() - when_ns) / 1000);
	if (d->phase_ns == 0 || when_ns - d->phase_ns > CLOCK_HOLD_NS) {
		// The first event, or the first after the display was off: the kernel's count.
		d->phase_ns = when_ns;
		d->phase_sequence = sequence;
	} else {
		/*
		 * The kernel timestamps a vblank when it handles the interrupt: now and then
		 * 2-3 ms late, for minutes in some sessions. A late stamp moves the phase only
		 * by PHASE_SLEW_NS, an earlier one at once; refreshes are counted by time.
		 */
		int64_t n = (when_ns - d->phase_ns + PERIOD_NS / 2) / PERIOD_NS;
		int64_t err = when_ns - (d->phase_ns + n * PERIOD_NS);
		d->phase_ns += n * PERIOD_NS + (err > PHASE_SLEW_NS ? PHASE_SLEW_NS : err);
		d->phase_sequence += (uint64_t)n;
	}
	if (d->fake) {
		vblank_tick(d, sequence); // the fake vblanks come from a timer already
	} else {
		pthread_cond_broadcast(&d->cond);
	}
}

/*
 * SteamVR takes the moment a vblank event fence signals as the time of that vblank. The
 * kernel's vblank events reach the vblank thread late whenever it waits for the lock or for
 * the CPU (up to 120 ms seen), and catching up then signals several vblanks at once. So the
 * fences follow a clock instead: locked to the kernel's timestamps and the refresh period,
 * at real-time priority. Late events only correct the phase.
 */

static void *
clock_main(void *arg)
{
	struct display *d = arg;
	struct sched_param param = {.sched_priority = 50};
	int err = pthread_setschedparam(pthread_self(), SCHED_FIFO, &param);
	if (err != 0) {
		LOG("vblank clock without real-time priority: %s", strerror(err));
	}
	pthread_mutex_lock(&d->lock);
	while (!d->vblank_stop) {
		// With a lead, each vblank is signalled lead_ns early: the clock runs that much ahead.
		int64_t now = now_ns() + d->lead_ns;
		if (d->phase_ns == 0 || now - d->phase_ns > CLOCK_HOLD_NS) {
			// No recent kernel vblank: the display is off or starting.
			struct timespec wait = deadline_after(PERIOD_NS);
			pthread_cond_timedwait(&d->cond, &d->lock, &wait);
			continue;
		}
		int64_t k = now >= d->phase_ns ? (now - d->phase_ns) / PERIOD_NS : 0;
		uint64_t latest = d->phase_sequence + (uint64_t)k;
		if (d->ticked_sequence > latest + 1) {
			d->ticked_sequence = latest; // a new mode restarted the kernel's count
		}
		if (d->ticked_sequence < latest) {
			d->ticked_sequence = latest;
			vblank_tick(d, latest);
			continue;
		}
		int64_t target = d->phase_ns + (k + 1) * PERIOD_NS - d->lead_ns;
		pthread_mutex_unlock(&d->lock);
		struct timespec at = {.tv_sec = target / NS_PER_S, .tv_nsec = target % NS_PER_S};
		clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &at, NULL);
		pthread_mutex_lock(&d->lock);
	}
	pthread_mutex_unlock(&d->lock);
	return NULL;
}

static void
vblank_handler(int fd, unsigned sequence, unsigned sec, unsigned usec, void *data)
{
	struct display *d = data;
	(void)fd;
	// DRM timestamps vblank events on CLOCK_MONOTONIC.
	int64_t when_ns = (int64_t)sec * NS_PER_S + (int64_t)usec * 1000;
	DBG("vblank %u at %.3f ms, delivered %.3f ms later, %.3f ms after the previous", sequence,
	    when_ns / 1e6, (now_ns() - when_ns) / 1e6, (when_ns - d->vblank_ns) / 1e6);
	on_vblank(d, sequence, when_ns);
	d->vblank_pending = false;
}

static void *
vblank_main(void *arg)
{
	struct display *d = arg;
	int timer = -1;
	if (d->fake) {
		timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
		struct itimerspec its = {.it_interval = {0, PERIOD_NS}, .it_value = {0, PERIOD_NS}};
		timerfd_settime(timer, 0, &its, NULL);
	}
	uint64_t fake_sequence = 0;
	pthread_mutex_lock(&d->lock);
	while (!d->vblank_stop) {
		if (d->fake) {
			pthread_mutex_unlock(&d->lock);
			uint64_t ticks = 0;
			ssize_t n = read(timer, &ticks, sizeof(ticks));
			pthread_mutex_lock(&d->lock);
			if (n == sizeof(ticks)) {
				fake_sequence += ticks;
				on_vblank(d, fake_sequence, now_ns());
			}
			continue;
		}
		/*
		 * Keep one vblank event queued, as pico4-kms-test does: this vendor
		 * driver holds no vblank reference for commits on its own, and events
		 * only come while the CRTC is active.
		 */
		if (d->active && !d->vblank_pending) {
			drmVBlank request = {0};
			request.request.type = DRM_VBLANK_RELATIVE | DRM_VBLANK_EVENT |
			                       ((d->crtc_index << DRM_VBLANK_HIGH_CRTC_SHIFT) & DRM_VBLANK_HIGH_CRTC_MASK);
			request.request.sequence = 1;
			request.request.signal = (unsigned long)d;
			d->vblank_pending = drmWaitVBlank(d->fd, &request) == 0;
		}
		int fd = d->fd;
		bool waiting = d->vblank_pending;
		pthread_mutex_unlock(&d->lock);
		struct pollfd p = {.fd = fd, .events = POLLIN};
		if (!waiting || poll(&p, 1, 100) <= 0) {
			if (!waiting) {
				usleep(20000);
			}
			pthread_mutex_lock(&d->lock);
			continue;
		}
		pthread_mutex_lock(&d->lock);
		drmEventContext ctx = {.version = 2, .vblank_handler = vblank_handler};
		drmHandleEvent(fd, &ctx);
	}
	pthread_mutex_unlock(&d->lock);
	if (timer >= 0) {
		close(timer);
	}
	return NULL;
}

//! Locked.
static void
vblank_start(struct display *d)
{
	if (d->vblank_started) {
		return;
	}
	pthread_condattr_t attr;
	pthread_condattr_init(&attr);
	pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
	pthread_cond_init(&d->cond, &attr);
	pthread_condattr_destroy(&attr);
	d->vblank_stop = false;
	/*
	 * SteamVR starts its compositor a fixed few milliseconds before the vblank it sees
	 * (its running start); a compositor frame that needs longer misses the vblank and
	 * shows one refresh later. A lead signals vblanks early so that the frame starts early.
	 */
	d->lead_ns = env_num("PICO4_VRDISPLAY_VSYNC_LEAD_US", 0) * 1000;
	d->vblank_started = pthread_create(&d->vblank_thread, NULL, vblank_main, d) == 0;
	d->clock_started = !d->fake && pthread_create(&d->clock_thread, NULL, clock_main, d) == 0;
}


/*
 *
 * Swapchains: images, acquire, present thread.
 *
 */

enum image_state
{
	IMAGE_FREE,
	IMAGE_ACQUIRED,
	IMAGE_QUEUED,  //!< Presented; waiting for the GPU.
	IMAGE_PENDING, //!< Committed; on screen from the next vblank.
	IMAGE_SHOWN,
};

struct sc_image
{
	VkImage image;
	VkDeviceMemory memory;
	VkFence fence; //!< Signalled when the present's wait semaphores are.
	int dmabuf;
	uint32_t gem, fb;
	VkDeviceSize offset, pitch, size;
	uint64_t modifier; //!< DRM_FORMAT_MOD_LINEAR or, with PICO4_VRDISPLAY_UBWC, QCOM_COMPRESSED
	// Compressed scanout only: the image the planes read, and the conversion into it.
	VkImage scan;
	VkDeviceMemory scan_memory;
	VkCommandBuffer copy;
	VkCommandBuffer copy_dump; //!< the conversion plus a copy into the readback buffer
	bool readback;             //!< this present carries the dump's copy
	bool direct;               //!< the application renders into the scanout image (image == scan)
	bool mappable;
	enum image_state state;
	uint64_t present_id;
};

struct sc_stats
{
	uint64_t presents, commits, busy_retries, retire_waits, retire_timeouts, late, stalls, gpu_timeouts;
	uint64_t present_waits, present_wait_timeouts, max_present_wait_lag;
	int64_t first_ns, last_ns, max_interval_ns;
	int64_t gpu_wait_ns, max_gpu_wait_ns, retire_wait_ns, max_retire_wait_ns;
	double eye_mean[2][3];
};

struct swapchain
{
	struct swapchain *next;
	struct device_data *dev;
	VkFormat format;
	uint32_t fourcc;
	uint32_t count;
	VkCommandPool pool; //!< compressed scanout's conversions, else VK_NULL_HANDLE
	// Compressed scanout with PICO4_VRDISPLAY_DUMP: host-visible copy of the dumped present.
	VkBuffer readback;
	VkDeviceMemory readback_memory;
	uint64_t dump_present;
	struct sc_image imgs[MAX_IMAGES];

	uint32_t queue[MAX_IMAGES]; //!< Presented image indices, oldest first.
	uint32_t queued;
	int wake; //!< eventfd that wakes the present thread
	pthread_t thread;
	bool thread_started, stop, lost;
	int pending, shown; //!< Image indices, -1 if none.
	int release_fd, retire_fd;
	int64_t pending_ns;
	uint64_t presented_id;
	uint64_t ahead; //!< PICO4_VRDISPLAY_FRAMES_AHEAD
	struct sc_stats stats;
};

static struct swapchain *
find_swapchain(VkSwapchainKHR handle)
{
	for (struct swapchain *s = g_display.swapchains; s != NULL; s = s->next) {
		if ((VkSwapchainKHR)(uintptr_t)s == handle) {
			return s;
		}
	}
	return NULL;
}

static bool
is_our_swapchain(VkSwapchainKHR handle)
{
	pthread_mutex_lock(&g_display.lock);
	bool ours = find_swapchain(handle) != NULL;
	pthread_mutex_unlock(&g_display.lock);
	return ours;
}

static uint32_t
format_fourcc(VkFormat format)
{
	switch (format) {
	case VK_FORMAT_B8G8R8A8_UNORM:
	case VK_FORMAT_B8G8R8A8_SRGB: return DRM_FORMAT_XRGB8888;
	case VK_FORMAT_R8G8B8A8_UNORM:
	case VK_FORMAT_R8G8B8A8_SRGB: return DRM_FORMAT_XBGR8888;
	default: return 0;
	}
}

/*!
 * Compressed scanout (PICO4_VRDISPLAY_UBWC): msm_drm's planes take Qualcomm's
 * compressed modifier for XBGR8888 only ("XB24/5/1" in their capabilities; XR24
 * is refused), SteamVR renders B8G8R8A8 only, and Turnip compresses no image with
 * storage usage on this GPU. The application therefore renders into a tiled,
 * compressed image of the layer's, without storage usage, and each present blits
 * it into a compressed R8G8B8A8 image that the planes scan out.
 *
 * With PICO4_VRDISPLAY_UBWC=2 the application renders into the scanout images
 * themselves: its image views of them and the render passes that present (final
 * layout PRESENT_SRC) take the R8G8B8A8 variant of its B8G8R8A8 format. The
 * shaders write the same colours; only the byte order in memory changes, to the
 * one the planes read, and the conversion goes.
 */
static bool
ubwc_mode(void)
{
	return env_num("PICO4_VRDISPLAY_UBWC", 0) != 0 && !fake_mode();
}

static bool
direct_mode(void)
{
	return env_num("PICO4_VRDISPLAY_UBWC", 0) == 2 && !fake_mode();
}

//! The R8G8B8A8 format with @p format's encoding, or @p format if it is no B8G8R8A8 format.
static VkFormat
rgba_variant(VkFormat format)
{
	switch (format) {
	case VK_FORMAT_B8G8R8A8_UNORM: return VK_FORMAT_R8G8B8A8_UNORM;
	case VK_FORMAT_B8G8R8A8_SRGB: return VK_FORMAT_R8G8B8A8_SRGB;
	default: return format;
	}
}

//! Whether @p image is a scanout image an application renders into directly.
static bool
is_direct_image(VkImage image)
{
	bool found = false;
	pthread_mutex_lock(&g_display.lock);
	for (struct swapchain *s = g_display.swapchains; s != NULL && !found; s = s->next) {
		for (uint32_t i = 0; i < s->count; i++) {
			if (s->imgs[i].direct && s->imgs[i].image == image) {
				found = true;
				break;
			}
		}
	}
	pthread_mutex_unlock(&g_display.lock);
	return found;
}

//! Writes the downscaled image as PPM and keeps each eye's mean colour (debugging).
static void
sc_dump(struct swapchain *sc, struct sc_image *img, const char *path)
{
	struct device_data *dev = sc->dev;
	const uint8_t *map = NULL;
	void *vk_map = NULL;
	// Compressed scanout: the present copied the application's image into the readback buffer.
	VkDeviceSize offset = img->offset, pitch = img->pitch;
	VkDeviceMemory mapped = img->memory;
	if (img->modifier != DRM_FORMAT_MOD_LINEAR) {
		if (sc->readback_memory == VK_NULL_HANDLE ||
		    dev->MapMemory(dev->device, sc->readback_memory, 0, VK_WHOLE_SIZE, 0, &vk_map) != VK_SUCCESS) {
			LOG("no readback of the compressed image for the dump");
			return;
		}
		map = vk_map;
		mapped = sc->readback_memory;
		offset = 0;
		pitch = (VkDeviceSize)g_width * 4;
	} else if (img->dmabuf >= 0) {
		void *m = mmap(NULL, img->size, PROT_READ, MAP_SHARED, img->dmabuf, 0);
		map = m == MAP_FAILED ? NULL : m;
		struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
		if (map != NULL) {
			ioctl(img->dmabuf, DMA_BUF_IOCTL_SYNC, &sync);
		}
	} else if (img->mappable && dev->MapMemory(dev->device, img->memory, 0, VK_WHOLE_SIZE, 0, &vk_map) == VK_SUCCESS) {
		map = vk_map;
	}
	if (map == NULL) {
		LOG("cannot map the image for the dump");
		return;
	}
	FILE *f = fopen(path, "wbe");
	bool bgr = sc->fourcc == DRM_FORMAT_XRGB8888 && !img->direct; // direct: the planes' R8G8B8A8
	double sum[2][3] = {{0}};
	if (f != NULL) {
		fprintf(f, "P6\n%u %u\n255\n", g_width / 4, g_height / 4);
	}
	for (uint32_t y = 0; y < g_height; y += 4) {
		for (uint32_t x = 0; x < g_width; x += 4) {
			const uint8_t *px = map + offset + (size_t)y * pitch + (size_t)x * 4;
			uint8_t rgb[3] = {bgr ? px[2] : px[0], px[1], bgr ? px[0] : px[2]};
			for (int c = 0; c < 3; c++) {
				sum[x / (g_width / 2)][c] += rgb[c];
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
			sc->stats.eye_mean[e][c] = sum[e][c] / (((g_width / 2) / 4) * (g_height / 4));
		}
	}
	if (vk_map == NULL) {
		struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ};
		ioctl(img->dmabuf, DMA_BUF_IOCTL_SYNC, &sync);
		munmap((void *)map, img->size);
	} else {
		dev->UnmapMemory(dev->device, mapped);
	}
	LOG("dumped present %" PRIu64 " to %s", sc->stats.presents, path);
}

/*!
 * Luma of a sparse grid over the middle of the left eye, for the motion log: a turn of
 * the view changes it a lot, a still view hardly. Returns the samples taken.
 */
#define MOTION_BAND 1024 //!< Pixels of the band whose shift the motion log measures.
#define MOTION_SEARCH 160 //!< Largest shift it looks for, pixels.
#define MOTION_ROWS 16    //!< Bands over the eye's height in the logged form.
#define MOTION_ROW_PX 512 //!< Values per logged band (every other pixel).

/*!
 * The horizontal shift of @p cur against @p prev in pixels (subpixel), by the best
 * normalised correlation of the two bands' gradients.
 */
static double
band_shift(const float *prev, const float *cur)
{
	double best = -2, best_s = 0, score[2 * MOTION_SEARCH + 1];
	for (int s = -MOTION_SEARCH; s <= MOTION_SEARCH; s++) {
		double sab = 0, saa = 0, sbb = 0;
		for (int x = MOTION_SEARCH + 1; x < MOTION_BAND - MOTION_SEARCH - 1; x++) {
			double a = prev[x + 1] - prev[x - 1], b = cur[x + s + 1] - cur[x + s - 1];
			sab += a * b;
			saa += a * a;
			sbb += b * b;
		}
		double c = saa > 0 && sbb > 0 ? sab / sqrt(saa * sbb) : 0;
		score[s + MOTION_SEARCH] = c;
		if (c > best) {
			best = c;
			best_s = s;
		}
	}
	int i = (int)best_s + MOTION_SEARCH;
	if (i > 0 && i < 2 * MOTION_SEARCH) {
		double l = score[i - 1], m = score[i], r = score[i + 1], den = l - 2 * m + r;
		if (den < 0) {
			best_s += 0.5 * (l - r) / den;
		}
	}
	return best_s;
}

static unsigned
sc_sample(struct swapchain *sc, struct sc_image *img, uint8_t *out, unsigned max, float *band, uint8_t *rows)
{
	if (img->dmabuf < 0 || img->modifier != DRM_FORMAT_MOD_LINEAR) {
		return 0;
	}
	void *m = mmap(NULL, img->size, PROT_READ, MAP_SHARED, img->dmabuf, 0);
	if (m == MAP_FAILED) {
		return 0;
	}
	struct dma_buf_sync sync = {.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ};
	ioctl(img->dmabuf, DMA_BUF_IOCTL_SYNC, &sync);
	bool bgr = sc->fourcc == DRM_FORMAT_XRGB8888;
	unsigned n = 0;
	for (uint32_t y = g_height / 4; y < g_height * 3 / 4 && n < max; y += 32) {
		for (uint32_t x = g_width / 16; x < g_width * 7 / 16 && n < max; x += 32) {
			const uint8_t *px = (const uint8_t *)m + img->offset + (size_t)y * img->pitch + (size_t)x * 4;
			unsigned r = bgr ? px[2] : px[0], g = px[1], b = bgr ? px[0] : px[2];
			out[n++] = (uint8_t)((r * 77 + g * 150 + b * 29) >> 8);
		}
	}
	// The band: eight rows through the left eye's centre, MOTION_BAND pixels wide.
	uint32_t x0 = g_width / 4 - MOTION_BAND / 2;
	for (int x = 0; x < MOTION_BAND; x++) {
		band[x] = 0;
	}
	for (uint32_t y = g_height / 2 - 4; y < g_height / 2 + 4; y++) {
		const uint8_t *row = (const uint8_t *)m + img->offset + (size_t)y * img->pitch + (size_t)x0 * 4;
		for (int x = 0; x < MOTION_BAND; x++) {
			band[x] += (float)row[4 * x + 1]; // green
		}
	}
	if (rows != NULL) {
		// Logged bands from 10% to 90% of the height, each the mean of four rows.
		for (int r = 0; r < MOTION_ROWS; r++) {
			uint32_t y = g_height / 10 + (uint32_t)r * (g_height * 8 / 10) / (MOTION_ROWS - 1);
			for (int x = 0; x < MOTION_ROW_PX; x++) {
				unsigned sum = 0;
				for (uint32_t dy = 0; dy < 4; dy++) {
					sum += ((const uint8_t *)m + img->offset + (size_t)(y + dy) * img->pitch +
					        (size_t)(x0 + 2 * x) * 4)[1];
				}
				rows[r * MOTION_ROW_PX + x] = (uint8_t)(sum / 4);
			}
		}
	}
	sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
	ioctl(img->dmabuf, DMA_BUF_IOCTL_SYNC, &sync);
	munmap(m, img->size);
	return n;
}

static void
sc_write_stats(struct swapchain *sc, const char *path)
{
	struct display *d = &g_display;
	const struct sc_stats *s = &sc->stats;
	double seconds = (s->last_ns - s->first_ns) / 1e9;
	double vblank_seconds = d->last_vblank - d->first_vblank;
	// Written beside the file and renamed: checks read it while the compositor runs.
	char tmp[PATH_MAX];
	snprintf(tmp, sizeof(tmp), "%s.tmp", path);
	FILE *f = fopen(tmp, "we");
	if (f == NULL) {
		LOG("cannot write %s: %s", tmp, strerror(errno));
		return;
	}
	fprintf(f,
	        "{\"target\":\"pico4_vrdisplay\",\"fake\":%s,\"mode\":\"%s\",\"format\":\"%s\",\"images\":%u"
	        ",\"presents\":%" PRIu64 ",\"commits\":%" PRIu64 ",\"commit_hz\":%.4f,\"max_interval_ms\":%.3f"
	        ",\"late\":%" PRIu64 ",\"stalls\":%" PRIu64 ",\"busy_retries\":%" PRIu64 ",\"retire_waits\":%" PRIu64
	        ",\"retire_timeouts\":%" PRIu64 ",\"gpu_timeouts\":%" PRIu64 ",\"vblanks\":%" PRIu64
	        ",\"vblank_hz\":%.4f,\"gpu_wait_ms\":{\"mean\":%.3f,\"max\":%.3f}"
	        ",\"retire_wait_ms\":{\"mean\":%.3f,\"max\":%.3f},\"present_waits\":%" PRIu64
	        ",\"present_wait_timeouts\":%" PRIu64 ",\"max_present_wait_lag\":%" PRIu64
	        ",\"vblank_tick_intervals\":%" PRIu64 ",\"vblank_intervals\":%" PRIu64 ",\"eye_mean_rgb\":",
	        d->fake ? "true" : "false", d->fake ? "fake" : d->mode.name,
	        sc->fourcc == DRM_FORMAT_XRGB8888 ? "XRGB8888" : "XBGR8888", sc->count, s->presents, s->commits,
	        s->commits > 1 && seconds > 0 ? (s->commits - 1) / seconds : 0.0, s->max_interval_ns / 1e6, s->late,
	        s->stalls, s->busy_retries, s->retire_waits, s->retire_timeouts, s->gpu_timeouts, d->stat_vblanks,
	        d->stat_vblanks > 1 && vblank_seconds > 0 ? (d->last_sequence - d->first_sequence) / vblank_seconds
	                                                  : 0.0,
	        s->commits ? s->gpu_wait_ns / 1e6 / s->commits : 0.0, s->max_gpu_wait_ns / 1e6,
	        s->retire_waits ? s->retire_wait_ns / 1e6 / s->retire_waits : 0.0, s->max_retire_wait_ns / 1e6,
	        s->present_waits, s->present_wait_timeouts, s->max_present_wait_lag, d->stat_tick_intervals,
	        d->stat_intervals);
	if (s->eye_mean[0][0] < 0) {
		fprintf(f, "null}\n");
	} else {
		fprintf(f, "[[%.1f,%.1f,%.1f],[%.1f,%.1f,%.1f]]}\n", s->eye_mean[0][0], s->eye_mean[0][1],
		        s->eye_mean[0][2], s->eye_mean[1][0], s->eye_mean[1][1], s->eye_mean[1][2]);
	}
	if (fclose(f) != 0 || rename(tmp, path) != 0) {
		LOG("cannot write %s: %s", path, strerror(errno));
	}
	// The scheduler-tick state, once per statistics period in which it shows.
	uint64_t n = d->stat_intervals - d->logged_intervals, tick = d->stat_tick_intervals - d->logged_tick_intervals;
	if (n >= 90 && tick * 4 > n) {
		LOG("vblank events on the 10 ms scheduler tick: %" PRIu64 " of %" PRIu64 " intervals", tick, n);
	}
	d->logged_intervals = d->stat_intervals;
	d->logged_tick_intervals = d->stat_tick_intervals;
}

//! Locked. The pending commit is on screen: the image before it is free again.
static void
sc_retired(struct swapchain *sc)
{
	if (sc->release_fd >= 0) {
		close(sc->release_fd);
	}
	if (sc->retire_fd >= 0) {
		close(sc->retire_fd);
	}
	sc->release_fd = sc->retire_fd = -1;
	if (sc->pending < 0) {
		return;
	}
	if (sc->shown >= 0) {
		sc->imgs[sc->shown].state = IMAGE_FREE;
	}
	sc->shown = sc->pending;
	sc->pending = -1;
	sc->imgs[sc->shown].state = IMAGE_SHOWN;
	trace('R', sc->imgs[sc->shown].present_id, sc->shown);
	if (sc->imgs[sc->shown].present_id > sc->presented_id) {
		sc->presented_id = sc->imgs[sc->shown].present_id;
	}
	pthread_cond_broadcast(&g_display.cond);
}

//! Locked. Shows image @p index; returns false if the display is gone.
static bool
sc_commit(struct swapchain *sc, uint32_t index)
{
	struct display *d = &g_display;
	struct sc_image *img = &sc->imgs[index];
	int release = -1, retire = -1;
	bool modeset = !d->active;

	if (d->fake) {
		// A timer one refresh away stands in for the retire fence.
		retire = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
		struct itimerspec its = {.it_value = {0, PERIOD_NS}};
		timerfd_settime(retire, 0, &its, NULL);
		d->active = true;
		modeset = false;
	} else if (!kms_commit(d, img->fb, &release, &retire, &sc->stats.busy_retries)) {
		return false;
	}
	if (modeset) {
		// Shown at once: the mode set blocks until it is on screen.
		if (sc->shown >= 0) {
			sc->imgs[sc->shown].state = IMAGE_FREE;
		}
		sc->shown = (int)index;
		img->state = IMAGE_SHOWN;
		if (img->present_id > sc->presented_id) {
			sc->presented_id = img->present_id;
		}
	} else {
		sc->pending = (int)index;
		sc->release_fd = release;
		sc->retire_fd = retire;
		img->state = IMAGE_PENDING;
		// Frames ahead: the presents after this one count as shown already.
		if (sc->ahead > 0 && img->present_id + sc->ahead > sc->presented_id) {
			sc->presented_id = img->present_id + sc->ahead;
			trace('S', sc->presented_id, (int64_t)index);
		}
	}
	sc->pending_ns = now_ns();
	trace('C', img->present_id, (int64_t)index);
	pthread_cond_broadcast(&d->cond);

	struct sc_stats *s = &sc->stats;
	int64_t t = sc->pending_ns;
	if (s->commits > 0) {
		int64_t interval = t - s->last_ns;
		if (interval > s->max_interval_ns) {
			s->max_interval_ns = interval;
		}
		if (interval * 2 > PERIOD_NS * 3) {
			s->late++;
		}
		if (interval > STALL_NS) {
			s->stalls++;
		}
	} else {
		s->first_ns = t;
	}
	s->last_ns = t;
	s->commits++;
	return true;
}

static void *
present_main(void *arg)
{
	struct swapchain *sc = arg;
	struct display *d = &g_display;
	struct device_data *dev = sc->dev;
	const char *dump = getenv("PICO4_VRDISPLAY_DUMP");
	uint64_t dump_frame = (uint64_t)env_num("PICO4_VRDISPLAY_DUMP_FRAME", 300);
	uint64_t dump_every = (uint64_t)env_num("PICO4_VRDISPLAY_DUMP_EVERY", 0);
	const char *stats = getenv("PICO4_VRDISPLAY_STATS");
	int64_t stats_ns = now_ns();
	const char *motion_path = getenv("PICO4_VRDISPLAY_MOTION");
	FILE *motion = motion_path != NULL ? fopen(motion_path, "we") : NULL;
	enum { MOTION_SAMPLES = 2048 };
	static uint8_t samples[2][MOTION_SAMPLES];
	static float bands[2][MOTION_BAND];
	static uint8_t rows[MOTION_ROWS * MOTION_ROW_PX];
	unsigned sampled = 0, which = 0;
	bool log_band = env_num("PICO4_VRDISPLAY_MOTION_BAND", 0) != 0;
	const char *trace_path = getenv("PICO4_VRDISPLAY_TRACE");
	FILE *trace_file = trace_path != NULL ? fopen(trace_path, "we") : NULL;
	if (trace_file != NULL) {
		pthread_mutex_lock(&d->lock);
		g_trace_written = g_trace_n;
		g_trace_on = true;
		pthread_mutex_unlock(&d->lock);
	}
	if (motion != NULL) {
		setvbuf(motion, NULL, _IOLBF, 0);
		fprintf(motion, "#commit_ns,display_ns,luma_change,sample_ms,shift_px\n");
	}

	pthread_mutex_lock(&d->lock);
	for (;;) {
		if (sc->stop && sc->queued == 0 && sc->retire_fd < 0) {
			break;
		}
		if (sc->retire_fd >= 0) {
			// One flip in flight: wait for it (or for news) before the next.
			struct pollfd p[2] = {{.fd = sc->wake, .events = POLLIN}, {.fd = sc->retire_fd, .events = POLLIN}};
			int64_t before = now_ns();
			pthread_mutex_unlock(&d->lock);
			int n = poll(p, 2, 500);
			uint64_t drain;
			if (p[0].revents & POLLIN) {
				(void)!read(sc->wake, &drain, sizeof(drain));
			}
			pthread_mutex_lock(&d->lock);
			if (n == 0 || (p[1].revents & (POLLIN | POLLERR | POLLHUP)) != 0) {
				if (n == 0) {
					sc->stats.retire_timeouts++;
					LOG("retire fence not signalled after 500 ms");
				}
				int64_t waited = now_ns() - before;
				if (sc->queued > 0) {
					sc->stats.retire_waits++;
					sc->stats.retire_wait_ns += waited;
					if (waited > sc->stats.max_retire_wait_ns) {
						sc->stats.max_retire_wait_ns = waited;
					}
				}
				sc_retired(sc);
			}
			continue;
		}
		if (sc->queued == 0) {
			pthread_mutex_unlock(&d->lock);
			struct pollfd p = {.fd = sc->wake, .events = POLLIN};
			poll(&p, 1, 1000);
			uint64_t drain;
			if (p.revents & POLLIN) {
				(void)!read(sc->wake, &drain, sizeof(drain));
			}
			pthread_mutex_lock(&d->lock);
			continue;
		}

		uint32_t index = sc->queue[0];
		memmove(sc->queue, sc->queue + 1, (sc->queued - 1) * sizeof(sc->queue[0]));
		sc->queued--;
		struct sc_image *img = &sc->imgs[index];
		pthread_mutex_unlock(&d->lock);

		// Scanout must follow the rendering: the present's wait semaphores.
		int64_t before = now_ns();
		VkResult ret = dev->WaitForFences(dev->device, 1, &img->fence, VK_TRUE, 2 * NS_PER_S);
		int64_t waited = now_ns() - before;

		pthread_mutex_lock(&d->lock);
		trace('G', img->present_id, waited / 1000);
		if (ret != VK_SUCCESS) {
			sc->stats.gpu_timeouts++;
			LOG("GPU work of present %u did not finish: %d", index, ret);
		}
		sc->stats.gpu_wait_ns += waited;
		if (waited > sc->stats.max_gpu_wait_ns) {
			sc->stats.max_gpu_wait_ns = waited;
		}
		if (!sc->lost && !sc_commit(sc, index)) {
			sc->lost = true;
		}
		if (sc->lost) {
			img->state = IMAGE_FREE;
			pthread_cond_broadcast(&d->cond);
			continue;
		}
		// File I/O without the lock: the vblank clock must not wait for it. Only this
		// thread commits, so the image stays on screen meanwhile.
		if (motion != NULL) {
			// The flip happens at the first vblank after the commit.
			int64_t commit_ns = sc->pending_ns, display_ns = d->vblank_ns;
			while (display_ns > 0 && display_ns < commit_ns) {
				display_ns += PERIOD_NS;
			}
			pthread_mutex_unlock(&d->lock);
			int64_t t0 = now_ns();
			unsigned n = sc_sample(sc, img, samples[which], MOTION_SAMPLES, bands[which], log_band ? rows : NULL);
			double change = -1, shift = 0;
			if (n > 0 && n == sampled) {
				uint64_t sum = 0;
				for (unsigned i = 0; i < n; i++) {
					sum += (unsigned)abs((int)samples[which][i] - (int)samples[which ^ 1][i]);
				}
				change = (double)sum / n;
				shift = band_shift(bands[which ^ 1], bands[which]);
			}
			sampled = n;
			which ^= 1;
			fprintf(motion, "%" PRId64 ",%" PRId64 ",%.2f,%.2f,%.2f", commit_ns, display_ns, change,
			        (now_ns() - t0) / 1e6, shift);
			if (log_band && n > 0) {
				static const char hex[] = "0123456789abcdef";
				static char line[2 * MOTION_ROWS * MOTION_ROW_PX + 1];
				for (int i = 0; i < MOTION_ROWS * MOTION_ROW_PX; i++) {
					line[2 * i] = hex[rows[i] >> 4];
					line[2 * i + 1] = hex[rows[i] & 15];
				}
				line[2 * MOTION_ROWS * MOTION_ROW_PX] = '\0';
				fputc(',', motion);
				fputs(line, motion);
			}
			fputc('\n', motion);
			pthread_mutex_lock(&d->lock);
		}
		if (dump != NULL &&
		    (img->readback || (img->copy == VK_NULL_HANDLE && !img->direct && sc->stats.commits == dump_frame))) {
			img->readback = false;
			// A series (PICO4_VRDISPLAY_DUMP_EVERY): each dump named after its commit.
			char path[PATH_MAX];
			snprintf(path, sizeof(path), "%s", dump);
			if (dump_every > 0) {
				snprintf(path, sizeof(path), "%s.%06" PRIu64, dump, sc->stats.commits);
				sc->dump_present += dump_every;
				dump_frame += dump_every;
			}
			pthread_mutex_unlock(&d->lock);
			sc_dump(sc, img, path);
			pthread_mutex_lock(&d->lock);
		}
		if (stats != NULL && sc->stats.last_ns - stats_ns >= STATS_EVERY_NS) {
			stats_ns = sc->stats.last_ns;
			pthread_mutex_unlock(&d->lock);
			sc_write_stats(sc, stats); // a running compositor's numbers so far
			if (trace_file != NULL) {
				trace_flush(trace_file);
			}
			pthread_mutex_lock(&d->lock);
		}
	}
	pthread_mutex_unlock(&d->lock);
	if (motion != NULL) {
		fclose(motion);
	}
	if (trace_file != NULL) {
		trace_flush(trace_file);
		pthread_mutex_lock(&d->lock);
		g_trace_on = false;
		pthread_mutex_unlock(&d->lock);
		fclose(trace_file);
	}
	return NULL;
}

static void
sc_free_images(struct swapchain *sc)
{
	struct device_data *dev = sc->dev;
	struct display *d = &g_display;
	for (uint32_t i = 0; i < sc->count; i++) {
		struct sc_image *img = &sc->imgs[i];
		if (img->fb != 0) {
			drmModeRmFB(d->fd, img->fb);
		}
		if (img->gem != 0) {
			drmCloseBufferHandle(d->fd, img->gem);
		}
		if (img->dmabuf >= 0) {
			close(img->dmabuf);
		}
		if (img->fence != VK_NULL_HANDLE) {
			dev->DestroyFence(dev->device, img->fence, NULL);
		}
		if (img->image != VK_NULL_HANDLE && img->image != img->scan) {
			dev->DestroyImage(dev->device, img->image, NULL);
		}
		if (img->memory != VK_NULL_HANDLE) {
			dev->FreeMemory(dev->device, img->memory, NULL);
		}
		if (img->scan != VK_NULL_HANDLE) {
			dev->DestroyImage(dev->device, img->scan, NULL);
		}
		if (img->scan_memory != VK_NULL_HANDLE) {
			dev->FreeMemory(dev->device, img->scan_memory, NULL);
		}
		*img = (struct sc_image){.dmabuf = -1};
	}
	if (sc->pool != VK_NULL_HANDLE) {
		dev->DestroyCommandPool(dev->device, sc->pool, NULL); // frees the conversions
		sc->pool = VK_NULL_HANDLE;
	}
	if (sc->readback != VK_NULL_HANDLE) {
		dev->DestroyBuffer(dev->device, sc->readback, NULL);
		dev->FreeMemory(dev->device, sc->readback_memory, NULL);
		sc->readback = VK_NULL_HANDLE;
		sc->readback_memory = VK_NULL_HANDLE;
	}
}

static bool
memory_type(struct device_data *dev, uint32_t bits, VkMemoryPropertyFlags want, uint32_t *out)
{
	VkPhysicalDeviceMemoryProperties props;
	dev->inst->GetPhysicalDeviceMemoryProperties(dev->physical, &props);
	for (uint32_t i = 0; i < props.memoryTypeCount; i++) {
		if ((bits & (1u << i)) != 0 && (props.memoryTypes[i].propertyFlags & want) == want) {
			*out = i;
			return true;
		}
	}
	return false;
}

/*!
 * Device-local memory for an image, dedicated, exported as a dma-buf when asked;
 * host-visible as well in the fake display mode, for dumps.
 */
static VkResult
sc_bind_memory(struct device_data *dev, VkImage image, bool export, VkDeviceMemory *memory, VkDeviceSize *size,
               bool *mappable)
{
	struct display *d = &g_display;
	VkMemoryRequirements reqs;
	dev->GetImageMemoryRequirements(dev->device, image, &reqs);
	uint32_t type = 0;
	*mappable = d->fake && memory_type(dev, reqs.memoryTypeBits,
	                                   VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT,
	                                   &type);
	if (!*mappable && !memory_type(dev, reqs.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, &type)) {
		LOG("no device-local memory type for the swapchain");
		return VK_ERROR_OUT_OF_DEVICE_MEMORY;
	}
	VkMemoryDedicatedAllocateInfo dedicated = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
	    .image = image,
	};
	VkExportMemoryAllocateInfo export_info = {
	    .sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO,
	    .pNext = &dedicated,
	    .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	VkMemoryAllocateInfo alloc = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
	    .pNext = export ? (const void *)&export_info : (const void *)&dedicated,
	    .allocationSize = reqs.size,
	    .memoryTypeIndex = type,
	};
	VkResult ret = dev->AllocateMemory(dev->device, &alloc, NULL, memory);
	if (ret == VK_SUCCESS) {
		ret = dev->BindImageMemory(dev->device, image, *memory, 0);
	}
	if (ret != VK_SUCCESS) {
		LOG("swapchain image memory: %d", ret);
	}
	*size = reqs.size;
	return ret;
}

/*!
 * Records the conversion of the application's image into the scanout image; with
 * @p readback also a copy of the application's image into the readback buffer.
 */
static VkResult
sc_record_copy(struct swapchain *sc, struct sc_image *img, VkCommandBuffer *cmd, bool readback)
{
	struct device_data *dev = sc->dev;
	VkResult ret;
	VkCommandBufferAllocateInfo alloc = {
	    .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
	    .commandPool = sc->pool,
	    .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
	    .commandBufferCount = 1,
	};
	ret = dev->AllocateCommandBuffers(dev->device, &alloc, cmd);
	if (ret != VK_SUCCESS) {
		*cmd = VK_NULL_HANDLE;
		return ret;
	}
	// A command buffer made below the loader carries its device's loader dispatch.
	*(void **)*cmd = *(void **)dev->device;
	VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
	ret = dev->BeginCommandBuffer(*cmd, &begin);
	if (ret != VK_SUCCESS) {
		return ret;
	}
	const VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
	// Direct: no conversion, only the readback of the image the application rendered into.
	uint32_t images = img->direct ? 1 : 2;
	// The present's semaphore waits order this after the application's rendering.
	VkImageMemoryBarrier before[2] = {
	    {
	        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
	        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
	        .oldLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
	        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
	        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .image = img->image,
	        .subresourceRange = range,
	    },
	    {
	        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
	        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
	        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
	        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
	        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .image = img->scan,
	        .subresourceRange = range,
	    },
	};
	dev->CmdPipelineBarrier(*cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL,
	                        0, NULL, images, before);
	if (!img->direct) {
		VkImageBlit blit = {
		    .srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
		    .srcOffsets = {{0, 0, 0}, {(int32_t)g_width, (int32_t)g_height, 1}},
		    .dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
		    .dstOffsets = {{0, 0, 0}, {(int32_t)g_width, (int32_t)g_height, 1}},
		};
		dev->CmdBlitImage(*cmd, img->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, img->scan,
		                  VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &blit, VK_FILTER_NEAREST);
	}
	if (readback) {
		VkBufferImageCopy region = {
		    .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
		    .imageExtent = {g_width, g_height, 1},
		};
		dev->CmdCopyImageToBuffer(*cmd, img->image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, sc->readback, 1, &region);
		VkBufferMemoryBarrier host = {
		    .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
		    .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
		    .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
		    .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
		    .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
		    .buffer = sc->readback,
		    .size = VK_WHOLE_SIZE,
		};
		dev->CmdPipelineBarrier(*cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1,
		                        &host, 0, NULL);
	}
	// Back to the application's layout; the scanout image's writes made visible to the display.
	VkImageMemoryBarrier after[2] = {
	    {
	        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
	        .srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
	        .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
	        .newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
	        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .image = img->image,
	        .subresourceRange = range,
	    },
	    {
	        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
	        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
	        .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT,
	        .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
	        .newLayout = VK_IMAGE_LAYOUT_GENERAL,
	        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	        .image = img->scan,
	        .subresourceRange = range,
	    },
	};
	dev->CmdPipelineBarrier(*cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0,
	                        NULL, 0, NULL, images, after);
	return dev->EndCommandBuffer(*cmd);
}

/*!
 * Compressed scanout (see ubwc_mode): a compressed RGBA image for the planes, and
 * the command buffer that converts the application's image into it at present. With
 * @p info (direct mode) the application renders into the image itself instead, with
 * the usage and view formats of its swapchain.
 */
static VkResult
sc_create_scan(struct swapchain *sc, struct sc_image *img, const VkSwapchainCreateInfoKHR *info)
{
	struct device_data *dev = sc->dev;
	uint64_t compressed = DRM_FORMAT_MOD_QCOM_COMPRESSED;
	VkFormat views[8];
	VkImageFormatListCreateInfo formats = {.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO};
	for (const VkBaseInStructure *s = info != NULL ? info->pNext : NULL; s != NULL; s = s->pNext) {
		const VkImageFormatListCreateInfo *list = (const VkImageFormatListCreateInfo *)s;
		if (s->sType == VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO && list->viewFormatCount <= 8) {
			for (uint32_t i = 0; i < list->viewFormatCount; i++) {
				views[i] = rgba_variant(list->pViewFormats[i]);
			}
			formats.viewFormatCount = list->viewFormatCount;
			formats.pViewFormats = views;
		}
	}
	VkExternalMemoryImageCreateInfo external = {
	    .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
	    .pNext = formats.viewFormatCount > 0 ? &formats : NULL,
	    .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	VkImageDrmFormatModifierListCreateInfoEXT modifiers = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_LIST_CREATE_INFO_EXT,
	    .pNext = &external,
	    .drmFormatModifierCount = 1,
	    .pDrmFormatModifiers = &compressed,
	};
	bool srgb = sc->format == VK_FORMAT_B8G8R8A8_SRGB || sc->format == VK_FORMAT_R8G8B8A8_SRGB;
	VkImageCreateInfo image_info = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
	    .pNext = &modifiers,
	    .flags = info != NULL && (info->flags & VK_SWAPCHAIN_CREATE_MUTABLE_FORMAT_BIT_KHR) != 0
	                 ? VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT | VK_IMAGE_CREATE_EXTENDED_USAGE_BIT
	                 : 0,
	    .imageType = VK_IMAGE_TYPE_2D,
	    .format = srgb ? VK_FORMAT_R8G8B8A8_SRGB : VK_FORMAT_R8G8B8A8_UNORM,
	    .extent = {g_width, g_height, 1},
	    .mipLevels = 1,
	    .arrayLayers = 1,
	    .samples = VK_SAMPLE_COUNT_1_BIT,
	    .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
	    // Storage usage would rule out compression; SteamVR asks for it without using it.
	    .usage = info != NULL ? (info->imageUsage & ~VK_IMAGE_USAGE_STORAGE_BIT) | VK_IMAGE_USAGE_TRANSFER_SRC_BIT
	                          : VK_IMAGE_USAGE_TRANSFER_DST_BIT,
	    .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
	    .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
	};
	VkResult ret = dev->CreateImage(dev->device, &image_info, NULL, &img->scan);
	if (ret != VK_SUCCESS) {
		LOG("vkCreateImage (compressed scanout): %d", ret);
		return ret;
	}
	VkImageDrmFormatModifierPropertiesEXT props = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_PROPERTIES_EXT,
	};
	if (dev->GetImageDrmFormatModifierPropertiesEXT(dev->device, img->scan, &props) != VK_SUCCESS ||
	    props.drmFormatModifier != compressed) {
		LOG("the driver did not compress the scanout image (modifier 0x%" PRIx64 ")", props.drmFormatModifier);
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	img->modifier = compressed;
	bool mappable;
	ret = sc_bind_memory(dev, img->scan, true, &img->scan_memory, &img->size, &mappable);
	if (ret != VK_SUCCESS) {
		return ret;
	}

	if (info != NULL) {
		img->direct = true;
		img->image = img->scan;
	} else {
		ret = sc_record_copy(sc, img, &img->copy, false);
	}
	if (ret == VK_SUCCESS && sc->readback != VK_NULL_HANDLE) {
		ret = sc_record_copy(sc, img, &img->copy_dump, true);
	}
	return ret;
}

static VkResult
sc_create_image(struct swapchain *sc, const VkSwapchainCreateInfoKHR *info, struct sc_image *img)
{
	struct device_data *dev = sc->dev;
	struct display *d = &g_display;
	bool export = !d->fake;
	bool ubwc = sc->pool != VK_NULL_HANDLE;

	const VkImageFormatListCreateInfo *list = NULL;
	for (const VkBaseInStructure *s = info->pNext; s != NULL; s = s->pNext) {
		if (s->sType == VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO) {
			list = (const VkImageFormatListCreateInfo *)s;
		}
	}
	VkImageFormatListCreateInfo formats = {.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO};
	if (list != NULL) {
		formats.viewFormatCount = list->viewFormatCount;
		formats.pViewFormats = list->pViewFormats;
	}
	VkExternalMemoryImageCreateInfo external = {
	    .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
	    .pNext = list != NULL ? &formats : NULL,
	    .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	/*
	 * The application's image: the linear scanout image itself, or with compressed
	 * scanout a tiled image of its own that stays compressed (storage usage would
	 * rule that out on this GPU) and is converted at present.
	 */
	VkImageCreateInfo image_info = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
	    .pNext = export && !ubwc ? (const void *)&external : (list != NULL ? (const void *)&formats : NULL),
	    .flags = (info->flags & VK_SWAPCHAIN_CREATE_MUTABLE_FORMAT_BIT_KHR) != 0
	                 ? VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT | VK_IMAGE_CREATE_EXTENDED_USAGE_BIT
	                 : 0,
	    .imageType = VK_IMAGE_TYPE_2D,
	    .format = info->imageFormat,
	    .extent = {g_width, g_height, 1},
	    .mipLevels = 1,
	    .arrayLayers = 1,
	    .samples = VK_SAMPLE_COUNT_1_BIT,
	    .tiling = ubwc ? VK_IMAGE_TILING_OPTIMAL : VK_IMAGE_TILING_LINEAR,
	    .usage = ubwc ? (info->imageUsage & ~VK_IMAGE_USAGE_STORAGE_BIT) | VK_IMAGE_USAGE_TRANSFER_SRC_BIT
	                  : info->imageUsage,
	    .sharingMode = info->imageSharingMode,
	    .queueFamilyIndexCount = info->queueFamilyIndexCount,
	    .pQueueFamilyIndices = info->pQueueFamilyIndices,
	    .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
	};
	bool direct = ubwc && direct_mode();
	VkResult ret = VK_SUCCESS;
	if (!direct) {
		ret = dev->CreateImage(dev->device, &image_info, NULL, &img->image);
		if (ret != VK_SUCCESS) {
			LOG("vkCreateImage (%s, usage 0x%x, format %d): %d", ubwc ? "tiled" : "linear", image_info.usage,
			    info->imageFormat, ret);
			return ret;
		}
		ret = sc_bind_memory(dev, img->image, export && !ubwc, &img->memory, &img->size, &img->mappable);
		if (ret != VK_SUCCESS) {
			return ret;
		}
	}
	img->modifier = DRM_FORMAT_MOD_LINEAR;
	VkImage scan = img->image;
	VkDeviceMemory scan_memory = img->memory;
	if (ubwc) {
		ret = sc_create_scan(sc, img, direct ? info : NULL);
		if (ret != VK_SUCCESS) {
			return ret;
		}
		scan = img->scan;
		scan_memory = img->scan_memory;
	}

	VkImageSubresource sub = {
	    .aspectMask = ubwc ? VK_IMAGE_ASPECT_MEMORY_PLANE_0_BIT_EXT : VK_IMAGE_ASPECT_COLOR_BIT,
	};
	VkSubresourceLayout layout;
	dev->GetImageSubresourceLayout(dev->device, scan, &sub, &layout);
	img->offset = layout.offset;
	img->pitch = layout.rowPitch;

	VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
	ret = dev->CreateFence(dev->device, &fence_info, NULL, &img->fence);
	if (ret != VK_SUCCESS) {
		return ret;
	}
	if (!export) {
		return VK_SUCCESS;
	}

	VkMemoryGetFdInfoKHR fd_info = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
	    .memory = scan_memory,
	    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
	};
	if (dev->GetMemoryFdKHR == NULL) {
		LOG("the application did not enable VK_KHR_external_memory_fd; using the driver's entry point");
		dev->GetMemoryFdKHR = (PFN_vkGetMemoryFdKHR)dev->gdpa(dev->device, "vkGetMemoryFdKHR");
	}
	ret = dev->GetMemoryFdKHR != NULL ? dev->GetMemoryFdKHR(dev->device, &fd_info, &img->dmabuf)
	                                  : VK_ERROR_EXTENSION_NOT_PRESENT;
	if (ret != VK_SUCCESS) {
		img->dmabuf = -1;
		LOG("vkGetMemoryFdKHR: %d", ret);
		return ret;
	}
	uint32_t handles[4] = {0}, pitches[4] = {(uint32_t)img->pitch}, offsets[4] = {(uint32_t)img->offset};
	if (drmPrimeFDToHandle(d->fd, img->dmabuf, &img->gem) != 0) {
		LOG("importing the dma-buf into msm_drm: %s", strerror(errno));
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	handles[0] = img->gem;
	// Compressed images are one plane from offset 0: the display finds the metadata in it.
	uint64_t mods[4] = {img->modifier};
	int added = ubwc ? drmModeAddFB2WithModifiers(d->fd, g_width, g_height, DRM_FORMAT_XBGR8888, handles, pitches,
	                                              offsets, mods, &img->fb, DRM_MODE_FB_MODIFIERS)
	                 : drmModeAddFB2(d->fd, g_width, g_height, sc->fourcc, handles, pitches, offsets, &img->fb, 0);
	if (added != 0) {
		LOG("drmModeAddFB2 (pitch %u, offset %u, modifier 0x%" PRIx64 "): %s", pitches[0], offsets[0],
		    img->modifier, strerror(errno));
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	return VK_SUCCESS;
}

/*!
 * Compressed scanout with a dump requested: a host-visible buffer the dumped
 * present copies the application's image into (the CPU cannot read compressed images).
 */
static void
sc_create_readback(struct swapchain *sc)
{
	struct device_data *dev = sc->dev;
	VkBufferCreateInfo info = {
	    .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
	    .size = (VkDeviceSize)g_width * g_height * 4,
	    .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
	    .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
	};
	if (dev->CreateBuffer(dev->device, &info, NULL, &sc->readback) != VK_SUCCESS) {
		sc->readback = VK_NULL_HANDLE;
		LOG("no readback buffer for the dump");
		return;
	}
	VkMemoryRequirements reqs;
	dev->GetBufferMemoryRequirements(dev->device, sc->readback, &reqs);
	uint32_t type = 0;
	VkMemoryAllocateInfo alloc = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = reqs.size};
	if (!memory_type(dev, reqs.memoryTypeBits,
	                 VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, &type) ||
	    (alloc.memoryTypeIndex = type,
	     dev->AllocateMemory(dev->device, &alloc, NULL, &sc->readback_memory) != VK_SUCCESS) ||
	    dev->BindBufferMemory(dev->device, sc->readback, sc->readback_memory, 0) != VK_SUCCESS) {
		LOG("no readback memory for the dump");
		dev->DestroyBuffer(dev->device, sc->readback, NULL);
		if (sc->readback_memory != VK_NULL_HANDLE) {
			dev->FreeMemory(dev->device, sc->readback_memory, NULL);
		}
		sc->readback = VK_NULL_HANDLE;
		sc->readback_memory = VK_NULL_HANDLE;
	}
}

static VkResult
sc_create(struct device_data *dev, const VkSwapchainCreateInfoKHR *info, VkSwapchainKHR *out)
{
	struct display *d = &g_display;
	if (info->imageExtent.width != g_width || info->imageExtent.height != g_height) {
		LOG("swapchain extent %ux%u, the display is %ux%u", info->imageExtent.width, info->imageExtent.height,
		    g_width, g_height);
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	uint32_t fourcc = format_fourcc(info->imageFormat);
	struct swapchain *sc = calloc(1, sizeof(*sc));
	if (sc == NULL) {
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	sc->dev = dev;
	sc->format = info->imageFormat;
	sc->fourcc = fourcc;
	sc->count = info->minImageCount < 3 ? 3 : info->minImageCount;
	if (sc->count > MAX_IMAGES) {
		sc->count = MAX_IMAGES;
	}
	sc->pending = sc->shown = -1;
	sc->release_fd = sc->retire_fd = -1;
	long ahead = env_num("PICO4_VRDISPLAY_FRAMES_AHEAD", 0);
	sc->ahead = ahead > 0 ? (uint64_t)(ahead < 2 ? ahead : 2) : 0;
	sc->stats.eye_mean[0][0] = -1;
	sc->wake = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
	for (uint32_t i = 0; i < MAX_IMAGES; i++) {
		sc->imgs[i].dmabuf = -1;
	}

	pthread_mutex_lock(&d->lock);
	bool ok = display_probe(d) && kms_ensure(d);
	bool format_ok = (fourcc == DRM_FORMAT_XRGB8888 && d->xrgb) || (fourcc == DRM_FORMAT_XBGR8888 && d->xbgr);
	if (ok) {
		vblank_start(d);
	}
	pthread_mutex_unlock(&d->lock);
	if (!ok || !format_ok || sc->wake < 0) {
		LOG("cannot present format %d on the panels", info->imageFormat);
		if (sc->wake >= 0) {
			close(sc->wake);
		}
		free(sc);
		return VK_ERROR_INITIALIZATION_FAILED;
	}

	VkResult ret = VK_SUCCESS;
	if (ubwc_mode() && dev->GetImageDrmFormatModifierPropertiesEXT != NULL && dev->CmdBlitImage != NULL) {
		// Turnip has one queue family, which presents.
		VkCommandPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
		ret = dev->CreateCommandPool(dev->device, &pool_info, NULL, &sc->pool);
		if (ret != VK_SUCCESS) {
			sc->pool = VK_NULL_HANDLE;
			ret = VK_SUCCESS;
			LOG("no command pool for compressed scanout; linear images");
		} else if (getenv("PICO4_VRDISPLAY_DUMP") != NULL) {
			sc->dump_present = (uint64_t)env_num("PICO4_VRDISPLAY_DUMP_FRAME", 300);
			sc_create_readback(sc);
		}
	}
	for (uint32_t i = 0; i < sc->count && ret == VK_SUCCESS; i++) {
		ret = sc_create_image(sc, info, &sc->imgs[i]);
	}
	if (ret != VK_SUCCESS) {
		sc_free_images(sc);
		close(sc->wake);
		free(sc);
		return ret;
	}
	if (info->oldSwapchain != VK_NULL_HANDLE) {
		pthread_mutex_lock(&d->lock);
		struct swapchain *old = find_swapchain(info->oldSwapchain);
		if (old != NULL) {
			old->lost = true; // Retired: no more acquires.
		}
		pthread_mutex_unlock(&d->lock);
	}
	pthread_mutex_lock(&d->lock);
	sc->next = d->swapchains;
	d->swapchains = sc;
	sc->thread_started = pthread_create(&sc->thread, NULL, present_main, sc) == 0;
	pthread_mutex_unlock(&d->lock);
	LOG("swapchain: %u images, format %d, usage 0x%x, flags 0x%x, present mode %d, pitch %u, modifier 0x%" PRIx64,
	    sc->count, info->imageFormat, info->imageUsage, info->flags, info->presentMode,
	    (unsigned)sc->imgs[0].pitch, sc->imgs[0].modifier);
	*out = (VkSwapchainKHR)(uintptr_t)sc;
	return VK_SUCCESS;
}

static void
sc_destroy(struct swapchain *sc)
{
	struct display *d = &g_display;
	pthread_mutex_lock(&d->lock);
	sc->stop = true;
	pthread_mutex_unlock(&d->lock);
	uint64_t one = 1;
	(void)!write(sc->wake, &one, sizeof(one));
	if (sc->thread_started) {
		pthread_join(sc->thread, NULL);
	}

	pthread_mutex_lock(&d->lock);
	for (struct swapchain **p = &d->swapchains; *p != NULL; p = &(*p)->next) {
		if (*p == sc) {
			*p = sc->next;
			break;
		}
	}
	const char *stats = getenv("PICO4_VRDISPLAY_STATS");
	if (stats != NULL) {
		sc_write_stats(sc, stats);
	}
	// Nothing may scan out an image that is about to go away.
	if (sc->shown >= 0 || sc->pending >= 0) {
		kms_disable(d);
	}
	pthread_mutex_unlock(&d->lock);
	LOG("swapchain destroyed after %" PRIu64 " commits", sc->stats.commits);
	sc_free_images(sc);
	close(sc->wake);
	free(sc);
}

//! Signals an acquire's semaphore and fence: nothing to wait for, the image is free.
static VkResult
signal_acquire(struct device_data *dev, VkSemaphore semaphore, VkFence fence)
{
	VkResult ret = VK_SUCCESS;
	bool semaphore_done = semaphore == VK_NULL_HANDLE, fence_done = fence == VK_NULL_HANDLE;
	if (!semaphore_done && dev->has_semaphore_fd && dev->ImportSemaphoreFdKHR != NULL) {
		// A sync file of -1 counts as already signalled.
		VkImportSemaphoreFdInfoKHR import = {
		    .sType = VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_FD_INFO_KHR,
		    .semaphore = semaphore,
		    .flags = VK_SEMAPHORE_IMPORT_TEMPORARY_BIT,
		    .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
		    .fd = -1,
		};
		semaphore_done = dev->ImportSemaphoreFdKHR(dev->device, &import) == VK_SUCCESS;
	}
	if (!fence_done && dev->has_fence_fd && dev->ImportFenceFdKHR != NULL) {
		VkImportFenceFdInfoKHR import = {
		    .sType = VK_STRUCTURE_TYPE_IMPORT_FENCE_FD_INFO_KHR,
		    .fence = fence,
		    .flags = VK_FENCE_IMPORT_TEMPORARY_BIT,
		    .handleType = VK_EXTERNAL_FENCE_HANDLE_TYPE_SYNC_FD_BIT,
		    .fd = -1,
		};
		fence_done = dev->ImportFenceFdKHR(dev->device, &import) == VK_SUCCESS;
	}
	if (!semaphore_done || !fence_done) {
		// An empty submission on the application's queue signals them.
		static bool warned;
		if (!warned) {
			warned = true;
			LOG("signalling acquires through the application's queue");
		}
		VkSubmitInfo submit = {
		    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
		    .signalSemaphoreCount = semaphore_done ? 0 : 1,
		    .pSignalSemaphores = &semaphore,
		};
		ret = dev->queue == VK_NULL_HANDLE ? VK_ERROR_DEVICE_LOST
		                                   : dev->QueueSubmit(dev->queue, 1, &submit,
		                                                      fence_done ? VK_NULL_HANDLE : fence);
	}
	return ret;
}

static VkResult
sc_acquire(struct swapchain *sc, uint64_t timeout, VkSemaphore semaphore, VkFence fence, uint32_t *out_index)
{
	struct display *d = &g_display;
	struct timespec deadline = deadline_after(timeout);
	pthread_mutex_lock(&d->lock);
	int index = -1;
	VkResult ret = VK_SUCCESS;
	while (index < 0) {
		if (sc->lost) {
			ret = VK_ERROR_OUT_OF_DATE_KHR;
			break;
		}
		for (uint32_t i = 0; i < sc->count && index < 0; i++) {
			if (sc->imgs[i].state == IMAGE_FREE) {
				index = (int)i;
			}
		}
		if (index >= 0) {
			break;
		}
		if (timeout == 0) {
			ret = VK_NOT_READY;
			break;
		}
		if (pthread_cond_timedwait(&d->cond, &d->lock, &deadline) == ETIMEDOUT) {
			ret = VK_TIMEOUT;
			break;
		}
	}
	if (index >= 0) {
		sc->imgs[index].state = IMAGE_ACQUIRED;
		trace('A', 0, index);
	}
	pthread_mutex_unlock(&d->lock);
	if (index < 0) {
		return ret;
	}
	ret = signal_acquire(sc->dev, semaphore, fence);
	if (ret != VK_SUCCESS) {
		pthread_mutex_lock(&d->lock);
		sc->imgs[index].state = IMAGE_FREE;
		pthread_mutex_unlock(&d->lock);
		return ret;
	}
	*out_index = (uint32_t)index;
	return VK_SUCCESS;
}


/*
 *
 * Instance-level entry points: the display, its plane and the surface.
 *
 */

struct surface
{
	struct surface *next;
	VkInstance instance;
};

static struct surface *g_surfaces;

static bool
is_our_surface(VkSurfaceKHR handle)
{
	pthread_mutex_lock(&g_display.lock);
	struct surface *s = g_surfaces;
	while (s != NULL && (VkSurfaceKHR)(uintptr_t)s != handle) {
		s = s->next;
	}
	pthread_mutex_unlock(&g_display.lock);
	return s != NULL;
}

static VkDisplayPropertiesKHR
display_properties(void)
{
	return (VkDisplayPropertiesKHR){
	    .display = DISPLAY_HANDLE,
	    .displayName = "PICO 4",
	    .physicalDimensions = {92, 46},
	    .physicalResolution = {g_width, g_height},
	    .supportedTransforms = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR,
	    .planeReorderPossible = VK_FALSE,
	    .persistentContent = VK_FALSE,
	};
}

static VkDisplayModePropertiesKHR
mode_properties(void)
{
	return (VkDisplayModePropertiesKHR){
	    .displayMode = MODE_HANDLE,
	    .parameters = {.visibleRegion = {g_width, g_height}, .refreshRate = HZ * 1000},
	};
}

//! Fills a counted array with one element (Vulkan's two-call idiom).
#define ONE_ELEMENT(count, array, value)                                                                         \
	do {                                                                                                     \
		if ((array) == NULL) {                                                                           \
			*(count) = 1;                                                                            \
			return VK_SUCCESS;                                                                       \
		}                                                                                                \
		if (*(count) < 1) {                                                                              \
			return VK_INCOMPLETE;                                                                    \
		}                                                                                                \
		*(count) = 1;                                                                                    \
		(array)[0] = (value);                                                                            \
		return VK_SUCCESS;                                                                               \
	} while (0)

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceDisplayPropertiesKHR(VkPhysicalDevice pd, uint32_t *count, VkDisplayPropertiesKHR *props)
{
	DBG("vkGetPhysicalDeviceDisplayPropertiesKHR");
	if (!display_present()) {
		*count = 0;
		return VK_SUCCESS;
	}
	ONE_ELEMENT(count, props, display_properties());
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceDisplayProperties2KHR(VkPhysicalDevice pd, uint32_t *count, VkDisplayProperties2KHR *props)
{
	DBG("vkGetPhysicalDeviceDisplayProperties2KHR");
	if (!display_present()) {
		*count = 0;
		return VK_SUCCESS;
	}
	if (props == NULL) {
		*count = 1;
		return VK_SUCCESS;
	}
	if (*count < 1) {
		return VK_INCOMPLETE;
	}
	*count = 1;
	props[0].displayProperties = display_properties();
	return VK_SUCCESS;
}

static VkDisplayPlanePropertiesKHR
plane_properties(void)
{
	return (VkDisplayPlanePropertiesKHR){.currentDisplay = DISPLAY_HANDLE, .currentStackIndex = 0};
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceDisplayPlanePropertiesKHR(VkPhysicalDevice pd,
                                                 uint32_t *count,
                                                 VkDisplayPlanePropertiesKHR *props)
{
	DBG("vkGetPhysicalDeviceDisplayPlanePropertiesKHR");
	if (!display_present()) {
		*count = 0;
		return VK_SUCCESS;
	}
	ONE_ELEMENT(count, props, plane_properties());
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceDisplayPlaneProperties2KHR(VkPhysicalDevice pd,
                                                  uint32_t *count,
                                                  VkDisplayPlaneProperties2KHR *props)
{
	DBG("vkGetPhysicalDeviceDisplayPlaneProperties2KHR");
	if (!display_present()) {
		*count = 0;
		return VK_SUCCESS;
	}
	if (props == NULL) {
		*count = 1;
		return VK_SUCCESS;
	}
	if (*count < 1) {
		return VK_INCOMPLETE;
	}
	*count = 1;
	props[0].displayPlaneProperties = plane_properties();
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDisplayPlaneSupportedDisplaysKHR(VkPhysicalDevice pd, uint32_t plane, uint32_t *count, VkDisplayKHR *displays)
{
	DBG("vkGetDisplayPlaneSupportedDisplaysKHR(%u)", plane);
	if (plane != 0 || !display_present()) {
		*count = 0;
		return VK_SUCCESS;
	}
	ONE_ELEMENT(count, displays, DISPLAY_HANDLE);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDisplayModePropertiesKHR(VkPhysicalDevice pd,
                                  VkDisplayKHR display,
                                  uint32_t *count,
                                  VkDisplayModePropertiesKHR *props)
{
	DBG("vkGetDisplayModePropertiesKHR");
	if (display != DISPLAY_HANDLE) {
		*count = 0;
		return VK_SUCCESS;
	}
	ONE_ELEMENT(count, props, mode_properties());
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDisplayModeProperties2KHR(VkPhysicalDevice pd,
                                   VkDisplayKHR display,
                                   uint32_t *count,
                                   VkDisplayModeProperties2KHR *props)
{
	DBG("vkGetDisplayModeProperties2KHR");
	if (display != DISPLAY_HANDLE) {
		*count = 0;
		return VK_SUCCESS;
	}
	if (props == NULL) {
		*count = 1;
		return VK_SUCCESS;
	}
	if (*count < 1) {
		return VK_INCOMPLETE;
	}
	*count = 1;
	props[0].displayModeProperties = mode_properties();
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateDisplayModeKHR(VkPhysicalDevice pd,
                           VkDisplayKHR display,
                           const VkDisplayModeCreateInfoKHR *info,
                           const VkAllocationCallbacks *alloc,
                           VkDisplayModeKHR *mode)
{
	const VkDisplayModeParametersKHR *p = &info->parameters;
	LOG("vkCreateDisplayModeKHR %ux%u@%u mHz", p->visibleRegion.width, p->visibleRegion.height, p->refreshRate);
	if (display != DISPLAY_HANDLE || p->visibleRegion.width != g_width || p->visibleRegion.height != g_height ||
	    (p->refreshRate != 0 && (p->refreshRate + 500) / 1000 != HZ)) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	*mode = MODE_HANDLE;
	return VK_SUCCESS;
}

static VkDisplayPlaneCapabilitiesKHR
plane_capabilities(void)
{
	return (VkDisplayPlaneCapabilitiesKHR){
	    .supportedAlpha = VK_DISPLAY_PLANE_ALPHA_OPAQUE_BIT_KHR,
	    .minSrcExtent = {g_width, g_height},
	    .maxSrcExtent = {g_width, g_height},
	    .maxSrcPosition = {0, 0},
	    .minDstExtent = {g_width, g_height},
	    .maxDstExtent = {g_width, g_height},
	    .maxDstPosition = {0, 0},
	};
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDisplayPlaneCapabilitiesKHR(VkPhysicalDevice pd,
                                     VkDisplayModeKHR mode,
                                     uint32_t plane,
                                     VkDisplayPlaneCapabilitiesKHR *caps)
{
	DBG("vkGetDisplayPlaneCapabilitiesKHR");
	*caps = plane_capabilities();
	return mode == MODE_HANDLE && plane == 0 ? VK_SUCCESS : VK_ERROR_INITIALIZATION_FAILED;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDisplayPlaneCapabilities2KHR(VkPhysicalDevice pd,
                                      const VkDisplayPlaneInfo2KHR *info,
                                      VkDisplayPlaneCapabilities2KHR *caps)
{
	DBG("vkGetDisplayPlaneCapabilities2KHR");
	caps->capabilities = plane_capabilities();
	return info->mode == MODE_HANDLE && info->planeIndex == 0 ? VK_SUCCESS : VK_ERROR_INITIALIZATION_FAILED;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateDisplayPlaneSurfaceKHR(VkInstance instance,
                                   const VkDisplaySurfaceCreateInfoKHR *info,
                                   const VkAllocationCallbacks *alloc,
                                   VkSurfaceKHR *out)
{
	LOG("vkCreateDisplayPlaneSurfaceKHR: plane %u, extent %ux%u", info->planeIndex, info->imageExtent.width,
	    info->imageExtent.height);
	if (info->displayMode != MODE_HANDLE || info->planeIndex != 0) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	struct surface *s = calloc(1, sizeof(*s));
	if (s == NULL) {
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	s->instance = instance;
	pthread_mutex_lock(&g_display.lock);
	s->next = g_surfaces;
	g_surfaces = s;
	pthread_mutex_unlock(&g_display.lock);
	*out = (VkSurfaceKHR)(uintptr_t)s;
	return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
layer_DestroySurfaceKHR(VkInstance instance, VkSurfaceKHR surface, const VkAllocationCallbacks *alloc)
{
	if (surface == VK_NULL_HANDLE) {
		return;
	}
	if (!is_our_surface(surface)) {
		struct instance_data *inst = find_instance(instance);
		inst->DestroySurfaceKHR(instance, surface, alloc);
		return;
	}
	pthread_mutex_lock(&g_display.lock);
	for (struct surface **p = &g_surfaces; *p != NULL; p = &(*p)->next) {
		if ((VkSurfaceKHR)(uintptr_t)*p == surface) {
			struct surface *s = *p;
			*p = s->next;
			free(s);
			break;
		}
	}
	pthread_mutex_unlock(&g_display.lock);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfaceSupportKHR(VkPhysicalDevice pd, uint32_t family, VkSurfaceKHR surface, VkBool32 *supported)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(surface)) {
		return inst->GetPhysicalDeviceSurfaceSupportKHR(pd, family, surface, supported);
	}
	uint32_t n = 0;
	inst->GetPhysicalDeviceQueueFamilyProperties(pd, &n, NULL);
	VkQueueFamilyProperties families[16];
	n = n > 16 ? 16 : n;
	inst->GetPhysicalDeviceQueueFamilyProperties(pd, &n, families);
	*supported = family < n && (families[family].queueFlags & VK_QUEUE_GRAPHICS_BIT) != 0;
	return VK_SUCCESS;
}

static const VkFormat k_formats[] = {
    VK_FORMAT_B8G8R8A8_UNORM,
    VK_FORMAT_B8G8R8A8_SRGB,
    VK_FORMAT_R8G8B8A8_UNORM,
    VK_FORMAT_R8G8B8A8_SRGB,
};

/*!
 * Usages that every offered format supports as a linear image, and the
 * offered formats: those the panels' layers take that render as linear images.
 */
static VkImageUsageFlags
surface_usage(struct instance_data *inst, VkPhysicalDevice pd, VkFormat *formats, uint32_t *format_count)
{
	pthread_mutex_lock(&g_display.lock);
	display_probe(&g_display);
	bool fake = g_display.fake;
	pthread_mutex_unlock(&g_display.lock);
	VkImageUsageFlags usage = ~(VkImageUsageFlags)0;
	uint32_t n = 0;
	for (size_t i = 0; i < sizeof(k_formats) / sizeof(k_formats[0]); i++) {
		VkFormatProperties props;
		inst->GetPhysicalDeviceFormatProperties(pd, k_formats[i], &props);
		VkFormatFeatureFlags f = props.linearTilingFeatures;
		if ((f & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT) == 0) {
			continue;
		}
		VkImageUsageFlags u = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
		u |= (f & VK_FORMAT_FEATURE_TRANSFER_DST_BIT) ? VK_IMAGE_USAGE_TRANSFER_DST_BIT : 0;
		u |= (f & VK_FORMAT_FEATURE_TRANSFER_SRC_BIT) ? VK_IMAGE_USAGE_TRANSFER_SRC_BIT : 0;
		u |= (f & VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT) ? VK_IMAGE_USAGE_SAMPLED_BIT : 0;
		u |= (f & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT) ? VK_IMAGE_USAGE_STORAGE_BIT : 0;
		if (!fake) {
			// Must also import as a framebuffer: dma-buf exportable.
			VkPhysicalDeviceExternalImageFormatInfo external = {
			    .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
			    .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
			};
			VkPhysicalDeviceImageFormatInfo2 info = {
			    .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
			    .pNext = &external,
			    .format = k_formats[i],
			    .type = VK_IMAGE_TYPE_2D,
			    .tiling = VK_IMAGE_TILING_LINEAR,
			    .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT,
			};
			VkExternalImageFormatProperties external_props = {
			    .sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES,
			};
			VkImageFormatProperties2 image_props = {
			    .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
			    .pNext = &external_props,
			};
			if (inst->GetPhysicalDeviceImageFormatProperties2(pd, &info, &image_props) != VK_SUCCESS ||
			    image_props.imageFormatProperties.maxExtent.width < g_width ||
			    (external_props.externalMemoryProperties.externalMemoryFeatures &
			     VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT) == 0) {
				continue;
			}
		}
		usage &= u;
		formats[n++] = k_formats[i];
	}
	*format_count = n;
	return n > 0 ? usage : 0;
}

static VkSurfaceCapabilitiesKHR
surface_capabilities(struct instance_data *inst, VkPhysicalDevice pd)
{
	VkFormat formats[4];
	uint32_t n;
	return (VkSurfaceCapabilitiesKHR){
	    .minImageCount = 2,
	    .maxImageCount = MAX_IMAGES,
	    .currentExtent = {g_width, g_height},
	    .minImageExtent = {g_width, g_height},
	    .maxImageExtent = {g_width, g_height},
	    .maxImageArrayLayers = 1,
	    .supportedTransforms = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR,
	    .currentTransform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR,
	    .supportedCompositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
	    .supportedUsageFlags = surface_usage(inst, pd, formats, &n),
	};
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfaceCapabilitiesKHR(VkPhysicalDevice pd, VkSurfaceKHR surface, VkSurfaceCapabilitiesKHR *caps)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(surface)) {
		return inst->GetPhysicalDeviceSurfaceCapabilitiesKHR(pd, surface, caps);
	}
	*caps = surface_capabilities(inst, pd);
	DBG("vkGetPhysicalDeviceSurfaceCapabilitiesKHR: usage 0x%x", caps->supportedUsageFlags);
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfaceCapabilities2KHR(VkPhysicalDevice pd,
                                               const VkPhysicalDeviceSurfaceInfo2KHR *info,
                                               VkSurfaceCapabilities2KHR *caps)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(info->surface)) {
		return inst->GetPhysicalDeviceSurfaceCapabilities2KHR(pd, info, caps);
	}
	caps->surfaceCapabilities = surface_capabilities(inst, pd);
	for (VkBaseOutStructure *s = caps->pNext; s != NULL; s = s->pNext) {
		switch (s->sType) {
		case VK_STRUCTURE_TYPE_SURFACE_PROTECTED_CAPABILITIES_KHR:
			((VkSurfaceProtectedCapabilitiesKHR *)s)->supportsProtected = VK_FALSE;
			break;
		case VK_STRUCTURE_TYPE_SHARED_PRESENT_SURFACE_CAPABILITIES_KHR:
			((VkSharedPresentSurfaceCapabilitiesKHR *)s)->sharedPresentSupportedUsageFlags = 0;
			break;
		case VK_STRUCTURE_TYPE_SURFACE_PRESENT_MODE_COMPATIBILITY_EXT: {
			VkSurfacePresentModeCompatibilityEXT *c = (VkSurfacePresentModeCompatibilityEXT *)s;
			if (c->pPresentModes != NULL && c->presentModeCount > 0) {
				c->pPresentModes[0] = VK_PRESENT_MODE_FIFO_KHR;
			}
			c->presentModeCount = 1;
			break;
		}
		case VK_STRUCTURE_TYPE_SURFACE_PRESENT_SCALING_CAPABILITIES_EXT: {
			VkSurfacePresentScalingCapabilitiesEXT *c = (VkSurfacePresentScalingCapabilitiesEXT *)s;
			c->supportedPresentScaling = 0;
			c->supportedPresentGravityX = c->supportedPresentGravityY = 0;
			c->minScaledImageExtent = c->maxScaledImageExtent = (VkExtent2D){g_width, g_height};
			break;
		}
		default: DBG("surface capabilities: ignoring structure %d", s->sType); break;
		}
	}
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfaceCapabilities2EXT(VkPhysicalDevice pd, VkSurfaceKHR surface, VkSurfaceCapabilities2EXT *caps)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(surface)) {
		return inst->GetPhysicalDeviceSurfaceCapabilities2EXT(pd, surface, caps);
	}
	VkSurfaceCapabilitiesKHR c = surface_capabilities(inst, pd);
	caps->minImageCount = c.minImageCount;
	caps->maxImageCount = c.maxImageCount;
	caps->currentExtent = c.currentExtent;
	caps->minImageExtent = c.minImageExtent;
	caps->maxImageExtent = c.maxImageExtent;
	caps->maxImageArrayLayers = c.maxImageArrayLayers;
	caps->supportedTransforms = c.supportedTransforms;
	caps->currentTransform = c.currentTransform;
	caps->supportedCompositeAlpha = c.supportedCompositeAlpha;
	caps->supportedUsageFlags = c.supportedUsageFlags;
	caps->supportedSurfaceCounters = VK_SURFACE_COUNTER_VBLANK_BIT_EXT;
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfaceFormatsKHR(VkPhysicalDevice pd,
                                         VkSurfaceKHR surface,
                                         uint32_t *count,
                                         VkSurfaceFormatKHR *formats)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(surface)) {
		return inst->GetPhysicalDeviceSurfaceFormatsKHR(pd, surface, count, formats);
	}
	VkFormat list[4];
	uint32_t n;
	surface_usage(inst, pd, list, &n);
	if (formats == NULL) {
		*count = n;
		return VK_SUCCESS;
	}
	uint32_t written = *count < n ? *count : n;
	for (uint32_t i = 0; i < written; i++) {
		formats[i] = (VkSurfaceFormatKHR){list[i], VK_COLOR_SPACE_SRGB_NONLINEAR_KHR};
	}
	*count = written;
	return written < n ? VK_INCOMPLETE : VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfaceFormats2KHR(VkPhysicalDevice pd,
                                          const VkPhysicalDeviceSurfaceInfo2KHR *info,
                                          uint32_t *count,
                                          VkSurfaceFormat2KHR *formats)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(info->surface)) {
		return inst->GetPhysicalDeviceSurfaceFormats2KHR(pd, info, count, formats);
	}
	VkFormat list[4];
	uint32_t n;
	surface_usage(inst, pd, list, &n);
	if (formats == NULL) {
		*count = n;
		return VK_SUCCESS;
	}
	uint32_t written = *count < n ? *count : n;
	for (uint32_t i = 0; i < written; i++) {
		formats[i].surfaceFormat = (VkSurfaceFormatKHR){list[i], VK_COLOR_SPACE_SRGB_NONLINEAR_KHR};
	}
	*count = written;
	return written < n ? VK_INCOMPLETE : VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDeviceSurfacePresentModesKHR(VkPhysicalDevice pd,
                                              VkSurfaceKHR surface,
                                              uint32_t *count,
                                              VkPresentModeKHR *modes)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(surface)) {
		return inst->GetPhysicalDeviceSurfacePresentModesKHR(pd, surface, count, modes);
	}
	ONE_ELEMENT(count, modes, VK_PRESENT_MODE_FIFO_KHR);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetPhysicalDevicePresentRectanglesKHR(VkPhysicalDevice pd, VkSurfaceKHR surface, uint32_t *count, VkRect2D *rects)
{
	struct instance_data *inst = find_instance(pd);
	if (!is_our_surface(surface)) {
		return inst->GetPhysicalDevicePresentRectanglesKHR(pd, surface, count, rects);
	}
	ONE_ELEMENT(count, rects, ((VkRect2D){{0, 0}, {g_width, g_height}}));
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDrmDisplayEXT(VkPhysicalDevice pd, int32_t drm_fd, uint32_t connector, VkDisplayKHR *display)
{
	*display = VK_NULL_HANDLE;
	if (!display_present()) {
		LOG("vkGetDrmDisplayEXT(connector %u): no panels", connector);
		return VK_SUCCESS;
	}
	drmModeConnector *c = drmModeGetConnector(drm_fd, connector);
	bool dsi = c != NULL && c->connector_type == DRM_MODE_CONNECTOR_DSI;
	drmModeFreeConnector(c);
	if (dsi || g_display.fake) {
		*display = DISPLAY_HANDLE;
	}
	LOG("vkGetDrmDisplayEXT(connector %u): %s", connector, *display != VK_NULL_HANDLE ? "the panels" : "none");
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_AcquireDrmDisplayEXT(VkPhysicalDevice pd, int32_t drm_fd, VkDisplayKHR display)
{
	if (display != DISPLAY_HANDLE) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	struct display *d = &g_display;
	pthread_mutex_lock(&d->lock);
	bool ok = display_probe(d);
	if (ok && !d->fake) {
		int fd = fcntl(drm_fd, F_DUPFD_CLOEXEC, 3);
		ok = fd >= 0 && kms_take_fd(d, fd);
	}
	if (ok) {
		vblank_start(d);
	}
	pthread_mutex_unlock(&d->lock);
	LOG("vkAcquireDrmDisplayEXT(fd %d): %s", drm_fd, ok ? "acquired" : "failed");
	return ok ? VK_SUCCESS : VK_ERROR_INITIALIZATION_FAILED;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_ReleaseDisplayEXT(VkPhysicalDevice pd, VkDisplayKHR display)
{
	if (display != DISPLAY_HANDLE) {
		struct instance_data *inst = find_instance(pd);
		return inst->ReleaseDisplayEXT(pd, display);
	}
	LOG("vkReleaseDisplayEXT");
	return VK_SUCCESS;
}

// VK_EXT_acquire_xlib_display without the Xlib headers: Display * and RROutput.
static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetRandROutputDisplayEXT(VkPhysicalDevice pd, void *dpy, unsigned long output, VkDisplayKHR *display)
{
	// The panels are not an X output; SteamVR then tries the next way to its display.
	LOG("vkGetRandROutputDisplayEXT(0x%lx): none", output);
	*display = VK_NULL_HANDLE;
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_AcquireXlibDisplayEXT(VkPhysicalDevice pd, void *dpy, VkDisplayKHR display)
{
	if (display == DISPLAY_HANDLE) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	struct instance_data *inst = find_instance(pd);
	typedef VkResult(VKAPI_PTR * acquire_fn)(VkPhysicalDevice, void *, VkDisplayKHR);
	return inst->AcquireXlibDisplayEXT != NULL ? ((acquire_fn)inst->AcquireXlibDisplayEXT)(pd, dpy, display)
	                                           : VK_ERROR_INITIALIZATION_FAILED;
}


/*
 *
 * Device-level entry points: swapchains, presentation, display control, fences.
 *
 */

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateSwapchainKHR(VkDevice device,
                         const VkSwapchainCreateInfoKHR *info,
                         const VkAllocationCallbacks *alloc,
                         VkSwapchainKHR *swapchain)
{
	struct device_data *dev = find_device(device);
	if (!is_our_surface(info->surface)) {
		return dev->CreateSwapchainKHR(device, info, alloc, swapchain);
	}
	return sc_create(dev, info, swapchain);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateSharedSwapchainsKHR(VkDevice device,
                                uint32_t count,
                                const VkSwapchainCreateInfoKHR *infos,
                                const VkAllocationCallbacks *alloc,
                                VkSwapchainKHR *swapchains)
{
	struct device_data *dev = find_device(device);
	bool ours = false;
	for (uint32_t i = 0; i < count; i++) {
		ours = ours || is_our_surface(infos[i].surface);
	}
	if (!ours) {
		return dev->CreateSharedSwapchainsKHR(device, count, infos, alloc, swapchains);
	}
	for (uint32_t i = 0; i < count; i++) {
		VkResult ret = is_our_surface(infos[i].surface) ? sc_create(dev, &infos[i], &swapchains[i])
		                                                : dev->CreateSwapchainKHR(device, &infos[i], alloc,
		                                                                          &swapchains[i]);
		if (ret != VK_SUCCESS) {
			return ret;
		}
	}
	return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
layer_DestroySwapchainKHR(VkDevice device, VkSwapchainKHR swapchain, const VkAllocationCallbacks *alloc)
{
	if (swapchain == VK_NULL_HANDLE) {
		return;
	}
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		dev->DestroySwapchainKHR(device, swapchain, alloc);
		return;
	}
	sc_destroy((struct swapchain *)(uintptr_t)swapchain);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetSwapchainImagesKHR(VkDevice device, VkSwapchainKHR swapchain, uint32_t *count, VkImage *images)
{
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->GetSwapchainImagesKHR(device, swapchain, count, images);
	}
	struct swapchain *sc = (struct swapchain *)(uintptr_t)swapchain;
	if (images == NULL) {
		*count = sc->count;
		return VK_SUCCESS;
	}
	uint32_t n = *count < sc->count ? *count : sc->count;
	for (uint32_t i = 0; i < n; i++) {
		images[i] = sc->imgs[i].image;
	}
	*count = n;
	return n < sc->count ? VK_INCOMPLETE : VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_AcquireNextImageKHR(VkDevice device,
                          VkSwapchainKHR swapchain,
                          uint64_t timeout,
                          VkSemaphore semaphore,
                          VkFence fence,
                          uint32_t *index)
{
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->AcquireNextImageKHR(device, swapchain, timeout, semaphore, fence, index);
	}
	return sc_acquire((struct swapchain *)(uintptr_t)swapchain, timeout, semaphore, fence, index);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_AcquireNextImage2KHR(VkDevice device, const VkAcquireNextImageInfoKHR *info, uint32_t *index)
{
	if (!is_our_swapchain(info->swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->AcquireNextImage2KHR(device, info, index);
	}
	return sc_acquire((struct swapchain *)(uintptr_t)info->swapchain, info->timeout, info->semaphore, info->fence,
	                  index);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_QueuePresentKHR(VkQueue queue, const VkPresentInfoKHR *info)
{
	struct device_data *dev = find_device(queue);
	// Present ids (VK_KHR_present_id and _id2 have the same layout) and present fences.
	const VkPresentIdKHR *ids = NULL;
	const VkSwapchainPresentFenceInfoEXT *fences = NULL;
	for (const VkBaseInStructure *s = info->pNext; s != NULL; s = s->pNext) {
		if (s->sType == VK_STRUCTURE_TYPE_PRESENT_ID_KHR || s->sType == VK_STRUCTURE_TYPE_PRESENT_ID_2_KHR) {
			ids = (const VkPresentIdKHR *)s;
		} else if (s->sType == VK_STRUCTURE_TYPE_SWAPCHAIN_PRESENT_FENCE_INFO_EXT) {
			fences = (const VkSwapchainPresentFenceInfoEXT *)s;
		}
	}

	uint32_t ours = 0;
	for (uint32_t i = 0; i < info->swapchainCount; i++) {
		ours += is_our_swapchain(info->pSwapchains[i]) ? 1 : 0;
	}
	if (ours == 0) {
		return dev->QueuePresentKHR(queue, info);
	}

	VkResult result = VK_SUCCESS;
	bool waited = false;
	for (uint32_t i = 0; i < info->swapchainCount; i++) {
		if (!is_our_swapchain(info->pSwapchains[i])) {
			continue;
		}
		struct swapchain *sc = (struct swapchain *)(uintptr_t)info->pSwapchains[i];
		uint32_t index = info->pImageIndices[i];
		struct sc_image *img = &sc->imgs[index];
		VkResult ret = VK_SUCCESS;
		if (index >= sc->count || img->state != IMAGE_ACQUIRED) {
			ret = VK_ERROR_OUT_OF_DATE_KHR;
		}
		if (ret == VK_SUCCESS) {
			// The wait semaphores, once, then this image's fence.
			uint32_t waits = waited ? 0 : info->waitSemaphoreCount;
			VkPipelineStageFlags *stages = calloc(waits > 0 ? waits : 1, sizeof(*stages));
			if (stages == NULL) {
				return VK_ERROR_OUT_OF_HOST_MEMORY;
			}
			for (uint32_t w = 0; w < waits; w++) {
				stages[w] = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
			}
			// With compressed scanout the conversion into the scanout image runs here too,
			// for the dumped present with a copy the CPU can read.
			VkCommandBuffer copy = img->copy;
			if (img->copy_dump != VK_NULL_HANDLE && sc->stats.presents + 1 == sc->dump_present) {
				copy = img->copy_dump;
				img->readback = true;
			}
			VkSubmitInfo submit = {
			    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
			    .waitSemaphoreCount = waits,
			    .pWaitSemaphores = info->pWaitSemaphores,
			    .pWaitDstStageMask = stages,
			    .commandBufferCount = copy != VK_NULL_HANDLE ? 1 : 0,
			    .pCommandBuffers = &copy,
			};
			dev->ResetFences(dev->device, 1, &img->fence);
			ret = dev->QueueSubmit(queue, 1, &submit, img->fence);
			free(stages);
			waited = true;
			// A present fence (swapchain maintenance) signals once the semaphores are consumed.
			if (ret == VK_SUCCESS && fences != NULL && i < fences->swapchainCount &&
			    fences->pFences[i] != VK_NULL_HANDLE) {
				VkSubmitInfo empty = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO};
				ret = dev->QueueSubmit(queue, 1, &empty, fences->pFences[i]);
			}
		}
		if (ret == VK_SUCCESS) {
			pthread_mutex_lock(&g_display.lock);
			ret = sc->lost ? VK_ERROR_OUT_OF_DATE_KHR : VK_SUCCESS;
			if (ret == VK_SUCCESS) {
				img->state = IMAGE_QUEUED;
				img->present_id = ids != NULL && i < ids->swapchainCount && ids->pPresentIds != NULL
				                      ? ids->pPresentIds[i]
				                      : 0;
				sc->queue[sc->queued++] = index;
				sc->stats.presents++;
				trace('Q', img->present_id, (int64_t)index);
			} else {
				img->state = IMAGE_FREE;
			}
			pthread_mutex_unlock(&g_display.lock);
			uint64_t one = 1;
			(void)!write(sc->wake, &one, sizeof(one));
		}
		if (info->pResults != NULL) {
			info->pResults[i] = ret;
		}
		if (ret != VK_SUCCESS) {
			result = ret;
		}
	}

	if (ours < info->swapchainCount) {
		// Other swapchains in the same call: their semaphores were waited for above.
		VkSwapchainKHR others[16];
		uint32_t indices[16];
		VkResult results[16];
		uint32_t n = 0;
		for (uint32_t i = 0; i < info->swapchainCount && n < 16; i++) {
			if (!is_our_swapchain(info->pSwapchains[i])) {
				others[n] = info->pSwapchains[i];
				indices[n++] = info->pImageIndices[i];
			}
		}
		VkPresentInfoKHR rest = {
		    .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
		    .swapchainCount = n,
		    .pSwapchains = others,
		    .pImageIndices = indices,
		    .pResults = results,
		};
		VkResult ret = dev->QueuePresentKHR(queue, &rest);
		for (uint32_t i = 0, j = 0; i < info->swapchainCount && info->pResults != NULL; i++) {
			if (!is_our_swapchain(info->pSwapchains[i]) && j < n) {
				info->pResults[i] = results[j++];
			}
		}
		if (ret != VK_SUCCESS && result == VK_SUCCESS) {
			result = ret;
		}
	}
	return result;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetSwapchainStatusKHR(VkDevice device, VkSwapchainKHR swapchain)
{
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->GetSwapchainStatusKHR(device, swapchain);
	}
	struct swapchain *sc = (struct swapchain *)(uintptr_t)swapchain;
	return sc->lost ? VK_ERROR_OUT_OF_DATE_KHR : VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_ReleaseSwapchainImagesEXT(VkDevice device, const VkReleaseSwapchainImagesInfoEXT *info)
{
	if (!is_our_swapchain(info->swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->ReleaseSwapchainImagesEXT(device, info);
	}
	struct swapchain *sc = (struct swapchain *)(uintptr_t)info->swapchain;
	pthread_mutex_lock(&g_display.lock);
	for (uint32_t i = 0; i < info->imageIndexCount; i++) {
		uint32_t index = info->pImageIndices[i];
		if (index < sc->count && sc->imgs[index].state == IMAGE_ACQUIRED) {
			sc->imgs[index].state = IMAGE_FREE;
		}
	}
	pthread_cond_broadcast(&g_display.cond);
	pthread_mutex_unlock(&g_display.lock);
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_WaitForPresentKHR(VkDevice device, VkSwapchainKHR swapchain, uint64_t present_id, uint64_t timeout)
{
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->WaitForPresentKHR(device, swapchain, present_id, timeout);
	}
	struct swapchain *sc = (struct swapchain *)(uintptr_t)swapchain;
	struct timespec deadline = deadline_after(timeout);
	VkResult ret = VK_SUCCESS;
	pthread_mutex_lock(&g_display.lock);
	trace('W', present_id, timeout > (uint64_t)INT32_MAX * 1000 ? INT32_MAX : (int64_t)(timeout / 1000));
	sc->stats.present_waits++;
	uint64_t lag = present_id > sc->presented_id ? present_id - sc->presented_id : 0;
	if (lag > sc->stats.max_present_wait_lag) {
		sc->stats.max_present_wait_lag = lag;
	}
	while (sc->presented_id < present_id) {
		if (sc->lost) {
			ret = VK_ERROR_OUT_OF_DATE_KHR;
			break;
		}
		if (timeout == 0 || pthread_cond_timedwait(&g_display.cond, &g_display.lock, &deadline) == ETIMEDOUT) {
			ret = VK_TIMEOUT;
			if (sc->stats.present_wait_timeouts++ < 5) {
				LOG("present %" PRIu64 " not shown within %.3f ms (shown: %" PRIu64 ", queued: %u)", present_id,
				    timeout / 1e6, sc->presented_id, sc->queued);
			}
			break;
		}
	}
	trace('w', present_id, ret);
	pthread_mutex_unlock(&g_display.lock);
	return ret;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_WaitForPresent2KHR(VkDevice device, VkSwapchainKHR swapchain, const VkPresentWait2InfoKHR *info)
{
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->WaitForPresent2KHR(device, swapchain, info);
	}
	return layer_WaitForPresentKHR(device, swapchain, info->presentId, info->timeout);
}

static VKAPI_ATTR void VKAPI_CALL
layer_SetHdrMetadataEXT(VkDevice device, uint32_t count, const VkSwapchainKHR *swapchains, const VkHdrMetadataEXT *metadata)
{
	// The panels are SDR: drop this layer's swapchains, pass the others on.
	struct device_data *dev = find_device(device);
	VkSwapchainKHR others[16];
	VkHdrMetadataEXT others_metadata[16];
	uint32_t n = 0;
	for (uint32_t i = 0; i < count && n < 16; i++) {
		if (!is_our_swapchain(swapchains[i])) {
			others[n] = swapchains[i];
			others_metadata[n++] = metadata[i];
		}
	}
	if (n > 0) {
		dev->SetHdrMetadataEXT(device, n, others, others_metadata);
	}
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetDeviceGroupSurfacePresentModesKHR(VkDevice device, VkSurfaceKHR surface, VkDeviceGroupPresentModeFlagsKHR *modes)
{
	if (!is_our_surface(surface)) {
		struct device_data *dev = find_device(device);
		return dev->GetDeviceGroupSurfacePresentModesKHR(device, surface, modes);
	}
	*modes = VK_DEVICE_GROUP_PRESENT_MODE_LOCAL_BIT_KHR;
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetSwapchainCounterEXT(VkDevice device, VkSwapchainKHR swapchain, VkSurfaceCounterFlagBitsEXT counter, uint64_t *value)
{
	if (!is_our_swapchain(swapchain)) {
		struct device_data *dev = find_device(device);
		return dev->GetSwapchainCounterEXT(device, swapchain, counter, value);
	}
	pthread_mutex_lock(&g_display.lock);
	uint64_t count = g_display.vblank_count;
	*value = g_display.vblank_sequence;
	pthread_mutex_unlock(&g_display.lock);
	DBG("vkGetSwapchainCounterEXT: %" PRIu64, *value);
	return count > 0 ? VK_SUCCESS : VK_ERROR_OUT_OF_DATE_KHR;
}

static VkResult
register_event_fence(struct device_data *dev, bool vblank, VkFence *out)
{
	// A real fence keeps the handle unique; this layer answers waits on it.
	VkFenceCreateInfo info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
	VkResult ret = dev->CreateFence(dev->device, &info, NULL, out);
	if (ret != VK_SUCCESS) {
		return ret;
	}
	struct event_fence *e = calloc(1, sizeof(*e));
	if (e == NULL) {
		dev->DestroyFence(dev->device, *out, NULL);
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	e->device = dev->device;
	e->fence = *out;
	e->vblank = vblank;
	DBG("event fence %p registered (vblank %d)", (void *)(uintptr_t)*out, vblank);
	pthread_mutex_lock(&g_display.lock);
	e->next = g_display.events;
	g_display.events = e;
	pthread_mutex_unlock(&g_display.lock);
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_RegisterDisplayEventEXT(VkDevice device,
                              VkDisplayKHR display,
                              const VkDisplayEventInfoEXT *info,
                              const VkAllocationCallbacks *alloc,
                              VkFence *fence)
{
	struct device_data *dev = find_device(device);
	if (display != DISPLAY_HANDLE) {
		return dev->RegisterDisplayEventEXT(device, display, info, alloc, fence);
	}
	DBG("vkRegisterDisplayEventEXT");
	pthread_mutex_lock(&g_display.lock);
	vblank_start(&g_display);
	pthread_mutex_unlock(&g_display.lock);
	return register_event_fence(dev, info->displayEvent == VK_DISPLAY_EVENT_TYPE_FIRST_PIXEL_OUT_EXT, fence);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_RegisterDeviceEventEXT(VkDevice device,
                             const VkDeviceEventInfoEXT *info,
                             const VkAllocationCallbacks *alloc,
                             VkFence *fence)
{
	// Hotplug: the panels never come or go. A fence that never signals.
	struct device_data *dev = find_device(device);
	if (!display_present()) {
		return dev->RegisterDeviceEventEXT(device, info, alloc, fence);
	}
	return register_event_fence(dev, false, fence);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_DisplayPowerControlEXT(VkDevice device, VkDisplayKHR display, const VkDisplayPowerInfoEXT *info)
{
	if (display != DISPLAY_HANDLE) {
		struct device_data *dev = find_device(device);
		return dev->DisplayPowerControlEXT(device, display, info);
	}
	// The panels stay on while presenting; SteamVR's standby only dims its content.
	LOG("vkDisplayPowerControlEXT(%d): ignored", info->powerState);
	return VK_SUCCESS;
}

//! Locked. The event fence for @p fence, or NULL.
static struct event_fence *
find_event(VkDevice device, VkFence fence)
{
	for (struct event_fence *e = g_display.events; e != NULL; e = e->next) {
		if (e->device == device && e->fence == fence) {
			return e;
		}
	}
	return NULL;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_WaitForFences(VkDevice device, uint32_t count, const VkFence *fences, VkBool32 wait_all, uint64_t timeout)
{
	struct device_data *dev = find_device(device);
	pthread_mutex_lock(&g_display.lock);
	uint32_t ours = 0;
	for (uint32_t i = 0; i < count; i++) {
		ours += find_event(device, fences[i]) != NULL ? 1 : 0;
	}
	pthread_mutex_unlock(&g_display.lock);
	if (ours == 0) {
		return dev->WaitForFences(device, count, fences, wait_all, timeout);
	}

	VkFence others[64];
	uint32_t n = 0;
	struct timespec deadline = deadline_after(timeout);
	int64_t start = now_ns();
	int64_t end = timeout > (uint64_t)INT64_MAX / 2 ? INT64_MAX : start + (int64_t)timeout;
	pthread_mutex_lock(&g_display.lock);
	for (uint32_t i = 0; i < count && n < 64; i++) {
		if (find_event(device, fences[i]) == NULL) {
			others[n++] = fences[i];
		}
	}
	VkResult ret = VK_TIMEOUT;
	for (;;) {
		uint32_t done = 0;
		for (uint32_t i = 0; i < count; i++) {
			struct event_fence *e = find_event(device, fences[i]);
			done += e != NULL && e->signaled ? 1 : 0;
		}
		bool others_done = n == 0;
		if (n > 0 && (wait_all || done == 0)) {
			// Driver fences: polled, as they cannot wake this condition.
			pthread_mutex_unlock(&g_display.lock);
			others_done = dev->WaitForFences(device, n, others, wait_all, 0) == VK_SUCCESS;
			pthread_mutex_lock(&g_display.lock);
		}
		if (wait_all ? (done == ours && others_done) : (done > 0 || (n > 0 && others_done))) {
			ret = VK_SUCCESS;
			break;
		}
		if (timeout == 0 || now_ns() >= end) {
			break;
		}
		struct timespec wait = deadline;
		if (n > 0) {
			struct timespec soon = deadline_after(1000000);
			if (soon.tv_sec < wait.tv_sec || (soon.tv_sec == wait.tv_sec && soon.tv_nsec < wait.tv_nsec)) {
				wait = soon;
			}
		}
		pthread_cond_timedwait(&g_display.cond, &g_display.lock, &wait);
	}
	pthread_mutex_unlock(&g_display.lock);
	DBG("vkWaitForFences: %u fences, %u events, all %u, timeout %.1f ms: %d after %.2f ms (vblank %" PRIu64 ")",
	    count, ours, wait_all, timeout / 1e6, ret, (now_ns() - start) / 1e6,
	    g_display.vblank_sequence);
	return ret;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_GetFenceStatus(VkDevice device, VkFence fence)
{
	pthread_mutex_lock(&g_display.lock);
	struct event_fence *e = find_event(device, fence);
	bool ours = e != NULL, signaled = ours && e->signaled;
	pthread_mutex_unlock(&g_display.lock);
	if (!ours) {
		struct device_data *dev = find_device(device);
		return dev->GetFenceStatus(device, fence);
	}
	DBG("vkGetFenceStatus(event %p): %d", (void *)(uintptr_t)fence, signaled);
	return signaled ? VK_SUCCESS : VK_NOT_READY;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_ResetFences(VkDevice device, uint32_t count, const VkFence *fences)
{
	struct device_data *dev = find_device(device);
	VkFence others[64];
	uint32_t n = 0;
	pthread_mutex_lock(&g_display.lock);
	for (uint32_t i = 0; i < count; i++) {
		struct event_fence *e = find_event(device, fences[i]);
		if (e != NULL) {
			e->signaled = false;
			DBG("vkResetFences(event %p)", (void *)(uintptr_t)fences[i]);
		} else if (n < 64) {
			others[n++] = fences[i];
		}
	}
	pthread_mutex_unlock(&g_display.lock);
	if (n == count) {
		return dev->ResetFences(device, count, fences);
	}
	return n > 0 ? dev->ResetFences(device, n, others) : VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
layer_DestroyFence(VkDevice device, VkFence fence, const VkAllocationCallbacks *alloc)
{
	struct device_data *dev = find_device(device);
	pthread_mutex_lock(&g_display.lock);
	for (struct event_fence **p = &g_display.events; *p != NULL; p = &(*p)->next) {
		if ((*p)->device == device && (*p)->fence == fence) {
			struct event_fence *e = *p;
			*p = e->next;
			free(e);
			DBG("vkDestroyFence(event %p)", (void *)(uintptr_t)fence);
			break;
		}
	}
	pthread_mutex_unlock(&g_display.lock);
	dev->DestroyFence(device, fence, alloc);
}

static VKAPI_ATTR void VKAPI_CALL
layer_GetDeviceQueue(VkDevice device, uint32_t family, uint32_t index, VkQueue *queue)
{
	struct device_data *dev = find_device(device);
	dev->GetDeviceQueue(device, family, index, queue);
	if (dev->queue == VK_NULL_HANDLE) {
		dev->queue = *queue;
	}
}

static VKAPI_ATTR void VKAPI_CALL
layer_GetDeviceQueue2(VkDevice device, const VkDeviceQueueInfo2 *info, VkQueue *queue)
{
	struct device_data *dev = find_device(device);
	dev->GetDeviceQueue2(device, info, queue);
	if (dev->queue == VK_NULL_HANDLE) {
		dev->queue = *queue;
	}
}


/*
 *
 * Instance and device creation, proc addresses.
 *
 */

static bool
has_extension(const VkDeviceCreateInfo *info, const char *name)
{
	for (uint32_t i = 0; i < info->enabledExtensionCount; i++) {
		if (strcmp(info->ppEnabledExtensionNames[i], name) == 0) {
			return true;
		}
	}
	return false;
}

// Extensions this layer implements itself (the manifest declares them too).
static const char *const k_instance_extensions[] = {
    VK_KHR_DISPLAY_EXTENSION_NAME,
    VK_KHR_GET_DISPLAY_PROPERTIES_2_EXTENSION_NAME,
    VK_EXT_DIRECT_MODE_DISPLAY_EXTENSION_NAME,
    VK_EXT_ACQUIRE_DRM_DISPLAY_EXTENSION_NAME,
    VK_EXT_DISPLAY_SURFACE_COUNTER_EXTENSION_NAME,
};
static const VkExtensionProperties k_device_extension = {VK_EXT_DISPLAY_CONTROL_EXTENSION_NAME, 1};
/*
 * gamescope needs the GPU's DRM nodes (VK_EXT_physical_device_drm). KGSL is not
 * a DRM device; msm_drm (the display, whose render node imports the GPU's
 * dma-bufs) stands in for it.
 */
static const VkExtensionProperties k_drm_extension = {VK_EXT_PHYSICAL_DEVICE_DRM_EXTENSION_NAME, 1};
#define RENDER_NODE "/dev/dri/renderD128"

static bool
in_list(const char *name, const char *const *list, size_t count)
{
	for (size_t i = 0; i < count; i++) {
		if (strcmp(name, list[i]) == 0) {
			return true;
		}
	}
	return false;
}

/*!
 * The enabled extensions minus those in @p ours, for a driver that lacks them:
 * this layer implements them on its own. Returns the new count.
 */
static uint32_t
without_ours(const char *const *names, uint32_t count, const char *const *ours, size_t ours_count, const char **out)
{
	uint32_t n = 0;
	for (uint32_t i = 0; i < count && n < 64; i++) {
		if (!in_list(names[i], ours, ours_count)) {
			out[n++] = names[i];
		}
	}
	return n;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateInstance(const VkInstanceCreateInfo *info, const VkAllocationCallbacks *alloc, VkInstance *instance)
{
	VkLayerInstanceCreateInfo *chain = (VkLayerInstanceCreateInfo *)info->pNext;
	while (chain != NULL && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO &&
	                          chain->function == VK_LAYER_LINK_INFO)) {
		chain = (VkLayerInstanceCreateInfo *)chain->pNext;
	}
	if (chain == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
	VkLayerInstanceLink *next = chain->u.pLayerInfo->pNext;
	chain->u.pLayerInfo = next;
	PFN_vkCreateInstance create = (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
	VkResult ret = create(info, alloc, instance);
	if (ret == VK_ERROR_EXTENSION_NOT_PRESENT && info->enabledExtensionCount <= 64) {
		// The driver lacks some of the display extensions: they are this layer's anyway.
		const char *names[64];
		VkInstanceCreateInfo copy = *info;
		copy.enabledExtensionCount =
		    without_ours(info->ppEnabledExtensionNames, info->enabledExtensionCount, k_instance_extensions,
		                 sizeof(k_instance_extensions) / sizeof(k_instance_extensions[0]), names);
		copy.ppEnabledExtensionNames = names;
		chain->u.pLayerInfo = next;
		ret = create(&copy, alloc, instance);
		LOG("the driver lacks display extensions; the layer provides them (%d)", ret);
	}
	if (ret != VK_SUCCESS) {
		return ret;
	}

	struct instance_data *d = calloc(1, sizeof(*d));
	if (d == NULL) {
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	d->instance = *instance;
	d->key = dispatch_key(*instance);
	d->gipa = gipa;
#define I(name) d->name = (PFN_vk##name)gipa(*instance, "vk" #name)
	I(DestroyInstance);
	I(CreateDevice);
	I(EnumerateDeviceExtensionProperties);
	I(GetPhysicalDeviceMemoryProperties);
	I(GetPhysicalDeviceFormatProperties);
	I(GetPhysicalDeviceImageFormatProperties2);
	I(GetPhysicalDeviceQueueFamilyProperties);
	I(DestroySurfaceKHR);
	I(GetPhysicalDeviceSurfaceSupportKHR);
	I(GetPhysicalDeviceSurfaceCapabilitiesKHR);
	I(GetPhysicalDeviceSurfaceCapabilities2KHR);
	I(GetPhysicalDeviceSurfaceCapabilities2EXT);
	I(GetPhysicalDeviceSurfaceFormatsKHR);
	I(GetPhysicalDeviceSurfaceFormats2KHR);
	I(GetPhysicalDeviceSurfacePresentModesKHR);
	I(GetPhysicalDevicePresentRectanglesKHR);
	I(ReleaseDisplayEXT);
#undef I
	d->AcquireXlibDisplayEXT = gipa(*instance, "vkAcquireXlibDisplayEXT");
	d->GetPhysicalDeviceProperties2 =
	    (PFN_vkGetPhysicalDeviceProperties2)gipa(*instance, "vkGetPhysicalDeviceProperties2");
	if (d->GetPhysicalDeviceProperties2 == NULL) {
		d->GetPhysicalDeviceProperties2 =
		    (PFN_vkGetPhysicalDeviceProperties2)gipa(*instance, "vkGetPhysicalDeviceProperties2KHR");
	}
	if (d->GetPhysicalDeviceImageFormatProperties2 == NULL) {
		d->GetPhysicalDeviceImageFormatProperties2 =
		    (PFN_vkGetPhysicalDeviceImageFormatProperties2)gipa(*instance, "vkGetPhysicalDeviceImageFormatProperties2KHR");
	}
	pthread_mutex_lock(&g_objects);
	d->next = g_instances;
	g_instances = d;
	pthread_mutex_unlock(&g_objects);
	vrsync_instance_created(*instance, gipa);
	DBG("instance created");
	return VK_SUCCESS;
}

//! The driver's properties, with msm_drm's nodes as the GPU's DRM nodes where the driver has none.
static VKAPI_ATTR void VKAPI_CALL
layer_GetPhysicalDeviceProperties2(VkPhysicalDevice pd, VkPhysicalDeviceProperties2 *props)
{
	struct instance_data *inst = find_instance(pd);
	inst->GetPhysicalDeviceProperties2(pd, props);
	for (VkBaseOutStructure *s = props->pNext; s != NULL; s = s->pNext) {
		if (s->sType != VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRM_PROPERTIES_EXT) {
			continue;
		}
		VkPhysicalDeviceDrmPropertiesEXT *drm = (VkPhysicalDeviceDrmPropertiesEXT *)s;
		struct stat primary, render;
		if (drm->hasPrimary || drm->hasRender || !display_present() || g_display.fake ||
		    stat(KMS_DEVICE, &primary) != 0) {
			continue;
		}
		drm->hasPrimary = VK_TRUE;
		drm->primaryMajor = major(primary.st_rdev);
		drm->primaryMinor = minor(primary.st_rdev);
		if (stat(RENDER_NODE, &render) == 0) {
			drm->hasRender = VK_TRUE;
			drm->renderMajor = major(render.st_rdev);
			drm->renderMinor = minor(render.st_rdev);
		}
		DBG("DRM properties: primary %" PRId64 ":%" PRId64 ", render %d", drm->primaryMajor, drm->primaryMinor,
		    drm->hasRender);
	}
}

//! The driver's device extensions plus VK_EXT_display_control, which this layer implements.
static VKAPI_ATTR VkResult VKAPI_CALL
layer_EnumerateDeviceExtensionProperties(VkPhysicalDevice pd,
                                         const char *layer,
                                         uint32_t *count,
                                         VkExtensionProperties *props)
{
	if (layer != NULL && strcmp(layer, LAYER_NAME) == 0) {
		ONE_ELEMENT(count, props, k_device_extension);
	}
	struct instance_data *inst = find_instance(pd);
	if (layer != NULL) {
		return inst->EnumerateDeviceExtensionProperties(pd, layer, count, props);
	}
	uint32_t n = 0;
	VkResult ret = inst->EnumerateDeviceExtensionProperties(pd, NULL, &n, NULL);
	if (ret != VK_SUCCESS) {
		return ret;
	}
	VkExtensionProperties *all = calloc(n + 2, sizeof(*all));
	if (all == NULL) {
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	ret = inst->EnumerateDeviceExtensionProperties(pd, NULL, &n, all);
	bool control = false, drm = false;
	for (uint32_t i = 0; i < n; i++) {
		control = control || strcmp(all[i].extensionName, k_device_extension.extensionName) == 0;
		drm = drm || strcmp(all[i].extensionName, k_drm_extension.extensionName) == 0;
	}
	if (!control && display_present()) {
		all[n++] = k_device_extension;
	}
	if (!drm && display_present() && !g_display.fake) {
		all[n++] = k_drm_extension;
	}
	if (ret == VK_SUCCESS || ret == VK_INCOMPLETE) {
		ret = VK_SUCCESS;
		if (props == NULL) {
			*count = n;
		} else {
			uint32_t written = *count < n ? *count : n;
			memcpy(props, all, written * sizeof(*props));
			ret = written < n ? VK_INCOMPLETE : VK_SUCCESS;
			*count = written;
		}
	}
	free(all);
	return ret;
}

static VKAPI_ATTR void VKAPI_CALL
layer_DestroyInstance(VkInstance instance, const VkAllocationCallbacks *alloc)
{
	struct instance_data *d = find_instance(instance);
	if (d == NULL) {
		return;
	}
	pthread_mutex_lock(&g_objects);
	for (struct instance_data **p = &g_instances; *p != NULL; p = &(*p)->next) {
		if (*p == d) {
			*p = d->next;
			break;
		}
	}
	bool last = g_instances == NULL;
	pthread_mutex_unlock(&g_objects);
	vrsync_instance_destroyed(instance);
	d->DestroyInstance(instance, alloc);
	free(d);

	if (last) {
		/*
		 * The loader unloads this library after the last instance: nothing of
		 * it may keep running. The panels go dark, as after any compositor.
		 */
		struct display *disp = &g_display;
		pthread_mutex_lock(&disp->lock);
		bool joining = disp->vblank_started, clock = disp->clock_started;
		disp->vblank_stop = true;
		pthread_cond_broadcast(&disp->cond);
		pthread_mutex_unlock(&disp->lock);
		if (joining) {
			pthread_join(disp->vblank_thread, NULL);
		}
		if (clock) {
			pthread_join(disp->clock_thread, NULL);
		}
		pthread_mutex_lock(&disp->lock);
		disp->vblank_started = disp->clock_started = false;
		disp->phase_ns = 0;
		disp->ticked_sequence = 0;
		disp->vblank_pending = false;
		kms_disable(disp);
		if (disp->fd >= 0) {
			close(disp->fd);
		}
		disp->fd = -1;
		disp->kms_ready = false;
		disp->probed = false;
		pthread_mutex_unlock(&disp->lock);
	}
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateDevice(VkPhysicalDevice physical,
                   const VkDeviceCreateInfo *info,
                   const VkAllocationCallbacks *alloc,
                   VkDevice *device)
{
	struct instance_data *inst = find_instance(physical);
	VkLayerDeviceCreateInfo *chain = (VkLayerDeviceCreateInfo *)info->pNext;
	while (chain != NULL && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO &&
	                          chain->function == VK_LAYER_LINK_INFO)) {
		chain = (VkLayerDeviceCreateInfo *)chain->pNext;
	}
	if (chain == NULL || inst == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	PFN_vkGetInstanceProcAddr gipa = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
	PFN_vkGetDeviceProcAddr gdpa = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
	VkLayerDeviceLink *next = chain->u.pLayerInfo->pNext;
	chain->u.pLayerInfo = next;
	PFN_vkCreateDevice create = (PFN_vkCreateDevice)gipa(inst->instance, "vkCreateDevice");

	/*
	 * Swapchain images are exported as dma-bufs, acquires signalled through
	 * sync files, and emulated OPAQUE_FD semaphores (pico4-vrsync.c) travel as
	 * sync files: enable what the driver has of that, whatever the application
	 * asked for.
	 */
	static const char *const needed[] = {
	    VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME,
	    VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
	    VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME,
	    VK_KHR_EXTERNAL_FENCE_FD_EXTENSION_NAME,
	    VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME, // compressed scanout only
	};
	size_t needed_count = sizeof(needed) / sizeof(needed[0]) - (ubwc_mode() ? 0 : 1);
	const char *names[96];
	VkDeviceCreateInfo with_ours = *info;
	uint32_t count = info->enabledExtensionCount;
	if (count + needed_count <= 96) {
		memcpy(names, info->ppEnabledExtensionNames, count * sizeof(names[0]));
		uint32_t n = 0;
		inst->EnumerateDeviceExtensionProperties(physical, NULL, &n, NULL);
		VkExtensionProperties *supported = calloc(n > 0 ? n : 1, sizeof(*supported));
		if (supported != NULL && inst->EnumerateDeviceExtensionProperties(physical, NULL, &n, supported) == VK_SUCCESS) {
			for (size_t i = 0; i < needed_count; i++) {
				bool have = false, offered = false;
				for (uint32_t j = 0; j < count; j++) {
					have = have || strcmp(names[j], needed[i]) == 0;
				}
				for (uint32_t j = 0; j < n; j++) {
					offered = offered || strcmp(supported[j].extensionName, needed[i]) == 0;
				}
				if (!have && offered) {
					names[count++] = needed[i];
				}
			}
		}
		free(supported);
		with_ours.enabledExtensionCount = count;
		with_ours.ppEnabledExtensionNames = names;
		info = &with_ours;
	}
	VkResult ret = create(physical, info, alloc, device);
	if (ret == VK_ERROR_EXTENSION_NOT_PRESENT && info->enabledExtensionCount <= 64) {
		const char *names[64];
		const char *ours[] = {k_device_extension.extensionName, k_drm_extension.extensionName};
		VkDeviceCreateInfo copy = *info;
		copy.enabledExtensionCount =
		    without_ours(info->ppEnabledExtensionNames, info->enabledExtensionCount, ours, 2, names);
		copy.ppEnabledExtensionNames = names;
		chain->u.pLayerInfo = next;
		ret = create(physical, &copy, alloc, device);
		LOG("the driver lacks VK_EXT_display_control or VK_EXT_physical_device_drm; the layer provides them (%d)",
		    ret);
	}
	if (ret != VK_SUCCESS) {
		return ret;
	}

	struct device_data *d = calloc(1, sizeof(*d));
	if (d == NULL) {
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	d->device = *device;
	d->key = dispatch_key(*device);
	d->physical = physical;
	d->inst = inst;
	d->gdpa = gdpa;
	d->has_semaphore_fd = has_extension(info, VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME);
	d->has_fence_fd = has_extension(info, VK_KHR_EXTERNAL_FENCE_FD_EXTENSION_NAME);
#define D(name) d->name = (PFN_vk##name)gdpa(*device, "vk" #name)
	D(DestroyDevice);
	D(GetDeviceQueue);
	D(GetDeviceQueue2);
	D(CreateImage);
	D(DestroyImage);
	D(GetImageDrmFormatModifierPropertiesEXT);
	D(CreateCommandPool);
	D(DestroyCommandPool);
	D(AllocateCommandBuffers);
	D(BeginCommandBuffer);
	D(EndCommandBuffer);
	D(CmdPipelineBarrier);
	D(CmdBlitImage);
	D(CmdBeginRenderPass);
	D(CmdBeginRenderPass2);
	D(CmdBeginRendering);
	D(CreateImageView);
	D(CreateRenderPass);
	D(CreateRenderPass2);
	// Below Vulkan 1.2/1.3 the driver has these by their extension names only, and the layer
	// answers both names (SteamVR's Facet renderer calls the extension ones).
	if (d->CmdBeginRenderPass2 == NULL) {
		d->CmdBeginRenderPass2 = (PFN_vkCmdBeginRenderPass2)gdpa(*device, "vkCmdBeginRenderPass2KHR");
	}
	if (d->CmdBeginRendering == NULL) {
		d->CmdBeginRendering = (PFN_vkCmdBeginRendering)gdpa(*device, "vkCmdBeginRenderingKHR");
	}
	if (d->CreateRenderPass2 == NULL) {
		d->CreateRenderPass2 = (PFN_vkCreateRenderPass2)gdpa(*device, "vkCreateRenderPass2KHR");
	}
	D(CmdCopyImageToBuffer);
	D(CreateBuffer);
	D(DestroyBuffer);
	D(GetBufferMemoryRequirements);
	D(BindBufferMemory);
	D(GetImageMemoryRequirements);
	D(AllocateMemory);
	D(FreeMemory);
	D(BindImageMemory);
	D(MapMemory);
	D(UnmapMemory);
	D(GetMemoryFdKHR);
	D(GetImageSubresourceLayout);
	D(CreateFence);
	D(DestroyFence);
	D(ResetFences);
	D(WaitForFences);
	D(GetFenceStatus);
	D(QueueSubmit);
	D(ImportSemaphoreFdKHR);
	D(ImportFenceFdKHR);
	D(CreateSwapchainKHR);
	D(DestroySwapchainKHR);
	D(GetSwapchainImagesKHR);
	D(AcquireNextImageKHR);
	D(AcquireNextImage2KHR);
	D(QueuePresentKHR);
	D(GetSwapchainStatusKHR);
	D(ReleaseSwapchainImagesEXT);
	D(WaitForPresentKHR);
	D(WaitForPresent2KHR);
	D(SetHdrMetadataEXT);
	D(GetDeviceGroupSurfacePresentModesKHR);
	D(CreateSharedSwapchainsKHR);
	D(GetSwapchainCounterEXT);
	D(RegisterDisplayEventEXT);
	D(RegisterDeviceEventEXT);
	D(DisplayPowerControlEXT);
#undef D
	pthread_mutex_lock(&g_objects);
	d->next = g_devices;
	g_devices = d;
	pthread_mutex_unlock(&g_objects);
	vrsync_device_created(physical, *device, gdpa);
	DBG("device created (semaphore fd %d, fence fd %d)", d->has_semaphore_fd, d->has_fence_fd);
	return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
layer_DestroyDevice(VkDevice device, const VkAllocationCallbacks *alloc)
{
	struct device_data *d = find_device(device);
	if (d == NULL) {
		return;
	}
	// Swapchains the application left behind still own images of this device.
	for (;;) {
		pthread_mutex_lock(&g_display.lock);
		struct swapchain *sc = g_display.swapchains;
		while (sc != NULL && sc->dev != d) {
			sc = sc->next;
		}
		pthread_mutex_unlock(&g_display.lock);
		if (sc == NULL) {
			break;
		}
		sc_destroy(sc);
	}
	pthread_mutex_lock(&g_objects);
	for (struct device_data **p = &g_devices; *p != NULL; p = &(*p)->next) {
		if (*p == d) {
			*p = d->next;
			break;
		}
	}
	pthread_mutex_unlock(&g_objects);
	vrsync_device_destroyed(device);
	d->DestroyDevice(device, alloc);
	free(d);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateImage(VkDevice device, const VkImageCreateInfo *info, const VkAllocationCallbacks *alloc,
                  VkImage *image)
{
	struct device_data *d = find_device(device);
	if (d == NULL) {
		return VK_ERROR_DEVICE_LOST;
	}
	VkResult ret = d->CreateImage(device, info, alloc, image);
	// Tiling decides whether Turnip can tile and compress (UBWC) the image; a modifier
	// list or external memory means it is shared with another process or the display.
	bool external = false, modifiers = false;
	for (const VkBaseInStructure *s = info->pNext; s != NULL; s = s->pNext) {
		external |= s->sType == VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO;
		modifiers |= s->sType == VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_LIST_CREATE_INFO_EXT ||
		             s->sType == VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT;
	}
	LOG("image %ux%ux%u mips %u layers %u samples %u format %d tiling %s usage 0x%x flags 0x%x%s%s: %d",
	    info->extent.width, info->extent.height, info->extent.depth, info->mipLevels, info->arrayLayers,
	    info->samples, info->format,
	    info->tiling == VK_IMAGE_TILING_OPTIMAL  ? "optimal"
	    : info->tiling == VK_IMAGE_TILING_LINEAR ? "linear"
	                                             : "modifier",
	    info->usage, info->flags, external ? " external" : "", modifiers ? " modifiers" : "", ret);
	return ret;
}

/*
 * PICO4_VRDISPLAY_PASS_BARRIER: a full memory barrier before every render pass. Without
 * one, chunks of SteamVR's overlays are missing in the right eye on this Turnip build (seen
 * in the lenses, blind comparison): a pass reads what an earlier one wrote before those
 * writes are visible. Which pass is not narrowed down; the barrier costs no measurable rate.
 */
static void
pass_barrier(struct device_data *d, VkCommandBuffer cmd)
{
	VkMemoryBarrier barrier = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
	    .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
	    .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
	};
	d->CmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 1,
	                      &barrier, 0, NULL, 0, NULL);
}

static VKAPI_ATTR void VKAPI_CALL
layer_CmdBeginRenderPass(VkCommandBuffer cmd, const VkRenderPassBeginInfo *info, VkSubpassContents contents)
{
	struct device_data *d = find_device(cmd);
	pass_barrier(d, cmd);
	d->CmdBeginRenderPass(cmd, info, contents);
}

static VKAPI_ATTR void VKAPI_CALL
layer_CmdBeginRenderPass2(VkCommandBuffer cmd, const VkRenderPassBeginInfo *info, const VkSubpassBeginInfo *sub)
{
	struct device_data *d = find_device(cmd);
	pass_barrier(d, cmd);
	d->CmdBeginRenderPass2(cmd, info, sub);
}

static VKAPI_ATTR void VKAPI_CALL
layer_CmdBeginRendering(VkCommandBuffer cmd, const VkRenderingInfo *info)
{
	struct device_data *d = find_device(cmd);
	pass_barrier(d, cmd);
	d->CmdBeginRendering(cmd, info);
}

/*
 * Direct compressed scanout (see direct_mode): the application's views of the scanout
 * images and its presenting render passes get the R8G8B8A8 variant of its format.
 */
static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateImageView(VkDevice device, const VkImageViewCreateInfo *info, const VkAllocationCallbacks *alloc,
                      VkImageView *view)
{
	struct device_data *d = find_device(device);
	if (!is_direct_image(info->image)) {
		return d->CreateImageView(device, info, alloc, view);
	}
	VkImageViewCreateInfo rgba = *info;
	rgba.format = rgba_variant(info->format);
	return d->CreateImageView(device, &rgba, alloc, view);
}

//! Whether an attachment with @p format and @p final_layout is a presented swapchain image.
static bool
presented_bgra(VkFormat format, VkImageLayout final_layout)
{
	return final_layout == VK_IMAGE_LAYOUT_PRESENT_SRC_KHR && rgba_variant(format) != format;
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateRenderPass(VkDevice device, const VkRenderPassCreateInfo *info, const VkAllocationCallbacks *alloc,
                       VkRenderPass *pass)
{
	struct device_data *d = find_device(device);
	VkAttachmentDescription attachments[16];
	if (info->attachmentCount > 16) {
		return d->CreateRenderPass(device, info, alloc, pass);
	}
	VkRenderPassCreateInfo copy = *info;
	for (uint32_t i = 0; i < info->attachmentCount; i++) {
		attachments[i] = info->pAttachments[i];
		if (presented_bgra(attachments[i].format, attachments[i].finalLayout)) {
			attachments[i].format = rgba_variant(attachments[i].format);
		}
	}
	copy.pAttachments = attachments;
	return d->CreateRenderPass(device, &copy, alloc, pass);
}

static VKAPI_ATTR VkResult VKAPI_CALL
layer_CreateRenderPass2(VkDevice device, const VkRenderPassCreateInfo2 *info, const VkAllocationCallbacks *alloc,
                        VkRenderPass *pass)
{
	struct device_data *d = find_device(device);
	VkAttachmentDescription2 attachments[16];
	if (info->attachmentCount > 16) {
		return d->CreateRenderPass2(device, info, alloc, pass);
	}
	VkRenderPassCreateInfo2 copy = *info;
	for (uint32_t i = 0; i < info->attachmentCount; i++) {
		attachments[i] = info->pAttachments[i];
		if (presented_bgra(attachments[i].format, attachments[i].finalLayout)) {
			attachments[i].format = rgba_variant(attachments[i].format);
		}
	}
	copy.pAttachments = attachments;
	return d->CreateRenderPass2(device, &copy, alloc, pass);
}

static PFN_vkVoidFunction
device_function(const char *name)
{
	if (strcmp(name, "vkCreateImage") == 0 && env_num("PICO4_VRDISPLAY_IMAGES", 0) != 0) {
		return (PFN_vkVoidFunction)layer_CreateImage;
	}
	if (direct_mode()) {
		if (strcmp(name, "vkCreateImageView") == 0) {
			return (PFN_vkVoidFunction)layer_CreateImageView;
		}
		if (strcmp(name, "vkCreateRenderPass") == 0) {
			return (PFN_vkVoidFunction)layer_CreateRenderPass;
		}
		if (strcmp(name, "vkCreateRenderPass2") == 0 || strcmp(name, "vkCreateRenderPass2KHR") == 0) {
			return (PFN_vkVoidFunction)layer_CreateRenderPass2;
		}
	}
	if (env_num("PICO4_VRDISPLAY_PASS_BARRIER", 0) != 0) {
		if (strcmp(name, "vkCmdBeginRenderPass") == 0) {
			return (PFN_vkVoidFunction)layer_CmdBeginRenderPass;
		}
		if (strcmp(name, "vkCmdBeginRenderPass2") == 0 || strcmp(name, "vkCmdBeginRenderPass2KHR") == 0) {
			return (PFN_vkVoidFunction)layer_CmdBeginRenderPass2;
		}
		if (strcmp(name, "vkCmdBeginRendering") == 0 || strcmp(name, "vkCmdBeginRenderingKHR") == 0) {
			return (PFN_vkVoidFunction)layer_CmdBeginRendering;
		}
	}
#define F(fn)                                                                                                    \
	if (strcmp(name, "vk" #fn) == 0)                                                                         \
		return (PFN_vkVoidFunction)layer_##fn;
	F(DestroyDevice);
	F(GetDeviceQueue);
	F(GetDeviceQueue2);
	F(CreateSwapchainKHR);
	F(CreateSharedSwapchainsKHR);
	F(DestroySwapchainKHR);
	F(GetSwapchainImagesKHR);
	F(AcquireNextImageKHR);
	F(AcquireNextImage2KHR);
	F(QueuePresentKHR);
	F(GetSwapchainStatusKHR);
	F(ReleaseSwapchainImagesEXT);
	F(WaitForPresentKHR);
	F(WaitForPresent2KHR);
	F(SetHdrMetadataEXT);
	F(GetDeviceGroupSurfacePresentModesKHR);
	F(GetSwapchainCounterEXT);
	F(RegisterDisplayEventEXT);
	F(RegisterDeviceEventEXT);
	F(DisplayPowerControlEXT);
	F(WaitForFences);
	F(GetFenceStatus);
	F(ResetFences);
	F(DestroyFence);
#undef F
	if (strcmp(name, "vkReleaseSwapchainImagesKHR") == 0) {
		return (PFN_vkVoidFunction)layer_ReleaseSwapchainImagesEXT;
	}
	return NULL;
}

static PFN_vkVoidFunction
instance_function(const char *name)
{
#define F(fn)                                                                                                    \
	if (strcmp(name, "vk" #fn) == 0)                                                                         \
		return (PFN_vkVoidFunction)layer_##fn;
	F(CreateInstance);
	F(DestroyInstance);
	F(CreateDevice);
	F(EnumerateDeviceExtensionProperties);
	F(GetPhysicalDeviceProperties2);
#undef F
	if (strcmp(name, "vkGetPhysicalDeviceProperties2KHR") == 0) {
		return (PFN_vkVoidFunction)layer_GetPhysicalDeviceProperties2;
	}
#define F(fn)                                                                                                    \
	if (strcmp(name, "vk" #fn) == 0)                                                                         \
		return (PFN_vkVoidFunction)layer_##fn;
	F(GetPhysicalDeviceDisplayPropertiesKHR);
	F(GetPhysicalDeviceDisplayProperties2KHR);
	F(GetPhysicalDeviceDisplayPlanePropertiesKHR);
	F(GetPhysicalDeviceDisplayPlaneProperties2KHR);
	F(GetDisplayPlaneSupportedDisplaysKHR);
	F(GetDisplayModePropertiesKHR);
	F(GetDisplayModeProperties2KHR);
	F(CreateDisplayModeKHR);
	F(GetDisplayPlaneCapabilitiesKHR);
	F(GetDisplayPlaneCapabilities2KHR);
	F(CreateDisplayPlaneSurfaceKHR);
	F(DestroySurfaceKHR);
	F(GetPhysicalDeviceSurfaceSupportKHR);
	F(GetPhysicalDeviceSurfaceCapabilitiesKHR);
	F(GetPhysicalDeviceSurfaceCapabilities2KHR);
	F(GetPhysicalDeviceSurfaceCapabilities2EXT);
	F(GetPhysicalDeviceSurfaceFormatsKHR);
	F(GetPhysicalDeviceSurfaceFormats2KHR);
	F(GetPhysicalDeviceSurfacePresentModesKHR);
	F(GetPhysicalDevicePresentRectanglesKHR);
	F(GetDrmDisplayEXT);
	F(AcquireDrmDisplayEXT);
	F(ReleaseDisplayEXT);
	F(GetRandROutputDisplayEXT);
	F(AcquireXlibDisplayEXT);
#undef F
	return NULL;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL layer_GetDeviceProcAddr(VkDevice device, const char *name);

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
layer_GetInstanceProcAddr(VkInstance instance, const char *name)
{
	if (strcmp(name, "vkGetInstanceProcAddr") == 0) {
		return (PFN_vkVoidFunction)layer_GetInstanceProcAddr;
	}
	if (strcmp(name, "vkGetDeviceProcAddr") == 0) {
		return (PFN_vkVoidFunction)layer_GetDeviceProcAddr;
	}
	PFN_vkVoidFunction f = instance_function(name);
	if (f == NULL) {
		f = device_function(name);
	}
	if (f != NULL || instance == VK_NULL_HANDLE) {
		return f;
	}
	struct instance_data *d = find_instance(instance);
	PFN_vkVoidFunction next = d != NULL ? d->gipa(instance, name) : NULL;
	f = vrsync_instance_function(name);
	if (f == NULL) {
		f = vrsync_device_function(name);
	}
	// Semaphore commands only where the driver has them: applications probe for some.
	return f != NULL && next != NULL ? f : next;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
layer_GetDeviceProcAddr(VkDevice device, const char *name)
{
	if (strcmp(name, "vkGetDeviceProcAddr") == 0) {
		return (PFN_vkVoidFunction)layer_GetDeviceProcAddr;
	}
	PFN_vkVoidFunction f = device_function(name);
	if (f != NULL) {
		return f;
	}
	struct device_data *d = find_device(device);
	PFN_vkVoidFunction next = d != NULL ? d->gdpa(device, name) : NULL;
	f = vrsync_device_function(name);
	return f != NULL && next != NULL ? f : next;
}

EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *version)
{
	if (version->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT || version->loaderLayerInterfaceVersion < 2) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	version->loaderLayerInterfaceVersion = 2;
	version->pfnGetInstanceProcAddr = layer_GetInstanceProcAddr;
	version->pfnGetDeviceProcAddr = layer_GetDeviceProcAddr;
	version->pfnGetPhysicalDeviceProcAddr = NULL;
	return VK_SUCCESS;
}
