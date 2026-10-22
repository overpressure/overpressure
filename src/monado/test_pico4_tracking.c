// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*
 * Unit test of the tracking-camera helpers with synthetic values (no device
 * data): the station frame builder (golden frames from the Python tool that
 * was verified on hardware, and the allowlist), the vibration frames (golden
 * frames from the documented layout), the factory calibration
 * parser, the Kannala-Brandt refit, the controller packet parser and the
 * camera metadata line.
 */

#include "pico4_blemcu.h"
#include "pico4_cam.h"
#include "pico4_station.h"
#include "pico4_tcam.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures;

#define EXPECT(cond)                                                                                                   \
	do {                                                                                                           \
		if (!(cond)) {                                                                                         \
			fprintf(stderr, "%s:%d: expected %s\n", __FILE__, __LINE__, #cond);                            \
			failures++;                                                                                    \
		}                                                                                                      \
	} while (0)

static void
test_station(void)
{
	uint8_t f[PICO4_STATION_FRAME];
	const uint8_t init[4] = {0x01, 0x00, 0x02, 0x00}, on[3] = {0x02, 0x00, 0x01}, fps[3] = {0x01, 0x00, 0x01};
	const uint8_t ack[2] = {0x12, 0x34};

	EXPECT(pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, init, sizeof(init)));
	EXPECT(f[0] == 0xAA && f[1] == 0x01 && f[2] == 0x04 && f[3] == 0x01 && f[5] == 0x02 && f[39] == 0x77);
	EXPECT(pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, on, sizeof(on)));
	EXPECT(f[3] == 0x02 && f[5] == 0x01 && f[39] == 0x06);
	EXPECT(pico4_station_frame(f, PICO4_STATION_CMD_CAMERA_FPS, fps, sizeof(fps)));
	EXPECT(f[2] == 0x19 && f[39] == 0x00);
	EXPECT(pico4_station_frame(f, PICO4_STATION_CMD_ACK, ack, sizeof(ack)));
	EXPECT(f[2] == 0xFA && f[3] == 0x12 && f[4] == 0x34 && f[39] == 0x28);

	// Nothing outside the allowlist: other commands, camera ops and payload shapes.
	const uint8_t version[1] = {0};
	const uint8_t cam_fps[3] = {0x07, 0x00, 0x01}, bad_stream[3] = {0x02, 0x00, 0x03}, short_init[2] = {0x01, 0x00};
	const uint8_t bad_rate[3] = {0x03, 0x00, 0x01};
	EXPECT(!pico4_station_frame(f, 0x01, version, sizeof(version)));
	for (int cmd = 0; cmd < 256; cmd++) {
		if (cmd != PICO4_STATION_CMD_CAMERA && cmd != PICO4_STATION_CMD_CAMERA_FPS && cmd != PICO4_STATION_CMD_ACK) {
			EXPECT(!pico4_station_frame(f, (uint8_t)cmd, ack, sizeof(ack)));
		}
	}
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, cam_fps, sizeof(cam_fps)));
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, bad_stream, sizeof(bad_stream)));
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, short_init, sizeof(short_init)));
	uint8_t para[28] = {0x03};
	EXPECT(pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, para, sizeof(para)));
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, para, 27));
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_CAMERA_FPS, bad_rate, sizeof(bad_rate)));
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_ACK, init, sizeof(init)));

	// CameraSetPara: scene setting for cameras 0-3, PICO OS's 50 Hz value (52) and the event
	// index, then the LED setting.
	const struct pico4_station_exposure scene = {300, 64}, leds = {4, 26}, long_leds = {50, 64};
	const uint8_t golden[28] = {0x03, 0x2C, 0x01, 0x40, 0x2C, 0x01, 0x40, 0x2C, 0x01, 0x40, 0x2C, 0x01, 0x40, 0x34, 0, 7,
	                            0x04, 0x00, 0x1A, 0x04, 0x00, 0x1A, 0x04, 0x00, 0x1A, 0x04, 0x00, 0x1A};
	uint8_t built[28];
	pico4_station_camera_para(built, scene, leds, 7);
	EXPECT(memcmp(built, golden, sizeof(golden)) == 0);
	EXPECT(pico4_station_frame(f, PICO4_STATION_CMD_CAMERA, built, sizeof(built)));
	// The event index counts 0-9.
	pico4_station_camera_para(built, scene, leds, 13);
	EXPECT(built[15] == 3);
	// The second set stays within PICO OS's LED limits (10 lines, gain 32).
	pico4_station_camera_para(built, scene, long_leds, 0);
	EXPECT(built[16] == 10 && built[17] == 0 && built[18] == 32 && built[25] == 10 && built[27] == 32);
	EXPECT(built[1] == 0x2C && built[2] == 0x01 && built[3] == 0x40);

	// Replies: header and CRC.
	uint8_t reply[PICO4_STATION_FRAME] = {0xBB, 0x01, 0x04, 0x01, 0x01};
	reply[39] = pico4_station_crc8(reply, 39);
	EXPECT(pico4_station_valid(reply));
	reply[10] ^= 1;
	EXPECT(!pico4_station_valid(reply));
}

