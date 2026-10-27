/*
 * pico4-reboot - reboot with a reason string, e.g. "bootloader" or "recovery".
 *
 * BusyBox's reboot cannot hand a command string to the kernel, but that string
 * is how the Qualcomm restart driver decides where the next boot goes
 * (fastboot, recovery, ...). This is a freestanding aarch64 program (no libc),
 * so any aarch64 cross compiler can build it:
 *
 *   aarch64-linux-gnu-gcc -Os -static -nostdlib -ffreestanding \
 *       -fno-stack-protector -fno-asynchronous-unwind-tables \
 *       -Wl,--build-id=none -s -o pico4-reboot pico4-reboot.c
 *
 * usage: pico4-reboot                 plain reboot (back to PICO OS)
 *        pico4-reboot bootloader      reboot into fastboot
 *        pico4-reboot recovery        reboot into recovery
 *        pico4-reboot poweroff        power off
 *        pico4-reboot -r <reason>     any other reboot command string
 *
 * Unknown arguments are refused rather than passed to the kernel, so a stray
 * word (say, text echoed back by a misconfigured serial terminal) cannot
 * reboot the headset.
 */

#define NR_write 64
#define NR_exit 93
#define NR_sync 81
#define NR_reboot 142

#define REBOOT_MAGIC1 0xfee1deadL
#define REBOOT_MAGIC2 672274793L
#define REBOOT_CMD_RESTART 0x01234567L
#define REBOOT_CMD_RESTART2 0xA1B2C3D4L
#define REBOOT_CMD_POWER_OFF 0x4321FEDCL

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

static void say(const char *s)
{
	long n = 0;
	while (s[n])
		n++;
	sys4(NR_write, 2, (long)s, n, 0);
}

static int eq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

long pico4_main(long argc, char **argv)
{
	const char *mode = argc > 1 ? argv[1] : "";

	if (eq(mode, "-r") && argc == 3 && *argv[2]) {
		mode = argv[2];
	} else if (argc > 2 || !(eq(mode, "") || eq(mode, "bootloader") || eq(mode, "recovery") ||
				 eq(mode, "poweroff"))) {
		say("usage: pico4-reboot [bootloader|recovery|poweroff]\n"
		    "       pico4-reboot -r <reboot command string>\n");
		return eq(mode, "-h") || eq(mode, "--help") ? 0 : 2;
	}
	sys4(NR_sync, 0, 0, 0, 0);
	if (eq(mode, "poweroff"))
		sys4(NR_reboot, REBOOT_MAGIC1, REBOOT_MAGIC2, REBOOT_CMD_POWER_OFF, 0);
	else if (*mode)
		sys4(NR_reboot, REBOOT_MAGIC1, REBOOT_MAGIC2, REBOOT_CMD_RESTART2, (long)mode);
	else
		sys4(NR_reboot, REBOOT_MAGIC1, REBOOT_MAGIC2, REBOOT_CMD_RESTART, 0);
	say("pico4-reboot: reboot(2) failed (not root?)\n");
	return 1;
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
