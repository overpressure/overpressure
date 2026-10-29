// SPDX-License-Identifier: MIT
/*
 * KMS check for the PICO 4 on the mainline msm driver, small enough for stage 1: no libdrm,
 * only the kernel's uapi, linked statically.
 *
 * Reports each msm card and its GPU (opening the GPU loads its firmware:
 * /lib/firmware/qcom/a650_sqe.fw, a650_gmu.bin and the zap shader the device tree names),
 * lists the connectors and their modes, sets the preferred mode of the first connected one
 * with a test frame (left eye: red border and white grid; right eye: blue border and grid; a
 * green band across both), measures the refresh rate over 180 vblanks, and keeps the frame on
 * screen for the given seconds.
 *
 *     pico4-kmstest [SECONDS]
 */
#define _GNU_SOURCE
#include <drm/drm.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_mode.h>
#include <drm/msm_drm.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define MAX_OBJECTS 32
#define VBLANKS 180

static int fd = -1;

static void fail(const char *what)
{
	fprintf(stderr, "%s: %s\n", what, strerror(errno));
	exit(1);
}

static int drm_ioctl(unsigned long request, void *arg)
{
	int ret;

	do
		ret = ioctl(fd, request, arg);
	while (ret == -1 && (errno == EINTR || errno == EAGAIN));
	return ret;
}

static double now(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec * 1e-9;
}

/*
 * The msm cards (stage 1 has no udev, but devtmpfs names them): with msm.separate_gpu_kms the
 * GPU is a card of its own. Each reports whether it has a GPU; the one with connectors is kept.
 */
static void open_card(void)
{
	char path[32], name[32];
	int kms = -1;

	for (int i = 0; i < 4; i++) {
		struct drm_version version = { .name = name, .name_len = sizeof(name) - 1 };
		struct drm_msm_param gpu = { .pipe = MSM_PIPE_3D0, .param = MSM_PARAM_CHIP_ID };
		struct drm_mode_card_res res = { 0 };

		snprintf(path, sizeof(path), "/dev/dri/card%d", i);
		fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		memset(name, 0, sizeof(name));
		/* "msm", or "msm-kms" for the display when the GPU is a card of its own */
		if (drm_ioctl(DRM_IOCTL_VERSION, &version) || strncmp(name, "msm", 3)) {
			close(fd);
			continue;
		}
		printf("%s: driver %s", path, name);
		if (drm_ioctl(DRM_IOCTL_MSM_GET_PARAM, &gpu))
			printf(", no GPU (%s)", strerror(errno));
		else
			printf(", GPU chip id %#llx", (unsigned long long)gpu.value);
		if (!drm_ioctl(DRM_IOCTL_MODE_GETRESOURCES, &res) && res.count_connectors) {
			printf(", %u connectors", res.count_connectors);
			if (kms < 0)
				kms = fd;
		}
		printf("\n");
		if (kms != fd)
			close(fd);
	}
	fd = kms;
	if (fd < 0) {
		errno = ENODEV;
		fail("no msm card with connectors");
	}
}

static void draw(uint32_t *pixels, uint32_t pitch, uint32_t width, uint32_t height)
{
	uint32_t eye = width / 2;

	for (uint32_t y = 0; y < height; y++) {
		uint32_t *row = (uint32_t *)((uint8_t *)pixels + (size_t)y * pitch);

		for (uint32_t x = 0; x < width; x++) {
			uint32_t ex = x % eye, right = x >= eye;
			uint32_t border = ex < 16 || ex >= eye - 16 || y < 16 || y >= height - 16;
			uint32_t grid = ex % 120 < 3 || y % 120 < 3;
			uint32_t band = y >= height / 2 - 60 && y < height / 2 + 60;
			uint32_t color = 0x101010;

			if (border)
				color = right ? 0x0000ff : 0xff0000;
			else if (band)
				color = 0x00c000;
			else if (grid)
				color = 0xc0c0c0;
			row[x] = color;
		}
	}
}

