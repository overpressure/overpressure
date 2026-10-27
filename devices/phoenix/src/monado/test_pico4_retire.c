// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*
 * Unit test of the bounded retire wait (pico4_retire.h) with fake fences: a
 * pipe that never becomes readable stands in for a flip that never retires,
 * one that is written to for a flip that retires.
 */

#include "pico4_retire.h"

#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

static int failures;

#define EXPECT(cond)                                                                                                   \
	do {                                                                                                           \
		if (!(cond)) {                                                                                         \
			fprintf(stderr, "%s:%d: expected %s\n", __FILE__, __LINE__, #cond);                            \
			failures++;                                                                                    \
		}                                                                                                      \
	} while (0)

static void *
signal_later(void *arg)
{
	usleep(150 * 1000);
	(void)!write(*(int *)arg, "x", 1);
	return NULL;
}

int
main(void)
{
	int never[2], late[2], done[2];
	EXPECT(pipe(never) == 0 && pipe(late) == 0 && pipe(done) == 0);

	// A fence that never signals: a timeout per 100 ms, given up after 350 ms, never retired.
	uint64_t timeouts = 0;
	int64_t start = pico4_retire_now_ms();
	EXPECT(pico4_retire_wait(never[0], 100, 350, &timeouts) == -ETIMEDOUT);
	int64_t took = pico4_retire_now_ms() - start;
	EXPECT(took >= 350 && took < 600);
	EXPECT(timeouts == 3);

	// Signalled already: retired at once, nothing counted.
	timeouts = 0;
	EXPECT(write(done[1], "x", 1) == 1);
	EXPECT(pico4_retire_wait(done[0], 100, 350, &timeouts) == 0 && timeouts == 0);

	// Signalled after 150 ms: retired, one report period passed on the way.
	pthread_t thread;
	EXPECT(pthread_create(&thread, NULL, signal_later, &late[1]) == 0);
	EXPECT(pico4_retire_wait(late[0], 100, 1000, &timeouts) == 0 && timeouts == 1);
	pthread_join(thread, NULL);

	// A closed writer (POLLHUP) ends the wait as well: the fence is gone.
	timeouts = 0;
	close(never[1]);
	EXPECT(pico4_retire_wait(never[0], 100, 350, &timeouts) == 0);

	if (failures == 0) {
		printf("test_pico4_retire: all checks passed\n");
	}
	return failures == 0 ? 0 : 1;
}
