// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Interface to the PICO 4 driver (stock kernel, IMU through the sensor DSP).
 * @ingroup drv_pico4
 */

#pragma once

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
struct xrt_device *
pico4_hmd_create(void);

/*!
 * Auto-prober: creates the HMD on a PICO 4 (an SLPI with an SNS client service).
 *
 * @ingroup drv_pico4
 */
struct xrt_auto_prober *
pico4_create_auto_prober(void);

#ifdef __cplusplus
}
#endif
