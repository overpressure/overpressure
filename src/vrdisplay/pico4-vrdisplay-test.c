// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-vrdisplay-test: drive a Vulkan direct-mode display the way SteamVR's
 * compositor does, and print a JSON summary (docs/session.md).
 *
 *   PICO4_VRDISPLAY=1 pico4-vrdisplay-test [SECONDS] [--min-hz HZ]
 *
 * Finds the display through VK_KHR_display, acquires it with the DRM fd
 * (VK_EXT_acquire_drm_display) as vrcompositor does, creates a display-plane
 * surface and a FIFO swapchain, and shows the left half red and the right half
 * blue every frame, so each eye's colour tells the panels apart. A second
 * thread waits for vblank events (VK_EXT_display_control), and the swapchain's
 * vblank counter gives the refresh rate. Passes if frames run at HZ or faster
 * (default 85.5, 95% of 90 Hz; counted from the first present, after the mode
 * set), the counter advances at HZ or faster and events arrive at 90% of the
 * mode's rate (the watcher re-arms between events and can miss one); 0 skips
 * the rate checks.
 * Without /dev/dri/card0 (or with PICO4_VRDISPLAY_FAKE=1) it skips the DRM
 * acquire.
 */
#define _GNU_SOURCE
#include <vulkan/vulkan.h>

#include <xf86drm.h>
#include <xf86drmMode.h>

#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define FRAMES_IN_FLIGHT 2
#define MAX_IMAGES 8
//! Valve's private swapchain counter: the vblank count and its CLOCK_MONOTONIC time in ns.
#define COUNTER_TIMESTAMP_VALVE ((VkSurfaceCounterFlagBitsEXT)0x7ffffffd)

static char g_error[256];

#define FAIL(...)                                                                                                \
	do {                                                                                                     \
		snprintf(g_error, sizeof(g_error), __VA_ARGS__);                                                 \
		goto out;                                                                                        \
	} while (0)
