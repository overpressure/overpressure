// SPDX-License-Identifier: BSL-1.0
/*
 * pico4-sysvsem: System V semaphores for a kernel without CONFIG_SYSVIPC
 * (docs/session.md).
 *
 * SteamVR's processes take their named locks (such as the bootstrapper lock
 * that keeps a second SteamVR from starting) with semget/semop/semctl. The
 * stock PICO 4 kernel has no System V IPC, so semget fails with ENOSYS and
 * SteamVR reports a "duplicate bootstrapper". Preloaded through
 * /etc/ld.so.preload, this library implements the three calls (and
 * semtimedop) on a table in shared memory, /dev/shm/pico4-sysvsem:
 *
 * - a robust, process-shared mutex guards the table, so a process that dies
 *   while holding it does not block the others;
 * - blocked operations sleep on a futex word that every change bumps;
 * - SEM_UNDO adjustments are kept per process and applied when it exits, or,
 *   if it died without running its destructor, by the next operation that
 *   finds the process gone.
 *
 * Nothing runs until a program calls one of these functions: the library is
 * inert for everything else, PID 1 included. PICO4_SYSVSEM_TABLE names another
 * table file (tests; ignored for privileged programs).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/futex.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ipc.h>
#include <sys/mman.h>
#include <sys/sem.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define EXPORT __attribute__((visibility("default")))

#define TABLE_PATH "/dev/shm/pico4-sysvsem"
#define TABLE_MAGIC 0x50345356u /* "P4SV" */
#define TABLE_VERSION 1
#define MAX_SETS 128
#define MAX_SEMS 32
#define MAX_UNDO 1024
#define SEM_MAX_VALUE 32767
#define REAP_INTERVAL_NS 50000000LL /* waiters look for dead processes this often */

struct sem_set
{
	bool used, removed;
	key_t key;
	int id;
	int nsems;
	uid_t uid, cuid;
	gid_t gid, cgid;
	mode_t mode;
	time_t otime, ctime;
	unsigned short value[MAX_SEMS];
	pid_t pid[MAX_SEMS];
	unsigned short ncnt[MAX_SEMS], zcnt[MAX_SEMS];
};

struct sem_undo
{
	pid_t pid; //!< 0: free
	int id;
	int num;
	int adj;
};

struct table
{
	uint32_t magic, version;
	pthread_mutex_t lock;
	uint32_t seq; //!< futex word: bumped on every change, waiters sleep on it
	int next_gen;
	struct sem_set sets[MAX_SETS];
	struct sem_undo undo[MAX_UNDO];
};

static struct table *g_table;
static pthread_mutex_t g_attach = PTHREAD_MUTEX_INITIALIZER;
static bool g_used_undo; //!< This process holds undo entries: apply them at exit.

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
	const char *path = secure_getenv("PICO4_SYSVSEM_TABLE");
	int fd = open(path != NULL ? path : TABLE_PATH, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0666);
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

//! Locked. The set of @p id, or NULL.
static struct sem_set *
find_id(struct table *t, int id)
{
	if (id < 0) {
		return NULL;
	}
	int index = id % MAX_SETS;
	struct sem_set *s = &t->sets[index];
	return s->used && !s->removed && s->id == id ? s : NULL;
}

static int
clamp_value(int v)
{
	return v < 0 ? 0 : (v > SEM_MAX_VALUE ? SEM_MAX_VALUE : v);
}

//! Locked. Applies and drops the undo entries of @p pid (all of them for pid 0: dead ones).
static bool
apply_undo(struct table *t, pid_t pid)
{
	bool changed = false;
	for (int i = 0; i < MAX_UNDO; i++) {
		struct sem_undo *u = &t->undo[i];
		if (u->pid == 0) {
			continue;
		}
		bool gone = pid != 0 ? u->pid == pid : (kill(u->pid, 0) != 0 && errno == ESRCH);
		if (!gone) {
			continue;
		}
		struct sem_set *s = find_id(t, u->id);
		if (s != NULL && u->num < s->nsems) {
			s->value[u->num] = (unsigned short)clamp_value(s->value[u->num] + u->adj);
			changed = true;
		}
		memset(u, 0, sizeof(*u));
	}
	return changed;
}

