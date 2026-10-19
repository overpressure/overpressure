// SPDX-License-Identifier: BSL-1.0
/*
 * Two processes share a timeline semaphore as an OPAQUE_FD handle, the way
 * SteamVR's compositor shares them with its clients (SESSION.md). On Turnip's
 * KGSL backend this exercises VK_LAYER_PICO4_vrdisplay's emulation
 * (pico4-vrsync.c); run it with PICO4_VRDISPLAY=1.
 *
 *   pico4-vrsync-test
 *
 * The parent exports the semaphore, the child imports it. Checks:
 *   gpu_wait     the child's batch waits for the parent's GPU signal (value 1):
 *                its start timestamp follows the end of the parent's work
 *   host_wait    each process sees the other's GPU signals (values 1 and 2)
 *   host_signal  a host signal (value 3) reaches the other process
 *   early_wait   a wait submitted before its signal operation (value 4) holds
 *                until the signal arrives, then signals value 5
 *   same_submit  one submission whose second batch waits for the first one's
 *                signal (values 6 and 7) completes at once
 * The parent uses vkQueueSubmit, the child vkQueueSubmit2. One JSON object on
 * stdout; exit status 0 only if every check passed.
 */
#define _GNU_SOURCE
#include <vulkan/vulkan.h>

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define FILL_BYTES (64u << 20)
#define FILLS 24
#define TIMEOUT_NS 5000000000ull

struct gpu
{
	VkInstance instance;
	VkPhysicalDevice physical;
	VkDevice device;
	VkQueue queue;
	uint32_t family;
	VkCommandPool pool;
	VkBuffer buffer;
	VkDeviceMemory memory;
	VkQueryPool queries;
	PFN_vkGetSemaphoreFdKHR GetSemaphoreFdKHR;
	PFN_vkImportSemaphoreFdKHR ImportSemaphoreFdKHR;
};

static const char *g_role = "parent";

static void
fail(const char *what, VkResult r)
{
	fprintf(stderr, "pico4-vrsync-test %s: %s failed (%d)\n", g_role, what, r);
	exit(2);
}

#define CHECK(call)                                                                                              \
	do {                                                                                                     \
		VkResult r_ = (call);                                                                            \
		if (r_ != VK_SUCCESS) {                                                                          \
			fail(#call, r_);                                                                         \
		}                                                                                                \
	} while (0)

static int64_t
now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void
gpu_init(struct gpu *g)
{
	VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "pico4-vrsync-test", 1, NULL, 0,
	                         VK_API_VERSION_1_3};
	VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL};
	CHECK(vkCreateInstance(&ici, NULL, &g->instance));
	uint32_t n = 1;
	VkResult r = vkEnumeratePhysicalDevices(g->instance, &n, &g->physical);
	if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || n == 0) {
		fail("vkEnumeratePhysicalDevices", r);
	}
	g->family = 0;
	float priority = 1.0f;
	VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, g->family, 1, &priority};
	VkPhysicalDeviceVulkan13Features f13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
	                                        .synchronization2 = VK_TRUE};
	VkPhysicalDeviceVulkan12Features f12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
	                                        .pNext = &f13,
	                                        .timelineSemaphore = VK_TRUE,
	                                        .hostQueryReset = VK_TRUE};
	const char *ext[] = {VK_KHR_EXTERNAL_SEMAPHORE_FD_EXTENSION_NAME};
	VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &f12, 0, 1, &qci, 0, NULL, 1, ext, NULL};
	CHECK(vkCreateDevice(g->physical, &dci, NULL, &g->device));
	vkGetDeviceQueue(g->device, g->family, 0, &g->queue);
	g->GetSemaphoreFdKHR = (PFN_vkGetSemaphoreFdKHR)vkGetDeviceProcAddr(g->device, "vkGetSemaphoreFdKHR");
	g->ImportSemaphoreFdKHR = (PFN_vkImportSemaphoreFdKHR)vkGetDeviceProcAddr(g->device, "vkImportSemaphoreFdKHR");
	if (g->GetSemaphoreFdKHR == NULL || g->ImportSemaphoreFdKHR == NULL) {
		fail("VK_KHR_external_semaphore_fd", VK_ERROR_EXTENSION_NOT_PRESENT);
	}
	VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL,
	                               VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, g->family};
	CHECK(vkCreateCommandPool(g->device, &pci, NULL, &g->pool));
	VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, FILL_BYTES,
	                          VK_BUFFER_USAGE_TRANSFER_DST_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
	CHECK(vkCreateBuffer(g->device, &bci, NULL, &g->buffer));
	VkMemoryRequirements req;
	vkGetBufferMemoryRequirements(g->device, g->buffer, &req);
	VkPhysicalDeviceMemoryProperties mp;
	vkGetPhysicalDeviceMemoryProperties(g->physical, &mp);
	uint32_t type = 0;
	while (type < mp.memoryTypeCount && !(req.memoryTypeBits & (1u << type))) {
		type++;
	}
	VkMemoryAllocateInfo mai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, req.size, type};
	CHECK(vkAllocateMemory(g->device, &mai, NULL, &g->memory));
	CHECK(vkBindBufferMemory(g->device, g->buffer, g->memory, 0));
	VkQueryPoolCreateInfo qpci = {VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO, NULL, 0, VK_QUERY_TYPE_TIMESTAMP, 2, 0};
	CHECK(vkCreateQueryPool(g->device, &qpci, NULL, &g->queries));
	vkResetQueryPool(g->device, g->queries, 0, 2);
}