//! A whole frame: AA, target, command, payload, zeros, CRC at byte 39.
static bool
frame_is(const uint8_t f[PICO4_STATION_FRAME], const uint8_t *head, size_t n, uint8_t crc)
{
	for (size_t i = n; i < 39; i++) {
		if (f[i] != 0) {
			return false;
		}
	}
	return memcmp(f, head, n) == 0 && f[39] == crc;
}

static void
test_station_vibrate(void)
{
	uint8_t f[PICO4_STATION_FRAME];

	// Plain motor command to the left controller: play, 200 × 50 % = 100, 300 ms.
	const uint8_t left[7] = {0xAA, 0x02, 0x01, 0x01, 0x64, 0x2C, 0x01};
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_LEFT, 200, 50, 300, 0));
	EXPECT(frame_is(f, left, sizeof(left), 0x81));

	// Motor event to the right controller: full level, 1000 ms, 160 Hz.
	const uint8_t right[9] = {0xAA, 0x03, 0x07, 0x01, 0xFF, 0xE8, 0x03, 0xA0, 0x00};
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_RIGHT, 255, 100, 1000, 160));
	EXPECT(frame_is(f, right, sizeof(right), 0xF3));

	// Level 0 stops: an all-zero payload, whatever else was asked.
	const uint8_t stop_left[3] = {0xAA, 0x02, 0x01}, stop_right[3] = {0xAA, 0x03, 0x07};
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_LEFT, 0, 75, 500, 0));
	EXPECT(frame_is(f, stop_left, sizeof(stop_left), 0x14));
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_RIGHT, 0, 75, 500, 160));
	EXPECT(frame_is(f, stop_right, sizeof(stop_right), 0xF4));

	// Clamps: level 255, amplitude 255, duration u16, frequency 40-500 Hz.
	const uint8_t clamped[9] = {0xAA, 0x03, 0x07, 0x01, 0xFF, 0xFF, 0xFF, 0xF4, 0x01};
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_RIGHT, 1000, 100, 70000, 900));
	EXPECT(frame_is(f, clamped, sizeof(clamped), 0xF5));
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_LEFT, 10, 100, 20, 5));
	EXPECT(f[2] == 0x07 && f[4] == 10 && f[7] == 40 && f[8] == 0);
	// The amplitude truncates: 3 × 75 / 100 = 2; 1 × 50 / 100 = 0 still plays, as PICO OS does.
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_LEFT, 3, 75, 20, 0) && f[3] == 1 && f[4] == 2);
	EXPECT(pico4_station_vibrate(f, PICO4_STATION_HAND_LEFT, 1, 50, 20, 0) && f[3] == 1 && f[4] == 0);

	// Refused: other targets and a scale above 100 %; nothing is written.
	memset(f, 0x5A, sizeof(f));
	EXPECT(!pico4_station_vibrate(f, (enum pico4_station_hand)0, 100, 50, 100, 0));
	EXPECT(!pico4_station_vibrate(f, (enum pico4_station_hand)3, 100, 50, 100, 0));
	EXPECT(!pico4_station_vibrate(f, PICO4_STATION_HAND_LEFT, 100, 101, 100, 0));
	EXPECT(f[0] == 0x5A && f[39] == 0x5A);

	// To the station, 01 (firmware version) and 07 (IMU stream) stay refused.
	const uint8_t motor[4] = {0x01, 0x64, 0x2C, 0x01}, event[6] = {0x01, 0x64, 0x2C, 0x01, 0xA0, 0x00};
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_MOTOR, motor, sizeof(motor)));
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_MOTOR_EVENT, event, sizeof(event)));

	// The calibration query: golden frames from pico4-station (controller_query_frame).
	const uint8_t calib_left[4] = {0xAA, 0x02, 0x02, 0x01}, calib_right[4] = {0xAA, 0x03, 0x02, 0x0C};
	EXPECT(pico4_station_calibration_query(f, PICO4_STATION_HAND_LEFT, 1));
	EXPECT(frame_is(f, calib_left, sizeof(calib_left), 0x62));
	EXPECT(pico4_station_calibration_query(f, PICO4_STATION_HAND_RIGHT, 12));
	EXPECT(frame_is(f, calib_right, sizeof(calib_right), 0xC4));
	// Refused: chunk 0 or beyond 12, another target, and command 02 to the station.
	memset(f, 0x5A, sizeof(f));
	EXPECT(!pico4_station_calibration_query(f, PICO4_STATION_HAND_LEFT, 0));
	EXPECT(!pico4_station_calibration_query(f, PICO4_STATION_HAND_LEFT, 13));
	EXPECT(!pico4_station_calibration_query(f, PICO4_STATION_HAND_RIGHT, 257));
	EXPECT(!pico4_station_calibration_query(f, (enum pico4_station_hand)3, 1));
	EXPECT(f[0] == 0x5A && f[39] == 0x5A);
	const uint8_t chunk[1] = {1};
	EXPECT(!pico4_station_frame(f, PICO4_STATION_CMD_CALIBRATION, chunk, sizeof(chunk)));

	// OpenXR mapping: XR_MIN_HAPTIC_DURATION is 500 ms, at least 10 ms, level = amplitude × 255.
	unsigned level = 99, ms = 99;
	pico4_station_haptic_map(1.0f, -1, &level, &ms);
	EXPECT(level == 255 && ms == 500);
	pico4_station_haptic_map(0.25f, 123456789, &level, &ms);
	EXPECT(level == 63 && ms == 123);
	pico4_station_haptic_map(0.5f, 2500000, &level, &ms);
	EXPECT(level == 127 && ms == 10);
	pico4_station_haptic_map(0.5f, 999999, &level, &ms); // under 1 ms: stop
	EXPECT(level == 0 && ms == 0);
	pico4_station_haptic_map(0.0f, 1000000000, &level, &ms);
	EXPECT(level == 0 && ms == 0);
	pico4_station_haptic_map(NAN, 1000000000, &level, &ms);
	EXPECT(level == 0 && ms == 0);
	pico4_station_haptic_map(2.0f, INT64_MAX, &level, &ms); // XR_INFINITE_DURATION
	EXPECT(level == 255 && ms == 65535);
}

