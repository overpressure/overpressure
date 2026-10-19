// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Tracking-camera frame sync through the controller radio (see pico4_station.h).
 * @ingroup drv_pico4
 */

#ifndef _GNU_SOURCE // Monado defines it on the command line
#define _GNU_SOURCE // usleep, O_CLOEXEC and clock_gettime under -std=c11
#endif

#include "pico4_station.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

#define HEADER_SEND 0xAA
#define HEADER_REPLY 0xBB
#define HEADER_NOTIFY 0xA5
#define TARGET_STATION 0x01
#define NOTIFY_STREAM 0x01   //!< A5 01: streamed data, not acknowledged.
#define NOTIFY_ANNOUNCE 0x06 //!< A5 06: the station announces itself after power-on.
#define IOCTL_POWER 0x80046B09 //!< _IOW('k', 9, int): 1 on, 0 off.

#define CAMERA_INIT 0x01
#define CAMERA_STREAM 0x02
#define CAMERA_SET_PARA 0x03 //!< Exposure and gain; 28-byte payload.
#define CAMERA_SET_PARA_LEN 28
#define CAMERA_ON 0x01
#define CAMERA_OFF 0x02
#define CAMERA_ALL 0x00
#define FPS_50 0x01        //!< The stock setting (sys_station_camera_fps 50).
#define DEVICE_TYPE 0x00   //!< DeviceTypeGet() on the PICO 4.
#define EXPOSURE_FIRST_ROW 0x02 //!< As PICO OS initialises the cameras.

struct pico4_station
{
	int fd;
	//! Commands and polling may come from different threads; a command keeps
	//! the lock until its reply arrived, so a poller cannot take the reply.
	pthread_mutex_t lock;
	bool announced;
	//! The last reply to a camera command: op, status.
	int camera_op, camera_status;
};

uint8_t
pico4_station_crc8(const uint8_t *data, size_t size)
{
	uint8_t c = 0;
	for (size_t i = 0; i < size; i++) {
		c ^= data[i];
		for (int b = 0; b < 8; b++) {
			c = (c & 1) ? (uint8_t)((c >> 1) ^ 0x8C) : (uint8_t)(c >> 1);
		}
	}
	return c;
}

static bool
allowed(uint8_t cmd, const uint8_t *p, size_t n)
{
	switch (cmd) {
	case PICO4_STATION_CMD_ACK: return n == 2;
	case PICO4_STATION_CMD_CAMERA_FPS: return n == 3 && (p[0] == 1 || p[0] == 2) && p[2] == 1;
	case PICO4_STATION_CMD_CAMERA:
		if (n == 4 && p[0] == CAMERA_INIT) {
			return p[2] <= 2 && p[3] <= 1;
		}
		if (n == CAMERA_SET_PARA_LEN && p[0] == CAMERA_SET_PARA) {
			return true;
		}
		return n == 3 && p[0] == CAMERA_STREAM && p[1] <= 4 && (p[2] == CAMERA_ON || p[2] == CAMERA_OFF);
	default: return false;
	}
}

bool
pico4_station_frame(uint8_t out[PICO4_STATION_FRAME], uint8_t cmd, const uint8_t *payload, size_t size)
{
	if (!allowed(cmd, payload, size)) {
		return false;
	}
	memset(out, 0, PICO4_STATION_FRAME);
	out[0] = HEADER_SEND;
	out[1] = TARGET_STATION;
	out[2] = cmd;
	memcpy(out + 3, payload, size);
	out[39] = pico4_station_crc8(out, 39);
	return true;
}

bool
pico4_station_valid(const uint8_t f[PICO4_STATION_FRAME])
{
	return (f[0] == HEADER_REPLY || f[0] == HEADER_NOTIFY) && pico4_station_crc8(f, 39) == f[39];
}

