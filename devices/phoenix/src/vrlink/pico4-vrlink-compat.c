// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-vrlink-compat: preloaded into SteamVR's Steam Link client (vrlink, through its
 * run_vrlink.sh; docs/session.md#steam-link) so that its V4L2 decoder code, written for the
 * Steam Frame's decoder, drives the PICO 4's msm_vidc decoder (pico4-vidc-compat.c).
 *
 * ioctl, mmap and close are wrapped. A V4L2 ioctl on a character device of the video codec's
 * major whose card is "msm_vidc_vdec" attaches the translation to that fd; every other call goes
 * to the C library unchanged. dlsym is wrapped for vrlink's Vulkan, whose picture memory may
 * have to be larger than its images (hook_allocate_memory). Buffers come from the kernel's ION system heap (/dev/ion: this
 * kernel has no dma-heaps), uncached. PICO4_VRLINK_COMPAT_LOG=0 silences the messages on stderr,
 * PICO4_VRLINK_COMPAT_LOG=2 adds one per translated ioctl.
 */
#define _GNU_SOURCE
#include "pico4-vidc-compat.h"

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

//! The kernel's msm_vidc nodes (/dev/video32, /dev/video33).
#define VIDC_MAJOR 81u
#define VIDC_DECODER_CARD "msm_vidc_vdec"
// This kernel's ION (4.19, the ABI without handles): allocate returns a dma-buf fd.
#define ION_IOC_ALLOC 0xc0184900ul
#define ION_SYSTEM_HEAP_ID 25u
#define MAX_FDS 4096
#define EXPORT __attribute__((visibility("default")))

struct ion_allocation_data
{
	uint64_t len;
	uint32_t heap_id_mask;
	uint32_t flags;
	uint32_t fd;
	uint32_t unused;
};

// The Vulkan structures the picture memory hook reads (vulkan_core.h's layout).
#define VK_STYPE_EXPORT_MEMORY_ALLOCATE_INFO 1000072002
#define VK_STYPE_MEMORY_DEDICATED_ALLOCATE_INFO 1000127001
#define VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF 0x200u

struct vk_base
{
	int32_t sType;
	const struct vk_base *pNext;
};

struct vk_memory_allocate_info
{
	int32_t sType;
	const void *pNext;
	uint64_t allocationSize;
	uint32_t memoryTypeIndex;
};

struct vk_export_memory_allocate_info
{
	int32_t sType;
	const void *pNext;
	uint32_t handleTypes;
};

struct vk_memory_dedicated_allocate_info
{
	int32_t sType;
	const void *pNext;
	uint64_t image;
	uint64_t buffer;
};

typedef void (*vk_function)(void);
typedef vk_function (*vk_get_proc_addr)(void *dispatchable, const char *name);
typedef int32_t (*vk_allocate_memory)(void *device, const struct vk_memory_allocate_info *info, const void *allocator,
                                      uint64_t *memory);

static void *(*real_dlsym)(void *, const char *);
static int (*real_ioctl)(int, unsigned long, ...);
static void *(*real_mmap)(void *, size_t, int, int, int, off_t);
static int (*real_close)(int);
static vk_get_proc_addr real_get_instance_proc_addr;
static vk_get_proc_addr real_get_device_proc_addr;
static vk_allocate_memory real_allocate_memory;
static int g_log = 1;
static int g_trace = 0;
static int g_ion = -1;
//! Per fd: the codec node it was found not to be the decoder with (0: not looked at).
static dev_t g_seen_rdev[MAX_FDS];

static void
resolve(void)
{
	if (real_ioctl == NULL) {
		real_dlsym = (void *(*)(void *, const char *))dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.34");
		if (real_dlsym == NULL) {
			real_dlsym = (void *(*)(void *, const char *))dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.17");
		}
		real_ioctl = (int (*)(int, unsigned long, ...))real_dlsym(RTLD_NEXT, "ioctl");
		real_mmap = (void *(*)(void *, size_t, int, int, int, off_t))real_dlsym(RTLD_NEXT, "mmap");
		real_close = (int (*)(int))real_dlsym(RTLD_NEXT, "close");
	}
}

static void
log_line(void *ctx, const char *line)
{
	(void)ctx;
	if (g_log) {
		fprintf(stderr, "pico4-vrlink-compat: %s\n", line);
	}
}

static int
ops_ioctl(void *ctx, int fd, unsigned long request, void *arg)
{
	(void)ctx;
	return real_ioctl(fd, request, arg);
}