//! A command buffer: optionally a timestamp first, `fills` fills of the buffer, optionally a timestamp last.
static VkCommandBuffer
record(struct gpu *g, int stamp_first, unsigned fills, int stamp_last)
{
	VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, g->pool,
	                                   VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
	VkCommandBuffer cb;
	CHECK(vkAllocateCommandBuffers(g->device, &cai, &cb));
	VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL,
	                               VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, NULL};
	CHECK(vkBeginCommandBuffer(cb, &bi));
	if (stamp_first >= 0) {
		vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, g->queries, (uint32_t)stamp_first);
	}
	for (unsigned i = 0; i < fills; i++) {
		vkCmdFillBuffer(cb, g->buffer, 0, VK_WHOLE_SIZE, i);
		VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_TRANSFER_WRITE_BIT,
		                      VK_ACCESS_TRANSFER_WRITE_BIT};
		vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0, NULL,
		                     0, NULL);
	}
	if (stamp_last >= 0) {
		vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, g->queries, (uint32_t)stamp_last);
	}
	CHECK(vkEndCommandBuffer(cb));
	return cb;
}

static uint64_t
timestamp(struct gpu *g, uint32_t query)
{
	uint64_t v = 0;
	CHECK(vkGetQueryPoolResults(g->device, g->queries, query, 1, sizeof(v), &v, sizeof(v),
	                            VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT));
	return v;
}

static VkSemaphore
timeline(struct gpu *g, bool exportable)
{
	VkExportSemaphoreCreateInfo export = {VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO, NULL,
	                                      VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT};
	VkSemaphoreTypeCreateInfo type = {VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO, exportable ? &export : NULL,
	                                  VK_SEMAPHORE_TYPE_TIMELINE, 0};
	VkSemaphoreCreateInfo info = {VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, &type, 0};
	VkSemaphore s;
	CHECK(vkCreateSemaphore(g->device, &info, NULL, &s));
	return s;
}

//! vkQueueSubmit (parent): one batch with an optional wait and a signal.
static void
submit1(struct gpu *g, VkCommandBuffer cb, VkSemaphore s, uint64_t wait, uint64_t signal)
{
	VkPipelineStageFlags stage = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
	VkTimelineSemaphoreSubmitInfo tl = {VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO, NULL, wait != 0, &wait, 1,
	                                    &signal};
	VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, &tl, wait != 0, &s, &stage, cb != NULL, &cb, 1, &s};
	CHECK(vkQueueSubmit(g->queue, 1, &si, VK_NULL_HANDLE));
}

//! vkQueueSubmit with two batches: the first signals `first`, the second waits for it and signals `second`.
static void
submit_pair(struct gpu *g, VkSemaphore s, uint64_t first, uint64_t second)
{
	VkPipelineStageFlags stage = VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
	VkTimelineSemaphoreSubmitInfo tl[2] = {
	    {VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO, NULL, 0, NULL, 1, &first},
	    {VK_STRUCTURE_TYPE_TIMELINE_SEMAPHORE_SUBMIT_INFO, NULL, 1, &first, 1, &second},
	};
	VkSubmitInfo si[2] = {
	    {VK_STRUCTURE_TYPE_SUBMIT_INFO, &tl[0], 0, NULL, NULL, 0, NULL, 1, &s},
	    {VK_STRUCTURE_TYPE_SUBMIT_INFO, &tl[1], 1, &s, &stage, 0, NULL, 1, &s},
	};
	CHECK(vkQueueSubmit(g->queue, 2, si, VK_NULL_HANDLE));
}

