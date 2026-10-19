// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-openxr-display: render through OpenXR (Vulkan) for a while and print a
 * JSON summary of the frame loop.
 *
 *   pico4-openxr-display [SECONDS] [--projection-only]
 *
 * Each eye gets a projection layer with a dim grid and a large L or R in its
 * own colour (left red, right blue), so eye routing is visible; a quad layer
 * with a green and white checkerboard stands 1.5 m in front of the start
 * position in LOCAL space, so it stays put while the head turns (left out with
 * --projection-only, the single-layer case runtimes optimise). The content is
 * static: each swapchain image is filled once by a buffer copy (nothing here
 * depends on shaders), so the frame loop measures the runtime, not the client.
 *
 * The summary reports the system, the swapchain format, frames, the runtime's
 * display period, the rate xrWaitFrame returned at, late frames (an interval
 * above 1.5 periods) and view locations without tracked orientation, all after
 * the first second. Exit status 0 means frames ran at 95% of the display rate
 * or better, at most 1% late, with tracked orientation.
 */

#define _DEFAULT_SOURCE // clock_gettime under -std=c11
#define XR_USE_GRAPHICS_API_VULKAN
#include <time.h>
#include <vulkan/vulkan.h>

#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define QUAD_SIZE 512

//! The JSON report; stdout itself goes to stderr while the in-process runtime runs.
static FILE *g_report;

static FILE *
report(void)
{
	return g_report != NULL ? g_report : stdout;
}

/*!
 * The runtime runs in this process and also writes to stdout (Basalt, for one).
 * Keep the real stdout for the report and point file descriptor 1 at stderr.
 */
static void
report_open(void)
{
	fflush(stdout);
	int fd = dup(STDOUT_FILENO);
	FILE *f = fd >= 0 ? fdopen(fd, "w") : NULL;
	if (f != NULL && dup2(STDERR_FILENO, STDOUT_FILENO) >= 0) {
		g_report = f;
	}
}

static void
fail(const char *what, long res)
{
	fprintf(report(), "{\"schema\": 1, \"status\": \"fail\", \"error\": \"%s failed (%ld)\"}\n", what, res);
	exit(1);
}

