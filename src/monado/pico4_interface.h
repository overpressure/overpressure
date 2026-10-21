// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Interface to the PICO 4 driver (stock kernel, IMU through the sensor DSP).
 * @ingroup drv_pico4
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*!
 * @defgroup drv_pico4 PICO 4 driver
 * @ingroup drv
 *
 * @brief Head orientation of the original PICO 4 from its ICM-42688 IMU, read
 * through the Qualcomm Sensor Core on the SLPI (QMI over QRTR).
 */

struct xrt_auto_prober;
struct xrt_device;

/*!
 * Create the HMD device; NULL if the sensor DSP or its IMU is not available.
 *
 * @ingroup drv_pico4
 */
/*!
 * Auto-prober: creates the HMD on a PICO 4 (an SLPI with an SNS client service).
 *
 * @ingroup drv_pico4
 */
struct xrt_auto_prober *
pico4_create_auto_prober(void);

/*!
 * The inverse of the PICO 4 HMD's distortion function for one channel (0 red,
 * 1 green, 2 blue): the panel UV of @p view that shows render-target UV (u, v).
 * False for other devices or where no panel point shows that UV. SteamVR's Facet
 * renderer builds its mesh from this (IVRDisplayComponent_003).
 *
 * @ingroup drv_pico4
 */
bool
pico4_hmd_compute_inverse_distortion(
    struct xrt_device *xdev, uint32_t view, uint32_t channel, float u, float v, float *out_u, float *out_v);

#ifdef __cplusplus
}
#endif
