// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: MIT
/*
 * The image processing PICO OS's tracking service applies to each head-tracking frame before its
 * tracker sees it (docs/xr.md#head-tracking), reimplemented: the first scene row doubled (it
 * replaces the sensor's metadata row), black level 16 off, a per-pixel lens-shading gain
 * (128 = 1x) and a 10-to-8-bit gamma table. Both tables come from PICO OS's configuration at
 * run time (config_lsc*.txt, chosen by the camera modules' vendor; config_gma10.txt).
 * Plain C: the host tests build it too.
 */
#pragma once
#include <stdint.h>

#define ISP_W 640
#define ISP_H 480

struct isp_tables
{
	uint16_t lsc[ISP_W * ISP_H];
	uint8_t gamma[1024];
};

/*!
 * Parses up to @p max unsigned decimal numbers (any separators) from @p text of @p len bytes;
 * returns how many.
 */
static inline int
isp_parse_numbers(const char *text, long len, uint32_t *out, int max)
{
	int count = 0, in = 0;
	uint32_t v = 0;
	for (long i = 0; i < len && count < max; i++) {
		char c = text[i];
		if (c >= '0' && c <= '9') {
			v = v * 10 + (uint32_t)(c - '0');
			in = 1;
		} else if (in) {
			out[count++] = v;
			v = 0;
			in = 0;
		}
	}
	if (in && count < max) {
		out[count++] = v;
	}
	return count;
}

/*!
 * One frame: @p rows are the sensor's 480 scene rows (10-bit samples in 16 bits, @p stride
 * samples apart); @p out gets 640x480 8-bit pixels as PICO OS hands them to its tracker.
 */
static inline void
isp_process(const struct isp_tables *t, const uint16_t *rows, int stride, uint8_t *out)
{
	for (int y = 0; y < ISP_H; y++) {
		// Row 0 is the first scene row again; the last scene row drops out.
		const uint16_t *in = rows + (long)(y == 0 ? 0 : y - 1) * stride;
		for (int x = 0; x < ISP_W; x++) {
			int i = y * ISP_W + x;
			uint32_t v = in[x] & 0x3ff;
			v = v > 16 ? v - 16 : 0;
			v += v >> 8;
			if (v > 1023) {
				v = 1023;
			}
			v = (v * t->lsc[i]) >> 7;
			if (v > 1023) {
				v = 1023;
			}
			out[i] = t->gamma[v];
		}
	}
}