#define CHECK(call)                                                                                                    \
	do {                                                                                                           \
		XrResult r_ = (call);                                                                                  \
		if (XR_FAILED(r_)) {                                                                                   \
			fail(#call, r_);                                                                               \
		}                                                                                                      \
	} while (0)

#define VK_CHECK(call)                                                                                                 \
	do {                                                                                                           \
		VkResult r_ = (call);                                                                                  \
		if (r_ != VK_SUCCESS) {                                                                                \
			fail(#call, r_);                                                                               \
		}                                                                                                      \
	} while (0)

static double
now_s(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec * 1e-9;
}

struct gpu
{
	VkInstance instance;
	VkPhysicalDevice physical;
	VkDevice device;
	uint32_t family;
	VkQueue queue;
	VkCommandPool pool;
	VkCommandBuffer cmd;
	VkFence fence;
};

struct target
{
	XrSwapchain swapchain;
	uint32_t width, height, count;
	VkImage images[8];
	bool filled[8];
	VkBuffer buffer;
	VkDeviceMemory memory;
};

//! Pixel in swapchain byte order.
static uint32_t
pixel(bool bgra, uint8_t r, uint8_t g, uint8_t b)
{
	return bgra ? (uint32_t)b | (uint32_t)g << 8 | (uint32_t)r << 16 | 0xff000000u
	            : (uint32_t)r | (uint32_t)g << 8 | (uint32_t)b << 16 | 0xff000000u;
}

//! Eye pattern: dim grid in the eye's colour, light border and a large L or R (as pico4-kms-test).
static void
fill_eye(uint32_t *px, uint32_t w, uint32_t h, int eye, bool bgra)
{
	uint32_t cell = w / 12, line = w / 540 + 2, stroke = w / 40;
	uint32_t lx = w * 2 / 5, ly = h * 3 / 8, lw = w / 6, lh = h / 4;
	for (uint32_t y = 0; y < h; y++) {
		for (uint32_t x = 0; x < w; x++) {
			uint32_t c = eye ? pixel(bgra, 0x10, 0x20, 0x40) : pixel(bgra, 0x40, 0x20, 0x10);
			if (x % cell < line || y % cell < line) {
				c = pixel(bgra, 0x60, 0x60, 0x60);
			}
			if (x < 2 * line || x >= w - 2 * line || y < 2 * line || y >= h - 2 * line) {
				c = pixel(bgra, 0xa0, 0xa0, 0xa0);
			}
			uint32_t dx = x - lx, dy = y - ly; // wraps when left of/above the letter
			bool in = dx < lw && dy < lh;
			bool letter = eye ? in && (dx < stroke || dy < stroke || (dy >= lh / 2 && dy < lh / 2 + stroke) ||
			                           (dx >= lw - stroke && dy < lh / 2) ||
			                           (dy >= lh / 2 && (dx * lh / lw + stroke > dy && dx * lh / lw < dy + stroke)))
			                  : in && (dx < stroke || dy >= lh - stroke);
			if (letter) {
				c = eye ? pixel(bgra, 0x60, 0x90, 0xff) : pixel(bgra, 0xff, 0x70, 0x40);
			}
			px[(size_t)y * w + x] = c;
		}
	}
}

static void
fill_quad(uint32_t *px, bool bgra)
{
	for (uint32_t y = 0; y < QUAD_SIZE; y++) {
		for (uint32_t x = 0; x < QUAD_SIZE; x++) {
			bool white = ((x / 64) ^ (y / 64)) & 1;
			bool edge = x < 8 || y < 8 || x >= QUAD_SIZE - 8 || y >= QUAD_SIZE - 8;
			px[y * QUAD_SIZE + x] =
			    edge ? pixel(bgra, 0xff, 0xff, 0x00) : white ? pixel(bgra, 0xf0, 0xf0, 0xf0) : pixel(bgra, 0x20, 0xc0, 0x40);
		}
	}
}

static uint32_t
memory_type(struct gpu *g, uint32_t bits, VkMemoryPropertyFlags flags)
{
	VkPhysicalDeviceMemoryProperties props;
	vkGetPhysicalDeviceMemoryProperties(g->physical, &props);
	for (uint32_t i = 0; i < props.memoryTypeCount; i++) {
		if ((bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & flags) == flags) {
			return i;
		}
	}
	fail("memory type", 0);
	return 0;
}

static void
create_target(XrSession session, struct gpu *g, int64_t format, uint32_t w, uint32_t h, struct target *t)
{
	XrSwapchainCreateInfo info = {
	    .type = XR_TYPE_SWAPCHAIN_CREATE_INFO,
	    .usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT,
	    .format = format,
	    .sampleCount = 1,
	    .width = w,
	    .height = h,
	    .faceCount = 1,
	    .arraySize = 1,
	    .mipCount = 1,
	};
	CHECK(xrCreateSwapchain(session, &info, &t->swapchain));
	t->width = w;
	t->height = h;
	XrSwapchainImageVulkan2KHR images[8];
	for (int i = 0; i < 8; i++) {
		images[i] = (XrSwapchainImageVulkan2KHR){.type = XR_TYPE_SWAPCHAIN_IMAGE_VULKAN2_KHR};
	}
	CHECK(xrEnumerateSwapchainImages(t->swapchain, 8, &t->count, (XrSwapchainImageBaseHeader *)images));
	for (uint32_t i = 0; i < t->count; i++) {
		t->images[i] = images[i].image;
	}

	VkBufferCreateInfo bi = {
	    .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
	    .size = (VkDeviceSize)w * h * 4,
	    .usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
	};
	VK_CHECK(vkCreateBuffer(g->device, &bi, NULL, &t->buffer));
	VkMemoryRequirements reqs;
	vkGetBufferMemoryRequirements(g->device, t->buffer, &reqs);
	VkMemoryAllocateInfo ai = {
	    .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
	    .allocationSize = reqs.size,
	    .memoryTypeIndex = memory_type(g, reqs.memoryTypeBits,
	                                   VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT),
	};
	VK_CHECK(vkAllocateMemory(g->device, &ai, NULL, &t->memory));
	VK_CHECK(vkBindBufferMemory(g->device, t->buffer, t->memory, 0));
}

static uint32_t *
map(struct gpu *g, struct target *t)
{
	void *p;
	VK_CHECK(vkMapMemory(g->device, t->memory, 0, VK_WHOLE_SIZE, 0, &p));
	return p;
}

static void
barrier(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags src, VkAccessFlags dst)
{
	VkImageMemoryBarrier b = {
	    .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
	    .srcAccessMask = src,
	    .dstAccessMask = dst,
	    .oldLayout = from,
	    .newLayout = to,
	    .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	    .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
	    .image = image,
	    .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
	};
	vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, NULL, 0,
	                     NULL, 1, &b);
}

//! Acquire, copy the target's buffer into the image the first time it comes up, release.
static void
upload(struct gpu *g, struct target *t)
{
	uint32_t index;
	XrSwapchainImageAcquireInfo acquire = {.type = XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
	CHECK(xrAcquireSwapchainImage(t->swapchain, &acquire, &index));
	XrSwapchainImageWaitInfo wait = {.type = XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO, .timeout = XR_INFINITE_DURATION};
	CHECK(xrWaitSwapchainImage(t->swapchain, &wait));
	XrSwapchainImageReleaseInfo release = {.type = XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
	if (t->filled[index]) {
		CHECK(xrReleaseSwapchainImage(t->swapchain, &release));
		return;
	}
	t->filled[index] = true;

	VkCommandBufferBeginInfo begin = {
	    .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
	    .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
	};
	VK_CHECK(vkResetCommandBuffer(g->cmd, 0));
	VK_CHECK(vkBeginCommandBuffer(g->cmd, &begin));
	// The whole image is overwritten: its previous contents do not matter.
	barrier(g->cmd, t->images[index], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0,
	        VK_ACCESS_TRANSFER_WRITE_BIT);
	VkBufferImageCopy region = {
	    .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
	    .imageExtent = {t->width, t->height, 1},
	};
	vkCmdCopyBufferToImage(g->cmd, t->buffer, t->images[index], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);
	// OpenXR takes Vulkan swapchain images back in COLOR_ATTACHMENT_OPTIMAL.
	barrier(g->cmd, t->images[index], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
	        VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_COLOR_ATTACHMENT_READ_BIT);
	VK_CHECK(vkEndCommandBuffer(g->cmd));
	VkSubmitInfo submit = {
	    .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
	    .commandBufferCount = 1,
	    .pCommandBuffers = &g->cmd,
	};
	VK_CHECK(vkResetFences(g->device, 1, &g->fence));
	VK_CHECK(vkQueueSubmit(g->queue, 1, &submit, g->fence));
	VK_CHECK(vkWaitForFences(g->device, 1, &g->fence, VK_TRUE, UINT64_MAX));
	CHECK(xrReleaseSwapchainImage(t->swapchain, &release));
}

static void
create_gpu(XrInstance instance, XrSystemId system, struct gpu *g)
{
	PFN_xrGetVulkanGraphicsRequirements2KHR requirements;
	PFN_xrCreateVulkanInstanceKHR create_instance;
	PFN_xrGetVulkanGraphicsDevice2KHR get_device;
	PFN_xrCreateVulkanDeviceKHR create_device;
	CHECK(xrGetInstanceProcAddr(instance, "xrGetVulkanGraphicsRequirements2KHR", (PFN_xrVoidFunction *)&requirements));
	CHECK(xrGetInstanceProcAddr(instance, "xrCreateVulkanInstanceKHR", (PFN_xrVoidFunction *)&create_instance));
	CHECK(xrGetInstanceProcAddr(instance, "xrGetVulkanGraphicsDevice2KHR", (PFN_xrVoidFunction *)&get_device));
	CHECK(xrGetInstanceProcAddr(instance, "xrCreateVulkanDeviceKHR", (PFN_xrVoidFunction *)&create_device));

	XrGraphicsRequirementsVulkan2KHR reqs = {.type = XR_TYPE_GRAPHICS_REQUIREMENTS_VULKAN2_KHR};
	CHECK(requirements(instance, system, &reqs));

	VkApplicationInfo app = {
	    .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
	    .pApplicationName = "pico4-openxr-display",
	    .apiVersion = VK_API_VERSION_1_1,
	};
	VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
	XrVulkanInstanceCreateInfoKHR xici = {
	    .type = XR_TYPE_VULKAN_INSTANCE_CREATE_INFO_KHR,
	    .systemId = system,
	    .pfnGetInstanceProcAddr = vkGetInstanceProcAddr,
	    .vulkanCreateInfo = &ici,
	};
	VkResult vr;
	CHECK(create_instance(instance, &xici, &g->instance, &vr));
	VK_CHECK(vr);

	XrVulkanGraphicsDeviceGetInfoKHR gi = {
	    .type = XR_TYPE_VULKAN_GRAPHICS_DEVICE_GET_INFO_KHR,
	    .systemId = system,
	    .vulkanInstance = g->instance,
	};
	CHECK(get_device(instance, &gi, &g->physical));

	VkQueueFamilyProperties families[8];
	uint32_t count = 8;
	vkGetPhysicalDeviceQueueFamilyProperties(g->physical, &count, families);
	g->family = UINT32_MAX;
	for (uint32_t i = 0; i < count && g->family == UINT32_MAX; i++) {
		if (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
			g->family = i;
		}
	}
	if (g->family == UINT32_MAX) {
		fail("graphics queue family", 0);
	}
	float priority = 1.0f;
	VkDeviceQueueCreateInfo qi = {
	    .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
	    .queueFamilyIndex = g->family,
	    .queueCount = 1,
	    .pQueuePriorities = &priority,
	};
	VkDeviceCreateInfo dci = {
	    .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
	    .queueCreateInfoCount = 1,
	    .pQueueCreateInfos = &qi,
	};
	XrVulkanDeviceCreateInfoKHR xdci = {
	    .type = XR_TYPE_VULKAN_DEVICE_CREATE_INFO_KHR,
	    .systemId = system,
	    .pfnGetInstanceProcAddr = vkGetInstanceProcAddr,
	    .vulkanPhysicalDevice = g->physical,
	    .vulkanCreateInfo = &dci,
	};
	CHECK(create_device(instance, &xdci, &g->device, &vr));
	VK_CHECK(vr);
	vkGetDeviceQueue(g->device, g->family, 0, &g->queue);

	VkCommandPoolCreateInfo pi = {
	    .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
	    .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
	    .queueFamilyIndex = g->family,
	};
	VK_CHECK(vkCreateCommandPool(g->device, &pi, NULL, &g->pool));
	VkCommandBufferAllocateInfo cai = {
	    .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
	    .commandPool = g->pool,
	    .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
	    .commandBufferCount = 1,
	};
	VK_CHECK(vkAllocateCommandBuffers(g->device, &cai, &g->cmd));
	VkFenceCreateInfo fi = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
	VK_CHECK(vkCreateFence(g->device, &fi, NULL, &g->fence));
}

static const char *
format_name(int64_t f)
{
	switch (f) {
	case VK_FORMAT_R8G8B8A8_SRGB: return "R8G8B8A8_SRGB";
	case VK_FORMAT_B8G8R8A8_SRGB: return "B8G8R8A8_SRGB";
	case VK_FORMAT_R8G8B8A8_UNORM: return "R8G8B8A8_UNORM";
	case VK_FORMAT_B8G8R8A8_UNORM: return "B8G8R8A8_UNORM";
	default: return "other";
	}
}

int
main(int argc, char **argv)
{
	report_open();
	double seconds = argc > 1 ? atof(argv[1]) : 10.0;
	bool use_quad = !(argc > 2 && strcmp(argv[2], "--projection-only") == 0);
	if (seconds <= 0 || seconds > 3600 || argc > 3 || (argc > 2 && use_quad)) {
		fprintf(stderr, "usage: pico4-openxr-display [SECONDS] [--projection-only]\n");
		return 2;
	}

	const char *extensions[] = {XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME};
	XrInstanceCreateInfo ici = {
	    .type = XR_TYPE_INSTANCE_CREATE_INFO,
	    .applicationInfo = {.applicationName = "pico4-openxr-display", .apiVersion = XR_API_VERSION_1_0},
	    .enabledExtensionCount = 1,
	    .enabledExtensionNames = extensions,
	};
	XrInstance instance;
	CHECK(xrCreateInstance(&ici, &instance));
	XrSystemGetInfo sgi = {.type = XR_TYPE_SYSTEM_GET_INFO, .formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY};
	XrSystemId system;
	CHECK(xrGetSystem(instance, &sgi, &system));
	XrSystemProperties sp = {.type = XR_TYPE_SYSTEM_PROPERTIES};
	CHECK(xrGetSystemProperties(instance, system, &sp));

	struct gpu g = {0};
	create_gpu(instance, system, &g);
	XrGraphicsBindingVulkan2KHR binding = {
	    .type = XR_TYPE_GRAPHICS_BINDING_VULKAN2_KHR,
	    .instance = g.instance,
	    .physicalDevice = g.physical,
	    .device = g.device,
	    .queueFamilyIndex = g.family,
	    .queueIndex = 0,
	};
	XrSessionCreateInfo sci = {.type = XR_TYPE_SESSION_CREATE_INFO, .next = &binding, .systemId = system};
	XrSession session;
	CHECK(xrCreateSession(instance, &sci, &session));

	XrReferenceSpaceCreateInfo rsci = {
	    .type = XR_TYPE_REFERENCE_SPACE_CREATE_INFO,
	    .referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL,
	    .poseInReferenceSpace = {.orientation = {0, 0, 0, 1}},
	};
	XrSpace local;
	CHECK(xrCreateReferenceSpace(session, &rsci, &local));

	XrViewConfigurationView config[2] = {{.type = XR_TYPE_VIEW_CONFIGURATION_VIEW},
	                                     {.type = XR_TYPE_VIEW_CONFIGURATION_VIEW}};
	uint32_t view_count;
	CHECK(xrEnumerateViewConfigurationViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 2,
	                                        &view_count, config));

	int64_t formats[64], format = 0;
	uint32_t format_count;
	CHECK(xrEnumerateSwapchainFormats(session, 64, &format_count, formats));
	const int64_t wanted[] = {VK_FORMAT_R8G8B8A8_SRGB, VK_FORMAT_B8G8R8A8_SRGB, VK_FORMAT_R8G8B8A8_UNORM,
	                          VK_FORMAT_B8G8R8A8_UNORM};
	for (size_t w = 0; w < 4 && !format; w++) {
		for (uint32_t i = 0; i < format_count && !format; i++) {
			if (formats[i] == wanted[w]) {
				format = wanted[w];
			}
		}
	}
	if (!format) {
		fail("no 8-bit RGBA swapchain format", 0);
	}
	bool bgra = format == VK_FORMAT_B8G8R8A8_SRGB || format == VK_FORMAT_B8G8R8A8_UNORM;

	struct target eyes[2] = {{0}}, quad = {0};
	for (int e = 0; e < 2; e++) {
		create_target(session, &g, format, config[e].recommendedImageRectWidth, config[e].recommendedImageRectHeight,
		              &eyes[e]);
		fill_eye(map(&g, &eyes[e]), eyes[e].width, eyes[e].height, e, bgra);
	}
	create_target(session, &g, format, QUAD_SIZE, QUAD_SIZE, &quad);
	fill_quad(map(&g, &quad), bgra);

	bool running = false, done = false, exit_requested = false;
	unsigned frames = 0, rendered = 0, late = 0, located = 0, untracked = 0, counted = 0;
	double start = 0, last_wait = 0, max_interval = 0, period = 0, warm = 0;
	XrTime first_display = 0, last_display = 0;
	while (!done) {
		XrEventDataBuffer ev = {.type = XR_TYPE_EVENT_DATA_BUFFER};
		while (xrPollEvent(instance, &ev) == XR_SUCCESS) {
			if (ev.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
				XrSessionState state = ((XrEventDataSessionStateChanged *)&ev)->state;
				if (state == XR_SESSION_STATE_READY) {
					XrSessionBeginInfo bi = {
					    .type = XR_TYPE_SESSION_BEGIN_INFO,
					    .primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
					};
					CHECK(xrBeginSession(session, &bi));
					running = true;
					start = now_s();
				} else if (state == XR_SESSION_STATE_STOPPING) {
					CHECK(xrEndSession(session));
					running = false;
					done = true;
				} else if (state == XR_SESSION_STATE_EXITING || state == XR_SESSION_STATE_LOSS_PENDING) {
					running = false;
					done = true;
				}
			}
			ev = (XrEventDataBuffer){.type = XR_TYPE_EVENT_DATA_BUFFER};
		}
		if (!running) {
			if (!done && start == 0) {
				struct timespec t = {0, 10 * 1000 * 1000};
				nanosleep(&t, NULL);
			}
			continue;
		}

		XrFrameState fs = {.type = XR_TYPE_FRAME_STATE};
		XrFrameWaitInfo fwi = {.type = XR_TYPE_FRAME_WAIT_INFO};
		CHECK(xrWaitFrame(session, &fwi, &fs));
		double t = now_s();
		period = fs.predictedDisplayPeriod / 1e9;
		if (frames > 0 && t - start > 1.0) {
			double interval = t - last_wait;
			if (interval > max_interval) {
				max_interval = interval;
			}
			if (interval > 1.5 * period) {
				late++;
			}
			if (warm == 0) {
				warm = t;
				first_display = fs.predictedDisplayTime;
			} else {
				counted++;
			}
			last_display = fs.predictedDisplayTime;
		}
		last_wait = t;
		frames++;

		XrFrameBeginInfo fbi = {.type = XR_TYPE_FRAME_BEGIN_INFO};
		CHECK(xrBeginFrame(session, &fbi));

		XrCompositionLayerProjectionView pv[2];
		XrCompositionLayerProjection projection = {
		    .type = XR_TYPE_COMPOSITION_LAYER_PROJECTION,
		    .space = local,
		    .viewCount = 2,
		    .views = pv,
		};
		XrCompositionLayerQuad quad_layer = {
		    .type = XR_TYPE_COMPOSITION_LAYER_QUAD,
		    .space = local,
		    .eyeVisibility = XR_EYE_VISIBILITY_BOTH,
		    .subImage = {.swapchain = quad.swapchain, .imageRect = {{0, 0}, {QUAD_SIZE, QUAD_SIZE}}},
		    .pose = {.orientation = {0, 0, 0, 1}, .position = {0, 0, -1.5f}},
		    .size = {0.6f, 0.6f},
		};
		const XrCompositionLayerBaseHeader *layers[2];
		uint32_t layer_count = 0;
		if (fs.shouldRender) {
			XrView views[2] = {{.type = XR_TYPE_VIEW}, {.type = XR_TYPE_VIEW}};
			XrViewState vs = {.type = XR_TYPE_VIEW_STATE};
			XrViewLocateInfo vli = {
			    .type = XR_TYPE_VIEW_LOCATE_INFO,
			    .viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
			    .displayTime = fs.predictedDisplayTime,
			    .space = local,
			};
			uint32_t n;
			CHECK(xrLocateViews(session, &vli, &vs, 2, &n, views));
			located++;
			if (t - start > 1.0 && !(vs.viewStateFlags & XR_VIEW_STATE_ORIENTATION_TRACKED_BIT)) {
				untracked++;
			}
			for (int e = 0; e < 2; e++) {
				upload(&g, &eyes[e]);
				pv[e] = (XrCompositionLayerProjectionView){
				    .type = XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW,
				    .pose = views[e].pose,
				    .fov = views[e].fov,
				    .subImage = {.swapchain = eyes[e].swapchain,
				                 .imageRect = {{0, 0}, {(int32_t)eyes[e].width, (int32_t)eyes[e].height}}},
				};
			}
			layers[layer_count++] = (const XrCompositionLayerBaseHeader *)&projection;
			if (use_quad) {
				upload(&g, &quad);
				layers[layer_count++] = (const XrCompositionLayerBaseHeader *)&quad_layer;
			}
			rendered++;
		}
		XrFrameEndInfo fei = {
		    .type = XR_TYPE_FRAME_END_INFO,
		    .displayTime = fs.predictedDisplayTime,
		    .environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE,
		    .layerCount = layer_count,
		    .layers = layers,
		};
		CHECK(xrEndFrame(session, &fei));

		if (!exit_requested && t - start >= seconds) {
			CHECK(xrRequestExitSession(session));
			exit_requested = true;
		}
	}

	// Frames after the first second, against wall time and against predicted display time.
	double frame_hz = counted > 0 ? counted / (last_wait - warm) : 0;
	double display_hz = counted > 0 ? counted / ((last_display - first_display) / 1e9) : 0;
	double display_rate = period > 0 ? 1.0 / period : 0;
	bool ok = rendered > 0 && frame_hz >= 0.95 * display_rate && late <= counted / 100 + 1 && untracked == 0;
	fprintf(report(), "{\"schema\": 1, \"system\": \"%s\", \"swapchain_format\": \"%s\", \"eye_size\": [%u, %u], "
	                  "\"layers\": %d, \"seconds\": %.2f, \"frames\": %u, \"rendered\": %u, \"display_period_ms\": %.3f, \"frame_hz\": %.3f, "
	                  "\"display_time_hz\": %.3f, \"late_frames\": %u, \"max_frame_interval_ms\": %.2f, \"located\": %u, "
	                  "\"untracked_after_warmup\": %u, \"status\": \"%s\"}\n",
	                  sp.systemName, format_name(format), eyes[0].width, eyes[0].height, use_quad ? 2 : 1, last_wait - start, frames, rendered,
	                  period * 1e3, frame_hz, display_hz, late, max_interval * 1e3, located, untracked, ok ? "pass" : "fail");

	for (int e = 0; e < 2; e++) {
		xrDestroySwapchain(eyes[e].swapchain);
	}
	xrDestroySwapchain(quad.swapchain);
	xrDestroySpace(local);
	xrDestroySession(session);
	xrDestroyInstance(instance);
	return ok ? 0 : 1;
}
