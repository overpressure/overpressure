// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  PICO 4 lens model (see pico4_lens.h).
 * @ingroup drv_pico4
 */

#include "pico4_lens.h"

#include <cjson/cJSON.h>

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

//! The polynomials are fitted inside the panel's inscribed circle; they diverge in the corners.
#define PICO4_LENS_FIT_RADIUS_M (PICO4_LENS_EYE_M / 2 * 1.02)

//! Numbers in these files are strings ("0.025000"); accept plain numbers too.
static bool
get_number(const cJSON *obj, const char *name, double *out)
{
	const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, name);
	char *end = NULL;
	if (cJSON_IsNumber(item)) {
		*out = item->valuedouble;
		return true;
	}
	if (cJSON_IsString(item) && item->valuestring != NULL) {
		*out = strtod(item->valuestring, &end);
		return end != item->valuestring;
	}
	return false;
}

bool
pico4_lens_parse_optical(const char *json_text, struct pico4_lens out_lenses[2])
{
	static const char *eyes[2] = {"left", "right"};
	cJSON *root = cJSON_Parse(json_text);
	const cJSON *params = cJSON_GetObjectItemCaseSensitive(root, "distortion_parameter");
	const cJSON *center = cJSON_GetObjectItemCaseSensitive(root, "lens_center_gos");
	bool ok = params != NULL;

	for (int e = 0; ok && e < 2; e++) {
		const cJSON *p = cJSON_GetObjectItemCaseSensitive(params, eyes[e]);
		struct pico4_lens l = {0};
		ok = get_number(p, "F", &l.f) && l.f > 0;
		for (int i = 0; ok && i < PICO4_LENS_COEFFS; i++) {
			char name[8];
			snprintf(name, sizeof(name), "A%d", i);
			ok = get_number(p, name, &l.k[i]);
		}
		// Optional: optical centre in mm (stock uses it as the lens centre offset).
		double mm;
		if (get_number(cJSON_GetObjectItemCaseSensitive(center, "centerx"), eyes[e], &mm)) {
			l.cx = mm * 0.001;
		}
		if (get_number(cJSON_GetObjectItemCaseSensitive(center, "centery"), eyes[e], &mm)) {
			l.cy = -mm * 0.001;
		}
		out_lenses[e] = l;
	}
	cJSON_Delete(root);
	return ok;
}

bool
pico4_lens_parse_ca(const char *json_text, struct pico4_lens_ca *out_ca)
{
	cJSON *root = cJSON_Parse(json_text);
	const cJSON *entries = cJSON_GetObjectItemCaseSensitive(root, "configData");
	unsigned found = 0;
	const cJSON *entry;
	cJSON_ArrayForEach(entry, entries)
	{
		const cJSON *key = cJSON_GetObjectItemCaseSensitive(entry, "keyName");
		int index;
		char tail;
		double value;
		if (cJSON_IsString(key) &&
		    sscanf(key->valuestring, "sdk_lens_chromaticAberration%d%c", &index, &tail) == 1 && index >= 0 &&
		    index < 6 && get_number(entry, "keyValue", &value)) {
			out_ca->ca[index] = value;
			found |= 1u << index;
		}
	}
	cJSON_Delete(root);
	return found == 0x3f;
}

double
pico4_lens_scale(const struct pico4_lens *lens, double rsq)
{
	double s = 0;
	for (int i = PICO4_LENS_COEFFS - 1; i >= 0; i--) {
		s = s * rsq + lens->k[i];
	}
	return s;
}

bool
pico4_lens_tan(const struct pico4_lens *lens, const struct pico4_lens_ca *ca, double x, double y, double out[3][2])
{
	double px = x - lens->cx, py = y - lens->cy;
	double rm = sqrt(px * px + py * py);
	/*
	 * Beyond the fitted radius, evaluate the polynomial at that radius and let
	 * the tan-angle grow steeply instead: the image then ends smoothly where it
	 * leaves the rendered field of view, not at mesh-cell steps.
	 */
	double outside = rm > PICO4_LENS_FIT_RADIUS_M ? rm / PICO4_LENS_FIT_RADIUS_M : 1.0;
	double tx = px / outside / lens->f, ty = py / outside / lens->f;
	double rsq = tx * tx + ty * ty, r = sqrt(rsq);
	double s = pico4_lens_scale(lens, rsq) * outside * outside * outside * outside;
	const double *c = ca->ca;
	double scale[3] = {
	    s * (1 + c[0] + c[1] * rsq + c[4] * r),
	    s,
	    s * (1 + c[2] + c[3] * rsq + c[5] * r),
	};
	for (int ch = 0; ch < 3; ch++) {
		out[ch][0] = tx * scale[ch];
		out[ch][1] = ty * scale[ch];
	}
	return outside == 1.0;
}

//! Green tan-angle at distance d (metres) from the lens centre along an axis.
static double
edge_tan(const struct pico4_lens *lens, double d)
{
	double t = d / lens->f;
	return t * pico4_lens_scale(lens, t * t);
}

void
pico4_lens_fov(const struct pico4_lens *lens, double *out_left, double *out_right, double *out_up, double *out_down)
{
	double half = PICO4_LENS_EYE_M / 2;
	*out_left = -atan(edge_tan(lens, half + lens->cx));
	*out_right = atan(edge_tan(lens, half - lens->cx));
	*out_up = atan(edge_tan(lens, half + lens->cy));
	*out_down = -atan(edge_tan(lens, half - lens->cy));
}
