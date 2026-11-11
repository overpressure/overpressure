// SPDX-License-Identifier: Unlicense
/*
 * pico4-gd32-reset - reset and wake the GD32 MCU as PICO OS's gd32ipdservice does, from the
 * maintenance stage 1 (PICO OS's kernel and its serial driver).
 *
 * PICO OS's serial driver for the MCU's UART (/dev/ttyHS1) drives the MCU's lines through its
 * own ioctls: 0x545F the boot line, 0x5460 the reset line, 0x5461 the sync line, 0x545E the
 * MCU's supply, each to the int the argument points to; 0x544D votes the port's clocks on for
 * a transaction. gd32ipdservice's ResetMCU sets boot 1 and reset 1, waits 10 ms, sets reset 0,
 * then wakes the MCU (wakeUp): SetBrightness A0 05 00 3F 00 9A (level 63, its value without a
 * configuration) every 10 ms, up to 50 times, until the MCU answers 79. Each transaction opens
 * the port at 115200 8N1, votes its clocks, flushes it, writes the 6 bytes and reads the reply
 * within a few ms. This program does the same and then asks for the MCU's version and the
 * fan's duty (two of pico4-fan's queries). It sends no other frame: nothing of the MCU's boot
 * loader or firmware update protocol.
 *
 * A freestanding aarch64 program (no libc), built as pico4-reboot is:
 *
 *   aarch64-linux-gnu-gcc -Os -static -nostdlib -ffreestanding \
 *       -fno-stack-protector -fno-asynchronous-unwind-tables \
 *       -Wl,--build-id=none -s -o pico4-gd32-reset pico4-gd32-reset.c
 *
 * usage: pico4-gd32-reset            ResetMCU, wakeUp, then the two queries
 *        pico4-gd32-reset status     wakeUp and the two queries, without the reset
 *
 * Only PICO OS's kernel has these ioctls: on Valve's kernel they fail and nothing is sent.
 */

#define NR_ioctl 29
#define NR_openat 56
#define NR_close 57
#define NR_read 63
#define NR_write 64
#define NR_nanosleep 101

#define AT_FDCWD -100L
#define O_RDWR 02L
#define O_NOCTTY 0400L
#define O_NONBLOCK 04000L
#define EAGAIN 11

#define TCGETS 0x5401
#define TCSETS 0x5402
#define TCFLSH 0x540B
#define TCIOFLUSH 2

/* PICO OS's serial driver (its ioctl handler's dispatch from 0x544D). */
#define GD32_VOTE 0x544D
#define GD32_BOOT 0x545F
#define GD32_RESET 0x5460

/* struct termios as the aarch64 kernel takes it (TCGETS/TCSETS). */
struct kterm {
	unsigned int iflag, oflag, cflag, lflag;
	unsigned char line;
	unsigned char cc[19];
};

#define CBAUD 0010017
#define B115200 0010002
#define CSIZE 0000060
#define CS8 0000060
#define CSTOPB 0000100
#define CREAD 0000200
#define PARENB 0000400
#define CLOCAL 0004000
#define CRTSCTS 020000000000U
#define VTIME 5
#define VMIN 6

#define DEVICE "/dev/ttyHS1"
#define WAKE_TRIES 50

static long sys4(long n, long a, long b, long c, long d)
{
	register long x8 __asm__("x8") = n;
	register long x0 __asm__("x0") = a;
	register long x1 __asm__("x1") = b;
	register long x2 __asm__("x2") = c;
	register long x3 __asm__("x3") = d;
	__asm__ volatile("svc #0"
			 : "+r"(x0)
			 : "r"(x8), "r"(x1), "r"(x2), "r"(x3)
			 : "memory", "cc");
	return x0;
}

static void out(const char *s)
{
	long n = 0;
	while (s[n])
		n++;
	sys4(NR_write, 1, (long)s, n, 0);
}

static void hex(const unsigned char *b, long n)
{
	static const char digits[] = "0123456789abcdef";
	char s[4];
	for (long i = 0; i < n; i++) {
		s[0] = ' ';
		s[1] = digits[b[i] >> 4];
		s[2] = digits[b[i] & 15];
		s[3] = 0;
		out(s);
	}
}

static void number(long v)
{
	char s[24];
	int i = 23;
	s[i] = 0;
	if (v < 0) {
		out("-");
		v = -v;
	}
	do {
		s[--i] = (char)('0' + v % 10);
		v /= 10;
	} while (v);
	out(s + i);
}

static void sleep_us(long us)
{
	long ts[2] = {us / 1000000, (us % 1000000) * 1000};
	sys4(NR_nanosleep, (long)ts, 0, 0, 0);
}

static int eq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

