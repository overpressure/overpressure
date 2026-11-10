// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-splash's picture: the logo's headset (docs/logo/overpressure-mark.svg), the name under it and a
 * moving bar under that, in the middle of each eye.
 *
 * Freestanding like pico4-splash.c, which includes it (and so does its host test): no libc, no libm.
 * The headset and the name are drawn once into a canvas, then copied into both eyes; the bar is
 * drawn again for every frame. Shapes are polygons (arcs and curves cut into short lines) filled by
 * exact area coverage: each edge adds, per pixel it crosses, the share of the pixel to its right, and
 * a sum along the row gives every pixel's coverage. The headset's numbers are the SVG's, in its units.
 */

#ifndef PICO4_SPLASH_DRAW_H
#define PICO4_SPLASH_DRAW_H

/* -- layout, in panel pixels -------------------------------------------------------------- */

#define CANVAS_W 600
#define CANVAS_H 392
#define MARK_W 560.0f      /* the headset's width: about a quarter of an eye */
#define NAME_TOP 336.0f    /* the name's x-height line in the canvas */
#define NAME_X 34.0f       /* the name's x-height */
#define NAME_STROKE 4.8f
#define NAME_GAP 0.58f     /* between letters, in x-heights */
#define BAR_W 360
#define BAR_H 8
#define BAR_GAP 44         /* from the canvas' bottom to the bar */
#define GROUP_H (CANVAS_H + BAR_GAP + BAR_H)
#define BAR_CYCLE 1600     /* ms for the bar's segment to cross the track */
#define BAR_SEGMENT 0.38f  /* the segment's length, a share of the track */

/* The drawing's colours. */
#define C_MOUNT_TOP 0x2457cf
#define C_MOUNT_BOTTOM 0x163a9e
#define C_VISOR_TOP 0x5a92ff
#define C_VISOR_MIDDLE 0x2a66ec
#define C_VISOR_BOTTOM 0x1a43b8
#define C_RIM 0x173a9a
#define C_LENS 0x0b0d12
#define C_RED 0xff7849
#define C_WHITE 0xffffff
#define C_NAME 0x9c9ea3
#define C_TRACK 0x1f1f1f
#define C_SEGMENT 0x3d7bf5

/* -- maths without libm --------------------------------------------------------------------- */

#define PI 3.14159265358979f

static float absf(float a)
{
	return a < 0 ? -a : a;
}

/* sin(A), A in degrees: folded into [-90, 90], then its Taylor series (error below 1e-5). */
static float sine(float degrees)
{
	while (degrees > 180)
		degrees -= 360;
	while (degrees < -180)
		degrees += 360;
	if (degrees > 90)
		degrees = 180 - degrees;
	else if (degrees < -90)
		degrees = -180 - degrees;
	float a = degrees * PI / 180, a2 = a * a;
	return a * (1 - a2 / 6 * (1 - a2 / 20 * (1 - a2 / 42 * (1 - a2 / 72))));
}

static float cosine(float degrees)
{
	return sine(degrees + 90);
}

/* -- coverage ---------------------------------------------------------------------------------- */

static float cells[CANVAS_H][CANVAS_W + 2]; /* per pixel, the signed area its edges add */
static unsigned canvas[CANVAS_H][CANVAS_W]; /* 0x00RRGGBB; black to begin with */
static int row_min = CANVAS_H, row_max = -1, col_min = CANVAS_W, col_max = -1;

/* One piece of an edge within row ROW and within one pixel column: D is its signed height. */
static void cell(int row, float xa, float xb, float d)
{
	int col = (int)(xa < xb ? xa : xb);
	float right = 0.5f * (xa + xb) - col; /* how far into the pixel the piece lies, on average */
	cells[row][col] += d * (1 - right);
	cells[row][col + 1] += d * right;
	if (col < col_min)
		col_min = col;
	if (col > col_max)
		col_max = col;
}

static float clampx(float x)
{
	return x < 0 ? 0 : x > CANVAS_W - 0.001f ? CANVAS_W - 0.001f : x;
}

