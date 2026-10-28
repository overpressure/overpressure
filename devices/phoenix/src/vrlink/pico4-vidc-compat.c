// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-vidc-compat: the mainline stateful-decoder API on the stock kernel's msm_vidc decoder
 * (pico4-vidc-compat.h, docs/session.md#steam-link).
 *
 * What the client (SteamVR's vrlink, written for the Steam Frame's decoder) does, and what the
 * downstream driver needs instead:
 *
 * - Buffers: the client asks for MMAP bitstream buffers and maps them through QUERYBUF and mmap,
 *   and queues its own dma-bufs (DMABUF, memory from its Vulkan device) for the pictures. The
 *   driver takes only USERPTR buffers whose plane carries a dma-buf fd in reserved[0] (and has
 *   no QUERYBUF, EXPBUF or mmap). Each MMAP buffer is therefore one dma-buf per plane, allocated
 *   here, handed to the driver as USERPTR and to the client as an mmap offset or an exported fd;
 *   a DMABUF buffer is the client's dma-buf with the driver's other planes allocated here.
 * - Planes: the driver's NV12 capture buffers have a second plane (16 KiB of extradata, also
 *   when extradata is off); the client sees one plane.
 * - Formats: the client decodes into V4L2_PIX_FMT_QC08C, mainline's name for Venus's UBWC NV12
 *   (what the GPU imports with DRM_FORMAT_MOD_QCOM_COMPRESSED); the driver calls the same
 *   format NV12_UBWC.
 * - The first picture size: the client streams the bitstream and waits for
 *   V4L2_EVENT_SOURCE_CHANGE before it sets up the capture queue. The driver's firmware parses
 *   only when both queues stream, and reports a size with its own event. So a placeholder
 *   capture queue (256x256, two buffers) streams with the bitstream; the driver's
 *   port-settings event becomes SOURCE_CHANGE once the placeholder is flushed, released and
 *   streamed off (unflushed, the firmware faults on buffers it still holds).
 * - Resets: the client streams the bitstream off and on to drop what is pending (its decoder
 *   keeps its state); the driver's stream-off ends the session instead, so it becomes the
 *   driver's flush of the bitstream port, and the real stream-off waits for the buffers' release.
 * - DISPLAY_DELAY_ENABLE with DISPLAY_DELAY 0 (pictures as soon as decoded) becomes the
 *   driver's decode order and low latency controls; V4L2_DEC_CMD_START is accepted as a no-op;
 *   the driver's own buffer flags become V4L2_BUF_FLAG_LAST and _ERROR.
 */
#define _GNU_SOURCE
#include "pico4-vidc-compat.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

// The downstream driver's vendor interface.
#define VIDC_EVENT_FLUSH_DONE 0x08001001u
#define VIDC_EVENT_RECONFIG 0x08001003u // port settings changed: data[0] height, data[1] width
#define VIDC_EVENT_SYS_ERROR 0x08001005u
#define VIDC_EVENT_UNSUPPORTED 0x0800100au
#define VIDC_CMD_FLUSH 4u
#define VIDC_FLUSH_OUTPUT 1u
#define VIDC_FLUSH_CAPTURE 2u
#define VIDC_CID_DECODE_ORDER 0x992003u
#define VIDC_CID_LOW_LATENCY 0x992038u
#define VIDC_BUF_EOS 0x02000000u
#define VIDC_BUF_READONLY 0x04000000u
#define VIDC_BUF_UNSUPPORTED 0x01000000u
#define VIDC_BUF_CORRUPT 0x00400000u
#define VIDC_BUF_CODECCONFIG 0x00020000u
#define VIDC_BUF_SUBFRAME 0x00000080u
#define VIDC_BUF_VENDOR                                                                                          \
	(VIDC_BUF_EOS | VIDC_BUF_READONLY | VIDC_BUF_UNSUPPORTED | VIDC_BUF_CORRUPT | VIDC_BUF_CODECCONFIG |       \
	 VIDC_BUF_SUBFRAME)
//! The driver's NV12 rows and plane heights are multiples of this (its "NV12_512").
#define VIDC_NV12_ALIGN 512u
#define VIDC_PIX_FMT_NV12_UBWC v4l2_fourcc('Q', '1', '2', '8')
#ifndef V4L2_PIX_FMT_QC08C
#define V4L2_PIX_FMT_QC08C v4l2_fourcc('Q', '0', '8', 'C')
#endif

#define PLACEHOLDER_SIZE 256u
#define PLACEHOLDER_BUFFERS 2u
#define FLUSH_TIMEOUT_MS 2000
#define EVENT_WAIT_MS 100
#define MAX_DEVICES 4
#define MAX_BUFFERS 32u
#define MAX_PLANES 2u
#define PAGE_BYTES 4096u

struct vidc_buffer
{
	int fd[MAX_PLANES]; //!< Allocated here; -1 for the client's own DMABUF plane.
	uint32_t length[MAX_PLANES];
	int client_fd; //!< DMABUF: the client's dma-buf for plane 0 at its last QBUF.
};