/* The port opened as gd32ipdservice opens it: 115200 8N1, raw, polled. */
static long port_open(void)
{
	long fd = sys4(NR_openat, AT_FDCWD, (long)DEVICE, O_RDWR | O_NOCTTY | O_NONBLOCK, 0);
	struct kterm t;
	if (fd < 0)
		return fd;
	if (sys4(NR_ioctl, fd, TCGETS, (long)&t, 0) == 0) {
		t.iflag = 0;
		t.oflag = 0;
		t.lflag = 0;
		t.cflag = (t.cflag & ~(CBAUD | CSIZE | CSTOPB | PARENB | CRTSCTS)) | B115200 | CS8 | CREAD | CLOCAL;
		t.cc[VMIN] = 0;
		t.cc[VTIME] = 0;
		sys4(NR_ioctl, fd, TCSETS, (long)&t, 0);
	}
	return fd;
}

/* One transaction as Uart_SetGet runs it; returns the bytes read (up to 20 ms), or a negative errno. */
static long transact(const unsigned char *tx, unsigned char *rx, long size)
{
	long fd = port_open(), n = 0;
	if (fd < 0)
		return fd;
	sys4(NR_ioctl, fd, GD32_VOTE, 0, 0);
	sys4(NR_ioctl, fd, TCFLSH, TCIOFLUSH, 0);
	if (sys4(NR_write, fd, (long)tx, 6, 0) != 6) {
		sys4(NR_close, fd, 0, 0, 0);
		return -5;
	}
	for (int i = 0; i < 20 && n < size; i++) {
		sleep_us(1000);
		long r = sys4(NR_read, fd, (long)(rx + n), size - n, 0);
		if (r > 0)
			n += r;
		else if (r != -EAGAIN && r != 0)
			break;
	}
	sys4(NR_close, fd, 0, 0, 0);
	return n;
}

/* gd32ipdservice's ResetMCU: boot 1, reset 1, 10 ms, reset 0. */
static int reset_mcu(void)
{
	int one = 1, zero = 0;
	long fd = port_open(), a, b, c;
	if (fd < 0) {
		out("open " DEVICE ": ");
		number(fd);
		out("\n");
		return 1;
	}
	a = sys4(NR_ioctl, fd, GD32_BOOT, (long)&one, 0);
	b = sys4(NR_ioctl, fd, GD32_RESET, (long)&one, 0);
	sleep_us(10000);
	c = sys4(NR_ioctl, fd, GD32_RESET, (long)&zero, 0);
	sys4(NR_close, fd, 0, 0, 0);
	out("reset: boot 1 ");
	number(a);
	out(", reset 1 ");
	number(b);
	out(", 10 ms, reset 0 ");
	number(c);
	out("\n");
	return a || b || c;
}

/* gd32ipdservice's wakeUp: the wake until the MCU answers 79. */
static int wake(void)
{
	static const unsigned char frame[6] = {0xA0, 0x05, 0x00, 0x3F, 0x00, 0x9A};
	unsigned char rx[16];
	long n = 0;
	for (int i = 1; i <= WAKE_TRIES; i++) {
		n = transact(frame, rx, sizeof(rx));
		if (n > 0 && rx[0] == 0x79) {
			out("wake: 79 at try ");
			number(i);
			out("\n");
			return 0;
		}
		sleep_us(10000);
	}
	out("wake: no 79 after ");
	number(WAKE_TRIES);
	out(" tries; the last answer:");
	if (n > 0)
		hex(rx, n);
	else {
		out(" ");
		number(n);
	}
	out("\n");
	return 1;
}

static void query(const char *name, const unsigned char *frame)
{
	unsigned char rx[16];
	long n = transact(frame, rx, sizeof(rx));
	out(name);
	out(":");
	if (n > 0)
		hex(rx, n);
	else {
		out(" ");
		number(n);
	}
	out("\n");
}

long pico4_main(long argc, char **argv)
{
	static const unsigned char version[6] = {0xA0, 0x26, 0x00, 0x00, 0x00, 0x86};
	static const unsigned char duty[6] = {0xA0, 0x0F, 0x00, 0x00, 0x00, 0xAF};
	const char *mode = argc > 1 ? argv[1] : "";
	int failed = 0;

	if (argc > 2 || !(eq(mode, "") || eq(mode, "status"))) {
		out("usage: pico4-gd32-reset [status]\n");
		return 2;
	}
	query("before: version", version);
	if (!*mode)
		failed |= reset_mcu();
	failed |= wake();
	query("version", version);
	query("fan duty", duty);
	return failed;
}

/* Entry point: argc at [sp], argv at sp+8. */
__asm__(".text\n"
	".global _start\n"
	"_start:\n"
	"	ldr x0, [sp]\n"
	"	add x1, sp, #8\n"
	"	bl pico4_main\n"
	"	mov x8, #93\n"
	"	svc #0\n");