/* An edge from (X0, Y0) to (X1, Y1), in canvas pixels. */
static void edge(float x0, float y0, float x1, float y1)
{
	float dir = 1;
	if (y0 > y1) {
		float t = x0;
		x0 = x1, x1 = t, t = y0, y0 = y1, y1 = t;
		dir = -1;
	}
	if (y1 - y0 < 1e-6f)
		return;
	x0 = clampx(x0), x1 = clampx(x1);
	float dxdy = (x1 - x0) / (y1 - y0);
	for (int row = y0 < 0 ? 0 : (int)y0; row < CANVAS_H && row < y1; row++) {
		float ya = y0 > row ? y0 : row, yb = y1 < row + 1 ? y1 : row + 1;
		float xa = x0 + (ya - y0) * dxdy, xb = x0 + (yb - y0) * dxdy;
		float d = (yb - ya) * dir;
		if (row < row_min)
			row_min = row;
		if (row > row_max)
			row_max = row;
		/* Cut the piece where it crosses into the next pixel column. */
		float lo = xa < xb ? xa : xb, hi = xa < xb ? xb : xa, span = hi - lo;
		if ((int)lo == (int)hi || span < 1e-6f) {
			cell(row, xa, xb, d);
			continue;
		}
		for (float x = lo; x < hi;) {
			float next = (float)((int)x + 1);
			if (next > hi)
				next = hi;
			cell(row, x, next, d * (next - x) / span);
			x = next;
		}
	}
}

/* The coverage of row ROW's pixels COL_MIN..COL_MAX+1 into OUT, and the row's cells cleared. */
static void resolve(int row, float *out)
{
	float sum = 0;
	for (int col = col_min; col <= col_max + 1 && col < CANVAS_W; col++) {
		sum += cells[row][col];
		cells[row][col] = 0;
		float c = absf(sum);
		out[col] = c > 1 ? 1 : c;
	}
	cells[row][col_max + 1] = 0;
}

/* A colour at a canvas row: up to three stops, each with an opacity. */
struct stop {
	float y;
	unsigned rgb;
	float alpha;
};

static float channel(unsigned rgb, int shift)
{
	return (float)((rgb >> shift) & 0xff);
}

/* Fill what the edges since the last fill enclose, in the colour the STOPS give per row. */
static void fill(const struct stop *stops, int n)
{
	static float coverage[CANVAS_W + 2];
	for (int row = row_min; row <= row_max; row++) {
		resolve(row, coverage);
		/* The row's colour: between the stops around its middle. */
		float y = row + 0.5f, r, g, b, a;
		int k = 0;
		while (k < n - 1 && y > stops[k + 1].y)
			k++;
		const struct stop *s = &stops[k], *t = &stops[k + 1 < n ? k + 1 : k];
		float f = t->y > s->y ? (y - s->y) / (t->y - s->y) : 0;
		f = f < 0 ? 0 : f > 1 ? 1 : f;
		r = channel(s->rgb, 16) * (1 - f) + channel(t->rgb, 16) * f;
		g = channel(s->rgb, 8) * (1 - f) + channel(t->rgb, 8) * f;
		b = channel(s->rgb, 0) * (1 - f) + channel(t->rgb, 0) * f;
		a = s->alpha * (1 - f) + t->alpha * f;
		for (int col = col_min; col <= col_max && col < CANVAS_W; col++) {
			float c = coverage[col] * a;
			if (c <= 0)
				continue;
			unsigned old = canvas[row][col];
			unsigned nr = (unsigned)(channel(old, 16) * (1 - c) + r * c + 0.5f);
			unsigned ng = (unsigned)(channel(old, 8) * (1 - c) + g * c + 0.5f);
			unsigned nb = (unsigned)(channel(old, 0) * (1 - c) + b * c + 0.5f);
			canvas[row][col] = nr << 16 | ng << 8 | nb;
		}
	}
	row_min = CANVAS_H, row_max = -1, col_min = CANVAS_W, col_max = -1;
}

static void solid(unsigned rgb)
{
	struct stop s[1] = {{0, rgb, 1}};
	fill(s, 1);
}

/* -- paths ------------------------------------------------------------------------------------- */

/* Where the drawing's units land in the canvas: x' = ox + x * scale. */
static float scale = 1, ox, oy;
static float pen_x, pen_y, start_x, start_y;