struct vidc_queue
{
	uint32_t type;
	/*!
	 * The client's buffers: 0 (passed through), V4L2_MEMORY_MMAP (dma-bufs allocated here) or
	 * V4L2_MEMORY_DMABUF (the client's dma-buf as the picture plane, the driver's other planes
	 * allocated here).
	 */
	uint32_t memory;
	unsigned count;  //!< Buffers allocated here (the client's or the placeholder's).
	unsigned planes; //!< The driver's planes per buffer.
	struct vidc_buffer buf[MAX_BUFFERS];
	bool streaming; //!< The client's STREAMON.
	uint32_t sequence;
};

struct vidc_device
{
	bool used;
	int fd;
	struct vidc_ops ops;
	pthread_mutex_t lock;
	struct vidc_queue out, cap;
	bool placeholder; //!< The capture queue streams the placeholder.
	//! The client streamed the bitstream off, which was a flush: the driver still streams it.
	bool out_flushed;
	//! A port-settings event dequeued during a flush, for the client's next DQEVENT.
	struct v4l2_event held;
	bool held_reconfig;
	int delay_enable;
	bool warned_layout;
	/*!
	 * The largest picture the client's format has had: after a smaller picture size the firmware
	 * still takes only buffers that large (and the driver checks the dma-buf's size), while it
	 * writes just the smaller pictures.
	 */
	uint32_t picture_floor;
};

static struct vidc_device g_dev[MAX_DEVICES];
static pthread_mutex_t g_table = PTHREAD_MUTEX_INITIALIZER;

static void
say(struct vidc_device *d, const char *fmt, ...)
{
	if (d->ops.log == NULL) {
		return;
	}
	char line[256];
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	d->ops.log(d->ops.ctx, line);
}

static int
drv(struct vidc_device *d, unsigned long request, void *arg)
{
	return d->ops.ioctl(d->ops.ctx, d->fd, request, arg);
}

static struct vidc_device *
find(int fd)
{
	for (int i = 0; i < MAX_DEVICES; i++) {
		if (g_dev[i].used && g_dev[i].fd == fd) {
			return &g_dev[i];
		}
	}
	return NULL;
}

static struct vidc_queue *
queue_of(struct vidc_device *d, uint32_t type)
{
	if (type == V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE) {
		return &d->out;
	}
	if (type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE) {
		return &d->cap;
	}
	return NULL;
}

//! The mmap offset of a client buffer's plane: page-aligned, never 0.
static off_t
offset_of(const struct vidc_queue *q, unsigned index, unsigned plane)
{
	off_t capture = q->type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
	return (1 + capture) << 30 | (off_t)index << 16 | (off_t)plane << 12;
}

static void
free_buffers(struct vidc_device *d, struct vidc_queue *q)
{
	for (unsigned i = 0; i < q->count; i++) {
		for (unsigned p = 0; p < MAX_PLANES; p++) {
			if (q->buf[i].fd[p] >= 0) {
				d->ops.close(d->ops.ctx, q->buf[i].fd[p]);
			}
			q->buf[i].fd[p] = -1;
		}
	}
	q->count = 0;
}

//! count buffers of the driver's current format for the queue (from first_plane on).
static int
alloc_buffers(struct vidc_device *d, struct vidc_queue *q, unsigned count, unsigned first_plane)
{
	struct v4l2_format f = {.type = q->type};
	if (drv(d, VIDIOC_G_FMT, &f) != 0) {
		return -1;
	}
	unsigned planes = f.fmt.pix_mp.num_planes;
	if (planes < 1 || planes > MAX_PLANES) {
		say(d, "the driver's format has %u planes", planes);
		errno = EINVAL;
		return -1;
	}
	free_buffers(d, q);
	q->planes = planes;
	for (unsigned i = 0; i < count; i++) {
		for (unsigned p = 0; p < MAX_PLANES; p++) {
			q->buf[i].fd[p] = -1;
			q->buf[i].length[p] = 0;
		}
		q->buf[i].client_fd = -1;
	}
	q->count = count;
	for (unsigned i = 0; i < count; i++) {
		for (unsigned p = 0; p < planes; p++) {
			uint32_t length = (f.fmt.pix_mp.plane_fmt[p].sizeimage + PAGE_BYTES - 1) / PAGE_BYTES * PAGE_BYTES;
			if (q == &d->cap && p == 0 && length < d->picture_floor) {
				length = d->picture_floor;
			}
			q->buf[i].length[p] = length;
			if (p < first_plane) {
				continue;
			}
			int fd = d->ops.alloc(d->ops.ctx, length);
			if (fd < 0) {
				int e = errno;
				say(d, "no %u-byte buffer: %s", length, strerror(e));
				free_buffers(d, q);
				errno = e;
				return -1;
			}
			q->buf[i].fd[p] = fd;
		}
	}
	return 0;
}

static int
driver_reqbufs(struct vidc_device *d, struct vidc_queue *q, unsigned count)
{
	struct v4l2_requestbuffers r = {.count = count, .type = q->type, .memory = V4L2_MEMORY_USERPTR};
	return drv(d, VIDIOC_REQBUFS, &r);
}

