// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-vidc-compat: the mainline stateful-decoder V4L2 API (MMAP buffers, QUERYBUF, EXPBUF,
 * V4L2_EVENT_SOURCE_CHANGE, DISPLAY_DELAY) on top of the stock kernel's downstream msm_vidc
 * decoder (USERPTR buffers carrying dma-buf fds, an extradata plane on capture, vendor events),
 * for SteamVR's Steam Link client (docs/session.md#steam-link).
 *
 * The translation works on a decoder fd through a small set of operations, so that the
 * preloaded hooks (pico4-vrlink-compat.c) pass the real ioctl/mmap and an ION allocator, and the
 * tests a fake driver.
 */
#pragma once

#include <stddef.h>
#include <sys/types.h>

struct vidc_ops {
	//! The driver's ioctl: 0 or -1 with errno, as ioctl(2).
	int (*ioctl)(void *ctx, int fd, unsigned long request, void *arg);
	//! A CPU-mappable dma-buf of at least len bytes: its fd, or -1 with errno.
	int (*alloc)(void *ctx, size_t len);
	//! Closes one of those buffers (not through a wrapped close: the caller holds its locks).
	int (*close)(void *ctx, int fd);
	//! Wait for the decoder fd: poll(2) on one descriptor; its revents, 0 on timeout.
	int (*poll)(void *ctx, int fd, short events, int timeout_ms);
	//! Whether the client's fd blocks (no O_NONBLOCK): its DQEVENT then waits for an event.
	int (*blocking)(void *ctx, int fd);
	//! Messages (one line, no newline); NULL for none.
	void (*log)(void *ctx, const char *line);
	//! Non-zero: one message per translated ioctl (diagnostics).
	int trace;
	void *ctx;
};

//! Starts translating for a decoder fd; 0, or -1 if the table is full.
int vidc_attach(const struct vidc_ops *ops, int fd);

//! Whether fd is a translated decoder.
int vidc_attached(int fd);

//! Stops translating for fd (on close): its buffers' fds are closed, mappings stay valid.
void vidc_detach(int fd);

/*!
 * One ioctl on a translated decoder: 0 or -1 with errno, as the driver would answer the
 * mainline API.
 */
int vidc_ioctl(int fd, unsigned long request, void *arg);

/*!
 * The dma-buf fd and offset that a mainline mmap of offset on the decoder fd means: 0 and
 * *out_fd, or -1 with errno for an offset the decoder never gave out.
 */
int vidc_mmap_target(int fd, off_t offset, int *out_fd, off_t *out_offset);

/*!
 * The smallest picture buffer the decoders take now, in bytes (0: no picture size yet): the
 * largest picture size their streams have had, which the firmware keeps as its minimum when
 * the size drops. The client's own picture memory must be that large.
 */
size_t vidc_picture_floor(void);