static void move(float x, float y)
{
	pen_x = start_x = ox + x * scale;
	pen_y = start_y = oy + y * scale;
}

static void line(float x, float y)
{
	float nx = ox + x * scale, ny = oy + y * scale;
	edge(pen_x, pen_y, nx, ny);
	pen_x = nx, pen_y = ny;
}

static void close_path(void)
{
	edge(pen_x, pen_y, start_x, start_y);
	pen_x = start_x, pen_y = start_y;
}

/* Along the circle around (CX, CY) of radius R from angle A0 to A1 (degrees, clockwise on screen when
 * A1 > A0), in steps of at most 2 degrees; the path is expected at the arc's start already. */
static void arc(float cx, float cy, float r, float a0, float a1)
{
	int steps = (int)(absf(a1 - a0) / 2) + 1;
	for (int i = 1; i <= steps; i++) {
		float a = a0 + (a1 - a0) * i / steps;
		line(cx + r * cosine(a), cy + r * sine(a));
	}
}

static void circle(float cx, float cy, float r)
{
	move(cx + r, cy);
	arc(cx, cy, r, 0, 360);
	close_path();
}

/* A circle drawn the other way round: inside an outer one, it cuts a hole. */
static void hole(float cx, float cy, float r)
{
	move(cx + r, cy);
	arc(cx, cy, r, 360, 0);
	close_path();
}

static void cubic(float x1, float y1, float x2, float y2, float x3, float y3)
{
	float x0 = (pen_x - ox) / scale, y0 = (pen_y - oy) / scale;
	for (int i = 1; i <= 24; i++) {
		float t = i / 24.0f, u = 1 - t;
		line(u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
		     u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3);
	}
}

static void rounded(float x, float y, float w, float h, float r)
{
	move(x + r, y);
	line(x + w - r, y);
	arc(x + w - r, y + r, r, -90, 0);
	line(x + w, y + h - r);
	arc(x + w - r, y + h - r, r, 0, 90);
	line(x + r, y + h);
	arc(x + r, y + h - r, r, 90, 180);
	line(x, y + r);
	arc(x + r, y + r, r, 180, 270);
	close_path();
}

/* A line of width W from angle A0 to A1 along the circle around (CX, CY) of radius R, with round ends. */
static void arc_stroke(float cx, float cy, float r, float w, float a0, float a1)
{
	float h = w / 2;
	move(cx + (r + h) * cosine(a0), cy + (r + h) * sine(a0));
	arc(cx, cy, r + h, a0, a1);
	arc(cx + r * cosine(a1), cy + r * sine(a1), h, a1, a1 + 180);
	arc(cx, cy, r - h, a1, a0);
	arc(cx + r * cosine(a0), cy + r * sine(a0), h, a0 + 180, a0 + 360);
	close_path();
}

/* A straight line of width W from (X0, Y0) to (X1, Y1) with round ends; the ends' half circles turn
 * through the line's own direction (D) and its normal (N). */
static void line_stroke(float x0, float y0, float x1, float y1, float w)
{
	float dx = x1 - x0, dy = y1 - y0, len = dx * dx + dy * dy, h = w / 2;
	/* The length by Newton's method: no libm. */
	float s = len > 1 ? len / 2 : 1;
	for (int i = 0; i < 20; i++)
		s = 0.5f * (s + len / s);
	dx /= s, dy /= s;
	float nx = -dy, ny = dx;
	move(x0 + nx * h, y0 + ny * h);
	line(x1 + nx * h, y1 + ny * h);
	for (int i = 1; i <= 16; i++) {
		float c = cosine(i * 180.0f / 16), sn = sine(i * 180.0f / 16);
		line(x1 + h * (nx * c + dx * sn), y1 + h * (ny * c + dy * sn));
	}
	line(x0 - nx * h, y0 - ny * h);
	for (int i = 1; i <= 16; i++) {
		float c = cosine(i * 180.0f / 16), sn = sine(i * 180.0f / 16);
		line(x0 - h * (nx * c + dx * sn), y0 - h * (ny * c + dy * sn));
	}
	close_path();
}

/* -- the headset (docs/logo/overpressure-mark.svg) --------------------------------------------- */