//! vkQueueSubmit2 (child).
static void
submit2(struct gpu *g, VkCommandBuffer cb, VkSemaphore s, uint64_t wait, uint64_t signal)
{
	VkSemaphoreSubmitInfo w = {VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, NULL, s, wait,
	                           VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, 0};
	VkSemaphoreSubmitInfo sg = {VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO, NULL, s, signal,
	                            VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT, 0};
	VkCommandBufferSubmitInfo c = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO, NULL, cb, 0};
	VkSubmitInfo2 si = {VK_STRUCTURE_TYPE_SUBMIT_INFO_2, NULL, 0, wait != 0, &w, cb != NULL, &c, 1, &sg};
	CHECK(vkQueueSubmit2(g->queue, 1, &si, VK_NULL_HANDLE));
}

static bool
wait_value(struct gpu *g, VkSemaphore s, uint64_t value, int64_t *took_ns)
{
	VkSemaphoreWaitInfo wi = {VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO, NULL, 0, 1, &s, &value};
	int64_t t0 = now_ns();
	VkResult r = vkWaitSemaphores(g->device, &wi, TIMEOUT_NS);
	if (took_ns != NULL) {
		*took_ns = now_ns() - t0;
	}
	return r == VK_SUCCESS;
}

static void
send_fd(int sock, int fd)
{
	char byte = 'f';
	struct iovec iov = {&byte, 1};
	union
	{
		struct cmsghdr h;
		char buf[CMSG_SPACE(sizeof(int))];
	} u;
	memset(&u, 0, sizeof(u));
	struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1, .msg_control = u.buf, .msg_controllen = sizeof(u.buf)};
	struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
	c->cmsg_level = SOL_SOCKET;
	c->cmsg_type = SCM_RIGHTS;
	c->cmsg_len = CMSG_LEN(sizeof(int));
	memcpy(CMSG_DATA(c), &fd, sizeof(int));
	if (sendmsg(sock, &msg, 0) != 1) {
		fail("sendmsg", VK_ERROR_UNKNOWN);
	}
}

static int
recv_fd(int sock)
{
	char byte;
	struct iovec iov = {&byte, 1};
	union
	{
		struct cmsghdr h;
		char buf[CMSG_SPACE(sizeof(int))];
	} u;
	struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1, .msg_control = u.buf, .msg_controllen = sizeof(u.buf)};
	int fd = -1;
	if (recvmsg(sock, &msg, 0) == 1) {
		for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c != NULL; c = CMSG_NXTHDR(&msg, c)) {
			if (c->cmsg_type == SCM_RIGHTS) {
				memcpy(&fd, CMSG_DATA(c), sizeof(int));
			}
		}
	}
	return fd;
}

//! Messages between the processes: a tag and a value.
struct msg
{
	char tag[8];
	uint64_t value;
};

static void
say(int sock, const char *tag, uint64_t value)
{
	struct msg m = {{0}, value};
	snprintf(m.tag, sizeof(m.tag), "%s", tag);
	if (send(sock, &m, sizeof(m), 0) != (ssize_t)sizeof(m)) {
		fail("send", VK_ERROR_UNKNOWN);
	}
}

static uint64_t
hear(int sock, const char *tag)
{
	struct msg m;
	if (recv(sock, &m, sizeof(m), 0) != (ssize_t)sizeof(m) || strncmp(m.tag, tag, sizeof(m.tag)) != 0) {
		fprintf(stderr, "pico4-vrsync-test %s: expected message %s\n", g_role, tag);
		exit(2);
	}
	return m.value;
}

static int
child(int sock)
{
	g_role = "child";
	struct gpu g = {0};
	gpu_init(&g);
	int fd = recv_fd(sock);
	VkSemaphore s = timeline(&g, false);
	VkImportSemaphoreFdInfoKHR imp = {VK_STRUCTURE_TYPE_IMPORT_SEMAPHORE_FD_INFO_KHR, NULL, s, 0,
	                                  VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT, fd};
	CHECK(g.ImportSemaphoreFdKHR(g.device, &imp));

	// gpu_wait: submitted while the parent's work for value 1 still runs.
	hear(sock, "sub1");
	submit2(&g, record(&g, 0, 1, -1), s, 1, 2);
	int64_t took = 0;
	bool host1 = wait_value(&g, s, 1, &took);
	bool own2 = wait_value(&g, s, 2, NULL);
	say(sock, "host1", host1);
	say(sock, "start", own2 ? timestamp(&g, 0) : 0);

	// host_signal
	hear(sock, "sig3");
	say(sock, "host3", wait_value(&g, s, 3, NULL));

	// early_wait: the wait for value 4 is submitted before the parent signals it.
	say(sock, "ready4", 0);
	int64_t t0 = now_ns();
	submit2(&g, record(&g, -1, 1, -1), s, 4, 5);
	say(sock, "held4", (uint64_t)(now_ns() - t0));
	say(sock, "own5", wait_value(&g, s, 5, NULL));
	vkDeviceWaitIdle(g.device);
	return 0;
}

