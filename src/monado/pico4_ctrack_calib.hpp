// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  The controller tracker's calibration: the factory tracking cameras (sensor.yaml) and
 *         the LED models and lever arms extracted from the headset's firmware (led-models.json,
 *         lever-arm.json).
 * @ingroup drv_pico4
 */

#pragma once

#include "pico4_ctrack_model.hpp"

#include <array>
#include <string>

namespace pico4::ctrack {

/*!
 * The four tracking cameras from @p dir (pvr/mav0/cam<k>/sensor.yaml): the omnidirectional model
 * and T_BS, the camera's pose in the headset body frame. False with a message in @p error.
 */
bool
load_cameras(const std::string &dir, std::array<Camera, kCameras> &cams, std::string &error);

//! Both LED models with their normals (led-models.json, pico4/ledmodel.py). False with a message in @p error.
bool
load_led_models(const std::string &path, LedModel &left, LedModel &right, std::string &error);

/*!
 * PICO OS's lever arm of each hand in the aim frame, metres (lever-arm.json, pico4/leverarm.py),
 * indexed by Hand. False with a message in @p error.
 */
bool
load_lever_arms(const std::string &path, std::array<Vec3, 2> &lever, std::string &error);

} // namespace pico4::ctrack
