// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: MIT
/*
 * Preloaded into PICO OS's tracker processes (libpico4-slam-stubs.so): there is no logd and no
 * property area in the tracker's root, so Android's log calls go to stderr (the journal) and
 * property lookups find nothing, which the tracker's defaults cover. Verbose and debug log
 * lines are dropped unless PICO4_SLAMD_DEBUG is set.
 */
#include "bionic.h"

static const char prio[] = "??VDIWEFS";

int
__android_log_vprint(int p, const char *tag, const char *fmt, va_list ap)
{
	static int debug = -1;
	if (debug < 0) {
		const char *d = getenv("PICO4_SLAMD_DEBUG");
		debug = d != NULL && *d != '\0' && *d != '0';
	}
	if (p < 4 && !debug) { // ANDROID_LOG_INFO
		return 0;
	}
	char line[1024];
	vsnprintf(line, sizeof line, fmt, ap);
	fprintf(stderr, "%c/%s: %s\n", p >= 0 && p < 9 ? prio[p] : '?', tag ? tag : "", line);
	return 1;
}

int
__android_log_print(int p, const char *tag, const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	int r = __android_log_vprint(p, tag, fmt, ap);
	va_end(ap);
	return r;
}

int
__android_log_write(int p, const char *tag, const char *msg)
{
	return __android_log_print(p, tag, "%s", msg);
}

int
__android_log_buf_print(int buf, int p, const char *tag, const char *fmt, ...)
{
	(void)buf;
	va_list ap;
	va_start(ap, fmt);
	int r = __android_log_vprint(p, tag, fmt, ap);
	va_end(ap);
	return r;
}

// Properties: none set; the tracker's own defaults apply.
int
__system_property_get(const char *name, char *value)
{
	(void)name;
	value[0] = 0;
	return 0;
}

const void *
__system_property_find(const char *name)
{
	(void)name;
	return 0;
}

int
__system_property_set(const char *name, const char *value)
{
	(void)name;
	(void)value;
	return 0;
}
