// Copyright 2026, overpressure contributors.
// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-lens-panel: the lens distance in SteamVR's dashboard (docs/xr.md#lens-distortion-and-ipd).
 *
 * SteamVR shows the IPD when it changes but has no control that sets it. This panel has one, for
 * people taking turns with the headset: a slider, buttons for half a millimetre, and four presets
 * (a press moves the lenses, holding one stores the distance in it). pico4-lens moves the lenses,
 * and the driver hands the new distance to SteamVR, whose own display shows it. Nothing of Valve's
 * is linked: libopenvr_api.so is opened at run time.
 *
 * The images go to SteamVR as Vulkan textures: with one through SetOverlayRaw or SetOverlayFromFile
 * alive, the compositor crashes in its cleanup at every stop (docs/session.md).
 *
 *   pico4-lens-panel [--open] [--render FILE.png MM]
 *
 * --open opens the dashboard on the panel. --render draws it into a file, without SteamVR.
 */
#include "pico4_lens_panel.h"

#include <cairo/cairo.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "openvr.h"

namespace {

constexpr const char *kOpenvrApi = "/opt/steamvr/bin/linuxarm64/libopenvr_api.so";
constexpr const char *kFont = "Adwaita Sans";

volatile sig_atomic_t g_stop;

void
stop_requested(int)
{
	g_stop = 1;
}

struct State
{
	double mm = 0;             //!< What the driver reports; 0 before the first reading.
	double target = 0;         //!< Where the lenses are going, or a slider being dragged; 0: nowhere.
	double presets[PICO4_PANEL_PRESETS] = {};
	int pressed = PICO4_PANEL_NONE;
	bool stored = false;       //!< The pressed preset was just stored.
	double pressed_at = 0;
	std::string message;
	pid_t mover = -1;
	double pending = 0;        //!< A move asked for while another one runs.
};

double
now_s()
{
	timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec / 1e9;
}

std::string
presets_path()
{
	const char *home = getenv("HOME");
	std::string dir = std::string(home != nullptr ? home : "/tmp") + "/.config/pico4-lens";
	mkdir((std::string(home != nullptr ? home : "/tmp") + "/.config").c_str(), 0755);
	mkdir(dir.c_str(), 0755);
	return dir + "/presets";
}

void
start_move(State &s, double mm)
{
	if (s.mover > 0) {
		s.pending = mm;
		return;
	}
	char value[16];
	snprintf(value, sizeof(value), "%.2f", mm);
	pid_t pid = fork();
	if (pid == 0) {
		int null = open("/dev/null", O_WRONLY);
		dup2(null, 1);
		execlp("pico4-lens", "pico4-lens", "set", value, (char *)nullptr);
		_exit(127);
	}
	if (pid < 0) {
		s.message = "Could not move the lenses";
		return;
	}
	s.mover = pid;
	s.target = mm;
	s.message.clear();
}

void
reap_mover(State &s)
{
	int status;
	if (s.mover <= 0 || waitpid(s.mover, &status, WNOHANG) != s.mover) {
		return;
	}
	s.mover = -1;
	if (!(WIFEXITED(status) && WEXITSTATUS(status) == 0)) {
		s.message = "The lenses did not reach it";
	}
	if (s.pending > 0) {
		double next = s.pending;
		s.pending = 0;
		start_move(s, next);
	}
}

void
rounded(cairo_t *c, double x, double y, double w, double h, double r)
{
	cairo_new_sub_path(c);
	cairo_arc(c, x + w - r, y + r, r, -M_PI / 2, 0);
	cairo_arc(c, x + w - r, y + h - r, r, 0, M_PI / 2);
	cairo_arc(c, x + r, y + h - r, r, M_PI / 2, M_PI);
	cairo_arc(c, x + r, y + r, r, M_PI, 1.5 * M_PI);
	cairo_close_path(c);
}

void
text_centered(cairo_t *c, const char *text, double x, double y, double size, cairo_font_weight_t weight)
{
	cairo_select_font_face(c, kFont, CAIRO_FONT_SLANT_NORMAL, weight);
	cairo_set_font_size(c, size);
	cairo_text_extents_t e;
	cairo_text_extents(c, text, &e);
	cairo_move_to(c, x - e.width / 2 - e.x_bearing, y);
	cairo_show_text(c, text);
}

void
button(cairo_t *c, double x, double y, double w, double h, bool pressed, bool selected)
{
	rounded(c, x, y, w, h, 14);
	if (pressed) {
		cairo_set_source_rgb(c, 0.20, 0.44, 0.72);
	} else if (selected) {
		cairo_set_source_rgb(c, 0.16, 0.32, 0.50);
	} else {
		cairo_set_source_rgb(c, 0.22, 0.24, 0.27);
	}
	cairo_fill(c);
}

void
draw(cairo_surface_t *surface, const State &s)
{
	cairo_t *c = cairo_create(surface);
	cairo_set_source_rgb(c, 0.11, 0.12, 0.14);
	cairo_paint(c);

	double shown = s.target > 0 ? s.target : s.mm;
	char line[64];
	cairo_set_source_rgb(c, 0.70, 0.73, 0.77);
	text_centered(c, "Lens distance", PICO4_PANEL_W / 2.0, 52, 26, CAIRO_FONT_WEIGHT_NORMAL);
	cairo_set_source_rgb(c, 1, 1, 1);
	snprintf(line, sizeof(line), shown > 0 ? "%.1f mm" : "--", shown);
	text_centered(c, line, PICO4_PANEL_W / 2.0, 140, 76, CAIRO_FONT_WEIGHT_BOLD);

	const double row = PICO4_PANEL_ROW1_Y, h = PICO4_PANEL_ROW1_H, w = PICO4_PANEL_BUTTON_W;
	const double plus_x = PICO4_PANEL_W - PICO4_PANEL_BUTTON_X0 - w;
	button(c, PICO4_PANEL_BUTTON_X0, row, w, h, s.pressed == PICO4_PANEL_MINUS, false);
	button(c, plus_x, row, w, h, s.pressed == PICO4_PANEL_PLUS, false);
	cairo_set_source_rgb(c, 1, 1, 1);
	text_centered(c, "-", PICO4_PANEL_BUTTON_X0 + w / 2, row + 57, 52, CAIRO_FONT_WEIGHT_BOLD);
	text_centered(c, "+", plus_x + w / 2, row + 57, 52, CAIRO_FONT_WEIGHT_BOLD);

	// The slider: a track, its filled part and a knob at the distance.
	const double track = row + h / 2;
	rounded(c, PICO4_PANEL_SLIDER_X0, track - 5, PICO4_PANEL_SLIDER_X1 - PICO4_PANEL_SLIDER_X0, 10, 5);
	cairo_set_source_rgb(c, 0.30, 0.33, 0.37);
	cairo_fill(c);
	if (shown > 0) {
		const double knob = pico4_panel_slider_x(shown);
		rounded(c, PICO4_PANEL_SLIDER_X0, track - 5, knob - PICO4_PANEL_SLIDER_X0, 10, 5);
		cairo_set_source_rgb(c, 0.27, 0.56, 0.90);
		cairo_fill(c);
		cairo_arc(c, knob, track, s.pressed == PICO4_PANEL_SLIDER ? 22 : 18, 0, 2 * M_PI);
		cairo_set_source_rgb(c, 1, 1, 1);
		cairo_fill(c);
	}
	cairo_set_source_rgb(c, 0.60, 0.63, 0.67);
	text_centered(c, "62", PICO4_PANEL_SLIDER_X0 + 8, row + h + 12, 18, CAIRO_FONT_WEIGHT_NORMAL);
	text_centered(c, "72", PICO4_PANEL_SLIDER_X1 - 8, row + h + 12, 18, CAIRO_FONT_WEIGHT_NORMAL);

	for (int i = 0; i < PICO4_PANEL_PRESETS; i++) {
		const double x = PICO4_PANEL_PRESET_X0 + i * (PICO4_PANEL_PRESET_W + PICO4_PANEL_PRESET_GAP);
		const double y = PICO4_PANEL_ROW2_Y;
		const bool here = s.presets[i] > 0 && shown > 0 && fabs(s.presets[i] - shown) < 0.15;
		button(c, x, y, PICO4_PANEL_PRESET_W, PICO4_PANEL_ROW2_H, s.pressed == PICO4_PANEL_PRESET0 + i, here);
		cairo_set_source_rgb(c, 0.75, 0.78, 0.82);
		snprintf(line, sizeof(line), "User %d", i + 1);
		text_centered(c, line, x + PICO4_PANEL_PRESET_W / 2, y + 40, 22, CAIRO_FONT_WEIGHT_NORMAL);
		cairo_set_source_rgb(c, 1, 1, 1);
		if (s.pressed == PICO4_PANEL_PRESET0 + i && s.stored) {
			text_centered(c, "Stored", x + PICO4_PANEL_PRESET_W / 2, y + 82, 30, CAIRO_FONT_WEIGHT_BOLD);
		} else if (s.presets[i] > 0) {
			snprintf(line, sizeof(line), "%.1f", s.presets[i]);
			text_centered(c, line, x + PICO4_PANEL_PRESET_W / 2, y + 82, 36, CAIRO_FONT_WEIGHT_BOLD);
		} else {
			cairo_set_source_rgb(c, 0.55, 0.58, 0.62);
			text_centered(c, "empty", x + PICO4_PANEL_PRESET_W / 2, y + 80, 24, CAIRO_FONT_WEIGHT_NORMAL);
		}
	}

	cairo_set_source_rgb(c, s.message.empty() ? 0.60 : 0.95, s.message.empty() ? 0.63 : 0.55, s.message.empty() ? 0.67 : 0.45);
	text_centered(c, s.message.empty() ? "Press a user to move the lenses. Hold it to store the distance." : s.message.c_str(),
	              PICO4_PANEL_W / 2.0, 466, 22, CAIRO_FONT_WEIGHT_NORMAL);
	cairo_destroy(c);
	cairo_surface_flush(surface);
}

void
draw_thumbnail(cairo_surface_t *surface)
{
	cairo_t *c = cairo_create(surface);
	cairo_set_source_rgb(c, 0.11, 0.12, 0.14);
	cairo_paint(c);
	cairo_set_line_width(c, 12);
	cairo_set_source_rgb(c, 1, 1, 1);
	cairo_arc(c, 76, 128, 44, 0, 2 * M_PI);
	cairo_stroke(c);
	cairo_arc(c, 180, 128, 44, 0, 2 * M_PI);
	cairo_stroke(c);
	cairo_destroy(c);
	cairo_surface_flush(surface);
}

//! Cairo's premultiplied native ARGB as SteamVR's RGBA bytes.
void
to_rgba(cairo_surface_t *surface, std::vector<uint8_t> &out)
{
	const int w = cairo_image_surface_get_width(surface), h = cairo_image_surface_get_height(surface);
	const int stride = cairo_image_surface_get_stride(surface);
	const uint8_t *data = cairo_image_surface_get_data(surface);
	out.resize((size_t)w * h * 4);
	for (int y = 0; y < h; y++) {
		const uint32_t *in = reinterpret_cast<const uint32_t *>(data + y * stride);
		uint8_t *o = out.data() + (size_t)y * w * 4;
		for (int x = 0; x < w; x++, o += 4) {
			o[0] = (in[x] >> 16) & 255;
			o[1] = (in[x] >> 8) & 255;
			o[2] = in[x] & 255;
			o[3] = in[x] >> 24;
		}
	}
}

std::vector<std::string>
split_words(const char *text)
{
	std::vector<std::string> words;
	std::string word;
	for (const char *c = text;; c++) {
		if (*c == ' ' || *c == '\0') {
			if (!word.empty()) {
				words.push_back(word);
			}
			word.clear();
			if (*c == '\0') {
				return words;
			}
		} else {
			word += *c;
		}
	}
}

//! What every overlay image shares: the device SteamVR asks for, and a command buffer.
struct Gpu
{
	VkInstance instance = VK_NULL_HANDLE;
	VkPhysicalDevice physical = VK_NULL_HANDLE;
	VkDevice device = VK_NULL_HANDLE;
	VkQueue queue = VK_NULL_HANDLE;
	uint32_t family = 0;
	VkCommandBuffer commands = VK_NULL_HANDLE;
};

bool
gpu_create(Gpu &g, vr::IVRCompositor *compositor)
{
	char names[4096] = "";
	compositor->GetVulkanInstanceExtensionsRequired(names, sizeof(names));
	const std::vector<std::string> instance_words = split_words(names);
	std::vector<const char *> instance_extensions;
	for (const std::string &w : instance_words) {
		instance_extensions.push_back(w.c_str());
	}
	VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
	app.pApplicationName = "pico4-lens-panel";
	app.apiVersion = VK_API_VERSION_1_1;
	VkInstanceCreateInfo instance_info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
	instance_info.pApplicationInfo = &app;
	instance_info.enabledExtensionCount = (uint32_t)instance_extensions.size();
	instance_info.ppEnabledExtensionNames = instance_extensions.data();
	if (vkCreateInstance(&instance_info, nullptr, &g.instance) != VK_SUCCESS) {
		return false;
	}
	uint32_t count = 1;
	if (vkEnumeratePhysicalDevices(g.instance, &count, &g.physical) < 0 || count == 0) {
		return false;
	}
	uint32_t families = 0;
	vkGetPhysicalDeviceQueueFamilyProperties(g.physical, &families, nullptr);
	std::vector<VkQueueFamilyProperties> family_properties(families);
	vkGetPhysicalDeviceQueueFamilyProperties(g.physical, &families, family_properties.data());
	while (g.family < families && !(family_properties[g.family].queueFlags & VK_QUEUE_GRAPHICS_BIT)) {
		g.family++;
	}
	if (g.family == families) {
		return false;
	}
	names[0] = '\0';
	compositor->GetVulkanDeviceExtensionsRequired(g.physical, names, sizeof(names));
	const std::vector<std::string> device_words = split_words(names);
	std::vector<const char *> device_extensions;
	for (const std::string &w : device_words) {
		device_extensions.push_back(w.c_str());
	}
	const float priority = 0;
	VkDeviceQueueCreateInfo queue_info{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
	queue_info.queueFamilyIndex = g.family;
	queue_info.queueCount = 1;
	queue_info.pQueuePriorities = &priority;
	VkDeviceCreateInfo device_info{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
	device_info.queueCreateInfoCount = 1;
	device_info.pQueueCreateInfos = &queue_info;
	device_info.enabledExtensionCount = (uint32_t)device_extensions.size();
	device_info.ppEnabledExtensionNames = device_extensions.data();
	if (vkCreateDevice(g.physical, &device_info, nullptr, &g.device) != VK_SUCCESS) {
		return false;
	}
	vkGetDeviceQueue(g.device, g.family, 0, &g.queue);
	VkCommandPoolCreateInfo pool_info{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
	pool_info.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
	pool_info.queueFamilyIndex = g.family;
	VkCommandPool pool;
	if (vkCreateCommandPool(g.device, &pool_info, nullptr, &pool) != VK_SUCCESS) {
		return false;
	}
	VkCommandBufferAllocateInfo command_info{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
	command_info.commandPool = pool;
	command_info.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
	command_info.commandBufferCount = 1;
	return vkAllocateCommandBuffers(g.device, &command_info, &g.commands) == VK_SUCCESS;
}

bool
allocate(const Gpu &g, const VkMemoryRequirements &needs, VkMemoryPropertyFlags flags, VkDeviceMemory *out)
{
	VkPhysicalDeviceMemoryProperties properties;
	vkGetPhysicalDeviceMemoryProperties(g.physical, &properties);
	for (uint32_t i = 0; i < properties.memoryTypeCount; i++) {
		if ((needs.memoryTypeBits & (1u << i)) && (properties.memoryTypes[i].propertyFlags & flags) == flags) {
			VkMemoryAllocateInfo info{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
			info.allocationSize = needs.size;
			info.memoryTypeIndex = i;
			return vkAllocateMemory(g.device, &info, nullptr, out) == VK_SUCCESS;
		}
	}
	return false;
}

//! One overlay's image on the GPU and the buffer each redraw is copied through.
struct Texture
{
	uint32_t width = 0, height = 0;
	VkImage image = VK_NULL_HANDLE;
	VkBuffer staging = VK_NULL_HANDLE;
	void *mapped = nullptr;
	bool written = false;
};

bool
texture_create(const Gpu &g, Texture &t, uint32_t width, uint32_t height)
{
	t.width = width;
	t.height = height;
	VkImageCreateInfo image_info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
	image_info.imageType = VK_IMAGE_TYPE_2D;
	image_info.format = VK_FORMAT_R8G8B8A8_UNORM;
	image_info.extent = {width, height, 1};
	image_info.mipLevels = 1;
	image_info.arrayLayers = 1;
	image_info.samples = VK_SAMPLE_COUNT_1_BIT;
	image_info.tiling = VK_IMAGE_TILING_OPTIMAL;
	image_info.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
	if (vkCreateImage(g.device, &image_info, nullptr, &t.image) != VK_SUCCESS) {
		return false;
	}
	VkMemoryRequirements needs;
	vkGetImageMemoryRequirements(g.device, t.image, &needs);
	VkDeviceMemory image_memory, staging_memory;
	if (!allocate(g, needs, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, &image_memory) ||
	    vkBindImageMemory(g.device, t.image, image_memory, 0) != VK_SUCCESS) {
		return false;
	}
	VkBufferCreateInfo buffer_info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
	buffer_info.size = (VkDeviceSize)width * height * 4;
	buffer_info.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
	if (vkCreateBuffer(g.device, &buffer_info, nullptr, &t.staging) != VK_SUCCESS) {
		return false;
	}
	vkGetBufferMemoryRequirements(g.device, t.staging, &needs);
	return allocate(g, needs, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, &staging_memory) &&
	       vkBindBufferMemory(g.device, t.staging, staging_memory, 0) == VK_SUCCESS &&
	       vkMapMemory(g.device, staging_memory, 0, VK_WHOLE_SIZE, 0, &t.mapped) == VK_SUCCESS;
}

void
image_barrier(VkCommandBuffer commands, VkImage image, VkImageLayout from, VkImageLayout to, VkAccessFlags src, VkAccessFlags dst)
{
	VkImageMemoryBarrier barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
	barrier.srcAccessMask = src;
	barrier.dstAccessMask = dst;
	barrier.oldLayout = from;
	barrier.newLayout = to;
	barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.image = image;
	barrier.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
	vkCmdPipelineBarrier(commands, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &barrier);
}

//! The surface's pixels into the texture, then the texture to SteamVR, in the layout it takes them.
bool
texture_show(const Gpu &g, Texture &t, cairo_surface_t *surface, vr::IVROverlay *overlay, vr::VROverlayHandle_t handle)
{
	std::vector<uint8_t> rgba;
	to_rgba(surface, rgba);
	memcpy(t.mapped, rgba.data(), rgba.size());
	VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
	begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
	vkBeginCommandBuffer(g.commands, &begin);
	image_barrier(g.commands, t.image, t.written ? VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED,
	              VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT);
	VkBufferImageCopy copy{};
	copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
	copy.imageExtent = {t.width, t.height, 1};
	vkCmdCopyBufferToImage(g.commands, t.staging, t.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
	image_barrier(g.commands, t.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
	              VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT);
	vkEndCommandBuffer(g.commands);
	VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
	submit.commandBufferCount = 1;
	submit.pCommandBuffers = &g.commands;
	if (vkQueueSubmit(g.queue, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS || vkQueueWaitIdle(g.queue) != VK_SUCCESS) {
		return false;
	}
	t.written = true;
	vr::VRVulkanTextureData_t data{};
	data.m_nImage = (uint64_t)t.image;
	data.m_pDevice = g.device;
	data.m_pPhysicalDevice = g.physical;
	data.m_pInstance = g.instance;
	data.m_pQueue = g.queue;
	data.m_nQueueFamilyIndex = g.family;
	data.m_nWidth = t.width;
	data.m_nHeight = t.height;
	data.m_nFormat = VK_FORMAT_R8G8B8A8_UNORM;
	data.m_nSampleCount = 1;
	vr::Texture_t texture{&data, vr::TextureType_Vulkan, vr::ColorSpace_Auto};
	return overlay->SetOverlayTexture(handle, &texture) == vr::VROverlayError_None;
}

bool
handle_event(State &s, const vr::VREvent_t &e)
{
	const double x = e.data.mouse.x, y = PICO4_PANEL_H - e.data.mouse.y; // SteamVR's y points up.
	switch (e.eventType) {
	case vr::VREvent_MouseButtonDown: {
		s.pressed = pico4_panel_hit(x, y);
		s.pressed_at = now_s();
		s.stored = false;
		if (s.pressed == PICO4_PANEL_SLIDER) {
			s.target = pico4_panel_slider_mm(x);
		}
		return true;
	}
	case vr::VREvent_MouseMove:
		if (s.pressed == PICO4_PANEL_SLIDER) {
			s.target = pico4_panel_slider_mm(x);
			return true;
		}
		return false;
	case vr::VREvent_MouseButtonUp: {
		const double from = s.target > 0 ? s.target : s.mm;
		const int was = s.pressed;
		const bool stored = s.stored;
		s.pressed = PICO4_PANEL_NONE;
		s.stored = false;
		if (from <= 0) {
			return true;
		}
		if (was == PICO4_PANEL_MINUS) {
			start_move(s, pico4_panel_clamp(from - PICO4_PANEL_STEP_MM));
		} else if (was == PICO4_PANEL_PLUS) {
			start_move(s, pico4_panel_clamp(from + PICO4_PANEL_STEP_MM));
		} else if (was == PICO4_PANEL_SLIDER) {
			start_move(s, s.target);
		} else if (was >= PICO4_PANEL_PRESET0 && was < PICO4_PANEL_PRESET0 + PICO4_PANEL_PRESETS && !stored &&
		           s.presets[was] > 0) {
			start_move(s, s.presets[was]);
		}
		return true;
	}
	default: return false;
	}
}

int
render(const char *file, double mm)
{
	State s;
	s.mm = mm;
	s.presets[0] = 63.5;
	s.presets[1] = mm;
	cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, PICO4_PANEL_W, PICO4_PANEL_H);
	draw(surface, s);
	int r = cairo_surface_write_to_png(surface, file) == CAIRO_STATUS_SUCCESS ? 0 : 1;
	cairo_surface_destroy(surface);
	return r;
}

} // namespace

int
main(int argc, char **argv)
{
	if (argc == 4 && strcmp(argv[1], "--render") == 0) {
		return render(argv[2], atof(argv[3]));
	}
	signal(SIGPIPE, SIG_IGN);
	signal(SIGTERM, stop_requested);
	signal(SIGINT, stop_requested);

	void *lib = dlopen(kOpenvrApi, RTLD_NOW);
	if (lib == nullptr) {
		fprintf(stderr, "pico4-lens-panel: %s\n", dlerror());
		return 1;
	}
	auto vr_init = (uint32_t(*)(vr::EVRInitError *, vr::EVRApplicationType))dlsym(lib, "VR_InitInternal");
	auto vr_interface = (void *(*)(const char *, vr::EVRInitError *))dlsym(lib, "VR_GetGenericInterface");
	auto vr_shutdown = (void (*)())dlsym(lib, "VR_ShutdownInternal");
	if (vr_init == nullptr || vr_interface == nullptr || vr_shutdown == nullptr) {
		fprintf(stderr, "pico4-lens-panel: not an OpenVR library\n");
		return 1;
	}
	vr::EVRInitError err = vr::VRInitError_Driver_Failed;
	for (int i = 0; i < 60 && err != vr::VRInitError_None && !g_stop; i++) {
		vr_init(&err, vr::VRApplication_Background);
		if (err != vr::VRInitError_None && !g_stop) {
			sleep(2);
		}
	}
	if (g_stop && err != vr::VRInitError_None) {
		return 0;
	}
	if (err != vr::VRInitError_None) {
		fprintf(stderr, "pico4-lens-panel: VR_Init: %d\n", err);
		return 1;
	}
	auto *overlay = static_cast<vr::IVROverlay *>(vr_interface(vr::IVROverlay_Version, &err));
	auto *system = static_cast<vr::IVRSystem *>(vr_interface(vr::IVRSystem_Version, &err));
	auto *compositor = static_cast<vr::IVRCompositor *>(vr_interface(vr::IVRCompositor_Version, &err));
	if (overlay == nullptr || system == nullptr || compositor == nullptr) {
		fprintf(stderr, "pico4-lens-panel: no overlay interface: %d\n", err);
		return 1;
	}
	vr::VROverlayHandle_t main_overlay, thumbnail;
	vr::EVROverlayError oe = overlay->CreateDashboardOverlay("pico4.lens.panel", "Lens distance", &main_overlay, &thumbnail);
	if (oe != vr::VROverlayError_None) {
		fprintf(stderr, "pico4-lens-panel: dashboard overlay: %d\n", oe);
		return 1;
	}
	overlay->SetOverlayWidthInMeters(main_overlay, 2.0f);
	overlay->SetOverlayInputMethod(main_overlay, vr::VROverlayInputMethod_Mouse);
	vr::HmdVector2_t scale = {{(float)PICO4_PANEL_W, (float)PICO4_PANEL_H}};
	overlay->SetOverlayMouseScale(main_overlay, &scale);

	State s;
	const std::string path = presets_path();
	pico4_panel_presets_load(path.c_str(), s.presets);
	cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, PICO4_PANEL_W, PICO4_PANEL_H);
	Gpu gpu;
	Texture main_texture, thumbnail_texture;
	if (!gpu_create(gpu, compositor) || !texture_create(gpu, thumbnail_texture, 256, 256) ||
	    !texture_create(gpu, main_texture, PICO4_PANEL_W, PICO4_PANEL_H)) {
		fprintf(stderr, "pico4-lens-panel: no Vulkan textures\n");
		return 1;
	}
	{
		cairo_surface_t *small = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 256, 256);
		draw_thumbnail(small);
		if (!texture_show(gpu, thumbnail_texture, small, overlay, thumbnail)) {
			fprintf(stderr, "pico4-lens-panel: thumbnail not accepted\n");
		}
		cairo_surface_destroy(small);
	}
	if (argc == 2 && strcmp(argv[1], "--open") == 0) {
		overlay->ShowDashboard("pico4.lens.panel");
	}
	printf("pico4-lens-panel: in the dashboard\n");
	fflush(stdout);

	bool dirty = true, running = true, drawn = false;
	double next_read = 0;
	while (running && !g_stop) {
		vr::VREvent_t e;
		while (overlay->PollNextOverlayEvent(main_overlay, &e, sizeof(e))) {
			dirty = handle_event(s, e) || dirty;
		}
		while (system->PollNextEvent(&e, sizeof(e))) {
			if (e.eventType == vr::VREvent_Quit) {
				system->AcknowledgeQuit_Exiting();
				running = false;
			}
		}
		const double now = now_s();
		if (s.pressed >= PICO4_PANEL_PRESET0 && s.pressed < PICO4_PANEL_PRESET0 + PICO4_PANEL_PRESETS && !s.stored &&
		    now - s.pressed_at >= PICO4_PANEL_HOLD_S && s.mm > 0) {
			s.presets[s.pressed] = round(s.mm * 10) / 10;
			s.stored = true;
			if (!pico4_panel_presets_save(path.c_str(), s.presets)) {
				s.message = "Could not store it";
			}
			dirty = true;
		}
		const pid_t mover = s.mover;
		reap_mover(s);
		dirty = dirty || mover != s.mover;
		if (now >= next_read) {
			next_read = now + 0.25;
			vr::ETrackedPropertyError pe;
			const float m = system->GetFloatTrackedDeviceProperty(vr::k_unTrackedDeviceIndex_Hmd,
			                                                      vr::Prop_UserIpdMeters_Float, &pe);
			if (pe == vr::TrackedProp_Success && m > 0.05f && fabs(m * 1000 - s.mm) >= 0.05) {
				s.mm = m * 1000;
				dirty = true;
			}
			// The driver has caught up with the finished move.
			if (s.mover <= 0 && s.pressed != PICO4_PANEL_SLIDER && s.target > 0 && now - s.pressed_at > 2.5) {
				s.target = 0;
				dirty = true;
			}
		}
		const bool visible = overlay->IsOverlayVisible(main_overlay);
		// Once before anyone looks, as soon as the distance is known, so that the tab never opens blank.
		if (dirty && (visible || (!drawn && s.mm > 0))) {
			drawn = true;
			draw(surface, s);
			if (!texture_show(gpu, main_texture, surface, overlay, main_overlay)) {
				fprintf(stderr, "pico4-lens-panel: image not accepted\n");
			}
			dirty = false;
		}
		usleep(visible ? 16000 : 200000);
	}
	cairo_surface_destroy(surface);
	overlay->DestroyOverlay(main_overlay);
	vr_shutdown();
	return 0;
}