//! Queues buffer index; client (or NULL) gives its timestamp, flags and the bitstream's length.
static int
driver_qbuf(struct vidc_device *d, struct vidc_queue *q, unsigned index, const struct v4l2_buffer *client)
{
	struct v4l2_plane planes[MAX_PLANES];
	memset(planes, 0, sizeof(planes));
	struct v4l2_buffer b = {
	    .index = index,
	    .type = q->type,
	    .memory = V4L2_MEMORY_USERPTR,
	    .field = V4L2_FIELD_NONE,
	    .length = q->planes,
	    .m.planes = planes,
	};
	for (unsigned p = 0; p < q->planes; p++) {
		int fd = q->buf[index].fd[p];
		uint32_t length = q->buf[index].length[p];
		if (p == 0 && q->memory == V4L2_MEMORY_DMABUF && client != NULL && client->length >= 1) {
			fd = client->m.planes[0].m.fd;
			off_t size = lseek(fd, 0, SEEK_END); // the dma-buf's size
			if (size < 0) {
				return -1;
			}
			length = (uint32_t)size;
			q->buf[index].client_fd = fd;
		}
		planes[p].m.userptr = (unsigned long)fd;
		planes[p].length = length;
		planes[p].reserved[0] = (uint32_t)fd;
	}
	if (client != NULL) {
		b.flags = client->flags & ~(uint32_t)(V4L2_BUF_FLAG_MAPPED | V4L2_BUF_FLAG_QUEUED | V4L2_BUF_FLAG_DONE);
		b.timestamp = client->timestamp;
		if (q->type == V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE && client->length >= 1 && client->m.planes != NULL) {
			planes[0].bytesused = client->m.planes[0].bytesused;
			planes[0].data_offset = client->m.planes[0].data_offset;
			planes[0].reserved[1] = client->m.planes[0].data_offset;
		}
	}
	return drv(d, VIDIOC_QBUF, &b);
}

//! Dequeues a buffer of the queue as the driver returns it.
static int
driver_dqbuf(struct vidc_device *d, struct vidc_queue *q, struct v4l2_buffer *b, struct v4l2_plane *planes)
{
	memset(planes, 0, sizeof(*planes) * MAX_PLANES);
	memset(b, 0, sizeof(*b));
	b->type = q->type;
	b->memory = V4L2_MEMORY_USERPTR;
	b->length = q->planes;
	b->m.planes = planes;
	return drv(d, VIDIOC_DQBUF, b);
}


static uint32_t
client_flags(uint32_t flags)
{
	uint32_t out = flags & ~VIDC_BUF_VENDOR;
	if (flags & VIDC_BUF_EOS) {
		out |= V4L2_BUF_FLAG_LAST;
	}
	if (flags & (VIDC_BUF_CORRUPT | VIDC_BUF_UNSUPPORTED)) {
		out |= V4L2_BUF_FLAG_ERROR;
	}
	return out;
}

/*!
 * Takes buffers back from the firmware (the driver's flush) and waits for the flush to finish:
 * the capture queue's alone, or all (the driver has no flush of the bitstream alone: it accepts
 * one and does nothing). Returned bitstream buffers are dropped; returned pictures too, for the
 * capture flush, and for the full flush queued again, the client's still. The number of
 * pictures that came back.
 */
static unsigned
flush(struct vidc_device *d, bool all)
{
	const char *name = all ? "full" : "capture";
	struct v4l2_decoder_cmd c = {.cmd = VIDC_CMD_FLUSH,
	                             .flags = all ? VIDC_FLUSH_OUTPUT | VIDC_FLUSH_CAPTURE : VIDC_FLUSH_CAPTURE};
	if (drv(d, VIDIOC_DECODER_CMD, &c) != 0) {
		say(d, "%s flush refused: %s", name, strerror(errno));
	}
	bool flushed = false, back[MAX_BUFFERS] = {false};
	unsigned returned = 0;
	struct timespec start, now;
	clock_gettime(CLOCK_MONOTONIC, &start);
	for (;;) {
		struct v4l2_buffer b;
		struct v4l2_plane planes[MAX_PLANES];
		while (all && driver_dqbuf(d, &d->out, &b, planes) == 0) {
		}
		while (driver_dqbuf(d, &d->cap, &b, planes) == 0) {
			returned++;
			if (b.index < MAX_BUFFERS) {
				back[b.index] = true;
			}
		}
		// Only pending events: on a blocking fd DQEVENT waits for the next one.
		struct v4l2_event e;
		while (!flushed && (d->ops.poll(d->ops.ctx, d->fd, POLLPRI, 0) & POLLPRI) && drv(d, VIDIOC_DQEVENT, &e) == 0) {
			if (e.type == VIDC_EVENT_FLUSH_DONE) {
				flushed = true;
			} else if (e.type == VIDC_EVENT_RECONFIG && !d->held_reconfig) {
				d->held = e; // the client's next DQEVENT gets it
				d->held_reconfig = true;
			} else {
				say(d, "event 0x%x during the %s flush, dropped", e.type, name);
			}
		}
		clock_gettime(CLOCK_MONOTONIC, &now);
		long waited = (now.tv_sec - start.tv_sec) * 1000 + (now.tv_nsec - start.tv_nsec) / 1000000;
		if (flushed || waited >= FLUSH_TIMEOUT_MS) {
			break;
		}
		d->ops.poll(d->ops.ctx, d->fd, POLLIN | POLLOUT | POLLPRI, 20);
	}
	if (!flushed) {
		say(d, "no %s flush-done event within %d ms", name, FLUSH_TIMEOUT_MS);
	}
	for (unsigned i = 0; all && d->cap.memory != 0 && i < d->cap.count; i++) {
		if (back[i]) {
			struct v4l2_plane p = {.m.fd = d->cap.buf[i].client_fd};
			struct v4l2_buffer again = {.length = 1, .m.planes = &p};
			if (driver_qbuf(d, &d->cap, i, &again) != 0) {
				say(d, "picture %u not queued again: %s", i, strerror(errno));
			}
		}
	}
	return returned;
}

