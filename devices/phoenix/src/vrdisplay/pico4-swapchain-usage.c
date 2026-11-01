// SPDX-License-Identifier: MIT
/*
 * An implicit Vulkan layer for SteamVR's compositor on a mainline kernel: its swapchains without
 * the storage usage it asks for and never uses.
 *
 * The A650 cannot compress storage images, so Mesa's display WSI would scan out uncompressed
 * images, which costs the compositor about 5 ms of GPU time per frame: 60-66 frames per second
 * instead of 90. Without that usage it resolves straight into compressed scanout images, with
 * Valve's Mesa as it is (docs/kernel-feasibility.md). Every other process passes through, and so
 * does the stock kernel, whose Turnip does the same with a driconf option.
 */

#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <vulkan/vk_layer.h>
#include <vulkan/vulkan.h>

#define EXPORT __attribute__((visibility("default")))
#define MAX_DEVICES 16

struct device
{
	VkDevice handle;
	PFN_vkGetDeviceProcAddr next_get_device_proc_addr;
	PFN_vkCreateSwapchainKHR next_create_swapchain;
};

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static struct device devices[MAX_DEVICES];
static PFN_vkGetInstanceProcAddr next_get_instance_proc_addr;

static bool
is_compositor(void)
{
	return strcmp(program_invocation_short_name, "vrcompositor") == 0 && access("/dev/kgsl-3d0", F_OK) != 0;
}

static struct device *
find_device(VkDevice handle)
{
	for (int i = 0; i < MAX_DEVICES; i++) {
		if (devices[i].handle == handle) {
			return &devices[i];
		}
	}
	return NULL;
}

