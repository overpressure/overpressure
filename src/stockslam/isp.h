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
 * The lens-shading table of a camera-module vendor, as PICO OS's ISPpreinit maps it; NULL for
 * an id it calls illegal (it then shades nothing).
 */
static inline const char *
isp_lsc_file(int vendor)
{
	switch (vendor) {
	case 8: return "config_lscSY.txt";
	case 9:
	case 13: return "config_lscLH.txt";
	case 7: return "config_lscneo3.txt";
	default: return 0;
	}
}

/*!
 * Camera 0's vendor id from PICO OS's camera_otp.json (cJSON's "camera0": N), as its ISP takes
 * camera 0's for all four; -1 if the text has none.
 */
static inline int
isp_otp_vendor(const char *text, long len)
{
	static const char key[] = "\"camera0\"";
	for (long i = 0; i + (long)sizeof(key) - 1 <= len; i++) {
		long k = 0;
		while (key[k] != '\0' && text[i + k] == key[k]) {
			k++;
		}
		if (key[k] != '\0') {
			continue;
		}
		long j = i + k;
		while (j < len && (text[j] == ' ' || text[j] == '\t' || text[j] == '\n' || text[j] == '\r')) {
			j++;
		}
		if (j >= len || text[j] != ':') {
			return -1;
		}
		uint32_t v;
		return isp_parse_numbers(text + j + 1, len - j - 1, &v, 1) == 1 && v < 256 ? (int)v : -1;
	}
	return -1;
}

/*!
 * One frame: @p rows are the sensor's 480 scene rows (10-bit samples in 16 bits, @p stride
 * samples apart); @p out gets 640x480 8-bit pixels as PICO OS hands them to its tracker. With
 * @p linear (or NULL), also the 10-bit samples before the gamma table, which PICO OS's auto
 * exposure takes.
 */
static inline void
isp_process(const struct isp_tables *t, const uint16_t *rows, int stride, uint8_t *out, uint16_t *linear)
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
			if (linear != 0) {
				linear[i] = (uint16_t)v;
			}
			out[i] = t->gamma[v];
		}
	}
}
