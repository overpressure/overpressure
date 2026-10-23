// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-sysvmsg: System V message queues for a kernel without CONFIG_SYSVIPC
 * (docs/session.md), part of the preloaded pico4-sysvsem library.
 *
 * gamescope hands MangoHud's mangoapp its frame timings through a message
 * queue (msgget, msgsnd with IPC_NOWAIT; mangoapp waits in msgrcv). The stock
 * PICO 4 kernel has no System V IPC, so msgget fails with ENOSYS. This file
 * implements msgget, msgsnd, msgrcv and msgctl on a table in shared memory,
 * /dev/shm/pico4-sysvmsg, with the kernel's default limits: 32 queues here,
 * messages of up to MSGMAX bytes, MSGMNB bytes per queue.
 *
 * - a robust, process-shared mutex guards the table;
 * - each queue keeps its messages in arrival order in a byte arena of its own,
 *   closed up when a message is taken from the middle (msgrcv by type);
 * - blocked senders and receivers sleep on a futex word that every change
 *   bumps.
 *
 * PICO4_SYSVMSG_TABLE names another table file (tests; ignored for privileged
 * programs).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/futex.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/msg.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define EXPORT __attribute__((visibility("default")))

#define TABLE_PATH "/dev/shm/pico4-sysvmsg"
#define TABLE_MAGIC 0x50344d51u /* "P4MQ" */
#define TABLE_VERSION 1
#define MAX_QUEUES 32
#define QUEUE_BYTES 16384 /* MSGMNB */
#define MESSAGE_MAX 8192  /* MSGMAX */
/* Text plus the headers of up to 1024 messages; a queue of more tiny messages fills earlier. */
#define ARENA_BYTES (2 * QUEUE_BYTES)

//! A message in a queue's arena: its header, then its text, padded to the header's alignment.
struct record
{
	long type;
	size_t size;
};

struct queue
{
	bool used, removed;
	key_t key;
	int id;
	uid_t uid, cuid;
	gid_t gid, cgid;
	mode_t mode;
	time_t stime, rtime, ctime;
	pid_t lspid, lrpid;
	size_t count;              //!< messages
	size_t text;               //!< their text's bytes, what MSGMNB limits
	size_t used_bytes;         //!< bytes of the arena in use, headers included
	size_t limit;              //!< msg_qbytes
	unsigned char arena[ARENA_BYTES];
};

struct table
{
	uint32_t magic, version;
	pthread_mutex_t lock;
	uint32_t seq; //!< futex word: bumped on every change, waiters sleep on it
	int next_gen;
	struct queue queues[MAX_QUEUES];
};

static struct table *g_table;
static pthread_mutex_t g_attach = PTHREAD_MUTEX_INITIALIZER;

static long
futex(uint32_t *word, int op, uint32_t value, const struct timespec *timeout)
{
	return syscall(SYS_futex, word, op, value, timeout, NULL, 0);
}

//! Maps the shared table, creating and initialising it the first time.
static struct table *
attach(void)
{
	pthread_mutex_lock(&g_attach);
	if (g_table != NULL) {
		pthread_mutex_unlock(&g_attach);
		return g_table;
	}
	const char *path = secure_getenv("PICO4_SYSVMSG_TABLE");
	path = path != NULL ? path : TABLE_PATH;
	// An existing table is opened without O_CREAT: in /dev/shm (sticky) fs.protected_regular
	// refuses O_CREAT on another user's file, even to root.
	int fd = open(path, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
	if (fd < 0 && errno == ENOENT) {
		fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0666);
	}
	if (fd < 0) {
		pthread_mutex_unlock(&g_attach);
		return NULL;
	}
	// One table for every user, as System V IPC is system-wide.
	fchmod(fd, 0666);
	flock(fd, LOCK_EX);
	struct stat st;
	bool ok = fstat(fd, &st) == 0 &&
	          (st.st_size >= (off_t)sizeof(struct table) || ftruncate(fd, sizeof(struct table)) == 0);
	void *map = ok ? mmap(NULL, sizeof(struct table), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0) : MAP_FAILED;
	if (map != MAP_FAILED) {
		struct table *t = map;
		if (t->magic != TABLE_MAGIC || t->version != TABLE_VERSION) {
			memset(t, 0, sizeof(*t));
			pthread_mutexattr_t attr;
			pthread_mutexattr_init(&attr);
			pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
			pthread_mutexattr_setrobust(&attr, PTHREAD_MUTEX_ROBUST);
			pthread_mutex_init(&t->lock, &attr);
			pthread_mutexattr_destroy(&attr);
			t->next_gen = 1;
			t->version = TABLE_VERSION;
			__atomic_store_n(&t->magic, TABLE_MAGIC, __ATOMIC_RELEASE);
		}
		g_table = t;
	}
	flock(fd, LOCK_UN);
	close(fd);
	pthread_mutex_unlock(&g_attach);
	return g_table;
}