/*!
 * Takes the capture queue's buffers back from the firmware and, for the placeholder, streams
 * it off and releases it.
 */
static void
flush_capture(struct vidc_device *d)
{
	unsigned returned = flush(d, false);
	if (d->placeholder) {
		uint32_t type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
		if (drv(d, VIDIOC_STREAMOFF, &type) != 0) {
			say(d, "placeholder stream-off: %s", strerror(errno));
		}
		driver_reqbufs(d, &d->cap, 0);
		free_buffers(d, &d->cap);
		d->placeholder = false;
		say(d, "placeholder released (%u buffers back)", returned);
	}
}

//! The capture placeholder, streamed with the bitstream so that the firmware reports the size.
static void
start_placeholder(struct vidc_device *d)
{
	if (alloc_buffers(d, &d->cap, PLACEHOLDER_BUFFERS, 0) != 0 || driver_reqbufs(d, &d->cap, PLACEHOLDER_BUFFERS) != 0) {
		say(d, "no placeholder buffers: %s", strerror(errno));
		free_buffers(d, &d->cap);
		return;
	}
	for (unsigned i = 0; i < PLACEHOLDER_BUFFERS; i++) {
		if (driver_qbuf(d, &d->cap, i, NULL) != 0) {
			say(d, "placeholder buffer %u not queued: %s", i, strerror(errno));
		}
	}
	uint32_t type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
	if (drv(d, VIDIOC_STREAMON, &type) != 0) {
		say(d, "placeholder stream-on: %s", strerror(errno));
		driver_reqbufs(d, &d->cap, 0);
		free_buffers(d, &d->cap);
		return;
	}
	d->placeholder = true;
}

static int
do_fmt(struct vidc_device *d, unsigned long request, struct v4l2_format *f)
{
	if (f->type == V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE) {
		int ret = drv(d, request, f);
		if (ret == 0 && request == VIDIOC_S_FMT && d->cap.memory == 0) {
			// The capture format before any REQBUFS, as the driver wants: the placeholder's.
			struct v4l2_format c = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE};
			c.fmt.pix_mp.width = PLACEHOLDER_SIZE;
			c.fmt.pix_mp.height = PLACEHOLDER_SIZE;
			c.fmt.pix_mp.pixelformat = V4L2_PIX_FMT_NV12;
			c.fmt.pix_mp.field = V4L2_FIELD_NONE;
			if (drv(d, VIDIOC_S_FMT, &c) != 0) {
				say(d, "placeholder capture format: %s", strerror(errno));
			}
		}
		return ret;
	}
	if (f->type != V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE) {
		return drv(d, request, f);
	}
	struct v4l2_pix_format_mplane *p = &f->fmt.pix_mp;
	if (request != VIDIOC_G_FMT && p->pixelformat == V4L2_PIX_FMT_QC08C) {
		p->pixelformat = VIDC_PIX_FMT_NV12_UBWC;
	}
	int ret = drv(d, request, f);
	if (p->pixelformat == VIDC_PIX_FMT_NV12_UBWC) {
		p->pixelformat = V4L2_PIX_FMT_QC08C;
	}
	if (ret != 0) {
		return ret;
	}
	uint32_t size = (p->plane_fmt[0].sizeimage + PAGE_BYTES - 1) / PAGE_BYTES * PAGE_BYTES;
	if (request == VIDIOC_S_FMT && size > d->picture_floor) {
		d->picture_floor = size;
	}
	if (p->pixelformat == V4L2_PIX_FMT_NV12) {
		// After a size change the driver's G_FMT keeps the old row length.
		p->plane_fmt[0].bytesperline = (p->width + VIDC_NV12_ALIGN - 1) / VIDC_NV12_ALIGN * VIDC_NV12_ALIGN;
		if (p->height % VIDC_NV12_ALIGN != 0 && p->height != PLACEHOLDER_SIZE && !d->warned_layout) {
			d->warned_layout = true;
			say(d, "NV12 %ux%u: chroma at row %u, not %u", p->width, p->height,
			    (p->height + VIDC_NV12_ALIGN - 1) / VIDC_NV12_ALIGN * VIDC_NV12_ALIGN, p->height);
		}
	}
	for (unsigned i = 1; i < VIDEO_MAX_PLANES; i++) {
		memset(&p->plane_fmt[i], 0, sizeof(p->plane_fmt[i]));
	}
	p->num_planes = 1;
	return 0;
}