int main(int argc, char **argv)
{
	uint32_t connectors[MAX_OBJECTS], crtcs[MAX_OBJECTS], encoders[MAX_OBJECTS];
	struct drm_mode_modeinfo modes[MAX_OBJECTS], mode = { 0 };
	struct drm_mode_card_res res = { 0 };
	uint32_t connector = 0, crtc = 0, crtc_index = 0;
	int seconds = argc > 1 ? atoi(argv[1]) : 10;

	open_card();
	if (drm_ioctl(DRM_IOCTL_SET_MASTER, NULL))
		fail("SET_MASTER");
	if (drm_ioctl(DRM_IOCTL_MODE_GETRESOURCES, &res))
		fail("GETRESOURCES");
	if (res.count_connectors > MAX_OBJECTS || res.count_crtcs > MAX_OBJECTS ||
	    res.count_encoders > MAX_OBJECTS) {
		errno = E2BIG;
		fail("GETRESOURCES");
	}
	res.connector_id_ptr = (uintptr_t)connectors;
	res.crtc_id_ptr = (uintptr_t)crtcs;
	res.encoder_id_ptr = (uintptr_t)encoders;
	res.count_fbs = 0;
	if (drm_ioctl(DRM_IOCTL_MODE_GETRESOURCES, &res))
		fail("GETRESOURCES");
	printf("%u connectors, %u encoders, %u crtcs\n", res.count_connectors,
	       res.count_encoders, res.count_crtcs);

	for (uint32_t i = 0; i < res.count_connectors; i++) {
		struct drm_mode_get_connector conn = { .connector_id = connectors[i] };
		struct drm_mode_get_encoder enc = { 0 };
		uint32_t conn_encoders[MAX_OBJECTS];

		if (drm_ioctl(DRM_IOCTL_MODE_GETCONNECTOR, &conn))
			fail("GETCONNECTOR");
		if (conn.count_modes > MAX_OBJECTS)
			conn.count_modes = MAX_OBJECTS;
		if (conn.count_encoders > MAX_OBJECTS)
			conn.count_encoders = MAX_OBJECTS;
		conn.modes_ptr = (uintptr_t)modes;
		conn.encoders_ptr = (uintptr_t)conn_encoders;
		conn.count_props = 0;
		if (drm_ioctl(DRM_IOCTL_MODE_GETCONNECTOR, &conn))
			fail("GETCONNECTOR");
		printf("connector %u: type %u, status %u, %u modes\n", conn.connector_id,
		       conn.connector_type, conn.connection, conn.count_modes);
		for (uint32_t m = 0; m < conn.count_modes; m++)
			printf("  %s %ux%u %u Hz, clock %u kHz, htotal %u, vtotal %u%s\n",
			       modes[m].name, modes[m].hdisplay, modes[m].vdisplay, modes[m].vrefresh,
			       modes[m].clock, modes[m].htotal, modes[m].vtotal,
			       modes[m].type & DRM_MODE_TYPE_PREFERRED ? ", preferred" : "");
		if (connector || conn.connection != 1 || !conn.count_modes || !conn.count_encoders)
			continue;
		mode = modes[0];
		for (uint32_t m = 0; m < conn.count_modes; m++)
			if (modes[m].type & DRM_MODE_TYPE_PREFERRED)
				mode = modes[m];
		enc.encoder_id = conn_encoders[0];
		if (drm_ioctl(DRM_IOCTL_MODE_GETENCODER, &enc))
			fail("GETENCODER");
		for (uint32_t c = 0; c < res.count_crtcs; c++)
			if (enc.possible_crtcs & (1u << c)) {
				crtc = crtcs[c];
				crtc_index = c;
				break;
			}
		connector = conn.connector_id;
	}
	if (!connector || !crtc) {
		errno = ENODEV;
		fail("no connected connector with a crtc");
	}

	struct drm_mode_create_dumb dumb = { .width = mode.hdisplay, .height = mode.vdisplay, .bpp = 32 };
	if (drm_ioctl(DRM_IOCTL_MODE_CREATE_DUMB, &dumb))
		fail("CREATE_DUMB");
	struct drm_mode_map_dumb map = { .handle = dumb.handle };
	if (drm_ioctl(DRM_IOCTL_MODE_MAP_DUMB, &map))
		fail("MAP_DUMB");
	uint32_t *pixels = mmap(NULL, dumb.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, map.offset);
	if (pixels == MAP_FAILED)
		fail("mmap");
	draw(pixels, dumb.pitch, mode.hdisplay, mode.vdisplay);

	struct drm_mode_fb_cmd2 fb = {
		.width = mode.hdisplay, .height = mode.vdisplay, .pixel_format = DRM_FORMAT_XRGB8888,
		.handles = { dumb.handle }, .pitches = { dumb.pitch },
	};
	if (drm_ioctl(DRM_IOCTL_MODE_ADDFB2, &fb))
		fail("ADDFB2");

	struct drm_mode_crtc set = {
		.crtc_id = crtc, .fb_id = fb.fb_id, .set_connectors_ptr = (uintptr_t)&connector,
		.count_connectors = 1, .mode = mode, .mode_valid = 1,
	};
	double start = now();
	if (drm_ioctl(DRM_IOCTL_MODE_SETCRTC, &set))
		fail("SETCRTC");
	printf("mode %s set on connector %u, crtc %u in %.0f ms\n", mode.name, connector, crtc,
	       (now() - start) * 1e3);
	fflush(stdout);

	union drm_wait_vblank wait;
	unsigned int high = crtc_index > 1 ? (crtc_index << _DRM_VBLANK_HIGH_CRTC_SHIFT) & _DRM_VBLANK_HIGH_CRTC_MASK
					   : crtc_index ? _DRM_VBLANK_SECONDARY : 0;
	memset(&wait, 0, sizeof(wait));
	wait.request.type = _DRM_VBLANK_RELATIVE | high;
	wait.request.sequence = 1;
	if (drm_ioctl(DRM_IOCTL_WAIT_VBLANK, &wait)) {
		printf("no vblank: %s\n", strerror(errno));
	} else {
		start = now();
		for (int i = 0; i < VBLANKS; i++) {
			memset(&wait, 0, sizeof(wait));
			wait.request.type = _DRM_VBLANK_RELATIVE | high;
			wait.request.sequence = 1;
			if (drm_ioctl(DRM_IOCTL_WAIT_VBLANK, &wait))
				fail("WAIT_VBLANK");
		}
		printf("refresh %.2f Hz over %d vblanks\n", VBLANKS / (now() - start), VBLANKS);
	}
	fflush(stdout);
	sleep(seconds);
	return 0;
}
