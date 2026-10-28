// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-vidc-compat against a fake msm_vidc decoder that behaves as the PICO 4's was measured
 * (docs/session.md#steam-link): USERPTR buffers with the dma-buf fd in reserved[0], two capture
 * planes, parsing only with both queues streaming, the size as the vendor event, a capture
 * stream-off that faults the firmware unless the port was flushed, vendor buffer flags, no
 * QUERYBUF/EXPBUF/DISPLAY_DELAY/DEC_CMD_START. The client below does what vrlink does.
 */
#define _GNU_SOURCE
#include "pico4-vidc-compat.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CHECK(cond)                                                                                              \
	do {                                                                                                         \
		if (!(cond)) {                                                                                           \
			fprintf(stderr, "%s:%d: %s failed (errno %d)\n", __FILE__, __LINE__, #cond, errno);                \
			exit(1);                                                                                             \
		}                                                                                                        \
	} while (0)

#define FAKE_FD 77
#define STREAM_W 1536u
#define STREAM_H 6144u
#define UBWC v4l2_fourcc('Q', '1', '2', '8') // the driver's NV12_UBWC
#define QC08C v4l2_fourcc('Q', '0', '8', 'C')
#define MAXB 32

struct fake_queue
{
	uint32_t w, h, pixfmt;
	unsigned planes;
	uint32_t size[2];
	unsigned count;
	bool queued[MAXB];
	int fd[MAXB][2];
	uint32_t bytesused[MAXB];
	struct timeval ts[MAXB];
	bool streaming;
	bool done[MAXB]; // ready for DQBUF
	uint32_t done_flags[MAXB];
};

static struct
{
	struct fake_queue out, cap;
	uint32_t events[16];
	uint32_t event_data[16][2];
	int nevents;
	bool reconfig, flushed, crashed, session_stopped, eos_pending;
	bool blocking; // the client's fd: DQEVENT without a pending event would sleep forever here
	int decode_order, low_latency;
	uint64_t frames;
} f;

static void
event(uint32_t type, uint32_t a, uint32_t b)
{
	f.events[f.nevents] = type;
	f.event_data[f.nevents][0] = a;
	f.event_data[f.nevents][1] = b;
	f.nevents++;
}

static void
set_cap_format(uint32_t w, uint32_t h)
{
	uint32_t stride = (w + 511) / 512 * 512;
	f.cap.w = w;
	f.cap.h = h;
	f.cap.pixfmt = V4L2_PIX_FMT_NV12;
	f.cap.planes = 2;
	f.cap.size[0] = stride * ((h + 511) / 512 * 512 + ((h / 2) + 255) / 256 * 256);
	f.cap.size[1] = 16384;
}

//! The firmware: parse with both queues streaming, then one picture per bitstream buffer.
static void
process(void)
{
	if (!f.out.streaming || !f.cap.streaming || f.crashed) {
		return;
	}
	if (f.cap.w != STREAM_W || f.cap.h != STREAM_H) {
		if (!f.reconfig) {
			f.reconfig = true;
			event(0x08001003u, STREAM_H, STREAM_W);
		}
		return;
	}
	if (f.reconfig) {
		return;
	}
	for (unsigned o = 0; o < f.out.count; o++) {
		if (!f.out.queued[o]) {
			continue;
		}
		unsigned c = 0;
		while (c < f.cap.count && !f.cap.queued[c]) {
			c++;
		}
		if (c == f.cap.count) {
			return;
		}
		f.out.queued[o] = false;
		f.out.done[o] = true;
		f.out.done_flags[o] = 0x4000;
		f.cap.queued[c] = false;
		f.cap.done[c] = true;
		f.cap.done_flags[c] = 0x4000 | V4L2_BUF_FLAG_KEYFRAME | 0x00000080u; // + vendor "end of subframe"
		f.cap.ts[c] = f.out.ts[o];
		f.cap.bytesused[c] = f.cap.size[0];
		f.frames++;
	}
	if (f.eos_pending) {
		for (unsigned c = 0; c < f.cap.count; c++) {
			if (f.cap.queued[c]) {
				f.cap.queued[c] = false;
				f.cap.done[c] = true;
				f.cap.done_flags[c] = 0x4000 | 0x02000000u;
				f.cap.bytesused[c] = 0;
				f.eos_pending = false;
				break;
			}
		}
	}
}

static struct fake_queue *
fq(uint32_t type)
{
	return type == V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE ? &f.out : type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE ? &f.cap : NULL;
}

static void
fill_format(struct v4l2_format *fmt, struct fake_queue *q)
{
	struct v4l2_pix_format_mplane *p = &fmt->fmt.pix_mp;
	p->width = q->w;
	p->height = q->h;
	p->pixelformat = q->pixfmt;
	p->num_planes = q->planes;
	for (unsigned i = 0; i < q->planes; i++) {
		p->plane_fmt[i].sizeimage = q->size[i];
		p->plane_fmt[i].bytesperline = q == &f.cap && i == 0 ? (q->w + 511) / 512 * 512 : 0;
	}
}

static int
fail(int e)
{
	errno = e;
	return -1;
}

static int
fake_ioctl(void *ctx, int fd, unsigned long req, void *arg)
{
	(void)ctx;
	CHECK(fd == FAKE_FD);
	switch (req) {
	case VIDIOC_S_FMT: {
		struct v4l2_format *fmt = arg;
		struct fake_queue *q = fq(fmt->type);
		if (q == &f.out) {
			f.out.w = fmt->fmt.pix_mp.width;
			f.out.h = fmt->fmt.pix_mp.height;
			f.out.pixfmt = fmt->fmt.pix_mp.pixelformat;
			f.out.planes = 1;
			f.out.size[0] = 8847360;
		} else {
			if (fmt->fmt.pix_mp.pixelformat != V4L2_PIX_FMT_NV12 && fmt->fmt.pix_mp.pixelformat != UBWC) {
				return fail(EINVAL); // the driver's capture formats here (not mainline's QC08C)
			}
			set_cap_format(fmt->fmt.pix_mp.width, fmt->fmt.pix_mp.height);
			f.cap.pixfmt = fmt->fmt.pix_mp.pixelformat;
			if (f.cap.w == STREAM_W && f.cap.h == STREAM_H) {
				f.reconfig = false; // the reconfiguration ends with the new format
			}
		}
		fill_format(fmt, q);
		return 0;
	}
	case VIDIOC_G_FMT: {
		struct v4l2_format *fmt = arg;
		fill_format(fmt, fq(fmt->type));
		return 0;
	}
	case VIDIOC_REQBUFS: {
		struct v4l2_requestbuffers *r = arg;
		struct fake_queue *q = fq(r->type);
		if (r->memory != V4L2_MEMORY_USERPTR) {
			return fail(EINVAL);
		}
		if (q->streaming) {
			return fail(EBUSY);
		}
		q->count = r->count;
		memset(q->queued, 0, sizeof(q->queued));
		memset(q->done, 0, sizeof(q->done));
		return 0;
	}
	case VIDIOC_QBUF: {
		struct v4l2_buffer *b = arg;
		struct fake_queue *q = fq(b->type);
		if (b->memory != V4L2_MEMORY_USERPTR || b->length != q->planes || b->index >= q->count ||
		    b->m.planes[0].length % 4096 != 0) {
			return fail(EINVAL);
		}
		for (unsigned p = 0; p < q->planes; p++) {
			if (b->m.planes[p].length < q->size[p] || (int)b->m.planes[p].reserved[0] < 0 ||
			    b->m.planes[p].m.userptr != b->m.planes[p].reserved[0]) {
				return fail(EINVAL);
			}
			q->fd[b->index][p] = (int)b->m.planes[p].reserved[0];
		}
		q->queued[b->index] = true;
		q->ts[b->index] = b->timestamp;
		q->bytesused[b->index] = b->m.planes[0].bytesused;
		process();
		return 0;
	}
	case VIDIOC_DQBUF: {
		struct v4l2_buffer *b = arg;
		struct fake_queue *q = fq(b->type);
		if (b->memory != V4L2_MEMORY_USERPTR || b->length != q->planes) {
			return fail(EINVAL);
		}
		for (unsigned i = 0; i < q->count; i++) {
			if (q->done[i]) {
				q->done[i] = false;
				b->index = i;
				b->flags = q->done_flags[i];
				b->field = 0;
				b->sequence = 0;
				b->timestamp = q->ts[i];
				for (unsigned p = 0; p < q->planes; p++) {
					b->m.planes[p].bytesused = p == 0 ? q->bytesused[i] : 16384;
					b->m.planes[p].reserved[0] = (uint32_t)q->fd[i][p];
				}
				return 0;
			}
		}
		return fail(EAGAIN);
	}
	case VIDIOC_STREAMON: {
		struct fake_queue *q = fq(*(uint32_t *)arg);
		q->streaming = true;
		process();
		return 0;
	}
	case VIDIOC_STREAMOFF: {
		struct fake_queue *q = fq(*(uint32_t *)arg);
		if (q == &f.cap && f.reconfig) {
			bool held = false;
			for (unsigned i = 0; i < f.cap.count; i++) {
				held |= f.cap.queued[i];
			}
			if (held || !f.flushed) {
				f.crashed = true; // buffers unmapped under the firmware
			}
		} else {
			f.session_stopped = true;
		}
		q->streaming = false;
		return 0;
	}
	case VIDIOC_DECODER_CMD: {
		struct v4l2_decoder_cmd *c = arg;
		if (c->cmd == 4 && c->flags == 1) {
			return 0; // "Input only flush not supported, making it flush all": and nothing happens
		}
		if (c->cmd == 4 && c->flags == 3) {
			for (unsigned i = 0; i < f.out.count; i++) {
				if (f.out.queued[i]) {
					f.out.queued[i] = false;
					f.out.done[i] = true;
					f.out.done_flags[i] = 0x4000;
				}
			}
			for (unsigned i = 0; i < f.cap.count; i++) {
				if (f.cap.queued[i]) {
					f.cap.queued[i] = false;
					f.cap.done[i] = true;
					f.cap.done_flags[i] = 0x4000;
					f.cap.bytesused[i] = 0;
				}
			}
			event(0x08001001u, 3, 0);
			return 0;
		}
		if (c->cmd == 4 && c->flags == 2) {
			for (unsigned i = 0; i < f.cap.count; i++) {
				if (f.cap.queued[i]) {
					f.cap.queued[i] = false;
					f.cap.done[i] = true;
					f.cap.done_flags[i] = 0x4000;
					f.cap.bytesused[i] = 0;
				}
			}
			f.flushed = true;
			event(0x08001001u, 2, 0);
			return 0;
		}
		if (c->cmd == V4L2_DEC_CMD_STOP) {
			f.eos_pending = true;
			process();
			return 0;
		}
		return fail(EINVAL);
	}
	case VIDIOC_SUBSCRIBE_EVENT: return 0;
	case VIDIOC_DQEVENT: {
		if (f.nevents == 0) {
			CHECK(!f.blocking);
			return fail(ENOENT);
		}
		struct v4l2_event *e = arg;
		memset(e, 0, sizeof(*e));
		e->type = f.events[0];
		memcpy(e->u.data, f.event_data[0], 8);
		memmove(&f.events[0], &f.events[1], sizeof(f.events[0]) * (size_t)(f.nevents - 1));
		memmove(&f.event_data[0], &f.event_data[1], sizeof(f.event_data[0]) * (size_t)(f.nevents - 1));
		f.nevents--;
		return 0;
	}
	case VIDIOC_S_CTRL: {
		struct v4l2_control *c = arg;
		if (c->id == 0x992003u) {
			f.decode_order = c->value;
			return 0;
		}
		if (c->id == 0x992038u) {
			f.low_latency = c->value;
			return 0;
		}
		return fail(EINVAL);
	}
	default: return fail(ENOTTY);
	}
}

static int
fake_alloc(void *ctx, size_t len)
{
	(void)ctx;
	int fd = memfd_create("vidc", MFD_CLOEXEC);
	CHECK(fd >= 0 && ftruncate(fd, (off_t)len) == 0);
	return fd;
}

static int
fake_close(void *ctx, int fd)
{
	(void)ctx;
	return close(fd);
}

static int
fake_poll(void *ctx, int fd, short events, int timeout_ms)
{
	(void)ctx, (void)fd, (void)timeout_ms;
	int revents = f.nevents > 0 ? POLLPRI : 0;
	for (unsigned i = 0; i < MAXB; i++) {
		revents |= f.cap.done[i] || f.out.done[i] ? POLLIN : 0;
	}
	return revents & (events | POLLERR);
}

static int
fake_blocking(void *ctx, int fd)
{
	(void)ctx, (void)fd;
	return f.blocking;
}

static void
fake_log(void *ctx, const char *line)
{
	(void)ctx;
	printf("  %s\n", line);
}

static int
io(unsigned long req, void *arg)
{
	return vidc_ioctl(FAKE_FD, req, arg);
}

static void
mmap_check(uint32_t type, unsigned index, uint32_t min_length)
{
	struct v4l2_plane plane;
	struct v4l2_buffer b = {.index = index, .type = type, .memory = V4L2_MEMORY_MMAP, .length = 1, .m.planes = &plane};
	CHECK(io(VIDIOC_QUERYBUF, &b) == 0);
	CHECK(b.length == 1 && plane.length >= min_length && plane.length % 4096 == 0);
	int target;
	off_t offset;
	CHECK(vidc_mmap_target(FAKE_FD, plane.m.mem_offset, &target, &offset) == 0 && offset == 0);
	uint8_t *p = mmap(NULL, plane.length, PROT_READ | PROT_WRITE, MAP_SHARED, target, offset);
	CHECK(p != MAP_FAILED);
	p[0] = 0x42;
	CHECK(munmap(p, plane.length) == 0);
	CHECK(vidc_mmap_target(FAKE_FD, plane.m.mem_offset + 4096, &target, &offset) == -1);
}

int
main(void)
{
	const struct vidc_ops ops = {.ioctl = fake_ioctl,
	                             .alloc = fake_alloc,
	                             .close = fake_close,
	                             .poll = fake_poll,
	                             .blocking = fake_blocking,
	                             .log = fake_log};
	CHECK(vidc_attach(&ops, FAKE_FD) == 0 && vidc_attached(FAKE_FD) && !vidc_attached(FAKE_FD + 1));

	// Pictures as soon as decoded: the driver's decode order and low latency.
	struct v4l2_control c = {.id = V4L2_CID_MPEG_VIDEO_DEC_DISPLAY_DELAY_ENABLE, .value = 1};
	CHECK(io(VIDIOC_S_CTRL, &c) == 0);
	c = (struct v4l2_control){.id = V4L2_CID_MPEG_VIDEO_DEC_DISPLAY_DELAY, .value = 0};
	CHECK(io(VIDIOC_S_CTRL, &c) == 0);
	CHECK(f.decode_order == 1 && f.low_latency == 1);

	// The bitstream format; the capture side gets the placeholder's.
	struct v4l2_format fmt = {.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE};
	fmt.fmt.pix_mp.width = STREAM_W;
	fmt.fmt.pix_mp.height = STREAM_H;
	fmt.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_HEVC;
	CHECK(io(VIDIOC_S_FMT, &fmt) == 0 && fmt.fmt.pix_mp.plane_fmt[0].sizeimage == 8847360);
	CHECK(f.cap.w == 256 && f.cap.h == 256);

	// MMAP bitstream buffers: USERPTR underneath, mapped through QUERYBUF and mmap.
	struct v4l2_requestbuffers r = {.count = 16, .type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, .memory = V4L2_MEMORY_MMAP};
	CHECK(io(VIDIOC_REQBUFS, &r) == 0 && r.count == 16 && (r.capabilities & V4L2_BUF_CAP_SUPPORTS_MMAP));
	CHECK(f.out.count == 16);
	mmap_check(V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, 3, 8847360);

	struct v4l2_event_subscription s = {.type = V4L2_EVENT_SOURCE_CHANGE};
	CHECK(io(VIDIOC_SUBSCRIBE_EVENT, &s) == 0);
	for (unsigned i = 0; i < 4; i++) {
		struct v4l2_plane plane = {.bytesused = 1000 + i};
		struct v4l2_buffer b = {.index = i, .type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, .memory = V4L2_MEMORY_MMAP,
		                        .length = 1, .m.planes = &plane, .timestamp = {.tv_sec = 1, .tv_usec = (long)i}};
		CHECK(io(VIDIOC_QBUF, &b) == 0 && f.out.bytesused[i] == 1000 + i);
	}
	uint32_t type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
	CHECK(io(VIDIOC_STREAMON, &type) == 0);
	CHECK(f.cap.streaming && f.reconfig); // the placeholder made the firmware parse

	// The client's capture queue is not set up yet: nothing to dequeue there.
	struct v4l2_plane cplane;
	struct v4l2_buffer cb = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP, .length = 1,
	                         .m.planes = &cplane};
	CHECK(io(VIDIOC_DQBUF, &cb) == -1 && errno == EAGAIN);

	// The size arrives as SOURCE_CHANGE, the placeholder flushed and gone, the session alive. On
	// vrlink's blocking fd the flush dequeues only pending events (the fake fails any other).
	f.blocking = true;
	struct v4l2_event e;
	CHECK(io(VIDIOC_DQEVENT, &e) == 0);
	CHECK(e.type == V4L2_EVENT_SOURCE_CHANGE && e.u.src_change.changes == V4L2_EVENT_SRC_CH_RESOLUTION);
	CHECK(!f.crashed && !f.session_stopped && !f.cap.streaming && f.cap.count == 0);
	f.blocking = false;
	CHECK(io(VIDIOC_DQEVENT, &e) == -1 && errno == ENOENT);
	event(0x08001005u, 0, 0); // vendor events alone: nothing for the client
	CHECK(io(VIDIOC_DQEVENT, &e) == -1 && errno == ENOENT && f.nevents == 0);
	f.blocking = true;

	// The client's capture setup: one NV12 plane, MMAP buffers, EXPBUF.
	fmt = (struct v4l2_format){.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE};
	CHECK(io(VIDIOC_G_FMT, &fmt) == 0 && fmt.fmt.pix_mp.num_planes == 1);
	type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
	CHECK(io(VIDIOC_STREAMOFF, &type) == 0 && !f.crashed && !f.session_stopped);
	fmt.fmt.pix_mp.width = STREAM_W;
	fmt.fmt.pix_mp.height = STREAM_H;
	fmt.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
	fmt.fmt.pix_mp.num_planes = 1;
	CHECK(io(VIDIOC_S_FMT, &fmt) == 0);
	CHECK(fmt.fmt.pix_mp.num_planes == 1 && fmt.fmt.pix_mp.plane_fmt[0].bytesperline == STREAM_W &&
	      fmt.fmt.pix_mp.plane_fmt[1].sizeimage == 0);
	// The largest picture so far is the decoder's minimum buffer (the firmware keeps it when the
	// size drops): the client's memory and the mapped buffers are that large.
	size_t floor = vidc_picture_floor();
	CHECK(floor >= f.cap.size[0] && floor % 4096 == 0);
	struct v4l2_format small = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE};
	small.fmt.pix_mp.width = 256;
	small.fmt.pix_mp.height = 256;
	small.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
	CHECK(io(VIDIOC_S_FMT, &small) == 0 && vidc_picture_floor() == floor);
	// vrlink decodes into QC08C, the driver's NV12_UBWC.
	fmt.fmt.pix_mp.pixelformat = QC08C;
	CHECK(io(VIDIOC_S_FMT, &fmt) == 0 && f.cap.pixfmt == UBWC);
	CHECK(fmt.fmt.pix_mp.pixelformat == QC08C && fmt.fmt.pix_mp.num_planes == 1);
	struct v4l2_format got = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE};
	CHECK(io(VIDIOC_G_FMT, &got) == 0 && got.fmt.pix_mp.pixelformat == QC08C);
	r = (struct v4l2_requestbuffers){.count = 4, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP};
	CHECK(io(VIDIOC_REQBUFS, &r) == 0 && r.count == 4 && f.cap.count == 4);
	mmap_check(V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, 0, f.cap.size[0]);
	struct v4l2_exportbuffer x = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .index = 2, .flags = O_CLOEXEC};
	CHECK(io(VIDIOC_EXPBUF, &x) == 0 && x.fd >= 0 && close(x.fd) == 0);
	for (unsigned i = 0; i < 4; i++) {
		struct v4l2_plane plane = {0};
		struct v4l2_buffer b = {.index = i, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP,
		                        .length = 1, .m.planes = &plane};
		CHECK(io(VIDIOC_QBUF, &b) == 0);
	}
	CHECK(io(VIDIOC_STREAMON, &type) == 0 && f.frames == 4);

	// Pictures: one plane, mainline flags, the bitstream's timestamps, counted sequence.
	for (unsigned i = 0; i < 4; i++) {
		memset(&cplane, 0, sizeof(cplane));
		cb = (struct v4l2_buffer){.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP,
		                          .length = 1, .m.planes = &cplane};
		CHECK(io(VIDIOC_DQBUF, &cb) == 0);
		CHECK(cb.memory == V4L2_MEMORY_MMAP && cb.length == 1 && cb.sequence == i && cb.field == V4L2_FIELD_NONE);
		CHECK((cb.flags & V4L2_BUF_FLAG_KEYFRAME) && !(cb.flags & 0x00000080u) && !(cb.flags & V4L2_BUF_FLAG_LAST));
		CHECK(cb.timestamp.tv_sec == 1 && cplane.bytesused == f.cap.size[0] && cplane.m.mem_offset != 0);
	}
	struct v4l2_plane oplane;
	struct v4l2_buffer ob = {.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, .memory = V4L2_MEMORY_MMAP, .length = 1,
	                         .m.planes = &oplane};
	CHECK(io(VIDIOC_DQBUF, &ob) == 0 && ob.memory == V4L2_MEMORY_MMAP && ob.index < 4);

	// vrlink's reset, the bitstream streamed off and on: a full flush, with the driver's session
	// (and stream) kept and the client's queued pictures queued again.
	struct v4l2_plane pic = {0};
	struct v4l2_buffer pb = {.index = 2, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP,
	                         .length = 1, .m.planes = &pic};
	CHECK(io(VIDIOC_QBUF, &pb) == 0 && f.cap.queued[2]);
	for (unsigned i = 6; i < 8; i++) {
		oplane = (struct v4l2_plane){.bytesused = 300};
		ob = (struct v4l2_buffer){.index = i, .type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, .memory = V4L2_MEMORY_MMAP,
		                          .length = 1, .m.planes = &oplane};
		CHECK(io(VIDIOC_QBUF, &ob) == 0);
	}
	uint32_t otype = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
	CHECK(io(VIDIOC_STREAMOFF, &otype) == 0);
	CHECK(!f.session_stopped && f.out.streaming && !f.out.queued[7] && !f.out.done[7]);
	CHECK(f.cap.queued[2] && !f.cap.done[2]);
	CHECK(io(VIDIOC_STREAMON, &otype) == 0 && f.out.streaming && !f.session_stopped);

	// START is accepted, STOP ends with V4L2_BUF_FLAG_LAST.
	struct v4l2_decoder_cmd cmd = {.cmd = V4L2_DEC_CMD_START};
	CHECK(io(VIDIOC_DECODER_CMD, &cmd) == 0);
	struct v4l2_plane plane = {0};
	struct v4l2_buffer b = {.index = 1, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP,
	                        .length = 1, .m.planes = &plane};
	CHECK(io(VIDIOC_QBUF, &b) == 0);
	cmd = (struct v4l2_decoder_cmd){.cmd = V4L2_DEC_CMD_STOP};
	CHECK(io(VIDIOC_DECODER_CMD, &cmd) == 0);
	memset(&cplane, 0, sizeof(cplane));
	cb = (struct v4l2_buffer){.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP, .length = 1,
	                          .m.planes = &cplane};
	CHECK(io(VIDIOC_DQBUF, &cb) == 0 && (cb.flags & V4L2_BUF_FLAG_LAST) && !(cb.flags & 0x02000000u));

	// Teardown: the client's REQBUFS 0 releases the buffers.
	CHECK(io(VIDIOC_STREAMOFF, &type) == 0);
	r = (struct v4l2_requestbuffers){.count = 0, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_MMAP};
	CHECK(io(VIDIOC_REQBUFS, &r) == 0 && f.cap.count == 0);

	// vrlink's pictures: its own dma-bufs (DMABUF), the driver's extradata plane allocated here.
	r = (struct v4l2_requestbuffers){.count = 2, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_DMABUF};
	CHECK(io(VIDIOC_REQBUFS, &r) == 0 && r.count == 2 && f.cap.count == 2);
	CHECK(r.capabilities & V4L2_BUF_CAP_SUPPORTS_DMABUF);
	int client[2];
	for (unsigned i = 0; i < 2; i++) {
		client[i] = fake_alloc(NULL, (f.cap.size[0] + 4095) / 4096 * 4096);
		struct v4l2_plane cp = {.m.fd = client[i]};
		struct v4l2_buffer qb = {.index = i, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_DMABUF,
		                         .length = 1, .m.planes = &cp};
		CHECK(io(VIDIOC_QBUF, &qb) == 0);
		CHECK(f.cap.fd[i][0] == client[i] && f.cap.fd[i][1] >= 0 && f.cap.fd[i][1] != client[i]);
	}
	CHECK(io(VIDIOC_STREAMON, &type) == 0);
	oplane = (struct v4l2_plane){.bytesused = 500};
	ob = (struct v4l2_buffer){.index = 5, .type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, .memory = V4L2_MEMORY_MMAP,
	                          .length = 1, .m.planes = &oplane};
	CHECK(io(VIDIOC_QBUF, &ob) == 0);
	memset(&cplane, 0, sizeof(cplane));
	cb = (struct v4l2_buffer){.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_DMABUF, .length = 1,
	                          .m.planes = &cplane};
	CHECK(io(VIDIOC_DQBUF, &cb) == 0 && cb.memory == V4L2_MEMORY_DMABUF && cb.index == 0);
	CHECK(cplane.m.fd == client[0] && cplane.bytesused == f.cap.size[0]);
	CHECK(io(VIDIOC_STREAMOFF, &type) == 0);
	r = (struct v4l2_requestbuffers){.count = 0, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, .memory = V4L2_MEMORY_DMABUF};
	CHECK(io(VIDIOC_REQBUFS, &r) == 0 && f.cap.count == 0);
	CHECK(fcntl(client[0], F_GETFD) >= 0 && close(client[0]) == 0 && close(client[1]) == 0); // still the client's

	// Without the client's capture queue the bitstream's stream-off is the driver's.
	CHECK(io(VIDIOC_STREAMOFF, &otype) == 0 && !f.out.streaming && f.session_stopped);
	r = (struct v4l2_requestbuffers){.count = 0, .type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, .memory = V4L2_MEMORY_MMAP};
	CHECK(io(VIDIOC_REQBUFS, &r) == 0 && f.out.count == 0);
	vidc_detach(FAKE_FD);
	CHECK(!vidc_attached(FAKE_FD));
	puts("pico4-vidc-compat: ok");
	return 0;
}
