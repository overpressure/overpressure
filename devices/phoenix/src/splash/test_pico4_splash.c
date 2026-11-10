// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-splash's picture on the host: the headset, the name and the bar land where they should, in
 * their colours, and nothing is cut off. With a file name it also writes the canvas and three of the
 * bar's frames as a PPM picture, to look at.
 *
 *   cc -std=gnu11 -O1 -Wall -Wextra -Werror -o test_pico4_splash test_pico4_splash.c
 *   ./test_pico4_splash [OUT.ppm]
 */
#include <stdio.h>

#include "pico4-splash-draw.h"

#define EYE 2160

static int failures;

static void check(int ok, const char *what)
{
	if (!ok) {
		printf("FAIL %s\n", what);
		failures++;
	}
}

/* Whether GOT is WANT, each channel within TOLERANCE. */
static void near(unsigned got, unsigned want, int tolerance, const char *what)
{
	for (int shift = 0; shift <= 16; shift += 8) {
		int d = (int)((got >> shift) & 0xff) - (int)((want >> shift) & 0xff);
		if (d < -tolerance || d > tolerance) {
			printf("FAIL %s: %06x, want %06x\n", what, got, want);
			failures++;
			return;
		}
	}
}

/* The canvas pixel under the point (X, Y) of the drawing (docs/logo/overpressure-mark.svg's units). */
static unsigned mark(float x, float y)
{
	float s = MARK_W / 416, left = (CANVAS_W - MARK_W) / 2 - 48 * s, top = 4 - 162 * s;
	return canvas[(int)(top + y * s)][(int)(left + x * s)];
}

static unsigned on_dial(float cx, float r, float degrees)
{
	return mark(cx + r * cosine(degrees), 264 + r * sine(degrees));
}

static void write_ppm(const char *path)
{
	static unsigned bar[BAR_W];
	const int frames = 3, band = BAR_H + 12, height = CANVAS_H + frames * band;
	FILE *f = fopen(path, "wb");
	if (!f) {
		printf("FAIL cannot write %s\n", path);
		failures++;
		return;
	}
	fprintf(f, "P6\n%d %d\n255\n", CANVAS_W, height);
	for (int y = 0; y < height; y++)
		for (int x = 0; x < CANVAS_W; x++) {
			unsigned c = 0;
			if (y < CANVAS_H)
				c = canvas[y][x];
			else {
				int frame = (y - CANVAS_H) / band, row = (y - CANVAS_H) % band - 6;
				int left = (CANVAS_W - BAR_W) / 2;
				if (row >= 0 && row < BAR_H && x >= left && x < left + BAR_W) {
					bar_row(bar, row, (long)frame * BAR_CYCLE / 4 + BAR_CYCLE / 4);
					c = bar[x - left];
				}
			}
			fputc(c >> 16 & 0xff, f), fputc(c >> 8 & 0xff, f), fputc(c & 0xff, f);
		}
	fclose(f);
}

int main(int argc, char **argv)
{
	/* The maths without libm. */
	check(absf(sine(30) - 0.5f) < 1e-4f && absf(cosine(60) - 0.5f) < 1e-4f, "sine and cosine");
	check(absf(sine(-90) + 1) < 1e-4f && absf(sine(450) - 1) < 1e-4f && absf(cosine(180) + 1) < 1e-4f, "folding");

	draw_canvas();
	draw_track();

	/* The headset, part by part, in the SVG's colours. */
	near(mark(50, 200), 0, 0, "black beside the headset");
	near(mark(66, 264), 0x1d48b6, 4, "a strap mount, half way down its gradient");
	check((mark(256, 200) & 0xff) > 200 && (mark(256, 200) >> 16) < 0x70, "the visor is blue");
	near(mark(256, 345), 0, 0, "black under the nose's arch");
	check((mark(256, 316) & 0xff) > 150, "the visor above the nose's arch");
	for (int i = 0; i < 2; i++) {
		float cx = i ? 338 : 174;
		near(mark(cx, 264 - 67), C_RIM, 2, "a lens rim");
		near(mark(cx - 40, 264), C_LENS, 2, "a lens");
		near(mark(cx, 264), C_WHITE, 2, "the hub");
		near(on_dial(cx, 25, 338), C_WHITE, 2, "the needle");
		near(on_dial(cx, 40, 0), C_RED, 3, "the red zone at the right");
		near(on_dial(cx, 40, 40), C_RED, 3, "the red zone at the bottom right");
		near(on_dial(cx, 40, 180), C_LENS, 2, "no red zone at the left");
	}

	/* The name under it: the o's ring and its hole, the p's descender. */
	float x = (CANVAS_W - name_width()) / 2, h = NAME_X;
	near(canvas[(int)(NAME_TOP + h / 2)][(int)(x + h)], C_NAME, 8, "the o's ring");
	near(canvas[(int)(NAME_TOP + h / 2)][(int)(x + h / 2)], 0, 0, "the o's hole");
	for (const char *p = NAME; *p != 'p'; p++)
		x += (letter(*p)->advance + NAME_GAP) * h;
	near(canvas[(int)(NAME_TOP + 1.3f * h)][(int)x], C_NAME, 8, "the p's descender");

	/* Nothing cut off: the canvas' edges stay black. */
	int edge_lit = 0;
	for (int y = 0; y < CANVAS_H; y++)
		edge_lit |= canvas[y][0] | canvas[y][CANVAS_W - 1];
	for (int col = 0; col < CANVAS_W; col++)
		edge_lit |= canvas[0][col] | canvas[CANVAS_H - 1][col];
	check(!edge_lit, "the canvas' edges are black");
	check(GROUP_H < EYE / 3 && CANVAS_W < EYE / 3, "the picture fits the middle of an eye");

	/* The bar: the track alone when the segment is off it, the segment in the middle half way. */
	static unsigned bar[BAR_W], again[BAR_W];
	bar_row(bar, BAR_H / 2, 0);
	near(bar[BAR_W / 2], C_TRACK, 1, "the track");
	check(bar[0] != C_TRACK, "the track's rounded end");
	bar_row(bar, BAR_H / 2, BAR_CYCLE / 2);
	near(bar[BAR_W / 2], C_SEGMENT, 1, "the segment half way");
	near(bar[2], C_TRACK, 1, "the track before the segment");
	bar_row(again, BAR_H / 2, BAR_CYCLE / 2 + 7L * BAR_CYCLE);
	int same = 1;
	for (int i = 0; i < BAR_W; i++)
		same &= bar[i] == again[i];
	check(same, "the bar follows the clock, cycle after cycle");

	if (argc > 1)
		write_ppm(argv[1]);
	if (failures) {
		printf("test_pico4_splash: %d failed\n", failures);
		return 1;
	}
	printf("test_pico4_splash: passed\n");
	return 0;
}