static int64_t
now_ms(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int
send_cmd(struct pico4_station *st, uint8_t cmd, const uint8_t *payload, size_t size)
{
	uint8_t f[PICO4_STATION_FRAME];
	if (!pico4_station_frame(f, cmd, payload, size)) {
		return -EINVAL;
	}
	ssize_t w = write(st->fd, f, sizeof(f));
	if (w < 0) {
		return -errno;
	}
	return w == (ssize_t)sizeof(f) ? 0 : -EIO;
}

//! One frame within @p timeout_ms: 1 handled, 0 none, negative errno.
static int
read_one(struct pico4_station *st, int timeout_ms)
{
	uint8_t f[PICO4_STATION_FRAME];
	fd_set set;
	struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
	int r;

	FD_ZERO(&set);
	FD_SET(st->fd, &set);
	r = select(st->fd + 1, &set, NULL, NULL, &tv);
	if (r < 0) {
		return errno == EINTR ? 0 : -errno;
	}
	if (r == 0) {
		return 0;
	}
	if (read(st->fd, f, sizeof(f)) != (ssize_t)sizeof(f) || !pico4_station_valid(f)) {
		return 0;
	}
	if (!(f[0] == HEADER_NOTIFY && f[1] == NOTIFY_STREAM)) {
		send_cmd(st, PICO4_STATION_CMD_ACK, f + 37, 2);
	}
	if (f[0] == HEADER_NOTIFY && f[1] == NOTIFY_ANNOUNCE && !st->announced) {
		// As the stock service: the camera frame rate, after which camera commands work.
		const uint8_t fps[3] = {FPS_50, DEVICE_TYPE, 1};
		st->announced = true;
		send_cmd(st, PICO4_STATION_CMD_CAMERA_FPS, fps, sizeof(fps));
	}
	if (f[0] == HEADER_REPLY && f[2] == PICO4_STATION_CMD_CAMERA) {
		st->camera_op = f[3];
		st->camera_status = f[4];
	}
	return 1;
}

int
pico4_station_open(struct pico4_station **out, const char *device)
{
	struct pico4_station *st = calloc(1, sizeof(*st));
	if (st == NULL) {
		return -ENOMEM;
	}
	st->fd = open(device != NULL ? device : PICO4_STATION_DEVICE, O_RDWR | O_CLOEXEC);
	if (st->fd < 0) {
		int r = -errno;
		free(st);
		return r;
	}
	pthread_mutex_init(&st->lock, NULL);
	*out = st;
	return 0;
}

void
pico4_station_close(struct pico4_station *st)
{
	if (st != NULL) {
		close(st->fd);
		pthread_mutex_destroy(&st->lock);
		free(st);
	}
}

static int
power(struct pico4_station *st, int on)
{
	return ioctl(st->fd, IOCTL_POWER, &on) < 0 ? -errno : 0;
}

static int
reset_locked(struct pico4_station *st)
{
	int r;
	if ((r = power(st, 0)) < 0) {
		return r;
	}
	usleep(100 * 1000);
	st->announced = false;
	if ((r = power(st, 1)) < 0) {
		return r;
	}
	for (int64_t end = now_ms() + 3000; !st->announced && now_ms() < end;) {
		if ((r = read_one(st, 100)) < 0) {
			return r;
		}
	}
	if (!st->announced) {
		return -ETIMEDOUT;
	}
	// Take the replies to the frame-rate answer.
	for (int64_t end = now_ms() + 200; now_ms() < end;) {
		if ((r = read_one(st, 50)) < 0) {
			return r;
		}
	}
	return 0;
}

int
pico4_station_reset(struct pico4_station *st)
{
	pthread_mutex_lock(&st->lock);
	int r = reset_locked(st);
	pthread_mutex_unlock(&st->lock);
	return r;
}

//! Wait up to @p timeout_ms for the station to have a frame; 1, 0 or a negative errno.
static int
wait_readable(struct pico4_station *st, int timeout_ms)
{
	fd_set set;
	struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
	FD_ZERO(&set);
	FD_SET(st->fd, &set);
	int r = select(st->fd + 1, &set, NULL, NULL, &tv);
	return r < 0 ? (errno == EINTR ? 0 : -errno) : r > 0;
}

int
pico4_station_poll(struct pico4_station *st, int timeout_ms)
{
	int64_t end = now_ms() + timeout_ms;
	int n = 0;
	do {
		int64_t left = end - now_ms();
		// Wait without the lock: a command in another thread must be able to take
		// it (and with it the reply it waits for) at any time.
		int r = wait_readable(st, left > 0 ? (int)left : 0);
		if (r > 0) {
			pthread_mutex_lock(&st->lock);
			r = read_one(st, 0);
			pthread_mutex_unlock(&st->lock);
		}
		if (r < 0) {
			return r;
		}
		n += r;
	} while (now_ms() < end);
	return n;
}

//! Send a camera command and wait for its reply; the status byte is 1 on success.
static int
camera_cmd(struct pico4_station *st, const uint8_t *payload, size_t size, int timeout_ms)
{
	int r;
	st->camera_op = -1;
	if ((r = send_cmd(st, PICO4_STATION_CMD_CAMERA, payload, size)) < 0) {
		return r;
	}
	for (int64_t end = now_ms() + timeout_ms; st->camera_op != payload[0] && now_ms() < end;) {
		if ((r = read_one(st, 50)) < 0) {
			return r;
		}
	}
	if (st->camera_op != payload[0]) {
		return -ETIMEDOUT;
	}
	return st->camera_status == 1 ? 0 : -EIO;
}

int
pico4_station_camera_start(struct pico4_station *st)
{
	const uint8_t init[4] = {CAMERA_INIT, 0, EXPOSURE_FIRST_ROW, 0};
	const uint8_t on[3] = {CAMERA_STREAM, CAMERA_ALL, CAMERA_ON};
	pthread_mutex_lock(&st->lock);
	int r = camera_cmd(st, init, sizeof(init), 2000);
	if (r == 0) {
		r = camera_cmd(st, on, sizeof(on), 1000);
	}
	pthread_mutex_unlock(&st->lock);
	return r;
}

int
pico4_station_camera_set_exposure(struct pico4_station *st, unsigned exposure_lines, unsigned gain)
{
	// As the stock CameraSetPara: 8 x (u16 exposure, u8 gain), cameras 0-3 in
	// bytes 1-12 and 16-27 (the stock service fills both sets), u16 and event
	// index at 13-15 left 0. The station answers nothing.
	uint8_t p[CAMERA_SET_PARA_LEN] = {CAMERA_SET_PARA};
	for (int i = 0; i < 8; i++) {
		uint8_t *e = p + 1 + 3 * i + (i >= 4 ? 3 : 0);
		e[0] = (uint8_t)(exposure_lines & 0xff);
		e[1] = (uint8_t)(exposure_lines >> 8);
		e[2] = (uint8_t)gain;
	}
	pthread_mutex_lock(&st->lock);
	int r = send_cmd(st, PICO4_STATION_CMD_CAMERA, p, sizeof(p));
	pthread_mutex_unlock(&st->lock);
	return r;
}

int
pico4_station_camera_stop(struct pico4_station *st)
{
	const uint8_t off[3] = {CAMERA_STREAM, CAMERA_ALL, CAMERA_OFF};
	pthread_mutex_lock(&st->lock);
	int r = camera_cmd(st, off, sizeof(off), 1000);
	pthread_mutex_unlock(&st->lock);
	return r;
}