static int
ops_alloc(void *ctx, size_t len)
{
	(void)ctx;
	if (g_ion < 0) {
		g_ion = open("/dev/ion", O_RDONLY | O_CLOEXEC);
		if (g_ion < 0) {
			return -1;
		}
	}
	struct ion_allocation_data a = {.len = len, .heap_id_mask = 1u << ION_SYSTEM_HEAP_ID};
	if (real_ioctl(g_ion, ION_IOC_ALLOC, &a) != 0) {
		return -1;
	}
	int fd = (int)a.fd;
	fcntl(fd, F_SETFD, FD_CLOEXEC);
	return fd;
}

static int
ops_close(void *ctx, int fd)
{
	(void)ctx;
	return real_close(fd);
}

static int
ops_poll(void *ctx, int fd, short events, int timeout_ms)
{
	(void)ctx;
	struct pollfd p = {.fd = fd, .events = events};
	return poll(&p, 1, timeout_ms) > 0 ? p.revents : 0;
}

static int
ops_blocking(void *ctx, int fd)
{
	(void)ctx;
	int flags = fcntl(fd, F_GETFL);
	return flags >= 0 && !(flags & O_NONBLOCK);
}

static const struct vidc_ops g_ops = {.ioctl = ops_ioctl,
                                      .alloc = ops_alloc,
                                      .close = ops_close,
                                      .poll = ops_poll,
                                      .blocking = ops_blocking,
                                      .log = log_line,
                                      .ctx = NULL};
static struct vidc_ops g_traced_ops;

__attribute__((constructor)) static void
init(void)
{
	resolve();
	const char *env = getenv("PICO4_VRLINK_COMPAT_LOG");
	g_log = env == NULL || strcmp(env, "0") != 0;
	g_trace = env != NULL && strcmp(env, "2") == 0;
	g_traced_ops = g_ops;
	g_traced_ops.trace = g_trace;
	log_line(NULL, "loaded");
}

//! Whether fd is the decoder, attaching the translation on first sight.
static int
decoder(int fd)
{
	if (fd < 0 || fd >= MAX_FDS) {
		return 0;
	}
	if (vidc_attached(fd)) {
		return 1;
	}
	struct stat st;
	if (fstat(fd, &st) != 0 || !S_ISCHR(st.st_mode) || major(st.st_rdev) != VIDC_MAJOR) {
		return 0;
	}
	if (g_seen_rdev[fd] == st.st_rdev) {
		return 0; // the codec's other node, seen before
	}
	struct v4l2_capability cap;
	memset(&cap, 0, sizeof(cap));
	if (real_ioctl(fd, VIDIOC_QUERYCAP, &cap) != 0 || strcmp((char *)cap.card, VIDC_DECODER_CARD) != 0) {
		g_seen_rdev[fd] = st.st_rdev;
		return 0;
	}
	return vidc_attach(&g_traced_ops, fd) == 0;
}

EXPORT int
ioctl(int fd, unsigned long request, ...)
{
	resolve();
	va_list ap;
	va_start(ap, request);
	void *arg = va_arg(ap, void *);
	va_end(ap);
	if (_IOC_TYPE(request) == 'V' && decoder(fd)) {
		return vidc_ioctl(fd, request, arg);
	}
	return real_ioctl(fd, request, arg);
}

static void *
map(void *addr, size_t len, int prot, int flags, int fd, off_t offset)
{
	resolve();
	if (fd >= 0 && vidc_attached(fd)) {
		int target;
		off_t target_offset;
		if (vidc_mmap_target(fd, offset, &target, &target_offset) != 0) {
			return MAP_FAILED;
		}
		return real_mmap(addr, len, prot, flags, target, target_offset);
	}
	return real_mmap(addr, len, prot, flags, fd, offset);
}

EXPORT void *
mmap(void *addr, size_t len, int prot, int flags, int fd, off_t offset)
{
	return map(addr, len, prot, flags, fd, offset);
}

EXPORT void *
mmap64(void *addr, size_t len, int prot, int flags, int fd, off64_t offset)
{
	return map(addr, len, prot, flags, fd, (off_t)offset);
}

/*
 * The pictures' memory. vrlink allocates each picture's (dedicated to its image, exported as a
 * dma-buf for the decoder) to its image's size; after a smaller picture size the decoder needs
 * as much as the largest so far (vidc_picture_floor), so those allocations are raised to it.
 * vrlink reaches Vulkan through dlopen and dlsym("vkGetInstanceProcAddr"), so the hook sits in
 * dlsym and the two get-proc-addr functions.
 */