int
main(void)
{
	int sv[2];
	if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sv) != 0) {
		perror("socketpair");
		return 2;
	}
	pid_t pid = fork();
	if (pid == 0) {
		close(sv[0]);
		return child(sv[1]);
	}
	close(sv[1]);
	int sock = sv[0];
	struct gpu g = {0};
	gpu_init(&g);
	VkSemaphore s = timeline(&g, true);
	VkSemaphoreGetFdInfoKHR gi = {VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR, NULL, s,
	                              VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT};
	int fd = -1;
	CHECK(g.GetSemaphoreFdKHR(g.device, &gi, &fd));
	send_fd(sock, fd);
	close(fd);

	// gpu_wait and host_wait: long work, then value 1.
	int64_t t0 = now_ns();
	submit1(&g, record(&g, -1, FILLS, 1), s, 0, 1);
	say(sock, "sub1", 0);
	int64_t took2 = 0;
	bool host2 = wait_value(&g, s, 2, &took2);
	bool host1 = hear(sock, "host1") != 0;
	uint64_t start = hear(sock, "start");
	uint64_t end = timestamp(&g, 1);
	int64_t work_ns = now_ns() - t0;
	uint64_t counter = 0;
	vkGetSemaphoreCounterValue(g.device, s, &counter);
	bool gpu_wait = host2 && start != 0 && start >= end;

	// host_signal
	VkSemaphoreSignalInfo sig = {VK_STRUCTURE_TYPE_SEMAPHORE_SIGNAL_INFO, NULL, s, 3};
	CHECK(vkSignalSemaphore(g.device, &sig));
	say(sock, "sig3", 0);
	bool host_signal = hear(sock, "host3") != 0;

	// early_wait
	hear(sock, "ready4");
	struct timespec pause = {0, 200000000};
	nanosleep(&pause, NULL);
	submit1(&g, record(&g, -1, 1, -1), s, 0, 4);
	int64_t took5 = 0;
	bool host5 = wait_value(&g, s, 5, &took5);
	uint64_t held = hear(sock, "held4");
	bool own5 = hear(sock, "own5") != 0;
	bool early_wait = host5 && own5 && held >= 150000000;

	// same_submit: a later batch waits for an earlier batch of the same submission.
	int64_t t7 = now_ns();
	submit_pair(&g, s, 6, 7);
	bool same_submit = wait_value(&g, s, 7, NULL);
	int64_t took7 = now_ns() - t7;
	same_submit = same_submit && took7 < 1000000000;

	int status = 0;
	waitpid(pid, &status, 0);
	bool child_ok = WIFEXITED(status) && WEXITSTATUS(status) == 0;
	bool pass = gpu_wait && host1 && host2 && counter >= 2 && host_signal && early_wait && same_submit && child_ok;
	printf("{\"schema\":1,\"status\":\"%s\",\"gpu_wait\":%s,\"host_wait\":%s,\"host_signal\":%s,\"early_wait\":%s,"
	       "\"same_submit\":%s,\"child_exit\":%d,\"counter\":%" PRIu64 ",\"work_ms\":%.1f,\"wait2_ms\":%.1f,"
	       "\"child_start_ticks\":%" PRIu64 ",\"parent_end_ticks\":%" PRIu64 ",\"early_held_ms\":%.1f,"
	       "\"wait5_ms\":%.1f,\"same_submit_ms\":%.1f}\n",
	       pass ? "pass" : "fail", gpu_wait ? "true" : "false", host1 && host2 ? "true" : "false",
	       host_signal ? "true" : "false", early_wait ? "true" : "false", same_submit ? "true" : "false",
	       WIFEXITED(status) ? WEXITSTATUS(status) : -1, counter, work_ns / 1e6, took2 / 1e6, start, end,
	       held / 1e6, took5 / 1e6, took7 / 1e6);
	vkDeviceWaitIdle(g.device);
	return pass ? 0 : 1;
}
