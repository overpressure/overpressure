// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Waiting for a KMS flip to retire, with a bound.
 *
 * A commit's retire fence signals once the flip is on screen and the image before
 * it is no longer scanned out. A fence that does not signal never counts as a
 * retired flip: the caller keeps the image until it does, and gives up on the
 * display once @p lost_ms have passed.
 *
 * @ingroup comp_main
 */

#pragma once

#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <time.h>

static inline int64_t
pico4_retire_now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/*!
 * Wait for @p fd (a sync file) to signal: 0 once it has (or reports an error,
 * which ends the fence too), -ETIMEDOUT after @p lost_ms. Each @p report_ms
 * without a signal adds one to @p timeouts.
 */
static inline int
pico4_retire_wait(int fd, int report_ms, int lost_ms, uint64_t *timeouts)
{
	int64_t start = pico4_retire_now_ms();
	int64_t report = start + report_ms;
	for (;;) {
		int64_t now = pico4_retire_now_ms();
		if (now >= start + lost_ms) {
			return -ETIMEDOUT;
		}
		int64_t until = report < start + lost_ms ? report : start + lost_ms;
		struct pollfd p = {.fd = fd, .events = POLLIN};
		int n = poll(&p, 1, until > now ? (int)(until - now) : 0);
		if (n > 0 && (p.revents & (POLLIN | POLLERR | POLLHUP | POLLNVAL)) != 0) {
			return 0;
		}
		if (n < 0 && errno != EINTR) {
			return -errno;
		}
		if (pico4_retire_now_ms() >= report) {
			(*timeouts)++;
			report += report_ms;
		}
	}
}