static int32_t
hook_allocate_memory(void *device, const struct vk_memory_allocate_info *info, const void *allocator,
                     uint64_t *memory)
{
	bool dma_buf = false, image = false;
	for (const struct vk_base *s = info->pNext; s != NULL; s = s->pNext) {
		if (s->sType == VK_STYPE_EXPORT_MEMORY_ALLOCATE_INFO) {
			dma_buf |= (((const struct vk_export_memory_allocate_info *)s)->handleTypes &
			            VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF) != 0;
		} else if (s->sType == VK_STYPE_MEMORY_DEDICATED_ALLOCATE_INFO) {
			image |= ((const struct vk_memory_dedicated_allocate_info *)s)->image != 0;
		}
	}
	size_t floor = vidc_picture_floor();
	if (dma_buf && image && info->allocationSize < floor) {
		struct vk_memory_allocate_info raised = *info;
		raised.allocationSize = floor;
		if (g_trace) {
			fprintf(stderr, "pico4-vrlink-compat: picture memory %llu raised to %zu bytes\n",
			        (unsigned long long)info->allocationSize, floor);
		}
		return real_allocate_memory(device, &raised, allocator, memory);
	}
	return real_allocate_memory(device, info, allocator, memory);
}

static vk_function hook_get_instance_proc_addr(void *instance, const char *name);

static vk_function
hook_get_device_proc_addr(void *device, const char *name)
{
	vk_function f = real_get_device_proc_addr(device, name);
	if (f != NULL && strcmp(name, "vkAllocateMemory") == 0) {
		real_allocate_memory = (vk_allocate_memory)f;
		return (vk_function)hook_allocate_memory;
	}
	if (f != NULL && strcmp(name, "vkGetDeviceProcAddr") == 0) {
		return (vk_function)hook_get_device_proc_addr;
	}
	return f;
}

static vk_function
hook_get_instance_proc_addr(void *instance, const char *name)
{
	vk_function f = real_get_instance_proc_addr(instance, name);
	if (f == NULL) {
		return f;
	}
	if (strcmp(name, "vkGetInstanceProcAddr") == 0) {
		return (vk_function)hook_get_instance_proc_addr;
	}
	if (strcmp(name, "vkGetDeviceProcAddr") == 0) {
		real_get_device_proc_addr = (vk_get_proc_addr)f;
		return (vk_function)hook_get_device_proc_addr;
	}
	if (strcmp(name, "vkAllocateMemory") == 0) {
		real_allocate_memory = (vk_allocate_memory)f;
		return (vk_function)hook_allocate_memory;
	}
	return f;
}

EXPORT void *
dlsym(void *handle, const char *name)
{
	resolve();
	// RTLD_NEXT would mean the library after this one, not after the caller's: no client here
	// asks for it (vrlink looks its symbols up in the handles it opened).
	void *sym = real_dlsym(handle, name);
	if (sym != NULL && handle != RTLD_NEXT && strcmp(name, "vkGetInstanceProcAddr") == 0) {
		real_get_instance_proc_addr = (vk_get_proc_addr)sym;
		return (void *)hook_get_instance_proc_addr;
	}
	return sym;
}

/*
 * The controllers as vrlink sees them: the Frame's, the only ones it sends input for. On the PC,
 * Valve's driver activates a controller only with a serial, a manufacturer and a type in its
 * properties, and reads the input vrlink sends only for the type frame_controller. vrlink reads
 * the input by the Frame's paths (IVRPaths), so they are turned into ours, the reverse of
 * frame-controller-remapping.json. SteamVR on the headset keeps our type and bindings: only this
 * process's interfaces change.
 */
#define VR_SLOT_GET_STRING_PROPERTY 28 // IVRSystem_026::GetStringTrackedDeviceProperty
#define VR_SLOT_STRING_TO_HANDLE 2     // IVRPaths_002::StringToHandle
#define VR_PROP_MANUFACTURER 1005      // Prop_ManufacturerName_String
#define VR_PROP_CONTROLLER_TYPE 7000   // Prop_ControllerType_String
#define VR_PROP_ERROR_NOT_SET 4        // TrackedProp_UnknownProperty

typedef uint32_t (*vr_get_string_property)(void *self, uint32_t device, int32_t prop, char *value, uint32_t size,
                                           int32_t *error);
