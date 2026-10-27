// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: MIT
/*
 * room.h against hand-made boundaries: the file format both ways (b up), a malformed file, and
 * the fit of a turned and shifted copy. Built and run natively by src/stockslam/build.sh.
 */
#include "room.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures;
static const double PI = 3.14159265358979323846;

#define EXPECT(cond, ...)                                                                                              \
	do {                                                                                                           \
		if (!(cond)) {                                                                                         \
			fprintf(stderr, "test_room: " __VA_ARGS__);                                                    \
			fprintf(stderr, "\n");                                                                         \
			failures++;                                                                                    \
		}                                                                                                      \
	} while (0)

int
main(void)
{
	// As PICO OS writes it: (a, b, c) with b up, -> tracker world (b, -a, c).
	const char file[] = "isSaveMap 1\nvertexcount 6\n(1.000000, -1.500000, 2.000000)\n"
	                    "(-1.000000, -1.500000, 2.000000)\n(0.500000, -1.400000, -0.250000)\n";
	double p[ROOM_MAX_POINTS][3];
	int n = room_parse(file, (long)strlen(file), p, ROOM_MAX_POINTS);
	EXPECT(n == 3, "count %d", n);
	EXPECT(p[0][0] == -1.5 && p[0][1] == -1.0 && p[0][2] == 2.0, "point 0 %f %f %f", p[0][0], p[0][1], p[0][2]);
	EXPECT(fabs(p[2][0] + 1.4) < 1e-9 && fabs(p[2][1] + 0.5) < 1e-9 && fabs(p[2][2] + 0.25) < 1e-9, "point 2");
	double abc[3];
	room_to_file(p[1], abc);
	EXPECT(abc[0] == -1.0 && abc[1] == -1.5 && abc[2] == 2.0, "back to the file");
	const char wrong[] = "isSaveMap 1\nvertexcount 8\n(1, 2, 3)\n";
	EXPECT(room_parse(wrong, (long)strlen(wrong), p, ROOM_MAX_POINTS) == -1, "count mismatch");
	EXPECT(room_parse("", 0, p, ROOM_MAX_POINTS) == -1, "empty");
	// The tracker's copy after a relocalisation (stdataforalgorithm.txt) counts the points once.
	const char seen[] = "vertexcount 2\n(1.000276, -1.498821, -0.999374)\n(-0.999723, -1.498821, -1.000548)\n";
	n = room_parse(seen, (long)strlen(seen), p, ROOM_MAX_POINTS);
	EXPECT(n == 2 && fabs(p[1][1] - 0.999723) < 1e-9, "relocalised copy %d", n);

	// A square boundary, turned by 37 degrees about up and shifted: the fit gets both back.
	double s[4][3] = {{-1.5, -1, -1}, {-1.5, 1, -1}, {-1.5, 1, 1}, {-1.5, -1, 1}}, w[4][3];
	double yaw = 37 * PI / 180, t[3] = {0.1, 0.7, -0.4};
	for (int i = 0; i < 4; i++) {
		w[i][0] = s[i][0] + t[0];
		w[i][1] = cos(yaw) * s[i][1] - sin(yaw) * s[i][2] + t[1];
		w[i][2] = sin(yaw) * s[i][1] + cos(yaw) * s[i][2] + t[2];
	}
	double got_yaw, got_t[3];
	double rms = room_fit((const double(*)[3])s, (const double(*)[3])w, 4, &got_yaw, got_t, atan2, sin, cos, sqrt);
	EXPECT(rms >= 0 && rms < 1e-9, "rms %g", rms);
	EXPECT(fabs(got_yaw - yaw) < 1e-9, "yaw %f", got_yaw * 180 / PI);
	EXPECT(fabs(got_t[0] - t[0]) < 1e-9 && fabs(got_t[1] - t[1]) < 1e-9 && fabs(got_t[2] - t[2]) < 1e-9, "shift");
	EXPECT(room_fit((const double(*)[3])s, (const double(*)[3])w, 2, &got_yaw, got_t, atan2, sin, cos, sqrt) < 0,
	       "two points");

	if (failures == 0) {
		printf("test_room: passed\n");
	}
	return failures == 0 ? 0 : 1;
}
