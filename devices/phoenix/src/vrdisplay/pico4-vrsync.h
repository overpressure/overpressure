// SPDX-License-Identifier: BSL-1.0
/*
 * Exportable (OPAQUE_FD) semaphores emulated with sync files, for drivers and
 * kernels that cannot share a semaphore between processes: see pico4-vrsync.c.
 * Part of VK_LAYER_PICO4_vrdisplay, which forwards its lifecycle calls here.
 */
#pragma once

#include <stdbool.h>
#include <vulkan/vulkan.h>

//! The layer's log (pico4-vrdisplay.c).
void
vrdisplay_log(bool debug, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

void
vrsync_instance_created(VkInstance instance, PFN_vkGetInstanceProcAddr gipa);

void
vrsync_instance_destroyed(VkInstance instance);

void
vrsync_device_created(VkPhysicalDevice physical, VkDevice device, PFN_vkGetDeviceProcAddr gdpa);

//! Before the device is destroyed: releases this module's objects of it.
void
vrsync_device_destroyed(VkDevice device);

//! This module's entry point for a device-level command, or NULL.
PFN_vkVoidFunction
vrsync_device_function(const char *name);

//! This module's entry point for an instance-level command, or NULL.
PFN_vkVoidFunction
vrsync_instance_function(const char *name);