// Same structure as the factory files; made-up numbers of the same magnitude.
static const char *yaml = "%YAML:1.0\n"
                          "sensor_type: camera\n"
                          "T_BS:\n"
                          "  cols: 4\n"
                          "  rows: 4\n"
                          "  data: [ 0.0, 0.0, 1.0, 0.01,\n"
                          "          1.0, 0.0, 0.0, 0.10,\n"
                          "          0.0, 1.0, 0.0, -0.02,\n"
                          "          0.0, 0.0, 0.0, 1.0]\n"
                          "rate_hz: 30\n"
                          "resolution: [640, 480]\n"
                          "camera_model: omni\n"
                          "intrinsics: [3.5,1050.0,1052.0,321.0,242.0] #xi fu fv pu pv\n"
                          "distortion_model: radtan\n"
                          "distortion_coefficients: [1.2,15.5,0.001,-0.002]";

static void
test_calibration(void)
{
	struct pico4_tcam_omni c;
	struct pico4_tcam_kb4 k;

	EXPECT(pico4_tcam_parse_yaml(yaml, &c));
	EXPECT(c.width == 640 && c.height == 480);
	EXPECT(c.xi == 3.5 && c.fu == 1050.0 && c.fv == 1052.0 && c.pu == 321.0 && c.pv == 242.0);
	EXPECT(c.k1 == 1.2 && c.k2 == 15.5 && c.p1 == 0.001 && c.p2 == -0.002);
	EXPECT(c.T_BS[2] == 1.0 && c.T_BS[3] == 0.01 && c.T_BS[7] == 0.10 && c.T_BS[11] == -0.02 && c.T_BS[15] == 1.0);

	// Other models are refused rather than misread.
	char other[2048];
	snprintf(other, sizeof(other), "%s", yaml);
	memcpy(strstr(other, "omni"), "pinh", 4);
	EXPECT(!pico4_tcam_parse_yaml(other, &c));
	EXPECT(pico4_tcam_parse_yaml(yaml, &c));

	// The optical axis lands on the principal point in both models.
	double axis[3] = {0, 0, 1}, uv[2];
	EXPECT(pico4_tcam_omni_project(&c, axis, uv));
	EXPECT(fabs(uv[0] - 321.0) < 1e-9 && fabs(uv[1] - 242.0) < 1e-9);

	EXPECT(pico4_tcam_fit_kb4(&c, &k));
	printf("KB4 fit: f %.2f %.2f, k %.4f %.4f %.4f %.4f, rms %.3f px, max %.3f px over %d rays\n", k.fx, k.fy,
	       k.k[0], k.k[1], k.k[2], k.k[3], k.rms_px, k.max_px, k.samples);
	EXPECT(k.cx == 321.0 && k.cy == 242.0);
	EXPECT(k.samples > 1000);
	EXPECT(k.rms_px < 0.5);
	EXPECT(k.max_px < 2.0);

	// Spot check off axis: 40 degrees right of the axis.
	double p[3] = {sin(0.698), 0, cos(0.698)}, a[2], b[2];
	EXPECT(pico4_tcam_omni_project(&c, p, a));
	pico4_tcam_kb4_project(&k, p, b);
	EXPECT(fabs(a[0] - b[0]) < 1.0 && fabs(a[1] - b[1]) < 1.0);

	// A camera turned by 180 degrees sees a body point at the mirrored pixel.
	struct pico4_tcam_kb4 r = k;
	double T[16];
	memcpy(T, c.T_BS, sizeof(T));
	pico4_tcam_rotate_180(&r, T, c.width, c.height);
	EXPECT(!pico4_tcam_upside_down(c.T_BS, c.T_BS));
	EXPECT(pico4_tcam_upside_down(c.T_BS, T));
	double body[3] = {0.3, -0.2, 1.5}, pc[3], pr[3], u0[2], u1[2];
	for (int i = 0; i < 3; i++) {
		// Camera coordinates: R^T (p - t), R and t from the row-major T_BS.
		pc[i] = pr[i] = 0;
		for (int j = 0; j < 3; j++) {
			pc[i] += c.T_BS[j * 4 + i] * (body[j] - c.T_BS[j * 4 + 3]);
			pr[i] += T[j * 4 + i] * (body[j] - T[j * 4 + 3]);
		}
	}
	pico4_tcam_kb4_project(&k, pc, u0);
	pico4_tcam_kb4_project(&r, pr, u1);
	EXPECT(fabs(u1[0] - (c.width - 1 - u0[0])) < 1e-6 && fabs(u1[1] - (c.height - 1 - u0[1])) < 1e-6);
}