//! Locked. Records an undo adjustment for this process.
static int
add_undo(struct table *t, int id, int num, int adj)
{
	pid_t pid = getpid();
	struct sem_undo *free_slot = NULL;
	for (int i = 0; i < MAX_UNDO; i++) {
		struct sem_undo *u = &t->undo[i];
		if (u->pid == pid && u->id == id && u->num == num) {
			u->adj += adj;
			if (u->adj == 0) {
				memset(u, 0, sizeof(*u));
			}
			return 0;
		}
		if (u->pid == 0 && free_slot == NULL) {
			free_slot = u;
		}
	}
	if (free_slot == NULL) {
		return ENOSPC;
	}
	*free_slot = (struct sem_undo){.pid = pid, .id = id, .num = num, .adj = adj};
	g_used_undo = true;
	return 0;
}

//! Locked. Drops every undo entry of a semaphore (SETVAL, SETALL and IPC_RMID clear them).
static void
clear_undo(struct table *t, int id, int num)
{
	for (int i = 0; i < MAX_UNDO; i++) {
		struct sem_undo *u = &t->undo[i];
		if (u->pid != 0 && u->id == id && (num < 0 || u->num == num)) {
			memset(u, 0, sizeof(*u));
		}
	}
}

static bool
permitted(const struct sem_set *s, bool write)
{
	uid_t uid = geteuid();
	if (uid == 0) {
		return true;
	}
	mode_t bits = uid == s->uid || uid == s->cuid ? (s->mode >> 6) : (s->mode & 07);
	return (bits & (write ? 02 : 04)) != 0;
}

EXPORT int
semget(key_t key, int nsems, int flags)
{
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	if (nsems < 0 || nsems > MAX_SEMS) {
		errno = EINVAL;
		return -1;
	}
	lock_table(t);
	if (apply_undo(t, 0)) {
		wake_all(t);
	}
	int ret = -1, err = 0;
	struct sem_set *found = NULL;
	for (int i = 0; key != IPC_PRIVATE && i < MAX_SETS && found == NULL; i++) {
		struct sem_set *s = &t->sets[i];
		if (s->used && !s->removed && s->key == key) {
			found = s;
		}
	}
	if (found != NULL) {
		if ((flags & IPC_CREAT) && (flags & IPC_EXCL)) {
			err = EEXIST;
		} else if (nsems > found->nsems) {
			err = EINVAL;
		} else if (!permitted(found, false)) {
			err = EACCES;
		} else {
			ret = found->id;
		}
	} else if (key != IPC_PRIVATE && !(flags & IPC_CREAT)) {
		err = ENOENT;
	} else if (nsems == 0) {
		err = EINVAL;
	} else {
		int index = -1;
		for (int i = 0; i < MAX_SETS && index < 0; i++) {
			if (!t->sets[i].used || t->sets[i].removed) {
				index = i;
			}
		}
		if (index < 0) {
			err = ENOSPC;
		} else {
			struct sem_set *s = &t->sets[index];
			int gen = t->next_gen++;
			if (t->next_gen > INT_MAX / MAX_SETS - 1) {
				t->next_gen = 1;
			}
			*s = (struct sem_set){
			    .used = true,
			    .key = key,
			    .id = gen * MAX_SETS + index,
			    .nsems = nsems,
			    .uid = geteuid(),
			    .cuid = geteuid(),
			    .gid = getegid(),
			    .cgid = getegid(),
			    .mode = (mode_t)(flags & 0777),
			    .ctime = time(NULL),
			};
			ret = s->id;
		}
	}
	pthread_mutex_unlock(&t->lock);
	if (ret < 0) {
		errno = err;
	}
	return ret;
}