//! The driver's stream-off of the bitstream (its session ends), with the placeholder's release.
static int
stop_bitstream(struct vidc_device *d)
{
	uint32_t type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
	int ret = drv(d, VIDIOC_STREAMOFF, &type);
	int e = errno;
	d->out_flushed = false;
	if (d->placeholder) {
		type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
		drv(d, VIDIOC_STREAMOFF, &type);
		driver_reqbufs(d, &d->cap, 0);
		free_buffers(d, &d->cap);
		d->placeholder = false;
	}
	errno = e;
	return ret;
}

static int
do_enum_fmt(struct vidc_device *d, struct v4l2_fmtdesc *f)
{
	int ret = drv(d, VIDIOC_ENUM_FMT, f);
	if (ret == 0 && f->pixelformat == VIDC_PIX_FMT_NV12_UBWC) {
		f->pixelformat = V4L2_PIX_FMT_QC08C;
	}
	return ret;
}

static int
do_reqbufs(struct vidc_device *d, struct v4l2_requestbuffers *r)
{
	struct vidc_queue *q = queue_of(d, r->type);
	if (q == NULL || (r->memory != V4L2_MEMORY_MMAP && r->memory != V4L2_MEMORY_DMABUF)) {
		return drv(d, VIDIOC_REQBUFS, r);
	}
	if (q == &d->cap && d->placeholder) {
		flush_capture(d);
	}
	if (q == &d->out && d->out_flushed && stop_bitstream(d) != 0) {
		say(d, "bitstream stream-off: %s", strerror(errno));
	}
	const uint32_t caps = V4L2_BUF_CAP_SUPPORTS_MMAP | V4L2_BUF_CAP_SUPPORTS_DMABUF;
	if (r->count == 0) {
		if (driver_reqbufs(d, q, 0) != 0) {
			return -1;
		}
		free_buffers(d, q);
		q->memory = 0;
		r->capabilities = caps;
		return 0;
	}
	unsigned count = r->count < MAX_BUFFERS ? r->count : MAX_BUFFERS;
	bool dmabuf = r->memory == V4L2_MEMORY_DMABUF;
	if (alloc_buffers(d, q, count, dmabuf ? 1 : 0) != 0) {
		return -1;
	}
	if (driver_reqbufs(d, q, count) != 0) {
		int e = errno;
		free_buffers(d, q);
		errno = e;
		return -1;
	}
	q->memory = r->memory;
	q->sequence = 0;
	r->count = count;
	r->capabilities = caps;
	say(d, "%s: %u %s buffers of %u bytes", q == &d->cap ? "capture" : "bitstream", count,
	    dmabuf ? "client dma-buf" : "mapped", q->buf[0].length[0]);
	return 0;
}

static int
do_querybuf(struct vidc_device *d, struct v4l2_buffer *b)
{
	struct vidc_queue *q = queue_of(d, b->type);
	if (q == NULL || q->memory == 0) {
		return drv(d, VIDIOC_QUERYBUF, b);
	}
	if (b->index >= q->count || b->length < 1 || b->m.planes == NULL) {
		errno = EINVAL;
		return -1;
	}
	struct v4l2_plane *plane = &b->m.planes[0];
	memset(plane, 0, sizeof(*plane));
	plane->length = q->buf[b->index].length[0];
	if (q->memory == V4L2_MEMORY_MMAP) {
		plane->m.mem_offset = (uint32_t)offset_of(q, b->index, 0);
	} else {
		plane->m.fd = q->buf[b->index].client_fd;
	}
	b->memory = q->memory;
	b->length = 1;
	b->flags = (q->memory == V4L2_MEMORY_MMAP ? V4L2_BUF_FLAG_MAPPED : 0) | V4L2_BUF_FLAG_TIMESTAMP_COPY;
	b->field = V4L2_FIELD_NONE;
	return 0;
}

static int
do_qbuf(struct vidc_device *d, struct v4l2_buffer *b)
{
	struct vidc_queue *q = queue_of(d, b->type);
	if (q == NULL || q->memory == 0) {
		return drv(d, VIDIOC_QBUF, b);
	}
	if (b->index >= q->count || b->memory != q->memory ||
	    (q->memory == V4L2_MEMORY_DMABUF && (b->length < 1 || b->m.planes == NULL))) {
		errno = EINVAL;
		return -1;
	}
	if (driver_qbuf(d, q, b->index, b) != 0) {
		return -1;
	}
	b->flags |= V4L2_BUF_FLAG_QUEUED;
	return 0;
}