static void
test_blemcu(void)
{
	// Same layout as the radio packets: an IMU record with a time offset (right
	// controller), a tracker record to skip, keys and battery of the left one.
	uint8_t pkt[200] = {0x5A, 0x01, 0x00, 0x00, 0x78, 0x56, 0x34, 0x12, 0x01, 0x00, 0x00, 0x00};
	const uint8_t records[] = {
	    0x17, 0x00, 0x82, 0x80, 0x07,                         // IMU, right, 1920 us earlier
	    0xF5, 0xFF, 0xFC, 0xFF, 0x05, 0x00,                   // gyro -11 -4 5
	    0xC7, 0x03, 0xAB, 0x00, 0x0F, 0xFF,                   // accel 967 171 -241
	    0x0A, 0xFF, 0xA1, 0x00, 0xD0, 0x07,                   // temperature, counter, range
	    0x06, 0x00, 0x03, 0x01, 0x02, 0x03,                   // tracker 3: ignored
	    0x0A, 0x01, 0x01, 0x40, 0x10, 0xF0, 0x80, 0xA4, 0x15, 0x02, // keys, left
	    0x04, 0x02, 0x01, 0x05,                               // battery, left
	};
	memcpy(pkt + 13, records, sizeof(records));
	pkt[12] = sizeof(records);

	struct pico4_blemcu_packet p;
	EXPECT(pico4_blemcu_parse(pkt, sizeof(pkt), &p));
	EXPECT(p.station_time_us == 0x112345678ULL);
	EXPECT(p.imu_count == 1 && p.imu[0].device == PICO4_BLEMCU_RIGHT && p.imu[0].offset_us == 1920);
	EXPECT(p.imu[0].gyro[0] == -11 && p.imu[0].gyro[1] == -4 && p.imu[0].gyro[2] == 5);
	EXPECT(p.imu[0].accel[0] == 967 && p.imu[0].accel[1] == 171 && p.imu[0].accel[2] == -241);
	EXPECT(p.imu[0].temperature == -246 && p.imu[0].counter == 161);
	EXPECT(p.key_count == 1 && p.keys[0].device == PICO4_BLEMCU_LEFT);
	EXPECT(p.keys[0].trigger == 0x40 && p.keys[0].stick_y == 0x10 && p.keys[0].stick_x == 0xF0);
	EXPECT(p.keys[0].grip == 0x80);
	EXPECT(p.keys[0].buttons == (PICO4_BLEMCU_BTN_TRIGGER | PICO4_BLEMCU_BTN_STICK | PICO4_BLEMCU_BTN_AX));
	EXPECT(p.keys[0].touch == (PICO4_BLEMCU_TOUCH_TRIGGER | PICO4_BLEMCU_TOUCH_AX | PICO4_BLEMCU_TOUCH_STICK));
	EXPECT(p.battery_count == 1 && p.battery[0].device == PICO4_BLEMCU_LEFT && p.battery[0].level == 5);

	// Scales as PICO OS derives them: 2000 deg/s and 16 g over 32768, both doubled for the
	// PICO 4's controllers. A raw gyro of 8192 is 1000 deg/s, an accelerometer of 1024 1 g.
	EXPECT(fabs(PICO4_BLEMCU_GYRO_LSB_PER_DPS - 32768.0 / (2 * 2000.0)) < 1e-12);
	EXPECT(fabs(PICO4_BLEMCU_ACCEL_LSB_PER_G - 32768.0 / (2 * 16.0)) < 1e-12);
	EXPECT(fabs(8192 / PICO4_BLEMCU_GYRO_LSB_PER_DPS - 1000.0) < 1e-9);
	EXPECT(fabs(1024 / PICO4_BLEMCU_ACCEL_LSB_PER_G - 1.0) < 1e-12);

	// Not a radio packet, or records running past the length.
	pkt[0] = 0x5B;
	EXPECT(!pico4_blemcu_parse(pkt, sizeof(pkt), &p));
	pkt[0] = 0x5A;
	EXPECT(!pico4_blemcu_parse(pkt, 20, &p));
	pkt[13] = 0x30; // first record claims more bytes than there are
	EXPECT(pico4_blemcu_parse(pkt, sizeof(pkt), &p) && p.imu_count == 0 && p.key_count == 0);
}

