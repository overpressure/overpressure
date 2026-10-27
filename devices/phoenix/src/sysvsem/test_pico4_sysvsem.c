// SPDX-License-Identifier: BSL-1.0
/*
 * Tests pico4-sysvsem the way SteamVR uses System V semaphores: a named lock
 * created once, taken with SEM_UNDO, refused to a second taker with
 * IPC_NOWAIT, and released when its holder exits or is killed.
 *
 *   LD_PRELOAD=./libpico4-sysvsem.so ./test_pico4_sysvsem
 */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/sem.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int failures;

#define EXPECT(cond, ...)                                                                                        \
	do {                                                                                                     \
		if (!(cond)) {                                                                                   \
			fprintf(stderr, "FAIL line %d: ", __LINE__);                                             \
			fprintf(stderr, __VA_ARGS__);                                                            \
			fputc('\n', stderr);                                                                     \
			failures++;                                                                              \
		}                                                                                                \
	} while (0)

static int
lock(int id, short flags)
{
	struct sembuf op = {0, -1, (short)(SEM_UNDO | flags)};
	return semop(id, &op, 1);
}

static int
unlock(int id)
{
	struct sembuf op = {0, 1, SEM_UNDO};
	return semop(id, &op, 1);
}

int
main(void)
{
	key_t key = (key_t)(0x50340000 | (getpid() & 0xffff));

	int id = semget(key, 1, IPC_CREAT | IPC_EXCL | 0666);
	EXPECT(id >= 0, "create: %s", strerror(errno));
	EXPECT(semget(key, 1, IPC_CREAT | IPC_EXCL | 0666) < 0 && errno == EEXIST, "exclusive create twice");
	EXPECT(semget(key, 1, 0666) == id, "open existing");
	EXPECT(semctl(id, 0, SETVAL, 1) == 0, "SETVAL");
	EXPECT(semctl(id, 0, GETVAL) == 1, "GETVAL");

	// A holder that stays: a second taker is refused (the "duplicate bootstrapper" check).
	int ready[2];
	EXPECT(pipe(ready) == 0, "pipe: %s", strerror(errno));
	pid_t holder = fork();
	if (holder == 0) {
		lock(id, 0);
		if (write(ready[1], "x", 1) != 1) {
			_exit(1);
		}
		pause();
		_exit(0);
	}
	char c;
	EXPECT(read(ready[0], &c, 1) == 1, "the holder took the lock");
	EXPECT(lock(id, IPC_NOWAIT) < 0 && errno == EAGAIN, "lock held elsewhere is refused");
	EXPECT(semctl(id, 0, GETPID) == holder, "GETPID names the holder");

	// Killed holder: its SEM_UNDO adjustment comes back.
	kill(holder, SIGKILL);
	waitpid(holder, NULL, 0);
	struct timespec start, now;
	clock_gettime(CLOCK_MONOTONIC, &start);
	EXPECT(lock(id, 0) == 0, "lock after the holder was killed");
	clock_gettime(CLOCK_MONOTONIC, &now);
	EXPECT(now.tv_sec - start.tv_sec < 2, "the dead holder was reaped promptly");
	EXPECT(semctl(id, 0, GETVAL) == 0, "held by us now");
	EXPECT(unlock(id) == 0 && semctl(id, 0, GETVAL) == 1, "unlock");

	// A holder that exits normally gives its adjustment back too.
	pid_t child = fork();
	if (child == 0) {
		_exit(lock(id, 0) == 0 ? 0 : 1);
	}
	int status;
	waitpid(child, &status, 0);
	EXPECT(WIFEXITED(status) && WEXITSTATUS(status) == 0, "child locked");
	EXPECT(semctl(id, 0, GETVAL) == 1, "exit released the lock (value %d)", semctl(id, 0, GETVAL));

	// A blocked taker wakes when the lock is released.
	EXPECT(lock(id, 0) == 0, "lock for the wake test");
	pid_t waiter = fork();
	if (waiter == 0) {
		_exit(lock(id, 0) == 0 ? 0 : 1);
	}
	usleep(100000);
	EXPECT(semctl(id, 0, GETNCNT) == 1, "one waiter (%d)", semctl(id, 0, GETNCNT));
	unlock(id);
	waitpid(waiter, &status, 0);
	EXPECT(WIFEXITED(status) && WEXITSTATUS(status) == 0, "waiter got the lock");

	EXPECT(semctl(id, 0, IPC_RMID) == 0, "remove");
	EXPECT(semget(key, 1, 0666) < 0 && errno == ENOENT, "removed set is gone");

	printf("%s\n", failures == 0 ? "PASS" : "FAIL");
	return failures == 0 ? 0 : 1;
}