static int
do_dqbuf(struct vidc_device *d, struct v4l2_buffer *b)
{
	struct vidc_queue *q = queue_of(d, b->type);
	if (q == NULL || q->memory == 0) {
		if (q == &d->cap && d->placeholder) {
			errno = EAGAIN; // the placeholder's buffers are not the client's
			return -1;
		}
		return drv(d, VIDIOC_DQBUF, b);
	}
	struct v4l2_buffer got;
	struct v4l2_plane planes[MAX_PLANES];
	if (driver_dqbuf(d, q, &got, planes) != 0) {
		return -1;
	}
	struct v4l2_plane *client_planes = b->m.planes;
	uint32_t client_length = b->length;
	b->index = got.index;
	b->flags = client_flags(got.flags);
	b->field = V4L2_FIELD_NONE;
	b->timestamp = got.timestamp;
	b->timecode = got.timecode;
	b->sequence = q->sequence++;
	b->memory = q->memory;
	b->bytesused = 0;
	b->m.planes = client_planes;
	b->length = 1;
	if (client_length >= 1 && client_planes != NULL) {
		memset(&client_planes[0], 0, sizeof(client_planes[0]));
		client_planes[0].bytesused = planes[0].bytesused;
		client_planes[0].length = q->buf[got.index].length[0];
		if (q->memory == V4L2_MEMORY_MMAP) {
			client_planes[0].m.mem_offset = (uint32_t)offset_of(q, got.index, 0);
		} else {
			client_planes[0].m.fd = q->buf[got.index].client_fd;
		}
		if (q == &d->out) {
			client_planes[0].bytesused = 0;
		}
	}
	return 0;
}

static int
do_expbuf(struct vidc_device *d, struct v4l2_exportbuffer *x)
{
	struct vidc_queue *q = queue_of(d, x->type);
	if (q == NULL || q->memory != V4L2_MEMORY_MMAP) {
		return drv(d, VIDIOC_EXPBUF, x);
	}
	if (x->index >= q->count || x->plane != 0) {
		errno = EINVAL;
		return -1;
	}
	int fd = fcntl(q->buf[x->index].fd[0], (x->flags & O_CLOEXEC) ? F_DUPFD_CLOEXEC : F_DUPFD, 0);
	if (fd < 0) {
		return -1;
	}
	x->fd = fd;
	return 0;
}

static int
do_stream(struct vidc_device *d, unsigned long request, uint32_t *type)
{
	struct vidc_queue *q = queue_of(d, *type);
	if (q == NULL) {
		return drv(d, request, type);
	}
	if (request == VIDIOC_STREAMON) {
		if (q == &d->out && d->out_flushed) {
			d->out_flushed = false; // still streaming in the driver
			q->streaming = true;
			return 0;
		}
		if (q == &d->out && d->cap.memory == 0 && !d->placeholder) {
			start_placeholder(d);
		}
		if (drv(d, request, type) != 0) {
			return -1;
		}
		q->streaming = true;
		return 0;
	}
	if (q == &d->cap && !d->cap.streaming) {
		return 0; // never streamed by the client: the placeholder is gone already
	}
	if (q == &d->out && q->streaming && d->cap.memory != 0) {
		// The mainline stream-off drops the pending bitstream and keeps the decoder. The driver's
		// ends its session, which a stream-on before the capture's cannot start again: so both
		// ports are flushed instead, the pictures queued again, and the bitstream keeps
		// streaming in the driver.
		flush(d, true);
		q->streaming = false;
		d->out_flushed = true;
		return 0;
	}
	if (q == &d->out) {
		if (stop_bitstream(d) != 0) {
			return -1;
		}
		q->streaming = false;
		return 0;
	}
	if (drv(d, request, type) != 0) {
		return -1;
	}
	q->streaming = false;
	return 0;
}

//! A pending event for the client, the driver's own handled on the way: 0, or -1 with ENOENT.
static int
do_dqevent(struct vidc_device *d, struct v4l2_event *e)
{
	// Only pending events: on a blocking fd DQEVENT waits, holding the lock every other call needs.
	while (d->held_reconfig || (d->ops.poll(d->ops.ctx, d->fd, POLLPRI, 0) & POLLPRI)) {
		if (d->held_reconfig) {
			*e = d->held;
			d->held_reconfig = false;
		} else if (drv(d, VIDIOC_DQEVENT, e) != 0) {
			return -1;
		}
		switch (e->type) {
		case VIDC_EVENT_RECONFIG: {
			uint32_t data[2];
			memcpy(data, e->u.data, sizeof(data));
			say(d, "picture size %ux%u", data[1], data[0]);
			flush_capture(d);
			memset(&e->u, 0, sizeof(e->u));
			e->type = V4L2_EVENT_SOURCE_CHANGE;
			e->u.src_change.changes = V4L2_EVENT_SRC_CH_RESOLUTION;
			e->id = 0;
			return 0;
		}
		case VIDC_EVENT_SYS_ERROR:
			say(d, "the video firmware reported an error");
			continue;
		case VIDC_EVENT_UNSUPPORTED:
			say(d, "the video firmware cannot decode this stream");
			continue;
		case VIDC_EVENT_FLUSH_DONE:
			continue;
		default:
			if (e->type >= 0x08000000u) {
				continue; // the driver's other vendor events
			}
			return 0;
		}
	}
	errno = ENOENT;
	return -1;
}