static void
test_station_connection(void)
{
	// A connect notification with the layout the station sends (A5 02), made-up contents.
	uint8_t f[PICO4_STATION_FRAME] = {0xA5, 0x02, 0x02, 'P', 'R', '0', '3', '2', '6', 0x11, '0', '3', 0x06};
	memcpy(f + 13, "TESTSERIAL0000001", 17);
	f[39] = pico4_station_crc8(f, 39);
	enum pico4_station_hand hand = 0;
	struct pico4_station_controller c;
	EXPECT(pico4_station_parse_connection(f, &hand, &c) == 1);
	EXPECT(hand == PICO4_STATION_HAND_RIGHT && c.connected);
	EXPECT(strcmp(c.software, "PR0326") == 0 && strcmp(c.serial, "TESTSERIAL0000001") == 0);

	// Connected but invalid (status 2) is no connection; a disconnect names the hand.
	f[9] = 0x12;
	EXPECT(pico4_station_parse_connection(f, &hand, &c) == -1);
	const uint8_t off[4] = {0xA5, 0x03, 0x01, 0x05};
	memset(f, 0, sizeof(f));
	memcpy(f, off, sizeof(off));
	EXPECT(pico4_station_parse_connection(f, &hand, &c) == 0 && hand == PICO4_STATION_HAND_LEFT);
	f[2] = 3;
	EXPECT(pico4_station_parse_connection(f, &hand, &c) == -1);
	f[0] = 0xBB;
	f[2] = 1;
	EXPECT(pico4_station_parse_connection(f, &hand, &c) == -1);
}