static void headset(void)
{
	struct stop mount[2] = {{230, C_MOUNT_TOP, 1}, {300, C_MOUNT_BOTTOM, 1}};
	struct stop visor[3] = {{110, C_VISOR_TOP, 1}, {245, C_VISOR_MIDDLE, 1}, {380, C_VISOR_BOTTOM, 1}};
	struct stop sheen[2] = {{170, C_WHITE, 0.28f}, {228.5f, C_WHITE, 0}};
	/* The stops are in the drawing's units: into canvas rows. */
	for (int i = 0; i < 2; i++)
		mount[i].y = oy + mount[i].y * scale, sheen[i].y = oy + sheen[i].y * scale;
	for (int i = 0; i < 3; i++)
		visor[i].y = oy + visor[i].y * scale;

	/* The strap mounts, behind the visor. */
	rounded(56, 232, 40, 64, 16);
	rounded(416, 232, 40, 64, 16);
	fill(mount, 2);

	/* The visor, with the nose's arch at the bottom; then its light from above. */
	for (int pass = 0; pass < 2; pass++) {
		move(166, 170);
		line(346, 170);
		arc(346, 258, 88, -90, 0);
		line(434, 274);
		arc(346, 274, 88, 0, 90);
		line(308, 362);
		cubic(291.36f, 362, 287.2f, 322, 256, 322);
		cubic(224.8f, 322, 220.64f, 362, 204, 362);
		line(166, 362);
		arc(166, 274, 88, 90, 180);
		line(78, 258);
		arc(166, 258, 88, 180, 270);
		close_path();
		if (pass == 0)
			fill(visor, 3);
		else
			fill(sheen, 2);
	}

	static const float lens_x[2] = {174, 338};
	for (int i = 0; i < 2; i++) {
		float cx = lens_x[i], cy = 264;
		circle(cx, cy, 70);
		solid(C_RIM);
		circle(cx, cy, 64);
		solid(C_LENS);
		/* The dial: the red zone from the top right round to the bottom right, the needle through it. */
		arc_stroke(cx, cy, 40, 20, 312, 405);
		solid(C_RED);
		const float a = 338, half = 11 + 1.5f, tail = 10 + 1.5f, length = 50 + 1.5f;
		float dx = cosine(a), dy = sine(a);
		move(cx - dx * tail, cy - dy * tail);
		line(cx - dy * half, cy + dx * half);
		line(cx + dx * length, cy + dy * length);
		line(cx + dy * half, cy - dx * half);
		close_path();
		solid(C_WHITE);
		circle(cx, cy, 13);
		solid(C_WHITE);
	}
}

/* -- the name ------------------------------------------------------------------------------------ */

/* A letter of "overpressure": lines and arcs of the name's stroke, in x-heights from the top left of
 * its x-height box (y down); ADVANCE is its width. */
enum { END, LINE, ARC, RING };
struct part {
	unsigned char kind;
	float a, b, c, d, e; /* LINE x0 y0 x1 y1; ARC cx cy r from to (degrees); RING cx cy r */
};
struct letter {
	char ch;
	float advance;
	struct part parts[4];
};

static const struct letter letters[] = {
	{'o', 1.00f, {{RING, 0.5f, 0.5f, 0.5f, 0, 0}, {END, 0, 0, 0, 0, 0}}},
	{'v', 0.88f, {{LINE, 0, 0, 0.44f, 1, 0}, {LINE, 0.44f, 1, 0.88f, 0, 0}, {END, 0, 0, 0, 0, 0}}},
	{'e', 1.00f, {{ARC, 0.5f, 0.5f, 0.5f, 35, 360}, {LINE, 0, 0.5f, 1, 0.5f, 0}, {END, 0, 0, 0, 0, 0}}},
	{'r', 0.62f, {{LINE, 0, 0, 0, 1, 0}, {ARC, 0.45f, 0.45f, 0.45f, 180, 290}, {END, 0, 0, 0, 0, 0}}},
	{'p', 1.00f, {{RING, 0.5f, 0.5f, 0.5f, 0, 0}, {LINE, 0, 0, 0, 1.42f, 0}, {END, 0, 0, 0, 0, 0}}},
	{'s', 0.56f, {{ARC, 0.28f, 0.255f, 0.255f, 90, 340}, {ARC, 0.28f, 0.745f, 0.255f, 270, 520}, {END, 0, 0, 0, 0, 0}}},
	{'u', 0.88f, {{LINE, 0, 0, 0, 0.5f, 0}, {ARC, 0.44f, 0.5f, 0.44f, 0, 180}, {LINE, 0.88f, 0, 0.88f, 1, 0}, {END, 0, 0, 0, 0, 0}}},
};