static void
lock_table(struct table *t)
{
	if (pthread_mutex_lock(&t->lock) == EOWNERDEAD) {
		// A process died inside; the table itself is consistent between operations.
		pthread_mutex_consistent(&t->lock);
	}
}

static void
wake_all(struct table *t)
{
	__atomic_add_fetch(&t->seq, 1, __ATOMIC_RELEASE);
	futex(&t->seq, FUTEX_WAKE, INT_MAX, NULL);
}

//! Locked. The queue of @p id, or NULL.
static struct queue *
find_id(struct table *t, int id)
{
	if (id < 0) {
		return NULL;
	}
	struct queue *q = &t->queues[id % MAX_QUEUES];
	return q->used && !q->removed && q->id == id ? q : NULL;
}

static bool
permitted(const struct queue *q, bool write)
{
	uid_t uid = geteuid();
	if (uid == 0) {
		return true;
	}
	mode_t bits = uid == q->uid || uid == q->cuid ? (q->mode >> 6) : (q->mode & 07);
	return (bits & (write ? 02 : 04)) != 0;
}

static bool
owner(const struct queue *q)
{
	uid_t uid = geteuid();
	return uid == 0 || uid == q->uid || uid == q->cuid;
}

//! The arena bytes a message of @p size takes.
static size_t
record_bytes(size_t size)
{
	size_t align = _Alignof(struct record);
	return sizeof(struct record) + (size + align - 1) / align * align;
}

//! Unlocked. Sleeps until the table changes from @p seq, 50 ms at most; EINTR if interrupted.
static int
wait_change(struct table *t, uint32_t seq)
{
	struct timespec wait = {0, 50000000L};
	if (futex(&t->seq, FUTEX_WAIT, seq, &wait) < 0 && errno == EINTR) {
		return EINTR;
	}
	return 0;
}

EXPORT int
msgget(key_t key, int flags)
{
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	lock_table(t);
	int ret = -1, err = 0;
	struct queue *found = NULL;
	for (int i = 0; key != IPC_PRIVATE && i < MAX_QUEUES && found == NULL; i++) {
		struct queue *q = &t->queues[i];
		if (q->used && !q->removed && q->key == key) {
			found = q;
		}
	}
	if (found != NULL) {
		if ((flags & IPC_CREAT) && (flags & IPC_EXCL)) {
			err = EEXIST;
		} else if (!permitted(found, false)) {
			err = EACCES;
		} else {
			ret = found->id;
		}
	} else if (key != IPC_PRIVATE && !(flags & IPC_CREAT)) {
		err = ENOENT;
	} else {
		int index = -1;
		for (int i = 0; i < MAX_QUEUES && index < 0; i++) {
			if (!t->queues[i].used || t->queues[i].removed) {
				index = i;
			}
		}
		if (index < 0) {
			err = ENOSPC;
		} else {
			struct queue *q = &t->queues[index];
			int gen = t->next_gen++;
			if (t->next_gen > INT_MAX / MAX_QUEUES - 1) {
				t->next_gen = 1;
			}
			memset(q, 0, offsetof(struct queue, arena));
			q->used = true;
			q->key = key;
			q->id = gen * MAX_QUEUES + index;
			q->uid = q->cuid = geteuid();
			q->gid = q->cgid = getegid();
			q->mode = (mode_t)(flags & 0777);
			q->ctime = time(NULL);
			q->limit = QUEUE_BYTES;
			ret = q->id;
		}
	}
	pthread_mutex_unlock(&t->lock);
	if (ret < 0) {
		errno = err;
	}
	return ret;
}

EXPORT int
msgsnd(int id, const void *msgp, size_t size, int flags)
{
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	long type = *(const long *)msgp;
	if (size > MESSAGE_MAX || type < 1) {
		errno = EINVAL;
		return -1;
	}
	bool waited = false;
	for (;;) {
		lock_table(t);
		struct queue *q = find_id(t, id);
		int err = 0;
		if (q == NULL) {
			err = waited ? EIDRM : EINVAL;
		} else if (!permitted(q, true)) {
			err = EACCES;
		} else if (q->text + size <= q->limit && q->used_bytes + record_bytes(size) <= sizeof(q->arena)) {
			struct record r = {.type = type, .size = size};
			memcpy(q->arena + q->used_bytes, &r, sizeof(r));
			memcpy(q->arena + q->used_bytes + sizeof(r), (const char *)msgp + sizeof(long), size);
			q->used_bytes += record_bytes(size);
			q->text += size;
			q->count++;
			q->lspid = getpid();
			q->stime = time(NULL);
			pthread_mutex_unlock(&t->lock);
			wake_all(t);
			return 0;
		} else if (flags & IPC_NOWAIT) {
			err = EAGAIN;
		}
		if (err != 0) {
			pthread_mutex_unlock(&t->lock);
			errno = err;
			return -1;
		}
		uint32_t seq = __atomic_load_n(&t->seq, __ATOMIC_ACQUIRE);
		pthread_mutex_unlock(&t->lock);
		waited = true;
		if (wait_change(t, seq) == EINTR) {
			errno = EINTR;
			return -1;
		}
	}
}

