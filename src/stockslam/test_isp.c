// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: MIT
/*
 * isp.h against hand-computed values of PICO OS's image processing: row handling (first scene
 * row doubled, last dropped), black level, the (v >> 8) step, lens-shading gain and clamping,
 * the gamma lookup, and the table parser. Built and run natively by src/stockslam/build.sh.
 */
#include "isp.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;

#define EXPECT(cond, ...)                                                                                              \
	do {                                                                                                           \
		if (!(cond)) {                                                                                         \
			fprintf(stderr, "test_isp: " __VA_ARGS__);                                                     \
			fprintf(stderr, "\n");                                                                         \
			failures++;                                                                                    \
		}                                                                                                      \
	} while (0)

int
main(void)
{
	static struct isp_tables t;
	static uint16_t rows[ISP_H * ISP_W];
	static uint8_t out[ISP_W * ISP_H];
	static uint16_t linear[ISP_W * ISP_H];

	// Parser: any separators, a number at the very end, at most max.
	uint32_t n[4];
	const char text[] = "2048\n128\r\n7 15";
	EXPECT(isp_parse_numbers(text, (long)strlen(text), n, 4) == 4 && n[0] == 2048 && n[1] == 128 && n[3] == 15,
	       "parse");
	EXPECT(isp_parse_numbers(text, (long)strlen(text), n, 2) == 2, "parse limit");

	// Gain 1x everywhere, 2x at pixel (row 1, column 5), 16x at the last pixel; gamma = v >> 2.
	for (int i = 0; i < ISP_W * ISP_H; i++) {
		t.lsc[i] = 128;
	}
	t.lsc[ISP_W + 5] = 256;
	t.lsc[ISP_W * ISP_H - 1] = 2048;
	for (int i = 0; i < 1024; i++) {
		t.gamma[i] = (uint8_t)(i >> 2);
	}
	// Scene row r holds 100 + r in every column; row 0 marks column 3 with 600.
	for (int r = 0; r < ISP_H; r++) {
		for (int c = 0; c < ISP_W; c++) {
			rows[r * ISP_W + c] = (uint16_t)(100 + r);
		}
	}
	rows[3] = 600;
	rows[(ISP_H - 1) * ISP_W] = 1000; // the last scene row: dropped
	isp_process(&t, rows, ISP_W, out, linear);

	// Row 0 and row 1 are both scene row 0: (100 - 16) = 84 -> 84 >> 2 = 21.
	EXPECT(out[0] == 21 && out[ISP_W] == 21, "row 0 and 1 from scene row 0: %u %u", out[0], out[ISP_W]);
	// The mark shows in both: 600 - 16 = 584, + (584 >> 8) = 586, >> 2 = 146.
	EXPECT(out[3] == 146 && out[ISP_W + 3] == 146, "marked column: %u %u", out[3], out[ISP_W + 3]);
	// Gain 2x at row 1, column 5 (scene row 0): 84 * 256 >> 7 = 168 -> 42.
	EXPECT(out[ISP_W + 5] == 42, "2x gain: %u", out[ISP_W + 5]);
	// Row 2 is scene row 1: 101 - 16 = 85 -> 21.
	EXPECT(out[2 * ISP_W] == 21, "row 2: %u", out[2 * ISP_W]);
	// The last row is scene row 478 (578 - 16 = 562, + 2 = 564), its last pixel at 16x: clamped to 1023.
	EXPECT(out[(ISP_H - 1) * ISP_W] == 141, "last row: %u", out[(ISP_H - 1) * ISP_W]);
	EXPECT(out[ISP_W * ISP_H - 1] == 255, "16x clamps: %u", out[ISP_W * ISP_H - 1]);
	// The auto exposure's input: the same samples before the gamma table.
	EXPECT(linear[3] == 586 && linear[ISP_W + 5] == 168 && linear[ISP_W * ISP_H - 1] == 1023,
	       "linear: %u %u %u", linear[3], linear[ISP_W + 5], linear[ISP_W * ISP_H - 1]);
	// Below the black level: 0.
	rows[ISP_W * 10] = 10;
	isp_process(&t, rows, ISP_W, out, NULL);
	EXPECT(out[ISP_W * 11] == 0, "black level: %u", out[ISP_W * 11]);

	// The vendor's table, as PICO OS's ISPpreinit maps it; camera 0's id from cJSON's output.
	EXPECT(strcmp(isp_lsc_file(9), "config_lscLH.txt") == 0 && strcmp(isp_lsc_file(13), "config_lscLH.txt") == 0,
	       "vendor 9/13");
	EXPECT(strcmp(isp_lsc_file(8), "config_lscSY.txt") == 0, "vendor 8");
	EXPECT(strcmp(isp_lsc_file(7), "config_lscneo3.txt") == 0, "vendor 7");
	EXPECT(isp_lsc_file(5) == NULL && isp_lsc_file(-1) == NULL, "illegal vendor");
	const char otp[] = "{\n\t\"camera0\":\t9,\n\t\"camera1\":\t8,\n\t\"camera2\":\t9,\n\t\"camera3\":\t9\n}";
	EXPECT(isp_otp_vendor(otp, (long)strlen(otp)) == 9, "otp camera0");
	const char line[] = "camera otp \"camera1\": 8, \"camera0\" : 13";
	EXPECT(isp_otp_vendor(line, (long)strlen(line)) == 13, "otp camera0 later in the text");
	const char none[] = "{\"camera1\": 9}";
	EXPECT(isp_otp_vendor(none, (long)strlen(none)) == -1, "otp without camera0");

	if (failures == 0) {
		printf("test_isp: passed\n");
	}
	return failures == 0 ? 0 : 1;
}
