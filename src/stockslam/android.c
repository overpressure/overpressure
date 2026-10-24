// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: MIT
/*
 * libandroid.so for PICO OS's tracker: it needs only these two trace calls from it, and the
 * real library would pull in most of Android.
 */
void
ATrace_beginSection(const char *name)
{
	(void)name;
}

void
ATrace_endSection(void)
{
}
