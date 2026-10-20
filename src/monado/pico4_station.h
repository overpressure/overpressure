// Copyright 2026, PICO 4 Linux POC contributors.
// SPDX-License-Identifier: BSL-1.0
/*!
 * @file
 * @brief  Tracking-camera frame sync through the PICO 4 controller radio ("station").
 *
 * The station, an nRF52832 on /dev/spidev0.0, pulses the OV7251s' FSIN line;
 * the sensors run in FSIN slave mode and send nothing without it. Frames are
 * 40 bytes: header (AA to the station, BB replies, A5 notifications), target,
 * command, payload, and a CRC-8 (Dallas/Maxim) over bytes 0..38. The vendor
 * spidev driver moves whole frames on the station's interrupt.
 *
 * All calls may come from different threads (one internal lock).
 *
 * Safety: the station and controller firmware can be updated over this link.
 * This module builds only the frames in pico4_station_frame()'s allowlist
 * (acknowledge, camera frame rate, camera init/stream/exposure) and the power ioctl; it
 * has no way to send firmware, bootloader or flash commands and never touches
 * the boot pin.
 *
 * @ingroup drv_pico4
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PICO4_STATION_DEVICE "/dev/spidev0.0"
#define PICO4_STATION_FRAME 40

//! Commands this module may build.
enum pico4_station_cmd
{
	PICO4_STATION_CMD_CAMERA = 0x04,     //!< Payload [op, ...]: 01 init, 02 stream.
	PICO4_STATION_CMD_CAMERA_FPS = 0x19, //!< Answer to the power-on announcement.
	PICO4_STATION_CMD_ACK = 0xFA,        //!< Payload: the acknowledged frame's bytes 37..38.
};

//! CRC-8, reflected polynomial 0x8C, initial value 0.
uint8_t
pico4_station_crc8(const uint8_t *data, size_t size);

/*!
 * Build a request into @p out (40 bytes). Returns false, building nothing, for
 * any command or payload outside the allowlist.
 */
bool
pico4_station_frame(uint8_t out[PICO4_STATION_FRAME], uint8_t cmd, const uint8_t *payload, size_t size);

//! A frame from the station with a valid header and CRC.
bool
pico4_station_valid(const uint8_t frame[PICO4_STATION_FRAME]);

struct pico4_station;

int
pico4_station_open(struct pico4_station **out, const char *device);

void
pico4_station_close(struct pico4_station *st);

/*!
 * Power-cycle the station and answer its announcement with the camera frame
 * rate (50 Hz), after which it accepts camera commands. Controllers reconnect
 * on their own afterwards. Returns 0 or a negative errno (-ETIMEDOUT if no
 * announcement came).
 */
int
pico4_station_reset(struct pico4_station *st);

/*!
 * Read frames for up to @p timeout_ms, acknowledging them as the stock service
 * does. Returns the number of valid frames or a negative errno.
 */
int
pico4_station_poll(struct pico4_station *st, int timeout_ms);

/*!
 * Start FSIN: camera init, then stream on for all cameras. Call once all
 * sensors stream (CSL START_DEV); the station initialises them over its own
 * I2C and fails if they are powered down. Returns 0, -EIO if the station
 * refused, or another negative errno.
 */
int
pico4_station_camera_start(struct pico4_station *st);

/*!
 * Exposure of all cameras in sensor lines (about 19.3 us each; with the 9 ms
 * readout it must fit the 20 ms frame period, so at most about 570) and gain
 * (16 = 1x, up to 248). In FSIN slave mode the station sets the exposure; the
 * sensors' own registers are ignored.
 */
int
pico4_station_camera_set_exposure(struct pico4_station *st, unsigned exposure_lines, unsigned gain);

//! One exposure phase's setting: sensor lines and gain (16 = 1x).
struct pico4_station_exposure
{
	unsigned lines, gain;
};

//! PICO OS's limits for controller-LED exposures: at most 10 lines, gain below 33.
#define PICO4_STATION_LED_MAX_LINES 10
#define PICO4_STATION_LED_MAX_GAIN 32

/*!
 * CameraSetPara's 28-byte payload (u16 lines, u8 gain per camera): the scene phase's setting
 * for cameras 0-3 in bytes 1-12, a second set in bytes 16-27, bytes 13-15 0. By the frames'
 * metadata the first set drives the scene phase; the second changes neither phase on this
 * station (the LED phase stays at 4 lines, gain 26, whatever it holds). PICO OS fills it with
 * its LED setting; here it carries @p leds, clamped to PICO OS's LED limits.
 */
void
pico4_station_camera_para(uint8_t out[28], struct pico4_station_exposure scene, struct pico4_station_exposure leds);

//! Both phases' exposure (see pico4_station_camera_para).
int
pico4_station_camera_set_exposures(struct pico4_station *st,
                                   struct pico4_station_exposure scene,
                                   struct pico4_station_exposure leds);

//! Stop FSIN for all cameras.
int
pico4_station_camera_stop(struct pico4_station *st);

#ifdef __cplusplus
}
#endif
