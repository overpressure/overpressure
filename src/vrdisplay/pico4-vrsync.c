// SPDX-License-Identifier: BSL-1.0
/*
 * Exportable semaphores without kernel support, part of VK_LAYER_PICO4_vrdisplay
 * (docs/session.md).
 *
 * SteamVR shares timeline semaphores between its compositor and other processes
 * (mirror textures, application swapchains) as OPAQUE_FD handles. Turnip's KGSL
 * backend exports semaphores only as sync files, and the stock 4.19 kernel has
 * nothing that keeps a GPU fence reachable from several processes: msm_drm has
 * no syncobjs and KGSL no timelines. Where the driver cannot export OPAQUE_FD
 * semaphores, this module emulates them with sync files:
 *
 *  - the OPAQUE_FD handle is a memfd with the semaphore's shared state: the
 *    value it reached and its pending signal operations, each with the process
 *    that holds the operation's fence;
 *  - a queue signal operation goes to a binary semaphore instead, whose sync
 *    file becomes the operation's fence, held by the signalling process;
 *  - a queue wait operation looks up the earliest pending signal operation that
 *    satisfies it, fetches its fence from the holder over a Unix socket
 *    (SCM_RIGHTS) and waits through a binary semaphore with the fence imported;
 *  - a thread in each signalling process watches the fences it holds, records
 *    the values they reach and hands them to other processes on request.
 *
 * vkWaitSemaphores, vkSignalSemaphore and vkGetSemaphoreCounterValue work on the
 * shared state. Every process that uses such a semaphore must run the layer.
 * A wait submitted before its signal operation blocks the submission until the
 * signal is submitted (WAIT_FOR_SIGNAL_NS at most).
 *
 * Environment: PICO4_VRDISPLAY_SYNC=off leaves semaphores to the driver.
 */
#define _GNU_SOURCE
#include "pico4-vrsync.h"

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/futex.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define LOG(...) vrdisplay_log(false, "vrsync: " __VA_ARGS__)
#define DBG(...) vrdisplay_log(true, "vrsync: " __VA_ARGS__)

#define MAGIC 0x50345653u               //!< a memfd of this module
#define SHARED_SIZE 4096
#define POINTS 64                       //!< pending signal operations per semaphore
#define MAX_SPARES 256                  //!< binary semaphores per device
#define WAIT_FOR_SIGNAL_NS 2000000000LL //!< longest wait for a signal operation's submission
#define NS_PER_MS 1000000LL
#define OPAQUE_FD VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT
#define SYNC_FD VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT

//! A pending signal operation: its value and the process holding its fence.
struct point
{
	uint64_t value; //!< 0: unused
	int32_t pid;
	uint32_t id; //!< the fence's id in that process
};

//! A semaphore's state, shared through the memfd.
struct shared
{
	uint32_t magic;
	uint32_t type;        //!< VkSemaphoreType
	pthread_mutex_t lock; //!< robust, process-shared
	uint32_t seq;         //!< futex word, bumped on every change
	uint32_t reserved;
	uint64_t value;   //!< value reached (binary semaphores: signal operations completed)
	uint64_t signals; //!< binary semaphores: signal operations submitted
	struct point points[POINTS];
};
_Static_assert(sizeof(struct shared) <= SHARED_SIZE, "the shared state fits its memfd");

//! A mapping of a shared state, referenced by semaphores and held fences.
struct mapping
{
	int refs; //!< atomic
	int fd;   //!< the memfd
	struct shared *s;
};

//! A semaphore of this process whose payload is emulated.
struct sem
{
	struct sem *next;
	VkDevice device;
	VkSemaphore handle;
	struct mapping *map;
};

//! A fence this process holds for a pending signal operation.
struct fence
{
	struct fence *next;
	uint32_t id;
	int fd; //!< sync file
	uint64_t value;
	struct mapping *map;
};

//! A binary semaphore that stands in for emulated semaphores in submissions.
struct spare
{
	struct spare *next;
	VkSemaphore handle;
	bool used;
	int busy; //!< sync file of the last submission that used it, -1: none
};

struct device
{
	struct device *next;
	void *key;
	VkDevice device;
	bool emulate;
	PFN_vkCreateSemaphore CreateSemaphore;
	PFN_vkDestroySemaphore DestroySemaphore;
	PFN_vkGetSemaphoreFdKHR GetSemaphoreFdKHR;
	PFN_vkImportSemaphoreFdKHR ImportSemaphoreFdKHR;
	PFN_vkQueueSubmit QueueSubmit;
	PFN_vkQueueSubmit2 QueueSubmit2;
	PFN_vkWaitSemaphores WaitSemaphores;
	PFN_vkSignalSemaphore SignalSemaphore;
	PFN_vkGetSemaphoreCounterValue GetSemaphoreCounterValue;
	struct spare *spares; //!< under g_lock
	unsigned nspares;
};

struct instance
{
	struct instance *next;
	void *key;
	PFN_vkGetPhysicalDeviceExternalSemaphoreProperties GetPhysicalDeviceExternalSemaphoreProperties;
};

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static struct instance *g_instances;
static struct device *g_devices;
static struct sem *g_sems;
static struct fence *g_fences; //!< removed only by the server thread
static uint32_t g_fence_id;
static pid_t g_server_pid; //!< the process the server thread runs in
static int g_listen = -1, g_wake = -1;

static void *
key_of(const void *handle)
{
	return *(void *const *)handle;
}

static int64_t
now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void
futex_wait(uint32_t *word, uint32_t seen, int64_t ns)
{
	struct timespec ts = {ns / 1000000000LL, ns % 1000000000LL};
	syscall(SYS_futex, word, FUTEX_WAIT, seen, &ts, NULL, 0);
}

static void
futex_wake(uint32_t *word)
{
	syscall(SYS_futex, word, FUTEX_WAKE, INT_MAX, NULL, NULL, 0);
}

static bool
signalled(int fd)
{
	struct pollfd p = {fd, POLLIN, 0};
	return poll(&p, 1, 0) == 1;
}

static int
dup_cloexec(int fd)
{
	return fcntl(fd, F_DUPFD_CLOEXEC, 3);
}

static struct device *
find_device(const void *handle)
{
	void *key = key_of(handle);
	pthread_mutex_lock(&g_lock);
	struct device *d = g_devices;
	while (d != NULL && d->key != key) {
		d = d->next;
	}
	pthread_mutex_unlock(&g_lock);
	return d;
}

static struct instance *
find_instance(const void *handle)
{
	void *key = key_of(handle);
	pthread_mutex_lock(&g_lock);
	struct instance *in = g_instances;
	while (in != NULL && in->key != key) {
		in = in->next;
	}
	pthread_mutex_unlock(&g_lock);
	return in;
}


/*
 *
 * Shared state.
 *
 */

static void
lock_shared(struct shared *s)
{
	if (pthread_mutex_lock(&s->lock) == EOWNERDEAD) {
		pthread_mutex_consistent(&s->lock);
	}
}

static void
unlock_shared(struct shared *s)
{
	pthread_mutex_unlock(&s->lock);
}

//! Under the lock: records a reached value and drops the operations it satisfies.
static void
reach(struct shared *s, uint64_t value)
{
	if (value > s->value) {
		s->value = value;
	}
	for (int i = 0; i < POINTS; i++) {
		if (s->points[i].value != 0 && s->points[i].value <= s->value) {
			s->points[i].value = 0;
		}
	}
	s->seq++;
}

static void
reach_now(struct shared *s, uint64_t value)
{
	lock_shared(s);
	reach(s, value);
	unlock_shared(s);
	futex_wake(&s->seq);
}

static void
map_ref(struct mapping *m)
{
	__atomic_add_fetch(&m->refs, 1, __ATOMIC_ACQ_REL);
}

static void
map_unref(struct mapping *m)
{
	if (m != NULL && __atomic_sub_fetch(&m->refs, 1, __ATOMIC_ACQ_REL) == 0) {
		munmap(m->s, SHARED_SIZE);
		if (m->fd >= 0) {
			close(m->fd);
		}
		free(m);
	}
}