static int
do_s_ctrl(struct vidc_device *d, struct v4l2_control *c)
{
	if (c->id == V4L2_CID_MPEG_VIDEO_DEC_DISPLAY_DELAY_ENABLE) {
		d->delay_enable = c->value;
		return 0;
	}
	if (c->id == V4L2_CID_MPEG_VIDEO_DEC_DISPLAY_DELAY) {
		if (d->delay_enable && c->value == 0) {
			struct v4l2_control order = {.id = VIDC_CID_DECODE_ORDER, .value = 1};
			struct v4l2_control latency = {.id = VIDC_CID_LOW_LATENCY, .value = 1};
			if (drv(d, VIDIOC_S_CTRL, &order) != 0 || drv(d, VIDIOC_S_CTRL, &latency) != 0) {
				say(d, "decode order / low latency: %s", strerror(errno));
			}
		}
		return 0;
	}
	return drv(d, VIDIOC_S_CTRL, c);
}

static int
do_decoder_cmd(struct vidc_device *d, unsigned long request, struct v4l2_decoder_cmd *c)
{
	if (c->cmd == V4L2_DEC_CMD_START) {
		c->flags = 0;
		return 0; // the driver has no START: decoding resumes by itself
	}
	return drv(d, request, c);
}

//! One line per translated ioctl: what the client asked and what it got (ops.trace).
static void
trace(struct vidc_device *d, unsigned long request, void *arg, int ret, int err)
{
	const char *result = ret == 0 ? "ok" : strerror(err);
	switch (request) {
	case VIDIOC_QBUF:
	case VIDIOC_DQBUF: {
		const struct v4l2_buffer *b = arg;
		uint32_t used = b->length >= 1 && b->m.planes != NULL ? b->m.planes[0].bytesused : 0;
		say(d, "%s %s #%u used %u flags 0x%x ts %ld.%06ld: %s", request == VIDIOC_QBUF ? "QBUF" : "DQBUF",
		    b->type == V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE ? "cap" : "out", b->index, used, b->flags,
		    (long)b->timestamp.tv_sec, (long)b->timestamp.tv_usec, result);
		break;
	}
	case VIDIOC_DQEVENT: say(d, "DQEVENT type 0x%x: %s", ((const struct v4l2_event *)arg)->type, result); break;
	case VIDIOC_STREAMON:
	case VIDIOC_STREAMOFF:
		say(d, "%s %u: %s", request == VIDIOC_STREAMON ? "STREAMON" : "STREAMOFF", *(const uint32_t *)arg, result);
		break;
	case VIDIOC_S_FMT:
	case VIDIOC_G_FMT: {
		const struct v4l2_format *f = arg;
		say(d, "%s type %u %ux%u %.4s planes %u size %u: %s", request == VIDIOC_S_FMT ? "S_FMT" : "G_FMT", f->type,
		    f->fmt.pix_mp.width, f->fmt.pix_mp.height, (const char *)&f->fmt.pix_mp.pixelformat,
		    f->fmt.pix_mp.num_planes, f->fmt.pix_mp.plane_fmt[0].sizeimage, result);
		break;
	}
	case VIDIOC_REQBUFS: {
		const struct v4l2_requestbuffers *r = arg;
		say(d, "REQBUFS type %u memory %u count %u: %s", r->type, r->memory, r->count, result);
		break;
	}
	default: say(d, "ioctl 0x%lx: %s", request, result); break;
	}
}

/*!
 * The client's DQEVENT. On a blocking fd it waits as the driver would, but outside the lock:
 * the client's other threads queue and dequeue buffers meanwhile.
 */
static int
client_dqevent(struct vidc_device *d, struct v4l2_event *e)
{
	bool wait = d->ops.blocking(d->ops.ctx, d->fd);
	for (;;) {
		pthread_mutex_lock(&d->lock);
		int ret = do_dqevent(d, e);
		int err = errno;
		if (d->ops.trace && (ret == 0 || !wait)) {
			trace(d, VIDIOC_DQEVENT, e, ret, err);
		}
		pthread_mutex_unlock(&d->lock);
		if (ret == 0 || !wait || err != ENOENT) {
			errno = err;
			return ret;
		}
		int revents = d->ops.poll(d->ops.ctx, d->fd, POLLPRI, EVENT_WAIT_MS);
		if (revents != 0 && !(revents & POLLPRI)) {
			// An error or hang-up without an event: the driver's DQEVENT would sleep, so does this.
			struct timespec pause = {.tv_nsec = EVENT_WAIT_MS * 1000000L};
			nanosleep(&pause, NULL);
		}
	}
}