static int
do_semop(int id, struct sembuf *sops, size_t nsops, const struct timespec *timeout)
{
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	if (nsops == 0 || nsops > 64) {
		errno = nsops == 0 ? EINVAL : E2BIG;
		return -1;
	}
	struct timespec deadline = {0};
	if (timeout != NULL) {
		clock_gettime(CLOCK_MONOTONIC, &deadline);
		deadline.tv_sec += timeout->tv_sec;
		deadline.tv_nsec += timeout->tv_nsec;
		if (deadline.tv_nsec >= 1000000000L) {
			deadline.tv_sec++;
			deadline.tv_nsec -= 1000000000L;
		}
	}
	bool counted = false;
	for (;;) {
		lock_table(t);
		if (apply_undo(t, 0)) {
			wake_all(t);
		}
		struct sem_set *s = find_id(t, id);
		int err = 0;
		if (s == NULL) {
			err = counted ? EIDRM : EINVAL;
		}
		bool write = false;
		for (size_t i = 0; err == 0 && i < nsops; i++) {
			if (sops[i].sem_num >= s->nsems) {
				err = EFBIG;
			}
			write = write || sops[i].sem_op != 0;
		}
		if (err == 0 && !permitted(s, write)) {
			err = EACCES;
		}
		if (err != 0) {
			pthread_mutex_unlock(&t->lock);
			errno = err;
			return -1;
		}

		// All or nothing: try the operations on a copy of the values.
		int values[MAX_SEMS];
		for (int i = 0; i < s->nsems; i++) {
			values[i] = s->value[i];
		}
		int blocked = -1;
		for (size_t i = 0; i < nsops && blocked < 0 && err == 0; i++) {
			int v = values[sops[i].sem_num] + sops[i].sem_op;
			if (sops[i].sem_op == 0 ? values[sops[i].sem_num] != 0 : v < 0) {
				blocked = (int)i;
			} else if (v > SEM_MAX_VALUE) {
				err = ERANGE;
			} else {
				values[sops[i].sem_num] = v;
			}
		}
		if (err == 0 && blocked < 0) {
			for (size_t i = 0; i < nsops && err == 0; i++) {
				if ((sops[i].sem_flg & SEM_UNDO) && sops[i].sem_op != 0) {
					err = add_undo(t, id, sops[i].sem_num, -sops[i].sem_op);
				}
			}
		}
		if (err == 0 && blocked < 0) {
			pid_t pid = getpid();
			for (int i = 0; i < s->nsems; i++) {
				s->value[i] = (unsigned short)values[i];
			}
			for (size_t i = 0; i < nsops; i++) {
				s->pid[sops[i].sem_num] = pid;
			}
			s->otime = time(NULL);
			pthread_mutex_unlock(&t->lock);
			wake_all(t);
			return 0;
		}
		if (err != 0 || (sops[blocked].sem_flg & IPC_NOWAIT)) {
			pthread_mutex_unlock(&t->lock);
			errno = err != 0 ? err : EAGAIN;
			return -1;
		}

		// Wait for a change (or a process to die: reap again after a while).
		uint32_t seq = __atomic_load_n(&t->seq, __ATOMIC_ACQUIRE);
		int num = sops[blocked].sem_num;
		if (sops[blocked].sem_op == 0) {
			s->zcnt[num]++;
		} else {
			s->ncnt[num]++;
		}
		pthread_mutex_unlock(&t->lock);
		counted = true;

		struct timespec wait = {0, REAP_INTERVAL_NS};
		if (timeout != NULL) {
			struct timespec now;
			clock_gettime(CLOCK_MONOTONIC, &now);
			long long left = (deadline.tv_sec - now.tv_sec) * 1000000000LL + (deadline.tv_nsec - now.tv_nsec);
			if (left <= 0) {
				lock_table(t);
				if ((s = find_id(t, id)) != NULL) {
					if (sops[blocked].sem_op == 0) {
						s->zcnt[num]--;
					} else {
						s->ncnt[num]--;
					}
				}
				pthread_mutex_unlock(&t->lock);
				errno = EAGAIN;
				return -1;
			}
			if (left < REAP_INTERVAL_NS) {
				wait.tv_nsec = left;
			}
		}
		long r = futex(&t->seq, FUTEX_WAIT, seq, &wait);
		int futex_err = r < 0 ? errno : 0;

		lock_table(t);
		if ((s = find_id(t, id)) != NULL) {
			if (sops[blocked].sem_op == 0) {
				s->zcnt[num]--;
			} else {
				s->ncnt[num]--;
			}
		}
		pthread_mutex_unlock(&t->lock);
		if (futex_err == EINTR) {
			errno = EINTR;
			return -1;
		}
	}
}

EXPORT int
semop(int id, struct sembuf *sops, size_t nsops)
{
	return do_semop(id, sops, nsops, NULL);
}

EXPORT int
semtimedop(int id, struct sembuf *sops, size_t nsops, const struct timespec *timeout)
{
	return do_semop(id, sops, nsops, timeout);
}

union pico4_semun
{
	int val;
	struct semid_ds *buf;
	unsigned short *array;
	void *info;
};

