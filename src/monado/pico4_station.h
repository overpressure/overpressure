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
 * (acknowledge, camera frame rate, camera init/stream/exposure), the two
 * vibration frames to a controller (pico4_station_vibrate()) and the power
 * ioctl; it has no way to send firmware, bootloader or flash commands and never
 * touches the boot pin.
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

/*!
 * Commands this module may build. Byte 1 of a frame names the target: the station
 * (1) or a controller (2 left, 3 right), and a command byte means different things
 * for each; to the station 01 reads the firmware version and 07 switches the IMU
 * stream. The motor commands are therefore allowed only towards a controller.
 */
enum pico4_station_cmd
{
	PICO4_STATION_CMD_CAMERA = 0x04,     //!< Payload [op, ...]: 01 init, 02 stream.
	PICO4_STATION_CMD_CAMERA_FPS = 0x19, //!< Answer to the power-on announcement.
	PICO4_STATION_CMD_ACK = 0xFA,        //!< Payload: the acknowledged frame's bytes 37..38.
	//! To a controller only: [play, amplitude, u16 duration ms].
	PICO4_STATION_CMD_MOTOR = 0x01,
	//! To a controller only: [play, amplitude, u16 duration ms, u16 frequency Hz].
	PICO4_STATION_CMD_MOTOR_EVENT = 0x07,
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

/*!
 * Controllers as PICO OS's station service numbers them (controller_type_t); its
 * frames target 2 for the left and 3 for the right one.
 */
enum pico4_station_hand
{
	PICO4_STATION_HAND_LEFT = 1,
	PICO4_STATION_HAND_RIGHT = 2,
};

//! The motor-event frequency range PICO OS's station client clamps to.
#define PICO4_STATION_MOTOR_MIN_HZ 40
#define PICO4_STATION_MOTOR_MAX_HZ 500
//! PICO OS's default vibration strength, ctrl_motor_vibration_amplitude (StationService::SetMotor 0x72000).
#define PICO4_STATION_MOTOR_SCALE_PERCENT 75

/*!
 * Build a vibration request for one controller into @p out (40 bytes), laid out as
 * PICO OS's stationservice does (libstationopticsservice.so, StationService::SetMotor
 * 0x71eb4, SetVCMotorEvent 0x720e0; the level clamp is StationClient's,
 * libstationclient.pxr.so 0x374e4 and 0x3756c):
 *
 * - @p level (0-255, larger values count as 255) is PICO OS's motor level; 0 stops the
 *   motor and zeroes the whole payload.
 * - @p scale_percent (0-100) is the station service's amplitude setting; the amplitude
 *   byte is level × scale / 100, at most 255.
 * - @p duration_ms goes out as a u16 (larger values are clamped).
 * - @p frequency_hz is clamped to 40-500 Hz; 0 builds the plain motor command (01),
 *   anything else the motor event (07), which carries the frequency.
 *
 * The station sends nothing back for either. Returns false, building nothing, for a
 * hand other than left or right or a scale above 100.
 */
bool
pico4_station_vibrate(uint8_t out[PICO4_STATION_FRAME],
                      enum pico4_station_hand hand,
                      unsigned level,
                      unsigned scale_percent,
                      unsigned duration_ms,
                      unsigned frequency_hz);

/*!
 * PICO OS's mapping of an OpenXR haptic vibration onto pico4_station_vibrate()
 * (libpxrruntime.so controller set_output 0xa9d20/0xaa4c8, libpxrinputmanager.so
 * PhyController::SetPhyControllerVibrate 0x28d20): the duration in whole ms
 * (XR_MIN_HAPTIC_DURATION, -1, becomes 500 ms), at least 10 ms; level = amplitude ×
 * 255, truncated. A duration of 0 ms or an amplitude of 0 stops (level 0, 0 ms). The
 * frequency is not used: PICO OS sends the plain motor command. Unlike PICO OS, the
 * amplitude is clamped to 0-1 and the duration to 65535 ms.
 */
void
pico4_station_haptic_map(float amplitude, int64_t duration_ns, unsigned *out_level, unsigned *out_duration_ms);

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
 * The u16 PICO OS puts in bytes 13-14 of every CameraSetPara at a 50 Hz camera rate (160 at
 * other rates; its tracking service's gFPSInfo). With 0 there instead, the controller-LED
 * exposures come out about 27 times brighter (saturated samples per frame) than with no
 * command at all, and neighbouring LEDs merge into one blob; with 52 they stay as without one.
 */
#define PICO4_STATION_PARA_SYNC_50HZ 52

/*!
 * CameraSetPara's 28-byte payload (u16 lines, u8 gain per camera): the scene phase's setting
 * for cameras 0-3 in bytes 1-12, PICO4_STATION_PARA_SYNC_50HZ in bytes 13-14, the event index
 * in byte 15 (PICO OS counts it 0-9, one per command), a second set in bytes 16-27. By the
 * frames' metadata the first set drives the scene phase; the second changes neither phase on
 * this station (the LED phase stays at 4 lines, gain 26, whatever it holds). Here it carries
 * @p leds, clamped to PICO OS's LED limits.
 */
void
pico4_station_camera_para(uint8_t out[28],
                          struct pico4_station_exposure scene,
                          struct pico4_station_exposure leds,
                          unsigned index);

//! Both phases' exposure (see pico4_station_camera_para).
int
pico4_station_camera_set_exposures(struct pico4_station *st,
                                   struct pico4_station_exposure scene,
                                   struct pico4_station_exposure leds);

//! Stop FSIN for all cameras.
int
pico4_station_camera_stop(struct pico4_station *st);

/*!
 * Vibrate a controller at @p level (0-255, 0 stops) for @p duration_ms with PICO OS's default
 * strength: one plain motor frame, as PICO OS sends per request. The controller ends the
 * vibration itself; the station answers nothing.
 */
int
pico4_station_motor(struct pico4_station *st, enum pico4_station_hand hand, unsigned level, unsigned duration_ms);

#ifdef __cplusplus
}
#endif