int
vidc_attach(const struct vidc_ops *ops, int fd)
{
	pthread_mutex_lock(&g_table);
	struct vidc_device *d = find(fd);
	if (d == NULL) {
		for (int i = 0; i < MAX_DEVICES && d == NULL; i++) {
			if (!g_dev[i].used) {
				d = &g_dev[i];
			}
		}
		if (d == NULL) {
			pthread_mutex_unlock(&g_table);
			return -1;
		}
		memset(d, 0, sizeof(*d));
		d->used = true;
		d->fd = fd;
		d->ops = *ops;
		pthread_mutex_init(&d->lock, NULL);
		d->out.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
		d->cap.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
		// The driver's own events, whatever the client subscribes.
		const uint32_t events[] = {VIDC_EVENT_RECONFIG, VIDC_EVENT_FLUSH_DONE, VIDC_EVENT_SYS_ERROR,
		                           VIDC_EVENT_UNSUPPORTED};
		for (size_t i = 0; i < sizeof(events) / sizeof(events[0]); i++) {
			struct v4l2_event_subscription s = {.type = events[i]};
			if (drv(d, VIDIOC_SUBSCRIBE_EVENT, &s) != 0) {
				say(d, "event 0x%x not subscribed: %s", events[i], strerror(errno));
			}
		}
		say(d, "translating the msm_vidc decoder on fd %d", fd);
	}
	pthread_mutex_unlock(&g_table);
	return 0;
}

int
vidc_attached(int fd)
{
	pthread_mutex_lock(&g_table);
	int found = find(fd) != NULL;
	pthread_mutex_unlock(&g_table);
	return found;
}

void
vidc_detach(int fd)
{
	pthread_mutex_lock(&g_table);
	struct vidc_device *d = find(fd);
	if (d != NULL) {
		free_buffers(d, &d->out);
		free_buffers(d, &d->cap);
		pthread_mutex_destroy(&d->lock);
		d->used = false;
	}
	pthread_mutex_unlock(&g_table);
}

int
vidc_ioctl(int fd, unsigned long request, void *arg)
{
	pthread_mutex_lock(&g_table);
	struct vidc_device *d = find(fd);
	pthread_mutex_unlock(&g_table);
	if (d == NULL) {
		errno = EBADF;
		return -1;
	}
	if (request == VIDIOC_DQEVENT) {
		return client_dqevent(d, arg);
	}
	pthread_mutex_lock(&d->lock);
	int ret;
	switch (request) {
	case VIDIOC_G_FMT:
	case VIDIOC_S_FMT:
	case VIDIOC_TRY_FMT: ret = do_fmt(d, request, arg); break;
	case VIDIOC_REQBUFS: ret = do_reqbufs(d, arg); break;
	case VIDIOC_QUERYBUF: ret = do_querybuf(d, arg); break;
	case VIDIOC_QBUF: ret = do_qbuf(d, arg); break;
	case VIDIOC_DQBUF: ret = do_dqbuf(d, arg); break;
	case VIDIOC_EXPBUF: ret = do_expbuf(d, arg); break;
	case VIDIOC_STREAMON:
	case VIDIOC_STREAMOFF: ret = do_stream(d, request, arg); break;
	case VIDIOC_ENUM_FMT: ret = do_enum_fmt(d, arg); break;
	case VIDIOC_S_CTRL: ret = do_s_ctrl(d, arg); break;
	case VIDIOC_DECODER_CMD:
	case VIDIOC_TRY_DECODER_CMD: ret = do_decoder_cmd(d, request, arg); break;
	default: ret = drv(d, request, arg); break;
	}
	int e = errno;
	if (d->ops.trace) {
		trace(d, request, arg, ret, e);
	}
	pthread_mutex_unlock(&d->lock);
	errno = e;
	return ret;
}

size_t
vidc_picture_floor(void)
{
	size_t floor = 0;
	pthread_mutex_lock(&g_table);
	for (int i = 0; i < MAX_DEVICES; i++) {
		if (g_dev[i].used && g_dev[i].picture_floor > floor) {
			floor = g_dev[i].picture_floor;
		}
	}
	pthread_mutex_unlock(&g_table);
	return floor;
}

int
vidc_mmap_target(int fd, off_t offset, int *out_fd, off_t *out_offset)
{
	pthread_mutex_lock(&g_table);
	struct vidc_device *d = find(fd);
	pthread_mutex_unlock(&g_table);
	if (d == NULL) {
		errno = EBADF;
		return -1;
	}
	pthread_mutex_lock(&d->lock);
	int ret = -1;
	struct vidc_queue *queues[2] = {&d->out, &d->cap};
	for (int k = 0; k < 2 && ret != 0; k++) {
		struct vidc_queue *q = queues[k];
		for (unsigned i = 0; q->memory == V4L2_MEMORY_MMAP && i < q->count && ret != 0; i++) {
			if (offset_of(q, i, 0) == offset) {
				*out_fd = q->buf[i].fd[0];
				*out_offset = 0;
				ret = 0;
			}
		}
	}
	pthread_mutex_unlock(&d->lock);
	if (ret != 0) {
		errno = EINVAL;
	}
	return ret;
}