static void
test_blemcu_order(void)
{
	// Samples stamped at or after "now" are dropped, the others keep their order.
	struct pico4_blemcu_packet p = {0};
	const uint32_t offsets[] = {4000, 0, 2000};
	for (int i = 0; i < 3; i++) {
		p.imu[i].device = PICO4_BLEMCU_RIGHT;
		p.imu[i].offset_us = offsets[i];
	}
	p.imu_count = 3;
	EXPECT(pico4_blemcu_drop_future(&p, 10000000, 10000000) == 1);
	EXPECT(p.imu_count == 2 && p.imu[0].offset_us == 4000 && p.imu[1].offset_us == 2000);
	EXPECT(pico4_blemcu_drop_future(&p, 10000000, 7000000) == 1 && p.imu_count == 1);
	EXPECT(pico4_blemcu_drop_future(&p, 10000000, 20000000) == 0 && p.imu_count == 1);

	// Duplicates and backward steps are rejected and counted.
	struct pico4_blemcu_order o = {0};
	EXPECT(!pico4_blemcu_order_fresh(&o, 1000));
	EXPECT(pico4_blemcu_order_accept(&o, 1000000));
	EXPECT(!pico4_blemcu_order_accept(&o, 1000000));
	EXPECT(!pico4_blemcu_order_accept(&o, 998000));
	EXPECT(pico4_blemcu_order_accept(&o, 1002000));
	EXPECT(o.newest_ns == 1002000 && o.rejected == 2);

	// Fresh until the newest sample is 0.2 s old.
	EXPECT(pico4_blemcu_order_fresh(&o, 1002000 + PICO4_BLEMCU_STALE_NS - 1));
	EXPECT(!pico4_blemcu_order_fresh(&o, 1002000 + PICO4_BLEMCU_STALE_NS));
}