static const struct letter *letter(char ch)
{
	for (unsigned i = 0; i < sizeof(letters) / sizeof(letters[0]); i++)
		if (letters[i].ch == ch)
			return &letters[i];
	return 0;
}

static const char NAME[] = "overpressure";

static float name_width(void)
{
	float w = 0;
	for (const char *p = NAME; *p; p++)
		w += letter(*p)->advance + (p[1] ? NAME_GAP : 0);
	return w * NAME_X;
}

static void name(void)
{
	scale = 1, oy = 0;
	float x = (CANVAS_W - name_width()) / 2, y = NAME_TOP, u = NAME_X, w = NAME_STROKE, h = w / 2;
	for (const char *p = NAME; *p; p++) {
		const struct letter *l = letter(*p);
		for (const struct part *q = l->parts; q->kind != END; q++) {
			ox = 0;
			if (q->kind == LINE)
				line_stroke(x + q->a * u, y + q->b * u, x + q->c * u, y + q->d * u, w);
			else if (q->kind == ARC)
				arc_stroke(x + q->a * u, y + q->b * u, q->c * u, w, q->d, q->e);
			else {
				circle(x + q->a * u, y + q->b * u, q->c * u + h);
				hole(x + q->a * u, y + q->b * u, q->c * u - h);
			}
			solid(C_NAME); /* each part on its own: parts that overlap stay one shape */
		}
		x += (l->advance + NAME_GAP) * u;
	}
}

/* Draw the headset and the name into the canvas. */
static void draw_canvas(void)
{
	scale = MARK_W / 416, ox = (CANVAS_W - MARK_W) / 2 - 48 * scale, oy = 4 - 162 * scale;
	headset();
	name();
}

/* -- the bar ------------------------------------------------------------------------------------- */

static float track[BAR_H][BAR_W]; /* the track's rounded shape: each pixel's coverage */

static void draw_track(void)
{
	static float coverage[CANVAS_W + 2];
	scale = 1, ox = 0, oy = 0;
	rounded(0, 0, BAR_W, BAR_H, BAR_H / 2.0f);
	int first = row_min, last = row_max;
	for (int row = first; row <= last; row++) {
		resolve(row, coverage);
		/* The arcs' last points can land a hair below the track: that row is not the track's. */
		for (int col = 0; row < BAR_H && col < BAR_W; col++)
			track[row][col] = col >= col_min && col <= col_max + 1 ? coverage[col] : 0;
	}
	row_min = CANVAS_H, row_max = -1, col_min = CANVAS_W, col_max = -1;
}

static unsigned mix(unsigned a, unsigned b, float f, float m)
{
	unsigned out = 0;
	for (int shift = 0; shift <= 16; shift += 8) {
		float v = (channel(a, shift) * (1 - f) + channel(b, shift) * f) * m;
		out |= (unsigned)(v + 0.5f) << shift;
	}
	return out;
}

/* The bar's row Y at time MS into ROW (BAR_W pixels): a segment crosses the track, easing in and out. */
static void bar_row(unsigned *row, int y, long ms)
{
	float p = (float)(ms % BAR_CYCLE) / BAR_CYCLE, e = p * p * (3 - 2 * p);
	float seg = BAR_SEGMENT * BAR_W, s0 = -seg + e * (BAR_W + seg), s1 = s0 + seg;
	for (int x = 0; x < BAR_W; x++) {
		float lo = x > s0 ? x : s0, hi = x + 1 < s1 ? x + 1 : s1;
		row[x] = mix(C_TRACK, C_SEGMENT, hi > lo ? hi - lo : 0, track[y][x]);
	}
}

#endif
