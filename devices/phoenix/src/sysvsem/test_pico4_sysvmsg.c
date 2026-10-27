// SPDX-License-Identifier: BSL-1.0
/*
 * Tests pico4-sysvmsg the way gamescope and mangoapp use System V message
 * queues: a queue named by ftok, frame timings sent without waiting, a reader
 * in another process blocked in msgrcv; plus receiving by type, a full queue
 * and removal.
 *
 *   LD_PRELOAD=./libpico4-sysvsem.so ./test_pico4_sysvmsg
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/msg.h>
#include <sys/wait.h>
#include <unistd.h>

#define CHECK(cond)                                                                                                    \
	do {                                                                                                           \
		if (!(cond)) {                                                                                         \
			fprintf(stderr, "%s:%d: %s failed (errno %d)\n", __FILE__, __LINE__, #cond, errno);           \
			exit(1);                                                                                       \
		}                                                                                                      \
	} while (0)

struct frame
{
	long type;
	unsigned version;
	unsigned long long frametime_ns;
	char app[32];
};

static int
count(int id)
{
	struct msqid_ds ds;
	CHECK(msgctl(id, IPC_STAT, &ds) == 0);
	return (int)ds.msg_qnum;
}

int
main(void)
{
	// gamescope and mangoapp both name the queue with ftok("mangoapp", 65); without such a
	// file both get the same key (-1), which is still one queue for both.
	key_t key = ftok("mangoapp", 65);
	int id = msgget(key, 0666 | IPC_CREAT);
	CHECK(id >= 0);
	CHECK(msgget(key, 0666 | IPC_CREAT) == id);
	CHECK(msgget(key, 0666 | IPC_CREAT | IPC_EXCL) < 0 && errno == EEXIST);
	CHECK(msgget(0x50344d51, 0666) < 0 && errno == ENOENT);

	// A reader blocked in msgrcv gets the frames of another process in order.
	pid_t reader = fork();
	CHECK(reader >= 0);
	if (reader == 0) {
		for (unsigned i = 0; i < 3; i++) {
			struct frame f;
			ssize_t n = msgrcv(id, &f, sizeof(f) - sizeof(long), 1, 0);
			if (n != (ssize_t)(sizeof(f) - sizeof(long)) || f.frametime_ns != 11111111ull * (i + 1) ||
			    strcmp(f.app, "steamvr") != 0) {
				_exit(2);
			}
		}
		_exit(0);
	}
	usleep(100000);
	for (unsigned i = 0; i < 3; i++) {
		struct frame f = {.type = 1, .version = 1, .frametime_ns = 11111111ull * (i + 1)};
		strcpy(f.app, "steamvr");
		CHECK(msgsnd(id, &f, sizeof(f) - sizeof(long), IPC_NOWAIT) == 0);
	}
	int status;
	CHECK(waitpid(reader, &status, 0) == reader && WIFEXITED(status) && WEXITSTATUS(status) == 0);
	CHECK(count(id) == 0);

	// By type: the first of a type, the lowest type up to a bound, and anything but a type.
	struct
	{
		long type;
		char text[8];
	} m;
	for (long type = 3; type >= 1; type--) {
		m.type = type;
		snprintf(m.text, sizeof(m.text), "t%ld", type);
		CHECK(msgsnd(id, &m, sizeof(m.text), 0) == 0);
	}
	CHECK(msgrcv(id, &m, sizeof(m.text), 2, IPC_NOWAIT) == sizeof(m.text) && m.type == 2 && !strcmp(m.text, "t2"));
	CHECK(msgrcv(id, &m, sizeof(m.text), -3, IPC_NOWAIT) == sizeof(m.text) && m.type == 1);
	CHECK(msgrcv(id, &m, sizeof(m.text), 3, IPC_NOWAIT | MSG_EXCEPT) < 0 && errno == ENOMSG);
	CHECK(msgrcv(id, &m, 2, 0, IPC_NOWAIT) < 0 && errno == E2BIG);
	CHECK(msgrcv(id, &m, 2, 0, IPC_NOWAIT | MSG_NOERROR) == 2 && m.type == 3 && !strncmp(m.text, "t3", 2));
	CHECK(msgrcv(id, &m, sizeof(m.text), 0, IPC_NOWAIT) < 0 && errno == ENOMSG);

	// A full queue refuses with IPC_NOWAIT, as gamescope sends; taking one makes room.
	static struct
	{
		long type;
		char text[4096];
	} big = {.type = 1};
	int sent = 0;
	while (msgsnd(id, &big, sizeof(big.text), IPC_NOWAIT) == 0) {
		sent++;
	}
	CHECK(errno == EAGAIN && sent == 4); // MSGMNB: 16384 bytes of text
	CHECK(msgrcv(id, &big, sizeof(big.text), 0, IPC_NOWAIT) == sizeof(big.text));
	CHECK(msgsnd(id, &big, sizeof(big.text), IPC_NOWAIT) == 0);
	CHECK(msgsnd(id, &big, 8193, IPC_NOWAIT) < 0 && errno == EINVAL);

	// Removal wakes a blocked reader with EIDRM.
	int empty = msgget(IPC_PRIVATE, 0600);
	CHECK(empty >= 0 && empty != id);
	reader = fork();
	CHECK(reader >= 0);
	if (reader == 0) {
		_exit(msgrcv(empty, &m, sizeof(m.text), 0, 0) < 0 && errno == EIDRM ? 0 : 3);
	}
	usleep(100000);
	CHECK(msgctl(empty, IPC_RMID, NULL) == 0);
	CHECK(waitpid(reader, &status, 0) == reader && WIFEXITED(status) && WEXITSTATUS(status) == 0);
	CHECK(msgctl(id, IPC_RMID, NULL) == 0);
	CHECK(msgsnd(id, &m, 1, IPC_NOWAIT) < 0 && errno == EINVAL);
	puts("pico4-sysvmsg: ok");
	return 0;
}