//! A metadata line with gain @p gain, exposure @p lines and phase register @p phase as 10-bit samples.
static void
meta_line(uint8_t *line, unsigned gain, unsigned lines, unsigned phase)
{
	uint16_t w[PICO4_CAM_WIDTH] = {0};
	w[4] = (uint16_t)(gain << 2);
	w[6] = (uint16_t)(((lines >> 8) & 0xf) << 2);
	w[7] = (uint16_t)((lines & 0xff) << 2);
	w[82] = (uint16_t)(phase << 2);
	for (int i = 0; i < PICO4_CAM_WIDTH; i++) {
		line[2 * i] = (uint8_t)(w[i] & 0xff);
		line[2 * i + 1] = (uint8_t)(w[i] >> 8);
	}
}

static void
test_meta(void)
{
	uint8_t line[2 * PICO4_CAM_WIDTH];
	struct pico4_cam_meta m;

	// Values seen on the headset: a scene frame at 300 lines and gain 32, phase register 0x51.
	meta_line(line, 32, 300, 0x51);
	EXPECT(line[8] == 0x80 && line[12] == 0x04 && line[14] == 0xb0 && line[164] == 0x44 && line[165] == 0x01);
	EXPECT(pico4_cam_meta_decode(line, sizeof(line), &m));
	EXPECT(m.gain == 32 && m.exposure_lines == 300 && m.phase == PICO4_CAM_PHASE_SCENE && m.tag == 5);
	// A controller-LED frame: 4 lines, gain 26, phase register 0x72.
	meta_line(line, 26, 4, 0x72);
	EXPECT(pico4_cam_meta_decode(line, sizeof(line), &m));
	EXPECT(m.gain == 26 && m.exposure_lines == 4 && m.phase == PICO4_CAM_PHASE_LEDS && m.tag == 7);
	// Long exposures use the upper bits.
	meta_line(line, 248, 570, 0x01);
	EXPECT(pico4_cam_meta_decode(line, sizeof(line), &m) && m.exposure_lines == 570 && m.gain == 248);
	// No known phase, a sample above 10 bits (scene data, not metadata), a short line.
	meta_line(line, 32, 300, 0x53);
	EXPECT(!pico4_cam_meta_decode(line, sizeof(line), &m));
	meta_line(line, 32, 300, 0x51);
	line[9] = 0x04; // word 4 = 0x480
	EXPECT(!pico4_cam_meta_decode(line, sizeof(line), &m));
	meta_line(line, 32, 300, 0x51);
	EXPECT(!pico4_cam_meta_decode(line, 82 * 2, &m));
	EXPECT(!pico4_cam_meta_decode(NULL, sizeof(line), &m));
}

static void
test_median(void)
{
	// One camera covered (dark) or saturated does not move the group's signal much.
	const float covered[4] = {240, 3, 255, 250}, saturated[4] = {240, 1000, 255, 250}, odd[3] = {9, 1, 5};
	EXPECT(fabsf(pico4_cam_median(covered, 4) - 245.0f) < 0.01f);
	EXPECT(fabsf(pico4_cam_median(saturated, 4) - 252.5f) < 0.01f);
	EXPECT(fabsf(pico4_cam_median(odd, 3) - 5.0f) < 0.01f);
	EXPECT(pico4_cam_median(odd, 0) == 0.0f);
}

