// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 controller packets from the sensor DSP (see pico4_blemcu.h).
 * @ingroup drv_pico4
 */

#include "pico4_blemcu.h"

#include <string.h>

#define MAGIC 0x5A
#define HEADER 13
#define TYPE_IMU 0
#define TYPE_KEYS 1
#define TYPE_BATTERY 2

static uint16_t
le16(const uint8_t *p)
{
	return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t
le32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

bool
pico4_blemcu_parse(const uint8_t *data, size_t size, struct pico4_blemcu_packet *out)
{
	memset(out, 0, sizeof(*out));
	if (size < HEADER || data[0] != MAGIC || HEADER + (size_t)data[12] > size) {
		return false;
	}
	out->station_time_us = (uint64_t)le32(data + 4) | ((uint64_t)le32(data + 8) << 32);

	const uint8_t *p = data + HEADER, *end = p + data[12];
	while (p + 3 <= end) {
		size_t len = p[0];
		if (len < 3 || p + len > end) {
			break;
		}
		int device = p[2] & 0x3f;
		size_t start = 3;
		uint32_t offset = 0;
		if (p[2] & 0x80) {
			start = (p[2] & 0x40) ? 7 : 5;
			if (start > len) {
				break;
			}
			offset = (p[2] & 0x40) ? le32(p + 3) : le16(p + 3);
		}
		const uint8_t *d = p + start;
		size_t n = len - start;
		bool controller = device == PICO4_BLEMCU_LEFT || device == PICO4_BLEMCU_RIGHT;

		if (controller && p[1] == TYPE_IMU && n >= 16 && out->imu_count < PICO4_BLEMCU_MAX_IMU) {
			struct pico4_blemcu_imu *imu = &out->imu[out->imu_count++];
			imu->device = device;
			imu->offset_us = offset;
			for (int i = 0; i < 3; i++) {
				imu->gyro[i] = (int16_t)le16(d + 2 * i);
				imu->accel[i] = (int16_t)le16(d + 6 + 2 * i);
			}
			imu->temperature = (int16_t)le16(d + 12);
			imu->counter = le16(d + 14);
		} else if (controller && p[1] == TYPE_KEYS && n >= 6 && out->key_count < 2) {
			struct pico4_blemcu_keys *k = &out->keys[out->key_count++];
			k->device = device;
			k->trigger = d[0];
			k->stick_y = d[1]; // y first: checked by pushing each direction in turn
			k->stick_x = d[2];
			k->grip = d[3];
			k->buttons = d[4];
			k->touch = d[5];
		} else if (controller && p[1] == TYPE_BATTERY && n >= 1 && out->battery_count < 2) {
			out->battery[out->battery_count].device = device;
			out->battery[out->battery_count].level = d[0];
			out->battery_count++;
		}
		p += len;
	}
	return true;
}