//! Maps a memfd of this module; takes the fd on success.
static struct mapping *
map_fd(int fd)
{
	struct stat st;
	if (fd < 0 || fstat(fd, &st) != 0 || st.st_size != SHARED_SIZE) {
		return NULL;
	}
	struct shared *s = mmap(NULL, SHARED_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (s == MAP_FAILED) {
		return NULL;
	}
	struct mapping *m = calloc(1, sizeof(*m));
	if (s->magic != MAGIC || m == NULL) {
		munmap(s, SHARED_SIZE);
		free(m);
		return NULL;
	}
	m->refs = 1;
	m->fd = fd;
	m->s = s;
	return m;
}

static struct mapping *
map_create(VkSemaphoreType type, uint64_t initial)
{
	int fd = memfd_create("pico4-vrsync", MFD_CLOEXEC);
	if (fd < 0 || ftruncate(fd, SHARED_SIZE) != 0) {
		LOG("memfd: %s", strerror(errno));
		if (fd >= 0) {
			close(fd);
		}
		return NULL;
	}
	struct shared *s = mmap(NULL, SHARED_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (s == MAP_FAILED) {
		close(fd);
		return NULL;
	}
	pthread_mutexattr_t attr;
	pthread_mutexattr_init(&attr);
	pthread_mutexattr_setpshared(&attr, PTHREAD_PROCESS_SHARED);
	pthread_mutexattr_setrobust(&attr, PTHREAD_MUTEX_ROBUST);
	pthread_mutex_init(&s->lock, &attr);
	pthread_mutexattr_destroy(&attr);
	s->type = type;
	s->value = initial;
	s->magic = MAGIC;
	munmap(s, SHARED_SIZE);
	struct mapping *m = map_fd(fd);
	if (m == NULL) {
		close(fd);
	}
	return m;
}

//! Registers (or re-points) an emulated semaphore; takes the reference on `m`.
static bool
sem_set(VkDevice device, VkSemaphore handle, struct mapping *m)
{
	struct mapping *old = NULL;
	pthread_mutex_lock(&g_lock);
	struct sem *e = g_sems;
	while (e != NULL && !(e->device == device && e->handle == handle)) {
		e = e->next;
	}
	if (e == NULL && (e = calloc(1, sizeof(*e))) != NULL) {
		e->device = device;
		e->handle = handle;
		e->next = g_sems;
		__atomic_store_n(&g_sems, e, __ATOMIC_RELEASE);
	}
	if (e != NULL) {
		old = e->map;
		e->map = m;
	}
	pthread_mutex_unlock(&g_lock);
	map_unref(old);
	return e != NULL;
}

static void
sem_remove(VkDevice device, VkSemaphore handle)
{
	struct sem *gone = NULL;
	pthread_mutex_lock(&g_lock);
	for (struct sem **p = &g_sems; *p != NULL; p = &(*p)->next) {
		if ((*p)->device == device && (*p)->handle == handle) {
			gone = *p;
			*p = gone->next;
			break;
		}
	}
	pthread_mutex_unlock(&g_lock);
	if (gone != NULL) {
		map_unref(gone->map);
		free(gone);
	}
}

static bool
have_sems(void)
{
	return __atomic_load_n(&g_sems, __ATOMIC_ACQUIRE) != NULL;
}

//! The shared state of an emulated semaphore (a new reference), or NULL.
static struct mapping *
shared_of(VkDevice device, VkSemaphore handle)
{
	if (handle == VK_NULL_HANDLE || !have_sems()) {
		return NULL;
	}
	struct mapping *m = NULL;
	pthread_mutex_lock(&g_lock);
	for (struct sem *e = g_sems; e != NULL; e = e->next) {
		if (e->device == device && e->handle == handle) {
			m = e->map;
			map_ref(m);
			break;
		}
	}
	pthread_mutex_unlock(&g_lock);
	return m;
}

//! Pending operations whose holder exited will not be signalled by it any more.
static void
settle_exited(struct shared *s)
{
	lock_shared(s);
	uint64_t gone = 0;
	for (int i = 0; i < POINTS; i++) {
		const struct point *p = &s->points[i];
		if (p->value != 0 && p->pid != getpid() && kill(p->pid, 0) != 0 && errno == ESRCH && p->value > gone) {
			gone = p->value;
		}
	}
	if (gone != 0) {
		reach(s, gone);
	}
	unlock_shared(s);
	if (gone != 0) {
		futex_wake(&s->seq);
		LOG("the holder of value %" PRIu64 " exited; treating it as reached", gone);
	}
}


/*
 *
 * Fences: held by the signalling process, handed out over a Unix socket.
 *
 */

static socklen_t
address(pid_t pid, struct sockaddr_un *a)
{
	memset(a, 0, sizeof(*a));
	a->sun_family = AF_UNIX;
	int n = snprintf(a->sun_path + 1, sizeof(a->sun_path) - 1, "pico4-vrsync.%d", (int)pid);
	return (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + (size_t)n);
}

static void
set_timeouts(int sock)
{
	struct timeval tv = {1, 0};
	setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
	setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

static void
send_reply(int sock, int32_t status, int fd)
{
	union
	{
		struct cmsghdr header;
		char buf[CMSG_SPACE(sizeof(int))];
	} control;
	struct iovec iov = {&status, sizeof(status)};
	struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1};
	if (fd >= 0) {
		memset(&control, 0, sizeof(control));
		msg.msg_control = control.buf;
		msg.msg_controllen = sizeof(control.buf);
		struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
		c->cmsg_level = SOL_SOCKET;
		c->cmsg_type = SCM_RIGHTS;
		c->cmsg_len = CMSG_LEN(sizeof(int));
		memcpy(CMSG_DATA(c), &fd, sizeof(int));
	}
	sendmsg(sock, &msg, MSG_NOSIGNAL);
}

//! In the server thread: answers one request for a fence.
static void
serve_one(void)
{
	int c = accept4(g_listen, NULL, NULL, SOCK_CLOEXEC);
	if (c < 0) {
		return;
	}
	set_timeouts(c);
	uint32_t id = 0;
	if (recv(c, &id, sizeof(id), 0) == (ssize_t)sizeof(id)) {
		int fd = -1;
		pthread_mutex_lock(&g_lock);
		for (struct fence *f = g_fences; f != NULL; f = f->next) {
			if (f->id == id) {
				fd = f->fd; // only this thread frees fences
				break;
			}
		}
		pthread_mutex_unlock(&g_lock);
		send_reply(c, fd >= 0 ? 0 : 1, fd);
	}
	close(c);
}

static void
fence_free(struct fence *f)
{
	close(f->fd);
	map_unref(f->map);
	free(f);
}

//! Records the values of signalled fences and serves fence requests.
static void *
server(void *arg)
{
	(void)arg;
	struct pollfd *fds = NULL;
	struct fence **of = NULL;
	size_t cap = 0;
	for (;;) {
		pthread_mutex_lock(&g_lock);
		size_t n = 2;
		for (struct fence *f = g_fences; f != NULL; f = f->next) {
			n++;
		}
		if (n > cap) {
			size_t want = n + 32;
			struct pollfd *nf = realloc(fds, want * sizeof(*fds));
			if (nf != NULL) {
				fds = nf;
			}
			struct fence **no = realloc(of, want * sizeof(*of));
			if (no != NULL) {
				of = no;
			}
			if (nf != NULL && no != NULL) {
				cap = want;
			}
		}
		size_t i = 0;
		if (cap >= 2) {
			fds[i++] = (struct pollfd){g_listen, POLLIN, 0};
			fds[i++] = (struct pollfd){g_wake, POLLIN, 0};
			for (struct fence *f = g_fences; f != NULL && i < cap; f = f->next) {
				of[i] = f;
				fds[i++] = (struct pollfd){f->fd, POLLIN, 0};
			}
		}
		pthread_mutex_unlock(&g_lock);
		if (i < 2) {
			usleep(10000); // out of memory
			continue;
		}
		if (poll(fds, i, -1) < 0) {
			continue;
		}
		if (fds[1].revents != 0) {
			uint64_t v;
			if (read(g_wake, &v, sizeof(v)) < 0) {
				// drained already
			}
		}
		for (size_t k = 2; k < i; k++) {
			if (fds[k].revents == 0) {
				continue;
			}
			struct fence *f = of[k];
			reach_now(f->map->s, f->value);
			pthread_mutex_lock(&g_lock);
			for (struct fence **p = &g_fences; *p != NULL; p = &(*p)->next) {
				if (*p == f) {
					*p = f->next;
					break;
				}
			}
			pthread_mutex_unlock(&g_lock);
			fence_free(f);
		}
		if (fds[0].revents & POLLIN) {
			serve_one();
		}
	}
	return NULL;
}

//! Under g_lock: starts this process's server thread (again after a fork).
static bool
server_start_locked(void)
{
	pid_t pid = getpid();
	if (g_server_pid == pid) {
		return true;
	}
	while (g_fences != NULL) { // after a fork: the parent's fences are not this process's to hand out
		struct fence *f = g_fences;
		g_fences = f->next;
		fence_free(f);
	}
	if (g_listen >= 0) {
		close(g_listen);
		close(g_wake);
		g_listen = g_wake = -1;
	}
	struct sockaddr_un a;
	socklen_t len = address(pid, &a);
	int l = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
	int w = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
	if (l < 0 || w < 0 || bind(l, (struct sockaddr *)&a, len) != 0 || listen(l, 16) != 0) {
		LOG("fence socket: %s", strerror(errno));
		if (l >= 0) {
			close(l);
		}
		if (w >= 0) {
			close(w);
		}
		return false;
	}
	g_listen = l;
	g_wake = w;
	// The loader unloads the layer after the last instance; the thread outlives it.
	Dl_info self;
	if (dladdr((void *)server, &self) == 0 || dlopen(self.dli_fname, RTLD_NOW | RTLD_NODELETE) == NULL) {
		LOG("cannot keep the layer loaded: %s", dlerror());
		close(l);
		close(w);
		g_listen = g_wake = -1;
		return false;
	}
	// The thread takes no signals meant for the application.
	sigset_t all, old;
	sigfillset(&all);
	pthread_sigmask(SIG_SETMASK, &all, &old);
	pthread_attr_t attr;
	pthread_attr_init(&attr);
	pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
	pthread_t t;
	int err = pthread_create(&t, &attr, server, NULL);
	pthread_attr_destroy(&attr);
	pthread_sigmask(SIG_SETMASK, &old, NULL);
	if (err != 0) {
		LOG("fence thread: %s", strerror(err));
		close(l);
		close(w);
		g_listen = g_wake = -1;
		return false;
	}
	g_server_pid = pid;
	return true;
}

//! Makes a submitted signal operation visible to other processes; takes `fd` (-1: signalled).
static void
publish(struct mapping *m, uint64_t value, int fd)
{
	struct shared *s = m->s;
	struct fence *f = fd >= 0 ? calloc(1, sizeof(*f)) : NULL;
	pthread_mutex_lock(&g_lock);
	if (f == NULL || !server_start_locked()) {
		pthread_mutex_unlock(&g_lock);
		free(f);
		if (fd >= 0) {
			// The fence cannot be handed out: wait for it here instead.
			struct pollfd p = {fd, POLLIN, 0};
			poll(&p, 1, 1000);
			close(fd);
		}
		reach_now(s, value);
		return;
	}
	if (++g_fence_id == 0) {
		g_fence_id = 1;
	}
	f->id = g_fence_id;
	f->fd = fd;
	f->value = value;
	f->map = m;
	map_ref(m);
	f->next = g_fences;
	g_fences = f;
	uint32_t id = f->id;
	pthread_mutex_unlock(&g_lock);

	lock_shared(s);
	int slot = 0;
	for (int i = 0; i < POINTS; i++) {
		if (s->points[i].value == 0) {
			slot = i;
			break;
		}
		if (s->points[i].value < s->points[slot].value) {
			slot = i; // all taken: replace the oldest, its waiters take a later one
		}
	}
	s->points[slot] = (struct point){value, getpid(), id};
	s->seq++;
	unlock_shared(s);
	futex_wake(&s->seq);
	uint64_t one = 1;
	if (write(g_wake, &one, sizeof(one)) < 0) {
		// the thread is awake already
	}
}

//! A fence this process holds, duplicated; -1 if it is gone.
static int
local_fence(uint32_t id)
{
	int fd = -1;
	pthread_mutex_lock(&g_lock);
	for (struct fence *f = g_fences; f != NULL; f = f->next) {
		if (f->id == id) {
			fd = dup_cloexec(f->fd);
			break;
		}
	}
	pthread_mutex_unlock(&g_lock);
	return fd;
}

//! A fence another process holds: its fd, -1 if it signalled already, -2 if the holder is gone, -3 on errors.
static int
fetch(pid_t pid, uint32_t id)
{
	int c = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
	if (c < 0) {
		return -3;
	}
	set_timeouts(c);
	struct sockaddr_un a;
	socklen_t len = address(pid, &a);
	if (connect(c, (struct sockaddr *)&a, len) != 0) {
		int err = errno;
		close(c);
		return err == ECONNREFUSED || err == ENOENT ? -2 : -3;
	}
	if (send(c, &id, sizeof(id), MSG_NOSIGNAL) != (ssize_t)sizeof(id)) {
		close(c);
		return -3;
	}
	int32_t status = -1;
	int fd = -1;
	union
	{
		struct cmsghdr header;
		char buf[CMSG_SPACE(sizeof(int))];
	} control;
	struct iovec iov = {&status, sizeof(status)};
	struct msghdr msg = {.msg_iov = &iov,
	                     .msg_iovlen = 1,
	                     .msg_control = control.buf,
	                     .msg_controllen = sizeof(control.buf)};
	ssize_t r = recvmsg(c, &msg, MSG_CMSG_CLOEXEC);
	if (r == (ssize_t)sizeof(status)) {
		for (struct cmsghdr *h = CMSG_FIRSTHDR(&msg); h != NULL; h = CMSG_NXTHDR(&msg, h)) {
			if (h->cmsg_level == SOL_SOCKET && h->cmsg_type == SCM_RIGHTS) {
				memcpy(&fd, CMSG_DATA(h), sizeof(int));
			}
		}
	}
	close(c);
	if (r == (ssize_t)sizeof(status) && status == 0 && fd >= 0) {
		return fd;
	}
	if (fd >= 0) {
		close(fd);
	}
	return r == (ssize_t)sizeof(status) ? -1 : -3;
}

enum wait_need
{
	REACHED, //!< nothing to wait for
	FENCE,   //!< wait for the returned fence
	MISSING, //!< no signal operation was submitted in time
};

/*!
 * What a wait for `target` needs (binary semaphores: their latest signal
 * operation). Waits for the signal operation's submission if need be.
 */
static enum wait_need
resolve(struct mapping *m, uint64_t target, int *fd)
{
	struct shared *s = m->s;
	int64_t deadline = now_ns() + WAIT_FOR_SIGNAL_NS;
	if (s->type == VK_SEMAPHORE_TYPE_BINARY) {
		lock_shared(s);
		target = s->signals;
		unlock_shared(s);
	}
	for (bool checked = false;;) {
		lock_shared(s);
		if (s->value >= target) {
			unlock_shared(s);
			return REACHED;
		}
		struct point best = {0, 0, 0};
		for (int i = 0; i < POINTS; i++) {
			const struct point *p = &s->points[i];
			if (p->value >= target && (best.value == 0 || p->value < best.value)) {
				best = *p;
			}
		}
		uint32_t seq = s->seq;
		unlock_shared(s);
		if (best.value != 0) {
			int r = best.pid == getpid() ? local_fence(best.id) : fetch(best.pid, best.id);
			if (r >= 0) {
				*fd = r;
				return FENCE;
			}
			if (r == -2) {
				LOG("the holder of value %" PRIu64 " is gone; treating it as reached", best.value);
				reach_now(s, best.value);
				continue;
			}
		}
		int64_t left = deadline - now_ns();
		if (left <= 0) {
			return MISSING;
		}
		if (!checked && left < WAIT_FOR_SIGNAL_NS / 2) {
			settle_exited(s);
			checked = true;
		}
		futex_wait(&s->seq, seq, left < 10 * NS_PER_MS ? left : 10 * NS_PER_MS);
	}
}


/*
 *
 * Submissions: emulated semaphores replaced by binary ones.
 *
 */

static struct spare *
spare_get(struct device *d)
{
	pthread_mutex_lock(&g_lock);
	for (struct spare *sp = d->spares; sp != NULL; sp = sp->next) {
		if (!sp->used && (sp->busy < 0 || signalled(sp->busy))) {
			if (sp->busy >= 0) {
				close(sp->busy);
				sp->busy = -1;
			}
			sp->used = true;
			pthread_mutex_unlock(&g_lock);
			return sp;
		}
	}
	unsigned n = d->nspares;
	pthread_mutex_unlock(&g_lock);
	if (n >= MAX_SPARES) {
		LOG("%u binary semaphores in flight; not emulating this operation", n);
		return NULL;
	}
	VkExportSemaphoreCreateInfo export = {VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO, NULL, SYNC_FD};
	VkSemaphoreCreateInfo info = {VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, &export, 0};
	struct spare *sp = calloc(1, sizeof(*sp));
	if (sp == NULL || d->CreateSemaphore(d->device, &info, NULL, &sp->handle) != VK_SUCCESS) {
		free(sp);
		return NULL;
	}
	sp->used = true;
	sp->busy = -1;
	pthread_mutex_lock(&g_lock);
	sp->next = d->spares;
	d->spares = sp;
	d->nspares++;
	pthread_mutex_unlock(&g_lock);
	return sp;
}

static void
spare_put(struct spare *sp)
{
	pthread_mutex_lock(&g_lock);
	sp->used = false;
	pthread_mutex_unlock(&g_lock);
}

static int
export_sync_file(struct device *d, struct spare *sp)
{
	VkSemaphoreGetFdInfoKHR info = {VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR, NULL, sp->handle, SYNC_FD};
	int fd = -1;
	return d->GetSemaphoreFdKHR(d->device, &info, &fd) == VK_SUCCESS ? fd : -1;
}

//! A semaphore operation of a batch, whichever submit command it came from.
struct op
{
	VkSemaphore semaphore;
	uint64_t value;
	VkPipelineStageFlags2 stages;
	uint32_t device_index;
};

struct publication
{
	struct mapping *map;
	uint64_t value;
	uint32_t batch;
};

//! How one submission is rewritten.
struct plan
{
	uint32_t count;
	struct op **waits, **signals; //!< per batch
	uint32_t *nwaits, *nsignals;
	struct spare **signal_spare; //!< per batch: stands in for its emulated signal operations
	struct spare **used;
	unsigned nused;
	struct publication *pubs;
	unsigned npubs;
	struct spare *done; //!< signalled by the last batch: the submission's fence
};

static bool
plan_init(struct plan *p, uint32_t count, uint32_t ops)
{
	memset(p, 0, sizeof(*p));
	p->count = count;
	p->waits = calloc(count, sizeof(*p->waits));
	p->signals = calloc(count, sizeof(*p->signals));
	p->nwaits = calloc(count, sizeof(*p->nwaits));
	p->nsignals = calloc(count, sizeof(*p->nsignals));
	p->signal_spare = calloc(count, sizeof(*p->signal_spare));
	p->used = calloc(ops + count + 1, sizeof(*p->used));
	p->pubs = calloc(ops + 1, sizeof(*p->pubs));
	return p->waits != NULL && p->signals != NULL && p->nwaits != NULL && p->nsignals != NULL &&
	       p->signal_spare != NULL && p->used != NULL && p->pubs != NULL;
}

static void
plan_free(struct plan *p)
{
	for (uint32_t i = 0; i < p->count; i++) {
		if (p->waits != NULL) {
			free(p->waits[i]);
		}
		if (p->signals != NULL) {
			free(p->signals[i]);
		}
	}
	for (unsigned i = 0; i < p->npubs; i++) {
		map_unref(p->pubs[i].map);
	}
	free(p->waits);
	free(p->signals);
	free(p->nwaits);
	free(p->nsignals);
	free(p->signal_spare);
	free(p->used);
	free(p->pubs);
}

/*!
 * Rewrites one batch: a wait for an emulated semaphore becomes a wait for a
 * binary semaphore with the awaited fence imported (or nothing when the value
 * is reached), its signal operations one binary semaphore whose fence is
 * published after the submission. The last batch also signals `done`.
 */
static bool
plan_batch(struct device *d, struct plan *p, uint32_t b, const struct op *waits, uint32_t nwaits,
           const struct op *signals, uint32_t nsignals)
{
	struct op *w = calloc(nwaits + 1, sizeof(*w));
	struct op *sg = calloc(nsignals + 2, sizeof(*sg));
	p->waits[b] = w;
	p->signals[b] = sg;
	if (w == NULL || sg == NULL) {
		return false;
	}
	uint32_t nw = 0, ns = 0;
	for (uint32_t i = 0; i < nwaits; i++) {
		struct mapping *m = shared_of(d->device, waits[i].semaphore);
		if (m == NULL) {
			w[nw++] = waits[i];
			continue;
		}
		// An earlier batch of this submission signals it: the queue's order covers the wait.
		bool earlier = false;
		for (unsigned k = 0; k < p->npubs && !earlier; k++) {
			earlier = p->pubs[k].map->s == m->s &&
			          (m->s->type == VK_SEMAPHORE_TYPE_BINARY || p->pubs[k].value >= waits[i].value);
		}
		if (earlier) {
			map_unref(m);
			continue;
		}
		int fd = -1;
		enum wait_need need = resolve(m, waits[i].value, &fd);
		map_unref(m);
		if (need == MISSING) {
			LOG("no signal operation for value %" PRIu64 " was submitted in time; not waiting", waits[i].value);
		}
		if (need != FENCE) {
			continue;
		}
		struct spare *sp = spare_get(d);
		VkImportSemaphoreFdInfoKHR import = {VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_FD_INFO_KHR,
		                                     NULL,
		                                     sp != NULL ? sp->handle : VK_NULL_HANDLE,
		                                     VK_SEMAPHORE_IMPORT_TEMPORARY_BIT,
		                                     SYNC_FD,
		                                     fd};
		if (sp == NULL || d->ImportSemaphoreFdKHR(d->device, &import) != VK_SUCCESS) {
			LOG("cannot import a fence; not waiting for value %" PRIu64, waits[i].value);
			close(fd);
			if (sp != NULL) {
				spare_put(sp);
			}
			continue;
		}
		p->used[p->nused++] = sp;
		w[nw++] = (struct op){sp->handle, 0, waits[i].stages, waits[i].device_index};
	}
	for (uint32_t i = 0; i < nsignals; i++) {
		struct mapping *m = shared_of(d->device, signals[i].semaphore);
		if (m == NULL) {
			sg[ns++] = signals[i];
			continue;
		}
		uint64_t value = signals[i].value;
		if (m->s->type == VK_SEMAPHORE_TYPE_BINARY) {
			lock_shared(m->s);
			value = ++m->s->signals;
			unlock_shared(m->s);
		}
		if (p->signal_spare[b] == NULL) {
			struct spare *sp = spare_get(d);
			if (sp == NULL) {
				LOG("no binary semaphore for a signal operation; value %" PRIu64 " counts as reached", value);
				reach_now(m->s, value);
				map_unref(m);
				continue;
			}
			p->signal_spare[b] = sp;
			p->used[p->nused++] = sp;
			sg[ns++] = (struct op){sp->handle, 0, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, signals[i].device_index};
		}
		p->pubs[p->npubs++] = (struct publication){m, value, b};
	}
	if (b + 1 == p->count && (p->done = spare_get(d)) != NULL) {
		p->used[p->nused++] = p->done;
		sg[ns++] = (struct op){p->done->handle, 0, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, 0};
	}
	p->nwaits[b] = nw;
	p->nsignals[b] = ns;
	return true;
}

//! After the submission: publishes the signal operations' fences and recycles the binary semaphores.
static void
plan_finish(struct device *d, struct plan *p, bool submitted)
{
	int done = -1;
	if (submitted) {
		for (uint32_t b = 0; b < p->count; b++) {
			if (p->signal_spare[b] == NULL) {
				continue;
			}
			int fd = export_sync_file(d, p->signal_spare[b]);
			for (unsigned i = 0; i < p->npubs; i++) {
				if (p->pubs[i].batch == b) {
					publish(p->pubs[i].map, p->pubs[i].value, fd >= 0 ? dup_cloexec(fd) : -1);
				}
			}
			if (fd >= 0) {
				close(fd);
			}
		}
		if (p->done != NULL) {
			done = export_sync_file(d, p->done);
		}
	}
	pthread_mutex_lock(&g_lock);
	for (unsigned i = 0; i < p->nused; i++) {
		p->used[i]->busy = done >= 0 ? dup_cloexec(done) : -1;
		p->used[i]->used = false;
	}
	pthread_mutex_unlock(&g_lock);
	if (done >= 0) {
		close(done);
	}
	plan_free(p);
}

static bool
any_emulated(struct device *d, const VkSemaphore *a, uint32_t n)
{
	for (uint32_t i = 0; i < n; i++) {
		struct mapping *m = shared_of(d->device, a[i]);
		if (m != NULL) {
			map_unref(m);
			return true;
		}
	}
	return false;
}

static bool
any_emulated2(struct device *d, const VkSemaphoreSubmitInfo *a, uint32_t n)
{
	for (uint32_t i = 0; i < n; i++) {
		struct mapping *m = shared_of(d->device, a[i].semaphore);
		if (m != NULL) {
			map_unref(m);
			return true;
		}
	}
	return false;
}

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_QueueSubmit2(VkQueue queue, uint32_t count, const VkSubmitInfo2 *submits, VkFence fence)
{
	struct device *d = find_device(queue);
	bool emulated = false;
	uint32_t ops = 0;
	for (uint32_t i = 0; d->emulate && have_sems() && i < count; i++) {
		emulated = emulated ||
		           any_emulated2(d, submits[i].pWaitSemaphoreInfos, submits[i].waitSemaphoreInfoCount) ||
		           any_emulated2(d, submits[i].pSignalSemaphoreInfos, submits[i].signalSemaphoreInfoCount);
		ops += submits[i].waitSemaphoreInfoCount + submits[i].signalSemaphoreInfoCount;
	}
	if (!emulated) {
		return d->QueueSubmit2(queue, count, submits, fence);
	}
	struct plan p;
	VkSubmitInfo2 *out = calloc(count, sizeof(*out));
	VkSemaphoreSubmitInfo **infos = calloc(count, sizeof(*infos));
	bool planned = out != NULL && infos != NULL && plan_init(&p, count, ops);
	bool ok = planned;
	for (uint32_t b = 0; ok && b < count; b++) {
		const VkSubmitInfo2 *in = &submits[b];
		uint32_t nw = in->waitSemaphoreInfoCount, ns = in->signalSemaphoreInfoCount;
		struct op *src = calloc(nw + ns + 1, sizeof(*src));
		ok = src != NULL;
		for (uint32_t i = 0; ok && i < nw + ns; i++) {
			const VkSemaphoreSubmitInfo *s = i < nw ? &in->pWaitSemaphoreInfos[i] : &in->pSignalSemaphoreInfos[i - nw];
			src[i] = (struct op){s->semaphore, s->value, s->stageMask, s->deviceIndex};
		}
		ok = ok && plan_batch(d, &p, b, src, nw, src + nw, ns);
		free(src);
		if (!ok) {
			break;
		}
		nw = p.nwaits[b];
		ns = p.nsignals[b];
		infos[b] = calloc(nw + ns + 1, sizeof(**infos));
		ok = infos[b] != NULL;
		for (uint32_t i = 0; ok && i < nw + ns; i++) {
			const struct op *o = i < nw ? &p.waits[b][i] : &p.signals[b][i - nw];
			infos[b][i] = (VkSemaphoreSubmitInfo){VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, NULL, o->semaphore,
			                                      o->value, o->stages, o->device_index};
		}
		if (ok) {
			out[b] = *in;
			out[b].waitSemaphoreInfoCount = nw;
			out[b].pWaitSemaphoreInfos = infos[b];
			out[b].signalSemaphoreInfoCount = ns;
			out[b].pSignalSemaphoreInfos = infos[b] + nw;
		}
	}
	VkResult ret = ok ? d->QueueSubmit2(queue, count, out, fence) : VK_ERROR_OUT_OF_HOST_MEMORY;
	if (planned) {
		plan_finish(d, &p, ret == VK_SUCCESS);
	}
	for (uint32_t b = 0; infos != NULL && b < count; b++) {
		free(infos[b]);
	}
	free(infos);
	free(out);
	return ret;
}

//! Per batch of vkQueueSubmit: the rebuilt arrays and chained structures.
struct batch1
{
	VkSemaphore *semaphores; //!< waits, then signals
	VkPipelineStageFlags *stages;
	uint64_t *values;         //!< waits, then signals
	uint32_t *device_indices; //!< waits, then signals
	VkTimelineSemaphoreSubmitInfo timeline;
	VkDeviceGroupSubmitInfo group;
	VkProtectedSubmitInfo protect;
	VkPerformanceQuerySubmitInfoKHR query;
};

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_QueueSubmit(VkQueue queue, uint32_t count, const VkSubmitInfo *submits, VkFence fence)
{
	struct device *d = find_device(queue);
	bool emulated = false;
	uint32_t ops = 0;
	for (uint32_t i = 0; d->emulate && have_sems() && i < count; i++) {
		emulated = emulated || any_emulated(d, submits[i].pWaitSemaphores, submits[i].waitSemaphoreCount) ||
		           any_emulated(d, submits[i].pSignalSemaphores, submits[i].signalSemaphoreCount);
		ops += submits[i].waitSemaphoreCount + submits[i].signalSemaphoreCount;
	}
	if (!emulated) {
		return d->QueueSubmit(queue, count, submits, fence);
	}
	// Only these chained structures can be rebuilt around the new semaphore lists.
	for (uint32_t b = 0; b < count; b++) {
		for (const VkBaseInStructure *s = submits[b].pNext; s != NULL; s = s->pNext) {
			if (s->sType != VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO &&
			    s->sType != VK_STRUCTURE_TYPE_DEVICE_GROUP_SUBMIT_INFO &&
			    s->sType != VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO &&
			    s->sType != VK_STRUCTURE_TYPE_PERFORMANCE_QUERY_SUBMIT_INFO_KHR) {
				LOG("vkQueueSubmit with structure %d: emulated semaphores left to the driver", (int)s->sType);
				return d->QueueSubmit(queue, count, submits, fence);
			}
		}
	}
	struct plan p;
	VkSubmitInfo *out = calloc(count, sizeof(*out));
	struct batch1 *bb = calloc(count, sizeof(*bb));
	bool planned = out != NULL && bb != NULL && plan_init(&p, count, ops);
	bool ok = planned;
	for (uint32_t b = 0; ok && b < count; b++) {
		const VkSubmitInfo *in = &submits[b];
		const VkTimelineSemaphoreSubmitInfo *timeline = NULL;
		const VkDeviceGroupSubmitInfo *group = NULL;
		const VkProtectedSubmitInfo *protect = NULL;
		const VkPerformanceQuerySubmitInfoKHR *query = NULL;
		for (const VkBaseInStructure *s = in->pNext; s != NULL; s = s->pNext) {
			if (s->sType == VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO) {
				timeline = (const VkTimelineSemaphoreSubmitInfo *)s;
			} else if (s->sType == VK_STRUCTURE_TYPE_DEVICE_GROUP_SUBMIT_INFO) {
				group = (const VkDeviceGroupSubmitInfo *)s;
			} else if (s->sType == VK_STRUCTURE_TYPE_PROTECTED_SUBMIT_INFO) {
				protect = (const VkProtectedSubmitInfo *)s;
			} else {
				query = (const VkPerformanceQuerySubmitInfoKHR *)s;
			}
		}
		uint32_t nw = in->waitSemaphoreCount, ns = in->signalSemaphoreCount;
		struct op *src = calloc(nw + ns + 1, sizeof(*src));
		ok = src != NULL;
		for (uint32_t i = 0; ok && i < nw; i++) {
			src[i].semaphore = in->pWaitSemaphores[i];
			src[i].value = timeline != NULL && i < timeline->waitSemaphoreValueCount ? timeline->pWaitSemaphoreValues[i] : 0;
			src[i].stages = in->pWaitDstStageMask[i];
			src[i].device_index =
			    group != NULL && i < group->waitSemaphoreCount ? group->pWaitSemaphoreDeviceIndices[i] : 0;
		}
		for (uint32_t i = 0; ok && i < ns; i++) {
			struct op *o = &src[nw + i];
			o->semaphore = in->pSignalSemaphores[i];
			o->value =
			    timeline != NULL && i < timeline->signalSemaphoreValueCount ? timeline->pSignalSemaphoreValues[i] : 0;
			o->stages = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
			o->device_index =
			    group != NULL && i < group->signalSemaphoreCount ? group->pSignalSemaphoreDeviceIndices[i] : 0;
		}
		ok = ok && plan_batch(d, &p, b, src, nw, src + nw, ns);
		free(src);
		if (!ok) {
			break;
		}
		nw = p.nwaits[b];
		ns = p.nsignals[b];
		struct batch1 *o = &bb[b];
		o->semaphores = calloc(nw + ns + 1, sizeof(*o->semaphores));
		o->stages = calloc(nw + 1, sizeof(*o->stages));
		o->values = calloc(nw + ns + 1, sizeof(*o->values));
		o->device_indices = calloc(nw + ns + 1, sizeof(*o->device_indices));
		ok = o->semaphores != NULL && o->stages != NULL && o->values != NULL && o->device_indices != NULL;
		for (uint32_t i = 0; ok && i < nw + ns; i++) {
			const struct op *x = i < nw ? &p.waits[b][i] : &p.signals[b][i - nw];
			o->semaphores[i] = x->semaphore;
			o->values[i] = x->value;
			o->device_indices[i] = x->device_index;
			if (i < nw) {
				o->stages[i] = (x->stages >> 32) != 0 ? VK_PIPELINE_STAGE_ALL_COMMANDS_BIT
				                                      : (VkPipelineStageFlags)x->stages;
			}
		}
		if (!ok) {
			break;
		}
		const void *chain = NULL;
		if (query != NULL) {
			o->query = *query;
			o->query.pNext = chain;
			chain = &o->query;
		}
		if (protect != NULL) {
			o->protect = *protect;
			o->protect.pNext = chain;
			chain = &o->protect;
		}
		if (group != NULL) {
			o->group = *group;
			o->group.pNext = chain;
			o->group.waitSemaphoreCount = nw;
			o->group.pWaitSemaphoreDeviceIndices = o->device_indices;
			o->group.signalSemaphoreCount = ns;
			o->group.pSignalSemaphoreDeviceIndices = o->device_indices + nw;
			chain = &o->group;
		}
		o->timeline = (VkTimelineSemaphoreSubmitInfo){
		    VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO, chain, nw, o->values, ns, o->values + nw};
		out[b] = *in;
		out[b].pNext = &o->timeline;
		out[b].waitSemaphoreCount = nw;
		out[b].pWaitSemaphores = o->semaphores;
		out[b].pWaitDstStageMask = o->stages;
		out[b].signalSemaphoreCount = ns;
		out[b].pSignalSemaphores = o->semaphores + nw;
	}
	VkResult ret = ok ? d->QueueSubmit(queue, count, out, fence) : VK_ERROR_OUT_OF_HOST_MEMORY;
	if (planned) {
		plan_finish(d, &p, ret == VK_SUCCESS);
	}
	for (uint32_t b = 0; bb != NULL && b < count; b++) {
		free(bb[b].semaphores);
		free(bb[b].stages);
		free(bb[b].values);
		free(bb[b].device_indices);
	}
	free(bb);
	free(out);
	return ret;
}


/*
 *
 * Semaphore commands.
 *
 */

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_CreateSemaphore(VkDevice device,
                       const VkSemaphoreCreateInfo *info,
                       const VkAllocationCallbacks *alloc,
                       VkSemaphore *semaphore)
{
	struct device *d = find_device(device);
	const VkExportSemaphoreCreateInfo *export = NULL;
	const VkSemaphoreTypeCreateInfo *type = NULL;
	bool other = false;
	for (const VkBaseInStructure *s = info->pNext; s != NULL; s = s->pNext) {
		if (s->sType == VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO) {
			export = (const VkExportSemaphoreCreateInfo *)s;
		} else if (s->sType == VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO) {
			type = (const VkSemaphoreTypeCreateInfo *)s;
		} else {
			other = true;
		}
	}
	if (!d->emulate || export == NULL || !(export->handleTypes & OPAQUE_FD)) {
		return d->CreateSemaphore(device, info, alloc, semaphore);
	}
	if (other) {
		LOG("vkCreateSemaphore with unknown structures: OPAQUE_FD left to the driver");
		return d->CreateSemaphore(device, info, alloc, semaphore);
	}
	// The driver's semaphore stays the handle; its payload is the shared state.
	VkSemaphoreTypeCreateInfo type_copy;
	VkExportSemaphoreCreateInfo export_copy;
	VkSemaphoreCreateInfo copy = *info;
	const void *chain = NULL;
	if (type != NULL) {
		type_copy = *type;
		type_copy.pNext = chain;
		chain = &type_copy;
	}
	if ((export->handleTypes & ~OPAQUE_FD) != 0) {
		export_copy = *export;
		export_copy.handleTypes &= ~OPAQUE_FD;
		export_copy.pNext = chain;
		chain = &export_copy;
	}
	copy.pNext = chain;
	VkResult ret = d->CreateSemaphore(device, &copy, alloc, semaphore);
	if (ret != VK_SUCCESS) {
		return ret;
	}
	VkSemaphoreType t = type != NULL ? type->semaphoreType : VK_SEMAPHORE_TYPE_BINARY;
	struct mapping *m = map_create(t, t == VK_SEMAPHORE_TYPE_TIMELINE ? type->initialValue : 0);
	if (m == NULL || !sem_set(device, *semaphore, m)) {
		map_unref(m);
		d->DestroySemaphore(device, *semaphore, alloc);
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	DBG("emulated %s semaphore %p", t == VK_SEMAPHORE_TYPE_TIMELINE ? "timeline" : "binary", (void *)*semaphore);
	return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL
vrsync_DestroySemaphore(VkDevice device, VkSemaphore semaphore, const VkAllocationCallbacks *alloc)
{
	struct device *d = find_device(device);
	sem_remove(device, semaphore);
	d->DestroySemaphore(device, semaphore, alloc);
}

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_GetSemaphoreFdKHR(VkDevice device, const VkSemaphoreGetFdInfoKHR *info, int *fd)
{
	struct device *d = find_device(device);
	if (info->semaphore == VK_NULL_HANDLE) {
		// Turnip dereferences it: an application that failed to create the semaphore gets an error instead.
		return VK_ERROR_TOO_MANY_OBJECTS;
	}
	struct mapping *m = shared_of(device, info->semaphore);
	if (m == NULL) {
		return d->GetSemaphoreFdKHR(device, info, fd);
	}
	VkResult ret = VK_SUCCESS;
	if (info->handleType == OPAQUE_FD) {
		*fd = dup_cloexec(m->fd);
		ret = *fd >= 0 ? VK_SUCCESS : VK_ERROR_TOO_MANY_OBJECTS;
	} else if (info->handleType == SYNC_FD && m->s->type == VK_SEMAPHORE_TYPE_BINARY) {
		int f = -1;
		*fd = resolve(m, 0, &f) == FENCE ? f : -1; // -1: signalled already
	} else {
		ret = VK_ERROR_INVALID_EXTERNAL_HANDLE;
	}
	map_unref(m);
	return ret;
}

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_ImportSemaphoreFdKHR(VkDevice device, const VkImportSemaphoreFdInfoKHR *info)
{
	struct device *d = find_device(device);
	if (!d->emulate || info->handleType != OPAQUE_FD) {
		return d->ImportSemaphoreFdKHR(device, info);
	}
	struct mapping *m = map_fd(info->fd);
	if (m == NULL) {
		return d->ImportSemaphoreFdKHR(device, info); // not this module's: the driver decides
	}
	if (info->flags & VK_SEMAPHORE_IMPORT_TEMPORARY_BIT) {
		DBG("temporary OPAQUE_FD import made permanent");
	}
	if (!sem_set(device, info->semaphore, m)) {
		m->fd = -1; // the descriptor stays the application's
		map_unref(m);
		return VK_ERROR_OUT_OF_HOST_MEMORY;
	}
	DBG("imported semaphore %p", (void *)info->semaphore);
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_GetSemaphoreCounterValue(VkDevice device, VkSemaphore semaphore, uint64_t *value)
{
	struct device *d = find_device(device);
	struct mapping *m = shared_of(device, semaphore);
	if (m == NULL) {
		return d->GetSemaphoreCounterValue(device, semaphore, value);
	}
	lock_shared(m->s);
	*value = m->s->value;
	unlock_shared(m->s);
	map_unref(m);
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_SignalSemaphore(VkDevice device, const VkSemaphoreSignalInfo *info)
{
	struct device *d = find_device(device);
	struct mapping *m = shared_of(device, info->semaphore);
	if (m == NULL) {
		return d->SignalSemaphore(device, info);
	}
	reach_now(m->s, info->value);
	map_unref(m);
	return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL
vrsync_WaitSemaphores(VkDevice device, const VkSemaphoreWaitInfo *info, uint64_t timeout)
{
	struct device *d = find_device(device);
	uint32_t n = info->semaphoreCount;
	struct mapping **maps = have_sems() ? calloc(n + 1, sizeof(*maps)) : NULL;
	bool any = false;
	for (uint32_t i = 0; maps != NULL && i < n; i++) {
		maps[i] = shared_of(device, info->pSemaphores[i]);
		any = any || maps[i] != NULL;
	}
	if (!any) {
		free(maps);
		return d->WaitSemaphores(device, info, timeout);
	}
	bool wait_any = (info->flags & VK_SEMAPHORE_WAIT_ANY_BIT) != 0;
	int64_t start = now_ns();
	int64_t deadline = timeout > (uint64_t)(INT64_MAX - start) ? INT64_MAX : start + (int64_t)timeout;
	int64_t check_exited = start + 100 * NS_PER_MS;
	VkResult ret;
	for (;;) {
		uint32_t reached = 0, seq = 0;
		struct shared *block = NULL;
		bool driver = false;
		ret = VK_SUCCESS;
		for (uint32_t i = 0; i < n && ret == VK_SUCCESS; i++) {
			bool ok;
			if (maps[i] != NULL) {
				struct shared *s = maps[i]->s;
				lock_shared(s);
				ok = s->value >= info->pValues[i];
				if (!ok && block == NULL) {
					block = s;
					seq = s->seq;
				}
				unlock_shared(s);
			} else {
				uint64_t v = 0;
				ret = d->GetSemaphoreCounterValue(device, info->pSemaphores[i], &v);
				ok = v >= info->pValues[i];
				driver = driver || !ok;
			}
			reached += ok;
		}
		if (ret != VK_SUCCESS || (wait_any ? reached > 0 : reached == n)) {
			break;
		}
		int64_t now = now_ns();
		if (now >= deadline) {
			ret = VK_TIMEOUT;
			break;
		}
		if (now >= check_exited) {
			for (uint32_t i = 0; i < n; i++) {
				if (maps[i] != NULL) {
					settle_exited(maps[i]->s);
				}
			}
			check_exited = now + 100 * NS_PER_MS;
		}
		// Driver semaphores are polled; emulated ones wake the futex.
		int64_t slice = deadline - now, cap = driver || block == NULL ? NS_PER_MS : 50 * NS_PER_MS;
		slice = slice < cap ? slice : cap;
		if (block != NULL) {
			futex_wait(&block->seq, seq, slice);
		} else {
			struct timespec ts = {0, slice};
			nanosleep(&ts, NULL);
		}
	}
	// A slow wait on an emulated semaphore: what it waited for and who was to signal it.
	int64_t waited = now_ns() - start;
	if (ret == VK_TIMEOUT || waited > 250 * NS_PER_MS) {
		for (uint32_t i = 0; i < n; i++) {
			if (maps[i] == NULL) {
				continue;
			}
			struct shared *s = maps[i]->s;
			lock_shared(s);
			uint64_t reached = s->value;
			unsigned pending = 0;
			int32_t first = 0;
			uint64_t first_value = 0;
			for (unsigned k = 0; k < POINTS; k++) {
				if (s->points[k].value != 0) {
					if (pending++ == 0 || s->points[k].value < first_value) {
						first = s->points[k].pid;
						first_value = s->points[k].value;
					}
				}
			}
			unlock_shared(s);
			LOG("vkWaitSemaphores: %s after %.0f ms (timeout %.0f ms): value %" PRIu64 " of %p, reached %" PRIu64
			    ", %u signal operations pending, the first (%" PRIu64 ") held by %d",
			    ret == VK_TIMEOUT ? "timed out" : "done", waited / 1e6, timeout / 1e6, info->pValues[i],
			    (void *)info->pSemaphores[i], reached, pending, first_value, first);
		}
	}
	for (uint32_t i = 0; i < n; i++) {
		map_unref(maps[i]);
	}
	free(maps);
	return ret;
}


/*
 *
 * Lifecycle and dispatch.
 *
 */

//! The driver cannot export OPAQUE_FD timeline semaphores but can import and export sync files.
static bool
can_emulate(struct instance *in, VkPhysicalDevice physical)
{
	const char *env = getenv("PICO4_VRDISPLAY_SYNC");
	if (in == NULL || in->GetPhysicalDeviceExternalSemaphoreProperties == NULL ||
	    (env != NULL && strcmp(env, "off") == 0)) {
		return false;
	}
	VkSemaphoreTypeCreateInfo timeline = {VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO, NULL,
	                                      VK_SEMAPHORE_TYPE_TIMELINE, 0};
	VkPhysicalDeviceExternalSemaphoreInfo query = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO,
	                                               &timeline, OPAQUE_FD};
	VkExternalSemaphoreProperties props = {VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES, NULL, 0, 0, 0};
	in->GetPhysicalDeviceExternalSemaphoreProperties(physical, &query, &props);
	if (props.externalSemaphoreFeatures & VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT) {
		return false; // the driver shares semaphores itself
	}
	query.pNext = NULL;
	query.handleType = SYNC_FD;
	props = (VkExternalSemaphoreProperties){VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES, NULL, 0, 0, 0};
	in->GetPhysicalDeviceExternalSemaphoreProperties(physical, &query, &props);
	VkExternalSemaphoreFeatureFlags both =
	    VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT | VK_EXTERNAL_SEMAPHORE_FEATURE_IMPORTABLE_BIT;
	return (props.externalSemaphoreFeatures & both) == both;
}

static VKAPI_ATTR void VKAPI_CALL
vrsync_GetPhysicalDeviceExternalSemaphoreProperties(VkPhysicalDevice physical,
                                                    const VkPhysicalDeviceExternalSemaphoreInfo *info,
                                                    VkExternalSemaphoreProperties *props)
{
	struct instance *in = find_instance(physical);
	in->GetPhysicalDeviceExternalSemaphoreProperties(physical, info, props);
	if (info->handleType == OPAQUE_FD &&
	    !(props->externalSemaphoreFeatures & VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT) &&
	    can_emulate(in, physical)) {
		props->exportFromImportedHandleTypes = OPAQUE_FD;
		props->compatibleHandleTypes = OPAQUE_FD;
		props->externalSemaphoreFeatures =
		    VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT | VK_EXTERNAL_SEMAPHORE_FEATURE_IMPORTABLE_BIT;
	}
}

void
vrsync_instance_created(VkInstance instance, PFN_vkGetInstanceProcAddr gipa)
{
	struct instance *in = calloc(1, sizeof(*in));
	if (in == NULL) {
		return;
	}
	in->key = key_of(instance);
	in->GetPhysicalDeviceExternalSemaphoreProperties = (PFN_vkGetPhysicalDeviceExternalSemaphoreProperties)gipa(
	    instance, "vkGetPhysicalDeviceExternalSemaphoreProperties");
	if (in->GetPhysicalDeviceExternalSemaphoreProperties == NULL) {
		in->GetPhysicalDeviceExternalSemaphoreProperties = (PFN_vkGetPhysicalDeviceExternalSemaphoreProperties)gipa(
		    instance, "vkGetPhysicalDeviceExternalSemaphorePropertiesKHR");
	}
	pthread_mutex_lock(&g_lock);
	in->next = g_instances;
	g_instances = in;
	pthread_mutex_unlock(&g_lock);
}

void
vrsync_instance_destroyed(VkInstance instance)
{
	void *key = key_of(instance);
	struct instance *gone = NULL;
	pthread_mutex_lock(&g_lock);
	for (struct instance **p = &g_instances; *p != NULL; p = &(*p)->next) {
		if ((*p)->key == key) {
			gone = *p;
			*p = gone->next;
			break;
		}
	}
	pthread_mutex_unlock(&g_lock);
	free(gone);
}

static PFN_vkVoidFunction
either(VkDevice device, PFN_vkGetDeviceProcAddr gdpa, const char *core, const char *khr)
{
	PFN_vkVoidFunction f = gdpa(device, core);
	return f != NULL ? f : gdpa(device, khr);
}

void
vrsync_device_created(VkPhysicalDevice physical, VkDevice device, PFN_vkGetDeviceProcAddr gdpa)
{
	struct device *d = calloc(1, sizeof(*d));
	if (d == NULL) {
		return;
	}
	d->key = key_of(device);
	d->device = device;
	d->CreateSemaphore = (PFN_vkCreateSemaphore)gdpa(device, "vkCreateSemaphore");
	d->DestroySemaphore = (PFN_vkDestroySemaphore)gdpa(device, "vkDestroySemaphore");
	d->GetSemaphoreFdKHR = (PFN_vkGetSemaphoreFdKHR)gdpa(device, "vkGetSemaphoreFdKHR");
	d->ImportSemaphoreFdKHR = (PFN_vkImportSemaphoreFdKHR)gdpa(device, "vkImportSemaphoreFdKHR");
	d->QueueSubmit = (PFN_vkQueueSubmit)gdpa(device, "vkQueueSubmit");
	d->QueueSubmit2 = (PFN_vkQueueSubmit2)either(device, gdpa, "vkQueueSubmit2", "vkQueueSubmit2KHR");
	d->WaitSemaphores = (PFN_vkWaitSemaphores)either(device, gdpa, "vkWaitSemaphores", "vkWaitSemaphoresKHR");
	d->SignalSemaphore = (PFN_vkSignalSemaphore)either(device, gdpa, "vkSignalSemaphore", "vkSignalSemaphoreKHR");
	d->GetSemaphoreCounterValue = (PFN_vkGetSemaphoreCounterValue)either(
	    device, gdpa, "vkGetSemaphoreCounterValue", "vkGetSemaphoreCounterValueKHR");
	d->emulate = d->GetSemaphoreFdKHR != NULL && d->ImportSemaphoreFdKHR != NULL &&
	             can_emulate(find_instance(physical), physical);
	pthread_mutex_lock(&g_lock);
	d->next = g_devices;
	g_devices = d;
	pthread_mutex_unlock(&g_lock);
	DBG("device %p: %s", (void *)device,
	    d->emulate ? "OPAQUE_FD semaphores emulated" : "semaphores left to the driver");
}

void
vrsync_device_destroyed(VkDevice device)
{
	void *key = key_of(device);
	struct device *d = NULL;
	struct sem *sems = NULL;
	pthread_mutex_lock(&g_lock);
	for (struct device **p = &g_devices; *p != NULL; p = &(*p)->next) {
		if ((*p)->key == key) {
			d = *p;
			*p = d->next;
			break;
		}
	}
	for (struct sem **p = &g_sems; *p != NULL;) {
		struct sem *e = *p;
		if (e->device == device) {
			*p = e->next;
			e->next = sems;
			sems = e;
		} else {
			p = &e->next;
		}
	}
	pthread_mutex_unlock(&g_lock);
	while (sems != NULL) {
		struct sem *e = sems;
		sems = e->next;
		map_unref(e->map);
		free(e);
	}
	while (d != NULL && d->spares != NULL) {
		struct spare *sp = d->spares;
		d->spares = sp->next;
		d->DestroySemaphore(device, sp->handle, NULL);
		if (sp->busy >= 0) {
			close(sp->busy);
		}
		free(sp);
	}
	free(d);
}

PFN_vkVoidFunction
vrsync_device_function(const char *name)
{
	static const struct
	{
		const char *name;
		PFN_vkVoidFunction fn;
	} k_functions[] = {
	    {"vkCreateSemaphore", (PFN_vkVoidFunction)vrsync_CreateSemaphore},
	    {"vkDestroySemaphore", (PFN_vkVoidFunction)vrsync_DestroySemaphore},
	    {"vkGetSemaphoreFdKHR", (PFN_vkVoidFunction)vrsync_GetSemaphoreFdKHR},
	    {"vkImportSemaphoreFdKHR", (PFN_vkVoidFunction)vrsync_ImportSemaphoreFdKHR},
	    {"vkQueueSubmit", (PFN_vkVoidFunction)vrsync_QueueSubmit},
	    {"vkQueueSubmit2", (PFN_vkVoidFunction)vrsync_QueueSubmit2},
	    {"vkQueueSubmit2KHR", (PFN_vkVoidFunction)vrsync_QueueSubmit2},
	    {"vkWaitSemaphores", (PFN_vkVoidFunction)vrsync_WaitSemaphores},
	    {"vkWaitSemaphoresKHR", (PFN_vkVoidFunction)vrsync_WaitSemaphores},
	    {"vkSignalSemaphore", (PFN_vkVoidFunction)vrsync_SignalSemaphore},
	    {"vkSignalSemaphoreKHR", (PFN_vkVoidFunction)vrsync_SignalSemaphore},
	    {"vkGetSemaphoreCounterValue", (PFN_vkVoidFunction)vrsync_GetSemaphoreCounterValue},
	    {"vkGetSemaphoreCounterValueKHR", (PFN_vkVoidFunction)vrsync_GetSemaphoreCounterValue},
	};
	for (size_t i = 0; i < sizeof(k_functions) / sizeof(k_functions[0]); i++) {
		if (strcmp(name, k_functions[i].name) == 0) {
			return k_functions[i].fn;
		}
	}
	return NULL;
}

PFN_vkVoidFunction
vrsync_instance_function(const char *name)
{
	if (strcmp(name, "vkGetPhysicalDeviceExternalSemaphoreProperties") == 0 ||
	    strcmp(name, "vkGetPhysicalDeviceExternalSemaphorePropertiesKHR") == 0) {
		return (PFN_vkVoidFunction)vrsync_GetPhysicalDeviceExternalSemaphoreProperties;
	}
	return NULL;
}