typedef int32_t (*vr_string_to_handle)(void *self, uint64_t *handle, const char *path);

static vr_get_string_property real_get_string_property;
static vr_string_to_handle real_string_to_handle;

static const struct
{
	const char *frame;
	const char *ours;
} g_input_paths[] = {
    {"/user/hand/left/input/thumbstick/", "/user/hand/left/input/joystick/"},
    {"/user/hand/right/input/thumbstick/", "/user/hand/right/input/joystick/"},
    {"/user/hand/left/input/dpad_down/", "/user/hand/left/input/x/"},
    {"/user/hand/left/input/dpad_up/", "/user/hand/left/input/y/"},
};

static uint32_t
vr_string_result(const char *text, char *value, uint32_t size, int32_t *error)
{
	uint32_t needed = (uint32_t)strlen(text) + 1;
	if (value != NULL && size >= needed) {
		memcpy(value, text, needed);
	}
	if (error != NULL) {
		*error = 0;
	}
	return needed;
}

static uint32_t
hook_get_string_property(void *self, uint32_t device, int32_t prop, char *value, uint32_t size, int32_t *error)
{
	int32_t real_error = 0;
	uint32_t n = real_get_string_property(self, device, prop, value, size, &real_error);
	if (prop == VR_PROP_CONTROLLER_TYPE && real_error == 0 && value != NULL &&
	    strcmp(value, "pico_controller") == 0) {
		return vr_string_result("frame_controller", value, size, error);
	}
	if (prop == VR_PROP_MANUFACTURER && real_error == VR_PROP_ERROR_NOT_SET) {
		return vr_string_result("PICO", value, size, error);
	}
	if (error != NULL) {
		*error = real_error;
	}
	return n;
}

static int32_t
hook_string_to_handle(void *self, uint64_t *handle, const char *path)
{
	for (size_t i = 0; i < sizeof(g_input_paths) / sizeof(g_input_paths[0]); i++) {
		size_t len = strlen(g_input_paths[i].frame);
		if (strncmp(path, g_input_paths[i].frame, len) == 0) {
			char ours[128];
			snprintf(ours, sizeof(ours), "%s%s", g_input_paths[i].ours, path + len);
			return real_string_to_handle(self, handle, ours);
		}
	}
	return real_string_to_handle(self, handle, path);
}

//! Replaces a slot of an interface's table in this process; returns what it held.
static void *
patch_slot(void *iface, size_t slot, void *hook)
{
	void **entry = *(void ***)iface + slot;
	if (*entry == hook) {
		return NULL;
	}
	long page = sysconf(_SC_PAGESIZE);
	void *start = (void *)((uintptr_t)entry & ~((uintptr_t)page - 1));
	size_t len = (size_t)((uintptr_t)(entry + 1) - (uintptr_t)start);
	if (mprotect(start, len, PROT_READ | PROT_WRITE) != 0) {
		perror("pico4-vrlink-compat: mprotect");
		return NULL;
	}
	void *old = *entry;
	*entry = hook;
	mprotect(start, len, PROT_READ);
	return old;
}

EXPORT void *
VR_GetGenericInterface(const char *version, int32_t *error)
{
	static void *(*real)(const char *, int32_t *);
	if (real == NULL) {
		resolve();
		real = (void *(*)(const char *, int32_t *))real_dlsym(RTLD_NEXT, "VR_GetGenericInterface");
	}
	void *iface = real(version, error);
	if (iface == NULL) {
		return NULL;
	}
	if (strcmp(version, "IVRSystem_026") == 0) {
		void *old = patch_slot(iface, VR_SLOT_GET_STRING_PROPERTY, (void *)hook_get_string_property);
		if (old != NULL) {
			real_get_string_property = (vr_get_string_property)old;
		}
	} else if (strcmp(version, "IVRPaths_002") == 0) {
		void *old = patch_slot(iface, VR_SLOT_STRING_TO_HANDLE, (void *)hook_string_to_handle);
		if (old != NULL) {
			real_string_to_handle = (vr_string_to_handle)old;
		}
	}
	return iface;
}

EXPORT int
close(int fd)
{
	resolve();
	if (fd >= 0 && fd < MAX_FDS) {
		if (vidc_attached(fd)) {
			vidc_detach(fd);
		}
		g_seen_rdev[fd] = 0;
	}
	return real_close(fd);
}
