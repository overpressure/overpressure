// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 controllers: inputs and 3DoF orientation from the controller radio's packets.
 * @ingroup drv_pico4
 */

#pragma once

#include "pico4_blemcu.h"

#include "xrt/xrt_device.h"
#include "util/u_logging.h"

#ifdef __cplusplus
extern "C" {
#endif

struct pico4_controller;

/*!
 * Controller @p device (PICO4_BLEMCU_LEFT or _RIGHT). Its position follows
 * @p head at a fixed offset (no optical controller tracking yet). The object
 * is shared by Monado (through the returned device) and the HMD's sensor
 * thread; each side lets go with its own call and the last one frees it.
 */
struct pico4_controller *
pico4_controller_create(int device, struct xrt_device *head, enum u_logging_level log_level);

struct xrt_device *
pico4_controller_xdev(struct pico4_controller *c);

//! One parsed radio packet received at @p packet_ns (CLOCK_MONOTONIC).
void
pico4_controller_push(struct pico4_controller *c, const struct pico4_blemcu_packet *p, int64_t packet_ns);

//! The HMD side lets go (the head is going away).
void
pico4_controller_release(struct pico4_controller *c);

#ifdef __cplusplus
}
#endif