#define CHECK(call)                                                                                              \
	do {                                                                                                     \
		VkResult r_ = (call);                                                                            \
		if (r_ != VK_SUCCESS) {                                                                          \
			FAIL("%s: %d", #call, r_);                                                               \
		}                                                                                                \
	} while (0)

static double
now_s(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}

struct vblank_watch
{
	VkDevice device;
	VkDisplayKHR display;
	PFN_vkRegisterDisplayEventEXT register_event;
	volatile bool stop;
	uint64_t events, timeouts;
	double first, last;
};

static void *
vblank_main(void *arg)
{
	struct vblank_watch *w = arg;
	VkDisplayEventInfoEXT info = {
	    .sType = VK_STRUCTURE_TYPE_DISPLAY_EVENT_INFO_EXT,
	    .displayEvent = VK_DISPLAY_EVENT_TYPE_FIRST_PIXEL_OUT_EXT,
	};
	while (!w->stop) {
		VkFence fence;
		if (w->register_event(w->device, w->display, &info, NULL, &fence) != VK_SUCCESS) {
			break;
		}
		VkResult r = vkWaitForFences(w->device, 1, &fence, VK_TRUE, 1000000000ull);
		double t = now_s();
		vkDestroyFence(w->device, fence, NULL);
		if (r == VK_SUCCESS) {
			if (w->events == 0) {
				w->first = t;
			}
			w->last = t;
			w->events++;
		} else {
			w->timeouts++;
		}
	}
	return NULL;
}

static bool
has_device_extension(VkPhysicalDevice pd, const char *name)
{
	uint32_t n = 0;
	vkEnumerateDeviceExtensionProperties(pd, NULL, &n, NULL);
	VkExtensionProperties *props = calloc(n, sizeof(*props));
	vkEnumerateDeviceExtensionProperties(pd, NULL, &n, props);
	bool found = false;
	for (uint32_t i = 0; i < n && !found; i++) {
		found = strcmp(props[i].extensionName, name) == 0;
	}
	free(props);
	return found;
}

int
main(int argc, char **argv)
{
	double seconds = 10, min_hz = 85.5;
	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--min-hz") == 0 && i + 1 < argc) {
			min_hz = atof(argv[++i]);
		} else {
			seconds = atof(argv[i]);
		}
	}

	VkInstance instance = VK_NULL_HANDLE;
	VkDevice device = VK_NULL_HANDLE;
	VkSurfaceKHR surface = VK_NULL_HANDLE;
	VkSwapchainKHR swapchain = VK_NULL_HANDLE;
	VkCommandPool pool = VK_NULL_HANDLE;
	VkBuffer buffer = VK_NULL_HANDLE;
	VkDeviceMemory buffer_memory = VK_NULL_HANDLE;
	VkSemaphore acquired[FRAMES_IN_FLIGHT] = {0}, rendered[MAX_IMAGES] = {0};
	VkFence in_flight[FRAMES_IN_FLIGHT] = {0};
	VkPhysicalDevice pd = VK_NULL_HANDLE;
	VkDisplayKHR display = VK_NULL_HANDLE;
	bool display_acquired = false, watching = false, display_control = false;
	pthread_t watcher;
	struct vblank_watch watch = {0};
	char display_name[64] = "", mode_text[64] = "";
	const char *acquire = "none";
	VkFormat format = VK_FORMAT_UNDEFINED;
	uint32_t image_count = 0, frames = 0, late = 0;
	uint64_t counter_start = 0, counter_end = 0;
	double frame_hz = 0, vblank_hz = 0, event_hz = 0, elapsed = 0;
	double first_present = 0, last_present = 0, counter_start_s = 0, counter_end_s = 0;
	double stamped_age_ms = 0;
	bool stamped_ok = false;
	int drm_fd = -1;

	const char *instance_extensions[] = {
	    VK_KHR_SURFACE_EXTENSION_NAME,
	    VK_KHR_DISPLAY_EXTENSION_NAME,
	    VK_EXT_DIRECT_MODE_DISPLAY_EXTENSION_NAME,
	    VK_EXT_ACQUIRE_DRM_DISPLAY_EXTENSION_NAME,
	    VK_EXT_DISPLAY_SURFACE_COUNTER_EXTENSION_NAME,
	};
	VkApplicationInfo app = {
	    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
	    .pApplicationName = "pico4-vrdisplay-test",
	    .apiVersion = VK_API_VERSION_1_1,
	};
	VkInstanceCreateInfo instance_info = {
	    .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
	    .pApplicationInfo = &app,
	    .enabledExtensionCount = sizeof(instance_extensions) / sizeof(instance_extensions[0]),
	    .ppEnabledExtensionNames = instance_extensions,
	};
	CHECK(vkCreateInstance(&instance_info, NULL, &instance));
	uint32_t n = 1;
	VkResult r = vkEnumeratePhysicalDevices(instance, &n, &pd);
	if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || n == 0) {
		FAIL("no Vulkan device");
	}

	// The display, as vrcompositor finds it.
	n = 0;
	CHECK(vkGetPhysicalDeviceDisplayPropertiesKHR(pd, &n, NULL));
	if (n == 0) {
		FAIL("no direct-mode display (is PICO4_VRDISPLAY=1 set?)");
	}
	VkDisplayPropertiesKHR display_props;
	n = 1;
	r = vkGetPhysicalDeviceDisplayPropertiesKHR(pd, &n, &display_props);
	if (r != VK_SUCCESS && r != VK_INCOMPLETE) {
		FAIL("vkGetPhysicalDeviceDisplayPropertiesKHR: %d", r);
	}
	display = display_props.display;
	snprintf(display_name, sizeof(display_name), "%s", display_props.displayName ? display_props.displayName : "");

	// Acquire it through the DRM fd, as SteamVR does without a display server.
	const char *fake = getenv("PICO4_VRDISPLAY_FAKE");
	if ((fake == NULL || strcmp(fake, "1") != 0) && (drm_fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC)) >= 0) {
		PFN_vkGetDrmDisplayEXT get_drm_display =
		    (PFN_vkGetDrmDisplayEXT)vkGetInstanceProcAddr(instance, "vkGetDrmDisplayEXT");
		PFN_vkAcquireDrmDisplayEXT acquire_drm_display =
		    (PFN_vkAcquireDrmDisplayEXT)vkGetInstanceProcAddr(instance, "vkAcquireDrmDisplayEXT");
		drmModeRes *res = drmModeGetResources(drm_fd);
		for (int i = 0; res != NULL && i < res->count_connectors && !display_acquired; i++) {
			VkDisplayKHR d = VK_NULL_HANDLE;
			if (get_drm_display(pd, drm_fd, res->connectors[i], &d) == VK_SUCCESS && d == display) {
				CHECK(acquire_drm_display(pd, drm_fd, display));
				display_acquired = true;
				acquire = "drm";
			}
		}
		drmModeFreeResources(res);
		if (!display_acquired) {
			FAIL("no DRM connector maps to the display");
		}
	}

	n = 0;
	CHECK(vkGetDisplayModePropertiesKHR(pd, display, &n, NULL));
	VkDisplayModePropertiesKHR modes[8];
	n = n > 8 ? 8 : n;
	CHECK(vkGetDisplayModePropertiesKHR(pd, display, &n, modes));
	if (n == 0) {
		FAIL("the display has no modes");
	}
	uint32_t best = 0;
	for (uint32_t i = 1; i < n; i++) {
		if (abs((int)modes[i].parameters.refreshRate - 90000) < abs((int)modes[best].parameters.refreshRate - 90000)) {
			best = i;
		}
	}
	VkDisplayModePropertiesKHR mode = modes[best];
	VkExtent2D extent = mode.parameters.visibleRegion;
	double hz = mode.parameters.refreshRate / 1000.0;
	snprintf(mode_text, sizeof(mode_text), "%ux%u@%.3f", extent.width, extent.height, hz);

	uint32_t planes = 0;
	CHECK(vkGetPhysicalDeviceDisplayPlanePropertiesKHR(pd, &planes, NULL));
	VkDisplayKHR supported[4];
	n = 4;
	CHECK(vkGetDisplayPlaneSupportedDisplaysKHR(pd, 0, &n, supported));
	if (planes == 0 || n == 0 || supported[0] != display) {
		FAIL("plane 0 does not show the display");
	}
	VkDisplayPlaneCapabilitiesKHR plane_caps;
	CHECK(vkGetDisplayPlaneCapabilitiesKHR(pd, mode.displayMode, 0, &plane_caps));
	VkDisplaySurfaceCreateInfoKHR surface_info = {
	    .sType = VK_STRUCTURE_TYPE_DISPLAY_SURFACE_CREATE_INFO_KHR,
	    .displayMode = mode.displayMode,
	    .planeIndex = 0,
	    .transform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR,
	    .globalAlpha = 1.0f,
	    .alphaMode = VK_DISPLAY_PLANE_ALPHA_OPAQUE_BIT_KHR,
	    .imageExtent = extent,
	};
	CHECK(vkCreateDisplayPlaneSurfaceKHR(instance, &surface_info, NULL, &surface));

	uint32_t families = 0;
	vkGetPhysicalDeviceQueueFamilyProperties(pd, &families, NULL);
	uint32_t family = UINT32_MAX;
	for (uint32_t i = 0; i < families && family == UINT32_MAX; i++) {
		VkBool32 ok = VK_FALSE;
		vkGetPhysicalDeviceSurfaceSupportKHR(pd, i, surface, &ok);
		if (ok) {
			family = i;
		}
	}
	if (family == UINT32_MAX) {
		FAIL("no queue family presents to the display");
	}

	const char *device_extensions[3] = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
	uint32_t device_extension_count = 1;
	display_control = has_device_extension(pd, VK_EXT_DISPLAY_CONTROL_EXTENSION_NAME);
	if (display_control) {
		device_extensions[device_extension_count++] = VK_EXT_DISPLAY_CONTROL_EXTENSION_NAME;
	}
	if (has_device_extension(pd, VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME)) {
		device_extensions[device_extension_count++] = VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME;
	}
	float priority = 1.0f;
	VkDeviceQueueCreateInfo queue_info = {
	    .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
	    .queueFamilyIndex = family,
	    .queueCount = 1,
	    .pQueuePriorities = &priority,
	};
	VkDeviceCreateInfo device_info = {
	    .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
	    .queueCreateInfoCount = 1,
	    .pQueueCreateInfos = &queue_info,
	    .enabledExtensionCount = device_extension_count,
	    .ppEnabledExtensionNames = device_extensions,
	};
	CHECK(vkCreateDevice(pd, &device_info, NULL, &device));
	VkQueue queue;
	vkGetDeviceQueue(device, family, 0, &queue);

	VkSurfaceCapabilitiesKHR caps;
	CHECK(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(pd, surface, &caps));
	if ((caps.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT) == 0) {
		FAIL("the surface cannot be a transfer destination");
	}
	VkSurfaceFormatKHR formats[16];
	n = 16;
	r = vkGetPhysicalDeviceSurfaceFormatsKHR(pd, surface, &n, formats);
	if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || n == 0) {
		FAIL("no surface formats");
	}
	format = formats[0].format;
	for (uint32_t i = 0; i < n; i++) {
		if (formats[i].format == VK_FORMAT_B8G8R8A8_UNORM) {
			format = formats[i].format;
		}
	}
	bool bgr = format == VK_FORMAT_B8G8R8A8_UNORM || format == VK_FORMAT_B8G8R8A8_SRGB;

	VkSwapchainCounterCreateInfoEXT counter_info = {
	    .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_COUNTER_CREATE_INFO_EXT,
	    .surfaceCounters = VK_SURFACE_COUNTER_VBLANK_BIT_EXT,
	};
	VkSwapchainCreateInfoKHR swapchain_info = {
	    .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
	    .pNext = display_control ? &counter_info : NULL,
	    .surface = surface,
	    .minImageCount = caps.minImageCount > 3 ? caps.minImageCount : 3,
	    .imageFormat = format,
	    .imageColorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR,
	    .imageExtent = extent,
	    .imageArrayLayers = 1,
	    .imageUsage = VK_IMAGE_USAGE_TRANSFER_DST_BIT,
	    .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE,
	    .preTransform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR,
	    .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
	    .presentMode = VK_PRESENT_MODE_FIFO_KHR,
	    .clipped = VK_TRUE,
	};
	CHECK(vkCreateSwapchainKHR(device, &swapchain_info, NULL, &swapchain));
	VkImage images[MAX_IMAGES];
	image_count = MAX_IMAGES;
	CHECK(vkGetSwapchainImagesKHR(device, swapchain, &image_count, images));

	// The right half comes from a buffer of blue pixels; the rest is cleared red.
	uint32_t half = extent.width / 2;
	VkDeviceSize size = (VkDeviceSize)half * extent.height * 4;
	VkBufferCreateInfo buffer_info = {
	    .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
	    .size = size,
	    .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
	};
	CHECK(vkCreateBuffer(device, &buffer_info, NULL, &buffer));
	VkMemoryRequirements reqs;
	vkGetBufferMemoryRequirements(device, buffer, &reqs);
	VkPhysicalDeviceMemoryProperties memory;
	vkGetPhysicalDeviceMemoryProperties(pd, &memory);
	uint32_t type = UINT32_MAX;
	for (uint32_t i = 0; i < memory.memoryTypeCount && type == UINT32_MAX; i++) {
		VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
		if ((reqs.memoryTypeBits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & want) == want) {
			type = i;
		}
	}
	if (type == UINT32_MAX) {
		FAIL("no host-visible memory");
	}
	VkMemoryAllocateInfo alloc_info = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
	    .allocationSize = reqs.size,
	    .memoryTypeIndex = type,
	};
	CHECK(vkAllocateMemory(device, &alloc_info, NULL, &buffer_memory));
	CHECK(vkBindBufferMemory(device, buffer, buffer_memory, 0));
	uint8_t *pixels;
	CHECK(vkMapMemory(device, buffer_memory, 0, size, 0, (void **)&pixels));
	const uint8_t blue[4] = {bgr ? 255 : 0, 0, bgr ? 0 : 255, 255};
	for (VkDeviceSize i = 0; i < size; i += 4) {
		memcpy(pixels + i, blue, 4);
	}
	vkUnmapMemory(device, buffer_memory);

	VkCommandPoolCreateInfo pool_info = {
	    .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
	    .queueFamilyIndex = family,
	};
	CHECK(vkCreateCommandPool(device, &pool_info, NULL, &pool));
	VkCommandBuffer cmds[MAX_IMAGES];
	VkCommandBufferAllocateInfo cmd_info = {
	    .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
	    .commandPool = pool,
	    .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
	    .commandBufferCount = image_count,
	};
	CHECK(vkAllocateCommandBuffers(device, &cmd_info, cmds));
	VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
	for (uint32_t i = 0; i < image_count; i++) {
		VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
		CHECK(vkBeginCommandBuffer(cmds[i], &begin));
		VkImageMemoryBarrier to_dst = {
		    .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
		    .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
		    .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
		    .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
		    .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
		    .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
		    .image = images[i],
		    .subresourceRange = range,
		};
		vkCmdPipelineBarrier(cmds[i], VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0,
		                     NULL, 0, NULL, 1, &to_dst);
		VkClearColorValue red = {.float32 = {1.0f, 0.0f, 0.0f, 1.0f}};
		vkCmdClearColorImage(cmds[i], images[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &red, 1, &range);
		VkMemoryBarrier after_clear = {
		    .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
		    .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
		    .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
		};
		vkCmdPipelineBarrier(cmds[i], VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1,
		                     &after_clear, 0, NULL, 0, NULL);
		VkBufferImageCopy copy = {
		    .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
		    .imageOffset = {(int32_t)half, 0, 0},
		    .imageExtent = {half, extent.height, 1},
		};
		vkCmdCopyBufferToImage(cmds[i], buffer, images[i], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
		VkImageMemoryBarrier to_present = to_dst;
		to_present.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
		to_present.dstAccessMask = 0;
		to_present.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		to_present.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
		vkCmdPipelineBarrier(cmds[i], VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0,
		                     NULL, 0, NULL, 1, &to_present);
		CHECK(vkEndCommandBuffer(cmds[i]));
	}
	VkSemaphoreCreateInfo semaphore_info = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
	VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, .flags = VK_FENCE_CREATE_SIGNALED_BIT};
	for (uint32_t i = 0; i < FRAMES_IN_FLIGHT; i++) {
		CHECK(vkCreateSemaphore(device, &semaphore_info, NULL, &acquired[i]));
		CHECK(vkCreateFence(device, &fence_info, NULL, &in_flight[i]));
	}
	for (uint32_t i = 0; i < image_count; i++) {
		CHECK(vkCreateSemaphore(device, &semaphore_info, NULL, &rendered[i]));
	}

	PFN_vkGetSwapchainCounterEXT get_counter = NULL;
	if (display_control) {
		get_counter = (PFN_vkGetSwapchainCounterEXT)vkGetDeviceProcAddr(device, "vkGetSwapchainCounterEXT");
		watch.device = device;
		watch.display = display;
		watch.register_event =
		    (PFN_vkRegisterDisplayEventEXT)vkGetDeviceProcAddr(device, "vkRegisterDisplayEventEXT");
		watching = pthread_create(&watcher, NULL, vblank_main, &watch) == 0;
	}

	double start = now_s(), previous = start;
	bool counted = false;
	while (now_s() - start < seconds) {
		uint32_t slot = frames % FRAMES_IN_FLIGHT;
		CHECK(vkWaitForFences(device, 1, &in_flight[slot], VK_TRUE, 2000000000ull));
		uint32_t index;
		CHECK(vkAcquireNextImageKHR(device, swapchain, 2000000000ull, acquired[slot], VK_NULL_HANDLE, &index));
		CHECK(vkResetFences(device, 1, &in_flight[slot]));
		VkPipelineStageFlags stage = VK_PIPELINE_STAGE_TRANSFER_BIT;
		VkSubmitInfo submit = {
		    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
		    .waitSemaphoreCount = 1,
		    .pWaitSemaphores = &acquired[slot],
		    .pWaitDstStageMask = &stage,
		    .commandBufferCount = 1,
		    .pCommandBuffers = &cmds[index],
		    .signalSemaphoreCount = 1,
		    .pSignalSemaphores = &rendered[index],
		};
		CHECK(vkQueueSubmit(queue, 1, &submit, in_flight[slot]));
		VkPresentInfoKHR present = {
		    .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR,
		    .waitSemaphoreCount = 1,
		    .pWaitSemaphores = &rendered[index],
		    .swapchainCount = 1,
		    .pSwapchains = &swapchain,
		    .pImageIndices = &index,
		};
		CHECK(vkQueuePresentKHR(queue, &present));
		double t = now_s();
		if (frames > 0 && (t - previous) * hz > 1.5) {
			late++;
		}
		if (frames == 0) {
			first_present = t;
		}
		previous = last_present = t;
		frames++;
		if (get_counter != NULL && !counted && get_counter(device, swapchain, VK_SURFACE_COUNTER_VBLANK_BIT_EXT,
		                                                   &counter_start) == VK_SUCCESS) {
			counter_start_s = now_s();
			counted = true;
		}
	}
	elapsed = now_s() - start;
	if (get_counter != NULL &&
	    get_counter(device, swapchain, VK_SURFACE_COUNTER_VBLANK_BIT_EXT, &counter_end) == VK_SUCCESS) {
		counter_end_s = now_s();
	}
	// Valve's counter with the vblank's time, which vrcompositor reads after each present wait.
	uint64_t stamped[2] = {0, 0};
	if (get_counter != NULL &&
	    get_counter(device, swapchain, COUNTER_TIMESTAMP_VALVE, stamped) == VK_SUCCESS && stamped[1] != 0) {
		stamped_age_ms = (now_s() - stamped[1] / 1e9) * 1e3;
		stamped_ok = stamped[0] >= counter_end && stamped_age_ms > -20.0 && stamped_age_ms < 3000.0 / hz;
	}
	if (frames > 1 && last_present > first_present) {
		frame_hz = (frames - 1) / (last_present - first_present);
	}
	if (counted && counter_end_s > counter_start_s && counter_end > counter_start) {
		vblank_hz = (counter_end - counter_start) / (counter_end_s - counter_start_s);
	}

out:
	if (watching) {
		watch.stop = true;
		pthread_join(watcher, NULL);
		if (watch.events > 1) {
			event_hz = (watch.events - 1) / (watch.last - watch.first);
		}
	}
	if (device != VK_NULL_HANDLE) {
		vkDeviceWaitIdle(device);
		for (uint32_t i = 0; i < FRAMES_IN_FLIGHT; i++) {
			vkDestroySemaphore(device, acquired[i], NULL);
			vkDestroyFence(device, in_flight[i], NULL);
		}
		for (uint32_t i = 0; i < MAX_IMAGES; i++) {
			vkDestroySemaphore(device, rendered[i], NULL);
		}
		vkDestroyCommandPool(device, pool, NULL);
		vkDestroyBuffer(device, buffer, NULL);
		vkFreeMemory(device, buffer_memory, NULL);
		vkDestroySwapchainKHR(device, swapchain, NULL);
		vkDestroyDevice(device, NULL);
	}
	if (instance != VK_NULL_HANDLE) {
		vkDestroySurfaceKHR(instance, surface, NULL);
		PFN_vkReleaseDisplayEXT release =
		    (PFN_vkReleaseDisplayEXT)vkGetInstanceProcAddr(instance, "vkReleaseDisplayEXT");
		if (display_acquired && release != NULL) {
			release(pd, display);
		}
		vkDestroyInstance(instance, NULL);
	}
	if (drm_fd >= 0) {
		close(drm_fd);
	}

	bool pass = g_error[0] == '\0' && frames > 0 && (min_hz <= 0 || frame_hz >= min_hz) &&
	            (!display_control || ((min_hz <= 0 || (vblank_hz >= min_hz && event_hz >= 0.9 * hz)) &&
	                                  counter_end > counter_start && stamped_ok));
	if (g_error[0] == '\0' && !pass) {
		snprintf(g_error, sizeof(g_error),
		         "frames %.2f Hz, vblanks %.2f Hz, events %.2f Hz, counter %" PRIu64 " -> %" PRIu64
		         ", vblank time %.3f ms old",
		         frame_hz, vblank_hz, event_hz, counter_start, counter_end, stamped_age_ms);
	}
	printf("{\"schema\":1,\"status\":\"%s\",\"display\":\"%s\",\"mode\":\"%s\",\"acquire\":\"%s\","
	       "\"format\":%d,\"images\":%u,\"seconds\":%.3f,\"frames\":%u,\"frame_hz\":%.3f,\"late_frames\":%u,"
	       "\"display_control\":%s,\"vblank_events\":%" PRIu64 ",\"vblank_timeouts\":%" PRIu64
	       ",\"event_hz\":%.3f,\"vblank_hz\":%.3f,\"counter\":[%" PRIu64 ",%" PRIu64 "],\"vblank_age_ms\":%.3f",
	       pass ? "pass" : "fail", display_name, mode_text, acquire, format, image_count, elapsed, frames, frame_hz,
	       late, display_control ? "true" : "false", watch.events, watch.timeouts, event_hz, vblank_hz,
	       counter_start, counter_end, stamped_age_ms);
	if (!pass) {
		printf(",\"error\":\"%s\"", g_error);
	}
	printf("}\n");
	return pass ? 0 : 1;
}