static VKAPI_ATTR VkResult VKAPI_CALL
create_swapchain(VkDevice device,
                 const VkSwapchainCreateInfoKHR *create_info,
                 const VkAllocationCallbacks *allocator,
                 VkSwapchainKHR *swapchain)
{
	pthread_mutex_lock(&lock);
	const struct device *d = find_device(device);
	PFN_vkCreateSwapchainKHR next = d != NULL ? d->next_create_swapchain : NULL;
	pthread_mutex_unlock(&lock);
	if (next == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	VkSwapchainCreateInfoKHR info = *create_info;
	if (is_compositor()) {
		info.imageUsage &= ~VK_IMAGE_USAGE_STORAGE_BIT;
	}
	return next(device, &info, allocator, swapchain);
}

static VKAPI_ATTR VkResult VKAPI_CALL
create_instance(const VkInstanceCreateInfo *create_info, const VkAllocationCallbacks *allocator, VkInstance *instance)
{
	VkLayerInstanceCreateInfo *chain = (VkLayerInstanceCreateInfo *)create_info->pNext;
	while (chain != NULL && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO &&
	                          chain->function == VK_LAYER_LINK_INFO)) {
		chain = (VkLayerInstanceCreateInfo *)chain->pNext;
	}
	if (chain == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	PFN_vkGetInstanceProcAddr next = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
	PFN_vkCreateInstance next_create = (PFN_vkCreateInstance)next(VK_NULL_HANDLE, "vkCreateInstance");
	chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
	if (next_create == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	pthread_mutex_lock(&lock);
	next_get_instance_proc_addr = next;
	pthread_mutex_unlock(&lock);
	return next_create(create_info, allocator, instance);
}

static VKAPI_ATTR VkResult VKAPI_CALL
create_device(VkPhysicalDevice physical_device,
              const VkDeviceCreateInfo *create_info,
              const VkAllocationCallbacks *allocator,
              VkDevice *device)
{
	VkLayerDeviceCreateInfo *chain = (VkLayerDeviceCreateInfo *)create_info->pNext;
	while (chain != NULL && !(chain->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO &&
	                          chain->function == VK_LAYER_LINK_INFO)) {
		chain = (VkLayerDeviceCreateInfo *)chain->pNext;
	}
	if (chain == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	PFN_vkGetInstanceProcAddr next_instance = chain->u.pLayerInfo->pfnNextGetInstanceProcAddr;
	PFN_vkGetDeviceProcAddr next_device = chain->u.pLayerInfo->pfnNextGetDeviceProcAddr;
	PFN_vkCreateDevice next_create = (PFN_vkCreateDevice)next_instance(VK_NULL_HANDLE, "vkCreateDevice");
	chain->u.pLayerInfo = chain->u.pLayerInfo->pNext;
	if (next_create == NULL) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	VkResult result = next_create(physical_device, create_info, allocator, device);
	if (result != VK_SUCCESS) {
		return result;
	}
	pthread_mutex_lock(&lock);
	struct device *d = find_device(VK_NULL_HANDLE);
	if (d != NULL) {
		d->handle = *device;
		d->next_get_device_proc_addr = next_device;
		d->next_create_swapchain = (PFN_vkCreateSwapchainKHR)next_device(*device, "vkCreateSwapchainKHR");
	}
	pthread_mutex_unlock(&lock);
	return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
destroy_device(VkDevice device, const VkAllocationCallbacks *allocator)
{
	pthread_mutex_lock(&lock);
	struct device *d = find_device(device);
	PFN_vkDestroyDevice next = NULL;
	if (d != NULL) {
		next = (PFN_vkDestroyDevice)d->next_get_device_proc_addr(device, "vkDestroyDevice");
		memset(d, 0, sizeof(*d));
	}
	pthread_mutex_unlock(&lock);
	if (next != NULL) {
		next(device, allocator);
	}
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL get_device_proc_addr(VkDevice device, const char *name);

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
get_instance_proc_addr(VkInstance instance, const char *name)
{
	if (strcmp(name, "vkGetInstanceProcAddr") == 0) {
		return (PFN_vkVoidFunction)get_instance_proc_addr;
	}
	if (strcmp(name, "vkCreateInstance") == 0) {
		return (PFN_vkVoidFunction)create_instance;
	}
	if (strcmp(name, "vkCreateDevice") == 0) {
		return (PFN_vkVoidFunction)create_device;
	}
	if (strcmp(name, "vkGetDeviceProcAddr") == 0) {
		return (PFN_vkVoidFunction)get_device_proc_addr;
	}
	pthread_mutex_lock(&lock);
	PFN_vkGetInstanceProcAddr next = next_get_instance_proc_addr;
	pthread_mutex_unlock(&lock);
	return next != NULL ? next(instance, name) : NULL;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
get_device_proc_addr(VkDevice device, const char *name)
{
	if (strcmp(name, "vkGetDeviceProcAddr") == 0) {
		return (PFN_vkVoidFunction)get_device_proc_addr;
	}
	if (strcmp(name, "vkDestroyDevice") == 0) {
		return (PFN_vkVoidFunction)destroy_device;
	}
	pthread_mutex_lock(&lock);
	const struct device *d = find_device(device);
	PFN_vkGetDeviceProcAddr next = d != NULL ? d->next_get_device_proc_addr : NULL;
	bool has_swapchain = d != NULL && d->next_create_swapchain != NULL;
	pthread_mutex_unlock(&lock);
	if (strcmp(name, "vkCreateSwapchainKHR") == 0 && has_swapchain) {
		return (PFN_vkVoidFunction)create_swapchain;
	}
	return next != NULL ? next(device, name) : NULL;
}

EXPORT VKAPI_ATTR VkResult VKAPI_CALL
pico4_swapchain_usage_negotiate(VkNegotiateLayerInterface *version)
{
	if (version->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT || version->loaderLayerInterfaceVersion < 2) {
		return VK_ERROR_INITIALIZATION_FAILED;
	}
	version->loaderLayerInterfaceVersion = 2;
	version->pfnGetInstanceProcAddr = get_instance_proc_addr;
	version->pfnGetDeviceProcAddr = get_device_proc_addr;
	version->pfnGetPhysicalDeviceProcAddr = NULL;
	return VK_SUCCESS;
}