static void
test_set_assembly(void)
{
	const int64_t ms = 1000000, window = 4 * ms;
	struct pico4_cam_assembly a = {0};
	bool dropped;
	// Four cameras within 4 ms of the first: one complete set.
	EXPECT(pico4_cam_assembly_add(&a, 0, 100 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED && !dropped);
	EXPECT(pico4_cam_assembly_add(&a, 2, 101 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED);
	EXPECT(pico4_cam_assembly_add(&a, 2, 102 * ms, window, 4, &dropped) == PICO4_CAM_SET_DUPLICATE);
	EXPECT(pico4_cam_assembly_add(&a, 1, 103 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED);
	EXPECT(pico4_cam_assembly_add(&a, 3, 99 * ms, window, 4, &dropped) == PICO4_CAM_SET_COMPLETE && !dropped);
	pico4_cam_assembly_reset(&a);
	// Camera 3 misses the pulse: the next pulse's first frame drops the incomplete set and
	// starts a new one, which camera 3 then completes.
	EXPECT(pico4_cam_assembly_add(&a, 0, 140 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED);
	EXPECT(pico4_cam_assembly_add(&a, 1, 140 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED);
	EXPECT(pico4_cam_assembly_add(&a, 2, 141 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED && !dropped);
	EXPECT(pico4_cam_assembly_add(&a, 1, 180 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED && dropped);
	EXPECT(a.have == 1 && a.ts == 180 * ms && a.slot[1] && !a.slot[0]);
	// A frame of the dropped pulse arriving late is skipped, not mixed into the new set.
	EXPECT(pico4_cam_assembly_add(&a, 3, 141 * ms, window, 4, &dropped) == PICO4_CAM_SET_LATE && !dropped);
	EXPECT(pico4_cam_assembly_add(&a, 0, 180 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED);
	EXPECT(pico4_cam_assembly_add(&a, 2, 181 * ms, window, 4, &dropped) == PICO4_CAM_SET_ADDED);
	EXPECT(pico4_cam_assembly_add(&a, 3, 182 * ms, window, 4, &dropped) == PICO4_CAM_SET_COMPLETE);
}

static void
test_set_identity(void)
{
	struct pico4_cam_set_check c = {0};
	struct pico4_cam_meta m[4] = {{32, 300, 1, 5}, {32, 300, 1, 5}, {32, 300, 1, 5}, {32, 300, 1, 5}};
	char why[160];
	// Counters started at different times; scene frames every other pulse.
	uint64_t ids[4] = {100, 41, 7, 260};
	EXPECT(pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && why[0] == '\0');
	for (int i = 0; i < 4; i++) {
		ids[i] += 2;
	}
	EXPECT(pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)));
	// Camera 2's frame from the pulse before (a late frame mixed in): dropped.
	ids[0] += 2, ids[1] += 2, ids[3] += 2;
	EXPECT(!pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && strstr(why, "camera 2") != NULL);
	// Next pulse in step again: accepted with the old offsets.
	ids[0] += 2, ids[1] += 2, ids[2] += 4, ids[3] += 2;
	EXPECT(pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && why[0] == '\0');
	// Camera 1's counter skipped one for good: the first set is dropped, the second adopts it.
	ids[0] += 2, ids[1] += 3, ids[2] += 2, ids[3] += 2;
	EXPECT(!pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)));
	for (int i = 0; i < 4; i++) {
		ids[i] += 2;
	}
	EXPECT(pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && strstr(why, "adopted") != NULL);
	for (int i = 0; i < 4; i++) {
		ids[i] += 2;
	}
	EXPECT(pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && why[0] == '\0');
	// A camera that took the station's new exposure one frame later: two exposures in one set.
	for (int i = 0; i < 4; i++) {
		ids[i] += 2;
	}
	m[3].exposure_lines = 320;
	EXPECT(!pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && strstr(why, "camera 3 exposed 320") != NULL);
	m[3].exposure_lines = 300;
	m[1].gain = 48;
	EXPECT(!pico4_cam_set_consistent(&c, ids, m, 4, why, sizeof(why)) && strstr(why, "gain 48") != NULL);
}

int
main(void)
{
	test_station();
	test_station_vibrate();
	test_station_connection();
	test_calibration();
	test_blemcu();
	test_blemcu_order();
	test_meta();
	test_median();
	test_set_assembly();
	test_set_identity();
	if (failures != 0) {
		fprintf(stderr, "%d failure(s)\n", failures);
		return 1;
	}
	printf("ok\n");
	return 0;
}