EXPORT int
semctl(int id, int num, int cmd, ...)
{
	union pico4_semun arg = {0};
	if (cmd == SETVAL || cmd == GETALL || cmd == SETALL || cmd == IPC_STAT || cmd == IPC_SET ||
	    cmd == IPC_INFO || cmd == SEM_INFO || cmd == SEM_STAT) {
		va_list ap;
		va_start(ap, cmd);
		arg = va_arg(ap, union pico4_semun);
		va_end(ap);
	}
	struct table *t = attach();
	if (t == NULL) {
		errno = ENOSYS;
		return -1;
	}
	if (cmd == IPC_INFO || cmd == SEM_INFO || cmd == SEM_STAT) {
		errno = EINVAL;
		return -1;
	}
	lock_table(t);
	// Values must reflect processes that died holding SEM_UNDO adjustments.
	bool reaped = apply_undo(t, 0);
	struct sem_set *s = find_id(t, id);
	int ret = 0, err = 0;
	if (s == NULL) {
		err = EINVAL;
	} else if ((cmd == GETVAL || cmd == SETVAL || cmd == GETPID || cmd == GETNCNT || cmd == GETZCNT) &&
	           (num < 0 || num >= s->nsems)) {
		err = EINVAL;
	} else {
		switch (cmd) {
		case GETVAL: ret = s->value[num]; break;
		case GETPID: ret = s->pid[num]; break;
		case GETNCNT: ret = s->ncnt[num]; break;
		case GETZCNT: ret = s->zcnt[num]; break;
		case SETVAL:
			if (!permitted(s, true)) {
				err = EACCES;
			} else if (arg.val < 0 || arg.val > SEM_MAX_VALUE) {
				err = ERANGE;
			} else {
				s->value[num] = (unsigned short)arg.val;
				s->pid[num] = getpid();
				s->ctime = time(NULL);
				clear_undo(t, id, num);
			}
			break;
		case GETALL:
			for (int i = 0; i < s->nsems; i++) {
				arg.array[i] = s->value[i];
			}
			break;
		case SETALL:
			if (!permitted(s, true)) {
				err = EACCES;
				break;
			}
			for (int i = 0; i < s->nsems && err == 0; i++) {
				if (arg.array[i] > SEM_MAX_VALUE) {
					err = ERANGE;
				}
			}
			for (int i = 0; i < s->nsems && err == 0; i++) {
				s->value[i] = arg.array[i];
			}
			if (err == 0) {
				s->ctime = time(NULL);
				clear_undo(t, id, -1);
			}
			break;
		case IPC_STAT:
			memset(arg.buf, 0, sizeof(*arg.buf));
			arg.buf->sem_perm.__key = s->key;
			arg.buf->sem_perm.uid = s->uid;
			arg.buf->sem_perm.gid = s->gid;
			arg.buf->sem_perm.cuid = s->cuid;
			arg.buf->sem_perm.cgid = s->cgid;
			arg.buf->sem_perm.mode = (unsigned short)s->mode;
			arg.buf->sem_otime = s->otime;
			arg.buf->sem_ctime = s->ctime;
			arg.buf->sem_nsems = (unsigned long)s->nsems;
			break;
		case IPC_SET:
			if (geteuid() != 0 && geteuid() != s->uid && geteuid() != s->cuid) {
				err = EPERM;
				break;
			}
			s->uid = arg.buf->sem_perm.uid;
			s->gid = arg.buf->sem_perm.gid;
			s->mode = arg.buf->sem_perm.mode & 0777;
			s->ctime = time(NULL);
			break;
		case IPC_RMID:
			if (geteuid() != 0 && geteuid() != s->uid && geteuid() != s->cuid) {
				err = EPERM;
				break;
			}
			clear_undo(t, id, -1);
			s->removed = true;
			break;
		default: err = EINVAL; break;
		}
	}
	pthread_mutex_unlock(&t->lock);
	if (reaped || cmd == SETVAL || cmd == SETALL || cmd == IPC_RMID) {
		wake_all(t);
	}
	if (err != 0) {
		errno = err;
		return -1;
	}
	return ret;
}

//! A process that used SEM_UNDO gives its adjustments back when it exits normally.
__attribute__((destructor)) static void
undo_at_exit(void)
{
	if (!g_used_undo || g_table == NULL) {
		return;
	}
	lock_table(g_table);
	apply_undo(g_table, getpid());
	pthread_mutex_unlock(&g_table->lock);
	wake_all(g_table);
}