//! Locked. The offset of the message msgrcv takes for @p type and @p flags, or -1.
static long
pick(const struct queue *q, long type, int flags)
{
	long best = -1, best_type = LONG_MAX;
	size_t at = 0;
	for (size_t i = 0; i < q->count; i++) {
		struct record r;
		memcpy(&r, q->arena + at, sizeof(r));
		if (type == 0) {
			return (long)at;
		}
		if (type > 0 && ((flags & MSG_EXCEPT) ? r.type != type : r.type == type)) {
			return (long)at;
		}
		if (type < 0 && r.type <= -type && r.type < best_type) {
			best = (long)at;
			best_type = r.type;
		}
		at += record_bytes(r.size);
	}
	return best;
}

EXPORT ssize_t
msgrcv(int id, void *msgp, size_t size, long type, int flags)
{
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	if ((ssize_t)size < 0) {
		errno = EINVAL;
		return -1;
	}
	bool waited = false;
	for (;;) {
		lock_table(t);
		struct queue *q = find_id(t, id);
		int err = 0;
		long at = -1;
		if (q == NULL) {
			err = waited ? EIDRM : EINVAL;
		} else if (!permitted(q, false)) {
			err = EACCES;
		} else if ((at = pick(q, type, flags)) >= 0) {
			struct record r;
			memcpy(&r, q->arena + at, sizeof(r));
			if (r.size > size && !(flags & MSG_NOERROR)) {
				err = E2BIG;
			} else {
				size_t n = r.size < size ? r.size : size;
				*(long *)msgp = r.type;
				memcpy((char *)msgp + sizeof(long), q->arena + at + sizeof(r), n);
				size_t bytes = record_bytes(r.size);
				memmove(q->arena + at, q->arena + at + bytes, q->used_bytes - (size_t)at - bytes);
				q->used_bytes -= bytes;
				q->text -= r.size;
				q->count--;
				q->lrpid = getpid();
				q->rtime = time(NULL);
				pthread_mutex_unlock(&t->lock);
				wake_all(t);
				return (ssize_t)n;
			}
		} else if (flags & IPC_NOWAIT) {
			err = ENOMSG;
		}
		if (err != 0) {
			pthread_mutex_unlock(&t->lock);
			errno = err;
			return -1;
		}
		uint32_t seq = __atomic_load_n(&t->seq, __ATOMIC_ACQUIRE);
		pthread_mutex_unlock(&t->lock);
		waited = true;
		if (wait_change(t, seq) == EINTR) {
			errno = EINTR;
			return -1;
		}
	}
}

EXPORT int
msgctl(int id, int cmd, struct msqid_ds *buf)
{
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	if (cmd != IPC_STAT && cmd != IPC_SET && cmd != IPC_RMID) {
		errno = EINVAL;
		return -1;
	}
	lock_table(t);
	struct queue *q = find_id(t, id);
	int err = 0;
	if (q == NULL) {
		err = EINVAL;
	} else if (cmd == IPC_STAT) {
		if (!permitted(q, false)) {
			err = EACCES;
		} else {
			memset(buf, 0, sizeof(*buf));
			buf->msg_perm.__key = q->key;
			buf->msg_perm.uid = q->uid;
			buf->msg_perm.gid = q->gid;
			buf->msg_perm.cuid = q->cuid;
			buf->msg_perm.cgid = q->cgid;
			buf->msg_perm.mode = (unsigned short)q->mode;
			buf->msg_stime = q->stime;
			buf->msg_rtime = q->rtime;
			buf->msg_ctime = q->ctime;
			buf->__msg_cbytes = q->text;
			buf->msg_qnum = q->count;
			buf->msg_qbytes = q->limit;
			buf->msg_lspid = q->lspid;
			buf->msg_lrpid = q->lrpid;
		}
	} else if (!owner(q)) {
		err = EPERM;
	} else if (cmd == IPC_SET) {
		if (buf->msg_qbytes > QUEUE_BYTES && geteuid() != 0) {
			err = EPERM;
		} else {
			q->uid = buf->msg_perm.uid;
			q->gid = buf->msg_perm.gid;
			q->mode = buf->msg_perm.mode & 0777;
			// The arena holds QUEUE_BYTES of text at most, whatever is asked for.
			q->limit = buf->msg_qbytes < QUEUE_BYTES ? buf->msg_qbytes : QUEUE_BYTES;
			q->ctime = time(NULL);
		}
	} else {
		q->removed = true;
	}
	pthread_mutex_unlock(&t->lock);
	if (err == 0 && cmd != IPC_STAT) {
		wake_all(t);
	}
	if (err != 0) {
		errno = err;
		return -1;
	}
	return 0;
}
