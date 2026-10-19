// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*
 * Unit test of the lens model and the IPD reply parser, with synthetic values
 * (no device data): file parsing, the polynomial, chromatic aberration, the
 * fitted radius, the field of view and the only MCU request the driver sends.
 */

#include "pico4_ipd.h"
#include "pico4_lens.h"

#include <math.h>
#include <stdio.h>

static int failures;

#define EXPECT(cond)                                                                                                   \
	do {                                                                                                           \
		if (!(cond)) {                                                                                         \
			fprintf(stderr, "%s:%d: expected %s\n", __FILE__, __LINE__, #cond);                            \
			failures++;                                                                                    \
		}                                                                                                      \
	} while (0)

#define NEAR(a, b) (fabs((a) - (b)) < 1e-9)

// Same structure as the factory file: numbers as strings, centre offsets in mm.
static const char *optical =
    "{\"distortion_parameter\": {"
    "\"left\": {\"F\": \"0.025\", \"A0\": \"1\", \"A1\": \"0.5\", \"A2\": \"0\", \"A3\": \"0\", \"A4\": \"0\","
    " \"A5\": \"0\", \"A6\": \"0\", \"A7\": \"0\", \"A8\": \"0\", \"A9\": \"0\", \"A10\": \"0\"},"
    "\"right\": {\"F\": \"0.025\", \"A0\": \"1\", \"A1\": \"0\", \"A2\": \"0\", \"A3\": \"0\", \"A4\": \"0\","
    " \"A5\": \"0\", \"A6\": \"0\", \"A7\": \"0\", \"A8\": \"0\", \"A9\": \"0\", \"A10\": \"0\"}},"
    "\"lens_center_gos\": {\"centerx\": {\"left\": \"0.5\", \"right\": \"0\"},"
    " \"centery\": {\"left\": \"0\", \"right\": \"-0.25\"}}}";

// Same structure as the stock lens configuration.
static const char *config =
    "{\"configData\": ["
    "{\"keyName\": \"sdk_lens_chromaticAberration0\", \"keyValue\": \"0.01\"},"
    "{\"keyName\": \"sdk_lens_chromaticAberration1\", \"keyValue\": \"0\"},"
    "{\"keyName\": \"sdk_lens_chromaticAberration2\", \"keyValue\": \"-0.02\"},"
    "{\"keyName\": \"sdk_lens_chromaticAberration3\", \"keyValue\": \"0\"},"
    "{\"keyName\": \"sdk_lens_chromaticAberration4\", \"keyValue\": \"0\"},"
    "{\"keyName\": \"sdk_lens_chromaticAberration5\", \"keyValue\": \"0\"},"
    "{\"keyName\": \"sdk_lens_distortionCoefficients1\", \"keyValue\": \"9\"}]}";

int
main(void)
{
	struct pico4_lens lens[2];
	struct pico4_lens_ca ca;

	EXPECT(pico4_lens_parse_optical(optical, lens));
	EXPECT(NEAR(lens[0].f, 0.025) && NEAR(lens[0].k[1], 0.5) && NEAR(lens[1].k[0], 1));
	EXPECT(NEAR(lens[0].cx, 0.0005) && NEAR(lens[1].cy, 0.00025));
	EXPECT(!pico4_lens_parse_optical("{\"distortion_parameter\": {\"left\": {\"F\": \"0.02\"}}}", lens) &&
	       pico4_lens_parse_optical(optical, lens));
	EXPECT(pico4_lens_parse_ca(config, &ca));
	EXPECT(NEAR(ca.ca[0], 0.01) && NEAR(ca.ca[2], -0.02));
	EXPECT(!pico4_lens_parse_ca("{\"configData\": []}", &ca) && pico4_lens_parse_ca(config, &ca));

	// s(rsq) = 1 + 0.5 rsq.
	EXPECT(NEAR(pico4_lens_scale(&lens[0], 2), 2));

	// Right eye: identity polynomial, so green = (x - cx) / F; red and blue scaled by 1.01 and 0.98.
	double t[3][2];
	EXPECT(pico4_lens_tan(&lens[1], &ca, 0.01, 0.002, t));
	EXPECT(NEAR(t[1][0], 0.4) && NEAR(t[1][1], (0.002 - 0.00025) / 0.025));
	EXPECT(NEAR(t[0][0], 0.4 * 1.01) && NEAR(t[2][0], 0.4 * 0.98));

	// The panel corner is outside the circle the polynomial was fitted for.
	EXPECT(pico4_lens_tan(&lens[1], &ca, 0.022, 0, t));
	EXPECT(!pico4_lens_tan(&lens[1], &ca, 0.022, 0.022, t));

	// Field of view of the identity lens: atan(0.023 / 0.025) to each side, shifted by the centre offset.
	double l, r, u, d;
	pico4_lens_fov(&lens[1], &l, &r, &u, &d);
	EXPECT(NEAR(l, -atan(0.023 / 0.025)) && NEAR(r, atan(0.023 / 0.025)));
	EXPECT(NEAR(u, atan((0.023 + 0.00025) / 0.025)) && NEAR(d, -atan((0.023 - 0.00025) / 0.025)));

	// The only MCU request: A0 08 00 00 00 with its XOR checksum.
	EXPECT(pico4_ipd_get_position[0] == 0xA0 && pico4_ipd_get_position[1] == 0x08 &&
	       pico4_ipd_get_position[5] == 0xA8);
	// Reply: 79, position 6350 (63.50 mm) and ADC 1800 in either order, XOR checksum.
	double ipd = 0;
	uint8_t reply[6] = {0x79, 6350 & 0xff, 6350 >> 8, 1800 & 0xff, 1800 >> 8, 0};
	for (int i = 0; i < 5; i++) {
		reply[5] ^= reply[i];
	}
	EXPECT(pico4_ipd_parse_position(reply, sizeof(reply), &ipd) && NEAR(ipd, 0.0635));
	uint8_t swapped[6] = {0x79, 1800 & 0xff, 1800 >> 8, 6350 & 0xff, 6350 >> 8, 0};
	for (int i = 0; i < 5; i++) {
		swapped[5] ^= swapped[i];
	}
	EXPECT(pico4_ipd_parse_position(swapped, sizeof(swapped), &ipd) && NEAR(ipd, 0.0635));
	reply[5] ^= 1;
	EXPECT(!pico4_ipd_parse_position(reply, sizeof(reply), &ipd));

	if (failures == 0) {
		printf("test_pico4_lens: all checks passed\n");
	}
	return failures == 0 ? 0 : 1;
}
