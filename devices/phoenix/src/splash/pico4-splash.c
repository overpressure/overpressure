// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-splash: a sign of life in the lenses while the headset starts (docs/install.md).
 *
 * Shows a turning ring of dots in the middle of both eyes from stage 1 until a compositor takes
 * the display. It sets the panels' native 90 Hz mode with the same six colour-component layers
 * as Monado's KMS target and SteamVR's display layer (docs/graphics.md), then gives up DRM
 * master at once: whoever starts next (Monado, SteamVR's compositor) must become master, and
 * the kernel has no way to take master from a running program. Without master there are no
 * more commits, so the ring turns by drawing into the framebuffer the panels scan out. When a
 * compositor's commit replaces that framebuffer, pico4-splash exits.
 *
 * A freestanding aarch64 program without libc, like pico4-reboot, so that stage 1 runs it
 * without a C library and stage 2 runs the same file:
 *
 *   aarch64-linux-gnu-gcc -Os -static -nostdlib -ffreestanding -fno-stack-protector \
 *       -fno-asynchronous-unwind-tables -fno-builtin -Wall -Wextra -Werror \
 *       -Wl,--build-id=none,-z,max-page-size=4096 -s \
 *       -o prebuilt/aarch64/pico4-splash src/splash/pico4-splash.c
 *
 * usage: pico4-splash [SECONDS]   give up after SECONDS (default 300) even if nobody took over
 *
 * SIGTERM ends it; the kernel then removes its framebuffer and the panels go dark, unless a
 * compositor already shows its own frames.
 */
#include <linux/types.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>

#define NR_ioctl 29
#define NR_openat 56
#define NR_close 57
#define NR_write 64
#define NR_exit_group 94
#define NR_nanosleep 101
#define NR_clock_gettime 113
#define NR_mmap 222

#define AT_FDCWD -100
#define O_RDWR 02
#define O_CLOEXEC 02000000
#define PROT_READ 1
#define PROT_WRITE 2
#define MAP_SHARED 1
#define CLOCK_MONOTONIC 1
#define EINTR 4
/* The "type" plane property's values (libdrm's xf86drmMode.h, not the kernel's uapi). */
#define DRM_PLANE_TYPE_OVERLAY 0
#define DRM_PLANE_TYPE_CURSOR 2

#define EYE 2160         /* each panel is 2160x2160; the mode is both side by side */
#define LAYERS 6
#define STATE 14
#define MAX_OBJECTS 64
#define MAX_PROPS 96
#define MAX_MODES 32

struct timespec_k {
	long tv_sec, tv_nsec;
};

static long sys6(long n, long a, long b, long c, long d, long e, long f)
{
	register long x8 __asm__("x8") = n;
	register long x0 __asm__("x0") = a;
	register long x1 __asm__("x1") = b;
	register long x2 __asm__("x2") = c;
	register long x3 __asm__("x3") = d;
	register long x4 __asm__("x4") = e;
	register long x5 __asm__("x5") = f;
	__asm__ volatile("svc #0"
			 : "+r"(x0)
			 : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
			 : "memory", "cc");
	return x0;
}
#define sys(n, a, b, c) sys6(n, (long)(a), (long)(b), (long)(c), 0, 0, 0)

static int fd = -1;

/* One line in one write: /dev/kmsg, stage 1's log, makes a record of each write. */
static void say(const char *s)
{
	static const char prefix[] = "pico4-splash: ";
	char line[128];
	long n = 0;
	for (const char *p = prefix; *p; p++)
		line[n++] = *p;
	while (*s && n < (long)sizeof(line) - 1)
		line[n++] = *s++;
	line[n++] = '\n';
	sys(NR_write, 2, line, n);
}

static __attribute__((noreturn)) void quit(const char *why, int status)
{
	if (why)
		say(why);
	sys(NR_exit_group, status, 0, 0);
	__builtin_unreachable();
}

static int ioc(unsigned long request, void *arg)
{
	long r;
	do
		r = sys(NR_ioctl, fd, request, arg);
	while (r == -EINTR);
	return (int)r;
}

static void sleep_ms(long ms)
{
	struct timespec_k t = {ms / 1000, (ms % 1000) * 1000000};
	sys(NR_nanosleep, &t, 0, 0);
}

static long now_ms(void)
{
	struct timespec_k t;
	sys(NR_clock_gettime, CLOCK_MONOTONIC, &t, 0);
	return t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int eq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static int starts(const char *s, const char *prefix)
{
	while (*prefix && *s == *prefix)
		s++, prefix++;
	return !*prefix;
}

/* Some compilers emit calls to these even when freestanding. */
void *memset(void *d, int c, unsigned long n)
{
	unsigned char *p = d;
	while (n--)
		*p++ = (unsigned char)c;
	return d;
}
void *memcpy(void *d, const void *s, unsigned long n)
{
	unsigned char *p = d;
	const unsigned char *q = s;
	while (n--)
		*p++ = *q++;
	return d;
}

/* -- properties ------------------------------------------------------------------------- */

static __u32 prop_ids[MAX_PROPS];
static __u64 prop_values[MAX_PROPS];

/* The property NAME of an object: its id (0 if absent) and current value. */
static __u32 property(__u32 object, __u32 type, const char *name, __u64 *value)
{
	struct drm_mode_obj_get_properties get = {.obj_id = object, .obj_type = type};
	if (ioc(DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &get) || get.count_props > MAX_PROPS)
		return 0;
	get.props_ptr = (__u64)(unsigned long)prop_ids;
	get.prop_values_ptr = (__u64)(unsigned long)prop_values;
	if (ioc(DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &get))
		return 0;
	for (__u32 i = 0; i < get.count_props; i++) {
		struct drm_mode_get_property p = {.prop_id = prop_ids[i]};
		if (ioc(DRM_IOCTL_MODE_GETPROPERTY, &p))
			continue;
		if (eq(p.name, name)) {
			if (value)
				*value = prop_values[i];
			return prop_ids[i];
		}
	}
	return 0;
}

/* The value of enum property NAME's option OPTION on an object. */
static int enum_value(__u32 object, const char *name, const char *option, __u64 *value)
{
	__u32 id = property(object, DRM_MODE_OBJECT_PLANE, name, 0);
	struct drm_mode_property_enum enums[32];
	struct drm_mode_get_property p = {.prop_id = id};
	if (!id || ioc(DRM_IOCTL_MODE_GETPROPERTY, &p) || p.count_enum_blobs > 32)
		return 0;
	p.enum_blob_ptr = (__u64)(unsigned long)enums;
	p.count_values = 0;
	if (ioc(DRM_IOCTL_MODE_GETPROPERTY, &p))
		return 0;
	for (__u32 i = 0; i < p.count_enum_blobs; i++)
		if (eq(enums[i].name, option)) {
			*value = enums[i].value;
			return 1;
		}
	return 0;
}

static int has_xrgb(struct drm_mode_get_plane *plane, __u32 *formats)
{
	for (__u32 i = 0; i < plane->count_format_types; i++)
		if (formats[i] == 0x34325258) /* DRM_FORMAT_XRGB8888 */
			return 1;
	return 0;
}

/* -- the display ------------------------------------------------------------------------ */

static __u32 connector, crtc, crtc_index, planes[LAYERS];
static struct drm_mode_modeinfo mode;

/* Opens the stock display device, waiting for it (msm_drm probes late in stage 1). */
static void open_display(long wait_ms)
{
	long until = now_ms() + wait_ms;
	for (;;) {
		fd = (int)sys6(NR_openat, AT_FDCWD, (long)"/dev/dri/card0", O_RDWR | O_CLOEXEC, 0, 0, 0);
		if (fd >= 0) {
			char name[16] = {0};
			struct drm_version v = {.name_len = sizeof(name) - 1, .name = name};
			if (ioc(DRM_IOCTL_VERSION, &v) == 0 && eq(name, "msm_drm"))
				return;
			quit("/dev/dri/card0 is not the stock msm_drm", 1);
		}
		if (now_ms() > until)
			quit("no /dev/dri/card0", 1);
		sleep_ms(250);
	}
}

/* The connected DSI connector, its 90 Hz native mode and a CRTC for it. */
static void find_output(void)
{
	__u32 connectors[MAX_OBJECTS], crtcs[MAX_OBJECTS];
	struct drm_mode_card_res res = {0};
	if (ioc(DRM_IOCTL_MODE_GETRESOURCES, &res) || res.count_connectors > MAX_OBJECTS ||
	    res.count_crtcs > MAX_OBJECTS)
		quit("no display resources", 1);
	res.count_fbs = res.count_encoders = 0;
	res.connector_id_ptr = (__u64)(unsigned long)connectors;
	res.crtc_id_ptr = (__u64)(unsigned long)crtcs;
	if (ioc(DRM_IOCTL_MODE_GETRESOURCES, &res))
		quit("no display resources", 1);
	for (__u32 i = 0; i < res.count_connectors && !connector; i++) {
		struct drm_mode_modeinfo modes[MAX_MODES];
		__u32 encoders[MAX_OBJECTS];
		struct drm_mode_get_connector c = {.connector_id = connectors[i]};
		if (ioc(DRM_IOCTL_MODE_GETCONNECTOR, &c) || c.connector_type != DRM_MODE_CONNECTOR_DSI ||
		    c.connection != 1 || c.count_modes > MAX_MODES || c.count_encoders > MAX_OBJECTS)
			continue;
		c.count_props = 0;
		c.modes_ptr = (__u64)(unsigned long)modes;
		c.encoders_ptr = (__u64)(unsigned long)encoders;
		if (ioc(DRM_IOCTL_MODE_GETCONNECTOR, &c))
			continue;
		int found = 0;
		for (__u32 m = 0; m < c.count_modes && !found; m++)
			if (modes[m].hdisplay == 2 * EYE && modes[m].vdisplay == EYE &&
			    starts(modes[m].name, "4320x2160x90x")) {
				mode = modes[m];
				found = 1;
			}
		__u32 possible = 0;
		for (__u32 e = 0; e < c.count_encoders; e++) {
			struct drm_mode_get_encoder enc = {.encoder_id = encoders[e]};
			if (ioc(DRM_IOCTL_MODE_GETENCODER, &enc) == 0)
				possible |= enc.possible_crtcs;
		}
		for (__u32 k = 0; found && k < res.count_crtcs; k++)
			if (possible & (1u << k)) {
				connector = c.connector_id;
				crtc = crtcs[k];
				crtc_index = k;
				break;
			}
	}
	if (!connector)
		quit("no connected DSI output with the 4320x2160 90 Hz mode", 1);
}

/* The stock layers: four VIG (scaler) and two DMA planes. */
static void find_planes(void)
{
	__u32 ids[MAX_OBJECTS];
	struct drm_mode_get_plane_res res = {0};
	if (ioc(DRM_IOCTL_MODE_GETPLANERESOURCES, &res) || res.count_planes > MAX_OBJECTS)
		quit("no planes", 1);
	res.plane_id_ptr = (__u64)(unsigned long)ids;
	if (ioc(DRM_IOCTL_MODE_GETPLANERESOURCES, &res))
		quit("no planes", 1);
	unsigned nvig = 0, ndma = 0;
	for (__u32 i = 0; i < res.count_planes && nvig + ndma < LAYERS; i++) {
		__u32 formats[128];
		struct drm_mode_get_plane p = {.plane_id = ids[i]};
		if (ioc(DRM_IOCTL_MODE_GETPLANE, &p) || p.count_format_types > 128)
			continue;
		p.format_type_ptr = (__u64)(unsigned long)formats;
		if (ioc(DRM_IOCTL_MODE_GETPLANE, &p))
			continue;
		__u64 type = 0;
		property(p.plane_id, DRM_MODE_OBJECT_PLANE, "type", &type);
		int usable = has_xrgb(&p, formats) && type != DRM_PLANE_TYPE_CURSOR &&
			     (p.possible_crtcs & (1u << crtc_index));
		int scaler = property(p.plane_id, DRM_MODE_OBJECT_PLANE, "scaler_v2", 0) != 0;
		int multirect = property(p.plane_id, DRM_MODE_OBJECT_PLANE, "multirect_mode", 0) != 0;
		if (usable && scaler && nvig < 4)
			planes[nvig++] = p.plane_id;
		else if (usable && !scaler && !multirect && type == DRM_PLANE_TYPE_OVERLAY && ndma < 2)
			planes[4 + ndma++] = p.plane_id;
	}
	if (nvig != 4 || ndma != 2)
		quit("the stock layers (4 VIG, 2 DMA) are not there", 1);
}

/* -- buffers and drawing ---------------------------------------------------------------- */

struct buffer {
	__u32 fb, pitch;
	unsigned char *map;
};
static struct buffer buffer;

static void allocate(struct buffer *b)
{
	struct drm_mode_create_dumb create = {.width = EYE * 2, .height = EYE, .bpp = 32};
	if (ioc(DRM_IOCTL_MODE_CREATE_DUMB, &create))
		quit("cannot allocate a buffer", 1);
	struct drm_mode_fb_cmd2 fb = {
	    .width = EYE * 2, .height = EYE, .pixel_format = 0x34325258, /* XRGB8888 */
	    .handles = {create.handle}, .pitches = {create.pitch},
	};
	if (ioc(DRM_IOCTL_MODE_ADDFB2, &fb))
		quit("cannot add a framebuffer", 1);
	struct drm_mode_map_dumb map = {.handle = create.handle};
	if (ioc(DRM_IOCTL_MODE_MAP_DUMB, &map))
		quit("cannot map a buffer", 1);
	long addr = sys6(NR_mmap, 0, (long)create.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd,
			 (long)map.offset);
	if (addr < 0 && addr > -4096)
		quit("cannot map a buffer", 1);
	b->fb = fb.fb_id;
	b->pitch = create.pitch;
	b->map = (unsigned char *)addr;
	/* Dumb buffers start zeroed: black. */
}

/* Twelve dots on a circle in the middle of each eye; dot STEP is the brightest, the ones
 * behind it fade (a spinner). Integer maths only, on the square around the ring. */
#define DOTS 12
#define RING 70
#define DOT 11
static const int dot_x[DOTS] = {0, 35, 61, 70, 61, 35, 0, -35, -61, -70, -61, -35};
static const int dot_y[DOTS] = {-70, -61, -35, 0, 35, 61, 70, 61, 35, 0, -35, -61};

static void draw(struct buffer *b, int step)
{
	const int r = RING + DOT + 1;
	for (int eye = 0; eye < 2; eye++) {
		int cx = eye * EYE + EYE / 2, cy = EYE / 2;
		for (int y = -r; y <= r; y++) {
			unsigned *row = (unsigned *)(b->map + (unsigned long)(cy + y) * b->pitch) + cx;
			for (int x = -r; x <= r; x++) {
				unsigned level = 0;
				for (int d = 0; d < DOTS; d++) {
					int dx = x - dot_x[d], dy = y - dot_y[d];
					if (dx * dx + dy * dy <= DOT * DOT) {
						int age = (step - d + DOTS) % DOTS;
						level = age < 8 ? 200 - age * 22 : 30;
						break;
					}
				}
				row[x] = level << 16 | level << 8 | level;
			}
		}
	}
}

/* -- commits ---------------------------------------------------------------------------- */

static __u32 objects[LAYERS + 2], counts[LAYERS + 2], props[LAYERS * (STATE + 1) + 8];
static __u64 values[LAYERS * (STATE + 1) + 8];
static __u32 nobjects, nprops;

static void add(__u32 object, __u32 prop, __u64 value)
{
	if (!prop)
		quit("a display property is missing", 1);
	if (!nobjects || objects[nobjects - 1] != object) {
		objects[nobjects] = object;
		counts[nobjects++] = 0;
	}
	counts[nobjects - 1]++;
	props[nprops] = prop;
	values[nprops++] = value;
}

static int commit(__u32 flags)
{
	struct drm_mode_atomic a = {
	    .flags = flags, .count_objs = nobjects,
	    .objs_ptr = (__u64)(unsigned long)objects, .count_props_ptr = (__u64)(unsigned long)counts,
	    .props_ptr = (__u64)(unsigned long)props, .prop_values_ptr = (__u64)(unsigned long)values,
	};
	int r = ioc(DRM_IOCTL_MODE_ATOMIC, &a);
	nobjects = nprops = 0;
	return r;
}

/* Whether the first layer still shows one of our framebuffers. */
static int still_ours(void)
{
	__u64 fb = 0;
	property(planes[0], DRM_MODE_OBJECT_PLANE, "FB_ID", &fb);
	return fb == buffer.fb;
}

static long number(const char *s)
{
	long n = 0;
	if (!*s)
		return -1;
	for (; *s; s++) {
		if (*s < '0' || *s > '9')
			return -1;
		n = n * 10 + (*s - '0');
	}
	return n;
}

long pico4_main(long argc, char **argv)
{
	long seconds = argc > 1 ? number(argv[1]) : 300;
	if (argc > 2 || seconds <= 0) {
		say("usage: pico4-splash [SECONDS]");
		return argc > 1 && (eq(argv[1], "-h") || eq(argv[1], "--help")) ? 0 : 2;
	}
	long end = now_ms() + seconds * 1000;

	open_display(60000);
	struct drm_set_client_cap universal = {DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1};
	struct drm_set_client_cap atomic = {DRM_CLIENT_CAP_ATOMIC, 1};
	if (ioc(DRM_IOCTL_SET_CLIENT_CAP, &universal) || ioc(DRM_IOCTL_SET_CLIENT_CAP, &atomic))
		quit("no atomic modesetting", 1);
	/* Opening the device made us master unless someone else already is: then they show
	 * something and the splash is not needed. */
	if (ioc(DRM_IOCTL_SET_MASTER, 0))
		quit("another program drives the display", 0);
	find_output();
	find_planes();
	allocate(&buffer);
	draw(&buffer, 0);

	static const char *names[STATE] = {
	    "CRTC_ID", "sspp_layout", "layer_color_component", "blend_op", "rotation", "zpos", "CRTC_X",
	    "CRTC_Y",  "CRTC_W",      "CRTC_H",                "SRC_X",    "SRC_Y",    "SRC_W", "SRC_H",
	};
	static const int eye[LAYERS] = {1, 0, 1, 0, 1, 0};
	static const int stage[LAYERS] = {3, 2, 1, 0, 5, 4};
	static const char *color[LAYERS] = {"blue", "blue", "red", "red", "green", "green"};
	struct drm_mode_create_blob blob = {.data = (__u64)(unsigned long)&mode, .length = sizeof(mode)};
	if (ioc(DRM_IOCTL_MODE_CREATEPROPBLOB, &blob))
		quit("cannot create the mode", 1);
	add(connector, property(connector, DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID", 0), crtc);
	add(crtc, property(crtc, DRM_MODE_OBJECT_CRTC, "MODE_ID", 0), blob.blob_id);
	add(crtc, property(crtc, DRM_MODE_OBJECT_CRTC, "ACTIVE", 0), 1);
	for (int i = 0; i < LAYERS; i++) {
		__u64 layout, component, blend;
		if (!enum_value(planes[i], "sspp_layout", eye[i] ? "right" : "left", &layout) ||
		    !enum_value(planes[i], "layer_color_component", color[i], &component) ||
		    !enum_value(planes[i], "blend_op", "layer_color", &blend))
			quit("a layer lacks the stock properties", 1);
		__u64 state[STATE] = {
		    crtc, layout, component, blend, DRM_MODE_ROTATE_0, (__u64)stage[i], 0, 0, EYE, EYE,
		    (__u64)eye[i] * EYE << 16, 0, (__u64)EYE << 16, (__u64)EYE << 16,
		};
		add(planes[i], property(planes[i], DRM_MODE_OBJECT_PLANE, "FB_ID", 0), buffer.fb);
		for (int j = 0; j < STATE; j++)
			add(planes[i], property(planes[i], DRM_MODE_OBJECT_PLANE, names[j], 0), state[j]);
	}
	if (commit(DRM_MODE_ATOMIC_ALLOW_MODESET))
		quit("the panels refused the mode", 1);
	ioc(DRM_IOCTL_DROP_MASTER, 0);
	say("showing the boot indication");

	for (int step = 1; now_ms() < end; step++) {
		sleep_ms(83);
		if (!still_ours())
			quit("a compositor took the display", 0);
		draw(&buffer, step % DOTS);
	}
	quit("time is up", 0);
}

/* Entry point: argc at [sp], argv at sp+8. */
__asm__(".text\n"
	".global _start\n"
	"_start:\n"
	"	ldr x0, [sp]\n"
	"	add x1, sp, #8\n"
	"	bl pico4_main\n"
	"	mov x8, #94\n"
	"	svc #0\n");
